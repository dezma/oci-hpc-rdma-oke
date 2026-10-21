resource "oci_identity_dynamic_group" "operator" {
  count          = var.create_operator_dynamic_group ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-operator"
  description    = "Instance-principal identity for the private OKE RDMA operator VM."
  matching_rule  = "ALL {instance.id = '${oci_core_instance.operator.id}'}"
}

resource "oci_identity_policy" "operator" {
  count          = var.create_operator_policy ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.name_prefix}-operator"
  description    = "Scoped access for the private OKE RDMA operator VM."
  freeform_tags  = local.tags

  statements = local.operator_policy_statements
}

# Capacity is opt-in. Resource Manager executes with the caller's resource access;
# read permissions support full-stack refresh, while writes support pool resizing.
locals {
  capacity_compartment_statements = [
    "Allow ${local.operator_iam_principal} to {ORM_STACK_READ, ORM_STACK_USE, ORM_STACK_UPDATE} in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read orm-jobs in compartment id ${var.compartment_ocid} where target.stack.id = '${var.capacity_stack_ocid}'",
    "Allow ${local.operator_iam_principal} to manage orm-jobs in compartment id ${var.compartment_ocid} where all {target.stack.id = '${var.capacity_stack_ocid}', any {target.job.operation = 'PLAN', target.job.operation = 'APPLY'}}",
    "Allow ${local.operator_iam_principal} to {CLUSTER_NODE_POOL_UPDATE} in compartment id ${var.compartment_ocid} where target.nodepool.id = '${oci_containerengine_node_pool.rdma.id}'",
    "Allow ${local.operator_iam_principal} to manage cluster-node-pools in compartment id ${var.compartment_ocid} where all {target.nodepool.id = '${oci_containerengine_node_pool.rdma.id}', request.operation = 'DeleteNode'}",
    "Allow ${local.operator_iam_principal} to read cluster-work-requests in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to manage instance-family in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read virtual-network-family in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to use subnets in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to use vnics in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to use private-ips in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to use network-security-groups in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read compute-clusters in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read buckets in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read bastion-family in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read policies in compartment id ${var.compartment_ocid}",
  ]
  capacity_tenancy_statements = [
    "Allow ${local.operator_iam_principal} to manage compute-capacity-reports in tenancy",
    "Allow ${local.operator_iam_principal} to read dynamic-groups in tenancy",
    "Allow ${local.operator_iam_principal} to read policies in tenancy",
    "Allow ${local.operator_iam_principal} to inspect compartments in tenancy",
    "Allow ${local.operator_iam_principal} to inspect availability-domains in tenancy",
    "Allow ${local.operator_iam_principal} to read objectstorage-namespaces in tenancy",
  ]
}

resource "oci_identity_policy" "capacity_compartment" {
  count          = var.capacity_stack_ocid != "" && var.create_capacity_compartment_policy ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.name_prefix}-capacity"
  description    = "Opt-in Resource Manager capacity planning and reviewed node-pool scaling."
  statements     = local.capacity_compartment_statements
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "capacity_tenancy" {
  count          = var.capacity_stack_ocid != "" && var.create_capacity_tenancy_policy ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-capacity-read"
  description    = "Read-only tenancy discovery required for Terraform stack refresh."
  statements     = local.capacity_tenancy_statements
  freeform_tags  = local.tags
}

locals {
  operator_dynamic_group_id = var.create_operator_dynamic_group ? oci_identity_dynamic_group.operator[0].id : var.existing_operator_dynamic_group_ocid
  operator_iam_principal    = var.create_operator_dynamic_group ? "dynamic-group ${oci_identity_dynamic_group.operator[0].name}" : "dynamic-group id ${var.existing_operator_dynamic_group_ocid}"
  operator_policy_statements = [
    "Allow ${local.operator_iam_principal} to ${local.operator_cluster_verb} clusters in compartment id ${var.compartment_ocid} where target.cluster.id = '${oci_containerengine_cluster.this.id}'",
    "Allow ${local.operator_iam_principal} to read cluster-node-pools in compartment id ${var.compartment_ocid} where target.nodepool.id = '${oci_containerengine_node_pool.rdma.id}'",
    "Allow ${local.operator_iam_principal} to read instances in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read instance-agent-family in compartment id ${var.compartment_ocid}",
    "Allow ${local.operator_iam_principal} to read objects in compartment id ${var.compartment_ocid} where all {target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}', target.object.name = '${oci_objectstorage_object.operator_payload.object}'}",
  ]
}

# Preserve existing singleton addresses when adopting optional IAM resources.
moved {
  from = oci_identity_dynamic_group.operator
  to   = oci_identity_dynamic_group.operator[0]
}

moved {
  from = oci_identity_policy.operator
  to   = oci_identity_policy.operator[0]
}

moved {
  from = oci_identity_policy.nodepool_compute_cluster
  to   = oci_identity_policy.nodepool_compute_cluster[0]
}
