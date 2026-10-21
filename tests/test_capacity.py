from copy import deepcopy
from dataclasses import replace
import json
import subprocess
import pytest
from app.capacity import Capacity, ProviderError, validate_plan, POOL_ADDRESS
from app.config import Settings
from app.provider import LiveProvider
from test_control import client, AUTH

SID='ocid1.ormstack.oc1.eu-frankfurt-1.example'


def plan_document(before=3,target=5):
    a={'id':'pool-id','node_shape':'BM.Optimized3.36','node_config_details':[{'size':before,'compute_cluster_id':'compute-id'}],
       'node_pool_cycling_details':[{'is_node_cycling_enabled':False}], 'nodes':[{'id':'old-node'}]}
    b=deepcopy(a);b['node_config_details'][0]['size']=target;b['nodes']=None
    return {'format_version':'1.2','resource_changes':[{'address':POOL_ADDRESS,'mode':'managed','change':{'actions':['update'],'before':a,'after':b,'after_unknown':{'nodes':True}}}]}


class FakeProvider(LiveProvider):
    def __init__(self):
        super().__init__(replace(Settings(),mode='live',tenancy_id='tenancy',capacity_stack_ocid=SID,compartment_id='comp',cluster_id='cluster-id',node_pool_id='pool-id',rdma_tools_image='image@sha256:'+'a'*64))
        self.size=3;self.claims=[];self.templates=[];self.pods=[];self.host_calls=[];self.host_pass=True;self.force=False;self.cycling=False;self.grace='PT60M'
        self.progress=[];self.report_progress=lambda stage,done,total,detail='':self.progress.append((stage,done,total))
    def _rdma_nodes(self):
        return [{'metadata':{'name':f'worker-{n}','uid':f'u{n}','labels':{'oci-rdma':'true','oke-rdma-mode':'dranet'}},
                 'status':{'addresses':[{'type':'InternalIP','address':f'10.0.1.{n}'}],'nodeInfo':{'bootID':'boot1'},'conditions':[{'type':'Ready','status':'True'}]}} for n in range(1,self.size+1)]
    def oci(self,args,**kwargs):
        if args[:3]==['compute','compute-capacity-report','create']:
            return subprocess.CompletedProcess(args,0,json.dumps({'data':{'availability-domain':'AD1','shape-availabilities':[{'instance-shape':'BM.Optimized3.36','availability-status':'AVAILABLE','available-count':None}]}}),'')
        assert args[:3]==['ce','node-pool','get'], 'Direct OKE writes are forbidden'
        pool={'id':'pool-id','cluster_id':'cluster-id','lifecycle_state':'ACTIVE','node_shape':'BM.Optimized3.36','node_config_details':{'size':self.size,'placement_configs':[{'availability_domain':'AD1'}]},
              'node_pool_cycling_details':{'is_node_cycling_enabled':self.cycling},
              'node_eviction_node_pool_settings':{'eviction_grace_duration':self.grace,'is_force_delete_after_grace_duration':self.force},
              'nodes':[{'id':f'instance-{n}','private_ip':f'10.0.1.{n}','lifecycle_state':'ACTIVE'} for n in range(1,self.size+1)]}
        return subprocess.CompletedProcess(args,0,json.dumps({'data':pool}),'')
    def inventory(self):
        return {'driver_ready':True,'claims':self.claims,'nodes':[{'name':f'worker-{n}','issues':[],
                'host_issues':[] if self.host_pass else ['host needs attention'],'devices':[{'valid':True}]} for n in range(1,self.size+1)]}
    def _json(self,args,**kwargs):
        if 'pods' in args:return {'items':self.pods}
        if 'resourceclaimtemplates' in args:return {'items':self.templates}
        if 'deviceclasses' in args:return {'items':[]}
        raise AssertionError(args)
    def check_hosts(self,node_names=None):
        self.host_calls.append(node_names)
        return {'status':'passed' if self.host_pass else 'blocked','hosts':[]}


