"""Observed pair history and conservative, reproducible baseline comparisons."""
import hashlib
import json
import time
from .measurements import positive, MAX_AGE_SECONDS


def provenance(pair, inventory, cache, settings, profile):
    workers=[]
    for name in pair:
        node=next(n for n in inventory['nodes'] if n['name']==name)
        facts=cache.get(name,{}).get('facts',{})
        iface=next((i for i in facts.get('interfaces',[]) if i.get('name')==settings.rdma_pf_name),{})
        workers.append({'name':name,'uid':node.get('uid'),'boot_id':node.get('boot_id'),
                        'shape':node.get('shape'),'kernel':node.get('kernel'),
                        'driver':iface.get('driver'),'nic_identity':iface.get('driver_evidence',{}),
                        'worker_image_ocid':None})
    comparable=[{k:v for k,v in n.items() if k not in {'boot_id','worker_image_ocid'}} for n in workers]
    complete=all(n.get('uid') and n.get('shape') and n.get('kernel') and n.get('driver')
                 and n.get('nic_identity',{}).get('status')=='collected' and n.get('nic_identity',{}).get('output','').strip() for n in workers)
    configuration={'workers':comparable,'settings':settings.rdma_config(),'profile':profile,
                   'benchmark':'rdma-write-v2','iterations':5000,'qps':1,'cpu_placement':'scheduler-managed'}
    return {'workers':workers,'tools_image':settings.rdma_tools_image,'cpu_placement':'scheduler-managed; actual CPU/NUMA affinity is not pinned',
            'worker_image_note':'Worker source image OCID is not collected; comparisons match observed kernel/NIC versions and tools digest, not full image identity.',
            'comparison_key':hashlib.sha256(json.dumps(configuration,sort_keys=True).encode()).hexdigest() if complete else None}


def dashboard(jobs, environment, settings, now=None):
    now=time.time() if now is None else now
    names=sorted(n['name'] for n in environment['nodes'] if n.get('mode')=='dranet')
    current={n['name']:n for n in environment['nodes']}
    runs=sorted([j for j in jobs if j.get('action') in {'rdma_validation','monitoring_benchmark'} and j.get('requested_at',0)>environment.get('reset_at',0)],key=lambda j:j.get('requested_at',0),reverse=True)
    expanded=[]
    for job in runs:
        samples=(job.get('result') or {}).get('pair_results')
        if samples is None:expanded.append(job)
        else:
            for index,sample in enumerate(samples):
                expanded.append({**job,'id':job['id']+':'+str(index),'state':'SUCCEEDED' if sample.get('status')=='passed' else 'FAILED','result':sample})
    runs=expanded
    records=[]
    for server in names:
        for client in names:
            if server==client:continue
            matches=[j for j in runs if ((j.get('result') or {}).get('nodes') or [j.get('payload',{}).get('server_node'),j.get('payload',{}).get('client_node')])==[server,client]]
            row={'server':server,'client':client,'state':'not_measured'}
            if matches:
                j=matches[0];r=j.get('result') or {};row.update(job_id=j['id'],measured_at=r.get('measured_at'),profile=r.get('performance_profile','quick'))
                valid=j['state']=='SUCCEEDED' and r.get('status')=='passed' and positive(r.get('average_gbps')) and positive(r.get('latency',{}).get('average_us'))
                row['state']='measured' if valid else ('pending' if j['state'] in {'QUEUED','RUNNING'} else 'failed')
                if valid:
                    row.update(average_gbps=r['average_gbps'],average_us=r['latency']['average_us'],p99_us=r['latency'].get('p99_us'))
                    workers=r.get('provenance',{}).get('workers',[])
                    fresh=bool(len(workers)==2 and positive(r.get('measured_at')) and -60<=now-r['measured_at']<=MAX_AGE_SECONDS)
                    for n in workers:
                        actual=current.get(n['name'],{})
                        fresh=fresh and bool(n.get('uid') and n.get('boot_id') and n['uid']==actual.get('uid') and n['boot_id']==actual.get('boot_id'))
                    from .measurements import context
                    fresh=fresh and r.get('context',{}).get('configuration_sha256')==context(environment['nodes'],settings)['configuration_sha256']
                    row['state']='measured' if fresh else 'historical'
                    key=r.get('provenance',{}).get('comparison_key')
                    older=[x for x in matches[1:] if key and x['state']=='SUCCEEDED' and (x.get('result') or {}).get('status')=='passed' and x['result'].get('provenance',{}).get('comparison_key')==key and positive(x['result'].get('average_gbps')) and positive(x['result'].get('latency',{}).get('average_us'))]
                    if older:
                        base=older[-1];br=base['result']
                        row['baseline']={'job_id':base['id'],'measured_at':br.get('measured_at'),'bandwidth_change_percent':(r['average_gbps']/br['average_gbps']-1)*100,'latency_change_percent':(r['latency']['average_us']/br['latency']['average_us']-1)*100,'policy':'Earliest comparable successful run in retained history; observational comparison, not proof of cause.'}
                    else:
                        row['baseline_note']='No comparable baseline in retained history; changed test conditions are not a controlled comparison.'
                        references=[x for x in matches[1:] if x['state']=='SUCCEEDED' and (x.get('result') or {}).get('status')=='passed' and positive(x['result'].get('average_gbps')) and positive(x['result'].get('latency',{}).get('average_us'))]
                        if references:
                            ref=references[0]['result']
                            row['reference']={'job_id':references[0]['id'],'bandwidth_change_percent':(r['average_gbps']/ref['average_gbps']-1)*100,'latency_change_percent':(r['latency']['average_us']/ref['latency']['average_us']-1)*100,'warning':'Test conditions changed or provenance incomplete. Previous result is a descriptive reference, not a comparable regression baseline.','before':ref.get('provenance'),'after':r.get('provenance')}
            records.append(row)
    return {'pairs':records,'history_limit':100,'scope':'Directional measurements from retained runs; not an inferred full-fabric benchmark. Historical and failed results are never promoted to current health.'}


def capacity_timing(operation, jobs):
    if not operation:return None
    stages=dict(operation.get('milestones',{}));start=stages.get('apply_submitted_at')
    targets=operation.get('new_node_identities',[])
    passed={}
    for job in jobs:
        result=job.get('result') or {}
        for sample in result.get('pair_results',[result]):
            if job.get('action') not in {'rdma_validation','monitoring_benchmark'} or sample.get('status')!='passed' or not start or sample.get('measured_at',0)<start:continue
            if not positive(sample.get('average_gbps')) or not positive(sample.get('latency',{}).get('average_us')):continue
            for node in sample.get('provenance',{}).get('workers',[]):
                if any(all(node.get(k)==t.get(k) and node.get(k) for k in ['name','uid','boot_id']) for t in targets):
                    passed[node['name']]=min(passed.get(node['name'],float('inf')),sample.get('latency',{}).get('measured_at',sample['measured_at']))
    verified=max(passed.values()) if targets and all(n['name'] in passed for n in targets) else None
    stages['all_new_workers_validation_observed_at']=verified
    return {'stages':stages,'seconds_since_apply':{k:v-start if start and v is not None and v>=start else None for k,v in stages.items()},'time_until_verified_rdma_seconds':verified-start if verified and start else None,'pending_validation':[n['name'] for n in targets if n['name'] not in passed],'note':'Stage completion observations, not isolated service durations. ORM apply includes provisioning and bootstrap. Kubernetes registration and RDMA authentication internals are not timed independently. Validation requires a passing pair run for every new worker identity; retained history is limited to 100 jobs.'}
