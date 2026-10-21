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
