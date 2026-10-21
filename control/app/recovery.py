"""Confirmed, single-attempt recovery of the observed OCA startup failure.

Fail closed: a journal, admission guard and cordon survive worker interruption.
Finishing an interrupted attempt never resubmits an agent restart.
"""
from __future__ import annotations
from .link_findings import physical_link_blocked
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .diagnostics import DiagnosticProfile, _PROFILES
from .host_readiness import SCRIPT
from .provider_base import ProviderError

PROBE = r'''
python3 - <<'RECOVERY_PY'
import datetime, glob, json, os, subprocess, time, urllib.request
code = 0
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://169.254.169.254/opc/v2/identity/', headers={'Authorization': 'Bearer Oracle'})
    with opener.open(request, timeout=10) as response:
        code = response.status
except Exception as exc:
    code = getattr(exc, 'code', 0)
boot = time.time() - float(open('/proc/uptime').read().split()[0])
crash = False
log_root = '/var/log/oracle-cloud-agent/plugins/oci-hpc/oci-hpc-configure/'
for name in (glob.glob(log_root + '*.log') + glob.glob(log_root + '*/*.log'))[:40]:
    with open(name, errors='replace') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 262144))
        lines = stream.read().splitlines()
    for line in lines:
        try:
            stamp = datetime.datetime.strptime(line.split(' ', 1)[1][:19], '%Y/%m/%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()
        except (ValueError, IndexError):
            continue
        if stamp >= boot - 2 and 'oci-hpc-rdma-configure' in line and ('nil pointer dereference' in line or 'Current Status: [not responding]' in line):
            crash = True
service = subprocess.run(['systemctl', 'is-active', 'snap.oracle-cloud-agent.oracle-cloud-agent.service'], capture_output=True, text=True)
print('RECOVERY_FACTS=' + json.dumps({'metadata_http': code, 'recognized_failure': crash, 'agent_active': service.stdout.strip() == 'active'}))
RECOVERY_PY
'''
_PROFILES['rdma-recovery-assessment'] = DiagnosticProfile(
    name='rdma-recovery-assessment', title='RDMA initialization recovery assessment',
    description='Read-only current metadata, host readiness and current-boot OCA failure assessment. Does not restart services.',
    expected_seconds=20, timeout_seconds=120,
    sections=('Host readiness', 'Identity endpoint status only', 'Current-boot configuration failure'),
    script=SCRIPT + PROBE)


def state_dir():
    return Path(os.environ.get('HPC_CONTROL_RECOVERY_DIR', '/var/lib/oke-rdma-control/recovery'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.chmod(0o600)
    with temp.open('r+') as stream:
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def active():
    return [json.loads(p.read_text()) for p in state_dir().glob('attempt-*.json') if json.loads(p.read_text()).get('phase') != 'COMPLETE']


def guard(action):
    if active() and action not in {'recovery_finish', 'preflight'}:
        raise ProviderError('RDMA recovery protection remains active. Finish the recorded recovery from worker details before another operation.')


def documents(name, node):
    # Covers normal pod creation with nodeName and scheduler/direct Binding requests.
    predicate = "request.resource.resource == 'pods' ? (!has(object.spec.nodeName) || object.spec.nodeName != " + json.dumps(node) + ") : object.target.name != " + json.dumps(node)
    policy = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicy',
              'metadata': {'name': name}, 'spec': {'failurePolicy': 'Fail',
              'matchConstraints': {'resourceRules': [{'apiGroups': [''], 'apiVersions': ['v1'],
                  'operations': ['CREATE'], 'resources': ['pods', 'pods/binding', 'bindings']},
                  {'apiGroups':['resource.k8s.io'], 'apiVersions':['v1'], 'operations':['CREATE'], 'resources':['resourceclaims','resourceclaimtemplates']}]},
              'validations': [{'expression': "request.resource.group == 'resource.k8s.io' ? false : request.subResource == 'binding' ? object.target.name != " + json.dumps(node) + ' : (' + predicate + ')',
                               'message': 'OKE_RDMA_RECOVERY_GUARD: worker protected during confirmed RDMA recovery'}]}}
    binding = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicyBinding',
               'metadata': {'name': name}, 'spec': {'policyName': name, 'validationActions': ['Deny']}}
    return [policy, binding]


