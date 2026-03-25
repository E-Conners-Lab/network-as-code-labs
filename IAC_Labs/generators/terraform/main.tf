# Terraform configuration for FRR config generation (conceptual demo).
#
# This demonstrates how Terraform's declarative approach applies to
# network config generation. The local_file provider writes FRR configs
# using templatefile(), and Terraform's state tracking means it knows
# which files changed between runs.
#
# This is intentionally simplified. In a real Cisco NaC deployment,
# you would use the cisco-nac Terraform provider which manages device
# configuration as Terraform resources with full plan/apply/destroy
# lifecycle. Here we use local_file to show the pattern without
# requiring vendor-specific providers.
#
# Usage (if Terraform is installed):
#   cd generators/terraform
#   terraform init
#   terraform plan
#   terraform apply

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "2.5.1"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables -- these map to the YAML data model
# ---------------------------------------------------------------------------

variable "fabric_asn" {
  description = "Fabric-wide BGP ASN"
  type        = number
  default     = 65000
}

variable "ospf_area" {
  description = "OSPF area for the underlay"
  type        = string
  default     = "0.0.0.0"
}

variable "ospf_reference_bandwidth" {
  description = "OSPF reference bandwidth in Mbps"
  type        = number
  default     = 100000
}

variable "bgp_keepalive" {
  description = "BGP keepalive interval"
  type        = number
  default     = 30
}

variable "bgp_holdtime" {
  description = "BGP hold time"
  type        = number
  default     = 90
}

variable "description_prefix" {
  description = "Standard description prefix for managed interfaces"
  type        = string
  default     = "NaC-Managed"
}

variable "devices" {
  description = "Device inventory from the data model"
  type = map(object({
    role             = string
    loopback         = string
    loopback_ip      = string
    route_reflector  = bool
    cluster_id       = optional(string)
  }))
  default = {
    spine1 = {
      role            = "spine"
      loopback        = "10.0.0.1/32"
      loopback_ip     = "10.0.0.1"
      route_reflector = true
      cluster_id      = "10.0.0.1"
    }
    spine2 = {
      role            = "spine"
      loopback        = "10.0.0.2/32"
      loopback_ip     = "10.0.0.2"
      route_reflector = true
      cluster_id      = "10.0.0.2"
    }
    leaf1 = {
      role            = "leaf"
      loopback        = "10.0.0.11/32"
      loopback_ip     = "10.0.0.11"
      route_reflector = false
    }
    leaf2 = {
      role            = "leaf"
      loopback        = "10.0.0.12/32"
      loopback_ip     = "10.0.0.12"
      route_reflector = false
    }
    border1 = {
      role            = "border_leaf"
      loopback        = "10.0.0.21/32"
      loopback_ip     = "10.0.0.21"
      route_reflector = false
    }
    border2 = {
      role            = "border_leaf"
      loopback        = "10.0.0.22/32"
      loopback_ip     = "10.0.0.22"
      route_reflector = false
    }
  }
}

# ---------------------------------------------------------------------------
# Locals -- derived values
# ---------------------------------------------------------------------------

locals {
  route_reflectors = {
    for name, dev in var.devices : name => dev if dev.route_reflector
  }

  non_rr_devices = {
    for name, dev in var.devices : name => dev if !dev.route_reflector
  }
}

# ---------------------------------------------------------------------------
# Config generation via local_file
# ---------------------------------------------------------------------------
#
# In a real NaC Terraform deployment, these would be cisco_nac_* resources
# that manage device state directly. The local_file approach demonstrates
# two key Terraform concepts:
#
# 1. Declarative intent: you describe what the config should be, not the
#    steps to get there.
#
# 2. State tracking: Terraform knows the previous state. Running
#    `terraform plan` shows you exactly what changed since the last
#    apply. This is the "diff" that the Python path achieves by
#    comparing generated configs against a previous run.

resource "local_file" "frr_config" {
  for_each = var.devices

  filename = "${path.module}/../../configs-tf/${each.key}.conf"
  content  = templatefile("${path.module}/templates/frr.conf.tftpl", {
    hostname             = each.key
    role                 = each.value.role
    loopback             = each.value.loopback
    loopback_ip          = each.value.loopback_ip
    asn                  = var.fabric_asn
    ospf_area            = var.ospf_area
    ospf_ref_bw          = var.ospf_reference_bandwidth
    bgp_keepalive        = var.bgp_keepalive
    bgp_holdtime         = var.bgp_holdtime
    description_prefix   = var.description_prefix
    is_rr                = each.value.route_reflector
    cluster_id           = each.value.cluster_id
    # RR peers with everyone else; non-RR peers only with RRs
    neighbors = each.value.route_reflector ? {
      for name, dev in var.devices : name => dev if name != each.key
    } : local.route_reflectors
  })
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "generated_configs" {
  description = "Paths to generated configuration files"
  value       = { for name, file in local_file.frr_config : name => file.filename }
}
