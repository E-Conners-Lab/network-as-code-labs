"""Drift detection engine.

Compares the intended configuration (generated from the data model)
against the actual running configuration on each device. Any difference
is drift: either someone changed something manually (out-of-band), or
a deployment did not fully apply.

The detection engine normalizes both configs before comparison to
account for FRR reformatting commands (reordering, adding 'exit'
blocks, stripping comments). The goal is to detect meaningful drift,
not cosmetic differences in whitespace or command ordering.

Usage:
    uv run python -m drift.detect
    uv run python -m drift.detect --json
    uv run python -m drift.detect --device spine1
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validators import parse_all_files

console = Console()
BASE_DIR = Path(__file__).resolve().parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


@dataclass
class DriftItem:
    """A single configuration element that has drifted."""

    section: str
    intended: str
    actual: str


@dataclass
class DeviceDrift:
    """Drift report for a single device."""

    device: str
    has_drift: bool
    drift_items: list[DriftItem] = field(default_factory=list)
    diff_text: str = ""
    timestamp: str = ""


def _normalize_config(text: str) -> list[str]:
    """Normalize an FRR config for meaningful comparison.

    Strips noise that differs between generated and running configs
    but does not represent actual drift: version headers, blank lines,
    comments, 'exit' blocks, FRR internal decorators, and commands
    that FRR converts to a different form internally.
    """
    skip_prefixes = (
        "!",
        "end",
        "exit",
        "frr version",
        "frr defaults",
        "Building configuration",
        "Current configuration",
        "no ipv6 forwarding",
        "service integrated-vtysh-config",
        "log syslog",
        "line vty",
    )
    # Commands in our generated config that FRR converts internally:
    # - "network x.x.x.x/x area y" -> interface-level "ip ospf area"
    # - "passive-interface lo" -> interface-level "ip ospf passive"
    # - "auto-cost reference-bandwidth" is accepted but may not show in running
    # - "ip ospf hello-interval 10" is the default and FRR may omit it
    skip_patterns = (
        "network ",
        "passive-interface ",
        "auto-cost reference-bandwidth",
        "ip ospf hello-interval",
        "ip ospf passive",
        "rd ",
        "route-target ",
        "vni ",
        "exit-vni",
        "exit-address-family",
        "address-family l2vpn evpn",
        # FRR emits top-level vrf stanzas and internal BGP defaults in
        # show running-config that aren't part of our generated intent.
        "vrf ",
        "vnc ",
        "vrf-policy ",
    )
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        inner = stripped.lstrip()
        if any(inner.startswith(p) for p in skip_prefixes):
            continue
        if any(inner.startswith(p) for p in skip_patterns):
            continue
        # Normalize indentation to single space for comparison
        lines.append(inner)
    return sorted(set(lines))


def _extract_sections(text: str) -> dict[str, list[str]]:
    """Extract config into named sections for targeted drift analysis."""
    sections: dict[str, list[str]] = {
        "hostname": [],
        "interfaces": [],
        "ospf": [],
        "bgp": [],
        "other": [],
    }

    current = "other"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "!" or stripped == "end":
            continue

        if stripped.startswith("hostname"):
            current = "hostname"
        elif stripped.startswith("interface"):
            current = "interfaces"
        elif stripped.startswith("router ospf"):
            current = "ospf"
        elif stripped.startswith("router bgp"):
            current = "bgp"
        elif stripped.startswith("vrf "):
            # Top-level vrf stanzas are Linux VRF device config emitted by
            # FRR in show running-config. They're not part of our intent.
            current = "other"
        elif stripped.startswith("exit"):
            continue

        sections[current].append(stripped)

    return sections


def _find_drift_items(intended_text: str, running_text: str) -> list[DriftItem]:
    """Compare intended vs running by section and identify specific drifts."""
    intended_sections = _extract_sections(intended_text)
    running_sections = _extract_sections(running_text)

    # Lines to ignore in drift comparison
    noise_prefixes = (
        "!",
        "log ",
        "frr ",
        "no ipv6",
        "service ",
        "exit",
        "line vty",
        "network ",
        "passive-interface ",
        "auto-cost",
        "ip ospf hello-interval",
        "ip ospf passive",
        "rd ",
        "route-target ",
        "vni ",
        "exit-vni",
        "address-family l2vpn evpn",
        "vrf ",
        "vnc ",
        "vrf-policy ",
    )

    def _is_noise(line: str) -> bool:
        inner = line.lstrip()
        return any(inner.startswith(p) for p in noise_prefixes)

    items: list[DriftItem] = []

    for section in ["hostname", "interfaces", "ospf", "bgp"]:
        intended_set = {
            l for l in intended_sections.get(section, []) if not _is_noise(l)
        }
        running_set = {l for l in running_sections.get(section, []) if not _is_noise(l)}

        for line in sorted(intended_set - running_set):
            items.append(
                DriftItem(
                    section=section,
                    intended=line,
                    actual="(missing)",
                )
            )

        for line in sorted(running_set - intended_set):
            items.append(
                DriftItem(
                    section=section,
                    intended="(not in intent)",
                    actual=line,
                )
            )

    return items


async def _collect_running_config(container: str) -> str:
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


async def detect_drift(
    configs_dir: Path,
    device_filter: str | None = None,
) -> list[DeviceDrift]:
    """Detect drift across all (or filtered) devices."""
    parsed, errors = parse_all_files(BASE_DIR)
    if errors:
        console.print("[red]Data model has errors. Run validate.py first.[/red]")
        sys.exit(1)

    topology = parsed["topology"]
    devices = [d.name for d in topology.devices]

    if device_filter:
        devices = [d for d in devices if d == device_filter]
        if not devices:
            console.print(f"[red]Device '{device_filter}' not found.[/red]")
            sys.exit(1)

    timestamp = datetime.now(timezone.utc).isoformat()
    results: list[DeviceDrift] = []

    for device_name in sorted(devices):
        config_path = configs_dir / f"{device_name}.conf"
        if not config_path.exists():
            results.append(
                DeviceDrift(
                    device=device_name,
                    has_drift=True,
                    drift_items=[
                        DriftItem("config", f"No intended config at {config_path}", "")
                    ],
                    timestamp=timestamp,
                )
            )
            continue

        container = f"{CLAB_PREFIX}-{device_name}"
        intended_text = config_path.read_text()
        running_text = await _collect_running_config(container)

        # Normalized comparison for overall drift detection
        intended_norm = _normalize_config(intended_text)
        running_norm = _normalize_config(running_text)

        has_drift = intended_norm != running_norm

        # Section-level analysis for specific drift items
        drift_items = (
            _find_drift_items(intended_text, running_text) if has_drift else []
        )

        # Unified diff for display
        diff_lines = list(
            difflib.unified_diff(
                _normalize_config(intended_text),
                _normalize_config(running_text),
                fromfile=f"{device_name} (intended)",
                tofile=f"{device_name} (running)",
                lineterm="",
            )
        )

        results.append(
            DeviceDrift(
                device=device_name,
                has_drift=has_drift,
                drift_items=drift_items,
                diff_text="\n".join(diff_lines),
                timestamp=timestamp,
            )
        )

    return results


def _print_summary(results: list[DeviceDrift]) -> None:
    """Print the drift summary table."""
    table = Table(title="Drift Detection Results")
    table.add_column("Device", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Drift Items", justify="right")
    table.add_column("Sections Affected")

    for r in results:
        if r.has_drift:
            status = "[red]DRIFT[/red]"
            sections = (
                ", ".join(sorted(set(i.section for i in r.drift_items))) or "unknown"
            )
        else:
            status = "[green]CLEAN[/green]"
            sections = ""

        table.add_row(r.device, status, str(len(r.drift_items)), sections)

    console.print(table)


def _print_details(results: list[DeviceDrift]) -> None:
    """Print detailed drift information for devices with drift."""
    drifted = [r for r in results if r.has_drift]
    if not drifted:
        return

    for r in drifted:
        console.print()
        console.print(f"[bold red]Drift on {r.device}:[/bold red]")

        if r.drift_items:
            detail_table = Table(show_header=True)
            detail_table.add_column("Section", style="yellow")
            detail_table.add_column("Intended")
            detail_table.add_column("Running")

            for item in r.drift_items[:20]:
                detail_table.add_row(item.section, item.intended, item.actual)

            if len(r.drift_items) > 20:
                detail_table.add_row("...", f"({len(r.drift_items) - 20} more)", "")

            console.print(detail_table)


def main() -> None:
    """Run drift detection and display results."""
    parser = argparse.ArgumentParser(description="Detect configuration drift")
    parser.add_argument("--configs-dir", type=str, default="configs")
    parser.add_argument("--device", type=str, help="Check a single device")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    configs_dir = BASE_DIR / args.configs_dir
    if not configs_dir.exists():
        console.print(f"[red]Configs directory not found: {configs_dir}[/red]")
        console.print("Run the generator first.")
        sys.exit(1)

    results = asyncio.run(detect_drift(configs_dir, args.device))

    if args.json:
        data = [asdict(r) for r in results]
        print(json.dumps(data, indent=2))
        return

    console.print()
    console.print(
        Panel("[bold]Network as Code -- Drift Detection[/bold]", expand=False)
    )
    console.print()

    _print_summary(results)
    _print_details(results)

    total = len(results)
    clean = sum(1 for r in results if not r.has_drift)
    drifted = total - clean

    console.print()
    if drifted == 0:
        console.print(
            f"[bold green]{clean}/{total} devices match intended config. No drift detected.[/bold green]"
        )
    else:
        console.print(
            f"[bold red]{drifted}/{total} devices have configuration drift.[/bold red]"
        )
    console.print()

    sys.exit(1 if drifted > 0 else 0)


if __name__ == "__main__":
    main()
