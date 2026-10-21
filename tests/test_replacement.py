from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import subprocess
import pytest
from app.config import Settings
from app.provider_base import ProviderError
from app.replacement import Replacement, OKEGateway
from app.recovery import Recovery
from app.jobs import validate_request

class FakeGateway:
    def __init__(self):
        self.pool_data={'id':'pool','cluster_id':'cluster','lifecycle_state':'ACTIVE','node_config_details':{'size':2},'node_source_details':{'image_id':'image'},'nodes':[{'id':'old','lifecycle_state':'ACTIVE'},{'id':'peer','lifecycle_state':'ACTIVE'}]}
        self.calls=[];self.error=None;self.status='SUCCEEDED'
    def pool(self,_):return deepcopy(self.pool_data),'etag'
    def replace(self,*args):
        self.calls.append(args)
        if self.error:raise self.error
        return 'work'
    def work(self,_):return {'status':self.status}

@pytest.fixture
def service(tmp_path,monkeypatch):
    stack={'variables':{'hpc_custom_image_ocid':'image','node_count':'2'}}
    cap=SimpleNamespace(root=tmp_path,pending=lambda:None,stack_id=lambda:'stack',verify_stack=lambda _: (deepcopy(stack),'tag'),verify_image_pin=lambda *_:None,no_active=lambda _:None)
    n={'metadata':{'name':'n1','uid':'uid1','resourceVersion':'1','labels':{'oci-rdma':'true','oke-rdma-mode':'dranet'}},'instance':'old','spec':{},'status':{'nodeInfo':{'bootID':'boot'},'conditions':[{'type':'Ready','status':'True'}]}}
    class Provider:
        s=replace(Settings(),cluster_id='cluster',node_pool_id='pool')
        nodes=[n];validations=0;host_pass=True;deny=True
        def capacity_service(self):return cap
        def _node_instance_id(self,n):return n['instance']
        def _rdma_nodes(self):return deepcopy(self.nodes)
        def _json(self,args):
            if args[:2]==['get','node']:return deepcopy(next(n for n in self.nodes if n['metadata']['name']==args[2]))
            return {'items':[]}
        def kubectl(self,args,**kwargs):return subprocess.CompletedProcess(args,1 if self.deny else 0,'','OKE_RDMA_RECOVERY_GUARD' if self.deny else '')
        def report_progress(self,*_):pass
        def check_hosts(self,names):return {'status':'passed' if self.host_pass else 'blocked','hosts':[]}
        def inventory(self):return {'driver_ready':True,'nodes':[{'name':n['metadata']['name'],'mode':'dranet','ready':True,'cordoned':False,'devices':[{'valid':True,'allocated':False}]} for n in self.nodes]}
        def rdma_validation(self,*args):self.validations+=1;return {'status':'passed'}
    p=Provider();g=FakeGateway();s=Replacement(p,g)
    monkeypatch.setattr(Recovery,'authorize',lambda _:None)
    monkeypatch.setattr(Recovery,'idle',lambda *_:None)
    return s,p,g,stack

def launch(s):
    preview=s.preview('n1');s.start(preview['id']);return s.operation()

def replace_topology(p,g):
    g.pool_data['nodes'][0]['id']='new'
    n=deepcopy(p.nodes[0]);n['metadata'].update(name='new-node',uid='uid-new');n['instance']='new'
    peer=deepcopy(n);peer['metadata'].update(name='peer-node',uid='uid-peer');peer['instance']='peer'
    p.nodes=[n,peer]

def test_exact_target_and_never_resubmit_on_resume(service):
    s,p,g,_=service;op=launch(s)
    assert g.calls==[('pool','old','etag','rdma-replacement-'+op['id'])]
    s.resume(op['id']);s.resume(op['id']);assert len(g.calls)==1
    assert s.operation()['phase']!='COMPLETE'

def test_unknown_submission_retains_lock_and_cannot_close(service):
    s,p,g,_=service;g.error=TimeoutError('lost response');preview=s.preview('n1')
    with pytest.raises(ProviderError,match='second deletion'):s.start(preview['id'])
    op=s.operation();assert op['submission']=='UNKNOWN'
    with pytest.raises(ProviderError,match='pending'):s.guard('capacity_plan')
    s.resume(op['id']);assert len(g.calls)==1
    with pytest.raises(ProviderError,match='unknown'):s.close(op['id'])

