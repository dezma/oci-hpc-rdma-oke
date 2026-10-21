# Replace a defective managed worker.

## Prerequisites and permissions

- Live mode and the cluster-administrator Kubernetes access mode. Restricted RBAC is not silently escalated.
- A connected Resource Manager stack owning this cluster and pool, no pending capacity/recovery/replacement operation, and no active Resource Manager job.
- Saved operator image matching Terraform state, explicit worker image matching the current node-pool image, and the same desired worker count in the stack and pool. Pool-wide cycling must be disabled.
- Kubernetes node identity must map to an ACTIVE OCI instance in this managed pool. The tool will not select a destructive target by IP address alone. Kubernetes Ready is not required for preview, but a missing/unidentifiable Kubernetes node requires administrator diagnosis outside this workflow.
- Stop application controllers targeting the worker and safely remove their workloads first. The tool requires the selected node to be application-idle. Only completed pods and verified kube-system DaemonSets are exempted. All resource claims and claim templates, including pending claims elsewhere in the cluster, block replacement. Do not delete live application claims just to bypass this check.
- Pause changes made outside this tool, including Resource Manager applies and OKE node cycling. The local lock does not lock the OCI Console or another administrator's session.
- The operator's existing capacity IAM remains required.  It does not grant an unrestricted delete-node-pool action. The existing instance/network permissions used by managed-pool operations remain necessary.

For an existing deployment, regenerate and review the policy files with `provisioning/capacity-policy.py` (also included as `capacity-policy.py` in the application update). Follow `docs/CAPACITY.md` for the complete command and policy installation procedure. This generator writes reviewable JSON; it does not apply IAM changes. Update the existing policy deliberately with your administrator. An application-only update does not install IAM. Read access and preview success do not prove mutation permission; OCI checks it at submission. Do not use the new ORM package merely to change IAM without reviewing its full plan, because its new payload can replace the operator.

## UI: diagnose, preview, replace, verify

1. Open **Workers & scaling**, select **Details** for the defective worker, and run **Full RDMA support bundle**. Review the selected PF and current evidence. Recovery assessment is useful for the recognized OCA startup failure; a persistent physical-link problem may need replacement instead.
2. Finish any existing recovery protection. Review the prerequisites above, including workload and claim blockers.
3. In worker details, select **Preview worker replacement**. Review the exact Kubernetes node name, OCI instance OCID, pinned worker image, intended pool count, findings timestamp, workload list, blockers and temporary protections. A preview lasts five minutes and authorizes no deletion.
4. Select **Review replacement** and type `REPLACE <your-environment-name>`. The confirmation repeats the worker name and instance identity. The server rechecks the preview, worker UID/boot identity, pool membership, image/settings and stack state.
5. The tool cordons the selected worker and verifies admission protection. New pods/bindings targeting that worker and new claims/templates cluster-wide are temporarily denied. It then makes one OKE DeleteNode request with pool decrement **false**, eviction grace **60 minutes**, and forced termination **false**. OKE performs its managed drain and respects eviction/disruption constraints. Expect temporary loss of one worker's capacity; this is not surge replacement.
6. In **Workers & scaling**, use **Resume / verify replacement** to observe progress. The retained panel stays visible after the old node disappears. Each click performs a bounded observation/verification attempt; the tool does not continuously poll OCI after the job finishes. A request/check completing is not the same as replacement success.
7. Resume identifies exactly one new ACTIVE instance while preserving all original surviving members. It checks the new Kubernetes identity, Ready/schedulable state, host readiness (selected PF/carrier/MTU, OCA-configured address, shared RDMA, NRI), Dranet/device publication, and then runs a quick cross-node validation against a healthy free peer. Readiness can take time; resume later when OCI/bootstrap progresses.
8. Only **COMPLETE / Verified** means the functional test passed. The new instance ID, worker name, work-request ID, host evidence, test result and timeline remain in the replacement evidence. Operation summaries are included in the support bundle. Download the underlying host reports separately for full diagnostic output.

The admission guard is removed when the old OCI and Kubernetes identities have left, so it cannot obstruct bootstrap if a name is reused. The application mutation lock remains until verification succeeds or the operation is safely closed.

## Understanding outcomes

