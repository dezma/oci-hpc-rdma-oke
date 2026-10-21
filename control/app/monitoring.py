"""Opt-in serial monitoring, explicit baselines, and bounded OCI Notifications delivery."""
from __future__ import annotations
from datetime import datetime, timezone
from itertools import combinations
import json
import fcntl
from urllib.parse import urlsplit
import math
import time
from pydantic import BaseModel, ConfigDict, Field
from .capacity import atomic, digest
from .provider_base import ProviderError

STATES = ('healthy', 'unhealthy', 'unknown', 'stale', 'maintenance')

class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    interval_seconds: int = Field(default=900, ge=300, le=86400)
    stale_seconds: int = Field(default=7200, ge=600, le=604800)
    refresh_idle_hosts: bool = False
    nightly_pair: bool = False
    nightly_hour_utc: int = Field(default=1, ge=0, le=23)
    maintenance_until: float = Field(default=0, ge=0, allow_inf_nan=False)
    claim_age_seconds: int = Field(default=86400, ge=3600, le=2592000)
    notification_topic: str = Field(default='', pattern=r'^(?:ocid1\.onstopic\.[A-Za-z0-9._-]+)?$')
    confirmation: str = ''


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def profile(node, facts, settings):
    iface = next((i for i in facts.get('interfaces', []) if i.get('name') == settings.rdma_pf_name), {})
    evidence = iface.get('driver_evidence', {}).get('output', '')
    firmware = next((v.split(':', 1)[1].strip() for v in evidence.splitlines() if v.startswith('firmware-version:')), None)
    return {'kernel': node.get('kernel') or None, 'firmware': firmware,
            'ofed': facts.get('ofed_version', {}).get('output', '').strip() or None,
            'oca': facts.get('oca_version', {}).get('output', '').strip() or None,
            'mtu': iface.get('mtu'), 'driver': iface.get('driver'),
            'rdma_mode': 'shared' if 'netns shared' in facts.get('netns', '') else ('exclusive' if 'netns exclusive' in facts.get('netns', '') else None),
            'configuration_sha256': digest(settings.rdma_config())}


def classify(node, entry, config, now, baseline=None, settings=None):
    facts = entry.get('facts', {})
    fresh = bool(entry.get('at')) and 0 <= now-entry['at'] <= config['stale_seconds']
    identity = bool(node.get('boot_id')) and facts.get('boot_id') == node.get('boot_id')
    collected = entry.get('collection_status') == 'passed' or (entry.get('collection_status') is None and bool(facts.get('interfaces')))
    state = 'unknown'
    if node.get('cordoned') or config['maintenance_until'] > now: state = 'maintenance'
    elif not node.get('ready'): state = 'unhealthy'
    elif not collected: state = 'unknown'
    elif not fresh or not identity: state = 'stale'
    elif node.get('issues') or node.get('host_issues'): state = 'unhealthy'
    else: state = 'healthy'
    current = profile(node, facts, settings) if settings else {}
    changes, missing = [], []
    if baseline and fresh and identity and collected:
        for key, expected in baseline.get('profile', {}).items():
            if expected is None: continue
            if current.get(key) is None: missing.append(key)
            elif current[key] != expected: changes.append(key)
    return {'name': node['name'], 'state': state, 'host_checked_at': entry.get('at'),
            'uid': node.get('uid'), 'boot_id': node.get('boot_id'), 'profile': current,
            'drift': changes, 'unobserved_fields': missing,
            'issues': list(node.get('issues', []))+list(node.get('host_issues', [])),
            'baseline_status': 'not_approved' if not baseline else ('unknown' if not fresh or not identity or not collected else ('drift' if changes else ('incomplete' if missing else 'matches')))}


