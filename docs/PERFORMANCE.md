# Performance measurement guide

## Before running tests

Complete [image and registry setup](IMAGE-PULL-SECRET.md), verify the image contains MPI/UCX, SSH and `numactl`, and save its pushed digest. Keep the pull secret in both namespaces.

Run host readiness and check that Dranet devices are valid and free. Stop application traffic on the test workers before extended profiles. NIC claims reserve the RDMA device; CPU contention can still affect results.

## Choose a test

| Profile or view | What it measures | Scope |
| --- | --- | --- |
| Quick pair check | Bandwidth at 64 KiB and latency at 8 bytes, 5,000 writes each | Two free workers, RDMA-CM, one RC queue pair and the configured traffic class |
| All free worker pairs | Directional bandwidth and latency across the fleet | Two to eight workers; stops at the first failure or the two-hour limit |
| Message-size sweep | Bandwidth and latency at 8, 64, 512, 4096, 65536 and 1048576 bytes | Selected pair |
| Latency under concurrent RDMA traffic | Idle and loaded mean, median, p99, p99.9, standard deviation and maximum, where perftest reports them | Records background traffic and checks that its process spans the latency test; traffic intensity can vary |
| NUMA local versus remote placement | Bandwidth and latency with CPU and memory near the NIC, then on a remote NUMA node | Records bindings on both endpoints; the difference combines their placement effects |
| MPI collectives | Allreduce, Alltoall and Barrier timings | One rank per worker; automatic selection uses up to eight free workers, or choose both endpoints for a pair |
| Pair performance and baseline comparison | Change from the earliest comparable retained successful run | Matching worker identities, versions and test settings are required; runs with different conditions appear separately for reference |
| Claim-to-container startup | Claim creation/allocation and pod scheduling, sandbox, container and Ready times | Allocation is sampled every two seconds plus API latency |
| Capacity operation timeline | Apply, worker registration, Kubernetes readiness, host readiness and device publication | Verified-capacity timing also requires passing bandwidth and latency tests covering every new node UID/boot |

MPI runs 10 warmups and 100 samples per collective. Each sample records the slowest rank; results include mean, p99 and maximum. Allreduce uses 8, 1024 and 65536 bytes per rank. Alltoall uses those sizes per peer.

Latency uses perftest's half-round-trip estimate. p99 is the value at or below which 99% of samples fall. Compare results under similar load, placement and configuration. Use application tests to assess whether the fabric meets your workload's needs.

## Run and interpret the matrix

1. Select **Setup & validation → Measure cross-node RDMA → All free worker pairs**. This profile uses all eligible free workers, up to eight, regardless of the endpoint selectors.
2. Review the confirmation and run the test. Each direction creates and removes its own pods and ResourceClaims.
3. Compare bandwidth and latency alongside the test date, profile and baseline conditions. Blank cells mean the pair wasn't tested.
4. If a pair fails, inspect its result and clean up any retained validation resources. Completed pairs remain in the returned result. An operator interruption can lose samples still held in memory. Start a new sweep when ready.

## MPI launch and transport

MPI uses Open MPI's UCX PML with `UCX_TLS=rc,self,sm`. Each rank selects its worker's recorded RDMA device. Initialization fails if the required UCX transport is unavailable. SSH and MPI control traffic use the normal pod network.

Temporary SSH servers listen inside the test pods on port 2222. The leader holds an ephemeral private key; the public key is installed in the test pods. Host keys are retrieved through the authenticated Kubernetes API, and strict SSH host-key checking stays enabled.

MPI pods add SETUID, SETGID, SYS_CHROOT, CHOWN and DAC_OVERRIDE for `sshd`. They remain non-privileged and use neither host filesystem mounts nor host networking. Network policies must allow pod-to-pod SSH and MPI control traffic among the selected workers.

Each collective checks received data where applicable. An MPI error marks the extended profile failed even if the initial bandwidth test passed. Inspect `extended.error` and `extended.ssh_evidence` as appropriate. After an interruption, use validation cleanup before retrying.

## NUMA requirements

The NUMA profile changes CPU and memory binding inside the test pods. **No BIOS changes are needed.** It uses the allowed CPU set, allowed memory nodes and NIC locality reported through sysfs.

NUMA test pods add SYS_NICE for memory-policy operations. Both local and remote placements must be available, and `numactl` must successfully apply the requested bindings. Unknown topology, restricted CPU sets, missing packages or denied memory policy cause the test to fail. Review the reported reason before changing worker configuration.

Repeat comparisons under similar CPU load. The test records the combined effect of placement at both endpoints.

## Timing details

Claim allocation is recorded when the poller first sees it, so the measured delay includes polling overhead. `PodReadyToStartContainers` covers sandbox and network preparation. Startup results describe the validation container; measure application startup separately if your application needs additional initialization.

The implementation does not separately time DRA `NodePrepareResources` or RDMA authentication. Missing or inconsistent timestamps appear as Unknown. Failed runs retain the latest startup observations and pod status to help distinguish scheduling and image issues from benchmark failures.

Capacity timings use operation milestones and retained validation jobs. Apply duration includes Resource Manager orchestration, provisioning and bootstrap. Registration and Ready timestamps are stored in `node_stage_evidence`. After scaling, run enough pair tests or a matrix to cover all new workers. Verified-capacity timing remains unmeasured until those checks pass; a one-worker pool needs a second worker for cross-node testing.

## Scheduled tests

[Monitoring](MONITORING.md) can run one quick pair test per UTC day when enabled. It rotates eligible pairs and skips a day if fewer than two workers are available when the job runs. Results appear with manual measurements. Use a manual matrix when you need coverage of every eligible pair.

References:

- [Open MPI SSH launch](https://docs.open-mpi.org/en/v5.0.5/launching-apps/ssh.html)
- [Open MPI InfiniBand and RoCE transport](https://docs.open-mpi.org/en/v5.0.7/tuning-apps/networking/ib-and-roce.html)
- [Kubernetes pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
