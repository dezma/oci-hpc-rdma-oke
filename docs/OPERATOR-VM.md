# Operator VM guide

Use this route to operate from SSH without the browser. Start with [deployment and private access](../README.md). All commands on this page run on the **operator VM**, not a worker or Cloud Shell, unless a section says otherwise.

## 1. Check the operator

```bash
kubectl get nodes -o wide
sudo systemctl status oke-rdma-control-api.service oke-rdma-control-worker.service --no-pager
```

Expected: both services are active; provisioned workers appear in Kubernetes. A zero-worker deployment has no workers yet. If the service is unavailable, run `oke-rdma-doctor` and follow [troubleshooting](TROUBLESHOOTING.md).

## 2. Build the image and create the pull secret

Complete [image build → package verification → registry push → digest → pull-secret setup](IMAGE-PULL-SECRET.md) on this VM. Do not skip package verification: NUMA and MPI require additional tools beyond the basic RDMA benchmark.

## 3. Define a terminal API helper

Paste this Bash function into the operator shell. It reads the token internally using sudo, sends it only to localhost, and prints JSON responses. It does not print the token or put it in command-line arguments. Define it again when you open a new shell session.

```bash
api() {
  sudo python3.11 - "$@" <<'PY'
import json, pathlib, sys, urllib.request, urllib.error
method, path = sys.argv[1:3]
if not path.startswith('/api/v1/'):
    raise SystemExit('Use a local /api/v1/ path')
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
token = pathlib.Path('/etc/oke-rdma-control/admin.token').read_text().strip()
request = urllib.request.Request('http://127.0.0.1:8000' + path,
    data=data, method=method,
    headers={'Authorization': 'Bearer ' + token,
             'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read().decode()
        try: print(json.dumps(json.loads(body), indent=2))
        except json.JSONDecodeError: print(body, end='')
except urllib.error.HTTPError as error:
    print(error.read().decode(), file=sys.stderr)
    raise SystemExit(1)
PY
}
api GET /api/v1/environment
api GET /api/v1/rdma/config
```

For confirmation strings, obtain the exact environment name:

```bash
sudo python3.11 - <<'PY'
import json
with open('/etc/oke-rdma-control/config.json') as f:
    print(json.load(f)['environment_name'])
PY
```

Examples below use `hpc-oke-rdma`. Replace it if your environment differs. An accepted job enters the **queued** state. Record its returned job ID; poll the exact job before proceeding:

```bash
api GET /api/v1/jobs
api GET /api/v1/jobs/REPLACE_WITH_RETURNED_JOB_ID
```

Inspect status, result and error. After a request timeout, check job history before resubmitting. Continue when the job reports success.

## 4. Save configuration and check hosts

Replace the image placeholder with the immutable digest recorded during image setup:

```bash
api PUT /api/v1/rdma/config '{
  "rdma_pf_name": "ens800f0np0",
  "rdma_mtu": 4220,
  "rdma_child_cidr": "10.208.0.0/12",
  "rdma_tools_image": "REPLACE_WITH_REPOSITORY@sha256:REPLACE_WITH_64_HEX_DIGEST",
  "rdma_image_pull_secret": "ocir-pull",
  "rdma_tos": 41
}'
api GET /api/v1/preflight
api POST /api/v1/jobs '{"action":"check_hosts","confirmation":"hpc-oke-rdma"}'
```

For a public image set the secret name to an empty string. Ensure the child CIDR does not overlap your VCN/pod/service/fabric ranges. Poll the host-check job until complete. Require successfully collected host facts and resolved findings before installation. Check both Kubernetes and RDMA readiness.

The checks target `oci-rdma=true,oke-rdma-mode=dranet`. Host-check results may be reused for 24 hours and is invalidated after reboot. An empty facts object is a collection failure; investigate execution before changing every host setting.

## 5. Install Dranet and validate RDMA

Submit installation only after the host-check result passes:

```bash
api POST /api/v1/jobs '{"action":"install_dranet","confirmation":"hpc-oke-rdma"}'
```

Poll that job, then inspect publication:

```bash
kubectl -n kube-system get daemonsets,pods -o wide
kubectl get deviceclass dra.net
kubectl get resourceslices
api GET /api/v1/rdma/discovery
```

Require driver readiness and devices for eligible workers. Restricted access mode needs administrator bootstrap; see section 8.

With two free workers, submit the combined validation:

```bash
api POST /api/v1/jobs '{"action":"rdma_validation","confirmation":"hpc-oke-rdma","server_node":"","client_node":""}'
```

