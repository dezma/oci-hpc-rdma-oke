#!/usr/bin/env python3
"""Fixed, bounded profiles using already-reserved validation pods."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def verify_overlap(load, latency):
    return (load.get('status') == latency.get('status') == 'passed' and load.get('average_gbps', 0) > 0
            and load['started_at'] < latency['started_at'] < latency['finished_at'] < load['finished_at'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--profile',choices=['sweep','loaded','numa'],required=True)
    args, remaining=p.parse_known_args()
    runner=[sys.executable,str(Path(__file__).with_name('test_rdma.py')),*remaining]
    deadline=time.monotonic()+240
    result={'status':'failed','profile':args.profile,'samples':[], 'cpu_placement':'scheduler-managed; no CPU/NUMA pinning'}
    def run(extra):
        timeout=min(120,deadline-time.monotonic())
        if timeout<=0:raise RuntimeError('Performance profile exceeded its 240-second budget')
        r=subprocess.run(runner+extra,capture_output=True,text=True,timeout=timeout)
        try: sample=json.loads(r.stdout)
        except ValueError:raise RuntimeError('Benchmark returned no valid JSON: '+r.stderr[-1000:])
        if r.returncode or sample.get('status')!='passed':raise RuntimeError(sample.get('error','Benchmark failed'))
        return sample
    try:
        if args.profile=='sweep':
            for size in [8,64,512,4096,65536,1048576]:
                for test in ['bandwidth','latency']:
                    sample=run(['--test',test,'--message-bytes',str(size)])
                    result['samples'].append({'test':test,**sample})
        elif args.profile=='numa':
            for placement in ['near','far']:
                for test in ['bandwidth','latency']:
                    sample=run(['--test',test,'--placement',placement])
                    result['samples'].append({'test':test,**sample})
            near_bw,near_lat,far_bw,far_lat=result['samples']
            result['bandwidth_change_percent']=(far_bw['average_gbps']/near_bw['average_gbps']-1)*100
            result['latency_change_percent']=(far_lat['average_us']/near_lat['average_us']-1)*100
            result['cpu_placement']='Explicit CPU and memory binding; recorded NUMA nodes and CPUs per endpoint'
            result['interpretation']='Both endpoints move from NIC-local to a remote NUMA node. Observed combined CPU/memory placement penalty; not isolated memory latency or a causal guarantee. Requires allowed CPUs/memory nodes and set_mempolicy support.'
        else:
            result['idle_latency']=run(['--test','latency'])
            with tempfile.TemporaryDirectory(prefix='rdma-load-') as tmp:
                marker=str(Path(tmp)/'started')
                load=subprocess.Popen(runner+['--test','bandwidth','--duration','30','--port','18517','--started-marker',marker],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    ready_deadline=min(time.monotonic()+40,deadline-3)
                    while not Path(marker).exists():
                        if load.poll() is not None or time.monotonic()>ready_deadline:
                            raise RuntimeError('Concurrent traffic did not start')
                        time.sleep(.2)
                    time.sleep(3)
                    if load.poll() is not None:raise RuntimeError('Concurrent traffic ended before latency measurement')
                    result['loaded_latency']=run(['--test','latency'])
                    stdout,stderr=load.communicate(timeout=max(.1,min(75,deadline-time.monotonic())))
                    result['background_bandwidth']=json.loads(stdout)
                    if load.returncode or not verify_overlap(result['background_bandwidth'],result['loaded_latency']):
                        raise RuntimeError('Successful traffic overlap was not established; loaded latency is inconclusive')
                    idle=result['idle_latency']['average_us'];loaded=result['loaded_latency']['average_us']
                    result['latency_change_percent']=(loaded/idle-1)*100
                    result['interpretation']='Latency during a concurrent 30-second RDMA-write test. Process intervals overlap and background throughput is positive; continuous line-rate saturation is not established. CPU contention may contribute.'
                finally:
                    if load.poll() is None:
                        load.kill();load.communicate()
        result['status']='passed'
    except Exception as exc:
        result['error']=str(exc)[:2000]
    print(json.dumps(result))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
