data "oci_containerengine_cluster_option" "all" {
  cluster_option_id              = var.oke_cluster_option_id
  compartment_id                 = var.compartment_ocid
  should_list_all_patch_versions = true
}

data "oci_core_shapes" "rdma_candidate" {
  compartment_id      = var.compartment_ocid
  availability_domain = var.availability_domain
  image_id            = var.hpc_custom_image_ocid
  shape               = var.node_shape
}

data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_ocid
}


locals {
  tags = merge(var.freeform_tags, {
    environment = var.name_prefix
  })

  supported_kubernetes_versions = data.oci_containerengine_cluster_option.all.kubernetes_versions

  kubernetes_version_without_v = trimprefix(var.kubernetes_version, "v")
  kubernetes_version_parts     = split(".", local.kubernetes_version_without_v)
  kubernetes_minor             = join(".", slice(local.kubernetes_version_parts, 0, 2))

  # Oracle's published Ubuntu node-package compatibility matrix. Keep this map explicit
  # rather than assuming the package patch always equals the selected OKE patch.
  ubuntu_oke_node_packages = {
    "1.27" = "oci-oke-node-all-1.27.10"
    "1.28" = "oci-oke-node-all-1.28.10"
    "1.29" = "oci-oke-node-all-1.29.1"
    "1.30" = "oci-oke-node-all-1.30.10"
    "1.31" = "oci-oke-node-all-1.31.10"
    "1.32" = "oci-oke-node-all-1.32.10"
    "1.33" = "oci-oke-node-all-1.33.10"
    "1.34" = "oci-oke-node-all-1.34.10"
    "1.35" = "oci-oke-node-all-1.35.2"
    "1.36" = "oci-oke-node-all-1.36.4"
  }
  oke_node_package = lookup(local.ubuntu_oke_node_packages, local.kubernetes_minor, "")

  operator_image_id = var.operator_image_ocid

  ubuntu_repo_url = "https://objectstorage.us-sanjose-1.oraclecloud.com/n/odx-oke/b/okn-repositories/o/prod/${var.ubuntu_release}/kubernetes-${local.kubernetes_minor} stable main"

  managed_node_cloud_init = templatefile("${path.module}/operator/managed-ubuntu-hpc.yaml.tftpl", {
    oke_repo_url     = local.ubuntu_repo_url
    oke_node_package = local.oke_node_package
  })

  operator_payload_path   = "${path.module}/operator/payload/oke-rdma-control.tar.gz"
  operator_payload_sha256 = filesha256(local.operator_payload_path)
  operator_payload_name   = "releases/oke-rdma-control-${local.operator_payload_sha256}.tar.gz"

  operator_cluster_verb = var.operator_kubernetes_access_mode == "iam_admin" ? "manage" : "use"
}

resource "terraform_data" "preflight" {
  input = {
    kubernetes_version = var.kubernetes_version
    kubernetes_minor   = local.kubernetes_minor
    oke_node_package   = local.oke_node_package
    operator_image     = local.operator_image_id
  }

  lifecycle {
    precondition {
      condition     = var.create_operator_dynamic_group ? var.existing_operator_dynamic_group_ocid == "" : var.existing_operator_dynamic_group_ocid != ""
      error_message = "Create the operator dynamic group with an empty existing OCID, or disable creation and supply its existing dynamic-group OCID."
    }

    precondition {
      condition     = contains(local.supported_kubernetes_versions, var.kubernetes_version)
      error_message = "The selected full Kubernetes patch version is not in the live OKE option set for this tenancy/region."
    }

    precondition {
      condition     = tonumber(local.kubernetes_version_parts[0]) == 1 && tonumber(local.kubernetes_version_parts[1]) >= 34
      error_message = "Dranet requires Kubernetes 1.34 or later with the stable resource.k8s.io/v1 DRA APIs."
    }

    precondition {
      condition     = local.oke_node_package != ""
      error_message = "No Ubuntu OKE node package is mapped for Kubernetes ${local.kubernetes_minor}. Review Oracle's Ubuntu Node Package Compatibility matrix and update the quickstart compatibility map before deploying."
    }

    precondition {
      condition     = local.operator_image_id != ""
      error_message = "An explicit approved operator image is required. Set operator_image_ocid; latest-image selection is not supported."
    }

    precondition {
      condition     = length(data.oci_core_shapes.rdma_candidate.shapes) == 1
      error_message = "The selected CPU RDMA shape is not available in the chosen AD with the supplied custom image. Verify shape/image/AD compatibility and the custom image OCID."
    }

    precondition {
      condition     = length(data.oci_core_shapes.rdma_candidate.shapes) == 1 ? try(data.oci_core_shapes.rdma_candidate.shapes[0].rdma_ports, 0) > 0 : false
      error_message = "The selected node shape does not expose RDMA ports according to the live OCI shape catalog."
    }

    precondition {
      condition     = length(data.oci_core_shapes.rdma_candidate.shapes) == 1 ? try(data.oci_core_shapes.rdma_candidate.shapes[0].gpus, 0) == 0 : false
      error_message = "This quickstart is intentionally CPU-RDMA focused; select an RDMA-capable shape without GPUs."
    }

    precondition {
      condition     = !var.enable_worker_ssh_from_operator || trimspace(var.worker_ssh_public_key) != ""
      error_message = "worker_ssh_public_key is required when enable_worker_ssh_from_operator is true."
    }
  }
}
