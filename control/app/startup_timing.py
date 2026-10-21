"""API timestamps and observed DRA allocation; never infer a DRA prepare duration."""
from datetime import datetime

def epoch(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError):return None

def summarize(pod, claim, allocation_at=None):
    conditions={c['type']:epoch(c.get('lastTransitionTime')) for c in pod.get('status',{}).get('conditions',[]) if c.get('status')=='True'}
    started=[epoch(c.get('state',{}).get('running',{}).get('startedAt')) for c in pod.get('status',{}).get('containerStatuses',[])]
    stages={'claim_created':epoch(claim.get('metadata',{}).get('creationTimestamp')),'allocation_first_observed':allocation_at,'pod_created':epoch(pod.get('metadata',{}).get('creationTimestamp')),'pod_scheduled':conditions.get('PodScheduled'),'sandbox_ready':conditions.get('PodReadyToStartContainers'),'container_started':min((v for v in started if v is not None),default=None),'pod_ready':conditions.get('Ready')}
    def delta(a,b):
        x,y=stages[a],stages[b]
        return y-x if x is not None and y is not None and y>=x else None
    return {'pod':pod.get('metadata',{}).get('name'),'stages':stages,'seconds':{'claim_to_allocation_observed':delta('claim_created','allocation_first_observed'),'scheduling':delta('pod_created','pod_scheduled'),'scheduled_to_sandbox_ready':delta('pod_scheduled','sandbox_ready'),'sandbox_to_container_start':delta('sandbox_ready','container_started'),'claim_to_container_start':delta('claim_created','container_started'),'claim_to_pod_ready':delta('claim_created','pod_ready')},'note':'Allocation is first observed, not an exact API transition. Sandbox timing includes networking and other preparation; DRA prepare time is not independently exposed. Container start is the validation container, not application-level readiness. Missing or inconsistent timestamps remain unknown.'}
