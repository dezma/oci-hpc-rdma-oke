from dataclasses import replace
import json
import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import Settings, load_settings
from app.models import RdmaConfigUpdate
from app.provider import LiveProvider, DemoProvider, ProviderError, assess_host
from app import dranet, db
from app.jobs import validate_request
from app.diagnostics import sanitize_output

client = TestClient(app)
AUTH = {'Authorization': 'Bearer demo'}

def facts(**overrides):
    return {'boot_id': 'boot1', 'netns': 'netns shared copy-on-fork on', 'nri_socket': True,
            'sriov_boot_units': {'sriov-config.service': 'disabled'},
            'interfaces': [{'name':'ens800f0np0','mtu':4220,'carrier':'1','driver':'mlx5_core',
                            'addresses':['10.224.0.105/12'],'num_vfs':0,'vf_links':0}], **overrides}


def node(name='n1'):
    return {'metadata':{'name':name,'labels':{'oci-rdma':'true','oke-rdma-mode':'dranet'}},
            'status':{'nodeInfo':{'bootID':'boot1'},'conditions':[{'type':'Ready','status':'True'}]}}

@pytest.mark.parametrize('url', ['/api/v1/environment','/api/v1/jobs','/api/v1/operations/events','/api/v1/exports/values','/api/v1/exports/snapshot'])
def test_auth_required(url):
    assert client.get(url).status_code == 401
    assert client.get(url,headers=AUTH).status_code == 200


def test_security_and_token_not_stored_in_browser():
    r=client.get('/'); assert r.status_code==200
    assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
    from pathlib import Path
    js=(Path(__file__).parents[1]/'control/web/app.js').read_text()
    assert 'localStorage' not in js and 'sessionStorage' not in js


def test_demo_measurement_never_fabricated():
    r=DemoProvider(load_settings()).rdma_validation()
    assert r['status']=='simulated' and r['average_gbps'] is None

@pytest.mark.parametrize('action',['install_dranet','check_hosts','rdma_validation','cleanup_validation','node_diagnostic'])
def test_mutations_confirmed(action):
    with pytest.raises(ValueError):validate_request(Settings(),action,'wrong')
    validate_request(Settings(),action,Settings().environment_name)


def test_unsupported_actions_rejected():
    for body in [{'action':'configure_vfs'}, {'action':'install_dranet','command':'reboot'}]:
        assert client.post('/api/v1/jobs',headers=AUTH,json=body).status_code==422


def test_frozen_job_payload_and_duplicate_guard():
    r=client.post('/api/v1/jobs',headers=AUTH,json={'action':'rdma_validation','confirmation':'oke-rdma-demo'})
    assert r.status_code==200 and r.json()['payload']['settings']['rdma_tos']==41
    assert client.post('/api/v1/jobs',headers=AUTH,json={'action':'preflight'}).status_code==409
    assert client.put('/api/v1/diagnostics/image',headers=AUTH,json={'image':'r@sha256:'+'a'*64}).status_code==409


def test_failed_result_keeps_evidence():
    j=db.enqueue('check_hosts','');db.complete(j['id'],{'status':'blocked','hosts':['evidence']})
    out=db.get_job(j['id']);assert out['state']=='FAILED' and out['result']['hosts']==['evidence']


def test_expired_mutation_not_replayed():
    j=db.enqueue('install_dranet','');db.claim_next(lease_seconds=-1)
    assert db.claim_next() is None
    assert db.get_job(j['id'])['state']=='FAILED'
    assert db.get_job(j['id'])['finished_at']


def test_config_image_independent_of_discovery():
    r=client.put('/api/v1/diagnostics/image',headers=AUTH,json={'image':'repo/image@sha256:'+'a'*64})
    assert r.status_code==200
    assert load_settings().rdma_tools_image.endswith('a'*64)

@pytest.mark.parametrize('cidr',['10.224.0.0/12','127.0.0.0/8','169.254.0.0/16','224.0.0.0/4','10.208.0.1/12','::/64'])
def test_invalid_child_ranges(cidr):
    with pytest.raises(ValueError):RdmaConfigUpdate(rdma_pf_name='ens800f0np0',rdma_child_cidr=cidr)


def test_vcn_and_service_overlap_blocked():
    p=LiveProvider(Settings())
    for cidr in ['10.70.0.0/16','10.96.0.0/16','10.208.0.0/28']:
        with pytest.raises(ProviderError):p.validate_rdma_selection({'rdma_child_cidr':cidr})


def test_healthy_pf_does_not_depend_on_sriov_capacity():
    assert assess_host(facts(),Settings())==[]
    f=facts();f['interfaces'].append({'name':'ens300f0np0','mtu':9000,'total_vfs':54})
    assert assess_host(f,Settings())==[]

@pytest.mark.parametrize('change',[{'netns':'netns exclusive'},{'nri_socket':False},{'sriov_boot_units':{'x':'enabled'}}])
def test_host_preconditions(change):
    assert assess_host(facts(**change),Settings())


def test_active_vfs_and_missing_oca_ip_blocked():
    f=facts();f['interfaces'][0]['num_vfs']=2
    assert any('active VFs' in i for i in assess_host(f,Settings()))
    f=facts();f['interfaces'][0]['addresses']=[]
    assert any('OCA' in i for i in assess_host(f,Settings()))


def test_boot_evidence_invalidated(monkeypatch):
    p=LiveProvider(Settings());monkeypatch.setattr(p,'_host_cache',lambda:{'n1':{'at':time.time(),'facts':facts()}})
    assert not p._host_issues(node())
    n=node();n['status']['nodeInfo']['bootID']='boot2'
    assert 'rebooted' in p._host_issues(n)[0]


