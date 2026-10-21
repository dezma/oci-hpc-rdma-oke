# IAM provisioning choices

Resource Manager exposes every IAM policy created by this project in **IAM policies and operator identity**. Each creation switch defaults to enabled. Clear an individual switch when your administrator supplies the corresponding access externally. The switches control resource creation, not whether the permissions are required.

| Resource Manager option | Terraform variable | Required purpose |
|---|---|---|
| Create baseline operator policy | `create_operator_policy` | Operator access to OKE, worker and agent inspection, and download of its application payload |
| Create OKE node-pool Compute Cluster policy | `create_nodepool_compute_cluster_policy` | Authorize the OKE node-pool resource principal to launch workers into the Compute Cluster |
| Create compartment-level scaling policy | `create_capacity_compartment_policy` | Operator access to Resource Manager and compute/network resources for reviewed scaling |
| Create tenancy-level scaling policy | `create_capacity_tenancy_policy` | Tenancy discovery and IAM read access needed for Terraform refresh |

The two scaling policies are created only when `capacity_stack_ocid` is populated and their switches are enabled. Disabling their creation does not disable the configured stack connection. The `iam_configuration` and `capacity_management_policy` outputs expose ownership flags and required statements for administrator review.

## Reuse an existing operator dynamic group

Clear **Create operator dynamic group** and enter the OCID under **Existing operator dynamic group OCID**. Terraform uses the supplied ID directly without a dynamic-group discovery lookup. Any policies this stack does create reference the existing group by OCID, including groups in a non-default identity domain. The generated Kubernetes RBAC manifest also uses that selected group ID.

The existing group's matching rule must include the operator VM. For automated provisioning, use an administrator-approved matching rule that covers the operator before it boots, such as the appropriate compartment or defined tag scope. Its payload download and OCI authentication depend on those permissions. Providing a group OCID does not change membership or grant access.

If creating a new group, leave the existing OCID empty. Terraform blocks missing or contradictory identity selections. The default group matches only the operator instance.

## Permission scope and principals

A policy granting an operator dynamic group `manage all-resources in compartment ...` applies to that group's members and the specified compartment scope. It does not establish tenancy-level permissions. It also does not replace the separate grant for the OKE **node-pool resource principal** to launch workers in the Compute Cluster. Verify each principal and scope before disabling its policy.

The identity running the Resource Manager job also needs permission to provision the resources selected in the form. The operator's existing permissions and the provisioning caller's permissions are separate. Existing external IAM avoids creating the selected IAM resources; it does not remove the caller's other provisioning requirements.

## Changes to a deployed environment

Disabling creation of a policy already managed by Terraform can plan its deletion. Establish replacement access before making that change, and review the plan. A supplied existing group OCID does not change membership. After operator replacement, verify that an instance-specific rule matches the new VM.

Capacity-report permission belongs in the node-pool compartment. See [capacity](CAPACITY.md) for the statement and the separate Terraform discovery permissions. Worker replacement additionally needs pool-scoped DeleteNode and work-request access; review the generated policy before using [replacement](REPLACEMENT.md).

## Optional monitoring alerts

OCI Notifications alerts need compartment-scoped topic read and publish permissions for the operator dynamic group. These optional grants are configured separately from scaling. Follow [monitoring setup](MONITORING.md#send-alerts-through-oci-notifications) to use an existing topic and confirmed subscription.
