"""Monitoring contracts: evidence freshness, serialization and bounded delivery."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import time
import pytest
from fastapi.testclient import TestClient
from app import db
from app.config import Settings
from app.main import app
from app.monitoring import Monitoring, MonitoringConfig, classify, metrics, schedule
from app.capacity import atomic
from app.provider_base import ProviderError

@pytest.fixture
def monitor(tmp_path):
    now=time.time()
    cfg=Settings(mode='live')
    nodes=[{'name':n,'mode':'dranet','ready':True,'boot_id':'boot','uid':n,'kernel':'6.8','issues':[], 'host_issues':[], 'devices':[{'valid':True,'allocated':False}]} for n in ['n1','n2']]
    facts={'boot_id':'boot','netns':'netns shared','interfaces':[{'name':cfg.rdma_pf_name,'mtu':4220,'driver':'mlx5_core'}]}
    cache={n['name']:{'at':now,'collection_status':'passed','facts':deepcopy(facts)} for n in nodes}
    cap=SimpleNamespace(root=tmp_path,guard=lambda action:None,pool=lambda:{},no_active_oke=lambda p:None,stack_id=lambda:'',no_active=lambda s:None)
    p=SimpleNamespace(s=cfg,capacity_service=lambda:cap,inventory=lambda:{'nodes':nodes,'claims':[],'capacity':{'free':2},'driver_ready':True},_host_cache=lambda:cache,_json=lambda args:{'items':[]},oci=lambda *args,**kw:SimpleNamespace(returncode=0))
    p.check_hosts=lambda **kw:None
    p.rdma_validation=lambda a,b,c:{'status':'passed','nodes':[a,b],'average_gbps':90}
    return Monitoring(p)

def config(**kw):return MonitoringConfig(**kw).model_dump(exclude={'confirmation'})

def test_opt_in_and_manual_snapshot(monitor):
    schedule(monitor.p)
    assert db.list_jobs()==[]
    result=monitor.sweep()
    assert result['status']=='passed'
    assert [n['state'] for n in result['monitoring']['nodes']]==['healthy','healthy']
    assert result['monitoring']['nodes'][0]['baseline_status']=='not_approved'

@pytest.mark.parametrize('change,state', [('missing','unknown'),('failed','unknown'),('old','stale'),('reboot','stale'),('unready','unhealthy'),('bad','unhealthy'),('cordon','maintenance')])
def test_evidence_states(monitor,change,state):
    n=deepcopy(monitor.p.inventory()['nodes'][0]);e=deepcopy(monitor.p._host_cache()['n1']);now=time.time()
    if change=='missing':e={}
    if change=='failed':e['collection_status']='failed'
    if change=='old':e['at']=now-8000
    if change=='reboot':n['boot_id']='new'
    if change=='unready':n['ready']=False
    if change=='bad':n['issues']=['MTU wrong']
    if change=='cordon':n['cordoned']=True
    assert classify(n,e,config(),now)['state']==state

def test_baseline_explicit_and_partial(monitor):
    baseline=monitor.approve_baseline('n1')['baseline']
    assert baseline['profile']['firmware'] is None
    monitor.p.inventory()['nodes'][1]['kernel']='6.9'
    result=monitor.sweep()['monitoring']
    assert result['nodes'][0]['baseline_status']=='matches'
    assert result['nodes'][1]['drift']==['kernel']
    monitor.p._host_cache()['n1']['at']=1
    with pytest.raises(ValueError,match='fresh'):monitor.approve_baseline('n1')

def test_fresh_host_check_not_mistaken_for_future_evidence(monitor):
    atomic(monitor.config_path,config(refresh_idle_hosts=True))
    calls=[]
    def check(**kw):
        calls.append(kw)
        monitor.p._host_cache()[kw['node_names'][0]]['at']=time.time()
    monitor.p.check_hosts=check
    result=monitor.sweep()
    assert calls==[{'node_names':['n1'],'force':True}]
    assert all(n['state']=='healthy' for n in result['monitoring']['nodes'])

def test_claims_skip_active_checks_and_are_review_not_orphan(monitor):
    inv=monitor.p.inventory();inv['claims']=[{'namespace':'work','name':'claim','created_at':'2020-01-01T00:00:00Z','consumers':[]}]
    monitor.p.inventory=lambda:inv
    atomic(monitor.config_path,config(refresh_idle_hosts=True))
    monitor.p.check_hosts=lambda **kw:pytest.fail('Claims must block active checks')
    monitor.sweep();result=monitor.sweep()
    assert result['monitoring']['alerts'][0]['condition']=='long_held_claim_review'
    atomic(monitor.config_path,config(enabled=True,nightly_pair=True))
    assert monitor.benchmark()['status']=='skipped'

def test_collection_failure_not_hardware_failure(monitor):
    monitor.sweep()
    def fail():raise ProviderError('failed inventory')
    monitor.p.inventory=fail
    monitor.sweep();result=monitor.sweep()['monitoring']
    assert result['collection']=='failed'
    assert all(n['state']=='unknown' for n in result['nodes'])
    assert result['alerts'][0]['condition']=='collection_failed'
    assert result['last_success_at']<result['observed_at']

def test_alert_debounce_recovery_and_bounded_retry(monitor,monkeypatch):
    clock=[1000];monkeypatch.setattr('app.monitoring.time.time',lambda:clock[0])
    state={};monitor.alerts(state,{'worker:n1':'unhealthy'});assert state['alerts']==[]
    monitor.alerts(state,{'worker:n1':'unhealthy'});assert len(state['alerts'])==1
    monitor.alerts(state,{'worker:n1':'unhealthy'});assert len(state['alerts'])==1
    calls=[]
    def publish(*a,**kw):calls.append((a,kw));return SimpleNamespace(returncode=1)
    monitor.p.oci=publish
    cfg=config(notification_topic='ocid1.onstopic.oc1.fra.example')
    for _ in range(5):monitor.deliver(state,cfg);clock[0]+=4000
    assert len(calls)==3 and state['alerts'][0]['delivery']=='failed'
    assert calls[0][1]['timeout']==15 and '--no-retry' in calls[0][0][0]
    monitor.alerts(state,{'worker:n1':None});monitor.alerts(state,{'worker:n1':None})
    assert state['alerts'][-1]['condition']=='resolved'

def test_crashed_last_delivery_does_not_make_fourth_attempt(monitor):
    state={'alerts':[{'id':'id','delivery':'pending','attempts':3,'next_attempt':0}]}
    monitor.p.oci=lambda *a,**kw:pytest.fail('attempt budget exhausted')
    monitor.deliver(state,config(notification_topic='ocid1.onstopic.oc1.fra.example'))
    assert state['alerts'][0]['delivery']=='failed'

def test_scheduler_persistent_daily_limit_and_busy(monitor,monkeypatch):
    atomic(monitor.config_path,config(enabled=True,nightly_pair=True))
    now=1792548000.0
    schedule(monitor.p,now)
    jobs=db.list_jobs();assert len(jobs)==1 and jobs[0]['action']=='monitoring_benchmark'
    schedule(monitor.p,now+1);assert len(db.list_jobs())==1
    db.complete(jobs[0]['id'],{'status':'skipped'})
    schedule(monitor.p,now+2);assert db.list_jobs()[0]['action']=='monitoring_sweep'
    db.complete(db.list_jobs()[0]['id'],{'status':'passed'})
    # Simulate loss of older retained queue history; durable schedule still controls it.
    monkeypatch.setattr(db,'list_jobs',lambda limit=100:[])
    seen=[];monkeypatch.setattr(db,'enqueue',lambda *a:seen.append(a))
    schedule(monitor.p,now+3);assert seen==[]

def test_pending_operation_blocks_scheduler_and_manual(monitor):
    def fail(action):raise ProviderError('capacity pending')
    monitor.p.capacity_service().guard=fail
    atomic(monitor.config_path,config(enabled=True))
    schedule(monitor.p)
    assert db.list_jobs()==[]
    assert monitor.status()['scheduler']['state']=='blocked'
    with pytest.raises(ProviderError):monitor.sweep()

def test_maintenance_and_config_confirmation(monitor):
    with pytest.raises(ValueError):monitor.save_config(MonitoringConfig(enabled=True))
    monitor.save_config(MonitoringConfig(enabled=True,maintenance_until=time.time()+3600,confirmation=monitor.p.s.environment_name))
    schedule(monitor.p);assert db.list_jobs()==[]
    assert monitor.sweep()['status']=='skipped'
    job=db.enqueue('preflight','')
    with pytest.raises(Exception):monitor.save_config(MonitoringConfig(confirmation=monitor.p.s.environment_name))
    db.complete(job['id'],{})

def test_metrics_cached_readonly_and_auth(monitor,monkeypatch,tmp_path):
    monitor.sweep()
    monitor.p.inventory=lambda:pytest.fail('Scrape must not collect inventory')
    assert 'oke_rdma_free_slots 2' in metrics(monitor.p)
    state=monitor.status()['snapshot'];state['observed_at']=1;atomic(monitor.state_path,state)
    assert 'state="stale"} 1' in metrics(monitor.p)
    path=tmp_path/'metrics.token';path.write_text('scraper')
    monkeypatch.setenv('HPC_CONTROL_METRICS_TOKEN_FILE',str(path))
    client=TestClient(app)
    assert client.get('/metrics').status_code==401
    assert client.get('/metrics',headers={'Authorization':'Bearer scraper'}).status_code==200
    assert client.get('/api/v1/environment',headers={'Authorization':'Bearer scraper'}).status_code==401
    assert client.get('/api/v1/monitoring',headers={'Authorization':'Bearer demo'}).status_code==200

def test_pair_failure_does_not_hide_behind_old_measurement(monitor):
    j=db.enqueue('rdma_validation','',{'server_node':'n1','client_node':'n2'})
    db.complete(j['id'],{'status':'passed','nodes':['n1','n2'],'average_gbps':90,'measured_at':1})
    j=db.enqueue('rdma_validation','',{'server_node':'n1','client_node':'n2'})
    db.fail(j['id'],'image pull failed')
    out=metrics(monitor.p)
    assert 'oke_rdma_pair_latest_attempt_success{server="n1",client="n2"} 0' in out
    assert 'oke_rdma_pair_measurement_timestamp_seconds{server="n1",client="n2"} 1' in out

def test_monitoring_actions_require_confirmation():
    from app.jobs import validate_request
    for action in ('monitoring_baseline','monitoring_sweep'):
        with pytest.raises(ValueError):validate_request(Settings(),action,'')


def test_notification_uses_topic_endpoint(monitor):
    import json
    topic='ocid1.onstopic.oc1.fra.example'
    endpoint='https://cell-1.notification.eu-frankfurt-1.oraclecloud.com'
    calls=[]
    def oci(args,**kw):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'data':{'topic-id':topic,'api-endpoint':endpoint}}))
    monitor.p.oci=oci
    state={};monitor.alerts(state,{'worker:n1':'unhealthy'});monitor.alerts(state,{'worker:n1':'unhealthy'})
    monitor.deliver(state,config(notification_topic=topic))
    assert calls[0][1:4]==['ons','topic','get']
    assert calls[1][calls[1].index('--endpoint')+1]==endpoint
    assert state['alerts'][0]['delivery']=='sent'


def test_notification_rejects_wrong_topic_response(monitor):
    import json
    calls=[]
    def oci(args,**kw):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'data':{'topic-id':'wrong','api-endpoint':'https://example.com'}}))
    monitor.p.oci=oci
    state={};monitor.alerts(state,{'worker:n1':'unhealthy'});monitor.alerts(state,{'worker:n1':'unhealthy'})
    monitor.deliver(state,config(notification_topic='ocid1.onstopic.oc1.fra.example'))
    assert len(calls)==1 and state['alerts'][0]['delivery']=='pending'
