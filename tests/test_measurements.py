from copy import deepcopy
from dataclasses import replace
import io
import json
import zipfile
import pytest
from app.config import Settings
from app.measurements import context, summarize
from app.support import build_bundle
from test_control import client, AUTH

@pytest.fixture
def evidence():
    settings=Settings()
    env={'nodes':[{'name':'worker-a','mode':'dranet','uid':'uid-a','boot_id':'boot-a'}]}
    job={'id':'measured','action':'rdma_validation','state':'SUCCEEDED','requested_at':100,
         'result':{'status':'passed','average_gbps':97.99,'measured_at':200,'nodes':['worker-a','worker-b'],
                   'latency':{'status':'passed','average_us':1.8,'p99_us':2.5},'context':context(env['nodes'],settings)}}
    return settings,env,job


def test_current_evidence_and_pair_scope(evidence):
    s,e,j=evidence
    r=summarize([j],e,s,now=300)
    assert r['validation_current'] and r['measurement']['latency']['average_us']==1.8
    assert 'Pair measurement only' in r['measurement']['scope']

@pytest.mark.parametrize('change',['added','removed','reboot','replace','configuration','old','future','legacy','missing_identity','failed_latency','partial'])
def test_stale_or_incomplete_measurements_never_claim_current(evidence,change):
    s,e,j=evidence
    if change=='added':e['nodes'].append({'name':'new','mode':'dranet','uid':'u','boot_id':'b'})
    if change=='removed':e['nodes']=[]
    if change=='reboot':e['nodes'][0]['boot_id']='newboot'
    if change=='replace':e['nodes'][0]['uid']='newuid'
    if change=='configuration':s=replace(s,rdma_tos=42)
    if change=='old':j['result']['measured_at']=1
    if change=='future':j['result']['measured_at']=999999
    if change=='legacy':j['result'].pop('context')
    if change=='missing_identity':j['result']['context']['fleet'][0]['uid']=''
    if change=='failed_latency':j['result']['latency']['status']='failed'
    if change=='partial':j['state']='FAILED'
    r=summarize([j],e,s,now=90000 if change=='old' else 300)
    assert not r['validation_current'] and r['measurement']['reasons']
    assert r['measurement']['average_gbps']==97.99
    if change=='failed_latency':assert r['measurement']['latency'] is None


def test_old_latency_is_never_combined_with_new_bandwidth(evidence):
    s,e,j=evidence
    newer=deepcopy(j);newer['id']='bandwidth-only';newer['requested_at']=250;newer['result'].pop('latency')
    r=summarize([j,newer],e,s,now=300)
    assert r['measurement']['job_id']=='bandwidth-only'
    assert r['measurement']['latency'] is None and not r['validation_current']


def test_later_failure_retains_metrics_as_historical_and_reset_clears_summary(evidence):
    s,e,j=evidence
    failure={'id':'later','action':'rdma_validation','state':'FAILED','requested_at':250,'error':'old failure'}
    r=summarize([failure,j],e,s,now=300)
    assert r['latest_attempt']['id']=='later' and r['measurement']['job_id']=='measured'
    assert not r['validation_current']
    e['reset_at']=275
    assert summarize([failure,j],e,s,now=300)['measurement'] is None


def test_support_export_allowlists_and_authentication(evidence):
    s,e,j=evidence
    secret='SENSITIVE-CONTENT-MUST-NOT-EXPORT'
    e['token']=secret;e['nodes'][0]['host_issues']=[secret]
    j.update(payload={'settings':{'token':secret}},error=secret)
    j['result']['raw_logs']=secret
    with zipfile.ZipFile(io.BytesIO(build_bundle(e,[j],s))) as archive:
        assert 'measurement.json' in archive.namelist()
        assert all(secret not in archive.read(n).decode() for n in archive.namelist())
        assert json.loads(archive.read('workers.json'))[0]['host_issue_count']==1
    assert client.get('/api/v1/exports/support').status_code==401
    response=client.get('/api/v1/exports/support',headers=AUTH)
    assert response.status_code==200
    assert zipfile.is_zipfile(io.BytesIO(response.content))
