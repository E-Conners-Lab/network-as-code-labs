"""Structured routing table viewer for all fabric devices.

Parses FRR routing tables into structured data and displays them as
filtered, sortable tables. Supports filtering by protocol (OSPF, BGP,
connected) and by device.

Usage:
    uv run python -m scripts.route_table
    uv run python -m scripts.route_table --device spine1
    uv run python -m scripts.route_table --protocol ospf
    uv run python -m scripts.route_table --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"

PROTOCOL_MAP = {
    "O": "OSPF",
    "C": "Connected",
    "L": "Local",
    "K": "Kernel",
    "B": "BGP",
    "S": "Static",
}


@dataclass
class Route:
    """A single route from the routing table."""

    device: str
    prefix: str
    protocol: str
    metric: str
    next_hop: str
    interface: str
    selected: bool


def _parse_routes(device_name: str, output: str) -> list[Route]:
    """Parse 'show ip route' output into structured route objects."""
    routes: list[Route] = []
    current_prefix = ""

    for line in output.splitlines():
        # Match primary route lines like: O>* 10.0.0.2/32 [110/20] via 10.0.1.1, eth1
        match = re.match(
            r"\s*([OCLKBSNRITEFAV])([>*\s]*)\s*(\d+\.\d+\.\d+\.\d+/\d+)\s+"
            r"(?:\[(\d+/\d+)\])?\s*"
            r"(?:via\s+(\S+),\s+(\S+)|is directly connected,\s+(\S+))?",
            line,
        )
        if match:
            proto_code = match.group(1)
            selected = ">" in (match.group(2) or "")
            prefix = match.group(3)
            metric = match.group(4) or ""
            next_hop = match.group(5) or "connected"
            interface = match.group(6) or match.group(7) or ""
            current_prefix = prefix

            routes.append(Route(
                device=device_name,
                prefix=prefix,
                protocol=PROTOCOL_MAP.get(proto_code, proto_code),
                metric=metric,
                next_hop=next_hop,
                interface=interface.rstrip(","),
                selected=selected,
            ))
            continue

        # Match ECMP continuation lines like:   *  via 10.0.1.3, eth2
        ecmp_match = re.match(
            r"\s+\*?\s+via\s+(\S+),\s+(\S+)",
            line,
        )
        if ecmp_match and current_prefix:
            routes.append(Route(
                device=device_name,
                prefix=f"  (ecmp) {current_prefix}",
                protocol="",
                metric="",
                next_hop=ecmp_match.group(1),
                interface=ecmp_match.group(2).rstrip(","),
                selected=True,
            ))

    return routes


async def _get_routes(device_name: str) -> list[Route]:
    """Collect routes from a single device."""
    container = f"{CLAB_PREFIX}-{device_name}"
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container, "vtysh", "-c", "show ip route",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return _parse_routes(device_name, stdout.decode())


async def collect_all_routes(device_filter: str | None = None) -> list[Route]:
    """Collect routes from all (or filtered) devices."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    devices = [d.name for d in topology.devices]

    if device_filter:
        devices = [d for d in devices if d == device_filter]
        if not devices:
            console.print(f"[red]Device '{device_filter}' not found in topology.[/red]")
            sys.exit(1)

    tasks = [_get_routes(d) for d in sorted(devices)]
    results = await asyncio.gather(*tasks)
    return [route for device_routes in results for route in device_routes]


def main() -> None:
    """Collect and display routing tables."""
    parser = argparse.ArgumentParser(description="Structured routing table viewer")
    parser.add_argument("--device", type=str, help="Filter by device name")
    parser.add_argument("--protocol", type=str, help="Filter by protocol (ospf, bgp, connected)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    routes = asyncio.run(collect_all_routes(args.device))

    if args.protocol:
        proto = args.protocol.upper()
        if proto == "CONNECTED":
            proto = "Connected"
        routes = [r for r in routes if r.protocol.upper() == proto]

    if args.json:
        print(json.dumps([asdict(r) for r in routes], indent=2))
        return

    console.print()
    title = "Routing Tables"
    if args.device:
        title += f" ({args.device})"
    if args.protocol:
        title += f" [{args.protocol}]"
    console.print(Panel(f"[bold]Network as Code -- {title}[/bold]", expand=False))
    console.print()

    table = Table(title=title)
    table.add_column("Device", style="cyan")
    table.add_column("Prefix")
    table.add_column("Protocol")
    table.add_column("Metric")
    table.add_column("Next Hop")
    table.add_column("Interface")

    for r in routes:
        proto_style = {
            "OSPF": "green",
            "BGP": "magenta",
            "Connected": "blue",
            "Kernel": "yellow",
        }.get(r.protocol, "white")

        table.add_row(
            r.device,
            r.prefix,
            f"[{proto_style}]{r.protocol}[/{proto_style}]" if r.protocol else "",
            r.metric,
            r.next_hop,
            r.interface,
        )

    console.print(table)
    console.print()
    console.print(f"Total routes: {len(routes)}")
    console.print()


if __name__ == "__main__":
    main()