class FakeGateway:
    def __init__(self,p):
        self.p=p;self.stack_data={'id':SID,'compartment_id':'comp','lifecycle_state':'ACTIVE',
           'variables':{'operator_image_ocid':'image-pinned','node_count':'3','keep':'do-not-remove'},'terraform_version':'1.5.x','config_source':{'config_source_type':'ZIP_UPLOAD','version':'unchanged'}}
        self.etag='1';self.records=[];self.documents={};self.writes=[];self.fail_create='';self.fail_job='';self.extra_change=False
    def stack(self,sid):assert sid==SID;return deepcopy(self.stack_data),self.etag
    def update(self,sid,variables,etag):
        assert sid==SID and etag==self.etag
        assert variables['keep']=='do-not-remove'
        self.writes.append(('variables',deepcopy(variables)))
        self.stack_data['variables']=deepcopy(variables);self.etag=str(int(self.etag)+1)
    def jobs(self,sid):return deepcopy(self.records)
    def job(self,jid):return deepcopy(next(j for j in self.records if j['id']==jid))
    def create(self,sid,kind,operation_id,plan_id=''):
        self.writes.append((kind,plan_id))
        if kind=='APPLY':assert plan_id in self.documents
        jid=f'job-{len(self.records)+1}'
        job={'id':jid,'stack_id':sid,'operation':kind,'lifecycle_state':('FAILED' if self.fail_job==kind else 'SUCCEEDED'),
             'time_created':f'{len(self.records)+1:010}','display_name':f'rdma-scale-{kind.lower()}-{operation_id}'}
        if kind=='PLAN':
            doc=plan_document(self.p.size,int(self.stack_data['variables']['node_count']))
            if self.extra_change:doc['resource_changes'].append({'address':'oci_core_instance.operator','mode':'managed','change':{'actions':['delete','create']}})
            self.documents[jid]=doc
        elif not self.fail_job:self.p.size=int(self.stack_data['variables']['node_count'])
        self.records.insert(0,job)
        if self.fail_create==kind:raise ProviderError('Simulated lost response after OCI accepted the job')
        return deepcopy(job)
    def document(self,name,identifier):
        if name=='get_stack_tf_state':return {'resources':[{'type':'oci_core_instance','name':'operator','instances':[{'attributes':{'source_details':[{'source_id':'image-pinned'}]}}]}, {'type':'oci_containerengine_node_pool','name':'rdma','instances':[{'attributes':{'id':'pool-id'}}]}, {'type':'oci_containerengine_cluster','name':'this','instances':[{'attributes':{'id':'cluster-id'}}]}]}
        assert name=='get_job_tf_plan';return deepcopy(self.documents[identifier])


@pytest.fixture
def cap(tmp_path):
    p=FakeProvider();g=FakeGateway(p);return Capacity(p,tmp_path,g)


def test_plan_review_apply_uses_exact_orm_plan_and_checks_only_new_nodes(cap):
    result=cap.plan(5);op=result['capacity']
    assert op['phase']=='AWAITING_REVIEW' and cap.p.size==3 and cap.gateway.stack_data['variables']['node_count']=='5'
    assert not any(w[0]=='APPLY' for w in cap.gateway.writes)
    result=cap.apply(op['operation_id'])
    assert result['status']=='passed' and cap.p.size==5
    assert cap.p.host_calls==[['worker-4','worker-5']]
    assert ('APPLY',op['plan_job_id']) in cap.gateway.writes
    assert cap.operation()['phase']=='COMPLETE'


def test_discard_restores_only_target_and_preserves_other_variables(cap):
    op=cap.plan(5)['capacity'];result=cap.discard(op['operation_id'])
    assert result['status']=='passed' and cap.p.size==3
    assert cap.gateway.stack_data['variables']=={'operator_image_ocid':'image-pinned','node_count':'3','keep':'do-not-remove'}
    assert cap.operation()['phase']=='DISCARDED'


@pytest.mark.parametrize('bad',['replacement','shape','wrong_pool','unknown','other_resource','cycling','different_count'])
def test_plan_gate_rejects_every_unrelated_change(bad):
    doc=plan_document();change=doc['resource_changes'][0]['change']
    if bad=='replacement':change['actions']=['delete','create']
    if bad=='shape':change['after']['node_shape']='another-shape'
    if bad=='wrong_pool':change['after']['id']='other-pool'
    if bad=='unknown':change['after_unknown']['node_shape']=True
    if bad=='other_resource':doc['resource_changes'].append({'address':'oci_core_vcn.this','change':{'actions':['update']}})
    if bad=='cycling':change['after']['node_pool_cycling_details'][0]['is_node_cycling_enabled']=True
    if bad=='different_count':change['after']['node_config_details'][0]['size']=6
    with pytest.raises(ProviderError):validate_plan(doc,'pool-id',3,5)