def test_host_readiness_reuses_existing_evidence(monkeypatch):
    p=LiveProvider(Settings());monkeypatch.setattr(p,'_selected',lambda:[node()]);monkeypatch.setattr(p,'_host_cache',lambda:{'n1':{'at':time.time(),'facts':facts(),'issues':[],'hardware_identity':{}}})
    monkeypatch.setattr(p,'node_diagnostic',lambda *a:pytest.fail('Repeated a current host experiment'))
    assert p.check_hosts()['hosts'][0]['reused']


def test_complete_dra_workload_boundaries():
    s=replace(Settings(),rdma_tools_image='repo@sha256:'+'a'*64,rdma_image_pull_secret='ocir')
    claim,pod=dranet.workload(s,'sample',node='worker-10',validation=True)
    assert claim['apiVersion']=='resource.k8s.io/v1'
    assert claim['spec']['devices']['requests'][0]['exactly']['deviceClassName']=='dra.net'
    assert 'nodeName' not in pod['spec']
    assert all(pod['spec'][x] is False for x in ['hostNetwork','hostPID','hostIPC','automountServiceAccountToken'])
    c=pod['spec']['containers'][0]
    assert not c['securityContext']['privileged'] and c['securityContext']['capabilities']=={'drop':['ALL'],'add':['IPC_LOCK']}
    assert c['resources']['claims']==[{'name':'rdma'}]
    assert pod['spec']['imagePullSecrets']==[{'name':'ocir'}]
    assert 'volumes' not in pod['spec']


def test_cleanup_only_owned_pods_then_claims(monkeypatch):
    p=LiveProvider(Settings());calls=[]
    monkeypatch.setattr(p,'kubectl',lambda args,**kw:calls.append(args))
    p.cleanup_validation()
    assert calls[0][3]=='pods' and calls[1][3]=='resourceclaims'
    assert all('app=oke-dranet-validation,app.kubernetes.io/part-of=oke-rdma-quickstart' in c for c in calls)


def test_report_path_and_redaction():
    text,truncated=sanitize_output('password=abc\nAuthorization: Bearer xyz\n'+'x'*1000,max_bytes=200)
    assert 'abc' not in text and 'xyz' not in text and truncated
    from app.diagnostics import report_path
    with pytest.raises(ValueError):report_path('../config','json')


def test_diagnostic_api_rejects_browser_shell():
    assert client.post('/api/v1/diagnostics/runs',headers=AUTH,json={'node_name':'n1','profile':'dranet-readiness','command':'cat /etc/shadow'}).status_code==422


def test_perftest_parser_accepts_actual_success_and_rejects_missing_measurement():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('validator',Path(__file__).parents[1]/'control/validation/test_rdma.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.parse_bandwidth(' 65536 5000 80.00 79.50 0.151634\n')['average_gbps']==79.50
    with pytest.raises(ValueError):m.parse_bandwidth('Waiting for client')


def test_settings_locked_only_for_driver_topology(monkeypatch):
    import app.main as main
    p=DemoProvider(load_settings())
    monkeypatch.setattr(p,'configuration_status',lambda:{'installed':True})
    monkeypatch.setattr(main,'get_provider',lambda s:p)
    body=load_settings().rdma_config()
    body['rdma_tools_image']='new@sha256:'+'b'*64
    assert client.put('/api/v1/rdma/config',headers=AUTH,json=body).status_code==200
    body['rdma_child_cidr']='10.192.0.0/12'
    assert client.put('/api/v1/rdma/config',headers=AUTH,json=body).status_code==409


def test_full_yaml_exports():
    import yaml
    for kind in ['values','workload']:
        r=client.get('/api/v1/exports/'+kind,headers=AUTH)
        assert r.status_code==200
        docs=list(yaml.safe_load_all(r.text))
        if kind=='workload':assert [d['kind'] for d in docs]==['Namespace','DeviceClass','ResourceClaim','Pod']
        else:assert docs[0]['args']['cloudProviderHint']=='OKE'


def test_progress_survives_listing_and_does_not_fake_completion():
    j=db.enqueue('rdma_validation','');db.claim_next()
    db.progress(j['id'],'RDMA-CM latency',3,5,'8 bytes / 5,000 writes')
    observed=db.list_jobs()[0]
    assert observed['state']=='RUNNING' and observed['progress']['completed']==3
    assert observed['progress']['stage']=='RDMA-CM latency'
    db.complete(j['id'],{'status':'failed','error':'measurement failed'})
    assert db.get_job(j['id'])['state']=='FAILED'
    with pytest.raises(ValueError):db.progress(j['id'],'invalid',6,5)


def test_latency_parser_requires_microseconds_and_actual_measurement():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('lat_validator',Path(__file__).parents[1]/'control/validation/test_rdma.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    header='#bytes #iterations t_min[usec] t_max[usec] t_typical[usec] t_avg[usec] t_stdev[usec] 99% percentile[usec] 99.9% percentile[usec]'
    actual=header+'\n8 5000 1.2 8.9 1.6 1.72 0.12 2.5 5.1\n'
    assert m.parse_latency(actual)['median_us']==1.6
    assert m.parse_latency(actual)['p999_us']==5.1
    for invalid in [actual.replace('[usec]','[nsec]'),actual.replace('1.72','0'),actual.replace('8 5000','64 5000'),'Waiting for client']:
        with pytest.raises(ValueError):m.parse_latency(invalid)
