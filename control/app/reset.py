"""Previewed RDMA setup reset. Terraform infrastructure is outside its scope."""
from __future__ import annotations
import hashlib
import json
import re
import time
from urllib.parse import quote
import yaml
from . import dranet
from .config import _runtime_path
from .provider_base import ProviderError

PART_OF = 'oke-rdma-quickstart'
RETAINED = [
    'Terraform infrastructure: OKE, workers, Compute Cluster, networking, operator and Bastion',
    'Host image, Oracle Cloud Agent, RDMA authentication, drivers, shared RDMA mode and node labels',
    'Namespaces, registry pull secrets, unrelated applications and Kubernetes system components',
    'Operation history and diagnostic reports for audit and download',
]
LOCAL_FILES = ['host-readiness.json', 'dranet-values.yaml']


def owned(obj, diagnostic=False):
    labels = obj.get('metadata', {}).get('labels', {})
    if labels.get('app.kubernetes.io/part-of') != PART_OF:
        return False
    if diagnostic:
        return labels.get('app.kubernetes.io/name') == 'oke-rdma-node-diagnostic'
    return labels.get('app') == 'oke-dranet-validation'


def item(kind, obj):
    m = obj['metadata']
    return {'kind': kind, 'name': m['name'], 'namespace': m.get('namespace', ''), 'uid': m.get('uid', '')}


def requests_class(spec, classes):
    for request in spec.get('devices', {}).get('requests', []):
        alternatives = [request.get('exactly', request), *request.get('firstAvailable', [])]
        if any(r.get('deviceClassName') in classes for r in alternatives):
            return True
    return False