| Phase | Meaning and next action |
|---|---|
| PREPARING / REVIEW_REQUIRED with NOT_SENT | No deletion was sent. Close for manual review to remove recorded protection. The original node stays cordoned. Investigate the error and create a new preview. |
| REJECTED | OCI rejected submission. Correct IAM/identity/ETag findings, close safely, and create a fresh preview. |
| SUBMITTING / UNKNOWN | Response may have been lost. Never submit another deletion. Resume only observes topology; retain the lock if the outcome is unproven. |
| WAITING_FOR_OCI | OCI is draining or replacing the instance. Resume later and inspect the recorded work request. No forced termination is attempted. |
| OCI_FAILED | OKE reported failure/cancellation. Inspect OCI work-request errors. Close is permitted only when the original pool membership is ACTIVE and stable, or an already identified replacement has safely released protection. |
| VERIFYING_NODE | The new instance exists; registration/readiness may still be pending. |
| DEVICE_PENDING | Host checks passed but Dranet/device publication is not ready. Inspect the evidence. If setup changes are needed, close for manual review, then repair/validate through normal setup. |
| RDMA_FAILED | Replacement exists but host or cross-node testing failed. Evidence is retained. The tool does not automatically replace it again. |
| FUNCTIONAL_TEST_PENDING | No suitable free second worker is available. Host/device checks alone do not establish cross-node RDMA. |
| CLOSED | Safely released for manual review, not successful RDMA verification. |
| COMPLETE | New identity, host, device and cross-node verification passed. |

If the replacement has joined but further setup, scaling or cleanup is needed, **Close for manual review** releases the local lock only after the safety checks pass. For a one-worker pool, close the explicit functional-test-pending result, add a second worker through the normal reviewed capacity flow, and run pair validation. Closing a pre-submission or rejected attempt does not automatically uncordon the original worker. Review that worker before an administrator uncordons it.

A lost response without a work-request ID is reconciled conservatively from exact old/new identities. If the original worker remains and submission cannot be proven rejected or terminal, the tool retains the lock. An administrator must inspect OCI activity and resolve the operation; there is no journal-delete or force-close shortcut in the tool. If an interrupted functional test left owned pods/claims, use validation cleanup, then resume. No interruption path automatically replays deletion.

## Operator VM route

Define the authenticated `api` helper in the operator guide. The same confirmation, preview and guards apply:

```bash
api POST /api/v1/jobs '{"action":"replacement_preview","node_name":"10.70.1.89","confirmation":"hpc-oke-rdma"}'
api GET /api/v1/jobs
```

Replace the worker/environment examples with your actual values. Wait for the preview job to finish, review its entire result and copy its 32-character `result.id`. Substitute it for `PREVIEW_ID` below; the literal placeholder will be rejected:

```bash
api POST /api/v1/jobs '{"action":"replacement_run","replacement_id":"PREVIEW_ID","confirmation":"REPLACE hpc-oke-rdma"}'
api GET /api/v1/environment
```

The environment's `replacement` object remains available even if the job failed or the operator restarted. Use its `id` as `OPERATION_ID`:

```bash
api POST /api/v1/jobs '{"action":"replacement_resume","replacement_id":"OPERATION_ID","confirmation":"hpc-oke-rdma"}'
```

When the conditions described above permit closing for review:

```bash
api POST /api/v1/jobs '{"action":"replacement_close","replacement_id":"OPERATION_ID","confirmation":"CLOSE hpc-oke-rdma"}'
```

After replacement, a later Resource Manager refresh will observe the new computed node identities. Desired count and image variables are not changed by this workflow. Review the next scale plan normally; never ignore an unexpected change because a replacement occurred.

Sources: [OKE termination and replacement](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/replace-worker-node-top.htm), [OCI SDK DeleteNode parameters](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/container_engine/client/oci.container_engine.ContainerEngineClient.html), [OKE policy reference](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/contengpolicyreference.htm).


## Hardware identity and reuse

Run host readiness before replacing a worker whenever it can still run diagnostic pods.
The check records the OCI instance ID, Kubernetes UID and boot ID, DMI product serial,
and selected PF MAC. Open worker Details to see the serial, MAC and matches against
previous instance IDs. 
Missing serials appear as Not collected; they are never treated as matching identities.

Replacement preview retains this baseline. Resume records the new host observation and
shows the comparison even when RDMA checks fail. A serial match reports reuse of the
recorded server identity. A MAC-only match is suggestive, not conclusive. A different
identity does not prove working RDMA. Host, device and cross-node verification remain
required for COMPLETE. No replacement or repair is automatically triggered by a match.

History is local to the operator in hardware-history.json, retained through RDMA setup
reset, and bounded to the most recent 1,000 instance/boot observations. Each observation
retains up to ten prior matches in its readiness result. Rebuilding or losing the operator
without preserving its state loses this history. Historical identities cannot be recovered
from a deleted worker; collect a baseline first. Support evidence contains infrastructure
identifiers, so review it before sharing. Hardware identity is collected by host readiness;
other standalone diagnostic profiles do not populate the retained history.