@pytest.mark.parametrize('reason',['claims','template','application','force','cycling','zero_grace'])
def test_scale_in_blockers_prevent_even_variable_updates(cap,reason):
    if reason=='claims':cap.p.claims=[{'name':'active'}]
    if reason=='template':cap.p.templates=[{'spec':{'spec':{'devices':{'requests':[{'exactly':{'deviceClassName':'dra.net'}}]}}}}]
    if reason=='application':cap.p.pods=[{'metadata':{'name':'mpi-rank','namespace':'app'},'spec':{'nodeName':'worker-1'},'status':{'phase':'Running'}}]
    if reason=='zero_grace':cap.p.grace='PT0H0M0S'
    if reason=='force':cap.p.force=True
    if reason=='cycling':cap.p.cycling=True
    with pytest.raises(ProviderError):cap.plan(2)
    assert cap.gateway.writes==[]


def test_scale_in_allows_infrastructure_pods_and_does_not_repeat_host_checks(cap):
    cap.p.pods=[{'metadata':{'name':'coredns','namespace':'kube-system'},'spec':{'nodeName':'worker-1'},'status':{'phase':'Running'}}]
    op=cap.plan(2)['capacity'];result=cap.apply(op['operation_id'])
    assert result['status']=='passed' and cap.p.size==2 and not cap.p.host_calls


def test_claim_added_after_review_blocks_apply(cap):
    op=cap.plan(2)['capacity'];cap.p.claims=[{'name':'new-application'}]
    with pytest.raises(ProviderError,match='claims'):cap.apply(op['operation_id'])
    assert not any(w[0]=='APPLY' for w in cap.gateway.writes)


def test_unrelated_plan_is_retained_for_explicit_discard(cap):
    cap.gateway.extra_change=True
    with pytest.raises(ProviderError,match='unrelated'):cap.plan(5)
    op=cap.operation();assert op['phase']=='PLAN_BLOCKED'
    assert cap.gateway.stack_data['variables']['node_count']=='5' and cap.p.size==3
    assert cap.discard(op['operation_id'])['status']=='passed'


@pytest.mark.parametrize('changed',['variables','job','expired','etag'])
def test_stale_review_never_applies(cap,changed):
    op=cap.plan(5)['capacity']
    if changed=='variables':cap.gateway.stack_data['variables']['keep']='changed-externally'
    if changed=='job':cap.gateway.records.insert(0,{'id':'external-job','stack_id':SID,'operation':'PLAN','lifecycle_state':'SUCCEEDED','time_created':'99999999'})
    if changed=='etag':cap.gateway.etag='999'
    if changed=='expired':op['expires_at']=0;cap.save(op)
    with pytest.raises(ProviderError):cap.apply(op['operation_id'])
    assert not any(w[0]=='APPLY' for w in cap.gateway.writes)


def test_lost_apply_response_resumes_existing_job_without_resubmission(cap):
    op=cap.plan(5)['capacity'];cap.gateway.fail_create='APPLY'
    with pytest.raises(ProviderError,match='lost response'):cap.apply(op['operation_id'])
    assert cap.operation()['phase']=='SUBMITTING_APPLY'
    restarted=Capacity(cap.p,cap.root,cap.gateway)
    result=restarted.resume(op['operation_id'])
    assert result['status']=='passed'
    assert sum(w[0]=='APPLY' for w in cap.gateway.writes)==1


def test_applied_operation_cannot_be_discarded(cap):
    op=cap.plan(5)['capacity'];cap.gateway.fail_create='APPLY'
    with pytest.raises(ProviderError):cap.apply(op['operation_id'])
    with pytest.raises(ProviderError,match='apply was submitted'):cap.discard(op['operation_id'])


def test_failed_apply_close_does_not_rollback_or_alter_stack(cap):
    op=cap.plan(5)['capacity'];cap.gateway.fail_job='APPLY'
    with pytest.raises(ProviderError,match='FAILED'):cap.apply(op['operation_id'])
    writes=deepcopy(cap.gateway.writes)
    result=cap.close(op['operation_id'])
    assert result['status']=='passed' and cap.gateway.writes==writes
    assert cap.operation()['phase']=='COMPLETE'