class Recovery:
    def __init__(self, provider):
        self.p = provider

    def node(self, name):
        n = self.p._json(['get', 'node', name])
        labels = n['metadata'].get('labels', {})
        if labels.get('oci-rdma') != 'true' or labels.get('oke-rdma-mode') != 'dranet':
            raise ProviderError('Recovery requires a current selected RDMA worker.')
        if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in n.get('status', {}).get('conditions', [])):
            raise ProviderError('Recovery requires Kubernetes Ready. Investigate node health first.')
        return n

    def authorize(self):
        if self.p.s.mode != 'live' or self.p.s.operator_kubernetes_access_mode != 'iam_admin':
            raise ProviderError('Recovery requires live mode and the cluster-administrator access mode; restricted RBAC is not escalated.')
        for verb, resource in [('create','validatingadmissionpolicies'), ('delete','validatingadmissionpolicies'),
                               ('create','validatingadmissionpolicybindings'), ('delete','validatingadmissionpolicybindings'), ('patch','nodes')]:
            result = self.p.kubectl(['auth', 'can-i', verb, resource], check=False)
            if result.returncode or result.stdout.strip() != 'yes':
                raise ProviderError(f'Recovery needs administrator permission: {verb} {resource}.')

    def idle(self, name, own_pod='', allow_dns=False):
        # Conservative: any claims/templates in the cluster prevent recovery, including pending reservations.
        for resource in ['resourceclaims', 'resourceclaimtemplates']:
            if self.p._json(['get', resource, '-A']).get('items'):
                raise ProviderError('Release all resource claims/templates before recovery; pending reservations also block it.')
        for pod in self.p._json(['get', 'pods', '-A', '--field-selector', 'spec.nodeName=' + name]).get('items', []):
            meta = pod['metadata']
            if meta.get('name') == own_pod and meta.get('namespace') == 'oke-rdma-diagnostics':
                continue
            if pod.get('status', {}).get('phase') in {'Succeeded', 'Failed'}:
                continue
            # Permit existing cluster-managed DaemonSets only, verifying the owner UID.
            owners = meta.get('ownerReferences', [])
            if meta.get('namespace') == 'kube-system' and len(owners) == 1 and owners[0].get('kind') == 'DaemonSet' and owners[0].get('controller'):
                owner = self.p._json(['-n','kube-system','get','daemonset',owners[0]['name']])
                if owner.get('metadata', {}).get('uid') == owners[0].get('uid'):
                    continue
            if allow_dns and meta.get('namespace') == 'kube-system' and len(owners) == 1 and owners[0].get('kind') == 'ReplicaSet' and owners[0].get('controller'):
                rs = self.p._json(['-n','kube-system','get','replicaset',owners[0]['name']])
                parent = rs.get('metadata', {}).get('ownerReferences', [])
                if rs.get('metadata', {}).get('uid') == owners[0].get('uid') and len(parent) == 1 and parent[0].get('kind') == 'Deployment' and parent[0].get('name') == 'coredns' and parent[0].get('controller'):
                    deployment = self.p._json(['-n','kube-system','get','deployment','coredns'])
                    spec = pod.get('spec', {})
                    if deployment.get('metadata', {}).get('uid') == parent[0].get('uid') and not any(spec.get(k) for k in ['hostNetwork','hostPID','hostIPC']) and not any('hostPath' in v for v in spec.get('volumes', [])):
                        continue
            if meta.get('namespace') == 'kube-system':
                raise ProviderError(f'System pod blocks this operation: kube-system/{meta["name"]}. Do not stop cluster DNS to bypass this check. Review controller ownership and workload placement; only verified DaemonSets and, for OCA restart, standard CoreDNS pods are allowed.')
            raise ProviderError(f'Worker is not idle: {meta.get("namespace")}/{meta["name"]}. Pause application controllers and stop workloads first.')

    def assess(self, name, job_id, manual_restart=False):
        self.authorize()
        n = self.node(name)
        result = self.p.node_diagnostic(name, 'rdma-recovery-assessment', job_id)
        from .diagnostics import report_path
        report = json.loads(report_path(job_id, 'json').read_text())
        probe = next((json.loads(line.split('=',1)[1]) for line in report['output'].splitlines() if line.startswith('RECOVERY_FACTS=')), {})
        from .provider import assess_host
        issues = assess_host(result.get('facts', {}), self.p.s)
        blockers = []
        try:
            self.idle(name, allow_dns=True)
        except ProviderError as exc:
            blockers.append(str(exc))
        eligible = not blockers and not physical_link_blocked(result.get('facts', {}), self.p.s.rdma_pf_name) and bool(result.get('collection_status') == 'passed' and result.get('facts') and (manual_restart or issues) and probe.get('metadata_http') == 200 and (manual_restart or probe.get('recognized_failure')) and probe.get('agent_active'))
        preview = {'preview_id': uuid.uuid4().hex, 'created_at': time.time(), 'node_name': name,
                   'node_uid': n['metadata']['uid'], 'boot_id': n['status']['nodeInfo']['bootID'],
                   'mode': 'manual_restart' if manual_restart else 'recovery', 'blockers': blockers,
                   'restart_requirements': 'Current host evidence, identity HTTP 200, active OCA, no explicit no-cable evidence, and workload checks. Guided recovery additionally requires a recognized startup failure.',
                   'settings': self.p.s.rdma_config(), 'eligible': eligible, 'probe': probe, 'issues': issues,
                   'reports': result['reports'], 'action': 'Restart the affected worker Oracle Cloud Agent service once. Other OCA plugins briefly stop. No reboot or manual fabric addressing.',
                   'protection': 'Cordon plus verified admission denial for new pods/bindings targeting this worker. New resource claims/templates are temporarily blocked cluster-wide. Pause controllers; administrators must not bypass/remove protection.',
                   'timeout_seconds': 300}
        save(state_dir()/('preview-' + preview['preview_id'] + '.json'), preview)
        return {'status': 'passed' if eligible else 'blocked', 'message': 'Read-only assessment completed. Review restart eligibility, blockers and impact before confirming.', **preview}

    def read_preview(self, preview_id):
        if not re.fullmatch('[a-f0-9]{32}', preview_id): raise ProviderError('Invalid recovery preview ID.')
        preview = json.loads((state_dir()/('preview-' + preview_id + '.json')).read_text())
        if time.time() - preview['created_at'] > 300 or preview['settings'] != self.p.s.rdma_config() or not preview['eligible']:
            raise ProviderError('Recovery preview is expired, blocked or configuration changed. Run a fresh assessment.')
        n = self.node(preview['node_name'])
        if n['metadata']['uid'] != preview['node_uid'] or n['status']['nodeInfo']['bootID'] != preview['boot_id']:
            raise ProviderError('Worker identity or boot changed. Run a fresh assessment.')
        return preview, n

    def exec(self, record, script, timeout=60):
        return self.p.kubectl(['-n','oke-rdma-diagnostics','exec','-i',record['pod'],'--','chroot','/host','/bin/bash','-s'], stdin=script, check=False, timeout=timeout)

    def check(self, record):
        from .provider import assess_host
        result = self.exec(record, SCRIPT + PROBE, timeout=60)
        if result.returncode: raise ProviderError('Recovery inventory execution failed: ' + result.stderr[-1000:])
        facts = next((json.loads(l.split('=',1)[1]) for l in result.stdout.splitlines() if l.startswith('DRANET_FACTS=')), {})
        probe = next((json.loads(l.split('=',1)[1]) for l in result.stdout.splitlines() if l.startswith('RECOVERY_FACTS=')), {})
        if not facts or not probe: raise ProviderError('Recovery inventory is incomplete.')
        return facts, probe, assess_host(facts, self.p.s)

    def run(self, preview_id, job_id):
        self.authorize()
        preview, n = self.read_preview(preview_id)
        name = preview['node_name']
        self.p.require_registry(['oke-rdma-diagnostics'])
        self.idle(name, allow_dns=True)
        record = {'preview_id': preview_id, 'mode': preview.get('mode','recovery'), 'id': job_id, 'node_name': name, 'node_uid': n['metadata']['uid'], 'boot_id': n['status']['nodeInfo']['bootID'],
                  'original_unschedulable': n.get('spec', {}).get('unschedulable', False),
                  'pod': 'rdma-recovery-' + job_id[:8], 'guard': 'rdma-recovery-' + job_id,
                  'phase': 'PREPARING', 'restart_attempted': False, 'created_at': time.time()}
        path = state_dir()/('attempt-' + job_id + '.json')
        if path.exists() or active() or any(json.loads(p.read_text()).get('preview_id') == preview_id for p in state_dir().glob('attempt-*.json')): raise ProviderError('A recovery attempt is already recorded; use Finish recorded recovery. Restart will not be replayed.')
        save(path, record)
        try:
            self.p.report_progress('Protecting worker', 0, 4, name)
            self.p.kubectl(['patch','node',name,'--type=json','-p',json.dumps([
                {'op':'test','path':'/metadata/uid','value':record['node_uid']},
                {'op':'test','path':'/metadata/resourceVersion','value':n['metadata']['resourceVersion']},
                {'op':'add','path':'/spec/unschedulable','value':True}])])
            pod = {'apiVersion':'v1','kind':'Pod','metadata':{'name':record['pod'],'namespace':'oke-rdma-diagnostics'},
                   'spec':{'nodeName':name,'restartPolicy':'Never','activeDeadlineSeconds':900,
                           'automountServiceAccountToken':False,'hostNetwork':True,'hostPID':True,
                           'tolerations':[{'operator':'Exists'}],
                           'containers':[{'name':'recovery','image':self.p.s.rdma_tools_image,
                              'command':['/bin/bash','-c','sleep 850'],
                              'securityContext':{'privileged':True,'runAsUser':0},
                              'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'500m','memory':'512Mi'}},
                              'volumeMounts':[{'name':'host','mountPath':'/host'}]}],
                           'volumes':[{'name':'host','hostPath':{'path':'/','type':'Directory'}}]}}
            if self.p.s.rdma_image_pull_secret: pod['spec']['imagePullSecrets']=[{'name':self.p.s.rdma_image_pull_secret}]
            self.p.kubectl(['create','-f','-'],stdin=json.dumps(pod))
            self.p.kubectl(['-n','oke-rdma-diagnostics','wait','--for=condition=Ready','pod/'+record['pod'],'--timeout=120s'],timeout=130)
            guard_docs = documents(record['guard'], name)
            for doc in guard_docs: self.p.kubectl(['create','-f','-'],stdin=json.dumps(doc))
            # Prove denial before restart; do not rely on admission propagation timing.
            deadline = time.monotonic() + 30
            while True:
                checks = [
                    {'apiVersion':'resource.k8s.io/v1','kind':'ResourceClaim','metadata':{'name':'recovery-claim-probe','namespace':'oke-rdma-diagnostics'},'spec':{'devices':{'requests':[{'name':'rdma','exactly':{'deviceClassName':'dra.net'}}]}}},
                    {'apiVersion':'v1','kind':'Pod','metadata':{'name':'recovery-admission-probe','namespace':'oke-rdma-diagnostics'},'spec':{'nodeName':name,'restartPolicy':'Never','containers':[{'name':'probe','image':self.p.s.rdma_tools_image}]}},
                    {'apiVersion':'v1','kind':'Binding','metadata':{'name':record['pod'],'namespace':'oke-rdma-diagnostics'},'target':{'apiVersion':'v1','kind':'Node','name':name}}]
                denied = []
                for doc in checks:
                    args = ['create','--dry-run=server','-f','-'] if doc['kind']!='Binding' else ['create','--raw','/api/v1/namespaces/oke-rdma-diagnostics/pods/'+record['pod']+'/binding?dryRun=All','-f','-']
                    res=self.p.kubectl(args,stdin=json.dumps(doc),check=False,timeout=15)
                    denied.append(res.returncode != 0 and 'OKE_RDMA_RECOVERY_GUARD' in res.stderr)
                if all(denied): break
                if time.monotonic() >= deadline: raise ProviderError('Admission protection could not be verified. No restart submitted.')
                time.sleep(2)
            self.idle(name, record['pod'], allow_dns=True)
            facts, probe, issues = self.check(record)
            if facts.get('boot_id') != record['boot_id']: raise ProviderError('Worker rebooted during assessment.')
            if physical_link_blocked(facts, self.p.s.rdma_pf_name):
                raise ProviderError('NIC reports no cable on the selected PF. No agent restart submitted; investigate physical connectivity.')
            if probe.get('metadata_http') != 200 or not probe.get('agent_active') or (record['mode'] != 'manual_restart' and (not issues or not probe.get('recognized_failure'))):
                raise ProviderError('Current evidence no longer supports the reviewed restart. No restart submitted.')
            record['phase']='RESTART_SUBMITTED';record['restart_attempted']=True
            save(path,record)  # Durable intent precedes the only restart command; uncertain outcomes are never replayed.
            self.p.report_progress('Restarting Oracle Cloud Agent once',1,4,name)
            result=self.exec(record,'set -eu\ntimeout 60 systemctl restart snap.oracle-cloud-agent.oracle-cloud-agent.service\n',timeout=75)
            if result.returncode: raise ProviderError('OCA restart returned an error. Finish recorded recovery; do not resubmit: '+result.stderr[-1000:])
            deadline=time.monotonic()+300
            record['phase']='VERIFYING';save(path,record)
            self.p.report_progress('Waiting for host RDMA readiness',2,4,name)
            while True:
                facts,probe,issues=self.check(record)
                record['facts']=facts;record['probe']=probe;record['issues']=issues;save(path,record)
                if not issues and probe.get('metadata_http') == 200 and probe.get('agent_active'): break
                if time.monotonic() >= deadline: raise ProviderError('Host readiness did not recover within five minutes. Worker remains protected; review evidence and finish the recorded recovery.')
                time.sleep(10)
            return self.finish(job_id, healthy=True)
        except Exception as exc:
            record['phase']='ATTENTION';record['error']=str(exc)[-2000:];save(path,record)
            return {'status':'blocked','message':'Recovery stopped. No automatic restart retry. Worker protection is retained until Finish recorded recovery.', 'recovery':record}

    def finish(self, attempt_id, healthy=False):
        self.authorize()
        if not re.fullmatch('[a-f0-9-]{36}',attempt_id): raise ProviderError('Invalid recovery attempt ID.')
        path=state_dir()/('attempt-'+attempt_id+'.json');record=json.loads(path.read_text())
        if record['phase']=='COMPLETE': return {'status':'passed','recovery':record}
        n=self.node(record['node_name'])
        if n['metadata']['uid'] != record['node_uid']: raise ProviderError('Worker was replaced; administrator must review retained protection. No node was modified.')
        # Only verified health permits automatic scheduling restoration. Manual finish leaves it cordoned.
        if healthy:
            facts,probe,issues=self.check(record)
            if issues or probe.get('metadata_http') != 200 or not probe.get('agent_active') or facts.get('boot_id') != record['boot_id']: raise ProviderError('Readiness changed before protection removal.')
            from .provider import STATE_DIR
            cache=self.p._host_cache();cache[record['node_name']]={'at':time.time(),'facts':facts,'issues':[],'reports':{}}
            save(STATE_DIR/'host-readiness.json',cache)
        self.p.report_progress('Removing recovery protection',3,4,record['node_name'])
        self.p.kubectl(['-n','oke-rdma-diagnostics','delete','pod',record['pod'],'--ignore-not-found=true','--wait=true','--timeout=30s'],timeout=40)
        for resource in ['validatingadmissionpolicybinding','validatingadmissionpolicy']:
            self.p.kubectl(['delete',resource,record['guard'],'--ignore-not-found=true','--wait=true','--timeout=30s'],timeout=40)
        if healthy and not record['original_unschedulable']:
            self.p.kubectl(['patch','node',record['node_name'],'--type=json','-p',json.dumps([
                {'op':'test','path':'/metadata/uid','value':record['node_uid']},
                {'op':'add','path':'/spec/unschedulable','value':False}])])
        record['phase']='COMPLETE';record['finished_at']=time.time();record['restored_scheduling']=bool(healthy and not record['original_unschedulable']);save(path,record)
        self.p.report_progress('Recovery closed',4,4,record['node_name'])
        return {'status':'passed','message': 'Host readiness recovered; original scheduling state restored.' if healthy else 'Recovery protection removed without another restart. Worker remains cordoned; check readiness before an administrator uncordons it.', 'recovery':record}
