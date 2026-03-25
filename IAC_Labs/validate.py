"""Validation entry point for the Network as Code data model.

Loads all YAML files from the data directory and runs them through the
Pydantic schema models in two stages. Stage 1 validates each file
individually against its own schema. Stage 2 composes all validated
models into the FabricDataModel and runs cross-file checks that catch
referential integrity errors like a network pointing to a VRF that
does not exist or a link referencing a device not in the topology.

Usage:
    uv run python validate.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TypedDict, cast

import yaml
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from schemas.models import (
    DefaultsModel,
    FabricConfig,
    FabricDataModel,
    InterfacesModel,
    NetworksModel,
    OverlayModel,
    TopologyModel,
    UnderlayModel,
    VRFsModel,
)

console = Console()

DATA_DIR = Path(__file__).parent / "data"


class ParsedModels(TypedDict):
    """Typed container for all validated data model components."""

    fabric: FabricConfig
    topology: TopologyModel
    underlay: UnderlayModel
    overlay: OverlayModel
    defaults: DefaultsModel
    vrfs: VRFsModel
    networks: NetworksModel
    interfaces: InterfacesModel


def load_yaml(file_path: Path) -> dict[str, Any]:
    """Load and parse a YAML file, raising clear errors on failure."""
    if not file_path.exists():
        raise FileNotFoundError(f"Required data file not found: {file_path}")

    with open(file_path) as f:
        content = yaml.safe_load(f)

    if content is None:
        raise ValueError(f"Empty YAML file: {file_path}")

    if not isinstance(content, dict):
        raise ValueError(
            f"Expected a YAML mapping in {file_path}, "
            f"got {type(content).__name__}"
        )

    return content


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Turn a Pydantic ValidationError into human-readable lines."""
    lines: list[str] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"])
        lines.append(f"  {location}: {err['msg']}")
    return lines


def validate_individual_files() -> ParsedModels:
    """Validate each YAML file against its individual Pydantic model.

    Returns a typed dict of validated model instances keyed by component
    name. Prints pass/fail status for each file. If any file fails
    validation, prints all errors and exits with code 1.
    """
    file_specs: list[tuple[str, Path, type[BaseModel], str | None]] = [
        ("fabric", DATA_DIR / "fabric.yaml", FabricConfig, "fabric"),
        ("topology", DATA_DIR / "topology.yaml", TopologyModel, None),
        ("underlay", DATA_DIR / "underlay.yaml", UnderlayModel, None),
        ("overlay", DATA_DIR / "overlay.yaml", OverlayModel, None),
        ("defaults", DATA_DIR / "defaults.yaml", DefaultsModel, "defaults"),
        ("vrfs", DATA_DIR / "services" / "vrfs.yaml", VRFsModel, None),
        ("networks", DATA_DIR / "services" / "networks.yaml", NetworksModel, None),
        (
            "interfaces",
            DATA_DIR / "services" / "interfaces.yaml",
            InterfacesModel,
            None,
        ),
    ]

    parsed: dict[str, Any] = {}
    failed = False

    for name, path, model_class, root_key in file_specs:
        relative = path.relative_to(Path.cwd())
        try:
            raw = load_yaml(path)
            data = raw[root_key] if root_key else raw
            parsed[name] = model_class.model_validate(data)

            console.print(f"  [green]PASS[/green]  {relative}")

        except KeyError:
            console.print(
                f"  [red]FAIL[/red]  {relative}: "
                f"missing required top-level key '{root_key}'"
            )
            failed = True

        except (FileNotFoundError, ValueError) as exc:
            console.print(f"  [red]FAIL[/red]  {relative}: {exc}")
            failed = True

        except ValidationError as exc:
            console.print(f"  [red]FAIL[/red]  {relative}")
            for line in _format_validation_errors(exc):
                console.print(f"       [dim]{line}[/dim]")
            failed = True

    if failed:
        console.print()
        console.print("[bold red]Individual file validation failed.[/bold red]")
        sys.exit(1)

    return cast(ParsedModels, parsed)


def validate_cross_references(parsed: ParsedModels) -> None:
    """Run cross-file validation by composing all models into FabricDataModel.

    This catches referential integrity errors: links pointing to
    nonexistent devices, networks referencing undefined VRFs, IPs
    outside their designated ranges, and inconsistencies between the
    topology and overlay route reflector declarations.
    """
    try:
        FabricDataModel.model_validate(parsed)
        console.print("  [green]PASS[/green]  Cross-reference validation")
    except ValidationError as exc:
        console.print("  [red]FAIL[/red]  Cross-reference validation")
        for line in _format_validation_errors(exc):
            console.print(f"       [dim]{line}[/dim]")
        console.print()
        console.print("[bold red]Cross-reference validation failed.[/bold red]")
        sys.exit(1)


def print_summary(parsed: ParsedModels) -> None:
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

    console.print()
    console.print(table)


def main() -> None:
    """Run the full validation pipeline against the data model."""
    console.print()
    console.print(
        Panel("[bold]Network as Code -- Data Model Validation[/bold]", expand=False)
    )
    console.print()

    console.print("[bold]Stage 1: Individual File Validation[/bold]")
    parsed = validate_individual_files()
    console.print()

    console.print("[bold]Stage 2: Cross-Reference Validation[/bold]")
    validate_cross_references(parsed)

    print_summary(parsed)

    console.print()
    console.print("[bold green]All validations passed.[/bold green]")
    console.print()


if __name__ == "__main__":
    main()
