data "oci_core_services" "oracle_services" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name_prefix}-vcn"
  dns_label      = "okerdma"
  freeform_tags  = local.tags
}

resource "oci_core_nat_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-nat"
  freeform_tags  = local.tags
}

resource "oci_core_service_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-service-gateway"
  freeform_tags  = local.tags

  services {
    service_id = data.oci_core_services.oracle_services.services[0].id
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-private-rt"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.this.id
  }

  route_rules {
    destination       = data.oci_core_services.oracle_services.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.this.id
  }
}

resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-private-baseline"
  freeform_tags  = local.tags

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    description = "Private subnet egress through the associated route table."
  }
}

locals {
  subnets = {
    api      = { cidr = var.api_subnet_cidr, dns = "api" }
    workers  = { cidr = var.worker_subnet_cidr, dns = "workers" }
    pods     = { cidr = var.pod_subnet_cidr, dns = "pods" }
    services = { cidr = var.service_lb_subnet_cidr, dns = "services" }
    operator = { cidr = var.operator_subnet_cidr, dns = "operator" }
    bastion  = { cidr = var.bastion_subnet_cidr, dns = "bastion" }
  }
}

resource "oci_core_subnet" "private" {
  for_each = local.subnets

  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = each.value.cidr
  display_name               = "${var.name_prefix}-${each.key}"
  dns_label                  = each.value.dns
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  freeform_tags              = local.tags
}

resource "oci_core_network_security_group" "oke_internal" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-oke-internal"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "oke_internal_ingress" {
  network_security_group_id = oci_core_network_security_group.oke_internal.id
  direction                 = "INGRESS"
  protocol                  = "all"
  source                    = oci_core_network_security_group.oke_internal.id
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "OKE control-plane, managed-node and VCN-native pod communication."
}

resource "oci_core_network_security_group_security_rule" "oke_internal_egress" {
  network_security_group_id = oci_core_network_security_group.oke_internal.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group" "api_operator" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-api-operator"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "api_from_operator" {
  network_security_group_id = oci_core_network_security_group.api_operator.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.operator_subnet_cidr
  source_type               = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = 6443
      max = 6443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "api_from_bastion" {
  network_security_group_id = oci_core_network_security_group.api_operator.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.operator.private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  description               = "One-time least-privilege Kubernetes RBAC bootstrap tunnel from the Bastion private endpoint."

  tcp_options {
    destination_port_range {
      min = 6443
      max = 6443
    }
  }
}

resource "oci_core_network_security_group" "operator" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-operator"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "operator_ssh_from_bastion" {
  network_security_group_id = oci_core_network_security_group.operator.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.operator.private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  description               = "OCI Bastion managed SSH to the private operator from the Bastion private endpoint only."

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "operator_ui_from_bastion" {
  network_security_group_id = oci_core_network_security_group.operator.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.operator.private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  description               = "OCI Bastion port-forwarding to the private control-center UI from the Bastion private endpoint only."

  tcp_options {
    destination_port_range {
      min = 8000
      max = 8000
    }
  }
}

resource "oci_core_network_security_group_security_rule" "operator_egress" {
  network_security_group_id = oci_core_network_security_group.operator.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "workers_ssh_from_operator" {
  count = var.enable_worker_ssh_from_operator ? 1 : 0

  network_security_group_id = oci_core_network_security_group.oke_internal.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.operator.id
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "Optional break-glass SSH from the private operator VM to RDMA workers."

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}
