"""Allowlisted, bounded support summaries; no raw logs, credentials or state files."""
import io
import json
import time
import zipfile
from .measurements import summarize, positive


def build_bundle(environment, jobs, settings):
    jobs = jobs[:100]
    inventory = []
    for n in environment.get('nodes', [])[:256]:
        inventory.append({k: n.get(k) for k in ('name', 'uid', 'boot_id', 'mode', 'ready', 'cordoned', 'shape', 'zone', 'kernel', 'host_check_at', 'dranet_ready')})
        inventory[-1].update(host_issue_count=len(n.get('host_issues', [])), driver_issue_count=len(n.get('issues', [])),
                             devices=[{k: d.get(k) for k in ('name', 'valid', 'allocated')} for d in n.get('devices', [])[:16]])
    operations = []
    diagnostics = []
    for job in jobs:
        item = {k: job.get(k) for k in ('id', 'action', 'state', 'requested_at', 'started_at', 'finished_at')}
        result = job.get('result') or {}
        item['has_error'] = bool(job.get('error') or result.get('error'))
        if positive(result.get('average_gbps')): item['average_gbps'] = result['average_gbps']
        latency = result.get('latency') or {}
        if latency.get('status') == 'passed' and positive(latency.get('average_us')): item['average_latency_us'] = latency['average_us']
        if result.get('replacement'):
            item['replacement']={k:result['replacement'].get(k) for k in ('id','phase','node_name','instance_id','new_node_name','new_instance_id','work_request_id','work_status','submission','size')}
        operations.append(item)
        if job.get('action') in {'node_diagnostic', 'check_hosts'}:
            diagnostics.append({**item, 'profile': (job.get('payload') or {}).get('profile'), 'node': (job.get('payload') or {}).get('node_name'),
                                'hosts_reported': len(result.get('hosts', []))})
    files = {
        'environment.json': {k: environment.get(k) for k in ('environment_name','region','cluster_name','mode','dranet_version','observed_at','reset_at','driver_ready')},
        'configuration.json': {'rdma_pf_name': settings.rdma_pf_name, 'rdma_child_cidr': settings.rdma_child_cidr,
                               'rdma_mtu': settings.rdma_mtu, 'rdma_tos': settings.rdma_tos, 'tools_image_configured': bool(settings.rdma_tools_image),
                               'pull_secret_configured': bool(settings.rdma_image_pull_secret)},
        'replacement.json': {k:(environment.get('replacement') or {}).get(k) for k in ('id','phase','node_name','instance_id','new_node_name','new_instance_id','work_request_id','work_status','submission','size','created_at','updated_at')},
        'workers.json': inventory, 'operations.json': operations, 'diagnostics.json': diagnostics,
        'measurement.json': summarize(jobs, environment, settings),
        'manifest.json': {'schema_version': 1, 'exported_at': time.time(), 'operation_limit': 100,
                          'excluded': ['credentials','tokens','secret contents','Terraform state and plans','raw logs','job payloads','free-form error text']}}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('README.txt', 'OKE HPC support summary\nIncludes infrastructure identifiers and private worker names. Review before sharing.\nDiagnostic summaries only; download individual diagnostic reports separately when needed.\n')
        for name, value in files.items():
            raw = json.dumps(value, indent=2, allow_nan=False)
            if len(raw.encode()) > 2_000_000: raise ValueError('Support summary exceeds its export size limit.')
            archive.writestr(name, raw)
    return stream.getvalue()
