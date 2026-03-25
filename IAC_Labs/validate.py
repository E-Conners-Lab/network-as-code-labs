"""Validation entry point for the Network as Code data model.

Runs all four validation layers in order: format, syntax, semantic, and
compliance. Each layer builds on the previous one. If format validation
fails, syntax cannot run because the YAML is unreadable. If syntax
fails, semantic cannot run because the data is not structurally valid.
Compliance runs last because it checks policy conformance on data that
is already known to be logically correct.

Usage:
    uv run python validate.py
    uv run python validate.py --html reports/validation-report.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validators import ValidationLevel, ValidationResult, parse_all_files
from validators.compliance_validator import validate_compliance
from validators.format_validator import validate_format
from validators.semantic_validator import validate_semantic
from validators.syntax_validator import validate_syntax

console = Console()

BASE_DIR = Path(__file__).parent


def _print_results(title: str, results: list[ValidationResult]) -> bool:
    """Print results for one validation layer. Returns True if all passed."""
    console.print(f"[bold]{title}[/bold]")
    all_passed = True
    for r in results:
        if r.passed:
            console.print(f"  [green]PASS[/green]  {r.rule_id}  {r.message}")
        else:
            all_passed = False
            console.print(f"  [red]FAIL[/red]  {r.rule_id}  {r.message}")
            for detail in r.details:
                console.print(f"       [dim]{detail}[/dim]")
    console.print()
    return all_passed


def _print_summary(parsed: dict) -> None:
    """Print a summary table of the validated data model."""
    topology = parsed["topology"]
    underlay = parsed["underlay"]
    overlay = parsed["overlay"]
    vrfs = parsed["vrfs"]
    networks = parsed["networks"]
    interfaces = parsed["interfaces"]

    table = Table(title="Fabric Data Model Summary")
    table.add_column("Component", style="cyan")
    table.add_column("Count", style="green", justify="right")

    device_roles: dict[str, int] = {}
    for device in topology.devices:
        role_label = device.role.value.replace("_", " ").title()
        device_roles[role_label] = device_roles.get(role_label, 0) + 1

    table.add_row("Devices", str(len(topology.devices)))
    for role_label, count in sorted(device_roles.items()):
        table.add_row(f"  {role_label}s", str(count))
    table.add_row("P2P Links", str(len(underlay.links)))
    table.add_row("Route Reflectors", str(len(overlay.route_reflectors)))
    table.add_row("VRFs", str(len(vrfs.vrfs)))
    table.add_row("Network Segments", str(len(networks.networks)))
    table.add_row("Interface Assignments", str(len(interfaces.interfaces)))

    console.print(table)


def main() -> None:
    """Run the full four-layer validation pipeline."""
    parser = argparse.ArgumentParser(description="Validate the NaC data model")
    parser.add_argument(
        "--html",
        type=str,
        default=None,
        help="Generate an HTML report at the given path (requires pytest)",
    )
    args = parser.parse_args()

    if args.html:
        import subprocess

        report_path = Path(args.html)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "--html", str(report_path),
                "--self-contained-html",
                "-v",
            ],
            cwd=str(BASE_DIR),
        )
        sys.exit(result.returncode)

    console.print()
    console.print(
        Panel("[bold]Network as Code -- Data Model Validation[/bold]", expand=False)
    )
    console.print()

    # Layer 1: Format
    format_results = validate_format(BASE_DIR)
    format_ok = _print_results("Layer 1: Format Validation", format_results)
    if not format_ok:
        console.print("[bold red]Format validation failed. Fix YAML errors before proceeding.[/bold red]")
        sys.exit(1)

    # Layer 2: Syntax
    syntax_results = validate_syntax(BASE_DIR)
    syntax_ok = _print_results("Layer 2: Syntax Validation", syntax_results)
    if not syntax_ok:
        console.print("[bold red]Syntax validation failed. Fix schema errors before proceeding.[/bold red]")
        sys.exit(1)

    # Parse models for semantic and compliance layers
    parsed, parse_errors = parse_all_files(BASE_DIR)
    if parse_errors:
        for e in parse_errors:
            console.print(f"  [red]FAIL[/red]  {e.rule_id}  {e.message}")
        sys.exit(1)

    # Layer 3: Semantic
    semantic_results = validate_semantic(parsed)
    semantic_ok = _print_results("Layer 3: Semantic Validation", semantic_results)
    if not semantic_ok:
        console.print("[bold red]Semantic validation failed. Fix logical inconsistencies.[/bold red]")
        sys.exit(1)

    # Layer 4: Compliance
    compliance_results = validate_compliance(parsed)
    compliance_ok = _print_results("Layer 4: Compliance Validation", compliance_results)
    if not compliance_ok:
        console.print("[bold red]Compliance validation failed. Fix policy violations.[/bold red]")
        sys.exit(1)

    _print_summary(parsed)

    total = len(format_results) + len(syntax_results) + len(semantic_results) + len(compliance_results)
    console.print()
    console.print(f"[bold green]All {total} checks passed across 4 validation layers.[/bold green]")
    console.print()


if __name__ == "__main__":
    main()
