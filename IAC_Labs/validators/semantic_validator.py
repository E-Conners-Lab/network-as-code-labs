"""Semantic validation layer -- cross-file logical consistency.

Format and syntax validation prove that each file is individually correct.
Semantic validation proves that the files are correct *together*. This is
where referential integrity checks live: does a network's VRF actually
exist? Does a link reference a real device? Are IP addresses within their
designated ranges?

The Pydantic FabricDataModel already enforces many of these via model
validators. This module adds higher-level fabric design rules that go
beyond simple referential integrity.

Rule IDs:
    SEM-01  Link devices exist in topology
    SEM-02  Route reflectors exist and are spines
    SEM-03  RR declarations consistent between topology and overlay
    SEM-04  Network VRFs exist
    SEM-05  Interface devices exist in topology
    SEM-06  Interface VLANs defined in networks
    SEM-07  Host-facing interfaces only on leaf/border devices
    SEM-08  Loopbacks within fabric loopback range
    SEM-09  P2P IPs within fabric P2P range
    SEM-10  Management IPs within fabric management range
    SEM-11  L2 VNIs do not collide with L3 VNIs
    SEM-12  Device ASNs match fabric ASN (iBGP)
    SEM-13  Full mesh: every spine connects to every leaf and border
    SEM-14  No spine-to-spine links
    SEM-15  Each leaf/border has at least two spine uplinks (redundancy)
    SEM-16  Every VRF has at least one network segment
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from schemas.models import (
    DeviceRole,
    FabricConfig,
    InterfacesModel,
    NetworksModel,
    OverlayModel,
    TopologyModel,
    UnderlayModel,
    VRFsModel,
)
from validators import ValidationLevel, ValidationResult

_LEVEL = ValidationLevel.SEMANTIC


def _pass(rule_id: str, message: str) -> ValidationResult:
    return ValidationResult(level=_LEVEL, rule_id=rule_id, message=message, passed=True)


def _fail(rule_id: str, message: str, details: list[str] | None = None) -> ValidationResult:
    return ValidationResult(
        level=_LEVEL, rule_id=rule_id, message=message, passed=False, details=details or []
    )


def validate_semantic(parsed: dict[str, Any]) -> list[ValidationResult]:
    """Run all semantic checks against the parsed and schema-validated models.

    Expects a dict keyed by component name with validated Pydantic model
    instances as values. Missing components are skipped since earlier
    layers would have reported the failure.
    """
    required_keys = {"fabric", "topology", "underlay", "overlay", "defaults", "vrfs", "networks", "interfaces"}
    if not required_keys.issubset(parsed.keys()):
        missing = required_keys - parsed.keys()
        return [_fail("SEM-00", f"Cannot run semantic checks, missing components: {missing}")]

    fabric: FabricConfig = parsed["fabric"]
    topology: TopologyModel = parsed["topology"]
    underlay: UnderlayModel = parsed["underlay"]
    overlay: OverlayModel = parsed["overlay"]
    vrfs: VRFsModel = parsed["vrfs"]
    networks: NetworksModel = parsed["networks"]
    interfaces: InterfacesModel = parsed["interfaces"]

    results: list[ValidationResult] = []

    results.extend(_check_link_devices(topology, underlay))
    results.extend(_check_route_reflectors(topology, overlay))
    results.extend(_check_rr_consistency(topology, overlay))
    results.extend(_check_network_vrfs(vrfs, networks))
    results.extend(_check_interface_devices(topology, interfaces))
    results.extend(_check_interface_vlans(networks, interfaces))
    results.extend(_check_interfaces_not_on_spines(topology, interfaces))
    results.extend(_check_loopbacks_in_range(fabric, topology))
    results.extend(_check_p2p_ips_in_range(fabric, underlay))
    results.extend(_check_management_ips_in_range(fabric, topology))
    results.extend(_check_vni_no_overlap(vrfs, networks))
    results.extend(_check_asn_consistency(fabric, topology))
    results.extend(_check_full_mesh(topology, underlay))
    results.extend(_check_no_spine_to_spine(topology, underlay))
    results.extend(_check_uplink_redundancy(topology, underlay))
    results.extend(_check_vrf_has_networks(vrfs, networks))

    return results


# ---------------------------------------------------------------------------
# SEM-01: Link devices exist in topology
# ---------------------------------------------------------------------------

def _check_link_devices(topology: TopologyModel, underlay: UnderlayModel) -> list[ValidationResult]:
    device_names = {d.name for d in topology.devices}
    missing: list[str] = []
    for link in underlay.links:
        if link.a_device not in device_names:
            missing.append(f"Link '{link.name}' a-side: '{link.a_device}'")
        if link.b_device not in device_names:
            missing.append(f"Link '{link.name}' b-side: '{link.b_device}'")
    if missing:
        return [_fail("SEM-01", "Links reference devices not in topology", missing)]
    return [_pass("SEM-01", "All link devices exist in topology")]


# ---------------------------------------------------------------------------
# SEM-02: Route reflectors exist and are spines
# ---------------------------------------------------------------------------

def _check_route_reflectors(topology: TopologyModel, overlay: OverlayModel) -> list[ValidationResult]:
    device_map = {d.name: d for d in topology.devices}
    issues: list[str] = []
    for rr in overlay.route_reflectors:
        device = device_map.get(rr.device)
        if device is None:
            issues.append(f"RR '{rr.device}' not found in topology")
        elif device.role != DeviceRole.SPINE:
            issues.append(f"RR '{rr.device}' has role '{device.role.value}', expected spine")
    if issues:
        return [_fail("SEM-02", "Route reflector configuration errors", issues)]
    return [_pass("SEM-02", "All route reflectors are valid spine devices")]


# ---------------------------------------------------------------------------
# SEM-03: RR declarations consistent between topology and overlay
# ---------------------------------------------------------------------------

def _check_rr_consistency(topology: TopologyModel, overlay: OverlayModel) -> list[ValidationResult]:
    topo_rrs = {d.name for d in topology.devices if d.route_reflector}
    overlay_rrs = {rr.device for rr in overlay.route_reflectors}

    issues: list[str] = []
    for name in topo_rrs - overlay_rrs:
        issues.append(f"'{name}' marked as RR in topology but missing from overlay")
    for name in overlay_rrs - topo_rrs:
        issues.append(f"'{name}' listed as RR in overlay but not flagged in topology")

    if issues:
        return [_fail("SEM-03", "Route reflector declarations inconsistent", issues)]
    return [_pass("SEM-03", "Route reflector declarations consistent across files")]


# ---------------------------------------------------------------------------
# SEM-04: Network VRFs exist
# ---------------------------------------------------------------------------

def _check_network_vrfs(vrfs: VRFsModel, networks: NetworksModel) -> list[ValidationResult]:
    vrf_names = {v.name for v in vrfs.vrfs}
    missing = [
        f"Network '{n.name}' references VRF '{n.vrf}'"
        for n in networks.networks
        if n.vrf not in vrf_names
    ]
    if missing:
        return [_fail("SEM-04", "Networks reference undefined VRFs", missing)]
    return [_pass("SEM-04", "All network VRF references are valid")]


# ---------------------------------------------------------------------------
# SEM-05: Interface devices exist in topology
# ---------------------------------------------------------------------------

def _check_interface_devices(topology: TopologyModel, interfaces: InterfacesModel) -> list[ValidationResult]:
    device_names = {d.name for d in topology.devices}
    missing = [
        f"{i.device}:{i.interface}"
        for i in interfaces.interfaces
        if i.device not in device_names
    ]
    if missing:
        return [_fail("SEM-05", "Interface assignments reference unknown devices", missing)]
    return [_pass("SEM-05", "All interface device references are valid")]


# ---------------------------------------------------------------------------
# SEM-06: Interface VLANs defined in networks
# ---------------------------------------------------------------------------

def _check_interface_vlans(networks: NetworksModel, interfaces: InterfacesModel) -> list[ValidationResult]:
    defined_vlans = {n.vlan_id for n in networks.networks}
    issues: list[str] = []
    for iface in interfaces.interfaces:
        for vlan in iface.vlans:
            if vlan not in defined_vlans:
                issues.append(f"{iface.device}:{iface.interface} VLAN {vlan}")
    if issues:
        return [_fail("SEM-06", "Interface assignments reference undefined VLANs", issues)]
    return [_pass("SEM-06", "All interface VLAN references are valid")]


# ---------------------------------------------------------------------------
# SEM-07: Host-facing interfaces only on leaf/border devices
# ---------------------------------------------------------------------------

def _check_interfaces_not_on_spines(topology: TopologyModel, interfaces: InterfacesModel) -> list[ValidationResult]:
    device_roles = {d.name: d.role for d in topology.devices}
    spine_ifaces = [
        f"{i.device}:{i.interface}"
        for i in interfaces.interfaces
        if device_roles.get(i.device) == DeviceRole.SPINE
    ]
    if spine_ifaces:
        return [_fail("SEM-07", "Host-facing interfaces assigned to spine devices", spine_ifaces)]
    return [_pass("SEM-07", "No host-facing interfaces on spine devices")]


# ---------------------------------------------------------------------------
# SEM-08: Loopbacks within fabric loopback range
# ---------------------------------------------------------------------------

def _check_loopbacks_in_range(fabric: FabricConfig, topology: TopologyModel) -> list[ValidationResult]:
    out_of_range = [
        f"{d.name}: {d.loopback}"
        for d in topology.devices
        if d.loopback.ip not in fabric.loopback_range
    ]
    if out_of_range:
        return [_fail("SEM-08", f"Loopbacks outside fabric range {fabric.loopback_range}", out_of_range)]
    return [_pass("SEM-08", "All loopbacks within fabric range")]


# ---------------------------------------------------------------------------
# SEM-09: P2P IPs within fabric P2P range
# ---------------------------------------------------------------------------

def _check_p2p_ips_in_range(fabric: FabricConfig, underlay: UnderlayModel) -> list[ValidationResult]:
    out_of_range: list[str] = []
    for link in underlay.links:
        for ip in (link.a_ip, link.b_ip):
            if ip.ip not in fabric.p2p_range:
                out_of_range.append(f"Link '{link.name}': {ip}")
    if out_of_range:
        return [_fail("SEM-09", f"P2P IPs outside fabric range {fabric.p2p_range}", out_of_range)]
    return [_pass("SEM-09", "All P2P IPs within fabric range")]


# ---------------------------------------------------------------------------
# SEM-10: Management IPs within fabric management range
# ---------------------------------------------------------------------------

def _check_management_ips_in_range(fabric: FabricConfig, topology: TopologyModel) -> list[ValidationResult]:
    out_of_range = [
        f"{d.name}: {d.management_ip}"
        for d in topology.devices
        if d.management_ip.ip not in fabric.management_range
    ]
    if out_of_range:
        return [_fail("SEM-10", f"Management IPs outside range {fabric.management_range}", out_of_range)]
    return [_pass("SEM-10", "All management IPs within fabric range")]


# ---------------------------------------------------------------------------
# SEM-11: L2 VNIs do not collide with L3 VNIs
# ---------------------------------------------------------------------------

def _check_vni_no_overlap(vrfs: VRFsModel, networks: NetworksModel) -> list[ValidationResult]:
    vrf_vnis = {v.vni for v in vrfs.vrfs}
    collisions = [
        f"Network '{n.name}' VNI {n.vni} conflicts with a VRF L3 VNI"
        for n in networks.networks
        if n.vni in vrf_vnis
    ]
    if collisions:
        return [_fail("SEM-11", "L2/L3 VNI collision detected", collisions)]
    return [_pass("SEM-11", "No L2/L3 VNI collisions")]


# ---------------------------------------------------------------------------
# SEM-12: Device ASNs match fabric ASN (iBGP design)
# ---------------------------------------------------------------------------

def _check_asn_consistency(fabric: FabricConfig, topology: TopologyModel) -> list[ValidationResult]:
    mismatches = [
        f"{d.name}: ASN {d.asn} (fabric: {fabric.asn})"
        for d in topology.devices
        if d.asn != fabric.asn
    ]
    if mismatches:
        return [_fail("SEM-12", "Device ASNs do not match fabric ASN", mismatches)]
    return [_pass("SEM-12", "All device ASNs match fabric ASN")]


# ---------------------------------------------------------------------------
# SEM-13: Full mesh -- every spine connects to every leaf and border
# ---------------------------------------------------------------------------

def _check_full_mesh(topology: TopologyModel, underlay: UnderlayModel) -> list[ValidationResult]:
    spines = {d.name for d in topology.devices if d.role == DeviceRole.SPINE}
    non_spines = {d.name for d in topology.devices if d.role != DeviceRole.SPINE}

    # Build adjacency from links
    adjacency: dict[str, set[str]] = defaultdict(set)
    for link in underlay.links:
        adjacency[link.a_device].add(link.b_device)
        adjacency[link.b_device].add(link.a_device)

    missing: list[str] = []
    for spine in sorted(spines):
        for non_spine in sorted(non_spines):
            if non_spine not in adjacency.get(spine, set()):
                missing.append(f"{spine} <-> {non_spine}")

    if missing:
        return [_fail("SEM-13", "Incomplete spine-leaf mesh, missing links", missing)]
    return [_pass("SEM-13", "Full spine-to-leaf/border mesh verified")]


# ---------------------------------------------------------------------------
# SEM-14: No spine-to-spine links
# ---------------------------------------------------------------------------

def _check_no_spine_to_spine(topology: TopologyModel, underlay: UnderlayModel) -> list[ValidationResult]:
    spines = {d.name for d in topology.devices if d.role == DeviceRole.SPINE}
    spine_links = [
        link.name
        for link in underlay.links
        if link.a_device in spines and link.b_device in spines
    ]
    if spine_links:
        return [_fail("SEM-14", "Spine-to-spine links are not allowed in spine-leaf design", spine_links)]
    return [_pass("SEM-14", "No spine-to-spine links")]


# ---------------------------------------------------------------------------
# SEM-15: Each leaf/border has at least two spine uplinks (redundancy)
# ---------------------------------------------------------------------------

def _check_uplink_redundancy(topology: TopologyModel, underlay: UnderlayModel) -> list[ValidationResult]:
    spines = {d.name for d in topology.devices if d.role == DeviceRole.SPINE}
    non_spines = {d.name for d in topology.devices if d.role != DeviceRole.SPINE}

    uplink_count: dict[str, int] = {name: 0 for name in non_spines}
    for link in underlay.links:
        if link.a_device in spines and link.b_device in non_spines:
            uplink_count[link.b_device] += 1
        elif link.b_device in spines and link.a_device in non_spines:
            uplink_count[link.a_device] += 1

    under_connected = [
        f"{name}: {count} uplink(s)"
        for name, count in sorted(uplink_count.items())
        if count < 2
    ]
    if under_connected:
        return [_fail("SEM-15", "Leaf/border devices need at least 2 spine uplinks", under_connected)]
    return [_pass("SEM-15", "All leaf/border devices have redundant spine uplinks")]


# ---------------------------------------------------------------------------
# SEM-16: Every VRF has at least one network segment
# ---------------------------------------------------------------------------

def _check_vrf_has_networks(vrfs: VRFsModel, networks: NetworksModel) -> list[ValidationResult]:
    vrfs_with_networks = {n.vrf for n in networks.networks}
    empty_vrfs = [
        v.name
        for v in vrfs.vrfs
        if v.name not in vrfs_with_networks
    ]
    if empty_vrfs:
        return [_fail("SEM-16", "VRFs with no network segments", empty_vrfs)]
    return [_pass("SEM-16", "All VRFs have at least one network segment")]
