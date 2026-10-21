from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import time
import uuid
import yaml
from .config import Settings, public_settings
from .provider_base import BaseProvider, ProviderError
from . import dranet
from .db import list_jobs

STATE_DIR = Path(os.environ.get('HPC_CONTROL_STATE_DIR', '/var/lib/oke-rdma-control'))


def ready(node):
    return any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node.get('status', {}).get('conditions', []))


def attributes(device):
    return {k: next(iter(v.values()), None) for k, v in device.get('attributes', {}).items()}


def assess_host(facts, s):
    if not facts:
        return ['Host inventory unavailable. Review the diagnostic execution report before changing RDMA settings.']
    issues = []
    iface = next((x for x in facts.get('interfaces', []) if x['name'] == s.rdma_pf_name), {})
    if not iface:
        return [f'Configured PF {s.rdma_pf_name} was not found in the complete link inventory. Review interface names and drivers.']
    if 'netns shared' not in facts.get('netns', ''): issues.append('RDMA mode must be shared. Check the worker image and bootstrap configuration before scheduling workloads.')
    if not facts.get('nri_socket'): issues.append('CRI-O NRI socket is missing.')
    from .link_findings import findings
    issues.extend(f['message'] + ' ' + f['next_action'] for f in findings(facts, s.rdma_pf_name, s.rdma_mtu) if f['severity'] == 'block')
    if iface.get('num_vfs', 0) or iface.get('vf_links', 0): issues.append('The selected PF has active VFs. Use a worker with an unpartitioned RDMA PF dedicated to this profile.')
    if not iface.get('driver', '').startswith('mlx5'): issues.append('Selected interface is not an mlx5 PF.')
    if not any(ipaddress.ip_interface(a).ip in ipaddress.ip_network('10.224.0.0/12') for a in iface.get('addresses', [])):
        issues.append('OCA parent IPv4 address is absent. Do not allocate a claim until OCA configures the PF.')
    if any(v.startswith('enabled') for v in facts.get('sriov_boot_units', {}).values()):
        issues.append('An SR-IOV boot service owns this host. Resolve conflicting host configuration before installing Dranet.')
    return issues


