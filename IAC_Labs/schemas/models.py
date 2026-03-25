"""Pydantic v2 schema models for the Network as Code data model.

These models validate the YAML-based network intent definition, catching
structural errors, type mismatches, and constraint violations before any
configuration reaches the network. Each model maps to a specific YAML
file in the data directory. The FabricDataModel at the bottom composes
all individual models and runs cross-file validation that no single
file model can perform on its own.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from enum import Enum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BGP_ASN_MAX: int = 4_294_967_295


# ---------------------------------------------------------------------------
# Shared Utilities
# ---------------------------------------------------------------------------

_H = TypeVar("_H", bound=Hashable)


def _find_duplicates(values: Iterable[_H]) -> set[_H]:
    """Return the set of values that appear more than once."""
    seen: set[_H] = set()
    duplicates: set[_H] = set()
    for v in values:
        if v in seen:
            duplicates.add(v)
        seen.add(v)
    return duplicates


def _check_asn(value: int) -> int:
    """Validate that an ASN falls within the BGP range defined by RFC 6793."""
    if not 1 <= value <= _BGP_ASN_MAX:
        raise ValueError(
            f"ASN must be between 1 and {_BGP_ASN_MAX}, got {value}"
        )
    return value


def _validate_colon_pair(value: str, label: str) -> str:
    """Validate that a string is in ASN:ID format with integer parts."""
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{label} must be in ASN:ID format, got '{value}'")
    try:
        int(parts[0])
        int(parts[1])
    except ValueError as exc:
        raise ValueError(
            f"{label} parts must be integers, got '{value}'"
        ) from exc
    return value


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DeviceRole(str, Enum):
    """Valid roles a device can hold in the fabric."""

    SPINE = "spine"
    LEAF = "leaf"
    BORDER_LEAF = "border_leaf"


class UnderlayProtocol(str, Enum):
    """Supported underlay routing protocols."""

    OSPF = "ospf"
    ISIS = "isis"


class OverlayProtocol(str, Enum):
    """Supported overlay protocols."""

    BGP_EVPN = "bgp_evpn"


class LinkType(str, Enum):
    """Physical link types in the fabric."""

    POINT_TO_POINT = "point_to_point"


class InterfaceMode(str, Enum):
    """Switchport modes for host-facing interfaces."""

    ACCESS = "access"
    TRUNK = "trunk"
    ROUTED = "routed"


class AddressFamily(str, Enum):
    """BGP address families."""

    EVPN = "evpn"
    IPV4_UNICAST = "ipv4_unicast"


class FloodingMode(str, Enum):
    """VXLAN BUM traffic flooding modes."""

    HEAD_END_REPLICATION = "head_end_replication"
    MULTICAST = "multicast"


# ---------------------------------------------------------------------------
# Fabric Configuration (fabric.yaml)
# ---------------------------------------------------------------------------


class FabricConfig(BaseModel):
    """Fabric-wide settings including ASN, protocol selection, and IP ranges.

    The three IP ranges (loopback, point-to-point, management) must not
    overlap. The ASN must fall within the valid BGP range defined by
    RFC 6793.
    """

    name: str = Field(min_length=1, max_length=64, description="Fabric name identifier")
    asn: int = Field(description="Autonomous System Number for the fabric")
    underlay_protocol: UnderlayProtocol
    overlay_protocol: OverlayProtocol
    loopback_range: IPv4Network
    p2p_range: IPv4Network
    management_range: IPv4Network

    @field_validator("asn")
    @classmethod
    def validate_asn_range(cls, value: int) -> int:
        """Ensure ASN falls within the valid BGP range (RFC 6793)."""
        return _check_asn(value)

    @model_validator(mode="after")
    def validate_no_overlapping_ranges(self) -> FabricConfig:
        """Ensure loopback, P2P, and management ranges do not overlap."""
        ranges = [
            ("loopback_range", self.loopback_range),
            ("p2p_range", self.p2p_range),
            ("management_range", self.management_range),
        ]
        for i, (name_a, range_a) in enumerate(ranges):
            for name_b, range_b in ranges[i + 1 :]:
                if range_a.overlaps(range_b):
                    raise ValueError(
                        f"{name_a} ({range_a}) overlaps with {name_b} ({range_b})"
                    )
        return self


# ---------------------------------------------------------------------------
# Topology (topology.yaml)
# ---------------------------------------------------------------------------


class Device(BaseModel):
    """A single device in the fabric with its role and addressing.

    Device names must be lowercase alphanumeric with hyphens or underscores,
    starting with a letter. Only spines can be designated as route reflectors.
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    role: DeviceRole
    loopback: IPv4Interface
    management_ip: IPv4Interface
    asn: int
    route_reflector: bool = False

    @field_validator("asn")
    @classmethod
    def validate_asn_range(cls, value: int) -> int:
        """Ensure ASN falls within the valid BGP range."""
        return _check_asn(value)

    @model_validator(mode="after")
    def validate_route_reflector_is_spine(self) -> Device:
        """Only spines can be route reflectors."""
        if self.route_reflector and self.role != DeviceRole.SPINE:
            raise ValueError(
                f"Device '{self.name}' is marked as route_reflector but has role "
                f"'{self.role.value}'. Only spines can be route reflectors."
            )
        return self


