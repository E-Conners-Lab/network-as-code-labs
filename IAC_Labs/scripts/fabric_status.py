"""Fabric status dashboard -- structured view of the entire fabric state.

Connects to all ContainerLab devices, collects OSPF, BGP, and interface
state, parses the output into structured data, and displays it as clean
tables. This is the "show fabric status" command that doesn't exist
natively on any single device.

Usage:
    uv run python -m scripts.fabric_status
    uv run python -m scripts.fabric_status --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class OSPFNeighbor:
    """A single OSPF adjacency."""

    neighbor_id: str
    state: str
    interface: str
    address: str
    uptime: str


@dataclass
class BGPPeer:
    """A single BGP peering session."""

    neighbor: str
    name: str
    asn: int
    state: str
    prefixes_received: int
    uptime: str


@dataclass
class InterfaceStatus:
    """A single interface with IP and link state."""

    name: str
    status: str
    ip_address: str


@dataclass
class DeviceStatus:
    """Complete operational state for one device."""

    name: str
    role: str
    reachable: bool
    ospf_neighbors: list[OSPFNeighbor] = field(default_factory=list)
    bgp_peers: list[BGPPeer] = field(default_factory=list)
    interfaces: list[InterfaceStatus] = field(default_factory=list)
    route_count: int = 0


# ---------------------------------------------------------------------------
# Device interaction
# ---------------------------------------------------------------------------


async def _exec(container: str, command: str) -> str:
    """Run a command in a container and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


def _parse_ospf_neighbors(output: str) -> list[OSPFNeighbor]:
    """Parse 'show ip ospf neighbor' output into structured data."""
    neighbors: list[OSPFNeighbor] = []
    for line in output.splitlines():
        # Match lines like: 10.0.0.11  1 Full/-  24.126s  35.871s 10.0.1.1  eth1:10.0.1.0
        parts = line.split()
        if len(parts) >= 7 and re.match(r"\d+\.\d+\.\d+\.\d+", parts[0]):
            neighbors.append(
                OSPFNeighbor(
                    neighbor_id=parts[0],
                    state=parts[2].split("/")[0],
                    interface=parts[6].split(":")[0] if ":" in parts[6] else parts[6],
                    address=parts[5],
                    uptime=parts[3],
                )
            )
    return neighbors


def _parse_bgp_summary(output: str) -> list[BGPPeer]:
    """Parse 'show bgp summary' output into structured data."""
    peers: list[BGPPeer] = []
    seen_ips: set[str] = set()

    for line in output.splitlines():
        # Match lines like: spine2(10.0.0.2)  4  65000  8  6  0  0  0  00:00:35  0  0 spine2
        # Or: 10.0.0.2  4  65000  8  6  0  0  0  00:00:35  0  0 spine2
        match = re.match(
            r"\s*(?:(\w+)\()?(\d+\.\d+\.\d+\.\d+)\)?\s+"
            r"4\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+"
            r"(\S+)\s+"
            r"(\S+)\s+\d+\s*(.*)",
            line,
        )
        if match:
            ip = match.group(2)
            if ip in seen_ips:
                continue
            seen_ips.add(ip)

            name = match.group(1) or match.group(6).strip() or ip
            state_or_pfx = match.group(5)
            uptime = match.group(4)

            try:
                pfx = int(state_or_pfx)
                state = "Established"
            except ValueError:
                pfx = 0
                state = state_or_pfx

            peers.append(
                BGPPeer(
                    neighbor=ip,
                    name=name,
                    asn=int(match.group(3)),
                    state=state,
                    prefixes_received=pfx,
                    uptime=uptime,
                )
            )
    return peers


def _parse_interfaces(output: str) -> list[InterfaceStatus]:
    """Parse 'ip -br addr' output into structured data."""
    interfaces: list[InterfaceStatus] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0].split("@")[0]
            status = parts[1]
            ips = [p for p in parts[2:] if "/" in p and ":" not in p]
            ip = ips[0] if ips else ""
            interfaces.append(
                InterfaceStatus(
                    name=name,
                    status=status,
                    ip_address=ip,
                )
            )
    return interfaces


def _count_routes(output: str) -> int:
    """Count the number of active routes in 'show ip route' output."""
    return len(
        [
            line
            for line in output.splitlines()
            if line.strip().startswith(("O>*", "C>*", "K>*", "B>*", "S>*"))
        ]
    )


async def _collect_device_status(device_name: str, role: str) -> DeviceStatus:
    """Collect all operational state from a single device."""
    container = f"{CLAB_PREFIX}-{device_name}"

    # Check reachability
    try:
        result = await _exec(container, "echo ok")
        if "ok" not in result:
            return DeviceStatus(name=device_name, role=role, reachable=False)
    except Exception:
        return DeviceStatus(name=device_name, role=role, reachable=False)

    # Collect all data concurrently
    ospf_out, bgp_out, iface_out, route_out = await asyncio.gather(
        _exec(container, "vtysh -c 'show ip ospf neighbor' 2>/dev/null"),
        _exec(container, "vtysh -c 'show bgp summary' 2>/dev/null"),
        _exec(container, "ip -br addr"),
        _exec(container, "vtysh -c 'show ip route' 2>/dev/null"),
    )

    return DeviceStatus(
        name=device_name,
        role=role,
        reachable=True,
        ospf_neighbors=_parse_ospf_neighbors(ospf_out),
        bgp_peers=_parse_bgp_summary(bgp_out),
        interfaces=_parse_interfaces(iface_out),
        route_count=_count_routes(route_out),
    )


