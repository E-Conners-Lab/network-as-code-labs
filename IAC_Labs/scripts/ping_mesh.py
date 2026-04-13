"""Loopback-to-loopback ping mesh test.

Pings every loopback from every other device to prove full fabric
reachability. A healthy spine-leaf fabric should have 100% success
across all pairs. Any failure indicates a routing or forwarding issue.

Usage:
    uv run python -m scripts.ping_mesh
    uv run python -m scripts.ping_mesh --json
"""

from __future__ import annotations

import asyncio
import argparse
import json
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


@dataclass
class PingResult:
    """Result of a single ping from source to destination."""

    source: str
    destination: str
    dest_ip: str
    success: bool
    rtt_ms: float | None = None


async def _ping(
    source_container: str, source_name: str, dest_name: str, dest_ip: str
) -> PingResult:
    """Ping a destination IP from a source container."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        source_container,
        "ping",
        "-c",
        "1",
        "-W",
        "2",
        str(dest_ip),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode()

    success = proc.returncode == 0
    rtt = None
    if success:
        for line in output.splitlines():
            if "time=" in line:
                try:
                    rtt = float(line.split("time=")[1].split()[0])
                except (IndexError, ValueError):
                    pass

    return PingResult(
        source=source_name,
        destination=dest_name,
        dest_ip=str(dest_ip),
        success=success,
        rtt_ms=rtt,
    )


async def run_ping_mesh() -> list[PingResult]:
    """Ping every loopback from every device."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors. Run validate.py first.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    devices = {d.name: d.loopback.ip for d in topology.devices}

    tasks = []
    for src_name in sorted(devices):
        for dst_name, dst_ip in sorted(devices.items()):
            if src_name == dst_name:
                continue
            container = f"{CLAB_PREFIX}-{src_name}"
            tasks.append(_ping(container, src_name, dst_name, dst_ip))

    return await asyncio.gather(*tasks)


def _print_matrix(results: list[PingResult]) -> None:
    """Print a ping matrix table."""
    devices = sorted(
        set(r.source for r in results) | set(r.destination for r in results)
    )

    table = Table(title="Loopback Ping Mesh")
    table.add_column("From / To", style="cyan")
    for d in devices:
        table.add_column(d, justify="center")

    lookup = {(r.source, r.destination): r for r in results}

    for src in devices:
        row = []
        for dst in devices:
            if src == dst:
                row.append("[dim]--[/dim]")
            else:
                result = lookup.get((src, dst))
                if result and result.success:
                    rtt = f"{result.rtt_ms:.1f}" if result.rtt_ms else "ok"
                    row.append(f"[green]{rtt}[/green]")
                else:
                    row.append("[red]FAIL[/red]")
        table.add_row(src, *row)

    console.print(table)


def main() -> None:
    """Run the ping mesh and display results."""
    parser = argparse.ArgumentParser(description="Loopback ping mesh test")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = asyncio.run(run_ping_mesh())

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
        return

    console.print()
    console.print(Panel("[bold]Network as Code -- Ping Mesh Test[/bold]", expand=False))
    console.print()

    _print_matrix(results)

    total = len(results)
    passed = sum(1 for r in results if r.success)
    console.print()
    if passed == total:
        console.print(
            f"[bold green]{passed}/{total} pings successful. Full mesh reachability confirmed.[/bold green]"
        )
    else:
        console.print(
            f"[bold red]{passed}/{total} pings successful. {total - passed} failures detected.[/bold red]"
        )
    console.print()


if __name__ == "__main__":
    main()
