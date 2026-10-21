# Worker capacity management

The Control Center changes worker count through the Resource Manager stack that owns the cluster. Terraform remains responsible for `node_count`; the application does not resize OKE directly or edit Terraform state. The supported range is 0–64 workers, with a provisioning default of two. Zero pauses worker capacity while retaining the cluster, operator and infrastructure. Workloads and in-cluster services cannot run when no other worker pool is available. One worker is supported, but cross-node bandwidth and latency validation requires two free, ready workers. Scale up from zero through the same reviewed Resource Manager plan. New workers run their provisioning initialization; retained Dranet configuration reconciles automatically. A newly provisioned empty pool needs host checks and Dranet setup after its first scale-up.

## Enable access

For a new deployment:

1. Create the Resource Manager stack with **Run apply** cleared.
2. Copy the stack OCID from its details page.
3. Edit stack variables and set **Owning Resource Manager stack OCID** (`capacity_stack_ocid`) to that OCID.
4. Review the IAM permissions below, plan, and apply the stack.
5. Open **Workers & scaling → Scale workers** in the Control Center. The stack connection is preconfigured. Ownership is verified against the stack state before a scale plan is generated.

The Resource Manager form includes **Create compartment-level scaling policy** and **Create tenancy-level scaling policy**. Both default to true and only create resources when `capacity_stack_ocid` is set. Disable either independently to reuse permissions supplied by your IAM administrator. Keep the owning stack OCID configured to retain scaling integration.

| Variable | Controls |
|---|---|
| `create_capacity_compartment_policy` | The additional compartment policy for ORM and pool scaling |
| `create_capacity_tenancy_policy` | The additional tenancy discovery/read policy |

The baseline operator policy, node-pool policy and dynamic group have separate provisioning options; see [IAM provisioning choices](IAM.md). Externally managed permissions must apply to the operator instance: verify membership in the dynamic group named by the existing policy. A compartment-scoped `manage all-resources` grant does not replace tenancy-scoped discovery/read access. Disabling creation does not verify or supply those permissions; use the scaling prerequisite review before planning.

For an existing stack, disabling a policy already tracked in Terraform plans deletion of that policy. Review the plan and retain the switch when the managed policy is still needed. A policy whose creation failed will simply be omitted when its switch is disabled. Do not remove Terraform state entries as part of this option change. Review every planned resource change before applying configuration maintenance.

Leave `capacity_stack_ocid` empty to omit the optional capacity policies. Normal RDMA setup, validation, and reset remain available.

For a provisioned environment, an administrator can grant the capacity policies and enter the stack OCID using **Verify and save connection**. The connection is stored on the operator and persists across service restarts. It can only be changed when no operation or pending scale plan exists. The stack must own both `oci_containerengine_cluster.this` and `oci_containerengine_node_pool.rdma`, and must be ACTIVE in the deployment compartment.

Resource Manager jobs need permissions for the resources Terraform refreshes as well as the resource it changes. The supplied opt-in policies grant:

- Stack read, use, and update permissions in the deployment compartment. Stack update is compartment-scoped; the application verifies the selected stack's ownership.
- Job reads and PLAN/APPLY job management restricted to the selected stack. The application never requests a DESTROY job.
- Node-pool update restricted to the configured node pool.
- Compute instance-family management and the required subnet, VNIC, private-IP, and network-security-group use permissions in the deployment compartment.
- Read access for stack resources, including networking, compute clusters, buckets, Bastion, policies, and tenancy-level discovery.

These are infrastructure administration permissions, including instance management in the deployment compartment. Grant them in the project compartment after administrator review. The policy does not grant IAM write permissions. Review the `capacity_management_policy` Terraform output or [iam.tf](../terraform/iam.tf) before activation.

An administrator can render separate policy requests for a provisioned operator using [capacity-policy.py](../provisioning/capacity-policy.py). Obtain the IDs from the existing stack and its outputs, replace every placeholder, and run this from the source checkout in Cloud Shell:

```bash
python3 provisioning/capacity-policy.py \
  --name-prefix '<environment-name>' \
  --stack-id '<owning-stack-ocid>' \
  --node-pool-id '<node-pool-ocid>' \
  --dynamic-group-id '<operator-dynamic-group-ocid>' \
  --compartment-id '<deployment-compartment-ocid>' \
  --tenancy-id '<tenancy-ocid>' \
  --output-dir capacity-policy
```

Review both JSON files. An IAM administrator can then create the compartment and tenancy policies:

```bash
oci iam policy create --from-json file://capacity-policy/compartment.json
oci iam policy create --from-json file://capacity-policy/tenancy.json
```

The renderer only writes policy requests; it does not submit them. Use either Terraform-managed capacity policies or administrator-managed policies, according to your infrastructure ownership process. No user API key is stored on the operator: requests use its instance principal.

## Node-pool prerequisites

The reference node pool has node cycling disabled and a nonzero eviction grace period with force termination disabled. The complete configuration is in [oke.tf](../terraform/oke.tf). Its lifecycle settings are:

