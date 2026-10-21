output "cluster_id" {
  value       = oci_containerengine_cluster.this.id
  description = "Enhanced OKE cluster OCID."
}

output "cluster_name" {
  value       = oci_containerengine_cluster.this.name
  description = "Enhanced OKE cluster name."
}

output "compute_cluster_id" {
  value       = oci_core_compute_cluster.rdma.id
  description = "OCI Compute Cluster used by the managed RDMA node pool."
}

output "node_pool_id" {
  value       = oci_containerengine_node_pool.rdma.id
  description = "Managed RDMA node pool OCID."
}

output "operator_instance_id" {
  value       = oci_core_instance.operator.id
  description = "Private operator VM OCID."
}

output "operator_private_ip" {
  value       = oci_core_instance.operator.private_ip
  description = "Private operator VM address."
}

output "operator_dynamic_group_id" {
  value       = local.operator_dynamic_group_id
  description = "Dynamic group OCID used by the operator instance principal."
}

output "bastion_id" {
  value       = oci_bastion_bastion.operator.id
  description = "OCI Bastion used to reach the private operator."
}

output "operator_ui_url_after_tunnel" {
  value       = "http://127.0.0.1:18000"
  description = "Open this URL after running the Bastion SSH tunnel with local port 18000 mapped to the operator target port 8000."
}

output "operator_ui_bastion_session_create_command" {
  value       = "oci bastion session create-port-forwarding --bastion-id ${oci_bastion_bastion.operator.id} --target-private-ip ${oci_core_instance.operator.private_ip} --target-port 8000 --ssh-public-key-file \"$${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}\" --session-ttl ${60 * var.bastion_port_forwarding_session_ttl_minutes} --wait-for-state SUCCEEDED"
  description = "Creates a Bastion port-forwarding session to the control-center UI. Copy the SSH tunnel command from the resulting session details, then open operator_ui_url_after_tunnel."
}

output "operator_ssh_session_create_command" {
  value       = "oci bastion session create-managed-ssh --bastion-id ${oci_bastion_bastion.operator.id} --target-resource-id ${oci_core_instance.operator.id} --target-os-username opc --target-port 22 --ssh-public-key-file \"$${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}\" --session-ttl ${60 * var.bastion_ssh_session_ttl_minutes} --wait-for-state SUCCEEDED"
  description = "Creates a time-limited managed SSH session for break-glass diagnostics."
}

output "operator_ui_token_retrieval_command" {
  value       = "sudo cat /etc/oke-rdma-control/admin.token"
  description = "Run this after connecting to the private operator VM to retrieve the single-operator workspace token."
}

output "operator_kubeconfig_command" {
  value       = "export KUBECONFIG=/etc/oke-rdma-control/kube/config"
  description = "Enables kubectl in an interactive operator VM shell. New shells also receive this through /etc/profile.d."
}

output "worker_ssh_guidance" {
  value       = var.enable_worker_ssh_from_operator ? "Keep the worker private key on your workstation, connect to the operator with SSH agent forwarding, then run ssh ubuntu@<worker-private-ip>." : "Worker SSH is disabled. Use the Control Center node diagnostics and Kubernetes APIs; enable break-glass SSH only if required."
  description = "How to reach managed RDMA workers when the optional break-glass SSH path is enabled."
}


output "operator_ui_access_steps" {
  value       = <<-EOT
    1. Set SSH_PUBLIC_KEY_FILE to a public key whose private key is on your workstation.
    2. Run operator_ui_bastion_session_create_command (Cloud Shell is fine for session creation).
    3. Copy the session SSH command to your workstation, set the local port to 18000, and keep it running.
    4. Open http://127.0.0.1:18000.
    5. Retrieve the workspace token over the managed-SSH operator session with: sudo cat /etc/oke-rdma-control/admin.token
  EOT
  description = "Private Control Center access sequence. The SSH tunnel must run on the workstation whose browser opens localhost."
}

output "operator_diagnostics_command" {
  value       = "oke-rdma-doctor"
  description = "Run on the private operator VM to inspect bootstrap status, services, listener, health and RDMA nodes."
}

output "operator_ui_token" {
  value       = random_password.operator_ui_token.result
  description = "Single-operator bootstrap token for the private control-center UI. Replace this model with enterprise OIDC before broad multi-user deployment."
  sensitive   = true
}

output "operator_rbac_manifest" {
  value = templatefile("${path.module}/operator/operator-rbac.yaml.tftpl", {
    operator_dynamic_group_ocid = local.operator_dynamic_group_id
  })
  description = "Apply once as cluster-admin only when operator_kubernetes_access_mode=least_privilege_rbac."
}

output "operator_rbac_bastion_session_create_command" {
  value       = "oci bastion session create-port-forwarding --bastion-id ${oci_bastion_bastion.operator.id} --target-private-ip ${oci_containerengine_cluster.this.endpoints[0].private_endpoint} --target-port 6443 --ssh-public-key-file \"$${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}\" --session-ttl ${60 * var.bastion_port_forwarding_session_ttl_minutes} --wait-for-state SUCCEEDED"
  description = "One-time private API tunnel for least-privilege Kubernetes RBAC bootstrap."
}

output "artifact_bucket" {
  value       = oci_objectstorage_bucket.artifacts.name
  description = "Private bucket containing the immutable operator payload."
}

output "kubernetes_minor" {
  value       = local.kubernetes_minor
  description = "Derived Kubernetes minor used for the Ubuntu OKE repository."
}

output "ubuntu_oke_node_package" {
  value       = local.oke_node_package
  description = "Oracle Ubuntu OKE node package derived from the selected Kubernetes minor using the explicit compatibility map."
}

output "ubuntu_oke_repository" {
  value       = local.ubuntu_repo_url
  description = "Derived Oracle Ubuntu OKE package repository used by managed-node bootstrap."
}

output "capacity_management_policy" {
  description = "Opt-in capacity IAM statements. Review the compute/network write scope before enabling."
  value = {
    enabled                    = var.capacity_stack_ocid != ""
    compartment_policy_managed = var.capacity_stack_ocid != "" && var.create_capacity_compartment_policy
    tenancy_policy_managed     = var.capacity_stack_ocid != "" && var.create_capacity_tenancy_policy
    stack_id                   = var.capacity_stack_ocid
    compartment_statements     = local.capacity_compartment_statements
    tenancy_statements         = local.capacity_tenancy_statements
  }
}

output "iam_configuration" {
  description = "IAM resource ownership and baseline statements for administrator review. External permissions and dynamic-group membership must be established before provisioning."
  value = {
    operator_dynamic_group_managed          = var.create_operator_dynamic_group
    operator_dynamic_group_id               = local.operator_dynamic_group_id
    operator_policy_managed                 = var.create_operator_policy
    nodepool_compute_cluster_policy_managed = var.create_nodepool_compute_cluster_policy
    capacity_compartment_policy_managed     = var.capacity_stack_ocid != "" && var.create_capacity_compartment_policy
    capacity_tenancy_policy_managed         = var.capacity_stack_ocid != "" && var.create_capacity_tenancy_policy
    operator_statements                     = local.operator_policy_statements
    nodepool_compute_cluster_statements     = local.nodepool_compute_cluster_statements
  }
}
