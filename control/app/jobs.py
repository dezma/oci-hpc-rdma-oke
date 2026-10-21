from __future__ import annotations
from .config import Settings
from .provider import get_provider

MUTATING = {'monitoring_sweep','monitoring_baseline','replacement_preview','replacement_run','replacement_resume','replacement_close','recovery_restart_assess', 'recovery_assess', 'recovery_run', 'recovery_finish', 'check_hosts', 'install_dranet', 'rdma_validation', 'cleanup_validation', 'node_diagnostic', 'reset_rdma', 'capacity_plan', 'capacity_apply', 'capacity_discard', 'capacity_resume', 'capacity_close'}


def validate_request(settings: Settings, action: str, confirmation: str):
    prefix = {'replacement_run':'REPLACE ', 'replacement_close':'CLOSE ','recovery_run':'RECOVER ', 'recovery_finish':'FINISH ', 'reset_rdma':'RESET ', 'capacity_apply':'SCALE ', 'capacity_discard':'DISCARD ', 'capacity_close':'CLOSE '}.get(action, '')
    expected = prefix + settings.environment_name
    if action in MUTATING and confirmation != expected:
        raise ValueError(f"Type the exact environment name '{expected}' to approve this operation")


def execute(settings, action, payload=None, job_id=''):
    provider = get_provider(settings); payload = payload or {}
    if job_id:
        from .db import progress
        provider.report_progress = lambda stage, completed, total, detail='': progress(job_id, stage, completed, total, detail)
    from .recovery import Recovery, guard
    guard(action)
    provider.capacity_service().guard(action)
    if action.startswith('monitoring_'):
        from .monitoring import Monitoring
        monitor = Monitoring(provider)
        if action == 'monitoring_baseline': return monitor.approve_baseline(payload.get('node_name',''))
        if payload.get('scheduled') and not monitor.config()['enabled']: return {'status':'skipped','message':'Monitoring disabled after scheduling.'}
        return monitor.benchmark() if action == 'monitoring_benchmark' else monitor.sweep()
    if action.startswith('replacement_'):
        from .replacement import Replacement
        replacement = Replacement(provider)
        if action == 'replacement_preview': return replacement.preview(payload.get('node_name',''))
        if action == 'replacement_run': return replacement.start(payload.get('replacement_id',''))
        return getattr(replacement, action.removeprefix('replacement_'))(payload.get('replacement_id',''))
    if action == 'recovery_restart_assess': return Recovery(provider).assess(payload.get('node_name', ''), job_id, manual_restart=True)
    if action == 'recovery_assess': return Recovery(provider).assess(payload.get('node_name', ''), job_id)
    if action == 'recovery_run': return Recovery(provider).run(payload.get('recovery_preview_id', ''), job_id)
    if action == 'recovery_finish': return Recovery(provider).finish(payload.get('recovery_attempt_id', ''))
    if action.startswith('capacity_'):
        capacity = provider.capacity_service()
        if action == 'capacity_plan': return capacity.plan(payload.get('target_node_count'))
        return getattr(capacity, action.removeprefix('capacity_'))(payload.get('capacity_operation_id', ''))
    if action == 'reset_rdma':
        return provider.reset_rdma(payload.get('reset_plan_id', ''))
    if action == 'node_diagnostic':
        return provider.node_diagnostic(payload['node_name'], payload['profile'], job_id)
    if action == 'rdma_validation':
        return provider.rdma_validation(payload.get('server_node', ''), payload.get('client_node', ''), payload.get('performance_profile', 'quick'))
    if action in {'preflight', 'check_hosts', 'install_dranet', 'cleanup_validation'}:
        return getattr(provider, action)()
    raise ValueError('Unsupported operation')