```hcl
node_pool_cycling_details {
  is_node_cycling_enabled = false
  maximum_surge          = "1"
  maximum_unavailable    = "0"
}

node_eviction_node_pool_settings {
  eviction_grace_duration              = "PT60M"
  is_force_delete_after_grace_duration = false
}
```

Confirm these settings in the current stack's Terraform configuration before using capacity management. If maintenance is needed to establish the baseline, review and apply that maintenance separately. Changes to operator payloads, images, IAM, networking, or cycling settings cannot be bundled into a size-only scale apply. Pin your operator image when necessary to keep its selected image stable between plans.

## Plan, review, and apply

1. Refresh capacity and choose a whole target count from 0 through 64.
2. Select **Check capacity and scaling prerequisites** first. Require a passing report, then select **Generate scale plan** and confirm the environment name. This updates only the stack's `node_count` variable and creates a PLAN job. Existing stack variables are preserved using an ETag-protected update. Infrastructure has not been resized at this stage.
3. Review the before/after worker count and the sanitized Terraform change summary. The guard permits exactly one in-place node-pool size update. It rejects replacements, unknown configuration changes, node cycling, and changes to any other managed resource. Raw plans and Terraform state are never sent to the browser or retained as application reports.
4. Select **Review and apply plan** and type `SCALE <environment name>`. The application rechecks the stack inputs, ETag, latest OCI job, plan artifact, pool state, and workloads, then applies the exact reviewed PLAN job. It never uses an automatically approved apply.
5. Track the OCI apply, worker readiness, Dranet device publication, and new-worker host-check results beside the action. New workers inherit the node-pool image, bootstrap, labels, and Dranet DaemonSet configuration.

Approval expires after 30 minutes. An expired or changed plan must be discarded and regenerated. **Discard target** restores the previous `node_count` value, including removing the override if the original stack used its Terraform default. Discard does not apply infrastructure changes. If stack inputs changed externally, discard refuses to overwrite them; reconcile the pending target with the stack administrator first.

A pending operation prevents another scale plan, RDMA reset, validation, or driver/settings change from this tool. Read-only views, diagnostics, and host checks remain available. Coordinate other administrators and autoscalers: only one process should control the node count. Changes made outside the tool invalidate its reviewed plan.

## Scale-in protection

Stop application workloads and keep their controllers paused until scale-in finishes. Scale-in is blocked while the RDMA pool has application pods, RDMA ResourceClaims, or RDMA claim templates, including pending reservations. A retained claim must be released by its owner. Kubernetes infrastructure pods may drain under their existing disruption budgets.

Every current worker must be Ready and schedulable before the operation begins. Force termination must be disabled and eviction grace must be nonzero. These checks run again immediately before apply. OKE determines which workers a node-count reduction removes, so the tool requires the application pool to be idle rather than promising a particular node will be removed. It does not override disruption budgets, strip finalizers, or force-delete application pods. There is no global lock against changes made by external Kubernetes administrators; keep application controllers paused as instructed.

Scaling in terminates workers and can discard their node-local data. Keep durable application data on appropriate persistent storage. Increasing worker count adds capacity; it does not change the rank count of an already-running MPI job.

## Readiness and recovery

The tool waits for the requested pool size and Kubernetes readiness, then runs host checks for new workers or workers whose boot identity changed before waiting for Dranet/device publication. Recent checks for other workers are reused. Run a pair test or matrix after readiness passes. Scheduled monitoring can also run an optional nightly pair test.

If Terraform applies successfully but workers or RDMA devices are still initializing, **Resume recorded operation** continues monitoring and verification. OCI job IDs and submission phases are persisted before and after remote calls. A worker interruption or lost API response never causes an apply to be resubmitted. When submission outcome is uncertain, resume searches for the unique recorded operation; if it cannot identify a single job, inspect Resource Manager before taking further action.

An apply that has been submitted cannot be discarded. A terminal failed/canceled apply can be acknowledged using **Acknowledge failed apply** after inspecting its OCI job. This releases the local operation lock without changing infrastructure or stack variables. Review capacity and the stack target before generating a new reconciliation plan. A successful infrastructure apply with failed host readiness is reported as needing attention; the tool does not automatically roll infrastructure back.

The UI refreshes observed capacity on request and during operations. Operation history retains the reviewed counts, plan hash, OCI job IDs, progress, and host reports. Source-controlled variable files should reflect the intended node count when moving the same deployment to a different Terraform execution workflow.

## Readiness before planning

Open **Workers & scaling → Scale workers**, choose the target, then select **Check capacity and scaling prerequisites**. This read-only review checks stack ownership, read access, active ORM jobs, cycling configuration, worker readiness and direction-specific restrictions. It makes no stack or infrastructure changes. Write permissions and the contents of a future Terraform plan remain unverified. Generate-plan becomes available after a passing review; the review expires after five minutes or a changed target. The backend repeats its safety checks for each operation. Stack connection errors identify the OCID, ownership or access issue separately from the environment confirmation field.