async def collect_fabric_status() -> list[DeviceStatus]:
    """Collect status from all devices in the fabric."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors. Run validate.py first.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    tasks = [_collect_device_status(d.name, d.role.value) for d in topology.devices]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _print_overview(statuses: list[DeviceStatus]) -> None:
    """Print the fabric overview table."""
    table = Table(title="Fabric Overview")
    table.add_column("Device", style="cyan")
    table.add_column("Role")
    table.add_column("Status", justify="center")
    table.add_column("OSPF Nbrs", justify="right")
    table.add_column("BGP Peers", justify="right")
    table.add_column("Routes", justify="right")

    for s in sorted(statuses, key=lambda x: x.name):
        status = "[green]UP[/green]" if s.reachable else "[red]DOWN[/red]"
        ospf_up = sum(1 for n in s.ospf_neighbors if n.state == "Full")
        bgp_up = sum(1 for p in s.bgp_peers if p.state == "Established")
        ospf_str = f"[green]{ospf_up}[/green]" if ospf_up > 0 else "[red]0[/red]"
        bgp_str = f"[green]{bgp_up}[/green]" if bgp_up > 0 else "[red]0[/red]"

        table.add_row(
            s.name,
            s.role.replace("_", " "),
            status,
            ospf_str,
            bgp_str,
            str(s.route_count),
        )

    console.print(table)


def _print_ospf_detail(statuses: list[DeviceStatus]) -> None:
    """Print OSPF adjacency details."""
    table = Table(title="OSPF Adjacencies")
    table.add_column("Device", style="cyan")
    table.add_column("Neighbor ID")
    table.add_column("State", justify="center")
    table.add_column("Interface")
    table.add_column("Peer IP")
    table.add_column("Uptime")

    for s in sorted(statuses, key=lambda x: x.name):
        for n in s.ospf_neighbors:
            state_style = "green" if n.state == "Full" else "red"
            table.add_row(
                s.name,
                n.neighbor_id,
                f"[{state_style}]{n.state}[/{state_style}]",
                n.interface,
                n.address,
                n.uptime,
            )

    console.print(table)


def _print_bgp_detail(statuses: list[DeviceStatus]) -> None:
    """Print BGP peering details."""
    table = Table(title="BGP Peering Sessions")
    table.add_column("Device", style="cyan")
    table.add_column("Peer")
    table.add_column("Peer Name")
    table.add_column("ASN", justify="right")
    table.add_column("State", justify="center")
    table.add_column("Prefixes", justify="right")
    table.add_column("Uptime")

    for s in sorted(statuses, key=lambda x: x.name):
        for p in s.bgp_peers:
            state_style = "green" if p.state == "Established" else "red"
            table.add_row(
                s.name,
                p.neighbor,
                p.name,
                str(p.asn),
                f"[{state_style}]{p.state}[/{state_style}]",
                str(p.prefixes_received),
                p.uptime,
            )

    console.print(table)


def _print_interfaces(statuses: list[DeviceStatus]) -> None:
    """Print interface status for all devices."""
    table = Table(title="Interface Status")
    table.add_column("Device", style="cyan")
    table.add_column("Interface")
    table.add_column("Status", justify="center")
    table.add_column("IP Address")

    for s in sorted(statuses, key=lambda x: x.name):
        for i in s.interfaces:
            if i.name in ("lo", "eth0"):
                continue
            status_style = "green" if i.status in ("UP", "UNKNOWN") else "red"
            table.add_row(
                s.name,
                i.name,
                f"[{status_style}]{i.status}[/{status_style}]",
                i.ip_address,
            )

    console.print(table)


def main() -> None:
    """Collect and display fabric status."""
    parser = argparse.ArgumentParser(description="Fabric status dashboard")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--detail", action="store_true", help="Show per-neighbor/peer details"
    )
    args = parser.parse_args()

    statuses = asyncio.run(collect_fabric_status())

    if args.json:
        data = [asdict(s) for s in sorted(statuses, key=lambda x: x.name)]
        print(json.dumps(data, indent=2))
        return

    console.print()
    console.print(Panel("[bold]Network as Code -- Fabric Status[/bold]", expand=False))
    console.print()

    _print_overview(statuses)

    if args.detail:
        console.print()
        _print_ospf_detail(statuses)
        console.print()
        _print_bgp_detail(statuses)
        console.print()
        _print_interfaces(statuses)

    # Summary
    total = len(statuses)
    up = sum(1 for s in statuses if s.reachable)
    all_ospf = sum(len(s.ospf_neighbors) for s in statuses)
    full_ospf = sum(1 for s in statuses for n in s.ospf_neighbors if n.state == "Full")
    all_bgp = sum(len(s.bgp_peers) for s in statuses)
    est_bgp = sum(1 for s in statuses for p in s.bgp_peers if p.state == "Established")

    console.print()
    health = (
        "[bold green]HEALTHY[/bold green]"
        if (up == total and full_ospf == all_ospf and est_bgp == all_bgp)
        else "[bold red]DEGRADED[/bold red]"
    )
    console.print(
        f"Fabric health: {health}  |  Devices: {up}/{total}  |  OSPF: {full_ospf}/{all_ospf} Full  |  BGP: {est_bgp}/{all_bgp} Established"
    )
    console.print()


if __name__ == "__main__":
    main()
