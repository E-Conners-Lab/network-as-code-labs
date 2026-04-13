"""Topology diagram generator -- ASCII and Mermaid views of the fabric.

Reads the data model and generates visual representations of the fabric
topology showing devices, roles, links, and optionally live link state.

Usage:
    uv run python -m scripts.topology_diagram
    uv run python -m scripts.topology_diagram --mermaid
    uv run python -m scripts.topology_diagram --mermaid > docs/topology.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent


def _generate_ascii(parsed: dict) -> str:
    """Generate an ASCII topology diagram."""
    topology = parsed["topology"]
    underlay = parsed["underlay"]

    spines = [d for d in topology.devices if d.role.value == "spine"]
    leafs = [d for d in topology.devices if d.role.value == "leaf"]
    borders = [d for d in topology.devices if d.role.value == "border_leaf"]

    lines: list[str] = []
    lines.append("")
    lines.append("    Spine-Leaf Fabric Topology")
    lines.append("    ==========================")
    lines.append("")

    # Spine tier
    spine_str = "    ".join(
        f"[ {s.name} ({s.loopback.ip}) {'RR' if s.route_reflector else ''} ]"
        for s in spines
    )
    lines.append(f"  {spine_str}")
    lines.append("")

    # Connection indicators
    connector_width = len(spine_str) // 2
    lines.append("  " + "|" * 4 + " " * 4 + "|" * 4)
    lines.append("  " + "+" + "-" * (connector_width) + "+")
    lines.append("  " + "|" * 4 + " " * 4 + "|" * 4)
    lines.append("")

    # Leaf tier
    leaf_str = "    ".join(f"[ {l.name} ({l.loopback.ip}) ]" for l in leafs)
    lines.append(f"  {leaf_str}")
    lines.append("")

    # Border tier
    border_str = "    ".join(f"[ {b.name} ({b.loopback.ip}) ]" for b in borders)
    lines.append(f"  {border_str}")
    lines.append("")

    # Link summary
    lines.append(f"  Links: {len(underlay.links)} point-to-point")
    lines.append(f"  Underlay: OSPF area {underlay.ospf.area}")
    lines.append(
        f"  Overlay: iBGP with {len([s for s in spines if s.route_reflector])} route reflectors"
    )
    lines.append("")

    return "\n".join(lines)


def _generate_mermaid(parsed: dict) -> str:
    """Generate a Mermaid diagram of the topology."""
    topology = parsed["topology"]
    underlay = parsed["underlay"]

    lines: list[str] = []
    lines.append("```mermaid")
    lines.append("graph TD")
    lines.append("")

    # Define nodes with styling
    for d in topology.devices:
        role = d.role.value.replace("_", " ").title()
        rr = " / RR" if d.route_reflector else ""
        lines.append(f'    {d.name}["{d.name}<br/>{d.loopback.ip}<br/>{role}{rr}"]')

    lines.append("")

    # Define links
    for link in underlay.links:
        lines.append(
            f"    {link.a_device} --- |{link.a_ip} -- {link.b_ip}| {link.b_device}"
        )

    lines.append("")

    # Styling
    lines.append("    classDef spine fill:#2196F3,stroke:#1565C0,color:white")
    lines.append("    classDef leaf fill:#4CAF50,stroke:#2E7D32,color:white")
    lines.append("    classDef border fill:#FF9800,stroke:#E65100,color:white")

    spines = [d.name for d in topology.devices if d.role.value == "spine"]
    leafs = [d.name for d in topology.devices if d.role.value == "leaf"]
    borders = [d.name for d in topology.devices if d.role.value == "border_leaf"]

    if spines:
        lines.append(f"    class {','.join(spines)} spine")
    if leafs:
        lines.append(f"    class {','.join(leafs)} leaf")
    if borders:
        lines.append(f"    class {','.join(borders)} border")

    lines.append("```")
    return "\n".join(lines)


def main() -> None:
    """Generate and display the topology diagram."""
    parser = argparse.ArgumentParser(description="Topology diagram generator")
    parser.add_argument(
        "--mermaid", action="store_true", help="Output as Mermaid markdown"
    )
    args = parser.parse_args()

    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors.[/red]")
        sys.exit(1)

    if args.mermaid:
        print(_generate_mermaid(parsed))
    else:
        console.print()
        console.print(
            Panel("[bold]Network as Code -- Topology Diagram[/bold]", expand=False)
        )
        console.print(_generate_ascii(parsed))


if __name__ == "__main__":
    main()