def test_connection_checks_stack_ownership_before_saving(cap,monkeypatch):
    original=cap.gateway.document
    monkeypatch.setattr(cap.gateway,'document',lambda *args:{'resources':[]})
    with pytest.raises(ProviderError,match='does not own'):cap.connect(SID)
    assert not cap.connection.exists()
    monkeypatch.setattr(cap.gateway,'document',original)
    assert cap.connect(SID)['saved'] and cap.connection.exists()


def test_pending_operation_guards_reset_settings_and_new_plans(cap):
    op=cap.plan(5)['capacity']
    for action in ['reset_rdma','settings','rdma_validation','install_dranet','capacity_plan']:
        with pytest.raises(ProviderError):cap.guard(action)
    for action in ['capacity_apply','capacity_resume','capacity_discard','check_hosts','node_diagnostic']:cap.guard(action)
    cap.discard(op['operation_id']);cap.guard('reset_rdma')


def test_capacity_api_auth_strict_counts_and_confirmation():
    assert client.get('/api/v1/capacity').status_code==401
    assert client.get('/api/v1/capacity',headers=AUTH).json()['configured'] is False
    for count in [-1,65,3.5,True]:
        assert client.post('/api/v1/jobs',headers=AUTH,json={'action':'capacity_plan','target_node_count':count,'confirmation':'oke-rdma-demo'}).status_code==422
    assert client.post('/api/v1/jobs',headers=AUTH,json={'action':'capacity_apply','capacity_operation_id':'a'*64,'confirmation':'oke-rdma-demo'}).status_code==400
    assert client.post('/api/v1/jobs',headers=AUTH,json={'action':'capacity_plan','confirmation':'oke-rdma-demo'}).status_code==422


def test_real_sdk_models_pin_apply_to_the_reviewed_plan(monkeypatch):
    import oci
    from types import SimpleNamespace
    from app.capacity import ORMGateway
    captures=[]
    class Client:
        def create_job(self,details):captures.append(details);return SimpleNamespace(data={'id':'remote-job'})
        def update_stack(self,sid,details,**kwargs):captures.append((sid,details,kwargs));return SimpleNamespace()
    monkeypatch.setattr(oci.auth.signers,'InstancePrincipalsSecurityTokenSigner',lambda:object())
    monkeypatch.setattr(oci.resource_manager,'ResourceManagerClient',lambda *a,**kw:Client())
    gateway=ORMGateway('eu-frankfurt-1')
    gateway.create(SID,'APPLY','operation','reviewed-plan-job')
    details=captures[0].job_operation_details
    assert isinstance(details,oci.resource_manager.models.CreateApplyJobOperationDetails)
    assert details.execution_plan_strategy=='FROM_PLAN_JOB_ID' and details.execution_plan_job_id=='reviewed-plan-job'
    assert details.is_provider_upgrade_required is False
    gateway.update(SID,{'node_count':'5'},'etag-before')
    assert captures[1][2]=={'if_match':'etag-before'}


def test_new_node_readiness_failure_retains_the_applied_capacity(cap,monkeypatch):
    op=cap.plan(5)['capacity']
    def failed_hosts(node_names=None):
        cap.p.host_calls.append(node_names);cap.p.host_pass=False
        return {'status':'blocked','hosts':[]}
    monkeypatch.setattr(cap.p,'check_hosts',failed_hosts)
    result=cap.apply(op['operation_id'])
    assert result['status']=='failed' and cap.p.size==5
    assert cap.operation()['phase']=='COMPLETE' and cap.operation()['readiness']=='failed'
    assert sum(w[0]=='APPLY' for w in cap.gateway.writes)==1


def test_host_subset_reuses_evidence_without_rechecking_other_workers(monkeypatch,tmp_path):
    import time
    from test_control import node,facts
    from app import provider
    p=LiveProvider(Settings());monkeypatch.setattr(provider,'STATE_DIR',tmp_path)
    monkeypatch.setattr(p,'_selected',lambda:[node('n1'),node('n2')])
    evidence={'n2':{'at':time.time(),'facts':facts(),'issues':[],'hardware_identity':{}},'n1':{'at':0,'facts':{}}}
    monkeypatch.setattr(p,'_host_cache',lambda:evidence)
    monkeypatch.setattr(p,'node_diagnostic',lambda *a:pytest.fail('An unrelated worker was rechecked'))
    result=p.check_hosts(node_names=['n2'])
    assert [h['node'] for h in result['hosts']]==['n2'] and result['hosts'][0]['reused']
    assert json.loads((tmp_path/'host-readiness.json').read_text())['n1']['at']==0


