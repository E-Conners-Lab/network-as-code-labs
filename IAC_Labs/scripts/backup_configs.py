"""Config backup utility -- pull running configs from all devices.

Connects to every device, pulls the running configuration, and saves
timestamped copies to a backup directory. Useful for creating restore
points before making changes or for auditing what is actually running
on the network.

Usage:
    uv run python -m scripts.backup_configs
    uv run python -m scripts.backup_configs --output-dir backups/pre-change
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


async def _backup_device(device_name: str, output_dir: Path) -> tuple[str, bool, str]:
    """Pull running config from one device and save it."""
    container = f"{CLAB_PREFIX}-{device_name}"

    try:
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
        config = stdout.decode()

        if not config.strip():
            return device_name, False, "Empty config returned"

        output_path = output_dir / f"{device_name}.conf"
        output_path.write_text(config)
        line_count = len(config.splitlines())
        return device_name, True, f"{line_count} lines saved"

    except Exception as exc:
        return device_name, False, str(exc)


async def backup_all(output_dir: Path) -> list[tuple[str, bool, str]]:
    """Backup configs from all devices."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = [_backup_device(d.name, output_dir) for d in topology.devices]
    return await asyncio.gather(*tasks)


def main() -> None:
    """Run the backup and display results."""
    parser = argparse.ArgumentParser(
        description="Backup running configs from all devices"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: backups/<timestamp>)",
    )
    args = parser.parse_args()

    if args.output_dir:
        output_dir = BASE_DIR / args.output_dir
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_dir = BASE_DIR / "backups" / timestamp

    console.print()
    console.print(Panel("[bold]Network as Code -- Config Backup[/bold]", expand=False))
    console.print()
    console.print(f"Backup directory: {output_dir}")
    console.print()

    results = asyncio.run(backup_all(output_dir))

    table = Table(title="Backup Results")
    table.add_column("Device", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    for device, success, message in sorted(results):
        status = "[green]OK[/green]" if success else "[red]FAIL[/red]"
        table.add_row(device, status, message)

    console.print(table)

    total = len(results)
    saved = sum(1 for _, s, _ in results if s)
    console.print()
    console.print(
        f"[bold green]{saved}/{total} configs backed up to {output_dir}[/bold green]"
    )
    console.print()


if __name__ == "__main__":
    main()