Empty node values select automatically. To pin endpoints, replace them with two distinct Kubernetes node names observed in your pool. Poll the job. Expected: measured bandwidth and latency and successful cleanup. The test uses 64 KiB bandwidth and 8-byte latency writes, 5,000 iterations each. Latency is perftest's half-round-trip estimate in microseconds. No fixed throughput is assumed.

Require both bandwidth and latency to pass. After fleet or configuration changes, run enough pair tests to cover the affected workers. Host checks and scaling do not run performance tests; scheduled pair testing is a separate opt-in.

## 6. Export and run a workload

Export complete manifests using the saved profile:

```bash
umask 077
api GET /api/v1/exports/values > dranet-values.yaml
api GET /api/v1/exports/workload > rdma-workload.yaml
```

Check both helper commands succeeded before using their files. Review the entire workload YAML, including image, namespace, secret, ResourceClaim selector, resources and command. The example uses a tools container; replace its command with your intended application and use an image containing that application.

```bash
kubectl apply --dry-run=server -f rdma-workload.yaml
kubectl apply -f rdma-workload.yaml
kubectl -n rdma-workloads get pods,resourceclaims
```

Use names from the exported YAML for `kubectl describe pod`, `kubectl logs` and claim inspection. A Pending pod can mean no free RDMA NIC, an unresolved claim, an image issue or scheduling constraints; inspect events before guessing.

Each independently scheduled RDMA pod needs a claim. Do not launch extra replicas all referencing a single claim unless your application design supports that consumption model. Stop application controllers before deleting their pods. Release completed claims only after the consumers have stopped.

## 7. Inspect failed diagnostics and reservations

List supported fixed profiles and run one for a real node:

```bash
api GET /api/v1/diagnostics/profiles
api POST /api/v1/diagnostics/runs '{"node_name":"REPLACE_WITH_NODE_NAME","profile":"dranet-readiness","confirmation":"hpc-oke-rdma"}'
```

Poll the returned job. Retrieve the exact report path from its result; do not assume a host-check parent job ID equals each child diagnostic's report ID:

```bash
api GET /api/v1/diagnostics/reports/REPLACE_WITH_REPORT_ID/text
api GET /api/v1/diagnostics/reports/REPLACE_WITH_REPORT_ID/json
```

### Inspect a running diagnostic

Use a second SSH session while the check runs:

```bash
kubectl -n oke-rdma-diagnostics get pods -w
```

After noting the exact pod name, stop the watch with Ctrl-C and inspect it before automatic deletion:

```bash
kubectl -n oke-rdma-diagnostics describe pod REPLACE_WITH_POD_NAME
kubectl -n oke-rdma-diagnostics get pod REPLACE_WITH_POD_NAME -o json
kubectl -n oke-rdma-diagnostics logs REPLACE_WITH_POD_NAME -c diagnostic
kubectl -n oke-rdma-diagnostics get events --field-selector involvedObject.name=REPLACE_WITH_POD_NAME --sort-by=.metadata.creationTimestamp
```

Inspect `status.containerStatuses[].state.terminated` for reason, exit code and message, plus pod reason/message and image events. A terminated-container error from log retrieval does not explain why the container stopped. The report saves pod status and recent events before deleting the pod. The report identifies any errors while collecting output. Namespace events eventually expire.

| Result | What to investigate next |
|---|---|
| FailedToRetrieveImagePullSecret | Secret name, existence and namespace; [image and registry setup](IMAGE-PULL-SECRET.md). |
| Unauthorized image pull | Registry hostname, token validity and repository permissions. |
| Image not found | Repository and exact pushed digest. |
| Registry connection timeout | Egress, DNS, routing and registry reachability. |
| Nonzero exit / shell or command error | Tools-image contents and the diagnostic's output. |
| OOMKilled / deadline exceeded | Termination state, resource use and timing; do not infer solely from elapsed duration. |
| Facts collected with missing PF/address/NRI | Specific image/bootstrap/OCA/runtime prerequisite. |

### Inspect device claims before cleanup

```bash
kubectl get resourceclaims -A
kubectl get resourceclaimtemplates -A
kubectl -n rdma-workloads get pods -o wide
kubectl -n rdma-workloads describe resourceclaim REPLACE_WITH_CLAIM_NAME
```

A retained claim can reserve a NIC without a running pod. Inspect allocations and `reservedFor` consumers. Do not bulk-delete claims to fix an “allocated” label.

## 8. Direct Helm administrator route

This section uses `helm` and `kubectl` directly. It requires cluster-administrator privileges for driver infrastructure, independently of operator workload RBAC. Use it for the restricted-access bootstrap or deliberate manual administration. The terminal API remains available for guarded host checks, measurements and audit history.

