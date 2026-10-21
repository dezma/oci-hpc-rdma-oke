# Troubleshooting

Find the failed stage first. Fix one cause, rerun that stage, then continue. If diagnostic results are missing, resolve collection first.

## Gather diagnostic information

Record the environment name, node name, action, job ID, time and complete error. In the UI open Operations → the relevant job → Evidence and output. For host diagnostics, download text or JSON before changing the node. Reports normally survive deletion of the temporary diagnostic pod.

For image issues, include the job's `provenance.tools_image` digest and your Dockerfile/build command. Never send UI tokens, registry tokens, private keys, decoded Kubernetes secrets or raw Terraform state.

## Deployment and access

| Symptom | Check / action | Success checkpoint |
| --- | --- | --- |
| Resource Manager Apply fails | Open its job log and first failing resource. Check the chosen image/shape/AD, permissions and quotas; do not repeatedly recreate stacks. | Apply succeeds |
| IAM policy or dynamic-group creation denied | Ask the IAM administrator to supply the access, then select reuse options only for resources already provided. The operator's access and the provisioning caller's access differ. | Correct identities and policy scopes confirmed |
| Worker does not join | Check node-pool work requests, image/OKE compatibility and bootstrap network access. | Worker appears Ready in `kubectl get nodes` |
| Bastion SSH fails | Session Active and unexpired; source public CIDR allowed; correct private/public key pair; correct copied session command. | Operator `opc` shell opens |
| UI connection refused | Tunnel running on the same workstation as the browser; local port matches; target is 8000; operator service active. | Login page opens |
| Browser worked and stopped | Check session expiry and reopen a fresh tunnel. | Connection restored without redeployment |
| `api: command not found` | Paste the complete `api()` definition from the operator guide in the current shell. | `api GET /api/v1/environment` returns JSON |
| Python terminal stream is not seekable | Use the provided script's separate read/write opens of `/dev/tty`; do not combine them into `r+`. | All prompts accept input |

On the operator, inspect service/bootstrap health:

```bash
oke-rdma-doctor
sudo systemctl status oke-rdma-control-api.service oke-rdma-control-worker.service --no-pager
sudo journalctl -u oke-rdma-control-api.service -u oke-rdma-control-worker.service -n 100 --no-pager
sudo cat /var/lib/oke-rdma-control/bootstrap-status.json
sudo tail -n 100 /var/log/oke-rdma-control-bootstrap.log
```

Review logs locally and redact credentials before sharing. A missing service/token during provisioning may mean bootstrap is still running or failed downloading a dependency.

## Registry and host diagnosis

Use [the diagnostic decision table](DIAGNOSTIC-TROUBLESHOOTING.md) for detailed collection failures.

| Symptom | Action |
| --- | --- |
| `Unauthorized`, `ErrImagePull`, `ImagePullBackOff` | Check hostname, full registry identity, auth token, repository access and secret name. Refresh secrets in both namespaces and save the name in the UI. |
| Podman anonymous access denied | Log Podman in as the same OS user used to run it. `sudo podman` and Kubernetes pull secrets have separate credentials. |
| Diagnostic deadline / exit 137 | Inspect earlier events and termination reason. Look for an explicit `OOMKilled` reason before diagnosing memory exhaustion. An image-pull failure can cause the deadline. |
| Empty facts / health not established | Fix collection before attempting host repair. Download the report; the temporary pod may already be deleted. |
| Wrong interface or not mlx5 PF | Compare the configured PF with the worker's RDMA interface and approved shape/image. Do not select the ordinary network NIC just because it is up. |
| Parent IP absent / MTU not 4220 | Inspect host configuration and OCA logs; wait only while initialization is progressing. Do not invent a parent IP. |
| Parent IP present but carrier down | Inspect the selected PF's physical-link output and agent state. Check both link readiness and authentication logs. |
| Only unused RDMA ports are down | Evaluate the configured PF; unused ports can remain down. |
| NRI socket absent / RDMA mode not shared | Verify the approved worker image and bootstrap configuration; collect diagnostic results before reinstalling the driver. |
| OCA crash or unresponsive module | Use a read-only recovery preview; restart only if eligible. See [recovery](RECOVERY.md). |
| Restart blocked by workloads | Stop/pause the owning applications and release claims as instructed. Do not delete unrelated system services to bypass the check. |
| Persistent selected-PF no-cable/physical fault | Review placement and physical identity. Consider a previewed replacement; replacing an instance does not guarantee a different physical host. |

