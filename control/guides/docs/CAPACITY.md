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

For an existing stack, disabling a policy already tracked in Terraform plans deletion of that policy. Review the plan and retain the switch when the managed policy is still needed. A policy whose creation failed will simply be omitted when its switch is disabled. Do not remove Terraform state entries as part of this option change. Review all other planned resource changes before applying a new release archive.

Leave `capacity_stack_ocid` empty to omit the optional capacity policies. Normal RDMA setup, validation, and reset remain available.

For a provisioned environment, an administrator can grant the capacity policies and enter the stack OCID using **Verify and save connection**. The connection is stored on the operator and persists across service restarts. It can only be changed when no operation or pending scale plan exists. The stack must own both `oci_containerengine_cluster.this` and `oci_containerengine_node_pool.rdma`, and must be ACTIVE in the deployment compartment.

Resource Manager jobs need permissions for the resources Terraform refreshes as well as the resource it changes. The supplied opt-in policies grant:

- Stack read, use, and update permissions in the deployment compartment. Stack update is compartment-scoped; the application verifies the selected stack's ownership.
- Job reads and PLAN/APPLY job management restricted to the selected stack. The application never requests a DESTROY job.
- Node-pool update restricted to the configured node pool.
- Compute instance-family management and the required subnet, VNIC, private-IP, and network-security-group use permissions in the deployment compartment.
- Read access for stack resources, including networking, compute clusters, buckets, Bastion, policies, and tenancy-level discovery.

These are infrastructure administration permissions, including instance management in the deployment compartment. Enable them deliberately in an appropriately scoped deployment compartment. The policy does not grant IAM write permissions. Review the `capacity_management_policy` Terraform output or [iam.tf](../terraform/iam.tf) before activation.

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
2. Select **Generate scale plan** and confirm the environment name. This updates only the stack's `node_count` variable and creates a PLAN job. Existing stack variables are preserved using an ETag-protected update. Infrastructure has not been resized at this stage.
3. Review the before/after worker count and the sanitized Terraform change summary. The guard permits exactly one in-place node-pool size update. It rejects replacements, unknown configuration changes, node cycling, and changes to any other managed resource. Raw plans and Terraform state are never sent to the browser or retained as application reports.
4. Select **Review and apply plan** and type `SCALE <environment name>`. The application rechecks the stack inputs, ETag, latest OCI job, plan artifact, pool state, and workloads, then applies the exact reviewed PLAN job. It never uses an automatically approved apply.
5. Track the OCI apply, worker readiness, Dranet device publication, and new-worker host evidence beside the action. New workers inherit the node-pool image, bootstrap, labels, and Dranet DaemonSet configuration.

Approval expires after 30 minutes. An expired or changed plan must be discarded and regenerated. **Discard target** restores the previous `node_count` value, including removing the override if the original stack used its Terraform default. Discard does not apply infrastructure changes. If stack inputs changed externally, discard refuses to overwrite them; reconcile the pending target with the stack administrator first.

A pending operation prevents another scale plan, RDMA reset, validation, or driver/settings change from this tool. Read-only views, diagnostics, and host checks remain available. Coordinate other administrators and autoscalers: only one process should control the node count. Changes made outside the tool invalidate its reviewed plan.

## Scale-in protection

Stop application workloads and keep their controllers paused until scale-in finishes. Scale-in is blocked while the RDMA pool has application pods, RDMA ResourceClaims, or RDMA claim templates, including pending reservations. A retained claim must be released by its owner. Kubernetes infrastructure pods may drain under their existing disruption budgets.

Every current worker must be Ready and schedulable before the operation begins. Force termination must be explicitly disabled and eviction grace must be nonzero. These checks run again immediately before apply. OKE determines which workers a node-count reduction removes, so the tool requires the application pool to be idle rather than promising a particular node will be removed. It does not override disruption budgets, strip finalizers, or force-delete application pods. There is no global lock against changes made by external Kubernetes administrators; keep application controllers paused as instructed.

Scaling in terminates workers and can discard their node-local data. Keep durable application data on appropriate persistent storage. Increasing worker count adds capacity; it does not change the rank count of an already-running MPI job.

## Readiness and recovery

The tool waits for the requested pool size, Kubernetes readiness, Dranet readiness, and valid published RDMA devices. It then collects host evidence only for new workers or workers whose boot identity changed. Current evidence for other workers is reused. No automatic bandwidth or latency benchmark is run: a capacity/readiness operation is distinct from a measured cross-node workload validation.

