import json
import subprocess
from types import SimpleNamespace
import pytest
from app.capacity_report import collect
from app.provider_base import ProviderError
from test_capacity import cap


def test_report_block_stops_before_stack_variables_change(cap):
    cap.capacity_report = lambda *a: {'status':'blocked','rows':[], 'error':'OUT_OF_HOST_CAPACITY'}
    with pytest.raises(ProviderError, match='OUT_OF_HOST_CAPACITY'):
        cap.plan(5)
    assert not cap.gateway.writes
    assert cap.operation() is None


def test_apply_refresh_failure_keeps_reviewed_plan_without_apply(cap):
    op = cap.plan(5)['capacity']
    cap.capacity_report = lambda *a: {'status':'unavailable','rows':[], 'error':'report unavailable'}
    with pytest.raises(ProviderError, match='report unavailable'):
        cap.apply(op['operation_id'])
    assert cap.operation()['phase'] == 'AWAITING_REVIEW'
    assert not any(w[0] == 'APPLY' for w in cap.gateway.writes)


@pytest.mark.parametrize('status,count,expected', [('AVAILABLE',None,'available'), ('AVAILABLE',0,'blocked'), ('AVAILABLE',1,'blocked'), ('AVAILABLE',2,'available'), ('OUT_OF_HOST_CAPACITY',None,'blocked'), ('HARDWARE_NOT_SUPPORTED',None,'blocked'), ('UNKNOWN_ENUM_VALUE',None,'unavailable')])
def test_status_and_optional_count(status,count,expected):
    pool={'compartment_id':'project-compartment','node_shape':'BM.Optimized3.36','node_config_details':{'size':1,'placement_configs':[{'availability_domain':'AD1'}]}}
    def oci(args, **kwargs):
        assert args[args.index('--compartment-id')+1] == 'project-compartment'
        return subprocess.CompletedProcess(args,0,json.dumps({'data':{'availability-domain':'AD1','shape-availabilities':[{'instance-shape':'BM.Optimized3.36','availability-status':status,'available-count':count}]}}),'')
    p=SimpleNamespace(s=SimpleNamespace(region='region',tenancy_id='tenancy'),oci=oci)
    report=collect(p,pool,3)
    assert report['status']==expected
    assert report['rows'][0]['available_count']==count
    p.oci=lambda *a,**k:pytest.fail('Scale-in must not query capacity')
    assert collect(p,pool,0)['status']=='not_required'


def test_ad_fault_domain_rows_are_not_summed():
    pool={'compartment_id':'project-compartment','node_shape':'BM.X','node_config_details':{'size':0,'placement_configs':[{'availability_domain':'AD1'}]}}
    data={'data':{'availability-domain':'AD1','shape-availabilities':[{'instance-shape':'BM.X','fault-domain':fd,'availability-status':'AVAILABLE','available-count':1} for fd in ['FD1','FD2']]}}
    p=SimpleNamespace(s=SimpleNamespace(region='region',tenancy_id='tenancy'),oci=lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps(data),''))
    report=collect(p,pool,3)
    assert len(report['rows'])==2 and 'available_count' not in report
    assert report['status']=='available'  # availability only; placement/count sufficiency unproven
    p.oci=lambda *a,**k:subprocess.CompletedProcess([],1,'','Forbidden')
    assert collect(p,pool,3)['status']=='unavailable'


def test_missing_pool_compartment_does_not_fall_back_to_tenancy():
    p=SimpleNamespace(s=SimpleNamespace(region='r',tenancy_id='tenancy'),oci=lambda *a,**k:pytest.fail('No tenancy fallback'))
    pool={'node_shape':'BM.X','node_config_details':{'size':0,'placement_configs':[{'availability_domain':'AD1'}]}}
    assert collect(p,pool,1)['status']=='unavailable'

def test_request_failure_keeps_sanitized_reason():
    pool={'compartment_id':'project','node_shape':'BM.X','node_config_details':{'size':0,'placement_configs':[{'availability_domain':'AD1'}]}}
    p=SimpleNamespace(s=SimpleNamespace(region='r'),oci=lambda *a,**k:subprocess.CompletedProcess([],1,'','NotAuthorizedOrNotFound password=secret-value'))
    report=collect(p,pool,1)
    row=report['rows'][0]
    assert row['availability_status']=='REPORT_UNAVAILABLE'
    assert 'NotAuthorizedOrNotFound' in row['error']
    assert 'secret-value' not in json.dumps(report)
