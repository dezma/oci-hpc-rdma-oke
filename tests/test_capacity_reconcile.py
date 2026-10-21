import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.capacity import stack_fingerprint
spec=importlib.util.spec_from_file_location('reconcile',Path(__file__).parents[1]/'provisioning/reconcile-capacity.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    stack={'variables':{'node_count':'3'}}
    op={'phase':'VERIFYING_NODES','apply_job_id':'job','stack_id':'stack','target':3,'before':2,'planned_fingerprint':stack_fingerprint(stack)}
    job={'id':'job','stack_id':'stack','operation':'APPLY','lifecycle_state':'SUCCEEDED'}
    pool={'lifecycle_state':'ACTIVE','node_config_details':{'size':3},'nodes':[{'lifecycle_state':'ACTIVE'} for _ in range(3)]}
    nodes=[{'metadata':{'name':str(i)},'status':{'conditions':[{'type':'Ready','status':'True'}]}} for i in range(3)]
    c=SimpleNamespace(selected_operation=lambda _:op,stack_id=lambda:'stack',no_active=lambda _: [{'id':'job'}],gateway=SimpleNamespace(job=lambda _:job),verify_stack=lambda _:(stack,''),pool=lambda:pool,members=lambda _:nodes)
    return c,op,job,pool,nodes,stack

def test_verified_scaleout():
    c,*_=fixture()
    assert m.verify(c,'id')[1]==['0','1','2']

@pytest.mark.parametrize('failure',['running_apply','other_stack','not_latest','pool_count','not_ready','changed_inputs','wrong_phase','scale_in'])
def test_refuses_unverified_closure(failure):
    c,op,job,pool,nodes,stack=fixture()
    if failure=='running_apply':job['lifecycle_state']='IN_PROGRESS'
    if failure=='other_stack':job['stack_id']='other'
    if failure=='not_latest':c.no_active=lambda _:[{'id':'other'}]
    if failure=='pool_count':pool['nodes'].pop()
    if failure=='not_ready':nodes[0]['status']['conditions'][0]['status']='False'
    if failure=='changed_inputs':stack['variables']['node_count']='4'
    if failure=='wrong_phase':op['phase']='SUBMITTING_APPLY'
    if failure=='scale_in':op['before']=4
    with pytest.raises(ValueError):m.verify(c,'id')

# Live service regression: a desired size of six with only five surviving workers.
from copy import deepcopy
import json
import subprocess
from app.capacity import Capacity, ProviderError
from test_capacity import FakeProvider, FakeGateway, SID, plan_document

class PartialProvider(FakeProvider):
    def __init__(self):
        super().__init__(); self.size=5; self.desired=6; self.pool_state='NEEDS_ATTENTION'
    def oci(self,args,**kwargs):
        r=super().oci(args,**kwargs)
        if args[:3]==['ce','node-pool','get']:
            data=json.loads(r.stdout);data['data']['node_config_details']['size']=self.desired
            data['data']['lifecycle_state']=self.pool_state
            if self.desired==6:data['data']['nodes'].append({'id':'failed-slot','lifecycle_state':'FAILED'})
            r.stdout=json.dumps(data)
        return r

class PartialGateway(FakeGateway):
    def __init__(self,p):
        super().__init__(p);self.stack_data['variables']['node_count']='6'
        self.records=[{'id':'failed-apply','stack_id':SID,'operation':'APPLY','lifecycle_state':'FAILED','time_created':'0000000000'}]
    def create(self,sid,kind,operation_id,plan_id=''):
        result=super().create(sid,kind,operation_id,plan_id)
        if kind=='PLAN':self.documents[result['id']]=plan_document(self.p.desired,int(self.stack_data['variables']['node_count']))
        if kind=='APPLY':self.p.desired=self.p.size;self.p.pool_state='ACTIVE'
        return result

@pytest.fixture
def partial(tmp_path):
    p=PartialProvider();return Capacity(p,tmp_path,PartialGateway(p))

@pytest.mark.parametrize('target',[5,3,0])
def test_partial_failure_reconciles_through_reviewed_plan(partial,target):
    op=partial.plan(target)['capacity']
    assert op['reconciliation'] and op['before']==6
    assert op['before_observed']['provisioned']==5
    assert partial.p.size==5 and not any(x[0]=='APPLY' for x in partial.gateway.writes)
    result=partial.apply(op['operation_id'])
    assert result['status']=='passed' and partial.p.size==target
    assert partial.gateway.writes[-1]==('APPLY',op['plan_job_id'])

@pytest.mark.parametrize('reason',['claims','pods','force','active_work','unknown_work','not_ready','no_failed_apply','growth'])
def test_partial_reconciliation_retains_guards(partial,reason):
    target=5
    if reason=='claims':partial.p.claims=[{'name':'used'}]
    if reason=='pods':partial.p.pods=[{'metadata':{'name':'app','namespace':'app'},'spec':{'nodeName':'worker-1'},'status':{'phase':'Running'}}]
    if reason=='force':partial.p.force=True
    if reason=='active_work':partial.p.work_requests=[{'status':'IN_PROGRESS'}]
    if reason=='unknown_work':partial.p.work_requests=[{'status':'UNKNOWN'}]
    if reason=='not_ready':
        original=partial.p._rdma_nodes
        def not_ready():
            nodes=original();nodes[0]['status']['conditions'][0]['status']='False';return nodes
        partial.p._rdma_nodes=not_ready
    if reason=='no_failed_apply':partial.gateway.records[0]['lifecycle_state']='SUCCEEDED'
    if reason=='growth':target=7
    with pytest.raises(ProviderError):partial.plan(target)
    assert not partial.gateway.writes

def test_partial_apply_rechecks_inventory_and_work_requests(partial):
    op=partial.plan(5)['capacity'];partial.p.work_requests=[{'status':'ACCEPTED'}]
    with pytest.raises(ProviderError,match='work request'):partial.apply(op['operation_id'])
    partial.p.work_requests=[];partial.p.size=4
    with pytest.raises(ProviderError):partial.apply(op['operation_id'])
    assert not any(w[0]=='APPLY' for w in partial.gateway.writes)

def test_acknowledgement_records_counts_not_success(partial):
    partial.save({'operation_id':'a'*64,'phase':'APPLY_FAILED','before':3,'target':6,'stack_id':SID,'cluster_id':'cluster-id','node_pool_id':'pool-id','apply_job_id':'failed-apply'})
    result=partial.close('a'*64)
    assert result['capacity']['phase']=='CLOSED_FAILED'
    assert result['capacity']['outcome']=='failed'
    assert result['capacity']['observed_capacity']['provisioned']==5
    assert result['capacity']['observed_capacity']['requested']==6
    assert 'not rolled back' in result['message'] and partial.pending() is None
    assert not partial.gateway.writes

def test_legacy_acknowledgement_is_not_success(partial):
    partial.save({'phase':'COMPLETE','readiness':'not_verified','actual_size':6,'apply_job_id':'failed-apply'})
    assert partial.operation()['phase']=='CLOSED_FAILED'
    assert 'actual_size' not in partial.operation()

def test_failed_wait_captures_partial_capacity(partial):
    op={'phase':'APPLY_RUNNING','apply_job_id':'failed-apply','stack_id':SID}
    with pytest.raises(ProviderError,match='FAILED'):partial.wait_job(op,'APPLY',1)
    assert op['observed_capacity']['provisioned']==5 and op['outcome']=='failed'
