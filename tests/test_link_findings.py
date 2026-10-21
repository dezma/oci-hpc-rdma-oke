import json
from app.link_findings import findings, physical_link_blocked
from app.provider import LiveProvider, assess_host
from app.config import Settings
from app.diagnostics import profile

PF = 'ens800f0np0'
def inventory(carrier='0', output='Link detected: no (No cable)', status='collected'):
    return {'netns':'netns shared', 'nri_socket':True, 'interfaces':[
        {'name':PF, 'pci':'0000:98:00.0', 'driver':'mlx5_core', 'carrier':carrier,
         'mtu':1500, 'addresses':['10.224.0.89/12'], 'flags':['UP'],
         'link_evidence':{'status':status,'output':output,'detail':'query failed'}}]}

def test_observed_worker_reports_physical_and_mtu_separately():
    fs=findings(inventory(),PF)
    assert [f['code'] for f in fs] == ['physical_link_unavailable','mtu_mismatch']
    assert '0000:98:00.0' in fs[0]['evidence']
    assert physical_link_blocked(inventory(),PF)
    assert any('No cable' in x for x in assess_host(inventory(),Settings()))

def test_unused_port_and_stale_no_cable_do_not_block_recovery():
    facts=inventory('1');facts['interfaces'][0]['mtu']=4220
    facts['interfaces'].append({'name':'ens800f1np1','carrier':'0','link_evidence':{'status':'collected','output':'Link detected: no (No cable)'}})
    assert not findings(facts,PF)
    assert not physical_link_blocked(facts,PF)

def test_missing_tool_is_inconclusive_not_physical_fault():
    fs=findings(inventory(status='unavailable'),PF)
    assert 'link_evidence_unavailable' in {f['code'] for f in fs}
    assert not physical_link_blocked(inventory(status='unavailable'),PF)

def test_auth_completed_does_not_override_waiting_with_no_carrier():
    output='DRANET_FACTS='+json.dumps(inventory())+'\nInterface ens800f0np0 is not ready\nState = COMPLETED'
    fs=LiveProvider(Settings())._diagnostic_findings('rdma-authentication',output)
    assert {'physical_link_unavailable','mtu_mismatch','authentication_waiting'} <= {f.get('code') for f in fs}

def test_other_interface_auth_does_not_contaminate_selected_pf():
    output='DRANET_FACTS='+json.dumps(inventory())+'\nInterface ens800f1np1 is not ready'
    assert not any(f.get('code')=='authentication_waiting' for f in LiveProvider(Settings())._diagnostic_findings('rdma-authentication',output))

def test_all_relevant_profiles_collect_structured_evidence():
    for name in ['dranet-readiness','rdma-link-health','rdma-authentication','full-rdma-support-bundle']:
        assert 'DRANET_FACTS=' in profile(name).script
        assert "optional('ethtool', name)" in profile(name).script
