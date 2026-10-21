from dataclasses import replace
import pytest
from app.provider import LiveProvider, ProviderError
from app.config import Settings
from app import dranet
from test_control import node


def inventory_provider(monkeypatch, *, allocated=True, parent='10.224.0.105/12'):
    p=LiveProvider(Settings());monkeypatch.setattr(p,'_rdma_nodes',lambda:[node()]);monkeypatch.setattr(p,'_host_issues',lambda n:[])
    a={'dra.net/ifName':{'string':'ens800f0np0'},'dra.net/rdma':{'bool':True},'dra.net/rdmaDevice':{'string':'mlx5_2'},'dra.net/mtu':{'int':4220},'dra.net/state':{'string':'up'},'dra.net/ipv4':{'string':parent},'oke.dra.net/rdmaChildIpv4Cidr':{'string':'10.208.0.0/12'}}
    claims=[{'metadata':{'name':'retained','namespace':'rdma-workloads'},'status':{'allocation':{'devices':{'results':[{'driver':'dra.net','pool':'n1','device':'pf'}]}}}}] if allocated else []
    args=['--cloud-provider-hint=OKE','--profile-provider=cloud','--move-ib-interfaces=false','--cloud-provider-options=oke.rdma-child-ipv4-cidr=10.208.0.0/12', '--filter='+dranet.values(Settings())['args']['filter'], '--kubelet-root-dir=/var/lib/kubelet']
    def read(argv,**kwargs):
        if 'resourceslices' in argv:return {'items':[{'spec':{'nodeName':'n1','pool':{'name':'n1'},'devices':[{'name':'pf','attributes':a}]}}]}
        if 'resourceclaims' in argv:return {'items':claims}
        if 'pods' in argv:return {'items':[{'metadata':{'name':'dranet-xx','namespace':'kube-system','labels':{'app.kubernetes.io/instance':'dranet'}},'spec':{'nodeName':'n1'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        if 'daemonset' in argv:return {'spec':{'template':{'spec':{'nodeSelector':{'oke-rdma-mode':'dranet'},'containers':[{'args':args,'image':'registry.k8s.io/networking/dranet:v1.5.0'}]}}}}
        if 'deviceclass' in argv:return dranet.DEVICE_CLASS
        raise AssertionError(argv)
    monkeypatch.setattr(p,'_json',read)
    return p


def test_retained_claim_reserves_capacity_without_pod(monkeypatch):
    inv=inventory_provider(monkeypatch).inventory()
    assert inv['capacity']=={'total':1,'free':0,'claims':1}
    assert inv['claims'][0]['consumers']==[]


def test_invalid_parent_ip_blocks_device(monkeypatch):
    inv=inventory_provider(monkeypatch,allocated=False,parent='').inventory()
    assert not inv['driver_ready'] and inv['capacity']['free']==0


def test_no_free_capacity_never_deletes_existing_workload(monkeypatch):
    p=inventory_provider(monkeypatch)
    monkeypatch.setattr(p,'cleanup_validation',lambda:pytest.fail('Deleted workload while capacity was reserved'))
    with pytest.raises(ProviderError,match='Two distinct'):p.rdma_validation()


def test_install_no_escalation_for_restricted_operator(monkeypatch):
    p=LiveProvider(replace(Settings(),operator_kubernetes_access_mode='least_privilege_rbac'))
    monkeypatch.setattr(p,'preflight',lambda:{'blocking':0})
    monkeypatch.setattr(p,'inventory',lambda:{'issues':[],'driver_installed':False})
    monkeypatch.setattr(p,'_run',lambda *a,**kw:pytest.fail('Attempted privileged Helm installation'))
    with pytest.raises(ProviderError,match='cluster administrator'):p.install_dranet()


def test_validation_failure_preserves_evidence_and_releases_claims(monkeypatch):
    p=LiveProvider(replace(Settings(),rdma_tools_image='repo@sha256:'+'a'*64))
    rows=[{'name':name,'mode':'dranet','ready':True,'cordoned':False,'issues':[],
           'devices':[{'valid':True,'allocated':False,'attributes':{'dra.net/rdmaDevice':'mlx5_2'}}]} for name in ['n1','n2']]
    monkeypatch.setattr(p,'inventory',lambda:{'driver_ready':True,'nodes':rows})
    monkeypatch.setattr(p,'_selected',lambda:[node('n1'),node('n2')])
    monkeypatch.setattr(p,'_host_issues',lambda n:[])
    monkeypatch.setattr(p,'_json',lambda *a,**kw:{})
    monkeypatch.setattr(p,'_apply_documents',lambda *a,**kw:'ok')
    def fail_wait(*a,**kw):raise ProviderError('ImagePullBackOff')
    monkeypatch.setattr(p,'kubectl',fail_wait)
    monkeypatch.setattr(p,'validation_startup',fail_wait)
    cleanup=[]
    monkeypatch.setattr(p,'cleanup_validation',lambda:cleanup.append(True) or {'status':'passed'})
    result=p.rdma_validation()
    assert result['status']=='failed' and result['error']=='ImagePullBackOff'
    assert result['stage']=='Pod preparation' and cleanup==[True]


def test_cleanup_failure_not_reported_as_success(monkeypatch):
    p=LiveProvider(replace(Settings(),rdma_tools_image='repo@sha256:'+'a'*64))
    rows=[{'name':name,'mode':'dranet','ready':True,'cordoned':False,'issues':[],
           'devices':[{'valid':True,'allocated':False,'attributes':{'dra.net/rdmaDevice':'mlx5_2'}}]} for name in ['n1','n2']]
    monkeypatch.setattr(p,'inventory',lambda:{'driver_ready':True,'nodes':rows})
    monkeypatch.setattr(p,'_selected',lambda:[node('n1'),node('n2')]);monkeypatch.setattr(p,'_host_issues',lambda n:[])
    monkeypatch.setattr(p,'_json',lambda *a,**kw:{});monkeypatch.setattr(p,'_apply_documents',lambda *a,**kw:'ok')
    monkeypatch.setattr(p,'kubectl',lambda *a,**kw:None)
    monkeypatch.setattr(p,'validation_startup',lambda *a,**kw:{'status':'passed','pods':[]})
    import subprocess
    monkeypatch.setattr(p,'_run',lambda *a,**kw:subprocess.CompletedProcess([],0,'{"status":"passed","average_gbps":79.50}',''))
    def cleanup():raise ProviderError('deletion timed out')
    monkeypatch.setattr(p,'cleanup_validation',cleanup)
    result=p.rdma_validation()
    assert result['average_gbps']==79.50 and result['status']=='failed'
    assert result['cleanup']['error']=='deletion timed out'


def test_fresh_install_is_setup_pending_not_broken(monkeypatch):
    p=inventory_provider(monkeypatch,allocated=False)
    original=p._json
    def read(argv, **kwargs):
        if 'daemonset' in argv or 'deviceclass' in argv: return {}
        if 'resourceslices' in argv or 'pods' in argv: return {'items': []}
        return original(argv, **kwargs)
    monkeypatch.setattr(p,'_json',read)
    inv=p.inventory()
    assert not inv['driver_installed'] and not inv['driver_ready']
    assert inv['issues']==[]
    n=node();n['status']['conditions'][0]['status']='False'
    monkeypatch.setattr(p,'_rdma_nodes',lambda:[n])
    assert any('not Ready' in i['message'] for i in p.inventory()['issues'])


@pytest.mark.parametrize('latency_ok',[True,False])
def test_combined_validation_preserves_bandwidth_and_always_cleans_up(monkeypatch,latency_ok):
    import json, subprocess
    p=LiveProvider(replace(Settings(),rdma_tools_image='repo@sha256:'+'a'*64))
    rows=[{'name':name,'mode':'dranet','ready':True,'cordoned':False,'issues':[],
           'devices':[{'valid':True,'allocated':False,'attributes':{'dra.net/rdmaDevice':'mlx5_2'}}]} for name in ['n1','n2']]
    monkeypatch.setattr(p,'inventory',lambda:{'driver_ready':True,'nodes':rows})
    monkeypatch.setattr(p,'_selected',lambda:[node('n1'),node('n2')])
    monkeypatch.setattr(p,'_host_issues',lambda n:[])
    monkeypatch.setattr(p,'_json',lambda *a,**kw:{})
    monkeypatch.setattr(p,'_apply_documents',lambda *a,**kw:'ok')
    monkeypatch.setattr(p,'kubectl',lambda *a,**kw:None)
    monkeypatch.setattr(p,'validation_startup',lambda *a,**kw:{'status':'passed','pods':[]})
    calls=[];phases=[];clean=[]
    p.report_progress=lambda stage,done,total,detail='':phases.append((stage,done,total))
    def run(argv,**kw):
        test=argv[-1];calls.append(test)
        result={'status':'passed','average_gbps':79.5} if test=='bandwidth' else ({'status':'passed','average_us':1.7} if latency_ok else {'status':'failed','error':'latency failed'})
        return subprocess.CompletedProcess(argv,0 if result['status']=='passed' else 1,json.dumps(result),'')
    monkeypatch.setattr(p,'_run',run)
    monkeypatch.setattr(p,'cleanup_validation',lambda:clean.append(True) or {'status':'passed'})
    result=p.rdma_validation()
    assert calls==['bandwidth','latency'] and clean==[True]
    assert result['average_gbps']==79.5
    assert result['status']==('passed' if latency_ok else 'failed')
    assert result['latency']['status']==('passed' if latency_ok else 'failed')
    assert [v[1] for v in phases]==[0,1,2,3,4]