def test_denial_must_be_proven_before_submission(service):
    s,p,g,_=service;p.deny=False;preview=s.preview('n1')
    with pytest.raises(ProviderError,match='Admission denial'):s.start(preview['id'])
    assert not g.calls and s.operation()['submission']=='NOT_SENT'
    assert s.close(preview['id'])['replacement']['phase']=='CLOSED'

def test_changed_worker_or_image_invalidates_preview(service):
    s,p,g,_=service;preview=s.preview('n1');p.nodes[0]['metadata']['uid']='changed'
    with pytest.raises(ProviderError,match='changed'):s.start(preview['id'])
    assert not g.calls
    g.pool_data['node_source_details']['image_id']='new-image'
    with pytest.raises(ProviderError,match='Pin the worker image'):s.preview('n1')

def test_no_ip_fallback_for_destructive_target(service):
    s,p,g,_=service;p.nodes[0]['instance']='unknown'
    with pytest.raises(ProviderError,match='IP-only'):s.preview('n1')
    assert not g.calls

def test_host_failure_is_not_completed_and_does_not_loop(service):
    s,p,g,_=service;op=launch(s);replace_topology(p,g);p.host_pass=False
    result=s.resume(op['id']);assert result['replacement']['phase']=='RDMA_FAILED'
    assert p.validations==0 and len(g.calls)==1
    s.resume(op['id']);assert len(g.calls)==1
    s.close(op['id']);assert s.operation()['phase']=='CLOSED'

def test_completion_requires_new_identity_host_device_and_cross_node_pass(service):
    s,p,g,_=service;op=launch(s);replace_topology(p,g)
    result=s.resume(op['id']);assert result['replacement']['phase']=='COMPLETE'
    assert result['status']=='passed' and p.validations==1
    s.resume(op['id']);assert p.validations==1 and len(g.calls)==1

def test_no_peer_is_explicitly_pending(service):
    s,p,g,_=service;op=launch(s);replace_topology(p,g);p.nodes=p.nodes[:1]
    result=s.resume(op['id']);assert result['replacement']['phase']=='FUNCTIONAL_TEST_PENDING'
    assert p.validations==0

def test_claim_guard_blocks_preview_and_start(service,monkeypatch):
    s,p,g,_=service
    monkeypatch.setattr(Recovery,'idle',lambda *_: (_ for _ in ()).throw(ProviderError('claims exist')))
    preview=s.preview('n1');assert not preview['eligible'] and preview['blockers']==['claims exist']
    with pytest.raises(ProviderError):s.start(preview['id'])
    assert not g.calls

def test_failed_work_cannot_be_marked_success(service):
    s,p,g,_=service;op=launch(s);g.status='FAILED'
    assert s.resume(op['id'])['replacement']['phase']=='OCI_FAILED'
    assert s.close(op['id'])['replacement']['phase']=='CLOSED'

def test_oke_submission_uses_safe_options_and_no_retry():
    import oci
    g=object.__new__(OKEGateway);g.oci=oci;seen={}
    def delete(*args,**kwargs):seen.update(kwargs);seen['args']=args;return SimpleNamespace(headers={'opc-work-request-id':'work'})
    g.client=SimpleNamespace(delete_node=delete)
    assert g.replace('pool','instance','etag','request')=='work'
    assert seen['args']==('pool','instance') and seen['is_decrement_size'] is False
    assert seen['is_force_deletion_after_override_grace_duration'] is False
    assert seen['override_eviction_grace_duration']=='PT60M' and seen['if_match']=='etag'
    assert isinstance(seen['retry_strategy'],oci.retry.NoneRetryStrategy)

def test_confirmation_is_explicit():
    s=Settings(environment_name='demo')
    with pytest.raises(ValueError):validate_request(s,'replacement_run','demo')
    validate_request(s,'replacement_run','REPLACE demo')


def test_consumed_preview_cannot_be_reused(service):
    s,p,g,_=service;g.error=type('Rejected',(Exception,),{'status':403})('denied')
    preview=s.preview('n1')
    with pytest.raises(ProviderError):s.start(preview['id'])
    s.close(preview['id'])
    with pytest.raises(ProviderError,match='already used'):s.start(preview['id'])
    assert len(g.calls)==1


def test_configuration_drift_blocks_resume_without_deletion(service):
    s,p,g,stack=service;op=launch(s);stack['variables']['unrelated']='changed'
    with pytest.raises(ProviderError,match='changed during'):s.resume(op['id'])
    assert len(g.calls)==1
