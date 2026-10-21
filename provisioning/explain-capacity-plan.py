#!/usr/bin/env python3
"""Read the recorded OCI plan and explain the capacity gate without changing state."""
from pathlib import Path
import argparse
import json
import sys
root=Path(__file__).resolve().parents[1]
# Source checkout or extracted operator update (app/ alongside this script).
sys.path.insert(0,str(root/'control' if (root/'control/app').is_dir() else Path(__file__).resolve().parent))
from app.capacity import validate_plan
from app.provider_base import ProviderError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorded',action='store_true',required=True,help='Read the retained plan using the operator instance principal')
    parser.parse_args()
    from app.config import load_settings
    from app.provider import get_provider
    settings=load_settings()
    if settings.mode!='live':raise ProviderError('Run this on the provisioned operator VM.')
    service=get_provider(settings).capacity_service();op=service.operation()
    if not op or not op.get('plan_job_id'):raise ProviderError('No recorded capacity plan is available.')
    if op['stack_id']!=service.stack_id():raise ProviderError('Recorded stack differs from the current connection.')
    job=service.gateway.job(op['plan_job_id'])
    if job.get('stack_id')!=op['stack_id'] or job.get('operation')!='PLAN':raise ProviderError('Recorded OCI job identity does not match.')
    plan=service.gateway.document('get_job_tf_plan',op['plan_job_id'])
    print('Recorded target:',op['before'],'->',op['target'])
    print('Plan job:',op['plan_job_id'])
    try:
        review=validate_plan(plan,settings.node_pool_id,op['before'],op['target'])
        print('Size-only gate passed. This command does not authorize or submit an Apply.')
    except ProviderError as exc:print('Blocked:',str(exc))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ProviderError,ValueError) as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
