// DOM interaction smoke test. A rendered browser pass is separately required before production rollout.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'control/web/index.html'),'utf8');
assert.doesNotMatch(html,/href="\/guides\//);
const script=fs.readFileSync(path.join(root,'control/web/app.js'),'utf8');
const env={environment_name:'test-hpc',region:'eu-frankfurt-1',cluster_name:'test-cluster',mode:'demo',dranet_version:'v1.5.0',
  bastion_ssh_ttl_seconds:7200,bastion_port_forwarding_ttl_seconds:7200,rdma:{rdma_pf_name:'ens800f0np0',rdma_child_cidr:'10.208.0.0/12',rdma_tools_image:'repo@sha256:'+'a'.repeat(64),rdma_image_pull_secret:'',rdma_mtu:4220,rdma_tos:41},
  nodes:[10,11].map(x=>({name:`worker-${x}`,mode:'dranet',ready:true,cordoned:false,shape:'BM.Optimized3.36',zone:'AD1',issues:[],host_issues:[],host_check_at:100,devices:[{name:'pf',allocated:false,attributes:{'dra.net/rdmaDevice':'mlx5_2','dra.net/ipv4':`10.224.0.${x}/12`}}]})),claims:[],issues:[],driver_installed:true,driver_ready:true,device_class:true,capacity:{free:2,total:2,claims:0},observed_at:100};
const jobs=[],requests=[],errors=[],holds=new Map();
function hold(url){let release;const promise=new Promise(r=>release=r);holds.set(url,promise);return ()=>{holds.delete(url);release();};}let resetBlocked=true,connectionFailure=false;const capacity={configured:false,stack_id:'',minimum:0,maximum:64,operation:null};
const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://hpc.test/'}),w=dom.window;
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
w.fetch=async(url,opt={})=>{requests.push([url,opt]);if(holds.has(url))await holds.get(url);let data={};
if(url==='/api/v1/capacity/connection'&&connectionFailure)return {ok:false,status:502,json:async()=>({detail:{message:'Stack does not own this cluster.',field:'stack_id',code:'stack_ownership',next_action:'Use its provisioning stack.'}})};
if(url==='/api/v1/capacity')data=capacity;
else if(url.startsWith('/api/v1/capacity/readiness?'))data={target:Number(url.split('=')[1]),checked_at:Date.now()/1000,can_plan:true,capacity_report:{shape:'BM.Optimized3.36',region:'eu-frankfurt-1',checked_at:100},checks:[{name:'OCI scale-up capacity report',status:'pass',detail:'AD1: AVAILABLE; count Not disclosed'},{name:'Ownership',status:'pass',detail:'Verified'},{name:'Write IAM',status:'unverified',detail:'Not proven by read access'}]};
else if(url==='/api/v1/capacity/connection'){capacity.configured=true;capacity.stack_id=JSON.parse(opt.body).stack_id;capacity.desired=3;capacity.ready=3;data={saved:true};}
else if(url==='/api/v1/reset-plan')data={can_reset:!resetBlocked,plan_id:'0'.repeat(64),targets:[{kind:'DaemonSet',namespace:'kube-system',name:'dranet'}],local_files:['host-readiness.json'],retained:['Terraform infrastructure','Pull secrets'],blockers:resetBlocked?['Application claim in use']:[]};
else if(url==='/api/v1/session')data={ok:true};
else if(url==='/api/v1/environment')data=env;
else if(url==='/api/v1/diagnostics/profiles')data={image_configured:true,profiles:[{name:'full-rdma-support-bundle',title:'Full RDMA support bundle',description:'Full diagnosis.',timeout_seconds:180},{name:'dranet-readiness',title:'Dranet host readiness',description:'Read-only host check.',timeout_seconds:90}]};
else if(url==='/api/v1/jobs'&&opt.method==='POST'){const b=JSON.parse(opt.body);data={id:'test-id',action:b.action,state:'QUEUED',requested_at:100,payload:b};jobs.unshift(data);}
else if(url==='/api/v1/jobs')data={items:jobs};
else if(url==='/api/v1/rdma/config'){Object.assign(env.rdma,JSON.parse(opt.body));data={saved:true};}
else if(url==='/api/v1/operations/events')data={items:[{object:'pod',namespace:'rdma-workloads',reason:'FailedScheduling',message:'A free claim is needed.',count:1}]};
else if(url==='/api/v1/preflight')data={blocking:0,checks:[{name:'Hosts',status:'pass',detail:'Current'}]};
else throw new Error('Unexpected API '+url);
return {ok:true,status:200,json:async()=>data};};
w.addEventListener('error',e=>errors.push(e.message));w.eval(script+'\nwindow.testArrange=arrangeWorkspace;window.testRenderJob=renderJob;');
// New features may be added after maintenance in markup; layout still keeps it last.
const futureLink=w.document.createElement('a');futureLink.href='#future';futureLink.innerHTML='Future feature <span>99</span>';
w.document.querySelector('.sidebar nav').append(futureLink);
const futureSection=w.document.createElement('section');futureSection.id='future';futureSection.className='section';
w.document.querySelector('main').append(futureSection);w.testArrange();
assert.equal(w.document.querySelector('.sidebar nav').lastElementChild.getAttribute('href'),'#maintenance');
assert.equal([...w.document.querySelectorAll('main > section')].at(-1).id,'maintenance');
assert.equal(w.document.querySelector('.sidebar nav').lastElementChild.querySelector('span').textContent,'06');
futureLink.remove();futureSection.remove();w.testArrange();
assert.equal(w.document.querySelector('.sidebar nav').lastElementChild.querySelector('span').textContent,'05');
const oldResult=w.testRenderJob({action:'rdma_validation',state:'SUCCEEDED',requested_at:100,result:{status:'passed',average_gbps:97.99}});
assert.match(oldResult,/Bandwidth-only result/);assert.match(oldResult,/Run RDMA validation to measure both/);
assert(elProfileOptions());
function elProfileOptions(){return ['matrix','numa','mpi','sweep','loaded'].every(v=>w.document.querySelector('#performance-profile option[value="'+v+'"]'));}
const mpiHtml=w.testRenderJob({action:'rdma_validation',state:'SUCCEEDED',requested_at:100,result:{status:'passed',extended:{profile:'mpi',status:'passed',ranks:2,transport:'UCX',samples:[{operation:'Allreduce',bytes_per_peer_or_rank:8,mean_us:3,p99_us:4}]}}});
assert.match(mpiHtml,/Allreduce/);assert.match(mpiHtml,/UCX/);
const startupHtml=w.testRenderJob({action:'rdma_validation',state:'SUCCEEDED',requested_at:100,result:{startup_timing:{pods:[{pod:'test',seconds:{scheduling:2,device_preparation:null},note:'Unavailable remains unknown'}]}}});
assert.match(startupHtml,/Unknown/);assert.match(startupHtml,/2.000/);
const tick=()=>new Promise(r=>setTimeout(r,30));const el=s=>w.document.querySelector(s);
const click=s=>el(s).click();const submit=s=>el(s).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
(async()=>{try{
el('#token').value='demo';submit('#login-form');await tick();
assert.equal(el('#app').hidden,false);assert.equal(el('#metric-free').textContent,'2');assert.equal(el('#demo-banner').hidden,false);
assert.equal(el('#overview').hidden,false);assert.equal(el('#workflow').hidden,true);
w.location.hash='#capacity';await tick();assert.equal(el('#fleet').hidden,false);assert.equal(el('#overview').hidden,true);assert.equal(el('#maintenance').hidden,true);
w.location.hash='#workflow';await tick();assert.equal(el('#workflow').hidden,false);assert.equal(el('#fleet').hidden,true);
assert.equal(el('#rdma-pf').disabled,true);assert.equal(el('#rdma-image').disabled,false);
const releasePreflight=hold('/api/v1/preflight');click('#preflight');assert.equal(el('#preflight').getAttribute('aria-busy'),'true');assert.equal(el('#preflight').disabled,true);click('#preflight');assert.equal(requests.filter(([url])=>url==='/api/v1/preflight').length,1);releasePreflight();await tick();assert.match(el('#preflight-result').textContent,/0 blockers/);
click('.inspect-node');await tick();assert.equal(el('#node-dialog').open,true);assert.match(el('#node-title').textContent,/worker-10/);click('#close-node');
el('#node-filter').value='173';el('#node-filter').dispatchEvent(new w.Event('input'));assert.equal(w.document.querySelectorAll('#nodes tr').length,1);
click('#load-events');await tick();assert.match(el('#events').textContent,/FailedScheduling/);
el('#rdma-image').value='repo@sha256:'+'b'.repeat(64);submit('#settings-form');await tick();assert.match(el('#settings-status').textContent,/Saved/);
el('#performance-profile').value='sweep';
click('[data-action="rdma_validation"]');assert.equal(el('#confirm-dialog').open,true);
el('#confirm-input').value='wrong';submit('#confirm-form');await tick();assert.match(el('#confirm-error').textContent,/exact confirmation/);
const releaseSubmit=hold('/api/v1/jobs'),releaseInventory=hold('/api/v1/environment');el('#confirm-input').value='test-hpc';submit('#confirm-form');assert.equal(el('#confirm-form button[type=submit]').disabled,true);assert.match(el('#confirm-error').textContent,/Submitting/);submit('#confirm-form');releaseSubmit();await tick();assert.equal(el('#confirm-dialog').open,false);assert.equal(el('#active-operation').hidden,false);releaseInventory();assert.equal(w.location.hash,'#workflow');
assert.match(el('[data-result="rdma_validation"]').textContent,/Queued/);assert.equal(el('[data-action="rdma_validation"]').disabled,true);
assert(requests.filter(([url,opt])=>url==='/api/v1/jobs'&&opt.method==='POST').length===1);
assert.equal(jobs[0].payload.performance_profile,'sweep');
// A running phase is visible globally and beside the workflow step.
jobs[0].state='RUNNING';jobs[0].started_at=Date.now()/1000-12;
jobs[0].progress={stage:'RDMA-CM latency',completed:3,total:5,detail:'8 bytes / 5,000 writes'};
click('#refresh');await tick();
assert.match(el('#active-operation').textContent,/RDMA-CM latency/);
assert.equal(el('#active-operation progress').value,3);
w.location.hash='#maintenance';await tick();assert.equal(el('#active-operation').hidden,false);assert.equal(el('#maintenance').hidden,false);w.location.hash='#workflow';await tick();
assert.match(el('#validation-state').textContent,/Running/);
jobs[0].state='SUCCEEDED';jobs[0].finished_at=Date.now()/1000;
jobs[0].result={status:'passed',average_gbps:79.5,measured_at:100,latency:{status:'passed',average_us:1.72,median_us:1.6,p99_us:2.5,measured_at:100}};
env.measurement_status={validation_current:true,measurement:{job_id:'test-id',measured_at:Date.now()/1000,nodes:['worker-10','worker-11'],average_gbps:79.5,latency:jobs[0].result.latency,freshness:'current',reasons:[],scope:'Pair only'}};
click('#refresh');await tick();
assert.equal(el('#active-operation').hidden,true);
assert.match(el('#metric-latency').textContent,/1.72 µs/);
assert.match(el('[data-result="rdma_validation"]').textContent,/Completed/);
assert.match(el('[data-result="rdma_validation"]').textContent,/p99 2.50 µs/);
// Earlier failures are collapsed and inspection survives a status refresh.
jobs.push({id:'old-failure',action:'rdma_validation',state:'FAILED',requested_at:50,error:'Historical startup failure'});
click('#refresh');await tick();assert.doesNotMatch(el('#jobs-list').textContent,/Historical startup failure/);
click('#show-history');await tick();assert.equal(el('.history-job').open,false);el('.history-job').open=true;
click('#refresh');await tick();assert.equal(el('.history-job').open,true);click('#show-history');
// First-time setup is neutral and validation is gated on prerequisites.
env.measurement_status={validation_current:false,measurement:null};jobs.length=0;env.driver_ready=false;env.driver_installed=false;env.capacity={free:0,total:0,claims:0};env.rdma.rdma_tools_image='';
env.nodes.forEach(n=>{n.devices=[];n.host_check_at=null;n.host_issues=['Run host readiness once.'];});
click('#refresh');await tick();
assert.equal(el('#metric-issues').textContent,'0');
assert.equal(el('#setup-notice').hidden,false);
assert.match(el('#setup-notice').textContent,/expected setup state/);
assert.equal(el('#driver-status').textContent,'Setup pending');
assert.match(el('#validation-state').textContent,/Waiting for setup/);
assert.equal(el('[data-action="rdma_validation"]').disabled,true);
assert.equal(el('[data-action="install_dranet"]').disabled,true);
assert.match(el('#validation-capacity').textContent,/Complete configuration/);
// Reset requires a removal preview and the stronger typed confirmation.
const releasePreview=hold('/api/v1/reset-plan');click('#preview-reset');assert.equal(el('#confirm-dialog').open,true);assert.match(el('#confirm-preview').textContent,/Loading/);assert.equal(el('#confirm-form button[type=submit]').disabled,true);releasePreview();await tick();
assert.match(el('#confirm-preview').textContent,/Application claim in use/);
assert.equal(el('#confirm-form button[type=submit]').disabled,true);click('#cancel-confirm');
resetBlocked=false;click('#preview-reset');await tick();
assert.match(el('#confirm-preview').textContent,/DaemonSet/);
assert.match(el('#confirm-label').textContent,/RESET test-hpc/);
el('#confirm-input').value='test-hpc';submit('#confirm-form');await tick();
assert.match(el('#confirm-error').textContent,/exact confirmation/);
el('#confirm-input').value='RESET test-hpc';submit('#confirm-form');await tick();
const resetRequest=requests.findLast(([url,opt])=>url==='/api/v1/jobs'&&opt.method==='POST');
assert.equal(JSON.parse(resetRequest[1].body).reset_plan_id,'0'.repeat(64));
jobs[0].state='SUCCEEDED';jobs[0].result={status:'passed'};jobs[0].finished_at=Date.now()/1000;
jobs.push({id:'old-result',action:'rdma_validation',state:'SUCCEEDED',requested_at:100,result:{status:'passed',average_gbps:79.5,measured_at:100,latency:{status:'passed',average_us:1.72,measured_at:100}}});
env.reset_at=Date.now()/1000;
click('#refresh');await tick();
assert.equal(el('#metric-bw').textContent,'—');assert.equal(el('#metric-latency').textContent,'—');
assert.equal(el('[data-result="rdma_validation"]').textContent,'');
assert.match(el('#jobs-list').textContent,/79.50/);assert.match(el('#validation-state').textContent,/Waiting for setup/);
// Capacity planning and applying remain separate, with explicit plan identity.
jobs.length=0;env.reset_at=0;env.driver_ready=true;env.driver_installed=true;
connectionFailure=true;el('#capacity-stack-id').value='ocid1.ormstack.oc1.region.wrong';el('#capacity-stack-id').dispatchEvent(new w.Event('input'));el('#capacity-connect-confirm').value='test-hpc';submit('#capacity-connection-form');await tick();
assert.match(el('#stack-id-error').textContent,/does not own/);assert.equal(el('#stack-confirm-error').textContent,'');assert.equal(el('#capacity-stack-id').value,'ocid1.ormstack.oc1.region.wrong');connectionFailure=false;
el('#capacity-stack-id').value='ocid1.ormstack.oc1.region.example';el('#capacity-connect-confirm').value='test-hpc';submit('#capacity-connection-form');await tick();
assert.equal(el('#capacity-connection-state').textContent,'Connected');assert.equal(el('#capacity-plan').disabled,true);
el('#capacity-target').value='5';click('#capacity-readiness');await tick();assert.equal(el('#capacity-plan').disabled,false);assert.match(el('#capacity-readiness-result').textContent,/Not yet verified/);assert.match(el('#capacity-readiness-result').textContent,/BM.Optimized3.36/);assert.match(el('#capacity-readiness-result').textContent,/count Not disclosed/);click('#capacity-plan');
assert.match(el('#confirm-copy').textContent,/does not resize infrastructure/);click('#cancel-confirm');
capacity.operation={operation_id:'c'.repeat(64),phase:'AWAITING_REVIEW',before:3,target:5,plan_job_id:'plan-job',apply_job_id:'',expires_at:Date.now()/1000+1800,review:{changes:[{before:3,after:5}]}};
click('#capacity-refresh');await tick();
assert.equal(el('#capacity-apply').hidden,false);assert.equal(el('#capacity-plan').disabled,true);
assert.equal(el('#preview-reset').disabled,true);assert.equal(el('#save-settings').disabled,true);
click('#capacity-apply');assert.match(el('#confirm-label').textContent,/SCALE test-hpc/);
el('#confirm-input').value='test-hpc';submit('#confirm-form');await tick();assert.match(el('#confirm-error').textContent,/exact confirmation/);
el('#confirm-input').value='SCALE test-hpc';submit('#confirm-form');await tick();
const scaleRequest=requests.findLast(([url,opt])=>url==='/api/v1/jobs'&&opt.method==='POST');
assert.equal(JSON.parse(scaleRequest[1].body).capacity_operation_id,'c'.repeat(64));
assert.equal(JSON.parse(scaleRequest[1].body).action,'capacity_apply');
// Recovery requires an assessment, stronger confirmation and preserves attempt identity.
jobs.length=0;capacity.operation=null;env.mode='live';el('#node-filter').value='';
jobs.unshift({id:'assessment',action:'recovery_assess',state:'SUCCEEDED',requested_at:Date.now()/1000,payload:{node_name:'worker-10'},result:{eligible:true,preview_id:'a'.repeat(32),probe:{metadata_http:200},action:'Restart OCA once',protection:'Cordon and admission guard'}});
click('#refresh');await tick();assert(el('#host-evidence .inspect-node'));click('#host-evidence .inspect-node');await tick();assert.equal(el('#node-title').textContent,'worker-10');assert.equal(el('#diagnostic-profile').value,'full-rdma-support-bundle');
assert.equal(el('#assess-recovery').disabled,false);click('#assess-agent-restart');assert.match(el('#confirm-copy').textContent,/explicit single-worker Oracle Cloud Agent restart/);click('#cancel-confirm');click('#run-recovery');
assert.match(el('#confirm-label').textContent,/RECOVER test-hpc/);
assert.match(el('#confirm-copy').textContent,/worker-10/);
el('#confirm-input').value='test-hpc';submit('#confirm-form');await tick();assert.match(el('#confirm-error').textContent,/exact confirmation/);
el('#confirm-input').value='RECOVER test-hpc';submit('#confirm-form');await tick();
const recoveryRequest=requests.findLast(([url,opt])=>url==='/api/v1/jobs'&&opt.method==='POST');
assert.equal(JSON.parse(recoveryRequest[1].body).recovery_preview_id,'a'.repeat(32));
jobs.length=0;env.recoveries=[{node_name:'worker-10',id:'recorded-id',phase:'ATTENTION'}];
click('#refresh');await tick();assert.equal(el('#assess-recovery').disabled,true);click('#finish-recovery');assert.match(el('#confirm-label').textContent,/FINISH test-hpc/);click('#cancel-confirm');
assert.match(w.testRenderJob({action:'node_diagnostic',state:'SUCCEEDED',requested_at:100,result:{collection_status:'passed',health_status:'needs_attention',findings:[{severity:'block',message:'Selected PF down'}]}}),/Diagnostic collection: completed/);
assert.match(w.testRenderJob({action:'node_diagnostic',state:'SUCCEEDED',requested_at:100,result:{collection_status:'passed',health_status:'needs_attention'}}),/needs_attention/);
env.recoveries=[];env.mode='demo';click('#close-node');
env.performance={scope:'Directional retained evidence',pairs:[{server:'worker-10',client:'worker-11',state:'not_measured'}]};
click('#refresh');await tick();assert.match(el('#pair-performance').textContent,/not measured/);assert.doesNotMatch(el('#pair-performance').textContent,/0.00/);
// Replacement controls survive disappearance of the selected worker.
env.mode='live';env.replacement={id:'a'.repeat(32),phase:'WAITING_FOR_OCI',node_name:'gone-worker',instance_id:'ocid1.instance.example',size:2,submission:'ACCEPTED',work_request_id:'work'};
click('#refresh');await tick();assert.match(el('#replacement-operation').textContent,/gone-worker/);
click('#resume-replacement');assert.match(el('#confirm-title').textContent,/Resume replacement/);click('#cancel-confirm');
assert.equal(el('[data-action="rdma_validation"]').disabled,true);
assert.doesNotMatch(w.testRenderJob({action:'replacement_run',state:'SUCCEEDED',requested_at:100,result:{status:'pending',replacement:env.replacement}}),/Operation completed/);
env.replacement=null;env.mode='demo';
// Empty worker pools remain navigable and never appear RDMA-ready.
jobs.length=0;capacity.operation=null;env.nodes=[];env.driver_ready=false;
click('#refresh');await tick();
assert.equal(el('#driver-status').textContent,'Waiting for workers');
assert.equal(el('#validation-state').textContent,'Requires two workers');
assert.equal(el('[data-action="rdma_validation"]').disabled,true);
assert.match(el('#next-action').textContent,/Add worker capacity/);
el('#capacity-target').value='0';el('#capacity-target').dispatchEvent(new w.Event('input'));
click('#capacity-readiness');await tick();
assert(requests.some(([url])=>url==='/api/v1/capacity/readiness?target=0'));
Object.assign(capacity,{configured:true,desired:6,observed:5,ready:5,counts:{requested:6,provisioned:5,registered:5,ready:5},reconciliation_needed:true,operation:{phase:'CLOSED_FAILED',outcome:'failed',before:3,target:6,readiness:'not_verified',observed_capacity:{requested:6,provisioned:5,registered:5,ready:5}}});
click('#capacity-refresh');await tick();
assert.equal(el('#capacity-provisioned').textContent,'5');
assert.equal(el('#capacity-registered').textContent,'5');
assert.match(el('#capacity-operation').textContent,/FAILED APPLY ACKNOWLEDGED/);
assert.match(el('#capacity-operation').textContent,/not rolled back/);
assert.doesNotMatch(el('#capacity-operation').textContent,/Operation finished/);
assert.equal(el('#capacity-target').disabled,false);
assert.equal(el('#capacity-plan').textContent,'Generate reconciliation plan');
assert.equal(errors.length,0);click('#logout');assert.equal(el('#login').hidden,false);
console.log('UI DOM smoke: onboarding, real phase progress, completion, latency, prerequisites, login, configuration, readiness, node details, filtering, events, confirmation, inline jobs, duplicate prevention, logout passed.');
}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

const evidenceHtml=w.testRenderJob({action:'node_diagnostic',state:'SUCCEEDED',requested_at:100,result:{collection_status:'passed',health_status:'needs_attention',findings:[{severity:'block',message:'Physical link unavailable',evidence:'Link detected: no (No cable) <script>',next_action:'Compare compute-cluster membership'}]}});
assert.match(evidenceHtml,/No cable/);assert.match(evidenceHtml,/Compare compute-cluster membership/);assert.match(evidenceHtml,/&lt;script&gt;/);

const pullFailure=w.testRenderJob({action:'node_diagnostic',state:'FAILED',requested_at:100,result:{collection_status:'failed',health_status:'not_established',findings:[{severity:'block',code:'registry_authentication_failed',message:'Registry authentication failed: RDMA was not tested.',next_action:'Update ocir-pull and rerun.'}]}});
assert.match(pullFailure,/Registry authentication failed/);
assert.match(pullFailure,/Update ocir-pull and rerun/);
assert.doesNotMatch(pullFailure,/Collection completed/);
