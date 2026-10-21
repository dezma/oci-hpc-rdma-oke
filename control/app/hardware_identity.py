"""Observed hardware identity, never a substitute for RDMA verification."""
import json
import re
import time
from .capacity import atomic


def identity(node, facts, instance_id, pf):
    boot = node.get('status', {}).get('nodeInfo', {}).get('bootID')
    if not boot or facts.get('boot_id') != boot or not instance_id:
        return {}
    serial = str(facts.get('system_serial', '')).strip()[:128]
    if serial.lower() in {'unknown', 'none', 'not specified', 'default string', 'to be filled by o.e.m.', '0'}:
        serial = ''
    nic = next((i for i in facts.get('interfaces', []) if i.get('name') == pf), {})
    mac = str(nic.get('mac', '')).lower()
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac) or mac in {'00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff'}:
        mac = ''
    return {'instance_id': instance_id, 'node_uid': node['metadata'].get('uid'),
            'node_name': node['metadata']['name'], 'boot_id': boot,
            'system_serial': serial, 'pf_name': pf, 'pf_mac': mac}


def compare(before, after):
    if not before or not after or before.get('instance_id') == after.get('instance_id'):
        return {'status': 'unknown', 'message': 'Hardware reuse is not established.'}
    if before.get('system_serial') and before['system_serial'] == after.get('system_serial'):
        return {'status': 'serial_match', 'message': 'Same server serial observed on different OCI instances. Replacement reused the recorded server identity; verify RDMA independently.'}
    if before.get('pf_mac') and before['pf_mac'] == after.get('pf_mac'):
        return {'status': 'mac_match', 'message': 'Same selected PF MAC observed on different OCI instances. Possible hardware reuse; MAC alone does not prove server identity.'}
    if not any(before.get(k) and after.get(k) for k in ('system_serial', 'pf_mac')):
        return {'status': 'unknown', 'message': 'Comparable hardware identifiers were not collected on both instances.'}
    return {'status': 'no_match', 'message': 'No matching hardware identifier observed. This does not establish RDMA health.'}


def record(path, current):
    if not current:
        return {}
    rows = json.loads(path.read_text()) if path.exists() else []
    matches = []
    for row in rows:
        evidence = compare(row, current)
        if evidence['status'] in {'serial_match', 'mac_match'}:
            matches.append({'previous': row, **evidence})
    item = {**current, 'observed_at': time.time()}
    rows = [r for r in rows if (r.get('instance_id'), r.get('boot_id')) != (current['instance_id'], current['boot_id'])]
    atomic(path, (rows + [item])[-1000:])
    return {**item, 'reuse_matches': matches[-10:]}
