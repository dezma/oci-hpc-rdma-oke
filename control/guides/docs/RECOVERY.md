## v2.7.3 assessment and explicit OCA restart

Worker Details now offers two read-only previews: **Assess recovery** for the recognized
OCA startup crash, and **Preview Oracle Cloud Agent restart** for an explicit restart
without that historical crash signature. Both collect diagnostics before checking workloads,
so a busy worker still yields evidence and separate restart blockers. Existing administrator,
node health, registry and operation-lock requirements still apply.

To restart from the UI:
1. Open worker Details from Setup & validation or Workers & scaling.
2. Select Preview Oracle Cloud Agent restart. Review identity metadata status, host evidence,
   issues, workload blockers and impact. This step does not restart a service.
3. Once eligible, select Review Oracle Cloud Agent restart and confirm RECOVER followed by
   the exact environment name shown. The fixed snap OCA service on this worker restarts once.
   Other OCA plugins briefly stop. No browser-supplied service name or shell is accepted.
4. The operation protects the worker and checks host RDMA readiness for up to five minutes.
   Link, parent IPv4 and MTU must pass before success; a successful systemctl command alone
   is insufficient. Then run normal device and cross-node validation.
5. If recovery stops, inspect the retained evidence and use Finish recorded recovery to remove
   protection without another restart. Manual finish leaves the worker cordoned. A consumed
   preview cannot restart again; collect a fresh preview for a new reviewed attempt.

Application pods and all resource claims/templates still block restart. Verified kube-system
DaemonSets remain permitted. Standard CoreDNS pods are also permitted for OCA restart only,
when their ReplicaSet and the coredns Deployment ownership UIDs match and they do not use
host networking, host PID/IPC or hostPath volumes. CoreDNS is not stopped or evicted. Other
system pods receive a specific blocker message; do not disable DNS to bypass a check.
Node replacement retains its stricter idle policy and does not inherit the CoreDNS exception.

Explicit OCA restart still requires current host inventory, identity metadata HTTP 200,
an active snap OCA service, and no explicit selected-PF no-cable evidence. It does not require
a recognized historical module crash. This is not a general cluster-wide agent restart;
kubelet, CRI-O, Dranet and other services are not targeted by this action.

The earlier procedure below describes guided startup-crash recovery. Its workload eligibility
is amended by the assessment separation and CoreDNS exception above.

# Confirmed RDMA initialization recovery — v2.5.0

This is an administrator capability, not an automatic boot watchdog. It addresses the observed OCA configuration-module crash/unresponsive condition after transient identity-metadata failure. No automatic retry, image upgrade, manual RDMA IP assignment or reboot is performed.

## Eligibility and authority

The environment must use live `iam_admin` mode. The operator must actually have the admission-policy and node permissions checked at runtime; selecting that mode alone does not grant them. Restricted operator RBAC is not expanded to grant recovery. The UI's workspace bearer token is the administrative authorization boundary; this release does not introduce individual user identities or role separation. Protect that token as an administrative credential.

Assessment uses a fixed read-only diagnostic on a current Ready worker labelled for this profile. It requires successful host facts, failed host prerequisites, identity metadata HTTP 200, an active snap OCA service and a current-boot configuration crash/unresponsive signature. The preview binds the node UID, boot ID and RDMA settings and expires after five minutes. A fresh check repeats before restart. Log signatures are evidence for this recovery path, not an exhaustive diagnosis engine.

## Placement protection and scope

The worker is cordoned. A temporary ValidatingAdmissionPolicy/Binding denies new pods with the target `nodeName` and Binding requests targeting it. It also denies new ResourceClaims/ResourceClaimTemplates cluster-wide during the short maintenance window. Server-side dry-run probes must demonstrate denial before the restart; unsupported admission, CEL errors without the expected denial message, permissions or propagation failures stop recovery.

Before and after protection, the tool checks for application pods and any existing cluster ResourceClaims/templates. Only existing verified kube-system DaemonSet pods are exempt from the idle-pod check; pending/running application pods block it. This first implementation deliberately favors a coordinated idle environment over recovery during concurrent RDMA workloads.

Pause controllers before starting. Existing kube-system DaemonSets remain, but their replacement pods cannot be admitted onto this worker until the guard is removed. Static pods and direct privileged host access are outside admission enforcement. Administrators can bypass/remove policies or modify resources, so they must coordinate and must not alter the node during recovery. This is not an isolation guarantee against a hostile cluster administrator.

## Privileged implementation

Diagnostics use privileged host-network/host-PID pods with a read-only host-root mount. The recovery pod needs a writable host-root mount to invoke one fixed host systemd service restart. Both use the trusted immutable tools image. A digest identifies content but does not establish trust; review and control who can supply that image. Image credentials stay in Kubernetes secrets; metadata queries return only the HTTP status, not identity contents.