1. Extract the full **project** on the operator and change into its root (the directory containing `provisioning/`, `control/` and `workloads/`). The compact ORM ZIP is not the source checkout.
2. In restricted mode, have a cluster administrator save and apply the full `operator_rbac_manifest` Terraform output. Do not apply only a fragment. Verify operator access separately.
3. Complete the registry and host-check procedures above. Do not install the driver over an unresolved PF ownership problem.
4. Fetch the pinned vendor inputs:

```bash
./provisioning/fetch-vendor.sh
```

5. Export complete values using section 6 after saving your profile. From the project root, keep that exported file at `./dranet-values.yaml`, then install:

```bash
control/vendor/helm upgrade --install dranet control/vendor/dranet-v1.5.0.tgz \
  --namespace kube-system --values ./dranet-values.yaml --wait --timeout 3m
kubectl apply --server-side --field-manager=oke-rdma-bootstrap \
  -f workloads/dranet/deviceclass.yaml -f workloads/dranet/namespaces.yaml
control/vendor/helm status dranet -n kube-system
kubectl get deviceclass dra.net
kubectl get resourceslices
```

6. Inspect the installed DaemonSet and pod readiness; identify its name with `kubectl -n kube-system get daemonsets`. Use `kubectl -n kube-system rollout status daemonset/ACTUAL_NAME --timeout=180s`.
7. Run section 5's combined validation and section 6's application workflow.

Manual installation does not automatically create a successful Control Center installation job. Refresh discovery and verify configuration before declaring setup complete. Do not change PF/child range under allocated claims. Do not run a separate unguarded node-pool resize alongside Resource Manager ownership.

## 9. Scale from the terminal

The following route uses the same existing Resource Manager stack and exact-plan safeguards as the UI. It does not call a direct OKE resize API.

### Connect and review

```bash
api PUT /api/v1/capacity/connection '{"stack_id":"REPLACE_WITH_OWNING_ORM_STACK_OCID","confirmation":"hpc-oke-rdma"}'
api GET /api/v1/capacity
api GET '/api/v1/capacity/readiness?target=4'
```

Use the real `ocid1.ormstack...` value. Readiness checks are read-only and do not establish future plan contents or write permissions. Verify cycling disabled, nonzero eviction grace, force termination disabled, stack ownership and permissions. Resolve unrelated Terraform drift separately at the current count.

### Create a plan

```bash
api POST /api/v1/jobs '{"action":"capacity_plan","target_node_count":4,"confirmation":"hpc-oke-rdma"}'
```

Poll the returned job, then GET `/api/v1/capacity`. Planning changes the stack target variable but does not apply infrastructure. Review the returned operation, count change, gate result and OCI PLAN job. Copy the exact **capacity operation ID**; it is a 64-character value, not the API job UUID or OCI job OCID.

After reviewing an allowed current plan, submit:

```bash
api POST /api/v1/jobs '{"action":"capacity_apply","capacity_operation_id":"REPLACE_WITH_REVIEWED_OPERATION_ID","confirmation":"SCALE hpc-oke-rdma"}'
```

Poll its job and capacity state. Wait for the Apply to finish and for host/device readiness to pass. No automatic performance benchmark is performed.

### Discard or recover

Use only the action appropriate to the recorded operation's current phase:

```bash
# Before apply: restore the previous stack target after reviewing the pending plan.
api POST /api/v1/jobs '{"action":"capacity_discard","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"DISCARD hpc-oke-rdma"}'

# Continue monitoring/recovery of the recorded operation after interruption.
api POST /api/v1/jobs '{"action":"capacity_resume","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"hpc-oke-rdma"}'

# Only after reviewing a terminal failed/canceled Apply in OCI.
api POST /api/v1/jobs '{"action":"capacity_close","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"CLOSE hpc-oke-rdma"}'
```

Do not run these three as a sequence. Discard is unavailable after apply submission. Closing a failure changes neither infrastructure nor desired stack variables. Never resubmit an uncertain Apply without checking the recorded OCI job.

### Zero and scale-in

Targets are **0–64**. For scale-in, pause controllers, stop application pods, preserve needed node-local data and release application RDMA claims/templates. Keep the pool idle through the operation. Infrastructure pods honor disruption budgets; the tool does not force-delete applications. OKE decides which workers are removed.

Use the same readiness/plan/review/apply sequence with target `0`, then later a positive target to restore workers. The cluster/operator remain provisioned. An initially empty deployment needs host checks and initial Dranet installation after its first scale-up. Cross-node validation needs at least two free workers.