def plan(provider):
    s = provider.s
    blockers, targets = [], []
    if s.operator_kubernetes_access_mode != 'iam_admin':
        return {'can_reset': False, 'blockers': ['A cluster administrator must uninstall Dranet; this operator has restricted Kubernetes access.'],
                'targets': [], 'retained': RETAINED, 'plan_id': '', 'local_files': LOCAL_FILES}
    ds = provider._json(['-n', 'kube-system', 'get', 'daemonset', 'dranet'], optional=True)
    release = provider._run(['helm', 'status', 'dranet', '-n', 'kube-system', '-o', 'json'], check=False, timeout=30)
    release_info = None
    if release.returncode:
        if 'release: not found' not in (release.stderr + release.stdout).lower():
            raise ProviderError('Cannot verify the Dranet Helm release: ' + (release.stderr or release.stdout)[-1500:])
        if ds:
            blockers.append('Dranet exists without its expected Helm release. Have its administrator remove it.')
    else:
        info = json.loads(release.stdout)
        # Helm 3.19 deliberately strips Chart from `helm status` output.
        # Read its documented metadata endpoint and pin it to that revision.
        revision = info.get('version')
        if type(revision) is not int or revision < 1:
            raise ProviderError('Cannot verify the Dranet Helm release revision.')
        meta = json.loads(provider._run(['helm', 'get', 'metadata', 'dranet', '-n', 'kube-system',
                                        '--revision', str(revision), '-o', 'json'], timeout=30).stdout)
        release_info = {'name': info.get('name'), 'namespace': info.get('namespace'), 'revision': info.get('version'),
                        'chart': meta.get('chart'), 'version': meta.get('version')}
        if (release_info['name'], release_info['namespace'], meta.get('chart'), str(meta.get('version')).lstrip('v')) != ('dranet', 'kube-system', 'dranet', dranet.VERSION.lstrip('v')):
            blockers.append('The Helm release does not match the supported Dranet chart.')
        if (meta.get('name'), meta.get('namespace'), meta.get('revision')) != ('dranet', 'kube-system', revision):
            blockers.append('The Helm release metadata does not match the selected release revision.')
        manifest = provider._run(['helm', 'get', 'manifest', 'dranet', '-n', 'kube-system', '--revision', str(revision)], timeout=30).stdout
        for obj in yaml.safe_load_all(manifest):
            if not obj: continue
            target = item(obj['kind'], obj)
            targets.append(target)
            if target['kind'] not in {'DaemonSet', 'ServiceAccount', 'ClusterRole', 'ClusterRoleBinding'} or target['name'] != 'dranet' or target['namespace'] not in {'', 'kube-system'}:
                blockers.append('Unexpected Helm-managed resource: ' + target['kind'] + '/' + target['name'])
    if ds:
        template = ds.get('spec', {}).get('template', {}).get('spec', {})
        if template.get('nodeSelector') != {'oci-rdma': 'true', 'oke-rdma-mode': 'dranet'}:
            blockers.append('Dranet targets nodes outside this profile. Resolve driver ownership before resetting.')
    classes = provider._json(['get', 'deviceclasses']).get('items', [])
    driver_classes = {'dra.net'} | {c['metadata']['name'] for c in classes if 'dra.net' in json.dumps(c.get('spec', {}))}
    dc = next((c for c in classes if c['metadata']['name'] == 'dra.net'), None)
    if dc:
        if dc.get('spec') != dranet.DEVICE_CLASS['spec'] or dc['metadata'].get('labels', {}).get('app.kubernetes.io/part-of', PART_OF) != PART_OF or dc['metadata'].get('ownerReferences'):
            blockers.append('DeviceClass dra.net has different settings or ownership; it will not be deleted.')
        else:
            targets.append(item('DeviceClass', dc))
    for c in provider._json(['get', 'resourceclaims', '-A']).get('items', []):
        allocation = c.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
        uses_driver = requests_class(c.get('spec', {}), driver_classes) or any(x.get('driver') == 'dra.net' for x in allocation)
        if not uses_driver: continue
        if owned(c) and c['metadata']['namespace'] == s.workload_namespace:
            targets.append(item('ResourceClaim', c))
        else:
            blockers.append('Application RDMA claim must be released first: ' + c['metadata']['namespace'] + '/' + c['metadata']['name'])
    for t in provider._json(['get', 'resourceclaimtemplates', '-A']).get('items', []):
        if requests_class(t.get('spec', {}).get('spec', {}), driver_classes):
            blockers.append('Application RDMA claim template must be removed first: ' + t['metadata']['namespace'] + '/' + t['metadata']['name'])
    for p in provider._json(['get', 'pods', '-A']).get('items', []):
        ns = p['metadata']['namespace']
        test_claims = {(t['namespace'], t['name']) for t in targets if t['kind'] == 'ResourceClaim'}
        if not owned(p) and any((ns, ref.get('resourceClaimName')) in test_claims for ref in p.get('spec', {}).get('resourceClaims', [])):
            blockers.append('An application pod references a validation claim: ' + ns + '/' + p['metadata']['name'])
        if (owned(p) and ns == s.workload_namespace) or (owned(p, diagnostic=True) and ns == 'oke-rdma-diagnostics'):
            targets.append(item('Pod', p))
    selected = {n['metadata']['name'] for n in provider._selected()}
    for sl in provider._json(['get', 'resourceslices', '--field-selector=spec.driver=dra.net']).get('items', []):
        if sl['spec'].get('nodeName') not in selected:
            blockers.append('Dranet publishes devices outside the selected workers: ' + sl['metadata']['name'])
        else:
            targets.append(item('ResourceSlice', sl))
    # The token identifies exactly the previewed resources, not mutable status fields.
    targets.sort(key=lambda x: (x['kind'], x['namespace'], x['name']))
    plan_id = hashlib.sha256(json.dumps({'targets': targets, 'release': release_info}, sort_keys=True).encode()).hexdigest()
    return {'can_reset': not blockers, 'blockers': blockers, 'targets': targets, 'release': release_info,
            'plan_id': plan_id, 'retained': RETAINED, 'local_files': [*LOCAL_FILES, 'Saved RDMA settings (restore Terraform defaults)'],
            'message': 'Uninstall RDMA setup and return the guided workflow to its provisioned baseline. Quiesce application RDMA workloads before continuing.'}


