#!/usr/bin/env python3
"""Close a verified successful scale-out with unresolved RDMA health.

Run with the installed operator venv. Default is read-only preview.
No OCI mutation, Terraform apply, service restart or host change is performed.
"""
import argparse
import copy
import json
import os
import pwd
from pathlib import Path
import sys
import time


def verify(capacity, operation_id):
    from app.capacity import stack_fingerprint
    from app.provider import ready
    op = capacity.selected_operation(operation_id)
    if op['phase'] != 'VERIFYING_NODES' or not op.get('apply_job_id') or op['target'] <= op['before']:
        raise ValueError('Only a recorded scale-out in VERIFYING_NODES can be reconciled by this helper.')
    if op['stack_id'] != capacity.stack_id():
        raise ValueError('Connected stack differs from the operation.')
    jobs = capacity.no_active(op['stack_id'])
    if not jobs or jobs[0].get('id') != op['apply_job_id']:
        raise ValueError('The recorded apply is not the latest stack job; review external changes.')
    job = capacity.gateway.job(op['apply_job_id'])
    if (job.get('id') != op['apply_job_id'] or job.get('stack_id') != op['stack_id'] or
        job.get('operation') != 'APPLY' or job.get('lifecycle_state') != 'SUCCEEDED'):
        raise ValueError('The recorded OCI apply is not verified SUCCEEDED for this stack.')
    stack, _ = capacity.verify_stack(op['stack_id'])
    if stack_fingerprint(stack) != op['planned_fingerprint']:
        raise ValueError('Stack inputs changed since the reviewed plan.')
    if str((stack.get('variables') or {}).get('node_count')) != str(op['target']):
        raise ValueError('Stack node_count differs from the recorded target.')
    pool = capacity.pool()
    active = [n for n in pool.get('nodes', []) if n.get('lifecycle_state') != 'DELETED']
    if (pool.get('lifecycle_state') != 'ACTIVE' or pool['node_config_details']['size'] != op['target'] or
        len(active) != op['target'] or any(n.get('lifecycle_state') != 'ACTIVE' for n in active)):
        raise ValueError('The pool and active instance count do not match the recorded target.')
    nodes = capacity.members(pool)
    if len(nodes) != op['target'] or any(not ready(n) for n in nodes):
        raise ValueError('All target workers must be registered and Kubernetes Ready.')
    return op, sorted(n['metadata']['name'] for n in nodes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--confirm', default='', help='RECONCILE followed by the exact environment name; omit for preview')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo using /opt/oke-rdma-control/venv/bin/python.')
    root = Path('/opt/oke-rdma-control')
    sys.path.insert(0, str(root))
    os.environ.update(HPC_CONTROL_CONFIG='/etc/oke-rdma-control/config.json',
                      HPC_CONTROL_DB='/var/lib/oke-rdma-control/control.db',
                      HPC_CONTROL_STATE_DIR='/var/lib/oke-rdma-control',
                      HPC_CONTROL_RDMA_CONFIG='/var/lib/oke-rdma-control/rdma-config.json',
                      KUBECONFIG='/etc/oke-rdma-control/kube/config',
                      KUBECTL_KUBERC='false', OCI_CLI_AUTH='instance_principal',
                      PATH=str(root/'venv/bin')+':/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin')
    # Match both systemd services before accessing SQLite or replacing journals.
    account = pwd.getpwnam('opc')
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    from app.config import load_settings
    from app.provider import get_provider
    from app.capacity import atomic
    from app.db import idle_guard, DB_PATH
    settings = load_settings()
    if settings.mode != 'live' or not DB_PATH.is_file():
        raise SystemExit('An existing live operator installation is required.')
    if args.confirm and args.confirm != 'RECONCILE ' + settings.environment_name:
        raise SystemExit('Confirmation does not match the environment.')
    c = get_provider(settings).capacity_service()
    # Keep the existing job queue idle throughout verification and journal write.
    with idle_guard():
        recovery = c.root / 'recovery'
        if any(json.loads(p.read_text()).get('phase') != 'COMPLETE' for p in recovery.glob('attempt-*.json')):
            raise ValueError('Finish the recorded host recovery first.')
        op, nodes = verify(c, args.operation_id)
        summary = {'operation_id': op['operation_id'], 'apply_job_id': op['apply_job_id'],
                   'apply_state': 'SUCCEEDED', 'target': op['target'], 'ready_workers': nodes,
                   'rdma_readiness': 'failed', 'infrastructure_changes': False}
        if args.confirm:
            original = copy.deepcopy(op)
            backup = c.root / ('capacity-before-reconcile-' + op['operation_id'] + '.json')
            if backup.exists():
                raise ValueError('A reconciliation backup already exists; inspect it before retrying.')
            atomic(backup, original)
            op.update(phase='COMPLETE', readiness='failed', actual_size=op['target'],
                      reconciliation={'at': time.time(), 'reason': 'Successful scale-out acknowledged with unresolved RDMA readiness',
                                      'previous_phase': original['phase'], 'apply_job_id': op['apply_job_id'],
                                      'workers': nodes, 'backup': str(backup)})
            c.save(op)
            summary['result'] = 'Capacity operation closed. RDMA remains failed; infrastructure and stack target are unchanged.'
        else:
            summary['result'] = 'Preview only; no files or resources changed by reconciliation.'
            summary['confirmation'] = 'RECONCILE ' + settings.environment_name
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('Reconciliation refused: ' + str(exc))