Namespace `enforce: privileged` permits the necessary host access; it does not make scripts inherently safe. Restrict pod creation and exec access in the diagnostics namespace to trusted administrators/operator identities. Do not label application namespaces privileged as a workaround. Kubernetes RBAC cannot express field-level restrictions on a pod spec; environments requiring stronger image/spec constraints should enforce a compatible admission allowlist before rollout. Such an allowlist must account for the specific diagnostic and recovery pod shapes; this release does not install a universal trust policy for custom images.

The general diagnostics catalog never accepts arbitrary scripts from the browser. Recovery is a distinct confirmed job action, not an added shell parameter to a read-only profile. All administrative tokens currently have the same capability; the release does not provide per-person approvals.

## Timing and persistence

The tools pod has a 15-minute active deadline. Pod readiness has a 120-second wait; admission verification is bounded; restart is wrapped in a 60-second host timeout. Host verification polls for up to five minutes plus an in-flight bounded probe. The whole operation also includes API calls and cleanup, so five minutes is not a total-operation SLA.

A journal in `/var/lib/oke-rdma-control/recovery/attempt-<job-uuid>.json` records node identity, original cordon state, pod/guard names and restart intent before submission. The command is never automatically replayed after an uncertain response. The durable worker marks an interrupted job failed; protection and journal persist.

On verified success, the tool deletes the recovery pod and admission guard, refreshes host evidence and restores original scheduling. It does not benchmark performance. If anything fails, review Operations and use **Finish recorded recovery**. Manual finish removes temporary resources without restart and leaves the worker cordoned. Do not uncordon merely because cleanup completed.

## Manual interruption cleanup (operator unavailable)

**OPERATOR VM or authorized cluster-admin workstation**. First read the exact journal on the operator and stop conflicting administration. If the application is still processing recovery, wait or deliberately stop its worker service before manual intervention. This is an exceptional procedure, not normal cleanup.

1. Read only the specific recorded attempt, replacing the filename:

```bash
sudo cat /var/lib/oke-rdma-control/recovery/attempt-REPLACE_WITH_JOB_UUID.json
```

2. Verify `node_name`, `node_uid`, `pod`, `guard`, `phase`, `restart_attempted` and current Kubernetes identity. Do not reuse a node name after replacement as evidence of the same worker.

```bash
kubectl get node REPLACE_WITH_NODE_NAME -o jsonpath='{.metadata.uid}{"\n"}{.spec.unschedulable}{"\n"}'
kubectl -n oke-rdma-diagnostics get pod REPLACE_WITH_RECORDED_POD -o wide
kubectl get validatingadmissionpolicy REPLACE_WITH_RECORDED_GUARD
kubectl get validatingadmissionpolicybinding REPLACE_WITH_RECORDED_GUARD
```

3. Preserve logs/status, then delete only the recorded recovery resources. Keep the node cordoned. Do not delete policies by a broad label selector or strip finalizers.

```bash
kubectl -n oke-rdma-diagnostics delete pod REPLACE_WITH_RECORDED_POD --ignore-not-found=true --wait=true --timeout=30s
kubectl delete validatingadmissionpolicybinding REPLACE_WITH_RECORDED_GUARD --ignore-not-found=true
kubectl delete validatingadmissionpolicy REPLACE_WITH_RECORDED_GUARD --ignore-not-found=true
```

4. Restore operator service if it was stopped, then use **Finish recorded recovery** when the same worker is Ready to close the journal. Do not manually erase the journal to permit another restart. If the node was replaced, retain the journal and review the identity mismatch before administrative reconciliation.
5. Run host readiness and inspect the actual result before deliberately uncordoning the worker. Preserve its original cordon if it was already cordoned for another reason.

## Validation limits

Unit tests cover eligibility, idle checks, admission-verification refusal, durable restart intent, uncertain submission, no replay, retained protection and scheduling restoration. DOM tests exercise UI controls and confirmation. No live OKE recovery or Kubernetes admission integration test was performed in the build environment. The runtime refuses to restart unless live server-side admission probes demonstrate protection. Validate this feature on an idle test worker before production adoption.


## Diagnosing a worker that has an RDMA address but no carrier

After a scale-out, open the worker's diagnostics and run **RDMA link health**, then **OCI RDMA authentication**. In the setup host-readiness section, expand the evidence and download the JSON or text report. A completed diagnostic means collection finished; it does not mean authentication succeeded.

The UI evaluates only the configured PF. A current ethtool `Link detected: no (No cable)` with absent carrier produces **RDMA physical link unavailable**. Check the instance's compute-cluster membership against healthy workers, then investigate NIC/fabric connectivity. This evidence suggests a physical connection problem; it cannot prove a cable was physically unplugged. Do not switch to the management interface or manually assign fabric addresses.

MTU mismatch is a separate finding. Changing MTU does not restore missing carrier. Authentication logs may say the selected interface is not ready while the module reports COMPLETED; review the timestamped evidence and rerun diagnostics after the link recovers. A generic WPA process is not authentication proof.

Missing ethtool or a failed query is reported as unavailable evidence, never as a healthy link or a confirmed cable fault. Agent-restart recovery is blocked for a current selected-PF no-cable finding, including a recheck immediately before restart. Older reports remain historical evidence; collect a fresh report after repair.