class Monitoring:
    def __init__(self, provider):
        self.p = provider
        self.root = provider.capacity_service().root / 'monitoring'
        self.config_path = self.root/'config.json'
        self.state_path = self.root/'state.json'
        self.baseline_path = self.root/'baseline.json'
    def config(self):
        return MonitoringConfig(**read(self.config_path, {})).model_dump(exclude={'confirmation'})
    def save_config(self, values):
        from .db import idle_guard
        if values.confirmation != self.p.s.environment_name: raise ValueError('Type the exact environment name to save monitoring settings.')
        if values.maintenance_until > time.time()+7*86400: raise ValueError('Maintenance pause must be within seven days.')
        with idle_guard(): atomic(self.config_path, values.model_dump(exclude={'confirmation'}))
        return {'saved': True, 'config': self.config()}
    def status(self):
        config = self.config(); state = read(self.state_path, {})
        # Expired observations are never exposed as current health by read endpoints.
        now = time.time()
        for row in state.get('nodes', []):
            if config['maintenance_until'] > now: row['state'] = 'maintenance'
            elif now-state.get('observed_at', 0) > config['stale_seconds']: row['state'] = 'stale'
            elif row['state'] == 'healthy' and now-(row.get('host_checked_at') or 0) > config['stale_seconds']: row['state']='stale'
        return {'config': config, 'snapshot': state, 'baseline': read(self.baseline_path, None), 'scheduler': read(self.root/'scheduler.json', {})}
    def guard(self):
        cap = self.p.capacity_service()
        cap.guard('monitoring_sweep')
        if self.p.s.mode != 'live': raise ProviderError('Monitoring requires a live environment; demo does not generate health evidence.')
        pool = cap.pool(); cap.no_active_oke(pool)
        if cap.stack_id(): cap.no_active(cap.stack_id())
    def idle(self, inv):
        # Conservatively skip active checks whenever any RDMA claim exists.
        if inv.get('claims'): return []
        pods = self.p._json(['get', 'pods', '-A']).get('items', [])
        occupied = {p.get('spec', {}).get('nodeName') for p in pods
                    if p.get('status', {}).get('phase') not in {'Succeeded','Failed'}
                    and p.get('metadata', {}).get('namespace') not in {'kube-system','cert-manager','node-feature-discovery'}
                    and not any(o.get('kind') == 'DaemonSet' for o in p.get('metadata', {}).get('ownerReferences', []))}
        return [n for n in inv.get('nodes', []) if n.get('mode') == 'dranet' and n.get('ready') and not n.get('cordoned') and n['name'] not in occupied
                and not any(d.get('allocated') for d in n.get('devices', []))]
    def approve_baseline(self, name):
        self.guard(); cfg=self.config(); inv=self.p.inventory()
        node=next((n for n in inv['nodes'] if n['name']==name), None)
        if not node: raise ValueError('Select a current worker.')
        row=classify(node,self.p._host_cache().get(name,{}),cfg,time.time(),settings=self.p.s)
        if row['state']!='healthy': raise ValueError('Baseline approval requires fresh, healthy host evidence on the current boot.')
        baseline={'approved_at':time.time(),'source_node':name,'uid':node.get('uid'),'boot_id':node.get('boot_id'),'profile':row['profile']}
        atomic(self.baseline_path,baseline)
        return {'status':'passed','message':'Fleet comparison profile approved. Missing version fields remain unknown; no host setting changed.','baseline':baseline}
    def alerts(self, state, signals):
        now=time.time(); tracked=state.setdefault('signals',{}); out=state.setdefault('alerts',[])
        for key, value in signals.items():
            item=tracked.setdefault(key,{'candidate':None,'count':0,'active':None})
            item['count']=item['count']+1 if item['candidate']==value else 1;item['candidate']=value
            # Two observations suppress transient changes; null means resolved.
            if item['count']>=2 and item['active']!=value:
                previous=item['active'];item['active']=value
                if value is not None or previous is not None:
                    out.append({'id':digest([key,value,now])[:16],'at':now,'key':key,'condition':value or 'resolved',
                                'delivery':'pending','attempts':0,'next_attempt':now})
        state['alerts']=out[-100:]
    def deliver(self, state, cfg):
        if not cfg['notification_topic']: return
        now=time.time()
        pending=[a for a in state.get('alerts',[]) if a['delivery']=='pending' and a['next_attempt']<=now][:3]
        for alert in pending:
            # Persist attempt before transmission; a crash cannot cause unbounded retries.
            if alert['attempts']>=3:
                alert['delivery']='failed'
                atomic(self.state_path,state)
                continue
            alert['attempts']+=1;alert['next_attempt']=now+min(3600,60*2**alert['attempts'])
            atomic(self.state_path,state)
            body=json.dumps({'environment':self.p.s.environment_name,'event_id':alert['id'],'condition':alert['condition'],'subject':alert['key'],'at':alert['at']})
            try:
                topic=self.p.oci(['--no-retry','ons','topic','get','--topic-id',cfg['notification_topic']],check=False,timeout=15)
                if topic.returncode: raise ProviderError('Topic lookup failed')
                data=json.loads(topic.stdout)['data']
                endpoint=data.get('api-endpoint', '')
                url=urlsplit(endpoint)
                if data.get('topic-id',data.get('id')) != cfg['notification_topic'] or url.scheme!='https' or not url.hostname or url.username or url.password or url.port not in (None,443) or url.query or url.fragment:
                    raise ProviderError('Invalid topic endpoint')
                # The endpoint comes from authenticated OCI topic metadata, never UI input.
                result=self.p.oci(['--no-retry','--endpoint',endpoint,'ons','message','publish','--topic-id',cfg['notification_topic'],'--body',body],check=False,timeout=15)
                ok=result.returncode==0
            except Exception: ok=False
            alert['delivery']='sent' if ok else ('failed' if alert['attempts']>=3 else 'pending')
            alert['delivery_note']='Published to OCI topic; subscriber delivery is not verified.' if ok else 'Publish failed. Check topic, region and ONS read/publish permissions.'
            atomic(self.state_path,state)
    def sweep(self):
        self.guard(); cfg=self.config();now=time.time()
        if cfg['maintenance_until']>now:return {'status':'skipped','message':'Monitoring is paused for maintenance.'}
        state=read(self.state_path,{}); previous=state.get('nodes',[])
        try:
            inv=self.p.inventory()
            if cfg['refresh_idle_hosts']:
                eligible=self.idle(inv);cache=self.p._host_cache()
                eligible.sort(key=lambda n:cache.get(n['name'],{}).get('at',0))
                if eligible:
                    # One fixed-profile host pod per sweep, with its diagnostic deadline.
                    self.p.check_hosts(node_names=[eligible[0]['name']],force=True)
                    inv=self.p.inventory()
            cache=self.p._host_cache();baseline=read(self.baseline_path,None)
            now=time.time()
            rows=[classify(n,cache.get(n['name'],{}),cfg,now,baseline,self.p.s) for n in inv['nodes'] if n.get('mode')=='dranet']
            state.update(observed_at=now,last_success_at=now,collection='passed',nodes=rows,free_slots=inv.get('capacity',{}).get('free',0),claim_count=len(inv.get('claims',[])))
            signals={'worker:'+n['name']: ('configuration_drift' if n['drift'] and n['state']=='healthy' else None if n['state'] in {'healthy','maintenance'} else n['state']) for n in rows}
            for key in state.get('signals', {}):
                if key.startswith('worker:') and key not in signals:signals[key]='worker_absent'
            state['claims']=[]
            for c in inv.get('claims', []):
                age=None
                try: age=max(0,now-datetime.fromisoformat(c['created_at'].replace('Z','+00:00')).timestamp())
                except (KeyError,ValueError,TypeError): pass
                key='claim:'+c['namespace']+'/'+c['name']
                state['claims'].append({'namespace':c['namespace'],'name':c['name'],'age_seconds':age,'created_at':c.get('created_at'),'consumers':len(c.get('consumers',[]))})
                signals[key]='long_held_claim_review' if age is not None and age>=cfg['claim_age_seconds'] else None
            for key in state.get('signals',{}):
                if key.startswith('claim:') and key not in signals:signals[key]=None
            signals['driver']='driver_not_ready' if inv.get('nodes') and not inv.get('driver_ready') else None
            signals['collection']=None
            from .db import list_jobs
            attempts=[j for j in list_jobs(200) if j['action'] in {'rdma_validation','monitoring_benchmark'} and j['state'] in {'SUCCEEDED','FAILED'} and (j.get('result') or {}).get('status')!='skipped']
            if attempts:
                latest=attempts[0]; signals['validation']='validation_failed' if latest['state']=='FAILED' or (latest.get('result') or {}).get('status') not in {'passed','simulated'} else None
            self.alerts(state,signals)
        except Exception:
            state.update(observed_at=now,collection='failed',nodes=[{**n,'state':'unknown'} for n in previous])
            self.alerts(state,{'collection':'collection_failed'})
        atomic(self.state_path,state); self.deliver(state,cfg)
        return {'status':'passed' if state['collection']=='passed' else 'failed','message':'Monitoring snapshot recorded. Health and collection status are separate.','monitoring':self.status()['snapshot']}
    def benchmark(self):
        self.guard();cfg=self.config()
        if not cfg['enabled'] or not cfg['nightly_pair'] or cfg['maintenance_until']>time.time():return {'status':'skipped','message':'Scheduled benchmark is disabled or paused.'}
        inv=self.p.inventory(); eligible=[n for n in self.idle(inv) if not n.get('issues') and not n.get('host_issues') and any(d.get('valid') for d in n.get('devices',[]))]
        pairs=list(combinations(sorted(n['name'] for n in eligible),2))
        if not pairs:return {'status':'skipped','message':'Fewer than two healthy idle workers; no benchmark claims created.'}
        state=read(self.state_path,{});index=state.get('pair_index',0);pair=pairs[index%len(pairs)]
        state['pair_index']=index+1;atomic(self.state_path,state)
        return self.p.rdma_validation(pair[0],pair[1],'quick')