class TopologyModel(BaseModel):
    """Complete device inventory for the fabric.

    Enforces uniqueness across device names, loopback addresses, and
    management IPs. A fabric with duplicate addressing would cause
    routing loops or unreachable devices.
    """

    devices: list[Device] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_device_names(self) -> TopologyModel:
        """Ensure no duplicate device names."""
        dupes = _find_duplicates(d.name for d in self.devices)
        if dupes:
            raise ValueError(f"Duplicate device names: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_loopbacks(self) -> TopologyModel:
        """Ensure no duplicate loopback addresses."""
        dupes = _find_duplicates(str(d.loopback) for d in self.devices)
        if dupes:
            raise ValueError(f"Duplicate loopback addresses: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_management_ips(self) -> TopologyModel:
        """Ensure no duplicate management IPs."""
        dupes = _find_duplicates(str(d.management_ip) for d in self.devices)
        if dupes:
            raise ValueError(f"Duplicate management IPs: {dupes}")
        return self


# ---------------------------------------------------------------------------
# Underlay -- OSPF and P2P Links (underlay.yaml)
# ---------------------------------------------------------------------------


class OSPFTimers(BaseModel):
    """OSPF timer configuration.

    The dead interval must be at least as large as the hello interval.
    Standard practice is dead = 4x hello, but the schema only enforces
    dead >= hello to allow tuning.
    """

    hello: int = Field(ge=1, le=65535, description="Hello interval in seconds")
    dead: int = Field(ge=1, le=65535, description="Dead interval in seconds")

    @model_validator(mode="after")
    def validate_dead_ge_hello(self) -> OSPFTimers:
        """Dead interval must be >= hello interval."""
        if self.dead < self.hello:
            raise ValueError(
                f"Dead interval ({self.dead}) must be >= hello interval ({self.hello})"
            )
        return self


class OSPFConfig(BaseModel):
    """OSPF routing protocol configuration for the underlay."""

    area: str = Field(description="OSPF area in dotted notation (e.g., 0.0.0.0)")
    reference_bandwidth: int = Field(
        ge=1, description="Reference bandwidth in Mbps for cost calculation"
    )
    authentication: str | None = Field(
        default=None, description="OSPF authentication type"
    )
    timers: OSPFTimers


class P2PLink(BaseModel):
    """A point-to-point link between two fabric devices.

    Both endpoints must share the same IP subnet and have distinct
    addresses. The link name is descriptive and used in validation
    error messages.
    """

    name: str = Field(min_length=1, max_length=128)
    a_device: str = Field(min_length=1)
    a_interface: str = Field(min_length=1)
    a_ip: IPv4Interface
    b_device: str = Field(min_length=1)
    b_interface: str = Field(min_length=1)
    b_ip: IPv4Interface
    link_type: LinkType = LinkType.POINT_TO_POINT

    @model_validator(mode="after")
    def validate_same_subnet(self) -> P2PLink:
        """Both endpoints of a P2P link must share the same subnet."""
        a_net = self.a_ip.network
        b_net = self.b_ip.network
        if a_net != b_net:
            raise ValueError(
                f"Link '{self.name}': endpoints are on different subnets "
                f"({a_net} vs {b_net})"
            )
        return self

    @model_validator(mode="after")
    def validate_different_ips(self) -> P2PLink:
        """Both endpoints must have different IP addresses."""
        if self.a_ip == self.b_ip:
            raise ValueError(
                f"Link '{self.name}': both endpoints have the same IP ({self.a_ip})"
            )
        return self


class UnderlayModel(BaseModel):
    """Complete underlay configuration including OSPF and physical links.

    Validates that no IP address appears on more than one link endpoint,
    which would cause an IP conflict on the fabric.
    """

    ospf: OSPFConfig
    links: list[P2PLink] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_no_duplicate_ips(self) -> UnderlayModel:
        """No IP address should appear on more than one link endpoint."""
        all_ips = (
            ip_str
            for link in self.links
            for ip_str in (str(link.a_ip), str(link.b_ip))
        )
        dupes = _find_duplicates(all_ips)
        if dupes:
            raise ValueError(f"Duplicate IPs across P2P links: {dupes}")
        return self


# ---------------------------------------------------------------------------
# Overlay -- BGP EVPN (overlay.yaml)
# ---------------------------------------------------------------------------


class BGPTimers(BaseModel):
    """BGP timer configuration.

    Per RFC 4271, the hold time must be at least 3x the keepalive
    interval. This prevents a peer from being declared dead due to
    normal keepalive jitter.
    """

    keepalive: int = Field(ge=1, le=65535, description="Keepalive interval in seconds")
    holdtime: int = Field(ge=3, le=65535, description="Hold time in seconds")

    @model_validator(mode="after")
    def validate_holdtime_ge_3x_keepalive(self) -> BGPTimers:
        """Hold time must be at least 3x the keepalive interval (RFC 4271)."""
        minimum = self.keepalive * 3
        if self.holdtime < minimum:
            raise ValueError(
                f"Hold time ({self.holdtime}) must be >= 3x keepalive "
                f"({self.keepalive}). Minimum hold time: {minimum}"
            )
        return self


class BGPConfig(BaseModel):
    """BGP global parameters."""

    address_families: list[AddressFamily] = Field(min_length=1)
    timers: BGPTimers


class RouteReflector(BaseModel):
    """A BGP route reflector and its cluster ID."""

    device: str = Field(min_length=1)
    cluster_id: IPv4Address


class OverlayModel(BaseModel):
    """Complete overlay configuration including BGP and route reflectors."""

    bgp: BGPConfig
    route_reflectors: list[RouteReflector] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Services -- VRFs (services/vrfs.yaml)
# ---------------------------------------------------------------------------


class VRFConfig(BaseModel):
    """A VRF definition with route distinguisher and route targets.

    VRF names must be uppercase alphanumeric with hyphens or underscores.
    The VNI, RD, and RT values must all be unique across VRFs to prevent
    routing table collisions.
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_-]*$")
    vni: int = Field(ge=1, le=16777215, description="L3 VNI for the VRF")
    rd: str = Field(description="Route distinguisher (format: ASN:ID)")
    rt_import: list[str] = Field(min_length=1, description="Import route targets")
    rt_export: list[str] = Field(min_length=1, description="Export route targets")
    description: str = Field(min_length=1)

    @field_validator("rd")
    @classmethod
    def validate_rd_format(cls, value: str) -> str:
        """Route distinguisher must be in ASN:ID format."""
        return _validate_colon_pair(value, "Route distinguisher")

    @field_validator("rt_import", "rt_export")
    @classmethod
    def validate_rt_format(cls, values: list[str]) -> list[str]:
        """Route targets must be in ASN:ID format."""
        for rt in values:
            _validate_colon_pair(rt, "Route target")
        return values


class VRFsModel(BaseModel):
    """Collection of all VRF definitions.

    Enforces uniqueness across VRF names, L3 VNIs, and route
    distinguishers. Duplicate values in any of these would cause
    ambiguous forwarding in the overlay.
    """

    vrfs: list[VRFConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_vrf_names(self) -> VRFsModel:
        """No two VRFs can share a name."""
        dupes = _find_duplicates(v.name for v in self.vrfs)
        if dupes:
            raise ValueError(f"Duplicate VRF names: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_vnis(self) -> VRFsModel:
        """No two VRFs can share an L3 VNI."""
        dupes = _find_duplicates(v.vni for v in self.vrfs)
        if dupes:
            raise ValueError(f"Duplicate VRF VNIs: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_rds(self) -> VRFsModel:
        """No two VRFs can share a route distinguisher."""
        dupes = _find_duplicates(v.rd for v in self.vrfs)
        if dupes:
            raise ValueError(f"Duplicate route distinguishers: {dupes}")
        return self


# ---------------------------------------------------------------------------
# Services -- Network Segments (services/networks.yaml)
# ---------------------------------------------------------------------------


class NetworkSegment(BaseModel):
    """An L2/L3 network segment (VLAN + VNI + subnet).

    The gateway address must fall within the segment's subnet. This is
    the anycast gateway IP that every leaf will advertise for this
    segment.
    """

    name: str = Field(min_length=1, max_length=64)
    vni: int = Field(ge=1, le=16777215, description="L2 VNI for the segment")
    vlan_id: int = Field(ge=1, le=4094, description="VLAN ID")
    subnet: IPv4Network
    gateway: IPv4Address
    vrf: str = Field(min_length=1, description="VRF this segment belongs to")
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_gateway_in_subnet(self) -> NetworkSegment:
        """Gateway address must be within the segment's subnet."""
        if self.gateway not in self.subnet:
            raise ValueError(
                f"Gateway {self.gateway} is not within subnet {self.subnet} "
                f"for network '{self.name}'"
            )
        return self


class NetworksModel(BaseModel):
    """Collection of all network segment definitions.

    Enforces uniqueness across VNIs, VLAN IDs, and subnets. Overlapping
    subnets within a VRF would cause ambiguous routing. Duplicate VNIs
    or VLAN IDs would cause frame delivery to the wrong segment.
    """

    networks: list[NetworkSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_vnis(self) -> NetworksModel:
        """No two segments can share an L2 VNI."""
        dupes = _find_duplicates(n.vni for n in self.networks)
        if dupes:
            raise ValueError(f"Duplicate network VNIs: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_vlan_ids(self) -> NetworksModel:
        """No two segments can share a VLAN ID."""
        dupes = _find_duplicates(n.vlan_id for n in self.networks)
        if dupes:
            raise ValueError(f"Duplicate VLAN IDs: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_no_overlapping_subnets(self) -> NetworksModel:
        """No two segments can have overlapping subnets."""
        for i, net_a in enumerate(self.networks):
            for net_b in self.networks[i + 1 :]:
                if net_a.subnet.overlaps(net_b.subnet):
                    raise ValueError(
                        f"Subnet overlap: '{net_a.name}' ({net_a.subnet}) "
                        f"overlaps with '{net_b.name}' ({net_b.subnet})"
                    )
        return self


# ---------------------------------------------------------------------------
# Services -- Interface Assignments (services/interfaces.yaml)
# ---------------------------------------------------------------------------


class InterfaceAssignment(BaseModel):
    """A host-facing interface configuration on a leaf or border device.

    Access mode interfaces carry exactly one VLAN untagged. Trunk mode
    interfaces carry one or more VLANs tagged. All referenced VLANs
    must be defined in networks.yaml (validated at the FabricDataModel
    level).
    """

    device: str = Field(min_length=1)
    interface: str = Field(min_length=1)
    mode: InterfaceMode
    vlans: list[int] = Field(min_length=1)
    description: str = Field(min_length=1)

    @field_validator("vlans")
    @classmethod
    def validate_vlan_range(cls, values: list[int]) -> list[int]:
        """All VLAN IDs must be within the valid 802.1Q range."""
        for vlan in values:
            if not 1 <= vlan <= 4094:
                raise ValueError(f"VLAN ID must be between 1 and 4094, got {vlan}")
        return values

    @model_validator(mode="after")
    def validate_access_single_vlan(self) -> InterfaceAssignment:
        """Access mode interfaces must have exactly one VLAN."""
        if self.mode == InterfaceMode.ACCESS and len(self.vlans) != 1:
            raise ValueError(
                f"Access mode interface {self.device}:{self.interface} must have "
                f"exactly one VLAN, got {len(self.vlans)}"
            )
        return self


class InterfacesModel(BaseModel):
    """Collection of all host-facing interface assignments.

    A device:interface pair can only be assigned once. Assigning the
    same port to two different configurations would create an ambiguous
    forwarding state.
    """

    interfaces: list[InterfaceAssignment]

    @model_validator(mode="after")
    def validate_unique_interface_assignments(self) -> InterfacesModel:
        """No device:interface pair should be assigned more than once."""
        dupes = _find_duplicates(
            (i.device, i.interface) for i in self.interfaces
        )
        if dupes:
            raise ValueError(f"Duplicate interface assignments: {dupes}")
        return self


# ---------------------------------------------------------------------------
# Defaults (defaults.yaml)
# ---------------------------------------------------------------------------


class DefaultTimers(BaseModel):
    """Default timer values used when not explicitly overridden."""

    ospf_hello: int = Field(ge=1, le=65535)
    ospf_dead: int = Field(ge=1, le=65535)
    bgp_keepalive: int = Field(ge=1, le=65535)
    bgp_holdtime: int = Field(ge=3, le=65535)


class FloodingConfig(BaseModel):
    """Default VXLAN BUM traffic flooding configuration."""

    mode: FloodingMode


class DefaultsModel(BaseModel):
    """Fabric-wide default values for parameters not explicitly set per device."""

    mtu: int = Field(ge=1280, le=9216, description="Default MTU for fabric interfaces")
    fabric_link_mtu: int = Field(ge=1280, le=9216, description="MTU for spine-leaf links")
    management_mtu: int = Field(ge=576, le=9216, description="MTU for management interfaces")
    description_prefix: str = Field(min_length=1)
    timers: DefaultTimers
    flooding: FloodingConfig
    arp_suppression: bool


# ---------------------------------------------------------------------------
# Cross-File Validation (composed from all YAML files)
# ---------------------------------------------------------------------------


class FabricDataModel(BaseModel):
    """The complete validated fabric data model, composed from all YAML files.

    This model performs cross-file validation that individual file models
    cannot handle alone. It verifies that devices referenced in links exist
    in the topology, that VRFs referenced by networks are defined, that IP
    addresses fall within their designated ranges, and that the overlay
    route reflector configuration is consistent with the topology.
    """

    fabric: FabricConfig
    topology: TopologyModel
    underlay: UnderlayModel
    overlay: OverlayModel
    defaults: DefaultsModel
    vrfs: VRFsModel
    networks: NetworksModel
    interfaces: InterfacesModel

    @model_validator(mode="after")
    def validate_link_devices_exist(self) -> FabricDataModel:
        """Every device referenced in a P2P link must exist in the topology."""
        device_names = {d.name for d in self.topology.devices}
        for link in self.underlay.links:
            for side, device in [("a", link.a_device), ("b", link.b_device)]:
                if device not in device_names:
                    raise ValueError(
                        f"Link '{link.name}' {side}-side references "
                        f"unknown device '{device}'"
                    )
        return self

    @model_validator(mode="after")
    def validate_route_reflectors_exist(self) -> FabricDataModel:
        """Route reflectors must reference devices that exist in the topology."""
        device_names = {d.name for d in self.topology.devices}
        for rr in self.overlay.route_reflectors:
            if rr.device not in device_names:
                raise ValueError(
                    f"Route reflector references unknown device '{rr.device}'"
                )
        return self

    @model_validator(mode="after")
    def validate_route_reflectors_are_spines(self) -> FabricDataModel:
        """Devices designated as route reflectors must be spines."""
        device_roles = {d.name: d.role for d in self.topology.devices}
        for rr in self.overlay.route_reflectors:
            role = device_roles.get(rr.device)
            if role != DeviceRole.SPINE:
                raise ValueError(
                    f"Route reflector '{rr.device}' has role '{role}', "
                    f"but only spines can be route reflectors"
                )
        return self

    @model_validator(mode="after")
    def validate_rr_topology_consistency(self) -> FabricDataModel:
        """Devices marked as RR in topology must match overlay route_reflectors."""
        topology_rrs = {d.name for d in self.topology.devices if d.route_reflector}
        overlay_rrs = {rr.device for rr in self.overlay.route_reflectors}

        missing_in_overlay = topology_rrs - overlay_rrs
        if missing_in_overlay:
            raise ValueError(
                f"Devices marked as route_reflector in topology but missing "
                f"from overlay route_reflectors: {missing_in_overlay}"
            )

        missing_in_topology = overlay_rrs - topology_rrs
        if missing_in_topology:
            raise ValueError(
                f"Devices in overlay route_reflectors but not marked as "
                f"route_reflector in topology: {missing_in_topology}"
            )
        return self

    @model_validator(mode="after")
    def validate_network_vrfs_exist(self) -> FabricDataModel:
        """Every VRF referenced by a network segment must be defined."""
        vrf_names = {v.name for v in self.vrfs.vrfs}
        for network in self.networks.networks:
            if network.vrf not in vrf_names:
                raise ValueError(
                    f"Network '{network.name}' references undefined "
                    f"VRF '{network.vrf}'"
                )
        return self

    @model_validator(mode="after")
    def validate_interface_devices_exist(self) -> FabricDataModel:
        """Every device in interface assignments must exist in the topology."""
        device_names = {d.name for d in self.topology.devices}
        for iface in self.interfaces.interfaces:
            if iface.device not in device_names:
                raise ValueError(
                    f"Interface assignment references unknown "
                    f"device '{iface.device}'"
                )
        return self

    @model_validator(mode="after")
    def validate_interface_vlans_exist(self) -> FabricDataModel:
        """Every VLAN in interface assignments must be defined in networks."""
        defined_vlans = {n.vlan_id for n in self.networks.networks}
        for iface in self.interfaces.interfaces:
            for vlan in iface.vlans:
                if vlan not in defined_vlans:
                    raise ValueError(
                        f"Interface {iface.device}:{iface.interface} references "
                        f"undefined VLAN {vlan}"
                    )
        return self

    @model_validator(mode="after")
    def validate_interfaces_on_leafs_or_borders(self) -> FabricDataModel:
        """Host-facing interfaces belong on leaf or border devices, not spines."""
        device_roles = {d.name: d.role for d in self.topology.devices}
        for iface in self.interfaces.interfaces:
            role = device_roles.get(iface.device)
            if role == DeviceRole.SPINE:
                raise ValueError(
                    f"Interface assignment on spine device '{iface.device}' "
                    f"({iface.interface}). Host-facing interfaces belong on "
                    f"leaf or border devices only."
                )
        return self

    @model_validator(mode="after")
    def validate_loopbacks_in_range(self) -> FabricDataModel:
        """Device loopback addresses must fall within the fabric loopback range."""
        for device in self.topology.devices:
            if device.loopback.ip not in self.fabric.loopback_range:
                raise ValueError(
                    f"Device '{device.name}' loopback {device.loopback} is not "
                    f"within fabric loopback range {self.fabric.loopback_range}"
                )
        return self

    @model_validator(mode="after")
    def validate_p2p_ips_in_range(self) -> FabricDataModel:
        """P2P link IPs must fall within the fabric P2P range."""
        for link in self.underlay.links:
            for ip in (link.a_ip, link.b_ip):
                if ip.ip not in self.fabric.p2p_range:
                    raise ValueError(
                        f"Link '{link.name}' IP {ip} is not within "
                        f"fabric P2P range {self.fabric.p2p_range}"
                    )
        return self

    @model_validator(mode="after")
    def validate_management_ips_in_range(self) -> FabricDataModel:
        """Management IPs must fall within the fabric management range."""
        for device in self.topology.devices:
            if device.management_ip.ip not in self.fabric.management_range:
                raise ValueError(
                    f"Device '{device.name}' management IP "
                    f"{device.management_ip} is not within fabric "
                    f"management range {self.fabric.management_range}"
                )
        return self

    @model_validator(mode="after")
    def validate_vni_no_overlap_with_vrfs(self) -> FabricDataModel:
        """Network segment L2 VNIs must not collide with VRF L3 VNIs."""
        vrf_vnis = {v.vni for v in self.vrfs.vrfs}
        for network in self.networks.networks:
            if network.vni in vrf_vnis:
                raise ValueError(
                    f"Network '{network.name}' VNI {network.vni} conflicts "
                    f"with a VRF L3 VNI. L2 and L3 VNIs must be unique."
                )
        return self

    @model_validator(mode="after")
    def validate_fabric_asn_matches_devices(self) -> FabricDataModel:
        """All device ASNs must match the fabric ASN (iBGP design)."""
        for device in self.topology.devices:
            if device.asn != self.fabric.asn:
                raise ValueError(
                    f"Device '{device.name}' ASN ({device.asn}) does not match "
                    f"fabric ASN ({self.fabric.asn}). All devices must use the "
                    f"same ASN in this iBGP design."
                )
        return self
