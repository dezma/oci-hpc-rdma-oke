#!/usr/bin/env python3
"""Ephemeral SSH launch inside owned test pods; no host SSH or Kubernetes tokens."""
import argparse,ipaddress,json,os,subprocess,time,math
from pathlib import Path

def validate_result(result, ranks):
    rows=result.get('samples',[])
    expected={(op,size) for op in ['Allreduce','Alltoall'] for size in [8,1024,65536]} | {('Barrier',0)}
    if result.get('status')!='passed' or result.get('ranks')!=ranks or len(set(result.get('workers',[])))!=ranks or len(rows)!=7 or {(s.get('operation'),s.get('bytes_per_peer_or_rank')) for s in rows}!=expected:
        raise RuntimeError('MPI returned incomplete rank or collective evidence')
    if not all(isinstance(s.get(k),(int,float)) and math.isfinite(s[k]) and s[k]>0 for s in rows for k in ['mean_us','p99_us','max_us']):
        raise RuntimeError('MPI returned invalid timing values')
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--namespace',required=True);ap.add_argument('--participants',required=True);ap.add_argument('--tos',type=int,default=41)
    args=ap.parse_args();members=json.loads(args.participants)
    if not 2<=len(members)<=8:raise ValueError('MPI needs 2–8 workers')
    base=['kubectl','--kubeconfig',os.environ.get('KUBECONFIG','/etc/oke-rdma-control/kube/config'),'-n',args.namespace]
    env={**os.environ,'KUBECTL_KUBERC':'false'}
    def run(pod,cmd,stdin=None,timeout=20):
        r=subprocess.run(base+['exec','-i',pod,'--',*cmd],input=stdin,text=True,capture_output=True,timeout=timeout,env=env)
        if r.returncode:raise RuntimeError((r.stderr+r.stdout)[-3000:])
        return r.stdout
    def put(pod,path,data):run(pod,['sh','-c','cat > '+path],data)
    leader=members[0]['pod'];ips=[];keys=[]
    try:
        for member in members:
            pod=member['pod']
            r=subprocess.run(base+['get','pod',pod,'-o','json'],text=True,capture_output=True,timeout=15,env=env,check=True)
            doc=json.loads(r.stdout)
            if doc['metadata'].get('labels',{}).get('app')!='oke-dranet-validation' or doc['spec'].get('hostNetwork') or doc['spec'].get('hostPID'):raise RuntimeError('Unexpected MPI pod ownership or host access')
            ip=str(ipaddress.ip_address(doc['status']['podIP']));ips.append(ip)
            run(pod,['sh','-c','command -v mpirun && command -v sshd && python3 -c "import mpi4py" && mkdir -p /tmp/oke-mpi /run/sshd && chmod 700 /tmp/oke-mpi && ssh-keygen -q -t ed25519 -N "" -f /tmp/oke-mpi/host && usermod -p NP root'])
            keys.append(run(pod,['cat','/tmp/oke-mpi/host.pub']).strip())
            put(pod,'/tmp/oke-mpi/device',member['device'])
            put(pod,'/tmp/oke-mpi/collectives.py',Path(__file__).with_name('mpi_collectives.py').read_text())
        run(leader,['ssh-keygen','-q','-t','ed25519','-N','','-f','/tmp/oke-mpi/id'])
        public=run(leader,['cat','/tmp/oke-mpi/id.pub'])
        known='\n'.join('['+ip+']:2222 '+key for ip,key in zip(ips,keys))+'\n'
        put(leader,'/tmp/oke-mpi/known_hosts',known)
        for member,ip in zip(members,ips):
            pod=member['pod'];put(pod,'/tmp/oke-mpi/authorized_keys',public)
            config=f'''Port 2222
ListenAddress {ip}
HostKey /tmp/oke-mpi/host
PidFile /tmp/oke-mpi/sshd.pid
AuthorizedKeysFile /tmp/oke-mpi/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
UsePAM no
StrictModes yes
AllowTcpForwarding no
X11Forwarding no
'''
            put(pod,'/tmp/oke-mpi/sshd_config',config)
            run(pod,['sh','-c','chmod 600 /tmp/oke-mpi/authorized_keys && /usr/sbin/sshd -t -f /tmp/oke-mpi/sshd_config && (timeout 360 /usr/sbin/sshd -D -e -f /tmp/oke-mpi/sshd_config > /tmp/oke-mpi/sshd.log 2>&1 < /dev/null &)'])
        ssh=['ssh','-p','2222','-i','/tmp/oke-mpi/id','-o','UserKnownHostsFile=/tmp/oke-mpi/known_hosts','-o','StrictHostKeyChecking=yes','-o','BatchMode=yes','-o','ConnectTimeout=5']
        for ip in ips:run(leader,[*ssh,ip,'true'])
        command=['timeout','180','mpirun','--allow-run-as-root','--bind-to','none','--map-by','ppr:1:node','-np',str(len(members)),'--host',','.join(ip+':1' for ip in ips),'--mca','plm_rsh_agent',' '.join(ssh),'--mca','plm_rsh_no_tree_spawn','1','--mca','pml','ucx','-x','UCX_TLS=rc,self,sm','-x','UCX_IB_TRAFFIC_CLASS='+str(args.tos),'python3','/tmp/oke-mpi/collectives.py']
        output=run(leader,command,timeout=190)
        result=json.loads(next(l.split('=',1)[1] for l in output.splitlines() if l.startswith('MPI_RESULT=')))
        validate_result(result,len(members))
        print(json.dumps({'profile':'mpi',**result}));return 0
    except Exception as exc:
        print(json.dumps({'profile':'mpi','status':'failed','error':str(exc)}));return 1
    finally:
        # Provider always deletes all owned pods/claims; remote services also have a deadline.
        for member in members:
            try:run(member['pod'],['sh','-c','test ! -f /tmp/oke-mpi/sshd.pid || kill "$(cat /tmp/oke-mpi/sshd.pid)"'],timeout=5)
            except Exception:pass
if __name__=='__main__':raise SystemExit(main())
