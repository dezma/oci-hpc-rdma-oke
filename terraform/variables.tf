variable "tenancy_ocid" {
  description = "OCI tenancy OCID. Used for the operator dynamic group."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment in which the quickstart resources are created."
  type        = string
}

variable "region" {
  description = "OCI region, for example eu-frankfurt-1."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain containing the Compute Cluster and RDMA managed node pool."
  type        = string
}

variable "name_prefix" {
  description = "Short prefix used for names and tags."
  type        = string
  default     = "oke-rdma"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name_prefix))
    error_message = "name_prefix must start with a lowercase letter and contain 3-24 lowercase letters, digits, or hyphens."
  }
}

variable "oke_cluster_option_id" {
  description = "OKE cluster option ID used by Resource Manager to populate supported Kubernetes versions."
  type        = string
  default     = "all"
}

variable "kubernetes_version" {
  description = "Full OKE Kubernetes patch version, for example v1.36.4. The plan validates it against the live OKE option set."
  type        = string

  validation {
    condition     = can(regex("^v?[0-9]+\\.[0-9]+\\.[0-9]+$", var.kubernetes_version))
    error_message = "kubernetes_version must be a full patch version such as v1.36.4."
  }
}

variable "ubuntu_release" {
  description = "Ubuntu release used by the custom HPC image and Oracle OKE node-package repository."
  type        = string
  default     = "ubuntu-noble"

  validation {
    condition     = contains(["ubuntu-jammy", "ubuntu-noble"], var.ubuntu_release)
    error_message = "ubuntu_release must be ubuntu-jammy or ubuntu-noble."
  }
}

variable "hpc_custom_image_ocid" {
  description = "OCID of the approved Ubuntu HPC custom image used directly by the managed RDMA node pool. It must contain the compatible RDMA/OFED stack and Oracle Cloud Agent."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.image\\.", var.hpc_custom_image_ocid))
    error_message = "hpc_custom_image_ocid must be an OCI image OCID beginning with ocid1.image."
  }
}

variable "node_shape" {
  description = "RDMA and Compute Cluster capable bare-metal CPU shape."
  type        = string
  default     = "BM.Optimized3.36"
}

variable "node_count" {
  description = "Number of managed RDMA workers (0–64). Default two; cross-node validation requires two ready workers."
  type        = number
  default     = 2

  validation {
    condition     = var.node_count >= 0 && var.node_count <= 64 && floor(var.node_count) == var.node_count
    error_message = "node_count must be between 0 and 64."
  }
}

variable "node_boot_volume_gbs" {
  type    = number
  default = 100

  validation {
    condition     = var.node_boot_volume_gbs >= 50
    error_message = "node_boot_volume_gbs must be at least 50 GiB."
  }
}

variable "max_pods_per_node" {
  type    = number
  default = 31
}

variable "bastion_client_cidr" {
  description = "Public workstation or corporate VPN CIDR allowed to create OCI Bastion sessions. Use a /32 for one workstation when practical."
  type        = string
}

variable "enable_worker_ssh_from_operator" {
  description = "Enable break-glass TCP/22 access from the private operator NSG to RDMA worker nodes. Normal Control Center operation does not depend on SSH."
  type        = bool
  default     = false
}

variable "worker_ssh_public_key" {
  description = "Optional SSH public key installed on newly launched RDMA workers. Required only when enable_worker_ssh_from_operator is true. Keep the private key on the administrator workstation and use agent forwarding through the operator."
  type        = string
  default     = ""

  validation {
    condition = (
      trimspace(var.worker_ssh_public_key) == "" ||
      can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.worker_ssh_public_key)))
    )
    error_message = "worker_ssh_public_key must be empty or contain a valid OpenSSH public key."
  }
}

variable "operator_shape" {
  description = "Private operator VM shape."
  type        = string
  default     = "VM.Standard.E5.Flex"
}

variable "operator_shape_ocpus" {
  type    = number
  default = 1
}

variable "operator_shape_memory_gbs" {
  type    = number
  default = 8
}

variable "operator_boot_volume_gbs" {
  type    = number
  default = 60
}

variable "operator_image_ocid" {
  description = "Required pinned Oracle Linux 9 operator image OCID. In Resource Manager select Approved operator image override by name. Retain the existing image when scaling workers."
  type        = string

  validation {
    condition     = can(regex("^ocid1[.]image[.]", var.operator_image_ocid))
    error_message = "Select an explicit approved operator image. Automatic newest-image selection is not supported."
  }
}

variable "operator_kubernetes_access_mode" {
  description = "iam_admin is the easiest single-operator path, scoped to this cluster. least_privilege_rbac keeps OCI IAM at use clusters and requires the generated Kubernetes ClusterRoleBinding to be applied once by a cluster administrator."
  type        = string
  default     = "iam_admin"

  validation {
    condition     = contains(["iam_admin", "least_privilege_rbac"], var.operator_kubernetes_access_mode)
    error_message = "operator_kubernetes_access_mode must be iam_admin or least_privilege_rbac."
  }
}