class LiveProvider(BaseProvider):
    def _json(self, args, *, optional=False):
        r = self.kubectl([*args, '-o', 'json'], check=False)
        if r.returncode:
            # Missing optional objects are normal; RBAC, transport and other failures are not.
            if optional and ('NotFound' in r.stderr or "doesn't have a resource type" in r.stderr): return {}
            raise ProviderError((r.stderr or r.stdout)[-2000:])
        return json.loads(r.stdout)

    def _selected(self):
        return [n for n in self._rdma_nodes() if n.get('metadata', {}).get('labels', {}).get('oke-rdma-mode') == 'dranet']

    def _host_cache(self):
        path = STATE_DIR / 'host-readiness.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def check_hosts(self, node_names=None, force=False):
        selected = self._selected()
        if node_names is not None: selected = [n for n in selected if n['metadata']['name'] in node_names]
        if not selected: raise ProviderError('No nodes labelled oci-rdma=true,oke-rdma-mode=dranet. Check the managed RDMA node pool and its initial labels.')
        self.require_registry([self.s.workload_namespace, 'oke-rdma-diagnostics'])
        cache = self._host_cache()
        results = []
        self._checking_hosts = True
        for index, n in enumerate(selected):
            self.report_progress("Checking hosts", index, len(selected), f"Host {index + 1} of {len(selected)}: " + n["metadata"]["name"])
            name = n['metadata']['name']
            if not force and not self._host_issues(n) and 'hardware_identity' in cache.get(name, {}):
                results.append({'node': name, **cache[name], 'reused': True})
                continue
            result = self.node_diagnostic(name, 'dranet-readiness', str(uuid.uuid4()))
            entry = {'at': time.time(), 'collection_status': result.get('collection_status',result['status']), 'facts': result.get('facts', {}), 'reports': result.get('reports', {}), 'findings': result.get('findings', [])}
            entry['issues'] = assess_host(entry['facts'], self.s) if result.get('collection_status', result['status']) == 'passed' else [f['message'] + ' ' + f.get('next_action', '') for f in result.get('findings', []) if f.get('severity') == 'block'] or ['Host inventory collection failed. Review exit status, events and logs in the diagnostic report.']
            from .hardware_identity import identity, record
            entry['hardware_identity'] = record(STATE_DIR / 'hardware-history.json', identity(n, entry['facts'], self._node_instance_id(n), self.s.rdma_pf_name))
            cache[name] = entry
            results.append({'node': name, **entry})
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_DIR / 'host-readiness.tmp'
        tmp.write_text(json.dumps(cache)); tmp.chmod(0o600)
        os.replace(tmp, STATE_DIR / 'host-readiness.json')
        self._checking_hosts = False
        self.report_progress('Host checks complete', len(selected), len(selected), 'Host evidence saved')
        return {'status': 'blocked' if any(x['issues'] for x in results) else 'passed', 'hosts': results}

    def _host_issues(self, node):
        item = self._host_cache().get(node['metadata']['name'], {})
        if time.time() - item.get('at', 0) > 86400:
            return ['Host evidence is missing or older than 24 hours. Run host readiness once.']
        if item.get('facts', {}).get('boot_id') != node.get('status', {}).get('nodeInfo', {}).get('bootID'):
            return ['Node rebooted since host readiness. Refresh its evidence.']
        return assess_host(item.get('facts', {}), self.s)

    def configuration_status(self):
        ds = self._json(['-n', 'kube-system', 'get', 'daemonset', 'dranet'], optional=True)
        return {'installed': bool(ds), 'hardware_fields_locked': bool(ds), 'network_fields_locked': bool(ds)}

    def validate_rdma_selection(self, values):
        child = ipaddress.ip_network(values['rdma_child_cidr'])
        for cidr in [self.s.vcn_cidr, self.s.services_cidr, '10.224.0.0/12']:
            if child.overlaps(ipaddress.ip_network(cidr)):
                raise ProviderError(f'Child range overlaps {cidr}. Choose a separate range before installation.')
        if child.num_addresses < ipaddress.ip_network(self.s.worker_subnet_cidr).num_addresses:
            raise ProviderError('Child range is smaller than the primary VNIC subnet.')
        return values

    def rdma_discovery(self):
        cache = self._host_cache()
        return {'nodes': [{'name': n['metadata']['name'], **cache.get(n['metadata']['name'], {})} for n in self._selected()],
                'configured': self.s.rdma_config(), 'source': 'Fixed host readiness inventory; no VF-count heuristic.'}

    def inventory(self):
        nodes = self._rdma_nodes()
        slices = self._json(['get', 'resourceslices', '--field-selector=spec.driver=dra.net']).get('items', [])
        claims = self._json(['get', 'resourceclaims', '-A']).get('items', [])
        pods = self._json(['get', 'pods', '-A']).get('items', [])
        ds = self._json(['-n', 'kube-system', 'get', 'daemonset', 'dranet'], optional=True)
        dc = self._json(['get', 'deviceclass', 'dra.net'], optional=True)
        allocated = set()
        claim_rows = []
        for c in claims:
            results = c.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
            if not any(x.get('driver') == 'dra.net' for x in results) and not any(
                r.get('exactly', {}).get('deviceClassName') == 'dra.net' for r in c.get('spec', {}).get('devices', {}).get('requests', [])):
                continue
            for x in results:
                if x.get('driver') == 'dra.net': allocated.add((x['pool'], x['device']))
            ns, name = c['metadata']['namespace'], c['metadata']['name']
            consumers = [p for p in pods if p['metadata']['namespace'] == ns and any(
                r.get('resourceClaimName') == name for r in p.get('spec', {}).get('resourceClaims', []))]
            claim_rows.append({'namespace': ns, 'name': name, 'created_at': c['metadata'].get('creationTimestamp'), 'allocated': bool(results),
                               'devices': results, 'reserved_for': c.get('status', {}).get('reservedFor', []),
                               'consumers': [{'name': p['metadata']['name'], 'phase': p.get('status', {}).get('phase'),
                                              'node': p.get('spec', {}).get('nodeName')} for p in consumers]})
        node_rows, issues = [], []
        cache = self._host_cache()
        ds_ready = {p['spec'].get('nodeName') for p in pods if p['metadata'].get('labels', {}).get('app.kubernetes.io/instance') == 'dranet'
                    and any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in p.get('status', {}).get('conditions', []))}
        for node in nodes:
            name = node['metadata']['name']; labels = node['metadata'].get('labels', {})
            selected = labels.get('oke-rdma-mode') == 'dranet'
            devices = []
            node_issues = []
            for sl in slices:
                if sl['spec'].get('nodeName') != name: continue
                for d in sl['spec'].get('devices', []):
                    a = attributes(d)
                    if a.get('dra.net/ifName') != self.s.rdma_pf_name: continue
                    parent = a.get('dra.net/ipv4', '')
                    try: parent_ok = ipaddress.ip_interface(parent).ip in ipaddress.ip_network('10.224.0.0/12')
                    except ValueError: parent_ok = False
                    valid = parent_ok and a.get('dra.net/rdma') is True and a.get('dra.net/mtu') == self.s.rdma_mtu and a.get('dra.net/state') == 'up'
                    if not valid: node_issues.append('Published PF has invalid parent IP, MTU, RDMA capability or link state.')
                    if a.get('oke.dra.net/rdmaChildIpv4Cidr') != self.s.rdma_child_cidr:
                        node_issues.append('Published child CIDR differs from the saved configuration.')
                    devices.append({'name': d['name'], 'pool': sl['spec']['pool']['name'], 'attributes': a,
                                    'allocated': (sl['spec']['pool']['name'], d['name']) in allocated, 'valid': valid})
            if selected:
                if not ready(node): node_issues.append('Kubernetes node is not Ready.')
                if ds and name not in ds_ready: node_issues.append('Dranet daemon is not Ready on this node.')
                if ds and not devices: node_issues.append('No matching Dranet ResourceSlice device.')
                conflicting = [p['metadata']['name'] for p in pods if p.get('spec', {}).get('nodeName') == name
                               and p['metadata']['name'].startswith('sriov-network-config-daemon') and not p['metadata'].get('deletionTimestamp')]
                if conflicting: node_issues.append('SR-IOV config daemon still targets this Dranet node.')
            row = {'name': name, 'uid': node['metadata'].get('uid', ''), 'boot_id': node.get('status', {}).get('nodeInfo', {}).get('bootID', ''), 'dranet_ready': name in ds_ready, 'mode': labels.get('oke-rdma-mode', 'unassigned'), 'ready': ready(node),
                   'cordoned': node.get('spec', {}).get('unschedulable', False), 'devices': devices,
                   'shape': labels.get('node.kubernetes.io/instance-type', ''),
                   'zone': labels.get('topology.kubernetes.io/zone', ''), 'issues': node_issues,
                   'host_findings': cache.get(name, {}).get('findings', []), 'host_reports': cache.get(name, {}).get('reports', {}),
                   'hardware_identity': cache.get(name, {}).get('hardware_identity', {}) if cache.get(name, {}).get('hardware_identity', {}).get('boot_id') == node.get('status', {}).get('nodeInfo', {}).get('bootID') and cache.get(name, {}).get('hardware_identity', {}).get('instance_id') == self._node_instance_id(node) else {},
                   'host_check_at': cache.get(name, {}).get('at'), 'host_issues': self._host_issues(node) if selected else [],
                   'instance_id': self._node_instance_id(node), 'kernel': node.get('status', {}).get('nodeInfo', {}).get('kernelVersion', '')}
            node_rows.append(row)
            issues.extend({'node': name, 'message': msg} for msg in node_issues)
        args = ds.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [{}])[0].get('args', [])
        if ds:
            expected = ['--cloud-provider-hint=OKE', '--profile-provider=cloud', '--move-ib-interfaces=false',
                        '--cloud-provider-options=oke.rdma-child-ipv4-cidr=' + self.s.rdma_child_cidr]
            expected += ['--filter=' + dranet.values(self.s)['args']['filter'], '--kubelet-root-dir=/var/lib/kubelet']
            for arg in expected:
                if arg not in args: issues.append({'node': 'Driver', 'message': 'Configuration drift: missing ' + arg})
            template = ds['spec']['template']['spec']
            if template.get('nodeSelector', {}).get('oke-rdma-mode') != 'dranet':
                issues.append({'node': 'Driver', 'message': 'Configuration drift: driver is not restricted to Dranet nodes.'})
            if template['containers'][0].get('image') != 'registry.k8s.io/networking/dranet:' + dranet.VERSION:
                issues.append({'node': 'Driver', 'message': 'Configuration drift: driver image differs from the pinned release.'})
        if ds and not dc: issues.append({'node': 'Cluster', 'message': 'DeviceClass dra.net is missing.'})
        selected_rows = [n for n in node_rows if n['mode'] == 'dranet']
        free = sum(not d['allocated'] and d['valid'] for n in selected_rows if n['ready'] and not n['cordoned'] and not n['issues'] for d in n['devices'])
        return {'nodes': node_rows, 'claims': claim_rows, 'issues': issues, 'driver_installed': bool(ds),
                'driver_ready': bool(ds) and bool(dc) and bool(selected_rows) and not issues, 'device_class': bool(dc),
                'capacity': {'total': sum(len(n['devices']) for n in selected_rows), 'free': free, 'claims': len(claim_rows)},
                'observed_at': time.time()}

    def capacity_service(self):
        from .capacity import Capacity
        return Capacity(self, STATE_DIR)

    def reset_plan(self):
        from .reset import plan
        return plan(self)

    def reset_rdma(self, plan_id=''):
        from .reset import execute
        return execute(self, plan_id, STATE_DIR)

    def environment(self):
        marker = STATE_DIR / 'rdma-reset.json'
        reset_at = json.loads(marker.read_text()).get('reset_at', 0) if marker.exists() else 0
        return {'reset_at': reset_at, **public_settings(self.s), **self.inventory(), 'dranet_version': dranet.VERSION,
                'mode': self.s.mode, 'platform': {'kubernetes_version': self.s.kubernetes_version}}

    def preflight(self):
        checks = []
        try:
            self.validate_rdma_selection(self.s.rdma_config())
            checks.append({'name': 'Address plan', 'status': 'pass', 'detail': 'Child range does not overlap configured VCN, service or parent ranges.'})
        except ProviderError as e: checks.append({'name': 'Address plan', 'status': 'block', 'detail': str(e)})
        nodes = self._selected()
        checks.append({'name': 'Dranet target nodes', 'status': 'pass' if nodes else 'block', 'detail': f'{len(nodes)} labelled workers'})
        api = self.kubectl(['api-resources', '--api-group=resource.k8s.io', '-o', 'name']).stdout
        checks.append({'name': 'DRA APIs', 'status': 'pass' if all(x in api for x in ['resourceclaims', 'resourceslices', 'deviceclasses']) else 'block', 'detail': 'Requires Kubernetes 1.34+ and resource.k8s.io/v1.'})
        for n in nodes:
            name = n['metadata']['name']; issues = self._host_issues(n)
            if not ready(n): issues.append('Node is not Ready.')
            checks.append({'name': name, 'status': 'block' if issues else 'pass', 'detail': issues or 'Host readiness evidence is current for this boot.'})
        checks.extend(self.registry_checks([self.s.workload_namespace, 'oke-rdma-diagnostics']))
        return {'checks': checks, 'blocking': sum(x['status'] == 'block' for x in checks)}

    def install_dranet(self):
        self.report_progress('Reviewing prerequisites', 0, 3)
        pf = self.preflight()
        if pf['blocking']: raise ProviderError(json.dumps(pf))
        inv = self.inventory()
        if any('SR-IOV config daemon' in x['message'] for x in inv['issues']):
            raise ProviderError('An SR-IOV config daemon also manages selected workers. Resolve overlapping controller ownership before installing Dranet.')
        self.report_progress('Installing or verifying Dranet', 1, 3, 'Waiting for driver rollout; bounded to three minutes')
        if inv['driver_installed']:
            ds = self._json(['-n', 'kube-system', 'get', 'daemonset', 'dranet'])
            spec = ds['spec']['template']['spec']
            expected_filter = '--filter=' + dranet.values(self.s)['args']['filter']
            args = spec['containers'][0].get('args', [])
            if (any('Configuration drift' in x['message'] for x in inv['issues']) or
                expected_filter not in args or spec.get('nodeSelector', {}).get('oke-rdma-mode') != 'dranet' or
                spec['containers'][0]['image'] != 'registry.k8s.io/networking/dranet:' + dranet.VERSION):
                raise ProviderError('Installed driver settings differ from the requested profile. Compare exported Helm values with the deployed configuration before planning maintenance.')
        else:
            if self.s.operator_kubernetes_access_mode == 'least_privilege_rbac':
                raise ProviderError('Dranet installation creates privileged infrastructure and RBAC. A cluster administrator must run the documented Helm installation once; this restricted operator can then verify it and manage workloads.')
            chart = Path(__file__).resolve().parents[1] / 'vendor' / 'dranet-v1.5.0.tgz'
            if not chart.exists(): raise ProviderError('Bundled Dranet chart is missing. Rebuild the operator payload.')
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            val = STATE_DIR / 'dranet-values.yaml'; val.write_text(yaml.safe_dump(dranet.values(self.s)))
            self._run(['helm', 'upgrade', '--install', 'dranet', str(chart), '-n', 'kube-system', '-f', str(val), '--wait', '--timeout', '3m'], timeout=210)
        self.report_progress('Configuring DeviceClass', 2, 3)
        ns = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': self.s.workload_namespace}}
        self._apply_documents([ns, dranet.DEVICE_CLASS], dry_run=True)
        self._apply_documents([ns, dranet.DEVICE_CLASS])
        return {'status': 'passed', 'message': 'Dranet and DeviceClass are configured. Refresh inventory before validation.', 'version': dranet.VERSION}

    def node_diagnostics(self):
        items = []
        fallback = self._node_pool_instance_map()
        for n in self._rdma_nodes():
            name = n['metadata']['name']; iid = self._node_instance_id(n) or fallback.get(name, '')
            items.append({'name': name, 'instance': self._instance_details(iid), 'plugins': self._instance_agent_plugins(iid)})
        return {'nodes': items, 'observed_at': time.time()}

    def events(self):
        nodes = {n['metadata']['name'] for n in self._rdma_nodes()}
        items = self._json(['get', 'events', '-A', '--field-selector=type=Warning']).get('items', [])
        relevant = [x for x in items if x.get('involvedObject', {}).get('name') in nodes or
                    x.get('metadata', {}).get('namespace') in {self.s.workload_namespace, 'kube-system', 'nvidia-network-operator'}]
        relevant.sort(key=lambda x: x.get('lastTimestamp') or x['metadata'].get('creationTimestamp', ''), reverse=True)
        return {'items': [{'time': x.get('lastTimestamp'), 'namespace': x['metadata']['namespace'],
                           'object': x.get('involvedObject', {}).get('name'), 'reason': x.get('reason'),
                           'message': x.get('message'), 'count': x.get('count')} for x in relevant[:50]]}

    def validation_startup(self, names, result=None):
        from .startup_timing import summarize
        deadline=time.monotonic()+180;allocated={}
        while True:
            pods={p['metadata']['name']:p for p in self._json(['-n',self.s.workload_namespace,'get','pods','-l','app=oke-dranet-validation']).get('items',[])}
            claims={c['metadata']['name']:c for c in self._json(['-n',self.s.workload_namespace,'get','resourceclaims','-l','app=oke-dranet-validation']).get('items',[])}
            for name,c in claims.items():
                if c.get('status',{}).get('allocation'):allocated.setdefault(name,time.time())
            rows=[summarize(pods.get(name,{'metadata':{'name':name}}),claims.get(name,{}),allocated.get(name)) for name in names]
            if result is not None:result['startup_timing']={'status':'pending','pods':rows,'poll_interval_seconds':2}
            if all(name in pods and ready(pods[name]) for name in names):return {'status':'passed','pods':rows,'poll_interval_seconds':2}
            if any(p.get('status',{}).get('phase') in {'Failed','Succeeded'} for p in pods.values()) or time.monotonic()>=deadline:
                raise ProviderError('Validation pod startup failed or exceeded three minutes. Review pod events and claim allocation.')
            time.sleep(2)

    def performance_matrix(self, names):
        if not 2<=len(names)<=8:raise ProviderError('Matrix requires 2–8 free healthy workers. Reserve a smaller test pool before running.')
        pairs=[(a,b) for a in names for b in names if a!=b]
        result={'status':'passed','performance_profile':'matrix','nodes':names,'pair_results':[],'started_at':time.time()}
        deadline=time.monotonic()+7200
        for index,(a,b) in enumerate(pairs):
            if time.monotonic()>deadline:
                result.update(status='failed',error='Matrix exceeded its two-hour budget');break
            pods=self._json(['get','pods','-A']).get('items',[])
            busy=[p['metadata']['name'] for p in pods if p.get('spec',{}).get('nodeName') in [a,b] and p.get('status',{}).get('phase') not in {'Succeeded','Failed'} and p['metadata'].get('namespace') not in {'kube-system','cert-manager','node-feature-discovery'}]
            if busy:
                result.update(status='failed',error='Matrix stopped: application workloads on selected endpoints');break
            self.report_progress('Measuring worker matrix',index,len(pairs),a+' → '+b)
            try:sample=self.rdma_validation(a,b,'quick')
            except Exception as exc:sample={'status':'failed','nodes':[a,b],'error':str(exc)}
            result['pair_results'].append(sample)
            if sample.get('status')!='passed':
                result.update(status='failed',error='Matrix stopped after a failed pair; retained successful pairs remain individual measurements.');break
        result['finished_at']=time.time()
        return result

    def rdma_validation(self, server_node='', client_node='', performance_profile='quick'):
        if performance_profile not in {'quick','sweep','loaded','numa','mpi','matrix'}: raise ProviderError('Unknown performance profile')
        self.report_progress('Selecting endpoints and checking prerequisites', 0, 5)
        inv = self.inventory()
        if not inv['driver_ready']: raise ProviderError('Resolve driver readiness and drift findings before validation.')
        available = [n['name'] for n in inv['nodes'] if n['mode'] == 'dranet' and n['ready'] and not n['cordoned']
                     and not n['issues'] and any(d['valid'] and not d['allocated'] for d in n['devices'])]
        if bool(server_node) != bool(client_node): raise ProviderError('Select both endpoints or leave both automatic.')
        pair = [server_node, client_node] if server_node else available[:2]
        if len(pair) != 2 or pair[0] == pair[1] or any(n not in available for n in pair):
            raise ProviderError('Two distinct Ready, schedulable nodes with free DRA devices are required. Existing claims reserve capacity; remove only your completed workload claims.')
        if not self.s.rdma_tools_image: raise ProviderError('Save an immutable tools image first.')
        for n in self._selected():
            if n['metadata']['name'] in pair:
                problems = self._host_issues(n)
                if problems: raise ProviderError(n['metadata']['name'] + ': ' + ' '.join(problems))
        participants = available[:8] if performance_profile == 'mpi' and not server_node else pair
        if performance_profile == 'matrix':
            return self.performance_matrix(available)
        # Deep profiles create deliberate traffic. Require application-idle endpoints.
        if performance_profile != 'quick':
            pods = self._json(['get','pods','-A']).get('items', [])
            busy = [p['metadata']['name'] for p in pods if p.get('spec',{}).get('nodeName') in participants
                    and p.get('status',{}).get('phase') not in {'Succeeded','Failed'}
                    and p['metadata'].get('namespace') not in {'kube-system','cert-manager','node-feature-discovery'}]
            if busy: raise ProviderError('Deep performance tests require idle endpoints; active pods: ' + ', '.join(busy[:8]))
        # Never delete or adopt an unrelated workload with a colliding name.
        pod_names = ['oke-dranet-server', 'oke-dranet-client'] + ['oke-dranet-mpi-'+str(i) for i in range(2,len(participants))]
        for name in pod_names:
            for kind in ['pod', 'resourceclaim']:
                if self._json(['-n', self.s.workload_namespace, 'get', kind, name], optional=True):
                    raise ProviderError(f'{kind}/{name} already exists. Use owned validation cleanup before rerunning.')
        docs = []
        for pod_name, n in zip(pod_names, participants):
            host=next(x for x in self._selected() if x['metadata']['name']==n)
            if self._host_issues(host): raise ProviderError(n+': refresh host readiness before benchmarking')
            workload=dranet.workload(self.s, pod_name, node=n, validation=True)
            if performance_profile=='numa':
                workload[1]['spec']['containers'][0]['securityContext']['capabilities']['add'] += ['SYS_NICE']
            if performance_profile=='mpi':
                workload[1]['spec']['containers'][0]['securityContext']['capabilities']['add'] += ['SETUID','SETGID','SYS_CHROOT','CHOWN','DAC_OVERRIDE']
                workload[1]['spec']['activeDeadlineSeconds']=900
            docs.extend(workload)
        result = {"status": "failed", "nodes": pair, "stage": "Pod preparation", "performance_profile": performance_profile, "timeline": [{"stage":"pod_preparation_started","at":time.time()}]}
        try:
            self.report_progress('Preparing workload pods', 1, 5, 'Waiting for two pods and NIC claims; up to three minutes')
            self._apply_documents(docs, dry_run=True)
            self._apply_documents(docs)
            result['startup_timing'] = self.validation_startup(pod_names, result)
            result['timeline'].append({'stage':'pods_and_claims_ready','at':time.time()})
            devices = [next(n for n in inv['nodes'] if n['name'] == name)['devices'][0]['attributes']['dra.net/rdmaDevice'] for name in pair]
            for index, test in enumerate(['bandwidth', 'latency']):
                stage = 'RDMA-CM ' + test
                result['stage'] = stage
                self.report_progress(stage, 2 + index, 5,
                                     '64 KiB / 5,000 writes' if test == 'bandwidth' else '8 bytes / 5,000 writes; half round-trip latency')
                r = self._run(['python3', str(Path(__file__).resolve().parents[1] / 'validation' / 'test_rdma.py'),
                               '--namespace', self.s.workload_namespace, '--interface', self.s.rdma_pf_name,
                               '--child-cidr', self.s.rdma_child_cidr, '--tos', str(self.s.rdma_tos),
                               '--server-device', devices[0], '--client-device', devices[1], '--test', test], timeout=180, check=False)
                try:
                    measurement = json.loads(r.stdout)
                except json.JSONDecodeError:
                    raise ProviderError((r.stderr + r.stdout)[-3500:])
                if test == 'bandwidth':
                    result.update(measurement)
                else:
                    result['latency'] = measurement
                if r.returncode or measurement.get('status') != 'passed':
                    result.update(status='failed', stage=stage, error=measurement.get('error', stage + ' did not pass'))
                    break
            if result.get('status') == 'passed' and performance_profile != 'quick':
                self.report_progress('Extended performance profile', 3, 5, performance_profile + '; maximum four minutes')
                result['timeline'].append({'stage':'extended_profile_started','at':time.time()})
                if performance_profile=='mpi':
                    members=[{'pod':pod,'node':name,'device':next(n for n in inv['nodes'] if n['name']==name)['devices'][0]['attributes']['dra.net/rdmaDevice']} for pod,name in zip(pod_names,participants)]
                    command=['python3',str(Path(__file__).resolve().parents[1]/'validation'/'mpi_suite.py'),'--namespace',self.s.workload_namespace,'--participants',json.dumps(members),'--tos',str(self.s.rdma_tos)]
                else:
                    command=['python3',str(Path(__file__).resolve().parents[1]/'validation'/'performance_suite.py'),'--profile',performance_profile,'--namespace',self.s.workload_namespace,'--interface',self.s.rdma_pf_name,'--child-cidr',self.s.rdma_child_cidr,'--tos',str(self.s.rdma_tos),'--server-device',devices[0],'--client-device',devices[1]]
                r=self._run(command,timeout=300,check=False)
                try: result['extended'] = json.loads(r.stdout)
                except ValueError: raise ProviderError('Extended profile did not return valid evidence: ' + r.stderr[-1000:])
                if r.returncode or result['extended'].get('status') != 'passed':
                    result.update(status='failed', error=result['extended'].get('error','Extended profile failed'))
            result['nodes'] = pair

        except Exception as exc:
            result.update(status='failed', error=str(exc)[:4000])
            try:
                observed = self._json(['-n', self.s.workload_namespace, 'get', 'pods', '-l', 'app=oke-dranet-validation'])
                result['pod_status'] = [{'name': p['metadata']['name'], 'status': p.get('status', {})} for p in observed.get('items', [])]
            except Exception as inventory_error:
                result['inventory_error'] = str(inventory_error)[:1000]
        finally:
            self.report_progress('Releasing validation pods and claims', 4, 5)
            try:
                self._validation_cleanup = True
                result['cleanup'] = self.cleanup_validation()
                self._validation_cleanup = False
            except Exception as cleanup_error:
                result['cleanup'] = {'status': 'failed', 'error': str(cleanup_error)[:2000]}
                result['status'] = 'failed'
                result['message'] = 'Cleanup incomplete. Review owned validation resources before retrying.'
        from .measurements import context
        result['context'] = context(inv['nodes'], self.s)
        result['timeline'].append({'stage':'cleanup_finished','at':time.time()})
        from .performance import provenance
        result['provenance'] = provenance(pair, inv, self._host_cache(), self.s, performance_profile)
        result['participants'] = participants
        return result

    def cleanup_validation(self):
        if not getattr(self, '_validation_cleanup', False):
            self.report_progress('Releasing validation pods and claims', 0, 1)
        selector = 'app=oke-dranet-validation,app.kubernetes.io/part-of=oke-rdma-quickstart'
        self.kubectl(['-n', self.s.workload_namespace, 'delete', 'pods', '-l', selector, '--ignore-not-found', '--wait=true', '--timeout=40s'], timeout=50)
        self.kubectl(['-n', self.s.workload_namespace, 'delete', 'resourceclaims', '-l', selector, '--ignore-not-found', '--wait=true', '--timeout=40s'], timeout=50)
        return {'status': 'passed', 'message': 'Owned validation pods and claims removed; RDMA capacity released.'}


