"""Reviewed single-worker replacement. Never replay a destructive submission."""
from __future__ import annotations
import json
import re
import time
import uuid
from .capacity import atomic, digest, stack_fingerprint
from .provider_base import ProviderError
from .recovery import Recovery, documents

TERMINAL = {'COMPLETE', 'CLOSED'}


class OKEGateway:
    def __init__(self, region):
        import oci
        self.oci = oci
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        self.client = oci.container_engine.ContainerEngineClient({'region':region}, signer=signer, timeout=(10,60), retry_strategy=oci.retry.NoneRetryStrategy())
    def pool(self, pool_id):
        r=self.client.get_node_pool(pool_id)
        return self.oci.util.to_dict(r.data), r.headers['etag']
    def replace(self, pool_id, instance_id, etag, request_id):
        r=self.client.delete_node(pool_id, instance_id, is_decrement_size=False, if_match=etag,
            override_eviction_grace_duration='PT60M', is_force_deletion_after_override_grace_duration=False,
            opc_request_id=request_id, retry_strategy=self.oci.retry.NoneRetryStrategy())
        return r.headers.get('opc-work-request-id','')
    def work(self, work_id):
        return self.oci.util.to_dict(self.client.get_work_request(work_id).data)


class Replacement:
    def __init__(self, provider, gateway=None):
        self.p=provider;self.cap=provider.capacity_service();self.root=self.cap.root/'replacement'
        self.path=self.root/'operation.json';self._gateway=gateway
    @property
    def gateway(self):
        if self._gateway is None:self._gateway=OKEGateway(self.p.s.region)
        return self._gateway
    def operation(self):return json.loads(self.path.read_text()) if self.path.exists() else None
    def guard(self, action):
        op=self.operation()
        if op and op['phase'] not in TERMINAL and action not in {'replacement_resume','replacement_close','preflight','check_hosts','node_diagnostic','cleanup_validation'}:
            raise ProviderError('A worker replacement is pending. Resume or review it before changing capacity, recovery, settings or workloads.')
    def save(self, op, phase=None):
        if phase:op['phase']=phase
        op['updated_at']=time.time()
        rows=op.setdefault('timeline',[])
        if not rows or rows[-1]['phase']!=op['phase']:rows.append({'phase':op['phase'],'at':op['updated_at']})
        atomic(self.path,op)
    def authorize(self):
        Recovery(self.p).authorize()
        if self.cap.pending():raise ProviderError('Resolve the capacity operation before replacing a worker.')
    def snapshot(self):
        sid=self.cap.stack_id();stack,_=self.cap.verify_stack(sid)
        self.cap.verify_image_pin(sid,stack);self.cap.no_active(sid)
        pool,etag=self.gateway.pool(self.p.s.node_pool_id)
        if pool.get('id')!=self.p.s.node_pool_id or pool.get('cluster_id')!=self.p.s.cluster_id:
            raise ProviderError('Replacement pool does not match the configured cluster.')
        source=pool.get('node_source_details') or {}
        image=source.get('image_id')
        if not image or image!=(stack.get('variables') or {}).get('hpc_custom_image_ocid'):
            raise ProviderError('Pin the worker image to the currently configured pool image before replacement.')
        if int((stack.get('variables') or {}).get('node_count',-1))!=pool['node_config_details']['size']:
            raise ProviderError('Stack target and observed pool size differ. Reconcile capacity first.')
        if pool.get('node_pool_cycling_details',{}).get('is_node_cycling_enabled'):
            raise ProviderError('Disable pool-wide cycling before individual replacement.')
        configuration={k:v for k,v in pool.items() if k not in {'nodes','lifecycle_state','lifecycle_details','time_created'}}
        return pool,etag,{'stack':stack_fingerprint(stack),'pool':digest(configuration),'image':image,'settings':self.p.s.rdma_config()}
    def selected(self,name,pool):
        node=self.p._json(['get','node',name])
        labels=node['metadata'].get('labels',{})
        if labels.get('oci-rdma')!='true' or labels.get('oke-rdma-mode')!='dranet':raise ProviderError('Select a managed Dranet worker.')
        instance=self.p._node_instance_id(node)
        matches=[n for n in pool.get('nodes',[]) if n.get('id')==instance and n.get('lifecycle_state')=='ACTIVE']
        if not instance or len(matches)!=1:raise ProviderError('Cannot prove this Kubernetes node belongs to the managed pool by OCI instance ID. No IP-only replacement is allowed.')
        return node,instance
    def preview(self,name):
        self.authorize();self.guard('replacement_preview')
        pool,_,fingerprint=self.snapshot()
        if pool.get('lifecycle_state')!='ACTIVE' or any(n.get('lifecycle_state')!='ACTIVE' for n in pool.get('nodes',[])):
            raise ProviderError('Wait for a stable ACTIVE node pool before replacement.')
        if len(pool.get('nodes',[]))!=pool['node_config_details']['size']:raise ProviderError('Pool membership is not stable.')
        node,instance=self.selected(name,pool)
        blockers=[]
        try:Recovery(self.p).idle(name)
        except ProviderError as e:blockers.append(str(e))
        inv=self.p.inventory();row=next((n for n in inv['nodes'] if n['name']==name),{})
        op={'id':uuid.uuid4().hex,'created_at':time.time(),'node_name':name,'node_uid':node['metadata']['uid'],
            'boot_id':node.get('status',{}).get('nodeInfo',{}).get('bootID'),'instance_id':instance,
            'pool_id':pool['id'],'size':pool['node_config_details']['size'],'before_ids':sorted(n['id'] for n in pool['nodes']),
            'fingerprint':fingerprint,'blockers':blockers,'eligible':not blockers,
            'findings':row.get('host_issues',[]),'host_check_at':row.get('host_check_at'),
            'hardware_before':row.get('hardware_identity',{}),
            'claim_count':len(inv.get('claims',[])),
            'workloads':[{'namespace':pod.get('metadata',{}).get('namespace'),'name':pod.get('metadata',{}).get('name'),'phase':pod.get('status',{}).get('phase')} for pod in self.p._json(['get','pods','-A','--field-selector','spec.nodeName='+name]).get('items',[])],
            'impact':'Terminate only the selected instance and request one replacement. Pool size and pinned image stay unchanged. Temporary capacity loss; OKE drains with 60-minute grace and no forced termination. Pause application controllers first.',
            'protection':'Admission protection blocks new pods/bindings targeting this worker and new resource claims/templates cluster-wide until safe cleanup. Do not run concurrent Resource Manager or OKE changes.'}
        atomic(self.root/('preview-'+op['id']+'.json'),op)
        return {'status':'passed' if op['eligible'] else 'blocked',**op}
    def read_preview(self,ident):
        if not re.fullmatch('[a-f0-9]{32}',ident):raise ProviderError('Invalid replacement preview.')
        path=self.root/('preview-'+ident+'.json')
        if not path.exists():raise ProviderError('Replacement preview not found. Preview again.')
        op=json.loads(path.read_text())
        if time.time()-op['created_at']>300 or not op['eligible']:raise ProviderError('Replacement preview expired or blocked. Preview again.')
        return op
    def prove_guard(self,op):
        marker='OKE_RDMA_RECOVERY_GUARD'
        probes=[{'apiVersion':'v1','kind':'Pod','metadata':{'name':'replacement-probe','namespace':'oke-rdma-diagnostics'},'spec':{'nodeName':op['node_name'],'containers':[{'name':'probe','image':self.p.s.rdma_tools_image}]}},
          {'apiVersion':'resource.k8s.io/v1','kind':'ResourceClaim','metadata':{'name':'replacement-probe','namespace':'oke-rdma-diagnostics'},'spec':{'devices':{'requests':[{'name':'rdma','exactly':{'deviceClassName':'dra.net'}}]}}},
          {'apiVersion':'v1','kind':'Binding','metadata':{'name':'replacement-probe','namespace':'oke-rdma-diagnostics'},'target':{'apiVersion':'v1','kind':'Node','name':op['node_name']}}]
        for doc in probes:
            # Binding admission can be tested through the core bindings endpoint.
            res=self.p.kubectl(['create','--dry-run=server','-f','-'],stdin=json.dumps(doc),check=False,timeout=15)
            if not res.returncode or marker not in res.stderr:raise ProviderError('Admission denial not proven; no replacement submitted. Close this preparation and preview again.')
    def cleanup_guard(self,op):
        # Only recorded names; no label-wide deletion. Missing objects are safe after interrupted cleanup.
        for kind in ['validatingadmissionpolicybinding','validatingadmissionpolicy']:
            self.p.kubectl(['delete',kind,op['guard'],'--ignore-not-found=true'],timeout=30)
        op['guard_removed']=True;self.save(op)
    def start(self,ident):
        self.authorize();self.guard('replacement_run');preview=self.read_preview(ident)
        prior=self.operation()
        if prior and prior['id']==ident:raise ProviderError('This preview was already used. Create a fresh preview; submissions are never replayed.')
        pool,etag,fingerprint=self.snapshot();node,instance=self.selected(preview['node_name'],pool)
        if fingerprint!=preview['fingerprint'] or sorted(n['id'] for n in pool['nodes'])!=preview['before_ids'] or node['metadata']['uid']!=preview['node_uid'] or node.get('status',{}).get('nodeInfo',{}).get('bootID')!=preview['boot_id']:
            raise ProviderError('Worker or pool changed since preview. Preview again.')
        Recovery(self.p).idle(preview['node_name'])
        op={**preview,'phase':'PREPARING','guard':'rdma-replacement-'+preview['id'],'submission':'NOT_SENT','work_request_id':'','original_unschedulable':node.get('spec',{}).get('unschedulable',False)}
        self.save(op)
        try:
            self.p.report_progress('Protecting selected worker',0,4,op['node_name'])
            self.p.kubectl(['patch','node',op['node_name'],'--type=json','-p',json.dumps([
                {'op':'test','path':'/metadata/uid','value':op['node_uid']},
                {'op':'test','path':'/metadata/resourceVersion','value':node['metadata']['resourceVersion']},
                {'op':'add','path':'/spec/unschedulable','value':True}])])
            for doc in documents(op['guard'],op['node_name']):self.p.kubectl(['create','-f','-'],stdin=json.dumps(doc))
            self.prove_guard(op);Recovery(self.p).idle(op['node_name'])
            pool,etag,current=self.snapshot()
            current_node,current_id=self.selected(op['node_name'],pool)
            if current!=op['fingerprint'] or current_id!=instance or current_node['metadata']['uid']!=op['node_uid'] or sorted(n['id'] for n in pool['nodes'])!=op['before_ids']:
                raise ProviderError('Identity or configuration changed during protection. No request submitted.')
            op['submission']='UNKNOWN';op['request_id']='rdma-replacement-'+op['id'];self.save(op,'SUBMITTING')
            try:
                wid=self.gateway.replace(op['pool_id'],instance,etag,op['request_id'])
            except Exception as e:
                if getattr(e,'status',None) in {400,401,403,404,409,412,422,429}:op['submission']='REJECTED'
                raise
            op['submission']='ACCEPTED';op['work_request_id']=wid;self.save(op,'WAITING_FOR_OCI')
            return self.result(op,'Replacement requested. Resume to observe progress; deletion is never replayed.')
        except Exception as e:
            op['error']=str(e)[:2000];self.save(op,'REVIEW_REQUIRED')
            raise ProviderError('Replacement stopped: '+op['error']+'. Review the retained operation; do not submit a second deletion.') from e
    def get(self,ident):
        op=self.operation()
        if not op or op['id']!=ident:raise ProviderError('Replacement operation changed. Refresh before continuing.')
        return op
    def result(self,op,message):
        return {'status':('passed' if op['phase']=='COMPLETE' else 'closed' if op['phase']=='CLOSED' else 'blocked' if op['phase'] in {'RDMA_FAILED','OCI_FAILED','REVIEW_REQUIRED'} else 'pending'),'message':message,'replacement':op}
    def resume(self,ident):
        self.authorize();op=self.get(ident)
        if op['phase'] in TERMINAL:return self.result(op,'Recorded replacement outcome.')
        if op['submission'] in {'NOT_SENT','REJECTED'}:raise ProviderError('No deletion was accepted. Close this operation to remove protection, then review the cordoned worker.')
        pool,_,fingerprint=self.snapshot()
        if fingerprint!=op['fingerprint']:raise ProviderError('Pool, stack or settings changed during replacement. Reconcile external changes before resuming.')
        if op['work_request_id']:
            work=self.gateway.work(op['work_request_id']);op['work_status']=work.get('status');self.save(op)
            if work.get('status') in {'FAILED','CANCELED'}:
                self.save(op,'OCI_FAILED');return self.result(op,'OKE work request failed. Review OCI work-request errors. No retry was submitted.')

        ids={n['id'] for n in pool.get('nodes',[]) if n.get('lifecycle_state')!='DELETED'}
        old_node_present=any(n['metadata']['uid']==op['node_uid'] for n in self.p._rdma_nodes())
        if op['instance_id'] not in ids and not old_node_present and not op.get('guard_removed'):
            self.cleanup_guard(op)
        if op.get('work_request_id') and op.get('work_status')!='SUCCEEDED':
            return self.result(op,'OKE is still draining/replacing the worker. Resume later.')
        survivors=set(op['before_ids'])-{op['instance_id']};new=ids-survivors
        if op['instance_id'] in ids or not survivors.issubset(ids) or len(new)!=1 or len(ids)!=op['size'] or any(n.get('lifecycle_state')!='ACTIVE' for n in pool.get('nodes',[]) if n['id'] in ids):
            return self.result(op,'Waiting for exactly one new ACTIVE instance and all original surviving members. No deletion is replayed.')
        if any(n['metadata']['uid']==op['node_uid'] for n in self.p._rdma_nodes()):return self.result(op,'Waiting for the old Kubernetes node identity to leave.')
        op['new_instance_id']=next(iter(new));self.save(op,'VERIFYING_NODE')
        if not op.get('guard_removed'):self.cleanup_guard(op)
        nodes=[n for n in self.p._rdma_nodes() if self.p._node_instance_id(n)==op['new_instance_id']]
        if len(nodes)!=1:return self.result(op,'Waiting for the replacement Kubernetes node to register by instance identity.')
        n=nodes[0];op['new_node_name']=n['metadata']['name'];op['new_node_uid']=n['metadata']['uid']
        from .provider import ready
        if not ready(n) or n.get('spec',{}).get('unschedulable'):self.save(op);return self.result(op,'Replacement is not yet Ready and schedulable.')
        self.p.report_progress('Verifying replacement RDMA readiness',2,4,op['new_node_name'])
        op['host_readiness']=self.p.check_hosts([op['new_node_name']])
        from .hardware_identity import compare
        hosts=op['host_readiness'].get('hosts',[])
        op['hardware_after']=next((h.get('hardware_identity',{}) for h in hosts if h.get('node')==op['new_node_name']),{})
        op['hardware_comparison']=compare(op.get('hardware_before',{}),op['hardware_after'])
        if op['host_readiness'].get('status')!='passed':self.save(op,'RDMA_FAILED');return self.result(op,'Replacement exists but host RDMA verification failed. Inspect evidence; no automatic replacement loop.')
        inv=self.p.inventory();row=next((r for r in inv['nodes'] if r['name']==op['new_node_name']),{})
        if not inv.get('driver_ready') or row.get('issues') or not any(d.get('valid') for d in row.get('devices',[])):
            self.save(op,'DEVICE_PENDING');return self.result(op,'Host checks passed; Dranet/device readiness still needs attention.')
        peers=[r['name'] for r in inv['nodes'] if r['name']!=op['new_node_name'] and r.get('mode')=='dranet' and r.get('ready') and not r.get('cordoned') and not r.get('issues') and not r.get('host_issues') and any(d.get('valid') and not d.get('allocated') for d in r.get('devices',[]))]
        if not peers or not any(d.get('valid') and not d.get('allocated') for d in row.get('devices',[])):
            self.save(op,'FUNCTIONAL_TEST_PENDING');return self.result(op,'Host/device checks passed. Cross-node test requires a second healthy free worker; functional verification is pending.')
        self.p.report_progress('Measuring replacement against a free peer',3,4,peers[0])
        self.save(op,'FUNCTIONAL_TEST')
        op['validation']=self.p.rdma_validation(op['new_node_name'],peers[0],'quick')
        self.save(op,'COMPLETE' if op['validation'].get('status')=='passed' else 'RDMA_FAILED')
        return self.result(op,'Replacement verified.' if op['phase']=='COMPLETE' else 'Cross-node validation failed; review evidence before further action.')
    def close(self,ident):
        self.authorize();op=self.get(ident)
        if op['phase'] in TERMINAL:return self.result(op,'Already closed.')
        safe=op['submission'] in {'NOT_SENT','REJECTED'} or (op.get('guard_removed') and op.get('new_instance_id'))
        if not safe and op.get('work_request_id'):
            work=self.gateway.work(op['work_request_id'])
            pool,_,_=self.snapshot()
            safe=work.get('status') in {'FAILED','CANCELED'} and pool.get('lifecycle_state')=='ACTIVE' and sorted(n['id'] for n in pool.get('nodes',[]) if n.get('lifecycle_state')=='ACTIVE')==op['before_ids']
        if not safe:raise ProviderError('Replacement may still be in flight or its outcome is unknown. Resume/reconcile OCI evidence; closing cannot bypass an uncertain deletion.')
        if not op.get('guard_removed'):self.cleanup_guard(op)
        op['outcome']='Closed for manual review; not a successful RDMA replacement. Original worker, if present, remains cordoned.'
        self.save(op,'CLOSED');return self.result(op,op['outcome'])