def delete_target(provider, target):
    paths = {'Pod': ('/api/v1', 'pods'), 'ResourceClaim': ('/apis/resource.k8s.io/v1', 'resourceclaims'),
             'ResourceSlice': ('/apis/resource.k8s.io/v1', 'resourceslices'), 'DeviceClass': ('/apis/resource.k8s.io/v1', 'deviceclasses')}
    group, plural = paths[target['kind']]
    # UID preconditions prevent deleting a replacement object with the same name.
    if not target['uid']:
        raise ProviderError('Cannot safely delete a resource without its UID: ' + target['name'])
    ns = '/namespaces/' + quote(target['namespace'], safe='') if target['namespace'] else ''
    path = group + ns + '/' + plural + '/' + quote(target['name'], safe='')
    body = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': target['uid']}, 'propagationPolicy': 'Foreground'})
    response = provider.kubectl(['delete', '--raw', path, '-f', '-'], stdin=body, check=False, timeout=30)
    if response.returncode and 'NotFound' not in response.stderr:
        raise ProviderError((response.stderr or response.stdout)[-2000:])
    args = (['-n', target['namespace']] if target['namespace'] else [])
    provider.kubectl([*args, 'wait', '--for=delete', plural + '/' + target['name'], '--timeout=60s'], timeout=75)


def execute(provider, plan_id, state_dir):
    provider.report_progress('Verifying reset preview and application claims', 0, 5)
    current = plan(provider)
    if current['blockers']:
        raise ProviderError('Reset blocked: ' + ' '.join(current['blockers']))
    if not plan_id or plan_id != current['plan_id']:
        raise ProviderError('Reset inventory changed. Review a fresh reset preview before confirming.')
    result = {'status': 'failed', 'stage': 'Removing validation resources', 'removed': [], 'retained': RETAINED}
    try:
        provider.report_progress(result['stage'], 1, 5)
        for kind in ['Pod', 'ResourceClaim']:
            for target in current['targets']:
                if target['kind'] == kind:
                    delete_target(provider, target)
                    result['removed'].append(target)
        # Recheck immediately before stopping the driver. New application claims block reset.
        fresh = plan(provider)
        if fresh['blockers'] or fresh.get('release') != current.get('release'):
            raise ProviderError('Reset inventory changed before uninstall: ' + ' '.join(fresh['blockers']))
        result['stage'] = 'Uninstalling Dranet'
        provider.report_progress(result['stage'], 2, 5, 'Waiting for driver shutdown; up to three minutes')
        if current['release']:
            provider._run(['helm', 'uninstall', 'dranet', '-n', 'kube-system', '--wait', '--cascade', 'foreground', '--timeout', '3m'], timeout=210)
        if provider._json(['-n', 'kube-system', 'get', 'daemonset', 'dranet'], optional=True):
            raise ProviderError('Dranet is still present after uninstall. Reset did not complete.')
        result['removed'] += [t for t in current['targets'] if t['kind'] in {'DaemonSet', 'ServiceAccount', 'ClusterRole', 'ClusterRoleBinding'}]
        result['stage'] = 'Removing published RDMA devices and DeviceClass'
        provider.report_progress(result['stage'], 3, 5)
        for target in current['targets']:
            if target['kind'] in {'ResourceSlice', 'DeviceClass'}:
                delete_target(provider, target)
                result['removed'].append(target)
        remaining = provider._json(['get', 'resourceslices', '--field-selector=spec.driver=dra.net']).get('items', [])
        if remaining:
            raise ProviderError('RDMA devices are still published. Review reset again before proceeding.')
        result['stage'] = 'Restoring provisioned application defaults'
        provider.report_progress(result['stage'], 4, 5)
        for name in LOCAL_FILES:
            (state_dir / name).unlink(missing_ok=True)
        _runtime_path().unlink(missing_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        reset_at = time.time()
        marker = state_dir / 'rdma-reset.tmp'
        marker.write_text(json.dumps({'reset_at': reset_at}) + '\n')
        marker.chmod(0o600)
        marker.replace(state_dir / 'rdma-reset.json')
        result.update(status='passed', reset_at=reset_at,
                      message='RDMA setup reset completed. Terraform infrastructure is retained. Start again with the guided setup; prior measurements remain in the operations log.')
    except Exception as exc:
        result.update(status='failed', error=str(exc)[:4000],
                      message='Reset stopped before completion. Review the recorded stage and removed resources, then request a fresh preview. No force deletion was attempted.')
    return result
