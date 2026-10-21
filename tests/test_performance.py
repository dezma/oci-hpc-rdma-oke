from copy import deepcopy
from app.config import Settings
from app.measurements import context
from app.performance import dashboard
import importlib.util
from pathlib import Path


def sample():
    settings=Settings()
    nodes=[{'name':n,'mode':'dranet','uid':n,'boot_id':'boot'} for n in ['a','b']]
    env={'nodes':nodes}
    result={'nodes':['a','b'],'status':'passed','measured_at':100,'average_gbps':80,
            'latency':{'average_us':2,'p99_us':3},'context':context(nodes,settings),
            'provenance':{'comparison_key':'same','workers':deepcopy(nodes)}}
    job={'id':'new','action':'rdma_validation','state':'SUCCEEDED','requested_at':100,'result':result}
    return settings,env,job


def test_directional_missing_reverse_and_comparable_baseline():
    settings,env,job=sample()
    old=deepcopy(job);old.update(id='old',requested_at=50)
    old['result']['average_gbps']=100;old['result']['latency']['average_us']=1
    rows=dashboard([old,job],env,settings,now=101)['pairs']
    assert rows[0]['state']=='measured'
    assert round(rows[0]['baseline']['bandwidth_change_percent'])==-20
    assert rows[0]['baseline']['latency_change_percent']==100
    assert rows[1]['state']=='not_measured'
    old['result']['provenance']['comparison_key']='different'
    assert 'baseline' not in dashboard([old,job],env,settings,now=101)['pairs'][0]


def test_failed_attempt_masks_previous_success_without_result():
    settings,env,job=sample()
    failed={'id':'failed','action':'rdma_validation','state':'FAILED','requested_at':102,
            'result':None,'payload':{'server_node':'a','client_node':'b'}}
    row=dashboard([job,failed],env,settings,now=103)['pairs'][0]
    assert row['state']=='failed' and 'average_gbps' not in row


def test_reboot_and_age_mark_history():
    settings,env,job=sample()
    env['nodes'][0]['boot_id']='reboot'
    assert dashboard([job],env,settings,now=101)['pairs'][0]['state']=='historical'
    env['nodes'][0]['boot_id']='boot'
    assert dashboard([job],env,settings,now=100000)['pairs'][0]['state']=='historical'


def test_loaded_latency_requires_positive_traffic_and_enclosing_interval():
    path=Path(__file__).resolve().parents[1]/'control/validation/performance_suite.py'
    spec=importlib.util.spec_from_file_location('suite',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    load={'status':'passed','average_gbps':80,'started_at':1,'finished_at':30}
    lat={'status':'passed','started_at':5,'finished_at':7}
    assert mod.verify_overlap(load,lat)
    lat['finished_at']=31
    assert not mod.verify_overlap(load,lat)
    lat['finished_at']=7;load['average_gbps']=0
    assert not mod.verify_overlap(load,lat)


def test_matrix_successes_survive_partial_failure():
    settings,env,job=sample()
    job['result']={'status':'failed','pair_results':[job['result'],{'nodes':['b','a'],'status':'failed'}]}
    rows=dashboard([job],env,settings,now=101)['pairs']
    assert rows[0]['state']=='measured' and rows[1]['state']=='failed'


def test_startup_timestamps_do_not_invent_prepare_time():
    from app.startup_timing import summarize
    pod={'metadata':{'name':'test','creationTimestamp':'2026-01-01T00:00:00Z'},'status':{'conditions':[{'type':'PodScheduled','status':'True','lastTransitionTime':'2026-01-01T00:00:02Z'}],'containerStatuses':[{'state':{'running':{'startedAt':'2026-01-01T00:00:07Z'}}}]}}
    result=summarize(pod,{'metadata':{'creationTimestamp':'2026-01-01T00:00:00Z'}})
    assert result['seconds']['scheduling']==2 and result['seconds']['claim_to_container_start']==7
    assert result['seconds']['sandbox_to_container_start'] is None
    assert result['seconds']['claim_to_allocation_observed'] is None


def test_capacity_validation_timing_matches_boot_and_every_new_worker():
    from app.performance import capacity_timing
    settings,env,job=sample()
    op={'milestones':{'apply_submitted_at':10},'new_node_identities':[{'name':'a','uid':'a','boot_id':'boot'}]}
    assert capacity_timing(op,[job])['time_until_verified_rdma_seconds']==90
    op['new_node_identities'][0]['boot_id']='newboot'
    assert capacity_timing(op,[job])['time_until_verified_rdma_seconds'] is None


def test_mpi_rejects_incomplete_or_nonfinite_collective_evidence():
    import pytest
    path=Path(__file__).resolve().parents[1]/'control/validation/mpi_suite.py'
    spec=importlib.util.spec_from_file_location('mpi_suite',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    rows=[{'operation':op,'bytes_per_peer_or_rank':size,'mean_us':1,'p99_us':2,'max_us':3} for op in ['Allreduce','Alltoall'] for size in [8,1024,65536]]+[{'operation':'Barrier','bytes_per_peer_or_rank':0,'mean_us':1,'p99_us':2,'max_us':3}]
    result={'status':'passed','ranks':2,'workers':['a','b'],'samples':rows}
    mod.validate_result(result,2)
    rows[0]['mean_us']=float('nan')
    with pytest.raises(RuntimeError):mod.validate_result(result,2)


def test_startup_poll_observes_allocation_and_ready(monkeypatch):
    from app.provider import LiveProvider
    p=LiveProvider(Settings())
    def read(args):
        if 'resourceclaims' in args:return {'items':[{'metadata':{'name':'pod','creationTimestamp':'2026-01-01T00:00:00Z'},'status':{'allocation':{'devices':{}}}}]}
        return {'items':[{'metadata':{'name':'pod'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
    monkeypatch.setattr(p,'_json',read)
    result=p.validation_startup(['pod'])
    assert result['status']=='passed' and result['pods'][0]['stages']['allocation_first_observed']>0