For an authorised worker SSH investigation only, these are read-only commands:

```bash
rdma link show
ip -details link show ens800f0np0
ip -4 address show dev ens800f0np0
sudo systemctl status snap.oracle-cloud-agent.oracle-cloud-agent.service --no-pager
sudo journalctl -u snap.oracle-cloud-agent.oracle-cloud-agent.service -n 100 --no-pager
```

Use the configured PF if it differs. On snap-based Ubuntu images, check `snap.oracle-cloud-agent.oracle-cloud-agent.service`. Prefer the tool's diagnostic bundle when worker SSH is disabled.

## Performance and workloads

| Symptom | Action |
| --- | --- |
| No free worker / allocated device | Inspect ResourceClaims and consumers. Stop the owner/controller before releasing its claim. Use tool cleanup only for interrupted tool-owned tests. |
| `numactl` missing, `mpi4py` import fails, MPI exit 127 | The test image lacks dependencies or the wrong image was pushed/configured. Run the build-time package check, push that exact tag, save its digest and verify the job's recorded digest. |
| NUMA topology/placement unavailable | The container must expose usable local and remote NUMA CPU/memory nodes and NIC locality. Restricted cpusets can prevent the test. It does not require a BIOS change. |
| NUMA permission denied for memory policy | Review NUMA test capability/security policy and container runtime restrictions. Do not make all application pods privileged. |
| MPI publickey authentication failure | This is the ephemeral test pods' launch channel. Inspect `extended.ssh_evidence`; do not edit worker host SSH keys or disable host-key checking. |
| MPI cannot connect | Check pod-to-pod management network policy for temporary SSH port 2222 and MPI control traffic. RDMA payload uses its separately configured transport. |
| MPI UCX transport failure | Check MPI/UCX packages, selected device and host/device readiness. A basic perftest pass does not guarantee MPI transport initialization. |
| Bandwidth passes, extended profile fails | Retain the basic measurements but treat the chosen profile as failed. Read `extended.error`; a displayed stage label may still refer to the preceding basic test. |
| CPU frequency warning | Record the warning and compare under consistent load/placement. |
| Stale baseline / changed conditions | Rerun with matching settings and worker identities; do not interpret unrelated runs as a controlled regression. |
| Workload pod Pending | Describe the pod and claim; inspect capacity, scheduling, image credentials and device allocation. |

## Scaling and replacement

| Symptom | Action |
| --- | --- |
| Capacity report unavailable | Inspect the returned CLI error. Confirm instance-principal access to the live node-pool compartment. Do not interpret request failure as zero capacity. |
| Capacity AVAILABLE but provisioning fails | A snapshot does not reserve capacity or verify Compute Cluster placement or quota. Inspect the Resource Manager/OKE error. |
| Plan includes operator image, IAM or network changes | Discard an unapplied target and handle intentional maintenance separately at the current worker count. Keep images pinned. Do not weaken the size-only gate. |
| Stack desired count changed but workers did not | Planning saves the target before Apply. Review/apply or discard the pending plan; check observed counts to see which workers exist. |
| Capacity operation pending | Use the action appropriate to its phase: discard before apply, resume after submission, close only an eligible terminal failure. Never delete journals. |
| New workers Ready but RDMA fails | Open the new worker's host diagnostics. Kubernetes Ready and RDMA readiness are different. |
| Replacement not complete | Resume verification, review work request and exact identities. No automatic repeat deletion is performed. |
| Same hardware serial/MAC after replacement | OCI may reuse a physical host. Compare the recorded serial and PF MAC, then run host and cross-node checks on the replacement. |

Read [capacity](CAPACITY.md) and [replacement](REPLACEMENT.md) before taking recovery actions. If blocked, keep the reports and resolve the cause rather than bypassing the guard.

## Failed scale-up left some workers provisioned

If requested size is 6 but only 5 workers exist, do not assume the failed Apply rolled back. Use [capacity reconciliation](CAPACITY.md#reconcile-partially-provisioned-capacity). Acknowledge the terminal failed Apply, then review a fresh plan to a target at or below the surviving count. If work requests are still active, wait. If surviving workers are not Ready, resolve their health first. Do not delete operation journals or remove Terraform state to unlock scaling.
