# Performance measurement guide

## Before running tests

Complete [image and registry setup](IMAGE-PULL-SECRET.md) on the operator VM and save the pushed immutable digest in the configuration. Verify MPI/UCX, SSH tools and numactl are present before pushing. Keep the pull secret configured in both namespaces.

Run host readiness and ensure Dranet devices are valid/free. Stop application traffic on test
workers for extended profiles. Tests consume exclusive physical NIC claims but do not reserve
exclusive CPUs. Do not interpret shared CPU contention as proven fabric degradation.

## Feature checklist and how to use it

| Requested feature | UI workflow | Evidence and limitations |
| --- | --- | --- |
| Worker-to-worker matrix | Setup & validation → Measure cross-node RDMA → All free worker pairs | Sequential directional bandwidth and latency for 2–8 free workers. Stops on first failure, preserves returned partial results. Two-hour budget. Untested pairs stay unmeasured. |
| Regression against baseline | Pair performance and baseline comparison | Earliest comparable retained successful run, percentage changes and recorded versions/settings. A previous run with changed conditions is shown separately as a descriptive reference, never a controlled regression baseline. New node names may have no reference. |
| Time until usable RDMA | Workers & scaling → capacity operation timeline | Apply submission, apply completion observation, Kubernetes Ready, host RDMA readiness and device readiness. Time to verified capacity requires a successful bandwidth+latency pair run covering each new node UID/boot. Missing historical stages remain unknown. |
| Latency stability under load | Latency under concurrent RDMA traffic | Idle versus loaded mean, median, p99, p99.9, standard deviation and maximum where perftest exposes them. Positive background traffic and enclosing process intervals are required. Continuous saturation is not established. |
| Message-size behavior | Message-size sweep | Bandwidth and latency at 8, 64, 512, 4096, 65536 and 1048576 bytes. Reported latency is perftest half RTT, not independently measured one-way latency. |
| MPI collectives | MPI collectives | Automatic endpoints use up to eight available workers (first eight in discovered order); choosing both endpoints uses that pair. One rank per worker. Allreduce, Alltoall and Barrier; 10 warmups and 100 samples, maximum rank time per sample, mean/p99/max. Sizes: 8, 1024 and 65536 bytes per rank for Allreduce or per peer for Alltoall. |
| NUMA placement penalty | NUMA local versus remote placement | Both endpoints run bandwidth and latency with a CPU and memory node local to their NIC, then remote. Actual CPU/NUMA bindings are recorded. Reports combined endpoint placement effects, not an isolated memory-latency measurement. |
| ResourceClaim-to-workload startup | Every validation result → Claim-to-container startup | API creation/scheduling/sandbox/container/Ready timestamps and first observed allocation, sampled every two seconds plus API latency. This is the test container startup, not arbitrary application readiness. |

## Run and interpret the matrix

1. Select **All free worker pairs** and review the confirmation scope. Endpoint selectors do not
   restrict this profile: it tests all currently free healthy workers (maximum eight).
2. Run the test. Each direction creates and removes its own pair of pods and ResourceClaims.
3. Inspect the grid and directional table. Compare bandwidth and latency alongside freshness,
   profile and baseline conditions. A missing cell is not zero performance.
4. If a pair fails, inspect that result and run owned validation cleanup if required. Previously
   completed pairs are retained in the returned job result; an interrupted operator process may
   lose in-memory partial samples. A new sweep is an explicit new measurement, not an automatic retry.

## MPI launch and transport

MPI uses Open MPI with the UCX PML and UCX_TLS=rc,self,sm. Each rank selects its worker's
recorded RDMA device; the suite fails if the required UCX transport cannot initialize, rather
than silently falling back to TCP MPI payload transport. SSH/control-plane traffic uses the
pod network. The MPI hosts list does not select payload transport.

Temporary SSH servers listen only in the owned test pods on port 2222. An ephemeral public key
is installed only in those pods; the private key remains in the leader pod. Host keys are read
through the authenticated Kubernetes API, and SSH strict host-key checking remains enabled.
No worker host SSH access, host filesystem mounts, Kubernetes service-account tokens or host
networking are introduced. MPI pods add SETUID, SETGID, SYS_CHROOT, CHOWN and DAC_OVERRIDE for
sshd; they remain non-privileged. The extra capabilities apply only to MPI test pods. Pod-network
policy must permit pod-to-pod SSH and Open MPI control traffic among test workers.

Each collective verifies received data where applicable. A successful basic pair benchmark
with failed MPI remains a failed extended profile, not a successful MPI result. Clean up owned
validation resources after interruption. Services and pods have bounded deadlines.

## NUMA requirements

NUMA pods add SYS_NICE for memory-policy operations. The suite uses the container's allowed
CPU set and allowed memory nodes and the NIC's sysfs NUMA identity. It must find local and remote
placements and successfully apply numactl CPU and memory binding. Unknown topology, restricted
cpusets, missing numactl or denied memory policy returns an explicit test failure; no synthetic
penalty is reported. Host configuration and affinity are not changed. CPU contention can still
influence results; repeat tests under comparable load.

## Timing boundaries

ResourceClaim allocation is first observed by polling: it is an upper bound, not an exact
allocation-transition timestamp. PodReadyToStartContainers includes sandbox/network and other
preparation; an independent DRA NodePrepareResources duration is not exposed by this implementation.
Missing or inconsistent timestamps remain Unknown. Application-level readiness is not inferred
from a sleeping validation container. Failure results retain the latest startup observation and
pod status, so allocation/startup blockers can be distinguished from benchmark failures.

Capacity timing is based on operation milestones and retained validation jobs. Apply completion
includes ORM orchestration, provisioning and bootstrap; these are not separated into invented
service durations. New-node registration and Ready API timestamps are retained as operation
node_stage_evidence. RDMA authentication start/end is not independently timed. After scaling,
run a matrix or enough pair tests to cover all new workers; until then time-to-verified remains
unmeasured. A single-worker pool cannot establish cross-node RDMA performance.

## Validation status

Validate each enabled profile in your own cluster. Results depend on the image, shape, placement, runtime policy and current load.

References:
- https://docs.open-mpi.org/en/v5.0.5/launching-apps/ssh.html
- https://docs.open-mpi.org/en/v5.0.7/tuning-apps/networking/ib-and-roce.html
- https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
