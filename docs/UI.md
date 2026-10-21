# Web UI guide

Start after [deployment and private access](../README.md). Keep the workstation's Bastion tunnel open, open `http://127.0.0.1:18000`, enter the operator's admin token and select **Open workspace**. Check the environment name before making changes.

## 1. Prepare the tools image

Even if you use the UI for day-to-day operations, the image build and registry secret setup happen once on the operator VM. Follow [the complete image and registry procedure](IMAGE-PULL-SECRET.md), then return here with:

- The full pushed image reference ending in `@sha256:` plus 64 hexadecimal characters.
- The secret name `ocir-pull`, created in both `rdma-workloads` and `oke-rdma-diagnostics`.

Do not enter registry credentials in the web UI. Build verification must find `numactl`, MPI and SSH tools before you push.

## 2. Find your way around

| Page | Purpose |
| --- | --- |
| Overview | Current health, free slots and suggested next action |
| Setup & validation | Configure the image, check hosts, enable Dranet and run tests |
| Workers & scaling | Inspect individual workers, claims, capacity and scaling |
| Operations | Running and completed jobs, errors, evidence and reports |
| Maintenance | Review and reset RDMA setup when deliberately required |

A queued job is not complete. Follow its progress and inspect the result. Closing the browser does not cancel it. If a request times out, check Operations before submitting it again. Historical failed jobs remain visible; use the latest result and current health.

## 3. Save the workload configuration

Open **Setup & validation → Set the workload configuration**.

| Field | Enter | Meaning |
| --- | --- | --- |
| RDMA interface | `ens800f0np0` for the reference BM.Optimized3.36 profile | Host physical interface used by the RDMA fabric; do not substitute the ordinary network interface |
| Child IPv4 CIDR | `10.208.0.0/12` for the reference network design | Address range for pod IPvlan interfaces; it must not overlap VCN, service, pod or parent fabric ranges |
| Tools image | Your complete pushed `repository@sha256:…` value | Exact container contents used by diagnostics and benchmarks |
| Existing image pull secret | `ocir-pull` | Name only; never the auth token |
| RDMA traffic class | `41` | Benchmark fabric traffic-class setting |

MTU is fixed at 4220. Save and review readiness. Interface and child range become locked while the driver is installed; image and pull-secret settings remain editable. If configuration is blocked, read the displayed reason before proceeding.

**Checkpoint:** saved configuration shows your intended image digest and secret name.

## 4. Check the hosts

In step 2, run the host check and wait for its job. Each intended worker needs successful inventory and host readiness: selected physical interface, carrier/link, parent IPv4 address, MTU, shared RDMA mode and runtime support.

Kubernetes Ready is not the same as RDMA ready. Empty facts or an image-pull error mean collection failed; they do not prove faulty hardware. Open the result's visible Next step and download text if needed. Follow [diagnostic troubleshooting](DIAGNOSTIC-TROUBLESHOOTING.md).

For one worker, choose **Diagnose / recover this worker**, or open its **Details** under Workers & scaling. Run the full support bundle before attempting recovery. The worker dialog is available from both places.

**Checkpoint:** host readiness passes on the workers you intend to use.

## 5. Enable Dranet

In step 3, install or verify Dranet. Wait for completion and usable published devices. Dranet connects Kubernetes ResourceClaims to the RDMA interface.

If deployment uses restricted Kubernetes RBAC, a cluster administrator must install the infrastructure; follow [restricted access](DEPLOYMENT.md). Repeating the UI action does not grant administrator permissions.

**Checkpoint:** driver ready, devices valid, and at least two free workers for cross-node tests.

## 6. Measure performance

In step 4, leave both endpoint selectors on Automatic for a quick check, or select two distinct free workers. Choose **Quick pair check** and run validation. A successful result includes bandwidth, latency and cleanup of its test pods/claims.

