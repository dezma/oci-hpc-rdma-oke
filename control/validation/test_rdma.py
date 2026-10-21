#!/usr/bin/env python3
"""Bounded cross-node RDMA bandwidth or latency measurement."""
from __future__ import annotations
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time


def parse_bandwidth(output, message_bytes=65536, duration=False):
    rows = re.findall(rf'^\s*{message_bytes}\s+' + (r'\d+' if duration else '5000') + r'\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*$', output, re.M)
    if not rows: raise ValueError('perftest completed without the expected bandwidth result row')
    peak, average, rate = map(float, rows[-1])
    if average <= 0: raise ValueError('perftest reported no RDMA throughput')
    return {'peak_gbps': peak, 'average_gbps': average, 'message_rate_mpps': rate}


def parse_latency(output, message_bytes=8):
    # Require the documented microsecond header before interpreting numeric rows.
    header = re.search(r'^.*t_min\[usec\].*t_max\[usec\].*t_typical\[usec\].*t_avg\[usec\].*t_stdev\[usec\].*$', output, re.M)
    if not header:
        raise ValueError('perftest latency result has no supported microsecond header')
    rows = re.findall(rf'^\s*{message_bytes}\s+5000\s+((?:[0-9.]+\s+){{4,6}}[0-9.]+)\s*$', output[header.end():], re.M)
    if not rows:
        raise ValueError('perftest completed without the expected latency result row')
    values = list(map(float, rows[-1].split()))
    keys = ['min_us', 'max_us', 'median_us', 'average_us', 'stdev_us']
    has_percentiles = '99%' in header.group() and '99.9%' in header.group()
    if has_percentiles: keys += ['p99_us', 'p999_us']
    if len(values) != len(keys) or not all(math.isfinite(v) and v >= 0 for v in values) or values[3] <= 0:
        raise ValueError('perftest reported invalid latency measurements')
    return dict(zip(keys, values))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--placement', choices=['default','near','far'], default='default')
    ap.add_argument('--test', choices=['bandwidth', 'latency'], default='bandwidth')
    ap.add_argument('--message-bytes', type=int, choices=[8,64,512,4096,65536,1048576])
    ap.add_argument('--duration', type=int, choices=[30])
    ap.add_argument('--port', type=int, choices=[18515,18516,18517])
    ap.add_argument('--started-marker', default='')
    ap.add_argument('--namespace', required=True)
    ap.add_argument('--interface', required=True)
    ap.add_argument('--child-cidr', required=True)
    ap.add_argument('--tos', type=int, default=41)
    ap.add_argument('--server-device', default='mlx5_2')
    ap.add_argument('--client-device', default='mlx5_2')
    args = ap.parse_args()
    size = args.message_bytes or (65536 if args.test == 'bandwidth' else 8)
    if args.duration and args.test != 'bandwidth': raise ValueError('Duration applies only to bandwidth')
    # Automated kubectl must not read interactive preferences from a protected home.
    child_env = {**os.environ, 'KUBECTL_KUBERC': 'false'}
    prefix = ['kubectl', '--kubeconfig', os.environ.get('KUBECONFIG', '/etc/oke-rdma-control/kube/config'), '-n', args.namespace]
    def run(argv, timeout=20):
        r = subprocess.run(prefix + argv, text=True, capture_output=True, timeout=timeout, env=child_env)
        if r.returncode: raise RuntimeError((r.stderr + r.stdout)[-2500:])
        return r.stdout
    child_range = ipaddress.ip_network(args.child_cidr)
    addresses = {}
    nodes = []
    for role in ['server', 'client']:
        pod = 'oke-dranet-' + role
        doc = json.loads(run(['get', 'pod', pod, '-o', 'json']))
        spec = doc['spec']; nodes.append(spec['nodeName'])
        if spec.get('hostNetwork') or spec.get('hostPID') or spec.get('hostIPC'):
            raise RuntimeError('Validation workload has unexpected host namespace access')
        if any(c.get('securityContext', {}).get('privileged') for c in spec['containers']):
            raise RuntimeError('Validation workload unexpectedly privileged')
        links = json.loads(run(['exec', pod, '--', 'ip', '-j', '-4', 'address', 'show', 'dev', args.interface]))
        ips = [a['local'] for x in links for a in x.get('addr_info', []) if a['family'] == 'inet' and ipaddress.ip_address(a['local']) in child_range]
        if len(ips) != 1: raise RuntimeError(f'{pod}: expected one IPvlan child address in {child_range}')
        addresses[role] = ips[0]
    if nodes[0] == nodes[1]: raise RuntimeError('Validation endpoints are on the same node')
    placements = {}
    if args.placement != 'default':
        topology_code = """import os,json,pathlib
allowed=set(os.sched_getaffinity(0))
def expand(s):
 out=set()
 for part in s.split(','):
  a,_,b=part.partition('-');out.update(range(int(a),int(b or a)+1))
 return out
status=pathlib.Path('/proc/self/status').read_text()
mem=expand(next(x.split(':',1)[1].strip() for x in status.splitlines() if x.startswith('Mems_allowed_list:')))
import sys
near=int(pathlib.Path('/sys/class/infiniband/'+sys.argv[1]+'/device/numa_node').read_text())
choices=[]
for p in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*'):
 n=int(p.name[4:]);cpus=allowed & expand((p/'cpulist').read_text().strip())
 if n in mem and cpus:choices.append({'numa_node':n,'cpu':min(cpus)})
print(json.dumps({'nic_numa_node':near,'choices':choices}))"""
        for role,device in [('server',args.server_device),('client',args.client_device)]:
            topology=json.loads(run(['exec','oke-dranet-'+role,'--','python3','-c',topology_code,device]))
            choices=[c for c in topology['choices'] if (c['numa_node']==topology['nic_numa_node']) == (args.placement=='near')]
            if topology['nic_numa_node']<0 or not choices:raise RuntimeError('Requested NUMA placement is unavailable in this container cpuset')
            placements[role]={**choices[0],'nic_numa_node':topology['nic_numa_node']}
            run(['exec','oke-dranet-'+role,'--','numactl','--physcpubind='+str(choices[0]['cpu']),'--membind='+str(choices[0]['numa_node']),'true'])
    def command(role, device):
        perf = 'ib_write_bw' if args.test == 'bandwidth' else 'ib_write_lat'
        flags = ['-q', '1', '--report_gbits'] if args.test == 'bandwidth' else []
        return prefix + ['exec', 'oke-dranet-' + role, '--', 'timeout', '-k', '5s', '100s', 'stdbuf', '-oL', '-eL',
                         *(['numactl','--physcpubind='+str(placements[role]['cpu']),'--membind='+str(placements[role]['numa_node'])] if placements else []), perf, '-R', '-d', device, '-i', '1', '--bind_source_ip', addresses[role],
                         '-T', str(args.tos), '-s', str(size),
                         *(['-D', str(args.duration)] if args.duration else ['-n', '5000']),
                         '-p', str(args.port or (18515 if args.test == 'bandwidth' else 18516)), *flags]

    server = subprocess.Popen(command('server', args.server_device), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=child_env)
    server_lines = []; listening = threading.Event()
    def consume():
        for line in server.stdout:
            server_lines.append(line)
            # This banner precedes the initial peer handshake. The later RDMA-CM
            # QP message cannot be used as a gate for launching that peer.
            if 'Waiting for client to connect' in line: listening.set()
    reader = threading.Thread(target=consume, daemon=True); reader.start()
    client_output = ''
    try:
        deadline = time.monotonic() + 25
        while not listening.wait(0.2):
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('RDMA server did not become ready: ' + ''.join(server_lines)[-2500:])
        started_at = time.time()
        client = subprocess.Popen(command('client', args.client_device) + [addresses['server']], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env)
        if args.started_marker: Path(args.started_marker).write_text(str(started_at))
        try:
            stdout, stderr = client.communicate(timeout=110)
        except subprocess.TimeoutExpired:
            client.kill(); client.communicate(); raise
        finished_at = time.time()
        client_output = stdout + stderr
        if client.returncode: raise RuntimeError('RDMA client failed: ' + client_output[-2500:])
        server.wait(timeout=15); reader.join(timeout=3)
        output = ''.join(server_lines)
        if server.returncode: raise RuntimeError('RDMA server failed: ' + output[-2500:])
        metrics = parse_bandwidth(output, size, bool(args.duration)) if args.test == 'bandwidth' else parse_latency(client_output, size)
        result = {'status': 'passed', 'placement': args.placement, 'affinity': placements, **metrics, 'child_addresses': addresses, 'nodes': nodes,
                  'reported_gids': re.findall(r'GID:\s*(.+)', output), 'rdma_cm': True, 'tos': args.tos,
                  'message_bytes': size, 'duration_seconds': args.duration, 'started_at': started_at, 'finished_at': finished_at, 'iterations': None if args.duration else 5000, 'qps': 1, 'measured_at': time.time(),
                  'interpretation': 'Hardware RDMA passed in non-host-network pods. Reported GIDs may use parent PF addresses; child-GID isolation is not asserted.',
                  'server_output': output, 'client_output': client_output}
        if args.test == 'latency':
            result['interpretation'] = 'RDMA write latency in microseconds, reported as half round-trip by perftest; not an independently measured one-way transit time.'
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc), 'stage': 'RDMA-CM ' + args.test,
                          'child_addresses': addresses, 'server_output': ''.join(server_lines)[-4000:], 'client_output': client_output[-4000:]}))
        return 1
    finally:
        if server.poll() is None:
            server.terminate()
            try: server.wait(timeout=3)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
        # In-cluster timeout and provider cleanup bound the remote processes too.

if __name__ == '__main__':
    raise SystemExit(main())