def streamed_gateway(payload, encoding=None):
    """Use the actual SDK transport types without credentials or network calls."""
    import io
    from types import SimpleNamespace
    from oci._vendor import requests
    import urllib3
    from app.capacity import ORMGateway
    response = requests.Response()
    response.raw = urllib3.response.HTTPResponse(
        body=io.BytesIO(payload), headers={'Content-Encoding': encoding} if encoding else {},
        preload_content=False, decode_content=False)
    gateway = ORMGateway.__new__(ORMGateway)
    calls = []
    def call(name, identifier, **kwargs):
        calls.append((name, identifier, kwargs))
        return SimpleNamespace(data=response)
    gateway.call = call
    return gateway, response, calls


@pytest.mark.parametrize('name', ['get_stack_tf_state', 'get_job_tf_plan'])
@pytest.mark.parametrize('mode', ['plain', 'http-gzip', 'artifact-gzip', 'http-deflate'])
def test_orm_documents_decode_real_transport_streams(name, mode):
    import gzip
    import zlib
    document = {'resources': [], 'description': 'CPU RDMA'}
    raw = json.dumps(document).encode()
    encoding = None
    if mode in {'http-gzip', 'artifact-gzip'}: raw = gzip.compress(raw)
    if mode == 'http-gzip': encoding = 'gzip'
    if mode == 'http-deflate': raw = zlib.compress(raw); encoding = 'deflate'
    gateway, response, calls = streamed_gateway(raw, encoding)
    assert gateway.document(name, 'identifier') == document
    assert response.raw.closed
    assert calls == [(name, 'identifier', {'tf_plan_format': 'JSON'} if name == 'get_job_tf_plan' else {})]


@pytest.mark.parametrize('mode', ['plain', 'http-gzip', 'artifact-gzip'])
def test_orm_document_limits_apply_to_expanded_bytes(mode):
    import gzip
    raw = b'{"secret":"' + b'x' * 10_000_000 + b'"}'
    if mode != 'plain': raw = gzip.compress(raw)
    gateway, response, _ = streamed_gateway(raw, 'gzip' if mode == 'http-gzip' else None)
    with pytest.raises(ProviderError, match='size limit'):
        gateway.document('get_stack_tf_state', SID)
    assert response.raw.closed


@pytest.mark.parametrize('raw,encoding', [
    (b'{"secret":"sensitive-value"', None), (b'\x1f\x8btruncated', None),
    (b'\x1f\x8btruncated', 'gzip'), (b'[]', None), (b'\xff', None)])
def test_orm_document_failures_are_sanitized_and_close_stream(raw, encoding):
    gateway, response, _ = streamed_gateway(raw, encoding)
    with pytest.raises(ProviderError, match='could not be decoded') as error:
        gateway.document('get_stack_tf_state', SID)
    assert 'sensitive-value' not in str(error.value)
    assert response.raw.closed


def test_readiness_is_read_only_and_does_not_claim_write_access(cap):
    result=cap.readiness(5)
    assert result['can_plan'] and not cap.gateway.writes
    assert any(c['name']=='IAM write access' and c['status']=='unverified' for c in result['checks'])
    assert not cap.operation()
    cap.p.cycling=True
    result=cap.readiness(5)
    assert not result['can_plan'] and not cap.gateway.writes
    assert any('cycling' in c['detail'] for c in result['checks'] if c['status']=='block')


def test_connection_errors_identify_the_actual_field(cap,monkeypatch):
    from app.provider_base import ActionError
    monkeypatch.setattr(cap.gateway,'document',lambda *args:{'resources':[]})
    with pytest.raises(ActionError) as error:cap.connect(SID)
    assert error.value.detail['field']=='stack_id'
    assert error.value.detail['code']=='stack_ownership'
    response=client.put('/api/v1/capacity/connection',headers=AUTH,json={'stack_id':'invalid','confirmation':'oke-rdma-demo'})
    assert response.status_code==422
    assert any(f['field']=='stack_id' for f in response.json()['detail']['fields'])
    response=client.put('/api/v1/capacity/connection',headers=AUTH,json={'stack_id':SID,'confirmation':'wrong'})
    assert response.status_code==400 and response.json()['detail']['field']=='confirmation'


