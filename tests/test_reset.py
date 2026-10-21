"""Exercise reset ownership, stale preview protection, ordering and partial failure."""
from dataclasses import replace
import json
import subprocess
from copy import deepcopy
import pytest
from app import reset
from app.config import Settings, load_settings, save_rdma_config
from app.provider import LiveProvider, ProviderError
from test_control import client, AUTH


def resource(kind, name, ns='', labels=None, **spec):
    return {'kind':kind, 'metadata':{'name':name, 'namespace':ns, 'uid':'uid-'+name,
                                   'labels':labels or {}}, 'spec':spec}


class ResetProvider(LiveProvider):
    def __init__(self):
        super().__init__(replace(Settings(),rdma_tools_image='image@sha256:'+'a'*64))
        labels={'app.kubernetes.io/part-of':'oke-rdma-quickstart','app':'oke-dranet-validation'}
        self.ds=resource('DaemonSet','dranet','kube-system',template={'spec':{'nodeSelector':{'oci-rdma':'true','oke-rdma-mode':'dranet'}}})
        self.helm=True;self.helm_metadata={'name':'dranet','namespace':'kube-system','revision':1,'chart':'dranet','version':'1.5.0'}
        self.dc=resource('DeviceClass','dra.net',**deepcopy(reset.dranet.DEVICE_CLASS['spec']))
        self.claims=[resource('ResourceClaim','test-claim','rdma-workloads',labels,devices={'requests':[{'exactly':{'deviceClassName':'dra.net'}}]})]
        self.pods=[resource('Pod','test-pod','rdma-workloads',labels)]
        self.slices=[resource('ResourceSlice','test-slice',driver='dra.net',nodeName='worker-1')]
        self.templates=[];self.mutations=[];self.calls=[];self.fail_uninstall=False;self.conflict=False;self.extra_manifest=''
        self.stages=[]
        self.report_progress=lambda stage,done,total,detail='':self.stages.append((stage,done))

    def _selected(self):return [{'metadata':{'name':'worker-1'}}]
    def _json(self,args,**kwargs):
        if 'daemonset' in args:return self.ds
        if 'deviceclasses' in args:return {'items':[self.dc] if self.dc else []}
        if 'resourceclaims' in args:return {'items':self.claims}
        if 'resourceclaimtemplates' in args:return {'items':self.templates}
        if 'pods' in args:return {'items':self.pods}
        if 'resourceslices' in args:return {'items':self.slices}
        raise AssertionError(args)
    def _run(self,args,**kwargs):
        self.calls.append(args)
        if args[1]=='status':
            if not self.helm:return subprocess.CompletedProcess(args,1,'','Error: release: not found')
            return subprocess.CompletedProcess(args,0,json.dumps({'name':'dranet','namespace':'kube-system','version':1,'info':{'status':'deployed'}}),'')
        if args[1:3]==['get','metadata']:
            assert args[args.index('--revision')+1]=='1'
            return subprocess.CompletedProcess(args,0,json.dumps(self.helm_metadata),'')
        if args[1]=='get':
            return subprocess.CompletedProcess(args,0,'kind: DaemonSet\nmetadata:\n  name: dranet\n  namespace: kube-system\n'+self.extra_manifest,'')
        if args[1]=='uninstall':
            assert not self.pods and not self.claims, 'Driver stopped before owned workloads were released'
            self.mutations.append('helm-uninstall')
            if self.fail_uninstall:raise ProviderError('driver termination timed out')
            self.helm=False;self.ds={}
            return subprocess.CompletedProcess(args,0,'','')
        raise AssertionError(args)
    def kubectl(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='delete':
            assert '--raw' in args
            body=json.loads(kwargs['stdin']);assert body['preconditions']['uid']
            if self.conflict:return subprocess.CompletedProcess(args,1,'','Conflict: UID precondition failed')
            path=args[2];self.mutations.append(path)
            if '/pods/' in path:self.pods=[]
            elif '/resourceclaims/' in path:self.claims=[]
            elif '/resourceslices/' in path:self.slices=[]
            elif '/deviceclasses/' in path:self.dc={}
            else:pytest.fail('Unexpected reset target: '+path)
        else:assert 'wait' in args and '--for=delete' in args
        return subprocess.CompletedProcess(args,0,'','')


def test_preview_is_read_only_and_lists_retained_resources():
    p=ResetProvider();preview=reset.plan(p)
    assert preview['can_reset'] and len(preview['plan_id'])==64
    assert {x['kind'] for x in preview['targets']}=={'DaemonSet','Pod','ResourceClaim','ResourceSlice','DeviceClass'}
    assert not p.mutations and not any('delete' in c or 'uninstall' in c for c in p.calls)
    assert any('pull secrets' in x for x in preview['retained'])


def test_reset_orders_deletion_restores_defaults_and_is_repeatable(tmp_path):
    p=ResetProvider()
    for name in reset.LOCAL_FILES:(tmp_path/name).write_text('prior state')
    (tmp_path/'control.db').write_text('audit sentinel')
    save_rdma_config({'rdma_tools_image':'old@sha256:'+'b'*64})
    result=reset.execute(p,reset.plan(p)['plan_id'],tmp_path)
    assert result['status']=='passed',result
    assert ['/pods/' in p.mutations[0],'/resourceclaims/' in p.mutations[1],p.mutations[2]=='helm-uninstall']==[True]*3
    assert all(not (tmp_path/name).exists() for name in reset.LOCAL_FILES)
    assert (tmp_path/'control.db').read_text()=='audit sentinel'
    assert not reset._runtime_path().exists()
    assert json.loads((tmp_path/'rdma-reset.json').read_text())['reset_at']>0
    assert [n for stage,n in p.stages]==[0,1,2,3,4]
    p.mutations.clear()
    assert reset.execute(p,reset.plan(p)['plan_id'],tmp_path)['status']=='passed'
    assert not p.mutations


@pytest.mark.parametrize('type',['pending-claim','allocated-claim','template','foreign-consumer'])
def test_application_users_block_reset_without_mutation(tmp_path,type):
    p=ResetProvider()
    if type=='pending-claim':p.claims.append(resource('ResourceClaim','application','app',devices={'requests':[{'firstAvailable':[{'deviceClassName':'dra.net'}]}]}))
    if type=='allocated-claim':
        c=resource('ResourceClaim','application','app');c['status']={'allocation':{'devices':{'results':[{'driver':'dra.net'}]}}};p.claims.append(c)
    if type=='template':p.templates.append(resource('ResourceClaimTemplate','application','app',spec={'devices':{'requests':[{'exactly':{'deviceClassName':'dra.net'}}]}}))
    if type=='foreign-consumer':p.pods.append(resource('Pod','foreign','rdma-workloads',resourceClaims=[{'resourceClaimName':'test-claim'}]))
    preview=reset.plan(p)
    assert not preview['can_reset'] and preview['blockers']
    with pytest.raises(ProviderError,match='Reset blocked'):reset.execute(p,preview['plan_id'],tmp_path)
    assert not p.mutations


def test_stale_preview_rejected_before_deletion(tmp_path):
    p=ResetProvider();preview=reset.plan(p)
    p.claims[0]['metadata']['uid']='replacement'
    with pytest.raises(ProviderError,match='inventory changed'):reset.execute(p,preview['plan_id'],tmp_path)
    assert not p.mutations


def test_uid_conflict_stops_reset_without_force(tmp_path):
    p=ResetProvider();preview=reset.plan(p);p.conflict=True
    result=reset.execute(p,preview['plan_id'],tmp_path)
    assert result['status']=='failed' and 'UID precondition' in result['error']
    assert not p.mutations and not (tmp_path/'rdma-reset.json').exists()


def test_uninstall_failure_records_partial_cleanup_without_resetting_state(tmp_path):
    p=ResetProvider();p.fail_uninstall=True
    (tmp_path/'host-readiness.json').write_text('evidence')
    result=reset.execute(p,reset.plan(p)['plan_id'],tmp_path)
    assert result['status']=='failed' and result['stage']=='Uninstalling Dranet'
    assert len(result['removed'])==2
    assert (tmp_path/'host-readiness.json').read_text()=='evidence'
    assert not (tmp_path/'rdma-reset.json').exists() and p.dc and p.slices


def test_restricted_operator_cannot_uninstall_infrastructure(tmp_path):
    p=ResetProvider();p.s=replace(p.s,operator_kubernetes_access_mode='least_privilege_rbac')
    assert not reset.plan(p)['can_reset'] and not p.calls


@pytest.mark.parametrize('change',['manifest','class','nodes'])
def test_unexpected_resource_ownership_blocks_reset(change):
    p=ResetProvider()
    if change=='manifest':p.extra_manifest='---\nkind: Namespace\nmetadata:\n  name: user-data\n'
    if change=='class':p.dc['spec']={'selectors':[]}
    if change=='nodes':p.ds['spec']['template']['spec']['nodeSelector']={}
    assert not reset.plan(p)['can_reset']


def test_reset_api_requires_preview_and_distinct_typed_confirmation():
    assert client.get('/api/v1/reset-plan').status_code==401
    preview=client.get('/api/v1/reset-plan',headers=AUTH).json()
    assert preview['can_reset']
    body={'action':'reset_rdma','confirmation':'oke-rdma-demo','reset_plan_id':preview['plan_id']}
    assert client.post('/api/v1/jobs',headers=AUTH,json=body).status_code==400
    body['confirmation']='RESET oke-rdma-demo';body['reset_plan_id']='1'*64
    assert client.post('/api/v1/jobs',headers=AUTH,json=body).status_code==409
    body['reset_plan_id']=preview['plan_id']
    assert client.post('/api/v1/jobs',headers=AUTH,json=body).status_code==200


@pytest.mark.parametrize('field,value', [('chart','other'),('version','9.9.9'),('name','other'),('namespace','default'),('revision',2)])
def test_reset_rejects_wrong_release_metadata(field,value):
    p=ResetProvider();p.helm_metadata[field]=value
    preview=reset.plan(p)
    assert not preview['can_reset'] and not p.mutations


def test_reset_uses_chart_metadata_separately_from_chartless_helm_status():
    p=ResetProvider()
    preview=reset.plan(p)
    assert preview['can_reset']
    assert ['helm','get','metadata','dranet','-n','kube-system','--revision','1','-o','json'] in p.calls
    assert ['helm','get','manifest','dranet','-n','kube-system','--revision','1'] in p.calls
