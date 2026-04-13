"""Brownfield import tool.

Connects to all devices in a running ContainerLab topology, pulls their
running configurations, parses them into structured data, and generates
a starting-point YAML data model. This is the "I have 200 switches
configured by different engineers over 10 years, how do I get this into
a data model" tool.

The output is a best-effort translation. FRR configs are parsed using
regex patterns to extract hostnames, interfaces, OSPF, and BGP config.
The resulting YAML will need manual cleanup and standardization, but it
gives you a starting point instead of building the data model from scratch.

Usage:
    uv run python -m drift.brownfield_import
    uv run python -m drift.brownfield_import --output-dir data-imported/
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


async def _get_running_config(container: str) -> str:
    """Pull running config from a device."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container,
        "vtysh",
        "-c",
        "show running-config",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode()


def _parse_hostname(config: str) -> str:
    """Extract hostname from running config."""
    match = re.search(r"^hostname\s+(\S+)", config, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _parse_interfaces(config: str) -> list[dict]:
    """Extract interface configurations."""
    interfaces = []
    current: dict | None = None

    for line in config.splitlines():
        stripped = line.strip()

        if stripped.startswith("interface ") and not stripped.startswith(
            "interface lo"
        ):
            name = stripped.split()[1]
            current = {"name": name, "ip": None, "description": None, "ospf_area": None}
        elif stripped == "exit" and current:
            if current["ip"]:
                interfaces.append(current)
            current = None
        elif current:
            if stripped.startswith("ip address"):
                current["ip"] = stripped.split("ip address ")[1]
            elif stripped.startswith("description"):
                current["description"] = stripped.split("description ")[1]
            elif stripped.startswith("ip ospf area"):
                current["ospf_area"] = stripped.split("ip ospf area ")[1]

    return interfaces


def _parse_loopback(config: str) -> str | None:
    """Extract loopback IP address."""
    in_lo = False
    for line in config.splitlines():
        stripped = line.strip()
        if stripped == "interface lo":
            in_lo = True
        elif stripped == "exit" and in_lo:
            in_lo = False
        elif in_lo and stripped.startswith("ip address"):
            return stripped.split("ip address ")[1]
    return None


def _parse_bgp(config: str) -> dict:
    """Extract BGP configuration."""
    bgp: dict = {"asn": None, "router_id": None, "cluster_id": None, "neighbors": []}

    match = re.search(r"router bgp (\d+)", config)
    if match:
        bgp["asn"] = int(match.group(1))

    match = re.search(r"bgp router-id (\S+)", config)
    if match:
        bgp["router_id"] = match.group(1)

    match = re.search(r"bgp cluster-id (\S+)", config)
    if match:
        bgp["cluster_id"] = match.group(1)

    for match in re.finditer(r"neighbor (\S+) remote-as (\d+)", config):
        bgp["neighbors"].append(
            {
                "ip": match.group(1),
                "asn": int(match.group(2)),
            }
        )

    return bgp


def _parse_ospf(config: str) -> dict:
    """Extract OSPF configuration."""
    ospf: dict = {"router_id": None, "reference_bandwidth": None}

    match = re.search(r"ospf router-id (\S+)", config)
    if match:
        ospf["router_id"] = match.group(1)

    match = re.search(r"auto-cost reference-bandwidth (\d+)", config)
    if match:
        ospf["reference_bandwidth"] = int(match.group(1))

    return ospf


async def import_all(output_dir: Path) -> list[dict]:
    """Import running configs from all ContainerLab devices."""
    # Discover containers
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "ps",
        "--format",
        "{{.Names}}",
        "--filter",
        f"name={CLAB_PREFIX}",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    containers = [c.strip() for c in stdout.decode().splitlines() if c.strip()]

    if not containers:
        console.print("[red]No ContainerLab containers found.[/red]")
        sys.exit(1)

    devices = []
    for container in sorted(containers):
        device_name = container.replace(f"{CLAB_PREFIX}-", "")
        config = await _get_running_config(container)

        hostname = _parse_hostname(config)
        loopback = _parse_loopback(config)
        interfaces = _parse_interfaces(config)
        bgp = _parse_bgp(config)
        ospf = _parse_ospf(config)

        # Determine role from BGP config
        role = "spine" if bgp.get("cluster_id") else "leaf"
        is_rr = bgp.get("cluster_id") is not None

        devices.append(
            {
                "name": hostname,
                "container": container,
                "role": role,
                "loopback": loopback,
                "is_rr": is_rr,
                "cluster_id": bgp.get("cluster_id"),
                "asn": bgp.get("asn"),
                "bgp_neighbors": bgp["neighbors"],
                "ospf": ospf,
                "interfaces": interfaces,
            }
        )

    # Generate YAML data model files
    output_dir.mkdir(parents=True, exist_ok=True)

    # topology.yaml
    topo_devices = []
    for d in devices:
        topo_devices.append(
            {
                "name": d["name"],
                "role": d["role"],
                "loopback": d["loopback"],
                "management_ip": "unknown",
                "asn": d["asn"],
                "route_reflector": d["is_rr"],
            }
        )

    topo_path = output_dir / "topology.yaml"
    with open(topo_path, "w") as f:
        yaml.safe_dump(
            {"devices": topo_devices}, f, default_flow_style=False, sort_keys=False
        )

    # fabric.yaml
    asns = set(d["asn"] for d in devices if d["asn"])
    fabric_data = {
        "fabric": {
            "name": "imported-fabric",
            "asn": asns.pop() if asns else 0,
            "underlay_protocol": "ospf",
            "overlay_protocol": "bgp_evpn",
        }
    }
    fabric_path = output_dir / "fabric.yaml"
    with open(fabric_path, "w") as f:
        yaml.safe_dump(fabric_data, f, default_flow_style=False, sort_keys=False)

    # underlay.yaml (links from interface data)
    links = []
    seen_pairs: set[tuple[str, str]] = set()
    for d in devices:
        for iface in d["interfaces"]:
            if iface.get("ip") and iface.get("ospf_area"):
                link_key = tuple(sorted([d["name"], iface["name"]]))
                if link_key not in seen_pairs:
                    seen_pairs.add(link_key)
                    links.append(
                        {
                            "device": d["name"],
                            "interface": iface["name"],
                            "ip": iface["ip"],
                            "ospf_area": iface["ospf_area"],
                        }
                    )

    underlay_path = output_dir / "interfaces.yaml"
    with open(underlay_path, "w") as f:
        yaml.safe_dump(
            {"interfaces": links}, f, default_flow_style=False, sort_keys=False
        )

    # overlay.yaml (route reflectors)
    rrs = [
        {"device": d["name"], "cluster_id": d["cluster_id"]}
        for d in devices
        if d["is_rr"]
    ]
    overlay_path = output_dir / "overlay.yaml"
    with open(overlay_path, "w") as f:
        yaml.safe_dump(
            {"route_reflectors": rrs}, f, default_flow_style=False, sort_keys=False
        )

    return devices


def main() -> None:
    """Run the brownfield import."""
    parser = argparse.ArgumentParser(
        description="Import running configs into data model format"
    )
    parser.add_argument("--output-dir", type=str, default="data-imported")
    args = parser.parse_args()

    output_dir = BASE_DIR / args.output_dir

    console.print()
    console.print(
        Panel("[bold]Network as Code -- Brownfield Import[/bold]", expand=False)
    )
    console.print()

    devices = asyncio.run(import_all(output_dir))

    table = Table(title="Imported Devices")
    table.add_column("Device", style="cyan")
    table.add_column("Role")
    table.add_column("Loopback")
    table.add_column("ASN", justify="right")
    table.add_column("RR", justify="center")
    table.add_column("BGP Peers", justify="right")
    table.add_column("Interfaces", justify="right")

    for d in devices:
        rr = "[green]Yes[/green]" if d["is_rr"] else "No"
        table.add_row(
            d["name"],
            d["role"],
            d["loopback"] or "none",
            str(d["asn"]),
            rr,
            str(len(d["bgp_neighbors"])),
            str(len(d["interfaces"])),
        )

    console.print(table)
    console.print()
    console.print(f"[green]Imported {len(devices)} devices to {output_dir}/[/green]")
    console.print()
    console.print("[yellow]Note: The generated YAML is a starting point.[/yellow]")
    console.print(
        "[yellow]Review and clean up the files before using them as your data model.[/yellow]"
    )
    console.print()


if __name__ == "__main__":
    main()