def test_readiness_api_auth_and_bounds():
    assert client.get('/api/v1/capacity/readiness?target=5').status_code==401
    assert client.get('/api/v1/capacity/readiness?target=65',headers=AUTH).status_code==422
    assert client.get('/api/v1/capacity/readiness?target=5',headers=AUTH).json()['can_plan'] is False


def test_blocked_plan_names_fields_without_exposing_values(cap):
    doc=plan_document()
    doc['resource_changes'][0]['change']['before']['node_metadata']={'private_key':'OLD-SECRET'}
    doc['resource_changes'][0]['change']['after']['node_metadata']={'secret-map-key':'NEW-SECRET'}
    doc['resource_changes'][0]['change']['after']['node_config_details'][0]['placement_configs']=[{'availability_domain':'SENSITIVE'}]
    with pytest.raises(ProviderError) as error:validate_plan(doc,'pool-id',3,5)
    message=str(error.value)
    assert 'node_metadata' in message and 'node_config_details[0].placement_configs' in message
    assert not any(x in message for x in ['OLD-SECRET','NEW-SECRET','secret-map-key','SENSITIVE'])


def test_blocked_reason_is_retained_without_allowing_apply(cap):
    original=cap.gateway.create
    def create(*args,**kwargs):
        job=original(*args,**kwargs)
        if args[1]=='PLAN':cap.gateway.documents[job['id']]['resource_changes'][0]['change']['after']['node_shape']='different'
        return job
    cap.gateway.create=create
    with pytest.raises(ProviderError,match='node_shape'):cap.plan(5)
    op=cap.operation()
    assert op['phase']=='PLAN_BLOCKED' and 'node_shape' in op['blocked_reason']
    with pytest.raises(ProviderError):cap.apply(op['operation_id'])
    assert not any(w[0]=='APPLY' for w in cap.gateway.writes)

@pytest.mark.parametrize('target',[0,1])
def test_scale_below_two_and_back_up(cap, target):
    original=cap.p.inventory
    def inventory():
        inv=original(); inv['driver_ready']=cap.p.size>0
        return inv
    cap.p.inventory=inventory
    op=cap.plan(target)['capacity']
    result=cap.apply(op['operation_id'])
    assert result['status']=='passed' and cap.p.size==target
    assert cap.operation()['readiness']==('paused' if target==0 else 'passed')
    assert not cap.p.host_calls
    op=cap.plan(2)['capacity']
    assert cap.apply(op['operation_id'])['status']=='passed'
    assert cap.p.host_calls==[[f'worker-{n}' for n in range(target+1,3)]]


def test_initial_empty_pool_can_scale_without_driver(cap):
    cap.p.size=0
    cap.gateway.stack_data['variables']['node_count']='0'
    original=cap.p.inventory
    def inventory():
        inv=original();inv.update(driver_installed=False,driver_ready=False);return inv
    cap.p.inventory=inventory
    op=cap.plan(1)['capacity']
    result=cap.apply(op['operation_id'])
    assert result['status']=='passed' and result['capacity']['readiness']=='setup_required'
    assert cap.p.size==1 and not cap.p.host_calls


def test_zero_scale_retains_claim_guard(cap):
    cap.p.claims=[{'name':'active-claim'}]
    with pytest.raises(ProviderError,match='Release RDMA claims'):cap.plan(0)
    assert not cap.gateway.writes


@pytest.mark.parametrize('old,new,equal', [('PT1H','PT60M',True),('PT3600S','PT60M',True),('PT0.5H','PT30M',True),('PT30M','PT60M',False),(None,'PT60M',False),('PT','PT0S',False)])
def test_eviction_duration_semantics(old,new,equal):
    doc=plan_document();change=doc['resource_changes'][0]['change']
    change['before']['node_eviction_node_pool_settings']=[{'eviction_grace_duration':old,'is_force_delete_after_grace_duration':False}]
    change['after']['node_eviction_node_pool_settings']=[{'eviction_grace_duration':new,'is_force_delete_after_grace_duration':False}]
    snapshot=deepcopy(doc)
    if equal:assert validate_plan(doc,'pool-id',3,5)['target']==5
    else:
        with pytest.raises(ProviderError,match='Eviction grace duration:'):validate_plan(doc,'pool-id',3,5)
    assert doc==snapshot