For a blocked retained plan, from a matching full project root:

```bash
sudo /opt/oke-rdma-control/venv/bin/python provisioning/explain-capacity-plan.py --recorded
```

It reads the plan without applying it. Review safe changed-field output, then discard the blocked target and reconcile intentional non-scaling changes separately. Do not bypass the guard for IAM repair or a real eviction-setting change.

## 10. Cleanup, reset and updates

### Interrupted validation

Inspect the failed job first. To clean only the tool-owned validation resources:

```bash
api POST /api/v1/jobs '{"action":"cleanup_validation","confirmation":"hpc-oke-rdma"}'
```

Poll completion and inspect claims before retesting. This does not clean arbitrary application resources.

### Application cleanup

Stop the owning controller first. For the supplied example, after confirming these are the intended names:

```bash
kubectl -n rdma-workloads delete pod rdma-example --wait=true
kubectl -n rdma-workloads delete resourceclaim rdma-example --wait=true
```

### Reset RDMA setup

Stop application workloads and release their claims/templates. Retrieve the preview:

```bash
api GET /api/v1/reset-plan
```

Review every removal and blocker, require `can_reset`, and copy the returned current `plan_id`. Only then submit:

```bash
api POST /api/v1/jobs '{"action":"reset_rdma","reset_plan_id":"REPLACE_WITH_REVIEWED_PLAN_ID","confirmation":"RESET hpc-oke-rdma"}'
```

Poll the job. A changed inventory requires a fresh preview. Reset retains infrastructure, workers, host drivers, namespaces, pull secrets and history; it removes supported RDMA setup and owned test resources and clears runtime overrides and cached host checks. Repeat setup afterward. It does not repair a missing registry secret, reboot workers or destroy the cluster.


## 11. Run additional performance tests

First complete the quick pair test. Use idle workers. Replace the environment name and choose one profile: `sweep`, `loaded`, `numa`, `mpi` or `matrix`.

```bash
api POST /api/v1/jobs '{"action":"rdma_validation","performance_profile":"numa","server_node":"","client_node":"","confirmation":"hpc-oke-rdma"}'
```

Poll the returned job ID. Automatic MPI uses up to eight free workers; selecting both endpoints uses that pair. Matrix measures all ordered pairs among two to eight free workers. Read [performance measurement](PERFORMANCE.md) before interpreting results.

## 12. Diagnose, recover or replace one worker

List node names with `kubectl get nodes`. Use the fixed diagnostic profiles in section 7. Recovery and replacement require a reviewed preview and a separate typed confirmation. The complete terminal requests are in [worker recovery](RECOVERY.md) and [worker replacement](REPLACEMENT.md). Never run reset or delete a worker merely because diagnostic collection failed.

## 13. End a session or stop using the environment

Closing SSH or the browser tunnel does not stop OCI resources or cancel a queued job. For a temporary pause, stop workloads and use the reviewed scale-to-zero workflow; the operator, cluster and other resources remain. For permanent removal, follow [shutdown and cleanup](OPERATIONS.md).

Use [troubleshooting](TROUBLESHOOTING.md) when a command fails. `api: command not found` means the helper in section 3 has not been defined in this shell; paste its complete definition again.

## 14. Monitor from the terminal

Read the saved settings and latest snapshot:

```bash
api GET /api/v1/monitoring
```

Run an on-demand sweep using the saved settings:

```bash
api POST /api/v1/jobs '{"action":"monitoring_sweep","confirmation":"hpc-oke-rdma"}'
```

Poll the returned job. If idle-host refresh is enabled, this can run one privileged host diagnostic. To enable a passive 15-minute schedule, replace the confirmation name and submit the complete settings below. This request replaces the saved monitoring configuration, so retain any topic or maintenance pause you want to keep:

```bash
api PUT /api/v1/monitoring '{
  "enabled": true,
  "interval_seconds": 900,
  "stale_seconds": 7200,
  "refresh_idle_hosts": false,
  "nightly_pair": false,
  "nightly_hour_utc": 1,
  "maintenance_until": 0,
  "claim_age_seconds": 86400,
  "notification_topic": "",
  "confirmation": "hpc-oke-rdma"
}'
```

Approve a reference only after reviewing a worker’s fresh healthy profile in the snapshot. Replace the node name:

```bash
api POST /api/v1/jobs '{"action":"monitoring_baseline","node_name":"REPLACE_WITH_NODE_NAME","confirmation":"hpc-oke-rdma"}'
```

See [monitoring](MONITORING.md) for each field, alert delivery, metrics authentication and application updates.
