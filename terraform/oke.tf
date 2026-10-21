resource "oci_core_compute_cluster" "rdma" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "${var.name_prefix}-compute-cluster"
  freeform_tags       = local.tags
}

resource "oci_identity_policy" "nodepool_compute_cluster" {
  count          = var.create_nodepool_compute_cluster_policy ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.name_prefix}-nodepool-compute-cluster"
  description    = "Allow this OKE managed node pool resource principal to launch workers into its OCI Compute Cluster."
  freeform_tags  = local.tags

  statements = local.nodepool_compute_cluster_statements
}

resource "oci_containerengine_cluster" "this" {
  depends_on         = [terraform_data.preflight]
  compartment_id     = var.compartment_ocid
  kubernetes_version = var.kubernetes_version
  name               = "${var.name_prefix}-oke"
  type               = "ENHANCED_CLUSTER"
  vcn_id             = oci_core_vcn.this.id
  freeform_tags      = local.tags

  cluster_pod_network_options {
    cni_type = "OCI_VCN_IP_NATIVE"
  }

  endpoint_config {
    is_public_ip_enabled = false
    subnet_id            = oci_core_subnet.private["api"].id
    nsg_ids = [
      oci_core_network_security_group.oke_internal.id,
      oci_core_network_security_group.api_operator.id,
    ]
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.private["services"].id]

    add_ons {
      is_kubernetes_dashboard_enabled = false
      is_tiller_enabled               = false
    }

    kubernetes_network_config {
      services_cidr = var.services_cidr
    }
  }
}

resource "oci_containerengine_node_pool" "rdma" {
  depends_on = [oci_identity_policy.nodepool_compute_cluster]

  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = var.compartment_ocid
  kubernetes_version = var.kubernetes_version
  name               = "${var.name_prefix}-rdma"
  node_shape         = var.node_shape
  freeform_tags      = local.tags

  node_source_details {
    source_type             = "IMAGE"
    image_id                = var.hpc_custom_image_ocid
    boot_volume_size_in_gbs = var.node_boot_volume_gbs
  }

  node_metadata = {
    user_data = base64encode(local.managed_node_cloud_init)
  }

  # Optional break-glass access only. Existing nodes receive an updated key only
  # through the OKE node-pool cycling/replacement lifecycle.
  ssh_public_key = trimspace(var.worker_ssh_public_key) != "" ? trimspace(var.worker_ssh_public_key) : null

  node_config_details {
    size               = var.node_count
    compute_cluster_id = oci_core_compute_cluster.rdma.id
    nsg_ids            = [oci_core_network_security_group.oke_internal.id]

    placement_configs {
      availability_domain = var.availability_domain
      subnet_id           = oci_core_subnet.private["workers"].id
      # Do not set fault domains when a Compute Cluster is used.
    }

    node_pool_pod_network_option_details {
      cni_type          = "OCI_VCN_IP_NATIVE"
      max_pods_per_node = var.max_pods_per_node
      pod_subnet_ids    = [oci_core_subnet.private["pods"].id]
      pod_nsg_ids       = [oci_core_network_security_group.oke_internal.id]
    }
  }

  initial_node_labels {
    key   = "oci-rdma"
    value = "true"
  }

  initial_node_labels {
    key   = "oke-rdma-mode"
    value = "dranet"
  }

  node_eviction_node_pool_settings {
    eviction_grace_duration              = "PT60M"
    is_force_delete_after_grace_duration = false
  }

  node_pool_cycling_details {
    is_node_cycling_enabled = false
    maximum_surge           = "1"
    maximum_unavailable     = "0"
  }

  timeouts {
    create = "90m"
    update = "90m"
    delete = "90m"
  }
}

locals {
  nodepool_compute_cluster_statements = [
    "allow any-user to {COMPUTE_CLUSTER_LAUNCH_INSTANCE} in compartment id ${var.compartment_ocid} where all {request.principal.type = 'nodepool', target.resource.id = '${oci_core_compute_cluster.rdma.id}'}"
  ]
}
