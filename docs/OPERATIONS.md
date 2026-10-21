# Operations reference

For a guided journey, use [Web UI walkthrough](UI.md) or [operator guide](OPERATOR-VM.md). Recovery controls and failure handling are documented in [RECOVERY.md](RECOVERY.md).

## Everyday operations

## Navigate the workspace

The workspace has five task pages: Overview, Setup & validation, Workers & scaling, Operations, and Maintenance. Deep links open the relevant page and workflow step. A running operation stays visible above every page with its phase, elapsed time and completed phase count.

Operations defaults to the latest attempt of each type. Enable **Include earlier attempts** to inspect retained history. Use the latest checks to assess current worker health. Worker onboarding separates Kubernetes readiness, Dranet device publication, and current host-check results. Newly provisioned OCI workers appear before Kubernetes registration when the stack connection is available.

## Review the fleet

Start with the overview's worker health, available RDMA slots, latest bandwidth and latency measurements, and attention queue. A new deployment shows setup pending until the guided workflow is complete. Operations show their current phase, completed phase count, elapsed duration, and final completion or failure state. Fleet details combine the NIC, parent address, node readiness and rack/network-block identifiers when Dranet publishes them. Use Workers & scaling to filter workers, inspect readiness and plan capacity changes.

Refresh inventory when needed. Job progress refreshes while an operation runs; completed operations refresh the relevant inventory. OCI agent health and warning events are queried on demand. On-demand host checks can be reused for 24 hours and are invalidated after a reboot. Scheduled monitoring has its own expiry and optional refresh settings; resolve a specific finding with the relevant diagnostic profile before repeating a benchmark.

## Manage workload capacity

A ResourceClaim reserves its device independently of pod phase. Inspect its namespace, consumers, allocation and `reservedFor` references before removal. The UI's cleanup action only deletes resources labelled as its own validation workloads.

For the supplied `rdma-example`, stop the pod and wait before releasing the claim:

```bash
kubectl -n rdma-workloads delete pod rdma-example --wait=true
kubectl -n rdma-workloads delete resourceclaim rdma-example --wait=true
```

Use the workload names for your application. Stop a workload controller first if it would recreate the pod. Keep the claim while an application is expected to resume with it. One single-NIC reference worker offers one RDMA pod slot; additional ordinary pods can still run there.

## Resolve readiness findings

| Finding | Next action |
|---|---|
| Tools image cannot be pulled | Verify the immutable digest, registry reachability and pull secret in both workload and diagnostic namespaces. |
| Worker unavailable | Review OKE node state and OCI instance/agent health; inspect the node's boot and kubelet diagnostics. |
| Missing fabric parent address or link | Check the image's OCI HPC configuration and RDMA authentication plugins. Confirm the PF and MTU match the configured profile. |
| NRI socket absent | Verify the node runtime configuration and compatibility with the Dranet profile. |
| Shared RDMA mode absent | Check the supplied worker bootstrap and image; schedule host configuration changes during maintenance. |
| Competing PF configuration | Resolve ownership by another device-management controller or boot service before installing Dranet on that worker. |
| No free NICs | Inspect running pods and retained claims. Release completed workloads through their owner. |
| Driver settings differ | Compare exported Helm values with the deployed DaemonSet. Keep PF/child-CIDR changes within a planned maintenance window. |
| Benchmark fails | Review the operation's stage, raw output and pod status, then retrieve the relevant warning events or fixed node report. |
| Cleanup incomplete | Review the retained validation resources and use **Clean up interrupted test** before running another validation. |

For host authentication, the relevant file logs are under:

```text
/var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-hpc.log
/var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-rdma-authentication.log
```

Use the fixed OCI diagnostic profile to collect the relevant log excerpts. Do not copy private keys or certificate bundles into reports. Reports redact common credential patterns; review any diagnostic export before sharing it publicly.

An IPvlan interface can report link state `UNKNOWN`; evaluate the assigned IP, physical link and workload result together. Controller readiness establishes configuration state, while the hardware RDMA test establishes cross-node functionality. Perftest's `Transport type: IB` label can appear with an Ethernet/RoCE link; use the full report when interpreting results.

## Compare performance

The default functional test performs 5,000 writes of 64 KiB with one RC queue pair, RDMA-CM and TOS 41. Compare runs with the same shape, image, kernel/firmware, devices, traffic class and message/QP settings. Use longer application-specific tests to qualify sustained-load performance.

The operations log retains measured values and errors. Validation runs a 64 KiB bandwidth test and an 8-byte latency test with 5,000 iterations each, using RDMA-CM and the configured traffic class. Latency values are in microseconds and represent perftest’s half round-trip estimate. The overview displays bandwidth and latency from the same validation run. It marks measurements for review after 24 hours, a worker addition/removal/replacement/reboot, changed RDMA configuration, an incomplete run, or a newer validation attempt. A current measurement covers the tested pair. Run the matrix to compare other pairs. The run output includes average, median, minimum, maximum, standard deviation, and percentiles when reported. Raw server/client output and cleanup status are retained. No fixed latency threshold is imposed because placement, traffic, firmware, and workload conditions affect comparisons. Export a support bundle for an engineering handoff. It includes inventory summaries, configuration and operation summaries, measurement provenance, and diagnostic counts. It excludes tokens, secret contents, raw logs, job payloads, free-form errors, Terraform state and plans. Worker identifiers remain: review the archive before sharing. Individual diagnostic reports are available separately. Demo mode shows sample inventory and never reports a fabricated measurement.

