from app.hardware_identity import identity, compare, record


def test_identity_rejects_stale_boot_and_placeholder_identifiers():
    node = {'metadata': {'name': 'worker', 'uid': 'uid'}, 'status': {'nodeInfo': {'bootID': 'boot'}}}
    assert identity(node, {'boot_id': 'old'}, 'instance', 'pf') == {}
    result = identity(node, {'boot_id': 'boot', 'system_serial': 'To Be Filled By O.E.M.', 'interfaces': [{'name': 'pf', 'mac': '00:00:00:00:00:00'}]}, 'instance', 'pf')
    assert result['system_serial'] == result['pf_mac'] == ''
    assert compare({}, result)['status'] == 'unknown'


def test_reuse_evidence_survives_instance_change(tmp_path):
    path = tmp_path / 'history.json'
    before = {'instance_id': 'old', 'boot_id': 'a', 'system_serial': '2119XCL051', 'pf_mac': 'b8:ce:f6:00:72:8c'}
    record(path, before)
    current = record(path, {**before, 'instance_id': 'new', 'boot_id': 'b'})
    assert current['reuse_matches'][0]['status'] == 'serial_match'
    assert current['reuse_matches'][0]['previous']['instance_id'] == 'old'
    assert compare(before, {**current, 'system_serial': ''})['status'] == 'mac_match'
    assert compare(before, before)['status'] == 'unknown'


def test_missing_identifiers_never_match(tmp_path):
    before = {'instance_id': 'old', 'boot_id': 'a', 'system_serial': '', 'pf_mac': ''}
    path = tmp_path / 'history.json'
    record(path, before)
    assert not record(path, {**before, 'instance_id': 'new'})['reuse_matches']
