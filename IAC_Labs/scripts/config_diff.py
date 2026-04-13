"""Config drift detector -- compare intended configs against running state.

Pulls the running configuration from each device and diffs it against
the generated config in the configs/ directory. Any difference is
potential drift: either someone changed something manually, or the
deployment didn't fully apply.

This script is the foundation for Lab 6's drift detection engine.

Usage:
    uv run python -m scripts.config_diff
    uv run python -m scripts.config_diff --json
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


@dataclass
class ConfigDiff:
    """Diff result for a single device."""

    device: str
    has_drift: bool
    intended_lines: int
    running_lines: int
    diff_lines: list[str] = field(default_factory=list)


def _normalize_config(text: str) -> list[str]:
    """Normalize a config for comparison.

    Strips blank lines, comment-only lines, and FRR version headers
    that differ between generated and running configs.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped == "!" or stripped == "end":
            continue
        if stripped.startswith("frr version"):
            continue
        if stripped.startswith("frr defaults"):
            continue
        if stripped.startswith("Building configuration"):
            continue
        if stripped.startswith("Current configuration"):
            continue
        if "no ipv6 forwarding" in stripped:
            continue
        if stripped.startswith("exit"):
            continue
        lines.append(stripped)
    return lines


async def _get_running_config(container: str) -> str:
    """Pull the running config from a device."""
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


async def _diff_device(device_name: str, configs_dir: Path) -> ConfigDiff:
    """Compare intended config against running config for one device."""
    config_path = configs_dir / f"{device_name}.conf"
    if not config_path.exists():
        return ConfigDiff(
            device=device_name,
            has_drift=True,
            intended_lines=0,
            running_lines=0,
            diff_lines=[f"No intended config found at {config_path}"],
        )

    container = f"{CLAB_PREFIX}-{device_name}"
    intended_text = config_path.read_text()
    running_text = await _get_running_config(container)

    intended = _normalize_config(intended_text)
    running = _normalize_config(running_text)

    diff = list(
        difflib.unified_diff(
            intended,
            running,
            fromfile=f"{device_name} (intended)",
            tofile=f"{device_name} (running)",
            lineterm="",
        )
    )

    return ConfigDiff(
        device=device_name,
        has_drift=len(diff) > 0,
        intended_lines=len(intended),
        running_lines=len(running),
        diff_lines=diff,
    )


async def diff_all(configs_dir: Path) -> list[ConfigDiff]:
    """Diff all devices concurrently."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors. Run validate.py first.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    tasks = [_diff_device(d.name, configs_dir) for d in topology.devices]
    return await asyncio.gather(*tasks)


def main() -> None:
    """Run config diff and display results."""
    parser = argparse.ArgumentParser(description="Compare intended vs running configs")
    parser.add_argument(
        "--configs-dir", type=str, default="configs", help="Generated configs directory"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    configs_dir = BASE_DIR / args.configs_dir
    if not configs_dir.exists():
        console.print(f"[red]Configs directory not found: {configs_dir}[/red]")
        console.print(
            "Run the generator first: uv run python -m generators.python.render"
        )
        sys.exit(1)

    diffs = asyncio.run(diff_all(configs_dir))

    if args.json:
        print(
            json.dumps(
                [asdict(d) for d in sorted(diffs, key=lambda x: x.device)], indent=2
            )
        )
        return

    console.print()
    console.print(
        Panel("[bold]Network as Code -- Config Drift Check[/bold]", expand=False)
    )
    console.print()

    table = Table(title="Drift Summary")
    table.add_column("Device", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Intended Lines", justify="right")
    table.add_column("Running Lines", justify="right")
    table.add_column("Diff Lines", justify="right")

    for d in sorted(diffs, key=lambda x: x.device):
        status = "[red]DRIFT[/red]" if d.has_drift else "[green]CLEAN[/green]"
        diff_count = len(
            [l for l in d.diff_lines if l.startswith("+") or l.startswith("-")]
        )
        table.add_row(
            d.device,
            status,
            str(d.intended_lines),
            str(d.running_lines),
            str(diff_count),
        )

    console.print(table)

    # Show diffs for devices with drift
    drifted = [d for d in diffs if d.has_drift]
    if drifted:
        for d in sorted(drifted, key=lambda x: x.device):
            console.print()
            console.print(f"[bold red]Drift on {d.device}:[/bold red]")
            diff_text = "\n".join(d.diff_lines)
            console.print(Syntax(diff_text, "diff", theme="monokai"))

    total = len(diffs)
    clean = sum(1 for d in diffs if not d.has_drift)
    console.print()
    if clean == total:
        console.print(
            f"[bold green]{clean}/{total} devices match intended config. No drift detected.[/bold green]"
        )
    else:
        console.print(
            f"[bold red]{total - clean}/{total} devices have configuration drift.[/bold red]"
        )
    console.print()


if __name__ == "__main__":
    main()
