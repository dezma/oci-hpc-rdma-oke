# Diagnose and recover one worker

Start with diagnosis. Recovery is a deliberately confirmed administrator action to restart Oracle Cloud Agent on one eligible worker and recheck RDMA readiness. It is not a general reboot button and does not repair every physical-link or provisioning problem.

## 1. Collect evidence

Open worker **Details** from Setup & validation or Workers & scaling. Run the full RDMA support bundle. If collection fails, resolve [diagnostic collection](DIAGNOSTIC-TROUBLESHOOTING.md) first. A busy worker can still be assessed; diagnostic evidence and restart blockers are reported separately.

Review the selected PF, current link/carrier, parent IPv4, MTU, metadata reachability and agent logs. A parent IP alone does not prove link readiness. Unused ports can legitimately be down. A no-cable indication on the selected PF is different from an agent initialisation failure.

## 2. Choose a preview

| Action | Use |
| --- | --- |
| Assess recovery | Evaluate a recognised current-boot OCA configuration crash/unresponsive signature together with failed host readiness |
| Preview Oracle Cloud Agent restart | Explicitly assess restarting the agent without requiring that crash signature |

Neither preview restarts anything. The environment must use live `iam_admin` mode with the necessary Kubernetes permissions. The UI token grants administrative access; there are no separate per-user approval roles.

Eligibility requires current host inventory, a Ready managed worker, successful metadata HTTP status, an active snap OCA service and no explicit selected-PF no-cable evidence. Application pods and ResourceClaims/templates block restart. Verified system DaemonSets and standard CoreDNS ownership can be permitted for this action; other blockers must be resolved, not bypassed by deleting DNS.

## 3. Review and restart once

1. Pause application controllers and release relevant workloads/claims as instructed by the preview.
2. Review node identity, findings, blockers and impact. A preview expires after five minutes and is tied to node UID, boot identity and settings.
3. Select the review action and type `RECOVER` followed by the exact environment name shown.
4. The tool cordons the selected worker and verifies temporary admission protection. It denies new pods/bindings targeting that node and temporarily denies new claims/templates cluster-wide.
5. The fixed snap Oracle Cloud Agent service restarts once. Its other plugins briefly stop too. The tool never accepts a browser-supplied service name or shell command.
6. Watch the bounded host-readiness checks, up to five minutes. Restart command success alone is not a successful recovery: link, address, MTU and the other prerequisites must pass.
7. Verify normal device publication and run cross-node validation afterward.

Diagnostics use ephemeral privileged pods with read-only host-root access. Recovery needs a writable host-root mount to invoke the fixed host service action. Restrict diagnostics-namespace pod/exec access and trust the configured immutable image. Namespace privileged admission is scoped to infrastructure needs; it is not a reason to make arbitrary application pods privileged. See [security](../SECURITY.md).

## 4. If recovery is interrupted or unsuccessful

Read the retained operation and diagnostic evidence before retrying. **Finish recorded recovery** removes recorded temporary protection without restarting the agent again. Manual finish leaves the worker cordoned; an administrator should verify health before uncordoning. A consumed preview cannot be reused. Do not remove journals or manually replay an uncertain restart.

Persistent physical-link failure may require reviewing placement, image/bootstrap or [worker replacement](REPLACEMENT.md). Do not assign an arbitrary parent IP or force an MTU change to conceal missing host configuration.

## Operator VM commands

First define `api` using the [operator guide](OPERATOR-VM.md). Replace the node and environment examples. Submit one preview, poll the returned job and review its result:

```bash
api POST /api/v1/jobs '{"action":"recovery_assess","node_name":"REPLACE_WITH_NODE_NAME","confirmation":"hpc-oke-rdma"}'
```

For the explicit restart preview instead:

```bash
api POST /api/v1/jobs '{"action":"recovery_restart_assess","node_name":"REPLACE_WITH_NODE_NAME","confirmation":"hpc-oke-rdma"}'
```

Copy the preview ID returned by the successful assessment; do not substitute the job UUID. After reviewing eligibility and impact:

```bash
api POST /api/v1/jobs '{"action":"recovery_run","recovery_preview_id":"REPLACE_WITH_PREVIEW_ID","confirmation":"RECOVER hpc-oke-rdma"}'
```

If the recorded recovery needs to be finished, use its actual attempt ID:

```bash
api POST /api/v1/jobs '{"action":"recovery_finish","recovery_attempt_id":"REPLACE_WITH_ATTEMPT_ID","confirmation":"FINISH hpc-oke-rdma"}'
```

These are separate decision points, not a block of commands to run together. Poll each job and inspect its result before proceeding.