def test_equal_eviction_duration_does_not_hide_other_changes():
    doc=plan_document();change=doc['resource_changes'][0]['change']
    change['before']['node_eviction_node_pool_settings']=[{'eviction_grace_duration':'PT1H','is_force_delete_after_grace_duration':False}]
    change['after']['node_eviction_node_pool_settings']=[{'eviction_grace_duration':'PT60M','is_force_delete_after_grace_duration':True}]
    with pytest.raises(ProviderError,match='is_force_delete_after_grace_duration'):validate_plan(doc,'pool-id',3,5)


def test_zero_and_one_are_valid_api_model_counts():
    from app.models import JobCreate
    for count in [0,1,2,64]:
        assert JobCreate(action='capacity_plan',target_node_count=count,confirmation='environment').target_node_count==count


def test_zero_completion_waits_for_oci_deletion(cap,monkeypatch):
    op=cap.plan(0)['capacity']
    original=cap.pool;calls=[]
    def pool():
        value=original()
        if cap.p.size==0 and not calls:
            value['nodes']=[{'id':'instance-old','lifecycle_state':'DELETING'}]
            calls.append('deleting')
        return value
    cap.pool=pool
    sleeps=[];monkeypatch.setattr('app.capacity.time.sleep',lambda seconds:sleeps.append(seconds))
    result=cap.apply(op['operation_id'])
    assert result['capacity']['readiness']=='paused' and sleeps==[10]


@pytest.mark.parametrize('target',[0,2])
def test_scale_in_accepts_failed_rdma_but_still_checks_workloads(cap,target):
    cap.p.host_pass=False
    inventory=cap.p.inventory
    cap.p.inventory=lambda:dict(inventory(),driver_ready=False)
    assert len(cap.checks(cap.pool(),target))==3
    cap.p.claims=[{'name':'reserved'}]
    with pytest.raises(ProviderError,match='claims'):cap.checks(cap.pool(),target)
    cap.p.claims=[];cap.p.force=True
    with pytest.raises(ProviderError,match='force termination'):cap.checks(cap.pool(),target)


def test_scale_out_reports_actual_host_problem(cap):
    cap.p.host_pass=False
    with pytest.raises(ProviderError,match='worker-1: host needs attention'):cap.checks(cap.pool(),4)


def test_partial_scale_in_finishes_with_failed_rdma_without_waiting(cap,monkeypatch):
    cap.p.host_pass=False
    inventory=cap.p.inventory
    cap.p.inventory=lambda:dict(inventory(),driver_ready=False)
    monkeypatch.setattr('app.capacity.time.sleep',lambda _:pytest.fail('Must not wait for RDMA on scale-in'))
    op=cap.plan(2)['capacity']
    result=cap.apply(op['operation_id'])
    assert result['capacity']['phase']=='COMPLETE'
    assert result['capacity']['readiness']=='failed'
    assert cap.p.size==2


def test_operator_image_drift_names_actual_dropdown():
    plan=plan_document()
    plan['resource_changes'].append({'address':'oci_core_instance.operator','change':{'actions':['update'],'before':{'source_details':[{'source_id':'old'}]},'after':{'source_details':[{'source_id':'new'}]}}})
    with pytest.raises(ProviderError,match='Approved operator image override') as error:
        validate_plan(plan,'pool-id',3,5)
    assert 'source_details[0].source_id' in str(error.value)


@pytest.mark.parametrize('pin', ['', 'different-image'])
def test_image_pin_blocks_before_any_stack_mutation(cap, pin):
    cap.gateway.stack_data['variables']['operator_image_ocid'] = pin
    with pytest.raises(ProviderError, match='Operator image must be pinned'):
        cap.plan(5)
    assert cap.gateway.writes == []
    assert cap.gateway.stack_data['variables']['node_count'] == '3'
    assert not cap.readiness(5)['can_plan']