## Monitor between checks

Enable scheduled monitoring under **Maintenance → Monitoring & drift**. You can refresh one idle host per sweep, approve a fleet comparison profile, schedule a nightly pair test and send alerts through OCI Notifications. All background checks start disabled. See [monitoring](MONITORING.md) for setup, expiry settings and metrics export.

## Maintain access and configuration

Create a new Bastion session when the current one expires. Change managed-SSH or port-forwarding TTLs in Resource Manager, apply, then use the updated session-create outputs. Existing sessions keep their expiry.

Tools-image and test settings remain editable in the UI. The PF and child range are locked after driver installation because changing them can disrupt allocated devices. Plan network changes with the workload owners and cluster administrator; the UI does not drain or reboot workers.

On the operator, `oke-rdma-doctor` checks bootstrap and services. Application state is under `/var/lib/oke-rdma-control`; configuration and the token are under `/etc/oke-rdma-control`. Backups and exported snapshots may contain infrastructure identifiers or credentials and require restricted storage.

## Permanently remove the deployment

Export any results to retain. Stop application controllers and pods, then release their claims and clean up validation resources. Review resources created outside the stack, including workload volumes and load balancers, before deletion.

Run a Resource Manager **Destroy** job for the stack and review its completion. Check for retained volumes, manually created registry images/secrets, Bastion sessions and other out-of-stack resources according to your retention policy. Delete the stack record only after confirming infrastructure destruction. Deleting a stack record alone does not remove its resources.


## Reset RDMA setup

Use **Maintenance → Preview RDMA reset** to return the RDMA application setup to its provisioned baseline. The removal plan lists Kubernetes resources and local setup state before any change. Type `RESET <environment name>` to queue the reset. A cluster administrator is required; the restricted operator role cannot uninstall privileged infrastructure.

Stop application RDMA workloads and their controllers before requesting a reset. Application ResourceClaims and ResourceClaimTemplates block uninstall, including pending claims. The reset removes only the validation resources owned by this asset; it does not delete application workloads to free their claims. A changed removal inventory requires a fresh preview. Kubernetes deletion uses UID preconditions to protect replacement resources.

The reset removes owned validation and diagnostic pods, owned validation claims, the supported Dranet Helm release and its RBAC/service account, published Dranet ResourceSlices on selected workers, and the matching `dra.net` DeviceClass. It clears cached host readiness and generated Helm values and removes runtime RDMA setting overrides so the settings supplied during Terraform provisioning become effective again. Driver shutdown waits for the Helm uninstall to finish before removing published devices.

The OKE cluster, managed workers, Compute Cluster, VCN, operator, Bastion, host drivers, Oracle Cloud Agent authentication, RDMA mode, node labels, namespaces, and registry pull secrets remain. No worker reboot is performed. Operation history and diagnostic reports remain available for audit. Pre-reset measurements remain in history but are cleared from the current summary and do not mark the new workflow as validated.

If reset stops partway through, the failed job identifies the stage and the resources already removed. Review a fresh preview before retrying. The reset never force-deletes resources or strips finalizers.

## Performance reference

For BM.Optimized3.36, Oracle documents a 100 Gbit/s RDMA interface in the [Compute Shapes reference](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm). Oracle's [shape-specific OSU Micro-Benchmarks tutorial](https://oracle-japan.github.io/ocitutorials/hpc/benchmark/run-omb-hpc/) reports 1.67 microseconds for a 1-byte MPI latency test using OpenMPI/UCX on a bare-metal HPC image. This asset measures 8-byte `ib_write_lat` latency in workload pods. Similar measurements are useful corroboration, but different message sizes, benchmark methods, images, CPU placement, and sample counts prevent a strict like-for-like comparison. Published results are reference measurements rather than an application latency guarantee. Use the percentile and MPI profiles to assess tail latency and collective performance.

## Scale workers

Use **Workers & scaling → Scale workers** to generate, review, and apply a Terraform-owned node-count change. Plans and infrastructure applies are distinct actions. Read [Capacity management](CAPACITY.md) for permissions, lifecycle settings, scale-in protection, new-node readiness, and recovery. A pending capacity operation must be reconciled before RDMA setup is reset. RDMA reset retains the capacity connection and its infrastructure-managed IAM policies.

## Request feedback

Controls show an immediate busy label while their request is in flight. Confirmation dialogs distinguish submitting a request from an accepted queued job. Queued progress appears as soon as the server acknowledges the operation; slower inventory and capacity queries refresh independently. Reset previews open immediately with submission disabled until the removal plan arrives. Long requests remain labelled as waiting for the server and are not automatically retried. If submission is interrupted, check Operations before retrying.
