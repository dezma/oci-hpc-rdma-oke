resource "random_password" "operator_ui_token" {
  length  = 48
  special = false
}

resource "terraform_data" "operator_bootstrap" {
  input = {
    generation = var.operator_bootstrap_generation
    payload    = local.operator_payload_sha256
    cluster    = oci_containerengine_cluster.this.id
  }
}

resource "oci_bastion_bastion" "operator" {
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  target_subnet_id             = oci_core_subnet.private["bastion"].id
  name                         = "${var.name_prefix}-operator-bastion"
  client_cidr_block_allow_list = [var.bastion_client_cidr]
  max_session_ttl_in_seconds   = 60 * max(var.bastion_ssh_session_ttl_minutes, var.bastion_port_forwarding_session_ttl_minutes)
  freeform_tags                = local.tags
}

resource "oci_core_instance" "operator" {
  depends_on = [
    oci_objectstorage_object.operator_payload,
    terraform_data.operator_bootstrap,
  ]

  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "${var.name_prefix}-operator"
  shape               = var.operator_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.operator_shape_ocpus
    memory_in_gbs = var.operator_shape_memory_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.private["operator"].id
    assign_public_ip = false
    display_name     = "${var.name_prefix}-operator-vnic"
    nsg_ids          = [oci_core_network_security_group.operator.id]
  }

  source_details {
    source_type             = "image"
    source_id               = local.operator_image_id
    boot_volume_size_in_gbs = var.operator_boot_volume_gbs
  }

  agent_config {
    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/operator/cloud-init.sh.tftpl", {
      region                              = var.region
      tenancy_id                          = var.tenancy_ocid
      compartment_id                      = var.compartment_ocid
      cluster_id                          = oci_containerengine_cluster.this.id
      cluster_name                        = oci_containerengine_cluster.this.name
      node_pool_id                        = oci_containerengine_node_pool.rdma.id
      compute_cluster_id                  = oci_core_compute_cluster.rdma.id
      capacity_stack_ocid                 = var.capacity_stack_ocid
      environment_name                    = var.name_prefix
      object_namespace                    = data.oci_objectstorage_namespace.this.namespace
      artifact_bucket                     = oci_objectstorage_bucket.artifacts.name
      artifact_object                     = oci_objectstorage_object.operator_payload.object
      artifact_sha256                     = local.operator_payload_sha256
      kubernetes_version                  = var.kubernetes_version
      kubernetes_minor                    = local.kubernetes_minor
      ubuntu_release                      = var.ubuntu_release
      oke_node_package                    = local.oke_node_package
      oke_repo_url                        = local.ubuntu_repo_url
      admin_token                         = random_password.operator_ui_token.result
      operator_kubernetes_access_mode     = var.operator_kubernetes_access_mode
      worker_ssh_enabled                  = var.enable_worker_ssh_from_operator
      bastion_ssh_ttl_seconds             = 60 * var.bastion_ssh_session_ttl_minutes
      bastion_port_forwarding_ttl_seconds = 60 * var.bastion_port_forwarding_session_ttl_minutes
      vcn_cidr                            = var.vcn_cidr
      services_cidr                       = var.services_cidr
      worker_subnet_cidr                  = var.worker_subnet_cidr
      bastion_private_endpoint_ip         = oci_bastion_bastion.operator.private_endpoint_ip_address
    }))
  }

  lifecycle {
    replace_triggered_by = [terraform_data.operator_bootstrap]
  }
}