If Terraform applies successfully but workers or RDMA devices are still initializing, **Resume recorded operation** continues monitoring and verification. OCI job IDs and submission phases are persisted before and after remote calls. A worker interruption or lost API response never causes an apply to be silently resubmitted. When submission outcome is uncertain, resume searches for the unique recorded operation; if it cannot identify a single job, inspect Resource Manager before taking further action.

An apply that has been submitted cannot be discarded. A terminal failed/canceled apply can be explicitly acknowledged using **Close failed apply** after inspecting its OCI job. This releases the local operation lock without changing infrastructure or stack variables. Review actual capacity and the stack target before generating a new reconciliation plan. A successful infrastructure apply with failed host readiness is reported as needing attention; the tool does not automatically roll infrastructure back.

The UI refreshes observed capacity on request and during operations. Operation history retains the reviewed counts, plan hash, OCI job IDs, progress, and host reports. Source-controlled variable files should reflect the intended node count when moving the same deployment to a different Terraform execution workflow.

## Readiness before planning

Open **Workers & scaling → Scale workers**, choose the target, then select **Check scaling prerequisites**. This read-only review checks stack ownership, read access, active ORM jobs, cycling configuration, worker readiness and direction-specific restrictions. It makes no stack or infrastructure changes. Write permissions and the contents of a future Terraform plan remain explicitly unverified. Generate-plan becomes available after a passing review; the review expires after five minutes or a changed target. The backend repeats its safety checks for each operation. Stack connection errors identify the OCID, ownership or access issue separately from the environment confirmation field.

## Explain a blocked scale plan

A scale plan may change settings other than `node_config_details[0].size`, for example after a configuration edit or detected drift. The gate reports the changed field paths, with a limit of 30, and withholds values and metadata/tag map keys. It continues to reject replacements, unknown configuration and unrelated changes. Review the exact differences in Resource Manager before deciding whether maintenance is required.

For a previously recorded plan, run the read-only helper on the operator using the source checkout:

```bash
sudo /opt/oke-rdma-control/venv/bin/python provisioning/explain-capacity-plan.py --recorded
```

In the extracted operator update, the helper is at the archive root instead of `provisioning/`. It reads the recorded OCI plan, verifies its job/stack identity and prints the gate result. It does not update the stack, submit jobs, or alter the pending operation. Share the field-path output when diagnosing a rejection; the complete Terraform plan can contain sensitive values.

After review, discard a blocked scaling target to restore the previous desired count. Apply any intended non-scaling configuration changes through a separately reviewed Resource Manager plan at the current worker count. Generate a fresh scale plan only once the baseline is aligned. A gate rejection alone does not prove which proposed change is desirable.

Equivalent known eviction durations (for example, `PT1H` and `PT60M`) are treated as the same setting by the size-only plan check. Actual grace-period changes, force-deletion changes, unknown values and unrelated edits remain blocked. The error reports safe before/after grace values; apply intended maintenance separately at the current worker count.


## Scale-in and single-worker diagnosis (v2.5.2)

In Setup & validation, expand step 2 and choose **Diagnose / recover this worker** on the affected host. The worker dialog opens with the full RDMA support bundle selected. Run the diagnostic, review findings, and download the report. **Assess recovery** checks eligibility without restarting anything. Only an eligible assessment offers separately confirmed recovery; physical no-cable evidence blocks an agent restart. These are the same actions available in Workers & scaling.

Scale-in no longer requires healthy RDMA or a ready Dranet device on workers being removed. Kubernetes readiness, pool membership, safe eviction, workload and claim checks, and the reviewed size-only Terraform plan still apply. Zero removes every worker in this pool. Partial scale-in lets OKE choose removals and is not a way to target one faulty worker. Existing-worker RDMA health remains required for scale-out, with the worker and actual issue shown.


## Pin the operator image before worker scaling

Resource Manager shows a dropdown of image **names**, not a text field called `operator_image_ocid`. In the stack's variable form, expand **Advanced operator VM** and find **Approved operator image override**. That dropdown saves the selected image's OCID in the internal Terraform variable `operator_image_ocid`. New v2.5.3 Resource Manager forms require a selection.

For a new deployment, choose a specific approved Oracle Linux 9 image compatible with the operator shape. Record both its full name and OCID. For an existing deployment, choose the image already recorded for the operator, not the newest image. The legacy empty setting selects the newest compatible image on each plan, so a later scale plan can also propose an operator image change. An application-only operator update does not change the stack's form or saved variables.

If scaling is blocked on `oci_core_instance.operator` and `source_details[0].source_id`, inspect the recorded plan first. Obtain the **before** image OCID, then look up its display name in OCI Compute Images or from the operator VM:

