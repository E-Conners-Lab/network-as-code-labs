"""Configuration rendering engine for FRR devices.

Reads the validated data model, builds a per-device template context that
contains only the data relevant to that device, and renders Jinja2
templates into complete FRR configuration files. The output is one file
per device in the specified output directory.

The key logic here is context building. A spine's BGP section looks
different from a leaf's because the spine is a route reflector. Rather
than putting that logic in the template, the render engine pre-computes
the neighbor list, the RR config, and the VRF assignments so the
templates stay clean and declarative.

Usage:
    uv run python -m generators.python.render
    uv run python -m generators.python.render --output-dir configs/
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from schemas.models import (
    Device,
    DeviceRole,
    FabricDataModel,
    InterfaceAssignment,
    NetworkSegment,
    RouteReflector,
    VRFConfig,
)
from validators import parse_all_files

TEMPLATE_DIR = Path(__file__).parent / "templates"
BASE_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class FabricLink:
    """A fabric link from this device's perspective."""

    interface: str
    ip: str
    peer_device: str
    description: str


@dataclass
class BGPNeighbor:
    """A BGP neighbor from this device's perspective."""

    ip: IPv4Address
    peer_device: str
    is_rr: bool


def _get_device_links(device_name: str, model: FabricDataModel) -> list[FabricLink]:
    """Extract all fabric links for a specific device."""
    links: list[FabricLink] = []
    for link in model.underlay.links:
        if link.a_device == device_name:
            links.append(FabricLink(
                interface=link.a_interface,
                ip=str(link.a_ip),
                peer_device=link.b_device,
                description=f"to {link.b_device} {link.b_interface}",
            ))
        elif link.b_device == device_name:
            links.append(FabricLink(
                interface=link.b_interface,
                ip=str(link.b_ip),
                peer_device=link.a_device,
                description=f"to {link.a_device} {link.a_interface}",
            ))
    return links


def _get_bgp_neighbors(device: Device, model: FabricDataModel) -> list[BGPNeighbor]:
    """Build the BGP neighbor list for a device.

    Route reflectors peer with every non-RR device (they are the hub).
    Non-RR devices peer only with route reflectors (they are clients).
    This is the auto-peering logic that the intent model enables: you
    declare which devices are route reflectors, and the neighbor lists
    are derived automatically.
    """
    rr_devices = {rr.device for rr in model.overlay.route_reflectors}
    rr_loopbacks = {
        d.name: d.loopback.ip
        for d in model.topology.devices
        if d.name in rr_devices
    }
    all_loopbacks = {d.name: d.loopback.ip for d in model.topology.devices}

    neighbors: list[BGPNeighbor] = []

    if device.name in rr_devices:
        # RR peers with every other device
        for other in model.topology.devices:
            if other.name == device.name:
                continue
            neighbors.append(BGPNeighbor(
                ip=other.loopback.ip,
                peer_device=other.name,
                is_rr=other.name in rr_devices,
            ))
    else:
        # Non-RR peers only with route reflectors
        for rr_name, rr_ip in sorted(rr_loopbacks.items()):
            neighbors.append(BGPNeighbor(
                ip=rr_ip,
                peer_device=rr_name,
                is_rr=True,
            ))

    return neighbors


def _get_device_rr(device: Device, model: FabricDataModel) -> RouteReflector | None:
    """Return the RouteReflector config if this device is an RR, else None."""
    for rr in model.overlay.route_reflectors:
        if rr.device == device.name:
            return rr
    return None


def _get_device_vrfs(device: Device, model: FabricDataModel) -> list[VRFConfig]:
    """Get VRFs relevant to this device.

    Spines do not host VRFs. Leafs and borders get all VRFs that have
    network segments with interfaces assigned to them, or all VRFs if
    the device is a border (borders typically carry all VRFs for
    external connectivity).
    """
    if device.role == DeviceRole.SPINE:
        return []

    if device.role == DeviceRole.BORDER_LEAF:
        return list(model.vrfs.vrfs)

    # Leaf: get VRFs that have networks with VLANs assigned to this device
    device_vlans = set()
    for iface in model.interfaces.interfaces:
        if iface.device == device.name:
            device_vlans.update(iface.vlans)

    vrf_names = set()
    for net in model.networks.networks:
        if net.vlan_id in device_vlans:
            vrf_names.add(net.vrf)

    return [v for v in model.vrfs.vrfs if v.name in vrf_names]


def _get_device_networks(device: Device, model: FabricDataModel) -> list[NetworkSegment]:
    """Get network segments relevant to this device."""
    if device.role == DeviceRole.SPINE:
        return []

    if device.role == DeviceRole.BORDER_LEAF:
        return list(model.networks.networks)

    device_vlans = set()
    for iface in model.interfaces.interfaces:
        if iface.device == device.name:
            device_vlans.update(iface.vlans)

    return [n for n in model.networks.networks if n.vlan_id in device_vlans]


def _get_device_interfaces(device: Device, model: FabricDataModel) -> list[InterfaceAssignment]:
    """Get host-facing interface assignments for this device."""
    return [i for i in model.interfaces.interfaces if i.device == device.name]


def build_device_context(device: Device, model: FabricDataModel) -> dict[str, Any]:
    """Build the complete template context for one device.

    This is where intent gets translated into device-specific data. The
    templates receive a flat, pre-computed context so they do not need
    conditional logic for role-based differences.
    """
    return {
        "device": device,
        "links": _get_device_links(device.name, model),
        "ospf": model.underlay.ospf,
        "bgp": model.overlay.bgp,
        "neighbors": _get_bgp_neighbors(device, model),
        "rr": _get_device_rr(device, model),
        "vrfs": _get_device_vrfs(device, model),
        "networks": _get_device_networks(device, model),
        "interfaces": _get_device_interfaces(device, model),
        "defaults": model.defaults,
    }


def render_configs(
    model: FabricDataModel,
    output_dir: Path,
) -> dict[str, str]:
    """Render FRR configurations for all devices in the fabric.

    Returns a dict mapping device name to the rendered config string.
    Also writes each config to output_dir/{device_name}.conf.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template("frr_base.j2")

    output_dir.mkdir(parents=True, exist_ok=True)
    configs: dict[str, str] = {}

    for device in model.topology.devices:
        context = build_device_context(device, model)
        rendered = template.render(**context)
        configs[device.name] = rendered

        output_path = output_dir / f"{device.name}.conf"
        output_path.write_text(rendered)

    return configs


def main() -> None:
    """Load the data model, validate it, and render all device configs."""
    parser = argparse.ArgumentParser(
        description="Generate FRR configs from the NaC data model"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="configs",
        help="Directory to write generated configs (default: configs/)",
    )
    args = parser.parse_args()

    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        for e in errors:
            print(f"  FAIL  {e.rule_id}  {e.message}", file=sys.stderr)
        print("\nData model has errors. Fix them before generating configs.", file=sys.stderr)
        sys.exit(1)

    model = FabricDataModel.model_validate(parsed)
    output_dir = BASE_DIR / args.output_dir

    configs = render_configs(model, output_dir)

    print(f"\nGenerated {len(configs)} device configurations in {output_dir}/\n")
    for name in sorted(configs):
        lines = configs[name].count("\n")
        print(f"  {name}.conf  ({lines} lines)")
    print()


if __name__ == "__main__":
    main()
