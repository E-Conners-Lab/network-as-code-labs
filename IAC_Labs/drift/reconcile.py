"""Drift reconciliation with three strategies.

When drift is detected, you have three options:

1. Auto-remediate: Push the intended config back to the device.
   Use this for low-risk drift like description changes.

2. Report: Generate a detailed drift report for human review.
   Use this when the drift might be intentional or needs investigation.

3. Absorb: Pull the running config from the device and update the
   data model to match. Use this when someone made an emergency fix
   that should become the new intended state.

Usage:
    uv run python -m drift.reconcile remediate
    uv run python -m drift.reconcile remediate --device spine1
    uv run python -m drift.reconcile report
    uv run python -m drift.reconcile absorb --device spine1
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

from drift.detect import detect_drift

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


async def _run_docker_exec(container: str, command: str) -> tuple[int, str]:
    """Run a command inside a Docker container."""
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
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, (stdout.decode() + stderr.decode()).strip()


async def remediate(configs_dir: Path, device_filter: str | None = None) -> None:
    """Push the intended config back to drifted devices.

    This is the simplest reconciliation strategy: overwrite whatever
    is on the device with what the data model says should be there.
    """
    results = await detect_drift(configs_dir, device_filter)
    drifted = [r for r in results if r.has_drift]

    if not drifted:
        console.print("[green]No drift detected. Nothing to remediate.[/green]")
        return

    console.print(f"[yellow]Remediating {len(drifted)} device(s)...[/yellow]\n")

    table = Table(title="Remediation Results")
    table.add_column("Device", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    for drift in drifted:
        config_path = configs_dir / f"{drift.device}.conf"
        config_text = config_path.read_text().strip()
        container = f"{CLAB_PREFIX}-{drift.device}"

        # Write intended config and load via vtysh
        rc, output = await _run_docker_exec(
            container,
            f"cat > /tmp/frr_nac.conf << 'NACEOF'\n{config_text}\nNACEOF",
        )
        rc2, output2 = await _run_docker_exec(
            container,
            "vtysh -f /tmp/frr_nac.conf 2>&1",
        )
        await _run_docker_exec(
            container,
            "cp /tmp/frr_nac.conf /etc/frr/frr.conf && vtysh -c 'write memory' 2>&1",
        )

        errors = [
            l
            for l in output2.splitlines()
            if "error" in l.lower() or "unknown" in l.lower()
        ]
        if errors:
            table.add_row(
                drift.device,
                "[yellow]WARN[/yellow]",
                f"Config pushed with warnings: {errors[0][:60]}",
            )
        else:
            table.add_row(drift.device, "[green]OK[/green]", "Intended config restored")

    console.print(table)


async def report(configs_dir: Path, device_filter: str | None = None) -> None:
    """Generate a drift report for human review.

    This does not change anything on the network. It produces a report
    showing exactly what drifted, on which device, and what the
    intended vs actual values are.
    """
    results = await detect_drift(configs_dir, device_filter)
    drifted = [r for r in results if r.has_drift]

    if not drifted:
        console.print("[green]No drift detected.[/green]")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = (
        report_dir
        / f"drift-report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.txt"
    )

    lines: list[str] = []
    lines.append(f"Drift Report - {timestamp}")
    lines.append(f"Devices checked: {len(results)}")
    lines.append(f"Devices with drift: {len(drifted)}")
    lines.append("")

    for r in drifted:
        lines.append(f"--- {r.device} ---")
        lines.append(f"Drift items: {len(r.drift_items)}")
        for item in r.drift_items:
            lines.append(f"  [{item.section}] intended: {item.intended}")
            lines.append(f"  [{item.section}] actual:   {item.actual}")
        lines.append("")

    report_text = "\n".join(lines)
    report_path.write_text(report_text)

    console.print(f"[green]Drift report saved to {report_path}[/green]\n")
    console.print(report_text)


async def absorb(configs_dir: Path, device_filter: str | None = None) -> None:
    """Pull running configs and save them as the new intended state.

    This strategy says "the device is right, the data model is wrong."
    Use it when an emergency out-of-band change was made that should
    become the new baseline. After absorbing, the drift detection
    engine will show clean for those devices.

    This only updates the generated config files, not the YAML data
    model. To fully absorb a change, you would also need to update
    the data model manually to reflect the new intent.
    """
    if not device_filter:
        console.print(
            "[red]Absorb requires --device to prevent accidentally overwriting all configs.[/red]"
        )
        sys.exit(1)

    container = f"{CLAB_PREFIX}-{device_filter}"
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
    running_config = stdout.decode()

    if not running_config.strip():
        console.print(f"[red]Could not pull config from {device_filter}.[/red]")
        sys.exit(1)

    config_path = configs_dir / f"{device_filter}.conf"
    backup_path = configs_dir / f"{device_filter}.conf.bak"

    # Backup the old intended config
    if config_path.exists():
        config_path.rename(backup_path)
        console.print(f"Backed up old config to {backup_path}")

    # Save running config as the new intended config
    config_path.write_text(running_config)
    console.print(
        f"[green]Absorbed running config from {device_filter} as new intended state.[/green]"
    )
    console.print(f"Saved to {config_path}")
    console.print()
    console.print("[yellow]Note: The YAML data model has not been updated.[/yellow]")
    console.print(
        "[yellow]To make this permanent, update the data model to match[/yellow]"
    )
    console.print("[yellow]the absorbed config and regenerate.[/yellow]")


def main() -> None:
    """Run drift reconciliation."""
    parser = argparse.ArgumentParser(description="Drift reconciliation")
    subparsers = parser.add_subparsers(dest="strategy", required=True)

    # Remediate
    rem_parser = subparsers.add_parser(
        "remediate", help="Push intended config to drifted devices"
    )
    rem_parser.add_argument("--device", type=str, help="Remediate a single device")
    rem_parser.add_argument("--configs-dir", type=str, default="configs")

    # Report
    rep_parser = subparsers.add_parser(
        "report", help="Generate drift report without changes"
    )
    rep_parser.add_argument("--device", type=str, help="Report on a single device")
    rep_parser.add_argument("--configs-dir", type=str, default="configs")

    # Absorb
    abs_parser = subparsers.add_parser(
        "absorb", help="Accept running config as new intent"
    )
    abs_parser.add_argument(
        "--device", type=str, required=True, help="Device to absorb from"
    )
    abs_parser.add_argument("--configs-dir", type=str, default="configs")

    args = parser.parse_args()
    configs_dir = BASE_DIR / args.configs_dir

    console.print()
    strategy_name = args.strategy.upper()
    console.print(
        Panel(
            f"[bold]Network as Code -- Drift Reconciliation ({strategy_name})[/bold]",
            expand=False,
        )
    )
    console.print()

    if args.strategy == "remediate":
        asyncio.run(remediate(configs_dir, args.device))
    elif args.strategy == "report":
        asyncio.run(report(configs_dir, args.device))
    elif args.strategy == "absorb":
        asyncio.run(absorb(configs_dir, args.device))


if __name__ == "__main__":
    main()
