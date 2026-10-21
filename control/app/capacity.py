"""Terraform-owned capacity changes with explicit plan approval and durable OCI job IDs."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import time
from .provider_base import ProviderError, ActionError

TERMINAL = {'COMPLETE', 'DISCARDED'}
JOB_TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELED'}
STACK_PATTERN = r'^ocid1[.]ormstack[.][A-Za-z0-9._-]+$'
POOL_ADDRESS = 'oci_containerengine_node_pool.rdma'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def stack_fingerprint(stack):
    return digest({k: stack.get(k) for k in ['id', 'compartment_id', 'variables', 'config_source', 'terraform_version',
                  'working_directory', 'custom_terraform_provider', 'is_third_party_provider_experience_enabled', 'freeform_tags', 'defined_tags']})


def changed_paths(before, after, path='', limit=30):
    """Return bounded field paths only. Never publish plan values or map keys."""
    result = []
    opaque = {'metadata', 'node_metadata', 'freeform_tags', 'defined_tags', 'ssh_public_key', 'user_data'}
    def walk(a, b, prefix):
        if a == b or len(result) >= limit: return
        if prefix.rsplit('.', 1)[-1] in opaque:
            result.append(prefix); return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                name = key if re.fullmatch(r'[a-z][a-z0-9_]{0,80}', key) else '<field>'
                child = prefix+'.'+name if prefix else name
                if key not in a or key not in b: result.append(child)
                else: walk(a[key], b[key], child)
                if len(result) >= limit: break
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f'{prefix}[{index}]')
                if len(result) >= limit: break
        else: result.append(prefix or '<resource>')
    walk(before, after, path)
    return result[:limit]


def duration_seconds(value):
    """Parse bounded ISO time durations exactly; reject missing or ambiguous values."""
    if not isinstance(value, str) or len(value) > 64: return None
    match = re.fullmatch(r'PT(?:(\d+(?:[.]\d+)?)H)?(?:(\d+(?:[.]\d+)?)M)?(?:(\d+(?:[.]\d+)?)S)?', value)
    if not match or not any(match.groups()): return None
    return sum(Decimal(v or '0') * scale for v, scale in zip(match.groups(), [3600, 60, 1]))


def compare_eviction_duration(old, new):
    """Normalize only an equal, known grace duration; report safe values otherwise."""
    key = 'node_eviction_node_pool_settings'
    a, b = old.get(key), new.get(key)
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != 1 or len(b) != 1: return ''
    x, y = a[0].get('eviction_grace_duration'), b[0].get('eviction_grace_duration')
    if x == y: return ''
    seconds_x, seconds_y = duration_seconds(x), duration_seconds(y)
    if seconds_x is not None and seconds_x == seconds_y:
        b[0]['eviction_grace_duration'] = x
        return ''
    def safe(value):
        return value if duration_seconds(value) is not None else ('unset' if value is None else 'invalid duration')
    return f' Eviction grace duration: {safe(x)} → {safe(y)}.'


def validate_plan(plan, pool_id, before_size, target):
    """Allow exactly one in-place pool-size edit. Full plans never reach the browser."""
    changes = []
    for item in plan.get('resource_changes', []):
        if item.get('mode') == 'data': continue
        change = item.get('change', {})
        if change.get('actions') == ['no-op']: continue
        if item.get('address') != POOL_ADDRESS or change.get('actions') != ['update'] or change.get('importing'):
            if item.get('address') == 'oci_core_instance.operator':
                fields = changed_paths(change.get('before'), change.get('after'))
                raise ProviderError('Scaling was not applied: the plan contains an unrelated operator VM change (' + ', '.join(change.get('actions', [])) + '). Changed fields: ' + (', '.join(fields) or 'not available') + '. If source_details[0].source_id changed, discard the scaling target, then review Advanced operator VM → Approved operator image override in Resource Manager. Select the existing recorded image by name and verify its OCID; do not choose a newer image during scaling. Generate a fresh size-only plan.')
            raise ProviderError('Plan contains unrelated changes or a replacement: ' + str(item.get('address'))[:200])
        old, new = deepcopy(change.get('before')), deepcopy(change.get('after'))
        if not old or not new or old.get('id') != pool_id or new.get('id') != pool_id:
            raise ProviderError('Plan does not update the configured managed node pool in place.')
        try:
            if len(old['node_config_details']) != 1 or len(new['node_config_details']) != 1: raise ValueError()
            a = old['node_config_details'][0].pop('size')
            b = new['node_config_details'][0].pop('size')
            if type(a) is not int or type(b) is not int or a != before_size or b != target: raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ProviderError('Plan node counts differ from the requested capacity change.')
        cycling = new.get('node_pool_cycling_details', [])
        if len(cycling) != 1 or cycling[0].get('is_node_cycling_enabled') is not False:
            raise ProviderError('Disable node cycling in Terraform before capacity scaling; scaling must not rotate existing workers.')
        # Nodes are a read-only computed inventory and become unknown when size changes.
        for doc in [old, new]: doc.pop('nodes', None)
        duration_detail = compare_eviction_duration(old, new)
        if old != new:
            raise ProviderError('Plan changes node-pool settings other than its size. Changed fields (up to 30): ' + ', '.join(changed_paths(old, new)) + '.' + duration_detail + ' Review these fields in the Resource Manager plan. Discard this scaling target and apply any intended maintenance separately at the current node count.')
        unknown = deepcopy(change.get('after_unknown', {})); unknown.pop('nodes', None)
        def unresolved(v):
            if isinstance(v, dict): return any(unresolved(x) for x in v.values())
            if isinstance(v, list): return any(unresolved(x) for x in v)
            return v is True
        if unresolved(unknown): raise ProviderError('Plan contains unknown node-pool configuration changes.')
        changes.append({'address': POOL_ADDRESS, 'action': 'update', 'before': a, 'after': b})
    if len(changes) != 1: raise ProviderError('Expected exactly one node-pool size update.')
    return {'changes': changes, 'plan_sha256': digest(plan), 'before': before_size, 'target': target}


class ORMGateway:
    """The SDK is already pinned with the operator OCI CLI runtime."""
    def __init__(self, region):
        import oci
        self.oci = oci
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        self.client = oci.resource_manager.ResourceManagerClient(
            {'region': region}, signer=signer, timeout=(10, 30), retry_strategy=oci.retry.NoneRetryStrategy())

    def call(self, name, *args, **kwargs):
        try: return getattr(self.client, name)(*args, **kwargs)
        except Exception as exc:
            code = re.sub(r'[^A-Za-z0-9_]', '', str(getattr(exc, 'code', type(exc).__name__)))[:80]
            field = 'stack_id' if name in {'get_stack', 'get_stack_tf_state'} else ''
            if code in {'NotAuthorizedOrNotFound', 'NotAuthorized', 'NotAuthenticated'}:
                message = f'Resource Manager {name}: OCI denied access or could not find the resource ({code}).'
                next_action = 'Check the Resource Manager stack OCID, region, and operator IAM access. OCI does not distinguish an inaccessible resource from a missing resource in this response.'
            else:
                message = f'Resource Manager {name} failed ({code}).'
                next_action = 'Check the OCI job or service status and the operator connection.'
            raise ActionError(message, field=field, code=code, next_action=next_action) from None

    def stack(self, sid):
        r = self.call('get_stack', sid)
        return self.oci.util.to_dict(r.data), r.headers.get('etag')

    def update(self, sid, variables, etag):
        if not etag: raise ProviderError('Stack has no concurrency token; refusing to overwrite variables.')
        self.call('update_stack', sid, self.oci.resource_manager.models.UpdateStackDetails(variables=variables), if_match=etag)

    def jobs(self, sid):
        r = self.call('list_jobs', stack_id=sid, limit=100, sort_by='TIMECREATED', sort_order='DESC')
        return self.oci.util.to_dict(r.data)

    def job(self, jid): return self.oci.util.to_dict(self.call('get_job', jid).data)

    def create(self, sid, kind, operation_id, plan_id=''):
        models = self.oci.resource_manager.models
        details = (models.CreatePlanJobOperationDetails(operation='PLAN', is_provider_upgrade_required=False) if kind == 'PLAN'
                   else models.CreateApplyJobOperationDetails(operation='APPLY', execution_plan_strategy='FROM_PLAN_JOB_ID',
                        execution_plan_job_id=plan_id, is_provider_upgrade_required=False))
        r = self.call('create_job', models.CreateJobDetails(stack_id=sid,
                      display_name=f'rdma-scale-{kind.lower()}-{operation_id}', job_operation_details=details,
                      freeform_tags={'oke-rdma-operation-id': operation_id}))
        return self.oci.util.to_dict(r.data)

    def document(self, name, identifier):
        r = self.call(name, identifier, **({'tf_plan_format':'JSON'} if name == 'get_job_tf_plan' else {}))
        try:
            # OCI SDK streams expose a requests Response. raw.read() otherwise
            # bypasses HTTP Content-Encoding decompression (including gzip).
            limit = 10_000_000
            raw = r.data.raw.read(limit + 1, decode_content=True)
            if len(raw) > limit: raise ProviderError('Terraform document exceeds the capacity review size limit.')
            # Also accept a gzip artifact without HTTP Content-Encoding. Bound
            # expanded bytes rather than using unbounded gzip.decompress().
            if raw.startswith(b'\x1f\x8b'):
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(limit + 1)
                if len(raw) > limit: raise ProviderError('Terraform document exceeds the capacity review size limit.')
            document = json.loads(raw)
            if not isinstance(document, dict): raise ValueError('Expected a JSON object')
            return document
        except ProviderError:
            raise
        except Exception:
            # State and plans can contain secrets; never expose parser excerpts.
            raise ProviderError('Resource Manager Terraform document could not be decoded as JSON. Check the state or JSON plan artifact in Resource Manager.') from None
        finally: r.data.close()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as stream:
        os.chmod(tmp, 0o600)
        json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class Capacity:
    def __init__(self, provider, state_dir, gateway=None):
        self.p, self.s, self.root = provider, provider.s, Path(state_dir)
        self._gateway = gateway
        self.connection = self.root / 'capacity-connection.json'
        self.journal = self.root / 'capacity-operation.json'

    @property
    def gateway(self):
        if self._gateway is None: self._gateway = ORMGateway(self.s.region)
        return self._gateway

    def operation(self): return json.loads(self.journal.read_text()) if self.journal.exists() else None
    def save(self, op):
        op['updated_at'] = time.time()
        timeline = op.setdefault('timeline', [])
        if not timeline or timeline[-1]['phase'] != op['phase']:
            timeline.append({'phase': op['phase'], 'observed_at': op['updated_at']})
        atomic(self.journal, op)
    def stack_id(self):
        return (json.loads(self.connection.read_text())['stack_id'] if self.connection.exists() else self.s.capacity_stack_ocid)
    def pending(self):
        op = self.operation()
        return op if op and op['phase'] not in TERMINAL else None
    def guard(self, action):
        from .replacement import Replacement
        Replacement(self.p).guard(action)
        from .recovery import guard
        guard(action)
        if self.pending() and action not in {'capacity_apply', 'capacity_discard', 'capacity_resume', 'capacity_close', 'check_hosts', 'preflight', 'node_diagnostic'}:
            raise ProviderError('A capacity operation needs review or reconciliation. Apply, discard, or resume it before another cluster change.')

    def verify_stack(self, sid):
        if not re.fullmatch(STACK_PATTERN, sid): raise ActionError('Enter a Resource Manager stack OCID, starting with ocid1.ormstack.', field='stack_id', code='invalid_stack_ocid')
        stack, etag = self.gateway.stack(sid)
        if stack.get('id') != sid or stack.get('compartment_id') != self.s.compartment_id or stack.get('lifecycle_state') != 'ACTIVE':
            raise ActionError('The selected stack is not ACTIVE in this environment’s deployment compartment.', field='stack_id', code='stack_identity', next_action='Select the stack used to provision this cluster and wait for it to become ACTIVE.')
        state = self.gateway.document('get_stack_tf_state', sid)
        resources = {r.get('type')+'.'+r.get('name'): [x.get('attributes', {}).get('id') for x in r.get('instances', [])]
                     for r in state.get('resources', []) if not r.get('module') and r.get('mode', 'managed') == 'managed'}
        if self.s.node_pool_id not in resources.get(POOL_ADDRESS, []) or self.s.cluster_id not in resources.get('oci_containerengine_cluster.this', []):
            raise ActionError('The stack state does not own this OKE cluster and RDMA node pool.', field='stack_id', code='stack_ownership', next_action='Use the Resource Manager stack that provisioned this environment, not a cluster or node-pool OCID.')
        return stack, etag

    def verify_image_pin(self, sid, stack):
        selected = (stack.get('variables') or {}).get('operator_image_ocid', '')
        state = self.gateway.document('get_stack_tf_state', sid)
        images = [i.get('attributes', {}).get('source_details', [{}])[0].get('source_id')
                  for r in state.get('resources', [])
                  if r.get('type') == 'oci_core_instance' and r.get('name') == 'operator' and not r.get('module')
                  for i in r.get('instances', []) if i.get('attributes', {}).get('source_details')]
        if len(images) != 1 or not images[0]:
            raise ProviderError('Cannot establish the recorded operator image from stack state. Review stack ownership before scaling.')
        if not selected or selected != images[0]:
            raise ProviderError('Operator image must be pinned before scaling. Saved: ' + (selected or 'empty') +
                                '; recorded: ' + images[0] + '. In Resource Manager, Advanced operator VM → Approved operator image override, select the recorded image by name and verify its OCID. No scale target was changed.')
        return {'selected': selected, 'recorded': images[0]}

    def connect(self, sid):
        from .db import idle_guard
        if self.s.mode == 'demo': raise ProviderError('Capacity changes are disabled in demo mode.')
        self.guard('capacity_connect')
        self.verify_stack(sid)
        with idle_guard():
            self.guard('capacity_connect')
            atomic(self.connection, {'stack_id': sid})
        return {'saved': True, 'stack_id': sid}

    def pool(self):
        r = self.p.oci(['ce','node-pool','get','--node-pool-id',self.s.node_pool_id], check=False, timeout=35)
        if r.returncode: raise ProviderError('Cannot read the managed node pool. Grant read cluster-node-pools access to this operator.')
        def normalize(v):
            if isinstance(v, dict): return {k.replace('-','_'):normalize(x) for k,x in v.items()}
            if isinstance(v, list): return [normalize(x) for x in v]
            return v
        pool = normalize(json.loads(r.stdout)['data'])
        if pool.get('id') != self.s.node_pool_id or pool.get('cluster_id') != self.s.cluster_id:
            raise ProviderError('Managed node-pool identity differs from the configured environment.')
        return pool

    def members(self, pool):
        ids = {n['id'] for n in pool.get('nodes', []) if n.get('lifecycle_state') not in {'DELETED','DELETING'}}
        ips = {n.get('private_ip') for n in pool.get('nodes', []) if n['id'] in ids}
        return [n for n in self.p._rdma_nodes() if self.p._node_instance_id(n) in ids or self.p._node_internal_ip(n) in ips or n['metadata']['name'] in ips]

    def checks(self, pool, target):
        from .provider import ready
        if type(target) is not int or not 0 <= target <= 64: raise ProviderError('Target node count must be between 0 and 64.')
        before = pool['node_config_details']['size']
        nodes = self.members(pool)
        if len(nodes) != before or any(not ready(n) or n.get('spec', {}).get('unschedulable') for n in nodes):
            raise ProviderError('Wait for all current pool workers to be Ready and schedulable before scaling.')
        if pool.get('node_pool_cycling_details', {}).get('is_node_cycling_enabled') is True:
            raise ProviderError('Node cycling is enabled. OCI Terraform provider 9.9 sends this flag with node-pool updates, including a size change. Set is_node_cycling_enabled=false in the existing stack and review/apply that baseline at the current node count before scaling.')
        if pool.get('lifecycle_state') != 'ACTIVE': raise ProviderError('The managed pool is not ACTIVE.')
        inv = self.p.inventory()
        if target > before:
            names = {x['metadata']['name'] for x in nodes}
            problems = [n['name'] + ': ' + '; '.join(n.get('host_issues', []))
                        for n in inv['nodes'] if n['name'] in names and n.get('host_issues')]
            if problems:
                raise ProviderError('Scale-out requires healthy existing workers. ' + ' | '.join(problems)[:3000])
            if before > 0 and not inv['driver_ready']:
                raise ProviderError('Complete Dranet setup and resolve driver findings before scaling out.')
        if target < before:
            eviction = pool.get('node_eviction_node_pool_settings') or {}
            duration=re.fullmatch(r'PT(?:(\d+(?:[.]\d+)?)H)?(?:(\d+(?:[.]\d+)?)M)?(?:(\d+(?:[.]\d+)?)S)?',str(eviction.get('eviction_grace_duration','')))
            seconds=sum(float(value or 0)*scale for value,scale in zip(duration.groups(),[3600,60,1])) if duration else 0
            if eviction.get('is_force_delete_after_grace_duration') is not False or not 0 < seconds <= 3600:
                raise ProviderError('Scale-in requires an explicit nonzero eviction grace period and force termination disabled in Terraform.')
            from .reset import requests_class
            classes = {'dra.net'} | {c['metadata']['name'] for c in self.p._json(['get','deviceclasses']).get('items', []) if 'dra.net' in json.dumps(c.get('spec', {}))}
            templates = self.p._json(['get','resourceclaimtemplates','-A']).get('items', [])
            if inv['claims'] or any(requests_class(t.get('spec', {}).get('spec', {}), classes) for t in templates):
                raise ProviderError('Release RDMA claims and remove claim templates before scale-in. Stop application controllers first.')
            # OKE chooses removals. Protect application pods across the entire pool;
            # managed infrastructure pods can drain subject to their disruption budgets.
            names = {n['metadata']['name'] for n in nodes}
            pods = self.p._json(['get','pods','-A']).get('items', [])
            busy = [p['metadata']['name'] for p in pods if p.get('spec', {}).get('nodeName') in names
                    and p['metadata'].get('namespace') not in {'kube-system','cert-manager','node-feature-discovery'}
                    and p.get('status', {}).get('phase') not in {'Succeeded','Failed'}
                    and not any(o.get('kind') == 'DaemonSet' for o in p['metadata'].get('ownerReferences', []))]
            if busy: raise ProviderError('Scale-in requires an idle RDMA pool; application/system pods remain: ' + ', '.join(busy[:8]))
        return nodes

    def job_list(self, sid):
        jobs = self.gateway.jobs(sid)
        return sorted(jobs, key=lambda j:str(j.get('time_created','')), reverse=True)
    def no_active(self, sid):
        jobs = self.job_list(sid)
        if any(j.get('lifecycle_state') not in JOB_TERMINAL for j in jobs):
            raise ProviderError('A Resource Manager job is active on this stack. Wait for it to finish.')
        return jobs

    def capacity_report(self, pool, target):
        from .capacity_report import collect
        return collect(self.p, pool, target)

    def require_capacity_report(self, report):
        from .capacity_report import describe
        if report['status'] not in {'available', 'not_required'}:
            raise ProviderError('Scale-up capacity check ' + report['status'] + ': ' + describe(report) + ' No new infrastructure operation was submitted. Retry the report after resolving this finding.')

    def readiness(self, target):
        """Read-only prerequisites; write permissions cannot be proven without a write."""
        checks = []
        capacity_report = None
        def record(name, status, detail, action=''):
            checks.append({'name': name, 'status': status, 'detail': detail, 'next_action': action})
        sid = self.stack_id()
        try:
            self.guard('capacity_plan')
        except ProviderError as exc:
            record('Operation lock', 'block', str(exc), 'Finish the recorded replacement or recovery before scaling.')
        if not sid or self.s.mode == 'demo':
            record('Stack connection', 'block', 'Connect the provisioned Resource Manager stack in a live environment.', 'Save the stack connection first.')
            return {'target': target, 'checked_at': time.time(), 'can_plan': False, 'checks': checks}
        try:
            self.verify_stack(sid)
            record('Stack ownership and read access', 'pass', 'The stack state owns this cluster and managed pool.')
        except ProviderError as exc:
            record('Stack ownership and read access', 'block', str(exc), 'Review the stack OCID, region, state, and operator IAM policy.')
        try:
            stack, _ = self.verify_stack(sid)
            self.verify_image_pin(sid, stack)
            record('Operator image pin', 'pass', 'Saved image matches the recorded operator image.')
        except ProviderError as exc:
            record('Operator image pin', 'block', str(exc), 'Save the existing image selection before generating a scale plan.')
        try:
            self.no_active(sid)
            record('Resource Manager jobs', 'pass', 'No active job on the connected stack.')
        except ProviderError as exc:
            record('Resource Manager jobs', 'block', str(exc), 'Resolve or wait for the recorded Resource Manager job.')
        try:
            pool = self.pool()
            if pool['node_config_details']['size'] == target:
                record('Requested capacity', 'block', 'Target equals the current node-pool size.', 'Choose a different target count.')
            else:
                record('Requested capacity', 'pass', f"{pool['node_config_details']['size']} → {target} workers")
            capacity_report = self.capacity_report(pool, target)
            from .capacity_report import describe
            record('OCI scale-up capacity report', 'pass' if capacity_report['status'] in {'available', 'not_required'} else 'block', describe(capacity_report), capacity_report['note'])
            self.checks(pool, target)
            record('Pool, workers and RDMA prerequisites', 'pass', 'Pool is ACTIVE. Existing-worker and direction-specific guards passed; an empty pool needs no running driver.')
        except ProviderError as exc:
            record('Pool, workers and RDMA prerequisites', 'block', str(exc), 'Resolve this prerequisite, then check readiness again.')
        record('IAM write access', 'unverified', 'Read access was tested. Stack-update and PLAN/APPLY permissions are not proven by this read-only check.', 'Install the documented capacity IAM policy. OCI will enforce write permissions when an operation is submitted.')
        record('Terraform plan', 'unverified', 'Only the generated plan can establish size-only changes, disabled cycling and no replacements.', 'Review the plan before applying. No infrastructure is changed by this readiness check.')
        return {'target': target, 'checked_at': time.time(), 'can_plan': not any(c['status'] == 'block' for c in checks), 'checks': checks, 'capacity_report': capacity_report}

    def status(self):
        if self.s.mode == 'demo': return {'configured': False, 'stack_id': '', 'minimum': 0, 'maximum': 64, 'operation': None, 'message': 'Capacity operations require a live environment.'}
        sid = self.stack_id()
        data = {'configured':bool(sid), 'stack_id':sid, 'minimum':0, 'maximum':64, 'operation':self.operation()}
        if not sid: return data
        try:
            pool = self.pool()
            from .provider import ready
            members = self.members(pool)
            data.update(desired=pool['node_config_details']['size'], observed=len(members), ready=sum(ready(n) for n in members), pool_members=[n['metadata']['name'] for n in members])
            data['workers'] = [{'instance_id': n.get('id'), 'address': n.get('private_ip', ''), 'lifecycle_state': n.get('lifecycle_state', 'UNKNOWN')} for n in pool.get('nodes', []) if n.get('lifecycle_state') != 'DELETED']
        except ProviderError as exc: data['error'] = str(exc)
        from .performance import capacity_timing
        from .db import list_jobs
        data['timing']=capacity_timing(data.get('operation'),list_jobs(100))
        return data

    def wait_job(self, op, kind, seconds):
        jid = op[kind.lower()+'_job_id']
        deadline = time.monotonic() + seconds
        while True:
            job = self.gateway.job(jid)
            if job.get('stack_id') != op['stack_id'] or job.get('operation') != kind:
                raise ProviderError('OCI job identity differs from the recorded capacity operation.')
            state = job.get('lifecycle_state')
            self.p.report_progress('Resource Manager ' + kind.lower(), 1 if kind == 'PLAN' else 2, 5, 'OCI job: ' + str(state))
            if state in JOB_TERMINAL:
                if state != 'SUCCEEDED':
                    op['phase'] = kind+'_FAILED'; self.save(op)
                    raise ProviderError('Resource Manager job '+str(state)+'. Inspect its OCI job before continuing.')
                return job
            if time.monotonic() >= deadline: raise ProviderError('OCI job is still active. Use Resume to monitor the recorded job; do not submit another apply.')
            time.sleep(10)

    def review(self, op):
        self.wait_job(op, 'PLAN', 1200)
        reviewed_stack, reviewed_etag = self.gateway.stack(op['stack_id'])
        if stack_fingerprint(reviewed_stack) != op['planned_fingerprint']:
            op['phase']='PLAN_BLOCKED'; self.save(op)
            raise ProviderError('Stack inputs changed while planning. Review the stack with its administrator.')
        op['stack_etag'] = reviewed_etag
        plan = self.gateway.document('get_job_tf_plan', op['plan_job_id'])
        try: op['review'] = validate_plan(plan, self.s.node_pool_id, op['before'], op['target'])
        except ProviderError as exc:
            op['phase']='PLAN_BLOCKED'; op['blocked_reason']=str(exc); self.save(op); raise
        stack, _ = self.gateway.stack(op['stack_id'])
        if stack_fingerprint(stack) != op['planned_fingerprint']:
            op['phase']='PLAN_BLOCKED'; self.save(op)
            raise ProviderError('Stack inputs changed while planning. Discard and request a fresh plan.')
        op.update(phase='AWAITING_REVIEW', expires_at=time.time()+1800); self.save(op)
        return {'status':'passed', 'message':'Plan ready for review. Infrastructure has not been resized. Apply this exact plan or discard the target.', 'capacity':op}

    def plan(self, target):
        if self.s.mode == 'demo': raise ProviderError('Capacity changes are disabled in demo mode.')
        if self.pending(): raise ProviderError('Finish or discard the existing capacity operation first.')
        sid = self.stack_id()
        stack, etag = self.verify_stack(sid)
        self.verify_image_pin(sid, stack)
        self.no_active(sid)
        pool = self.pool(); before = pool['node_config_details']['size']
        if target == before: raise ProviderError('Target already matches the managed node-pool size.')
        nodes = self.checks(pool, target)
        report = self.capacity_report(pool, target)
        self.require_capacity_report(report)
        variables = dict(stack.get('variables') or {})
        original = variables.get('node_count'); variables['node_count']=str(target)
        planned = {**stack,'variables':variables}
        op = {'operation_id':secrets.token_hex(32),'phase':'UPDATING_VARIABLES', 'stack_id':sid,
              'cluster_id':self.s.cluster_id,'node_pool_id':self.s.node_pool_id,
              'capacity_report':report,'before':before,'target':target,'original_node_count':original,'base_fingerprint':stack_fingerprint(stack),
              'planned_fingerprint':stack_fingerprint(planned),'created_at':time.time(),
              'before_node_uids':[n['metadata']['uid'] for n in nodes], 'before_node_boots':{n['metadata']['uid']:n.get('status',{}).get('nodeInfo',{}).get('bootID') for n in nodes}, 'plan_job_id':'', 'apply_job_id':''}
        self.save(op)
        self.p.report_progress('Updating stack target count',0,5,'Only node_count is changed; Terraform still owns capacity')
        self.gateway.update(sid, variables, etag)
        updated, updated_etag = self.gateway.stack(sid)
        if stack_fingerprint(updated) != op['planned_fingerprint']:
            raise ProviderError('Stack inputs changed while updating the target. Review the pending operation before proceeding.')
        op['stack_etag'] = updated_etag
        op['phase']='SUBMITTING_PLAN'; self.save(op)
        job = self.gateway.create(sid, 'PLAN', op['operation_id'])
        op.update(phase='PLAN_RUNNING',plan_job_id=job['id']); self.save(op)
        return self.review(op)

    def selected_operation(self, operation_id):
        op = self.pending()
        if op and (op.get('cluster_id') != self.s.cluster_id or op.get('node_pool_id') != self.s.node_pool_id):
            raise ProviderError('The recorded capacity operation belongs to another environment.')
        if not op or op['operation_id'] != operation_id: raise ProviderError('Capacity operation changed. Refresh and review it again.')
        return op

    def apply(self, operation_id):
        op = self.selected_operation(operation_id)
        if op['phase'] != 'AWAITING_REVIEW' or time.time() > op['expires_at']:
            raise ProviderError('The scale plan is not approved-ready or has expired. Discard it and generate a fresh plan.')
        stack, current_etag = self.verify_stack(op['stack_id'])
        jobs = self.no_active(op['stack_id'])
        if not current_etag or current_etag != op.get('stack_etag') or stack_fingerprint(stack) != op['planned_fingerprint'] or not jobs or jobs[0]['id'] != op['plan_job_id']:
            raise ProviderError('Stack inputs or Terraform job history changed after planning. Request a fresh plan.')
        artifact = self.gateway.document('get_job_tf_plan', op['plan_job_id'])
        reviewed = validate_plan(artifact,self.s.node_pool_id,op['before'],op['target'])
        if reviewed != op['review']: raise ProviderError('Plan artifact differs from the reviewed plan.')
        pool=self.pool()
        if pool['node_config_details']['size'] != op['before']: raise ProviderError('Actual capacity changed after planning.')
        self.checks(pool,op['target'])
        op['apply_capacity_report'] = self.capacity_report(pool, op['target'])
        self.save(op)
        self.require_capacity_report(op['apply_capacity_report'])
        op['phase']='SUBMITTING_APPLY';op.setdefault('milestones',{}).setdefault('apply_submitted_at',time.time());self.save(op)
        job=self.gateway.create(op['stack_id'],'APPLY',op['operation_id'],op['plan_job_id'])
        op.update(phase='APPLY_RUNNING',apply_job_id=job['id']);self.save(op)
        return self.finish(op)

    def finish(self, op):
        self.wait_job(op,'APPLY',5400)
        op.setdefault('milestones',{}).setdefault('apply_finished_observed_at',time.time())
        op['phase']='VERIFYING_NODES';self.save(op)
        from .provider import ready
        deadline=time.monotonic()+900
        while True:
            pool=self.pool();nodes=self.members(pool)
            op['node_stage_evidence']=[{'name':n['metadata']['name'],'uid':n['metadata']['uid'],'registered_at':n['metadata'].get('creationTimestamp'),'ready_at':next((c.get('lastTransitionTime') for c in n.get('status',{}).get('conditions',[]) if c.get('type')=='Ready' and c.get('status')=='True'),None)} for n in nodes if n['metadata']['uid'] not in op['before_node_uids']]
            self.save(op)
            removals_pending = any(n.get('lifecycle_state') != 'DELETED' for n in pool.get('nodes', [])) if op['target'] == 0 else False
            if pool['lifecycle_state']=='ACTIVE' and not removals_pending and pool['node_config_details']['size']==op['target'] and len(nodes)==op['target'] and all(ready(n) for n in nodes):break
            self.p.report_progress('Waiting for worker readiness',3,5,f'{sum(ready(n) for n in nodes)} Ready / {op["target"]} requested')
            if time.monotonic()>=deadline:raise ProviderError('Terraform apply succeeded; workers are still joining. Resume verifies readiness without applying again.')
            time.sleep(10)
        op.setdefault('milestones', {}).setdefault('kubernetes_ready_observed_at', time.time()); self.save(op)
        if op['target'] == 0:
            op.update(phase='COMPLETE', readiness='paused', new_nodes=[]); self.save(op)
            return {'status':'passed', 'message':'Worker pool paused at zero. Cluster and operator remain available; scale up before RDMA setup or validation.', 'capacity':op, 'hosts':[], 'nodes_needing_attention':[]}
        if not self.p.inventory().get('driver_installed', True):
            op.update(phase='COMPLETE', readiness='setup_required', new_nodes=[n['metadata']['name'] for n in nodes]); self.save(op)
            return {'status':'passed', 'message':'Workers are Ready. Complete host checks and Dranet setup before RDMA validation.', 'capacity':op, 'hosts':[], 'nodes_needing_attention':[]}
        new=[n['metadata']['name'] for n in nodes if n['metadata']['uid'] not in op['before_node_uids'] or n.get('status',{}).get('nodeInfo',{}).get('bootID') != op.get('before_node_boots',{}).get(n['metadata']['uid'])]
        op['new_node_identities']=[{'name':n['metadata']['name'],'uid':n['metadata']['uid'],'boot_id':n.get('status',{}).get('nodeInfo',{}).get('bootID')} for n in nodes if n['metadata']['name'] in new]
        hosts=self.p.check_hosts(node_names=new) if new else {'status':'passed','hosts':[]}
        op['host_readiness']=hosts;self.save(op)
        if hosts['status']!='passed':
            op.update(phase='COMPLETE',readiness='failed',new_nodes=new);self.save(op)
            return {'status':'failed','message':'Capacity applied, but host RDMA failed. Review diagnostics and recover affected workers; no additional apply is needed.','capacity':op,'hosts':hosts.get('hosts',[]),'nodes_needing_attention':new}
        op.setdefault('milestones',{}).setdefault('host_ready_observed_at',time.time());self.save(op)
        deadline=time.monotonic()+900
        while op['target'] > op['before']:
            published=self.p.inventory()
            current_names={n['metadata']['name'] for n in nodes}
            rows={n['name']:n for n in published['nodes']}
            if published['driver_ready'] and all(name in rows and not rows[name]['issues'] and any(d.get('valid',False) for d in rows[name]['devices']) for name in current_names):break
            self.p.report_progress('Waiting for Dranet and RDMA device publication',3,5,'Worker initialization and OCI RDMA authentication are still in progress')
            if time.monotonic()>=deadline:raise ProviderError('Terraform applied; RDMA devices are still initializing. Resume readiness verification without applying again.')
            time.sleep(10)
        op.setdefault('milestones', {}).setdefault('device_check_finished_at', time.time()); self.save(op)
        new=[n['metadata']['name'] for n in nodes if n['metadata']['uid'] not in op['before_node_uids'] or n.get('status',{}).get('nodeInfo',{}).get('bootID') != op.get('before_node_boots',{}).get(n['metadata']['uid'])]
        self.p.report_progress('Checking new worker RDMA configuration',4,5,f'{len(new)} new workers; existing evidence is retained')
        inv=self.p.inventory()
        by_name={n['name']:n for n in inv['nodes']}
        problems=[name for name in {n['metadata']['name'] for n in nodes} if name not in by_name or by_name[name]['issues'] or by_name[name]['host_issues'] or not by_name[name]['devices']]
        passed=hosts['status']=='passed' and not problems and inv['driver_ready']
        op.update(phase='COMPLETE',readiness='passed' if passed else 'failed',new_nodes=new);self.save(op)
        return {'status':'passed' if passed else 'failed','message':('Capacity applied and RDMA readiness verified.' if passed else 'Capacity applied; new-node RDMA readiness needs attention. Infrastructure was not rolled back.'),
                'capacity':op,'hosts':hosts.get('hosts',[]),'nodes_needing_attention':problems}

    def discover(self, op, kind):
        expected=f'rdma-scale-{kind.lower()}-{op["operation_id"]}'
        matches=[j for j in self.job_list(op['stack_id']) if j.get('display_name')==expected and j.get('operation')==kind]
        if len(matches)!=1:raise ProviderError('Submission outcome is uncertain. Inspect Resource Manager for the recorded operation; no job will be resubmitted automatically.')
        op[kind.lower()+'_job_id']=matches[0]['id'];op['phase']=kind+'_RUNNING';self.save(op)

    def resume(self, operation_id):
        op=self.selected_operation(operation_id)
        if op['phase'] in {'SUBMITTING_PLAN','SUBMITTING_APPLY'}:self.discover(op,'PLAN' if op['phase']=='SUBMITTING_PLAN' else 'APPLY')
        if op['phase']=='PLAN_RUNNING':return self.review(op)
        if op['phase'] in {'APPLY_RUNNING','VERIFYING_NODES','APPLY_FAILED'}:return self.finish(op)
        raise ProviderError('This operation requires review or discard, rather than resume.')

    def discard(self, operation_id):
        op=self.selected_operation(operation_id)
        if op.get('apply_job_id') or op['phase']=='SUBMITTING_APPLY':
            raise ProviderError('An apply was submitted. Resume and inspect that job; discard cannot undo applied infrastructure.')
        if op['phase']=='SUBMITTING_PLAN':self.discover(op,'PLAN')
        self.no_active(op['stack_id'])
        stack,etag=self.gateway.stack(op['stack_id'])
        current=stack_fingerprint(stack)
        if current not in {op['planned_fingerprint'],op['base_fingerprint']}:
            raise ProviderError('Stack inputs changed externally; refusing to overwrite them. Reconcile the stack with its administrator.')
        if current!=op['base_fingerprint']:
            if op.get('stack_etag') and etag != op['stack_etag']:
                raise ProviderError('Stack configuration changed after the scale plan. Refusing to overwrite its target.')
            variables=dict(stack.get('variables') or {})
            if op['original_node_count'] is None:variables.pop('node_count',None)
            else:variables['node_count']=op['original_node_count']
            self.gateway.update(op['stack_id'],variables,etag)
        op['phase']='DISCARDED';self.save(op)
        return {'status':'passed','message':'Scale plan discarded; the previous stack target is restored. No node resize was applied.','capacity':op}


    def close(self, operation_id):
        op = self.selected_operation(operation_id)
        if not op.get('apply_job_id'): raise ProviderError('Only a terminal failed apply can be closed; discard unapplied plans instead.')
        self.no_active(op['stack_id'])
        job = self.gateway.job(op['apply_job_id'])
        if job.get('stack_id') != op['stack_id'] or job.get('operation') != 'APPLY' or job.get('lifecycle_state') not in {'FAILED','CANCELED'}:
            raise ProviderError('The recorded apply is not a terminal failed or canceled job.')
        pool = self.pool()
        op.update(phase='COMPLETE', readiness='not_verified', actual_size=pool['node_config_details']['size'])
        self.save(op)
        return {'status':'passed', 'capacity':op, 'message':'Failed OCI apply acknowledged. No infrastructure or stack variables were changed. Review actual capacity and create a new plan to reconcile the target.'}