def schedule(provider, now=None):
    """Called only while the durable worker is idle. enqueue serializes against UI jobs."""
    from .db import list_jobs, enqueue
    now=time.time() if now is None else now
    m=Monitoring(provider)
    m.root.mkdir(parents=True,exist_ok=True)
    with (m.root/'scheduler.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        return _schedule(provider,m,now)


def _schedule(provider,m,now):
    from .db import list_jobs, enqueue
    cfg=m.config()
    if not cfg['enabled'] or cfg['maintenance_until']>now or provider.s.mode!='live':return
    jobs=list_jobs(500)
    if any(j['state'] in {'QUEUED','RUNNING'} for j in jobs):return
    recorded=read(m.root/'scheduled.json',{})
    last=lambda action:max([j['requested_at'] for j in jobs if j['action']==action]+[recorded.get(action,0)])
    action=None
    midnight=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    if cfg['nightly_pair'] and now>=midnight+cfg['nightly_hour_utc']*3600 and last('monitoring_benchmark')<midnight:action='monitoring_benchmark'
    elif now-last('monitoring_sweep')>=cfg['interval_seconds']:action='monitoring_sweep'
    if not action:return
    try:
        m.guard()
    except ProviderError as exc:
        atomic(m.root/'scheduler.json',{'checked_at':now,'state':'blocked','reason':str(exc)[:1000]})
        return
    atomic(m.root/'scheduler.json',{'checked_at':now,'state':'ready'})
    enqueue(action,provider.s.environment_name,{'scheduled':True})
    recorded[action]=now
    atomic(m.root/'scheduled.json',recorded)


def metrics(provider):
    from .db import list_jobs
    m=Monitoring(provider); status=m.status();state=status['snapshot'];lines=[]
    def emit(name,value,labels=None):
        if type(value) not in (int,float) or not math.isfinite(value):return
        label='{'+','.join(k+'='+json.dumps(str(v),ensure_ascii=False) for k,v in (labels or {}).items())+'}' if labels else ''
        lines.append(f'oke_rdma_{name}{label} {value}')
    emit('monitoring_enabled',int(status['config']['enabled']))
    emit('snapshot_timestamp_seconds',state.get('observed_at',0))
    emit('last_success_timestamp_seconds',state.get('last_success_at',0))
    emit('collection_success',int(state.get('collection')=='passed'))
    emit('free_slots',state.get('free_slots'));emit('claims',state.get('claim_count'))
    for c in state.get('claims',[]):
        emit('claim_age_seconds',c.get('age_seconds'),{'namespace':c['namespace'],'claim':c['name']})
    for n in state.get('nodes',[]):
        for name in STATES:emit('worker_health',int(n['state']==name),{'node':n['name'],'state':name})
        emit('host_check_timestamp_seconds',n.get('host_checked_at') or 0,{'node':n['name']})
        emit('worker_drift',len(n.get('drift',[])),{'node':n['name']})
    jobs=list_jobs(500);counts={};pairs=set();attempt_pairs=set()
    for j in jobs:
        key=(j['action'],j['state']);counts[key]=counts.get(key,0)+1
        r=j.get('result') or {};nodes=r.get('nodes',[])
        if j['action'] in {'rdma_validation','monitoring_benchmark'} and j['state'] in {'SUCCEEDED','FAILED'} and r.get('status')!='skipped':
            payload=j.get('payload') or {}
            attempt_nodes=nodes or [payload.get('server_node'),payload.get('client_node')]
            if len(attempt_nodes)==2 and all(isinstance(n,str) and n for n in attempt_nodes) and tuple(attempt_nodes) not in attempt_pairs:
                attempt_pairs.add(tuple(attempt_nodes)); labels={'server':attempt_nodes[0],'client':attempt_nodes[1]}
                emit('pair_latest_attempt_success',int(j['state']=='SUCCEEDED' and r.get('status')=='passed'),labels)
                emit('pair_latest_attempt_timestamp_seconds',j.get('finished_at') or j['requested_at'],labels)
        if j['action'] in {'rdma_validation','monitoring_benchmark'} and len(nodes)==2 and all(isinstance(n,str) for n in nodes) and tuple(nodes) not in pairs and r.get('average_gbps') is not None:
            pairs.add(tuple(nodes));labels={'server':nodes[0],'client':nodes[1]}
            emit('pair_bandwidth_gbps',r.get('average_gbps'),labels)
            emit('pair_latency_microseconds',(r.get('latency') or {}).get('average_us'),labels)
            emit('pair_measurement_timestamp_seconds',r.get('measured_at',0),labels)
            emit('pair_measurement_success',int(j['state']=='SUCCEEDED' and r.get('status')=='passed'),labels)
    for (action,state_name),count in counts.items():emit('retained_jobs',count,{'action':action,'state':state_name})
    for delivery in ('pending','sent','failed'):emit('retained_alerts',sum(a['delivery']==delivery for a in state.get('alerts',[])),{'delivery':delivery})
    return '\n'.join(lines)+'\n'