| Profile | What it answers |
| --- | --- |
| Quick pair check | Can this worker pair perform RDMA, and what bandwidth/latency was measured? |
| Message-size sweep | How do bandwidth and latency vary with message size? |
| Latency under concurrent RDMA traffic | How does latency change while background RDMA traffic runs? |
| NUMA local versus remote placement | Does CPU/memory placement relative to the NIC affect results? No BIOS changes are needed. |
| MPI collectives | What are Allreduce, Alltoall and Barrier timings across the selected workers? |
| All free worker pairs | How does each direction compare across two to eight free workers? |

Automatic MPI uses up to eight free workers; choosing both endpoints limits MPI to the selected pair. Matrix ignores endpoint selectors and measures all ordered pairs of free workers, up to eight. Extended tests require idle endpoints and generate deliberate traffic. Review the confirmation scope before starting.

The matrix and detailed baseline table have separate panels. A blank/unmeasured cell is not zero performance. A comparable baseline requires matching test conditions; a changed-condition reference is informational. Host or configuration changes can make historical results stale.

Bandwidth is in Gbit/s. Latency is in microseconds and follows perftest's half-round-trip method. p99 describes a tail percentile, not the average. Higher bandwidth and lower latency are generally preferable under comparable conditions. No single threshold proves every application will perform well.

Read [performance measurement](PERFORMANCE.md) for each metric, startup/capacity timing boundaries and limitations. If bandwidth passed but MPI failed, MPI has not passed; inspect the extended-profile error.

## 7. Run your application

Select **Download workload YAML**. This downloads a complete ResourceClaim + Pod example to your workstation. Review its image, command, CPU/memory requests and claim before applying it. Replace the tools container with your application image and command as required.

Copy the complete file to the operator using your approved Bastion file-transfer route, or paste the nonsecret YAML into a file there. On the operator:

```bash
kubectl apply --dry-run=server -f rdma-workload.yaml
kubectl apply -f rdma-workload.yaml
kubectl -n rdma-workloads get pods,resourceclaims
```

Run apply only if dry-run succeeds. One independently allocated RDMA pod consumes one worker's NIC slot in this profile. Stop the owning application/controller before deleting its pod and releasing its claim. A Pending pod may be waiting for a slot, credentials or scheduling; inspect its events.

## 8. Scale worker capacity

Follow [capacity management](CAPACITY.md) to connect the owning Resource Manager stack and verify permissions. In Workers & scaling:

1. Enter the desired **total** worker count, not the number to add.
2. Select **Check capacity and scaling prerequisites**. Read every blocker. The capacity report uses the node pool's compartment and placement.
3. Generate a scale plan. This saves the desired count in the stack but does not yet resize workers.
4. Review the count change and confirm that only the node-pool size changes.
5. Review and apply the exact plan using the displayed typed confirmation.
6. Wait for infrastructure and host/device readiness, then run performance validation covering the new workers.

If you decide not to apply a plan, use Discard target. After apply submission, use Resume recorded operation to continue verification. Do not start a second operation in Resource Manager while the UI owns a pending one.

Scale-in requires application pods and RDMA claims/templates to be released, and controllers paused. Scaling to zero stops this pool's workloads but retains the cluster/operator. A capacity count is a snapshot, not a reservation or a promise of placement in a particular Compute Cluster.

## 9. Recover or replace a worker

Use worker Details to collect diagnostics first. **Preview Oracle Cloud Agent restart** shows restart eligibility, identity and impact; review before confirming. It restarts only the fixed agent service on that worker. It is not a fleet reboot or general service control. See [recovery](RECOVERY.md).

For a worker that remains defective, **Preview worker replacement** checks the node identity and workload restrictions before a separately confirmed replacement. Replacement is disruptive and keeps the intended pool count; verification needs a healthy free peer for cross-node testing. See [replacement](REPLACEMENT.md).

## 10. Troubleshoot and finish safely

Use [troubleshooting](TROUBLESHOOTING.md) for access, registry, host, benchmark and scaling problems. Download diagnostic text and retain the job ID before retrying. Do not include tokens or private keys in reports.

Maintenance reset is a deliberate removal of supported RDMA setup, not the first response to an error. Review its preview and stop/release applications first. Closing the UI does not stop billing; see [shutdown and cleanup](OPERATIONS.md).
