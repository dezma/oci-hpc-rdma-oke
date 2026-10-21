#!/usr/bin/env python3
"""One rank per claimed worker. Fixed sizes, warmup and bounded collectives."""
import os
from pathlib import Path
os.environ['UCX_NET_DEVICES']=Path('/tmp/oke-mpi/device').read_text().strip()+':1'
os.environ['UCX_TLS']='rc,self,sm'  # fail rather than silently select TCP for MPI payload
from mpi4py import MPI
import array,json,statistics,socket
comm=MPI.COMM_WORLD
rank=comm.rank
workers=comm.allgather(socket.gethostname())
if len(set(workers)) != comm.size or not 2 <= comm.size <= 8:
    comm.Abort(2)
rows=[]
for operation in ['Allreduce','Alltoall','Barrier']:
    for count in ([0] if operation=='Barrier' else [1,128,8192]):
        send=array.array('d',[float(rank+1)])*(count*(comm.size if operation=='Alltoall' else 1))
        recv=array.array('d',[0.])*len(send)
        values=[]
        for iteration in range(110):
            comm.Barrier();start=MPI.Wtime()
            if operation=='Allreduce':comm.Allreduce(send,recv,op=MPI.SUM)
            elif operation=='Alltoall':comm.Alltoall(send,recv)
            else:comm.Barrier()
            elapsed=comm.allreduce((MPI.Wtime()-start)*1e6,op=MPI.MAX)
            if iteration>=10:values.append(elapsed)
        valid=True
        if operation=='Allreduce':valid=all(x==comm.size*(comm.size+1)/2 for x in recv)
        elif operation=='Alltoall':valid=all(recv[r*count+i]==r+1 for r in range(comm.size) for i in range(count))
        if not comm.allreduce(valid,op=MPI.LAND):comm.Abort(3)
        values.sort()
        rows.append({'operation':operation,'bytes_per_peer_or_rank':count*8,'iterations':100,'mean_us':statistics.mean(values),'p99_us':values[98],'max_us':max(values)})
if rank==0:
    print('MPI_RESULT='+json.dumps({'status':'passed','ranks':comm.size,'workers':workers,'samples':rows,'mpi_version':MPI.Get_library_version(),'transport':'Open MPI UCX PML; UCX_TLS=rc,self,sm; per-worker NIC explicitly selected; no TCP payload fallback','note':'Maximum rank time per collective; 10 warmups and 100 samples. One rank per worker. Includes collective synchronization costs; not application throughput.'}),flush=True)
