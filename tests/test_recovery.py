import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
import pytest
from app.config import Settings
from app.provider import LiveProvider, assess_host
from app.provider_base import ProviderError
from app.recovery import Recovery, documents, save, active, guard

@pytest.fixture(autouse=True)
def recovery_state(tmp_path,monkeypatch):
    monkeypatch.setenv('HPC_CONTROL_RECOVERY_DIR',str(tmp_path/'recovery'))


def worker():
    return {'metadata':{'name':'n1','uid':'node-uid','resourceVersion':'8','labels':{'oci-rdma':'true','oke-rdma-mode':'dranet'}},
            'spec':{},'status':{'conditions':[{'type':'Ready','status':'True'}],'nodeInfo':{'bootID':'boot'}}}


def test_missing_inventory_is_not_six_host_faults():
    issues=assess_host({},Settings())
    assert len(issues)==1 and 'unavailable' in issues[0]


def test_selected_port_not_unused_port():
    p=LiveProvider(Settings())
    findings=p._diagnostic_findings('rdma-link-health','link mlx5_2/1 state ACTIVE physical_state LINK_UP netdev ens800f0np0\nlink mlx5_3/1 state DOWN physical_state DISABLED netdev ens800f1np1')
    assert not any(f['severity']=='block' for f in findings)
    assert any(f['severity']=='block' for f in p._diagnostic_findings('rdma-link-health','link mlx5_2/1 state DOWN physical_state DISABLED netdev ens800f0np0'))


def test_registry_checks_never_return_secret_data(monkeypatch):
    p=LiveProvider(replace(Settings(),rdma_image_pull_secret='ocir-pull'))
    calls=[]
    def k(args,**kw):
        calls.append(args)
        return subprocess.CompletedProcess(args,1,'','Error from server (NotFound): secrets "ocir-pull" not found')
    monkeypatch.setattr(p,'kubectl',k)
    with pytest.raises(ProviderError,match='oke-rdma-diagnostics'): p.require_registry(['oke-rdma-diagnostics'])
    assert calls[0][-1]=='jsonpath={.type}'


def test_restricted_mode_never_escalates():
    p=LiveProvider(replace(Settings(),operator_kubernetes_access_mode='least_privilege_rbac'))
    with pytest.raises(ProviderError,match='restricted RBAC'): Recovery(p).authorize()


def test_pending_claims_block_recovery(monkeypatch):
    p=LiveProvider(Settings())
    monkeypatch.setattr(p,'_json',lambda args: {'items':[{'metadata':{'name':'pending'}}]})
    with pytest.raises(ProviderError,match='pending reservations'): Recovery(p).idle('n1')


def test_application_pod_blocks_recovery(monkeypatch):
    p=LiveProvider(Settings())
    def read(args):
        return {'items':[{'metadata':{'namespace':'work','name':'application'},'status':{'phase':'Running'}}]} if 'pods' in args else {'items':[]}
    monkeypatch.setattr(p,'_json',read)
    with pytest.raises(ProviderError,match='not idle'): Recovery(p).idle('n1')


def test_preview_rejects_changed_boot_and_expiry(tmp_path,monkeypatch):
    from app.recovery import state_dir
    p=LiveProvider(Settings());service=Recovery(p)
    monkeypatch.setattr(service,'node',lambda name:worker())
    preview={'eligible':True,'created_at':time.time(),'settings':p.s.rdma_config(),'node_name':'n1','node_uid':'node-uid','boot_id':'old'}
    save(state_dir()/('preview-'+'a'*32+'.json'),preview)
    with pytest.raises(ProviderError,match='boot changed'):service.read_preview('a'*32)
    preview['created_at']=0;save(state_dir()/('preview-'+'a'*32+'.json'),preview)
    with pytest.raises(ProviderError,match='expired'):service.read_preview('a'*32)


def runner(monkeypatch, *, denied=True, restart_fails=False):
    from app.recovery import state_dir
    p=LiveProvider(replace(Settings(),rdma_tools_image='registry/repo@sha256:'+'a'*64))
    s=Recovery(p);calls=[]
    monkeypatch.setattr(s,'authorize',lambda:None)
    monkeypatch.setattr(s,'read_preview',lambda value:({'node_name':'n1'},worker()))
    monkeypatch.setattr(s,'node',lambda value:worker())
    monkeypatch.setattr(s,'idle',lambda *a,**k:None)
    monkeypatch.setattr(p,'require_registry',lambda *a:None)
    monkeypatch.setattr(p,'_host_cache',lambda:{})
    monkeypatch.setattr('app.recovery.time.sleep',lambda _:None)
    ticks=iter(range(0,10000,31))
    monkeypatch.setattr('app.recovery.time.monotonic',lambda:next(ticks))
    facts={'boot_id':'boot'}
    probes=iter([(facts,{'metadata_http':200,'recognized_failure':True,'agent_active':True},['down']),
                 (facts,{'metadata_http':200,'agent_active':True},[]),(facts,{'metadata_http':200,'agent_active':True},[])])
    monkeypatch.setattr(s,'check',lambda record:next(probes))
    def k(args,**kw):
        calls.append((args,kw))
        if '--dry-run=server' in args or '--raw' in args:
            return subprocess.CompletedProcess(args,1 if denied else 0,'','OKE_RDMA_RECOVERY_GUARD' if denied else '')
        if 'exec' in args:
            journal=json.loads(next(state_dir().glob('attempt-*')).read_text())
            assert journal['restart_attempted'] and journal['phase']=='RESTART_SUBMITTED'
            return subprocess.CompletedProcess(args,1 if restart_fails else 0,'','restart error' if restart_fails else '')
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(p,'kubectl',k)
    return s,calls


