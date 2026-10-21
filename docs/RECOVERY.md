# Diagnose and recover one worker

Recovery restarts Oracle Cloud Agent once on an eligible worker, then checks RDMA readiness. Diagnose the worker first and review the restart preview before confirming.

## 1. Collect diagnostic results

Open worker **Details** from Setup & validation or Workers & scaling. Run the full RDMA support bundle. If collection fails, resolve [diagnostic collection](DIAGNOSTIC-TROUBLESHOOTING.md) first. A busy worker can still be assessed; diagnostic results and restart blockers are reported separately.

Review the selected PF, current link/carrier, parent IPv4, MTU, metadata reachability and agent logs. Check link state as well as the parent IP. Unused ports can be down. A no-cable indication on the selected PF is different from an agent initialisation failure.

## 2. Choose a preview

| Action | Use |
| --- | --- |
| Assess recovery | Evaluate a recognised current-boot OCA configuration crash/unresponsive signature together with failed host readiness |
| Preview Oracle Cloud Agent restart | Assess restarting the agent without requiring that crash signature |

Neither preview restarts anything. The environment must use live `iam_admin` mode with the necessary Kubernetes permissions. The UI token grants administrative access; there are no separate per-user approval roles.

Eligibility requires current host inventory, a Ready managed worker, successful metadata HTTP status, an active snap OCA service and no reported no-cable condition on the selected PF. Application pods and ResourceClaims/templates block restart. Verified system DaemonSets and standard CoreDNS ownership can be permitted for this action; other blockers must be resolved, not bypassed by deleting DNS.

## 3. Review and restart once

1. Pause application controllers and release relevant workloads/claims as instructed by the preview.
2. Review node identity, findings, blockers and impact. A preview expires after five minutes and is tied to node UID, boot identity and settings.
3. Select the review action and type `RECOVER` followed by the exact environment name shown.
4. The tool cordons the selected worker and verifies temporary admission protection. It denies new pods/bindings targeting that node and temporarily denies new claims/templates cluster-wide.
5. The fixed snap Oracle Cloud Agent service restarts once. Its other plugins briefly stop too. The tool never accepts a browser-supplied service name or shell command.
6. Wait up to five minutes for the host-readiness checks. Recovery succeeds when link, address, MTU and the other prerequisites pass.
7. Verify normal device publication and run cross-node validation afterward.

Diagnostics use ephemeral privileged pods with read-only host-root access. Recovery needs a writable host-root mount to invoke the fixed host service action. Restrict diagnostics-namespace pod/exec access and trust the configured immutable image. Keep privileged pod access limited to the infrastructure namespaces and trusted administrators. See [security](../SECURITY.md).

## 4. If recovery is interrupted or unsuccessful

Read the retained operation and diagnostic results before retrying. **Finish recorded recovery** removes recorded temporary protection without restarting the agent again. Manual finish leaves the worker cordoned; an administrator should verify health before uncordoning. A consumed preview cannot be reused. Do not remove journals or manually replay an uncertain restart.

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

If the recorded recovery needs to be finished, use its attempt ID:

```bash
api POST /api/v1/jobs '{"action":"recovery_finish","recovery_attempt_id":"REPLACE_WITH_ATTEMPT_ID","confirmation":"FINISH hpc-oke-rdma"}'
```

Run one action at a time. Poll its job and inspect the result before choosing the next action.