class DemoProvider(LiveProvider):
    def _rdma_nodes(self):
        return [{'metadata': {'name': f'worker-{x}', 'labels': {'oci-rdma': 'true', 'oke-rdma-mode': 'dranet'}},
                 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}} for x in [10, 11]]

    def environment(self):
        nodes = []
        for x in [10, 11]:
            nodes.append({'name': f'worker-{x}', 'mode': 'dranet', 'ready': True, 'cordoned': False,
                          'shape': 'BM.Optimized3.36', 'zone': 'EU-FRANKFURT-1-AD-1', 'kernel': '6.8.0-1026-oracle',
                          'issues': [], 'host_issues': [], 'host_check_at': time.time(),
                          'devices': [{'name': 'pci-0000-98-00-0', 'allocated': False, 'valid': True,
                                       'attributes': {'dra.net/ifName': self.s.rdma_pf_name, 'dra.net/rdmaDevice': 'mlx5_2',
                                                      'dra.net/ipv4': f'10.224.0.{x}/12', 'dra.net/mtu': 4220,
                                                      'oke.dra.net/rackId': 'example-rack', 'oke.dra.net/networkBlockId': 'example-network-block'}}]})
        return {**public_settings(self.s), 'mode': 'demo', 'nodes': nodes, 'claims': [], 'issues': [],
                'driver_installed': True, 'driver_ready': True, 'device_class': True, 'dranet_version': dranet.VERSION,
                'capacity': {'total': 2, 'free': 2, 'claims': 0}, 'observed_at': time.time()}

    def reset_plan(self):
        return {'can_reset': True, 'plan_id': '0' * 64, 'blockers': [], 'targets': [], 'local_files': [], 'retained': ['Demo only: no infrastructure changes']}
    def reset_rdma(self, plan_id=''):
        return {'status': 'simulated', 'message': 'Demo reset; no cluster resources removed.'}
    def inventory(self): return self.environment()
    def configuration_status(self): return {'installed': False, 'hardware_fields_locked': False, 'network_fields_locked': False}
    def preflight(self): return {'blocking': 0, 'checks': [{'name': 'Demo preview', 'status': 'pass', 'detail': 'No live infrastructure queried.'}]}
    def check_hosts(self, node_names=None, force=False): return {'status': 'simulated', 'message': 'Demo host inventory; no privileged pod created.'}
    def install_dranet(self): return {'status': 'simulated', 'message': 'Demo installation; no cluster changed.'}
    def rdma_validation(self, server_node='', client_node='', performance_profile='quick'): return {'status': 'simulated', 'average_gbps': None, 'message': 'No measurement was taken.'}
    def cleanup_validation(self): return {'status': 'simulated', 'message': 'Demo cleanup; no resources deleted.'}
    def node_diagnostics(self): return {'nodes': [], 'message': 'Demo: OCI was not queried.'}
    def events(self): return {'items': []}
    def rdma_discovery(self): return {'nodes': [], 'configured': self.s.rdma_config()}
    def node_diagnostic(self, node_name, profile_name, job_id):
        self.validate_node_diagnostic(node_name, profile_name)
        from .diagnostics import write_reports
        report = {'node': node_name, 'profile': profile_name, 'status': 'simulated', 'output': 'No live host queried.'}
        return {**report, 'reports': write_reports(job_id, report, report['output'])}


def get_provider(settings):
    return DemoProvider(settings) if settings.mode == 'demo' else LiveProvider(settings)