def test_restart_once_durable_intent_and_restore(monkeypatch):
    s,calls=runner(monkeypatch)
    result=s.run('a'*32,'11111111-1111-1111-1111-111111111111')
    assert result['status']=='passed'
    assert len([a for a,k in calls if 'exec' in a])==1
    patches=[json.loads(a[-1]) for a,k in calls if a[0]=='patch']
    assert patches[0][-1]['value'] is True and patches[-1][-1]['value'] is False
    assert not active()


def test_no_restart_without_verified_admission(monkeypatch):
    s,calls=runner(monkeypatch,denied=False)
    result=s.run('a'*32,'11111111-1111-1111-1111-111111111111')
    assert result['status']=='blocked'
    assert not any('exec' in a for a,k in calls)
    assert active()
    with pytest.raises(ProviderError,match='protection'):guard('rdma_validation')


def test_uncertain_restart_is_not_replayed_and_manual_finish_keeps_cordon(monkeypatch):
    s,calls=runner(monkeypatch,restart_fails=True)
    attempt='11111111-1111-1111-1111-111111111111'
    result=s.run('a'*32,attempt)
    assert result['status']=='blocked' and active()[0]['restart_attempted']
    with pytest.raises(ProviderError,match='already recorded'):s.run('a'*32,attempt)
    result=s.finish(attempt)
    assert result['status']=='passed'
    assert len([a for a,k in calls if 'exec' in a])==1
    assert not any(a[0]=='patch' and json.loads(a[-1])[-1]['value'] is False for a,k in calls)


def test_admission_targets_pods_and_bindings():
    policy,binding=documents('guard','n1')
    assert policy['spec']['failurePolicy']=='Fail'
    assert policy['spec']['matchConstraints']['resourceRules'][0]['resources']==['pods','pods/binding','bindings']
    assert binding['spec']['validationActions']==['Deny']


def test_failure_report_preserves_evidence_before_cleanup(tmp_path,monkeypatch):
    from app.diagnostics import report_path
    p=LiveProvider(replace(Settings(),rdma_tools_image='repo@sha256:'+'a'*64))
    monkeypatch.setattr(p,'validate_node_diagnostic',lambda *a:{'node_name':'n1'})
    monkeypatch.setattr(p,'require_registry',lambda *a:None)
    monkeypatch.setattr(p,'_apply_documents',lambda *a,**kw:None)
    status={'status':{'phase':'Failed','containerStatuses':[{'name':'diagnostic','state':{'terminated':{'reason':'Error','exitCode':20}}}]}}
    monkeypatch.setattr(p,'_wait_for_diagnostic_pod',lambda *a:status)
    calls=[]
    def k(args,**kw):
        calls.append(args)
        if 'get' in args and 'pod' in args:return subprocess.CompletedProcess(args,0,json.dumps(status),'')
        if 'events' in args:return subprocess.CompletedProcess(args,0,json.dumps({'items':[{'reason':'FailedToRetrieveImagePullSecret','message':'ocir-pull unavailable'}]}),'')
        if 'logs' in args:return subprocess.CompletedProcess(args,1,'','container is terminated')
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(p,'kubectl',k)
    job='22222222-2222-2222-2222-222222222222'
    result=p.node_diagnostic('n1','dranet-readiness',job)
    report=json.loads(report_path(job,'json').read_text())
    assert result['collection_status']=='failed'
    assert result['health_status']=='not_established'
    assert result['findings'][0]['code']=='registry_secret_unavailable'
    assert not any('Collection completed' in f['message'] for f in result['findings'])
    assert 'exitCode' in report['output'] and 'FailedToRetrieveImagePullSecret' in report['output']
    assert next(i for i,a in enumerate(calls) if 'events' in a) < max(i for i,a in enumerate(calls) if 'delete' in a)