variable "operator_bootstrap_generation" {
  description = "Increment only when intentionally replacing the operator VM to pick up a new packaged control-center payload."
  type        = number
  default     = 1
}

variable "vcn_cidr" {
  type    = string
  default = "10.70.0.0/16"
}

variable "api_subnet_cidr" {
  type    = string
  default = "10.70.0.0/28"
}

variable "worker_subnet_cidr" {
  type    = string
  default = "10.70.1.0/24"
}

variable "pod_subnet_cidr" {
  type    = string
  default = "10.70.16.0/20"
}

variable "service_lb_subnet_cidr" {
  type    = string
  default = "10.70.32.0/24"
}

variable "operator_subnet_cidr" {
  type    = string
  default = "10.70.48.0/28"
}

variable "bastion_subnet_cidr" {
  type    = string
  default = "10.70.49.0/28"
}

variable "services_cidr" {
  type    = string
  default = "10.96.0.0/16"
}

variable "freeform_tags" {
  type = map(string)
  default = {
    project    = "oci-oke-rdma-quickstart"
    managed-by = "resource-manager"
  }
}

variable "bastion_ssh_session_ttl_minutes" {
  description = "Managed SSH session lifetime in minutes (30-180)."
  type        = number
  default     = 120
  validation {
    condition     = var.bastion_ssh_session_ttl_minutes >= 30 && var.bastion_ssh_session_ttl_minutes <= 180 && floor(var.bastion_ssh_session_ttl_minutes) == var.bastion_ssh_session_ttl_minutes
    error_message = "SSH TTL must be a whole number of minutes from 30 through 180."
  }
}

variable "bastion_port_forwarding_session_ttl_minutes" {
  description = "UI and private Kubernetes API port-forwarding session lifetime in minutes (30-180)."
  type        = number
  default     = 120
  validation {
    condition     = var.bastion_port_forwarding_session_ttl_minutes >= 30 && var.bastion_port_forwarding_session_ttl_minutes <= 180 && floor(var.bastion_port_forwarding_session_ttl_minutes) == var.bastion_port_forwarding_session_ttl_minutes
    error_message = "Port-forwarding TTL must be a whole number of minutes from 30 through 180."
  }
}

variable "capacity_stack_ocid" {
  type        = string
  default     = ""
  description = "Optional owning Resource Manager stack OCID. Enables reviewed UI capacity management. Separate switches control creation of its IAM policies."
  validation {
    condition     = var.capacity_stack_ocid == "" || can(regex("^ocid1[.]ormstack[.][A-Za-z0-9._-]+$", var.capacity_stack_ocid))
    error_message = "capacity_stack_ocid must be empty or a Resource Manager stack OCID."
  }
}

variable "create_capacity_compartment_policy" {
  type        = bool
  default     = true
  description = "Create the compartment-level capacity policy when capacity_stack_ocid is set. Disable only when equivalent access is supplied externally for the operator instance. Disabling an already managed policy plans its deletion."
}

variable "create_capacity_tenancy_policy" {
  type        = bool
  default     = true
  description = "Create the tenancy-level capacity policy when capacity_stack_ocid is set. Disable only when equivalent access is supplied externally for the operator instance. Disabling an already managed policy plans its deletion."
}

variable "create_operator_policy" {
  type        = bool
  default     = true
  description = "Create the baseline operator policy for OKE, node inspection and payload download. Disable only when the resource/access is supplied externally. Disabling an already managed resource plans its deletion."
}

variable "create_nodepool_compute_cluster_policy" {
  type        = bool
  default     = true
  description = "Create the policy allowing the OKE node-pool principal to launch workers in the Compute Cluster. Disable only when the resource/access is supplied externally. Disabling an already managed resource plans its deletion."
}

variable "create_operator_dynamic_group" {
  type        = bool
  default     = true
  description = "Create a dynamic group matching the operator VM. When false, provide existing_operator_dynamic_group_ocid and ensure its matching rule includes the operator. Disable only when the resource/access is supplied externally. Disabling an already managed resource plans its deletion."
}

variable "existing_operator_dynamic_group_ocid" {
  type        = string
  default     = ""
  description = "Existing operator dynamic group OCID when creation is disabled. Used directly without an IAM lookup; verify membership and permissions separately."
  validation {
    condition     = var.existing_operator_dynamic_group_ocid == "" || can(regex("^ocid1[.]dynamicgroup[.][A-Za-z0-9._-]+$", var.existing_operator_dynamic_group_ocid))
    error_message = "Enter a dynamic-group OCID starting with ocid1.dynamicgroup."
  }
}