## Explain a blocked scale plan

A scale plan may change settings other than `node_config_details[0].size`, for example after a configuration edit or detected drift. The gate reports the changed field paths, with a limit of 30, and withholds values and metadata/tag map keys. It continues to reject replacements, unknown configuration and unrelated changes. Review the exact differences in Resource Manager before deciding whether maintenance is required.

For a previously recorded plan, run the read-only helper on the operator using the source checkout:

```bash
sudo /opt/oke-rdma-control/venv/bin/python provisioning/explain-capacity-plan.py --recorded
```

It reads the recorded OCI plan, verifies its job/stack identity and prints the gate result. It does not update the stack, submit jobs, or alter the pending operation. Share the field-path output when diagnosing a rejection; the complete Terraform plan can contain sensitive values.

After review, discard a blocked scaling target to restore the previous desired count. Apply any intended non-scaling configuration changes through a separately reviewed Resource Manager plan at the current worker count. Generate a fresh scale plan only once the baseline is aligned. Review each rejected change with the stack administrator.

Equivalent known eviction durations (for example, `PT1H` and `PT60M`) are treated as the same setting by the size-only plan check. grace-period changes, force-deletion changes, unknown values and unrelated edits remain blocked. The error reports safe before/after grace values; apply intended maintenance separately at the current worker count.


## Capacity report permissions and interpretation

The report uses the live node pool's compartment, shape, AD and configured fault domains. The operator identity needs:

```text
Allow dynamic-group id <operator-dynamic-group-ocid> to manage compute-capacity-reports in compartment id <node-pool-compartment-ocid>
```

This permission is included in the generated compartment policy. It creates reports, not instances or reservations. If an instance-principal CLI report already succeeds for this compartment, additional report permission is not required. Other Terraform refresh/discovery permissions remain separate.

The check runs before planning and again before apply. AVAILABLE is a snapshot; the count is displayed only when returned. “Not disclosed” means OCI omitted the count. REPORT_UNAVAILABLE means the request/response could not establish availability; inspect its sanitized error. OUT_OF_HOST_CAPACITY and HARDWARE_NOT_SUPPORTED are returned capacity statuses. Counts do not guarantee placement in the specific Compute Cluster, quota or RDMA readiness.

## Images and worker changes

Select an approved operator image name under Advanced operator VM → Approved operator image override. It must remain pinned during worker scaling, as must the worker image. Review image maintenance separately from node count. Handle unrelated Terraform changes in a separate reviewed maintenance plan.

After scale-out, run pair tests or a matrix covering the new workers. The timing panel shows observed provisioning/readiness stages and time until successful validation covers the new identities. Missing timings remain unknown. See [performance](PERFORMANCE.md).

Use [worker replacement](REPLACEMENT.md) for deliberate replacement of one defective worker while retaining the intended count.


## Reconcile partially provisioned capacity

A failed Apply can leave real infrastructure changes. For example, a request from 3 to 6 may create two additional workers, leaving requested size 6 and provisioned count 5. Acknowledgement does not undo those workers and is not a successful scale operation.

1. Inspect the failed Resource Manager Apply and OKE work requests. Wait for all related work to become terminal.
2. Select **Acknowledge failed apply** if the operation is still pending. The operation remains labelled as a failed Apply, with observed counts retained.
3. Review current requested, OCI provisioned (ACTIVE), Kubernetes registered, Ready and RDMA-ready counts. These measure different stages.
4. Stop application controllers and release RDMA claims/templates. Corrective scale-down retains the normal workload and drain checks even when the target equals the number of provisioned workers.
5. Enter a target at or below the provisioned count, such as 5 to align the request or 3 to shrink the pool. Select **Check capacity and scaling prerequisites**, then **Generate reconciliation plan**.
6. Review the fresh size-only plan. The Terraform change is requested size 6 → 5 (or 3), not a guarantee about which individual workers OKE retains. Reject unrelated image, network or replacement changes.
7. Apply the reviewed plan and wait for current capacity/readiness checks. Revalidate RDMA on the surviving workers.

Reconciliation permits a requested/observed mismatch only for a terminal failed/canceled Apply on the owning stack, no active OKE work, matching surviving ACTIVE OCI and Ready/schedulable Kubernetes workers, and an ACTIVE or NEEDS_ATTENTION pool. Unknown lifecycle states, unmatched or unready workers, workload/claim blockers, unsafe eviction settings and inventory changes after planning still block it. To add workers beyond the surviving count, complete reconciliation first and then use ordinary scale-up. Capacity reports are not required for a reduction, but they remain required for ordinary scale-up.

No rollback, automatic retry or direct node deletion is performed. An unavailable work-request read blocks safely; the operator requires `read cluster-work-requests` in the pool compartment. Older acknowledged-failure records are displayed as failed acknowledgements without rewriting their stored history.

See [testing on an existing operator](TESTING-CAPACITY.md).
