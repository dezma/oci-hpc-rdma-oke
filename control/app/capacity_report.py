"""Compute capacity snapshots scoped to the managed pool; no provisioning."""
import json
import time
from .diagnostics import sanitize_output


def normalize(value):
    if isinstance(value, dict):
        return {k.replace('-', '_'): normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def collect(provider, pool, target):
    before = pool['node_config_details']['size']
    compartment = pool.get('compartment_id')
    report = {'checked_at': time.time(), 'region': provider.s.region,
              'shape': pool.get('node_shape'), 'compartment_id': compartment, 'additional_workers': target - before,
              'status': 'not_required', 'rows': [],
              'note': 'Compute availability snapshot only. Counts may not be disclosed. This does not reserve capacity or verify Compute Cluster placement, quota or RDMA health.'}
    if target <= before:
        return report
    report['status'] = 'unavailable'
    scopes = set()
    for placement in pool['node_config_details'].get('placement_configs', []):
        ad = placement.get('availability_domain')
        for fd in placement.get('fault_domains') or [None]:
            if ad:
                scopes.add((ad, fd))
    if not compartment or not report['shape'] or not scopes or len(scopes) > 9:
        report['error'] = 'Cannot establish node-pool compartment, shape and bounded AD/FD placement scopes from the managed pool.'
        return report
    config = pool.get('node_shape_config') or {}
    shape_config = {out: config[key] for key, out in [('ocpus', 'ocpus'), ('memory_in_gbs', 'memoryInGBs')] if config.get(key) is not None}
    report['shape_config'] = shape_config
    for ad, fd in sorted(scopes, key=lambda x: (x[0], x[1] or '')):
        request = {'instanceShape': report['shape']}
        if fd:
            request['faultDomain'] = fd
        if shape_config:
            request['instanceShapeConfig'] = shape_config
        row = {'availability_domain': ad, 'fault_domain': fd,
               'availability_status': 'REPORT_UNAVAILABLE', 'available_count': None}
        try:
            result = provider.oci(['compute', 'compute-capacity-report', 'create',
                '--compartment-id', compartment, '--availability-domain', ad,
                '--shape-availabilities', json.dumps([request]), '--no-retry'], check=False, timeout=35)
            if result.returncode:
                detail, _ = sanitize_output((result.stderr or result.stdout or 'No CLI error detail returned.'), max_bytes=1600)
                row['request_error'] = detail
                row['cli_exit_code'] = result.returncode
                raise ValueError('OCI capacity request failed for node-pool compartment ' + compartment + ': ' + detail)
            data = normalize(json.loads(result.stdout)['data'])
            if data.get('availability_domain') != ad:
                raise ValueError('OCI report returned a different availability domain.')
            rows = [r for r in data.get('shape_availabilities', []) if r.get('instance_shape') == report['shape'] and (fd is None or r.get('fault_domain') == fd)]
            if not rows or (fd is not None and len(rows) != 1):
                raise ValueError('OCI did not return a matching shape and fault-domain report.')
            # An unscoped AD request can return either an aggregate or FD rows.
            # Never combine overlapping aggregates and FD counts.
            if fd is None and any(r.get('fault_domain') is None for r in rows):
                rows = [r for r in rows if r.get('fault_domain') is None]
            if len({r.get('fault_domain') for r in rows}) != len(rows):
                raise ValueError('OCI returned duplicate capacity scopes.')
            for value in rows:
                if shape_config:
                    returned = value.get('instance_shape_config') or {}
                    if any(returned.get(k) != config[k] for k in ('ocpus', 'memory_in_gbs') if k in config and config[k] is not None):
                        raise ValueError('OCI returned a different shape configuration.')
            for value in rows:
                count = value.get('available_count')
                report['rows'].append({**row, 'fault_domain': value.get('fault_domain'),
                    'availability_status': value.get('availability_status') or 'UNKNOWN',
                    'available_count': count if type(count) is int and count >= 0 else None})
            continue
        except Exception as exc:
            row['error'] = sanitize_output(str(exc), max_bytes=2000)[0]
        report['rows'].append(row)
    statuses = [r['availability_status'] for r in report['rows']]
    report['status'] = ('available' if all(s == 'AVAILABLE' for s in statuses) else
                        'blocked' if any(s in {'OUT_OF_HOST_CAPACITY', 'HARDWARE_NOT_SUPPORTED'} for s in statuses) else 'unavailable')
    # Only compare a count to the requested increment when it covers a single scope.
    if len(report['rows']) == 1:
        count = report['rows'][0]['available_count']
        if count is not None and count < report['additional_workers']:
            report['status'] = 'blocked'
            report['error'] = 'Reported capacity is below the requested number of additional workers.'
    return report


def describe(report):
    rows = [f"{r['availability_domain']} / {r['fault_domain'] or 'AD scope'}: {r['availability_status']}; count {r['available_count'] if r['available_count'] is not None else 'Not disclosed'}" + ('. ' + r['error'] if r.get('error') else '') for r in report['rows']]
    return ' | '.join(rows + ([report['error']] if report.get('error') else [])) or 'No scale-up capacity report required.'