```bash
# Run on the operator VM. Substitute the before-image OCID from the recorded plan.
oci compute image get --image-id '<RECORDED_IMAGE_OCID>' \
  --auth instance_principal --query 'data.{name:"display-name",id:id}'
```

If image lookup is denied, use an administrator with image-read access. If the old image is unavailable or absent from the dropdown, stop and review the stack variable with an administrator; do not substitute a similarly named or newer image.

1. Copy the recorded image name and OCID before discarding the plan.
2. In the Control Center choose **Discard target**. This restores the previous worker-count target without resizing workers.
3. Edit the Resource Manager stack variables. Under **Advanced operator VM → Approved operator image override**, select the matching image name and verify its OCID. Keep the worker count at the current count (three in the reported case). Save the variables.
4. Refresh capacity in the Control Center and generate a fresh plan for the intended worker count. Only the worker-count change should remain. Investigate any other changed resource before applying.

For direct Terraform use, set `operator_image_ocid` explicitly in your tfvars. The empty-value fallback remains for legacy CLI compatibility; it is not a stable image pin. Image upgrades are separate maintenance changes requiring their own plan review. Do not weaken the size-only scaling gate or add lifecycle ignores to hide an unreviewed image change.


For single-worker replacement in v2.7.0, see [REPLACEMENT.md](REPLACEMENT.md). Review the updated policy generator: it adds pool-scoped DeleteNode operation permission and OKE work-request reads. Application updates do not install IAM.


## Required capacity-report step before scale-up (v2.7.2)

1. Install the capacity-report IAM permission on the operator's dynamic group at tenancy scope:
   `Allow dynamic-group id <operator-dynamic-group-ocid> to manage compute-capacity-reports in tenancy`.
   This resource type grants only COMPUTE_CAPACITY_REPORT_CREATE; it does not grant instance creation.
   The updated capacity-policy.py generator includes it in tenancy.json. Follow the existing IAM
   procedure to review and update the policy. The application updater does not install IAM.
2. In Workers & scaling, choose the desired total worker count. Select **Check capacity and scaling
   prerequisites**. The report uses the live managed pool's shape, shape configuration, AD and any
   explicit fault domains, with the configured tenancy OCID as the root compartment.
3. Review the OCI capacity row, shape, region and check timestamp. AVAILABLE is a point-in-time
   availability indication. Counts are shown only when the API returns a nonnegative integer;
   otherwise the UI says **Not disclosed**. A documented count field does not mean the tenancy
   has access to an exact remaining capacity count.
4. Generate the plan. The backend queries OCI again before changing the stack target. A failed
   capacity check at this stage leaves stack variables unchanged and creates no capacity operation.
5. Review and apply the size-only plan. OCI capacity is queried again before submitting APPLY.
   If this check fails, the plan remains AWAITING_REVIEW; the stack target is still the planned
   target, but no APPLY is submitted. Retry Apply before expiry or discard the target. An expired
   plan must be discarded and regenerated. Never delete the journal to bypass a failed check.
6. Continue normal new-worker host, Dranet device and cross-node RDMA validation after provisioning.

OUT_OF_HOST_CAPACITY, HARDWARE_NOT_SUPPORTED, an unknown status, permission failure, timeout,
missing placement data or an unusable response blocks scale-up. Unavailable reports are not shown
as zero capacity. An AVAILABLE response without a count can proceed; it does not establish that
all requested workers can be allocated. For a single returned scope, a disclosed count below the
requested increment blocks scale-up. Multi-scope reports are not summed and do not establish
count sufficiency; conservatively, every returned configured scope must report AVAILABLE.
Scale-down does not query or require this report. A zero-worker pool can be checked using its
configured shape and placement; running workers are not required for the report.

Reports are snapshots, not reservations or quota checks. They do not establish capacity within a
specific Compute Cluster or validate RDMA health. These remain separate from desired, observed,
Kubernetes Ready and RDMA-verified counts. The operation retains planning and apply report evidence,
including collection time. An old snapshot is never reused to authorize a new plan or apply.

Operator API: `api GET '/api/v1/capacity/readiness?target=3'` uses the same check. Planning/applying through API jobs
also enforces the reports, so the UI cannot bypass them.

References:
- https://docs.oracle.com/en-us/iaas/tools/oci-cli/latest/oci_cli_docs/cmdref/compute/compute-capacity-report/create.html
- https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/models/oci.core.models.CapacityReportShapeAvailability.html
- https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/corepolicyreference_topic-Details_for_Verb__ResourceType_Combinations.htm