def test_complete_inventory_includes_unaddressed_pf(monkeypatch,capsys):
    from app.host_readiness import SCRIPT
    code=SCRIPT.split("<<'DRANET_HOST_PY'\n",1)[1].rsplit('\nDRANET_HOST_PY',1)[0]
    def run(args,**kwargs):
        if args[:3]==('ip','-j','link'):
            out=json.dumps([{'ifname':'ens800f0np0','mtu':1500,'flags':[]}])
        elif args[0]=='ip':out='[]'
        elif args[:2]==('rdma','system'):out='netns shared'
        elif args[:2]==('rdma','link'):out='link mlx5_2/1 state DOWN physical_state DISABLED netdev ens800f0np0'
        else:out='not-found'
        return subprocess.CompletedProcess(args,0,out,'')
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setattr(Path,'exists',lambda self:True)
    monkeypatch.setattr(Path,'read_text',lambda self: '0' if self.name=='sriov_numvfs' else '')
    exec(compile(code,'inventory','exec'),{})
    facts=json.loads(capsys.readouterr().out.split('DRANET_FACTS=',1)[1])
    assert facts['interfaces'][0]['name']=='ens800f0np0'
    assert facts['interfaces'][0]['addresses']==[]


def test_physical_link_failure_blocks_restart_after_preview(monkeypatch):
    s,calls=runner(monkeypatch)
    from test_link_findings import inventory
    facts=inventory();facts['boot_id']='boot'
    monkeypatch.setattr(s,'check',lambda record:(facts,{'metadata_http':200,'agent_active':True,'recognized_failure':True},['no carrier']))
    result=s.run('a'*32,'11111111-1111-1111-1111-111111111111')
    assert result['status']=='blocked'
    assert not any('exec' in a for a,k in calls)


def test_assessment_collects_before_reporting_workload_blockers(tmp_path, monkeypatch):
    import app.diagnostics as diagnostics
    from test_control import facts
    p=LiveProvider(Settings());s=Recovery(p);events=[]
    monkeypatch.setattr(s,'authorize',lambda:None)
    monkeypatch.setattr(s,'node',lambda _:worker())
    report=tmp_path/'report.json'
    report.write_text(json.dumps({'output':'RECOVERY_FACTS='+json.dumps({'metadata_http':200,'agent_active':True,'recognized_failure':True})}))
    monkeypatch.setattr(diagnostics,'report_path',lambda *a:report)
    def diagnostic(*args):
        events.append('collected')
        return {'collection_status':'passed','facts':facts(),'reports':{}}
    def idle(*a,**kw):
        assert events==['collected']
        raise ProviderError('Worker is not idle: application')
    monkeypatch.setattr(p,'node_diagnostic',diagnostic)
    monkeypatch.setattr(s,'idle',idle)
    result=s.assess('n1','job',manual_restart=True)
    assert not result['eligible'] and result['blockers']==['Worker is not idle: application']
    assert result['probe']['metadata_http']==200


def test_coredns_exception_is_owner_verified_and_restart_only(monkeypatch):
    p=LiveProvider(Settings());s=Recovery(p)
    owner={'kind':'ReplicaSet','name':'coredns-rs','uid':'rs-uid','controller':True}
    parent={'kind':'Deployment','name':'coredns','uid':'dns-uid','controller':True}
    pod={'metadata':{'namespace':'kube-system','name':'coredns-pod','ownerReferences':[owner]},'spec':{},'status':{'phase':'Running'}}
    def read(args):
        if 'replicaset' in args:return {'metadata':{'uid':'rs-uid','ownerReferences':[parent]}}
        if 'deployment' in args:return {'metadata':{'uid':'dns-uid'}}
        return {'items':[pod] if 'pods' in args else []}
    monkeypatch.setattr(p,'_json',read)
    s.idle('n1',allow_dns=True)
    with pytest.raises(ProviderError,match='System pod'):s.idle('n1')
    owner['uid']='wrong'
    with pytest.raises(ProviderError,match='System pod'):s.idle('n1',allow_dns=True)
    owner['uid']='rs-uid';pod['spec']['hostNetwork']=True
    with pytest.raises(ProviderError,match='System pod'):s.idle('n1',allow_dns=True)


def test_explicit_restart_does_not_require_historical_crash(monkeypatch):
    s,calls=runner(monkeypatch)
    monkeypatch.setattr(s,'read_preview',lambda _:({'node_name':'n1','mode':'manual_restart'},worker()))
    monkeypatch.setattr(s,'check',lambda _:({'boot_id':'boot'},{'metadata_http':200,'agent_active':True,'recognized_failure':False},[]))
    result=s.run('a'*32,'11111111-1111-1111-1111-111111111111')
    assert result['status']=='passed'
    assert len([a for a,k in calls if 'exec' in a])==1
