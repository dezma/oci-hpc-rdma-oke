"""Run the actual validator against a local fake kubectl process pair."""
from pathlib import Path
import json
import os
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if os.environ.get('KUBECTL_KUBERC') != 'false':
    raise SystemExit('kuberc was not disabled')
marker = pathlib.Path(os.environ['PEER_MARKER'])
mode = os.environ['TEST_MODE']
if 'get' in args:
    role = 'server' if 'oke-dranet-server' in args else 'client'
    print(json.dumps({'spec': {'nodeName': 'worker-' + role,
                            'containers': [{'securityContext': {'privileged': False}}]}}))
elif 'address' in args:
    ip = '10.208.0.10' if 'oke-dranet-server' in args else '10.208.0.11'
    print(json.dumps([{'addr_info': [{'family': 'inet', 'local': ip}]}]))
elif 'ib_write_bw' in args or 'ib_write_lat' in args:
    latency = 'ib_write_lat' in args
    perf = args[args.index('ib_write_lat' if latency else 'ib_write_bw')+1:]
    for flag, value in [('-s','8' if latency else '65536'),('-n','5000'),('-T','41'),('-p','18516' if latency else '18515')]:
        assert perf[perf.index(flag)+1] == value
    if latency: assert '-q' not in perf and '--report_gbits' not in perf
    assert '-R' in args and '--bind_source_ip' in args
    if 'oke-dranet-server' in args:
        if mode == 'server_error':
            print('Cannot open RDMA device', flush=True)
            raise SystemExit(3)
        print('* Waiting for client to connect... *', flush=True)
        deadline = time.monotonic() + 4
        while not marker.exists():
            if time.monotonic() > deadline:
                raise SystemExit('client was never started')
            time.sleep(0.01)
        print('Waiting for client rdma_cm QP to connect', flush=True)
        if mode != 'missing_measurement':
            print('65536 5000 80.00 79.50 0.151634', flush=True)
    else:
        assert args[-1] == '10.208.0.10'
        marker.write_text('client started')
        if mode == 'client_error':
            print('RDMA client connection failed', file=sys.stderr)
            raise SystemExit(2)
        if mode != 'missing_measurement':
            if latency:
                print('#bytes #iterations t_min[usec] t_max[usec] t_typical[usec] t_avg[usec] t_stdev[usec] 99% percentile[usec] 99.9% percentile[usec]')
                print('8 5000 1.20 8.90 1.60 1.72 0.12 2.50 5.10')
            else:
                print('65536 5000 80.00 79.50 0.151634')
else:
    raise SystemExit('Unexpected fake kubectl command')
'''


@pytest.mark.parametrize('test', ['bandwidth', 'latency'])
@pytest.mark.parametrize('mode', ['success', 'server_error', 'client_error', 'missing_measurement'])
def test_validation_startup_and_failure_evidence(tmp_path, mode, test):
    fake = tmp_path/'kubectl'
    fake.write_text(FAKE_KUBECTL)
    fake.chmod(0o755)
    marker = tmp_path/'peer.started'
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
               PEER_MARKER=str(marker), TEST_MODE=mode, KUBECTL_KUBERC='true')
    result = subprocess.run(
        [sys.executable, str(ROOT/'control/validation/test_rdma.py'),
         '--namespace', 'rdma-workloads', '--interface', 'ens800f0np0',
         '--child-cidr', '10.208.0.0/12', '--test', test],
        env=env, text=True, capture_output=True, timeout=8,
    )
    report = json.loads(result.stdout)
    if mode == 'success':
        assert result.returncode == 0, report
        assert marker.exists()
        if test == 'bandwidth': assert report['average_gbps'] == 79.5
        else:
            assert report['average_us'] == 1.72 and report['p99_us'] == 2.5
            assert report['message_bytes'] == 8
        assert report['nodes'] == ['worker-server', 'worker-client']
    else:
        assert result.returncode != 0
        assert report['status'] == 'failed'
        assert 'average_gbps' not in report
        if mode == 'server_error':
            assert not marker.exists()
            assert 'Cannot open RDMA device' in report['server_output']
        elif mode == 'client_error':
            assert 'RDMA client connection failed' in report['client_output']
        else:
            assert ('expected bandwidth result row' if test == 'bandwidth' else 'microsecond header') in report['error']
