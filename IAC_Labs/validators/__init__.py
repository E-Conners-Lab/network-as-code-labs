"""Four-layer validation framework for the Network as Code data model.

This package implements the validation pipeline described in Lab 2:

    Format   -- Is the YAML well-formed?
    Syntax   -- Do fields match schema types and constraints?
    Semantic -- Are cross-file references and logical rules consistent?
    Compliance -- Does the config meet organizational policy?

Each layer produces a list of ValidationResult objects. A result carries
the layer it belongs to, a rule identifier, a human-readable message, and
a pass/fail status. The rule ID makes results machine-parseable so CI
pipelines can gate on specific checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

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


class ValidationLevel(str, Enum):
    """The four validation layers, ordered from cheapest to most expensive."""

    FORMAT = "format"
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    COMPLIANCE = "compliance"


@dataclass(frozen=True)
class ValidationResult:
    """A single check outcome from any validation layer.

    Attributes:
        level: Which validation layer produced this result.
        rule_id: Machine-readable identifier (e.g., SEM-03).
        message: Human-readable explanation of the result.
        passed: True if the check succeeded.
        file_path: Optional path to the file that was checked.
        details: Optional extra context for debugging.
    """

    level: ValidationLevel
    rule_id: str
    message: str
    passed: bool
    file_path: str | None = None
    details: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared data loading helpers
# ---------------------------------------------------------------------------

#: Maps component name to (relative path, Pydantic model class, root key).
#: root_key is the top-level YAML key to extract before validation, or None
#: if the file's entire content maps directly to the model.
FILE_SPECS: list[tuple[str, str, type[Any], str | None]] = [
    ("fabric", "data/fabric.yaml", FabricConfig, "fabric"),
    ("topology", "data/topology.yaml", TopologyModel, None),
    ("underlay", "data/underlay.yaml", UnderlayModel, None),
    ("overlay", "data/overlay.yaml", OverlayModel, None),
    ("defaults", "data/defaults.yaml", DefaultsModel, "defaults"),
    ("vrfs", "data/services/vrfs.yaml", VRFsModel, None),
    ("networks", "data/services/networks.yaml", NetworksModel, None),
    ("interfaces", "data/services/interfaces.yaml", InterfacesModel, None),
]


def load_yaml_safe(file_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load a YAML file, returning (data, None) on success or (None, error) on failure."""
    if not file_path.exists():
        return None, f"File not found: {file_path}"
    try:
        with open(file_path) as f:
            content = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return None, f"YAML parse error: {exc}"

    if content is None:
        return None, f"Empty YAML file: {file_path}"
    if not isinstance(content, dict):
        return None, f"Expected a YAML mapping, got {type(content).__name__}"

    return content, None


def parse_all_files(
    base_dir: Path,
) -> tuple[dict[str, Any], list[ValidationResult]]:
    """Load and validate all YAML files through their Pydantic models.

    Returns a tuple of (parsed_models_dict, list_of_errors). The dict is
    keyed by component name and contains validated Pydantic model instances.
    If any file fails format or syntax validation, its entry is missing
    from the dict and the error appears in the results list.
    """
    parsed: dict[str, Any] = {}
    errors: list[ValidationResult] = []

    for name, rel_path, model_class, root_key in FILE_SPECS:
        file_path = base_dir / rel_path
        raw, load_error = load_yaml_safe(file_path)

        if load_error is not None:
            errors.append(
                ValidationResult(
                    level=ValidationLevel.FORMAT,
                    rule_id="FMT-00",
                    message=load_error,
                    passed=False,
                    file_path=rel_path,
                )
            )
            continue

        assert raw is not None
        data = raw[root_key] if root_key else raw

        try:
            parsed[name] = model_class.model_validate(data)
        except KeyError:
            errors.append(
                ValidationResult(
                    level=ValidationLevel.FORMAT,
                    rule_id="FMT-01",
                    message=f"Missing required top-level key '{root_key}'",
                    passed=False,
                    file_path=rel_path,
                )
            )
        except ValidationError as exc:
            for err in exc.errors():
                location = ".".join(str(p) for p in err["loc"])
                errors.append(
                    ValidationResult(
                        level=ValidationLevel.SYNTAX,
                        rule_id="SYN-00",
                        message=f"{location}: {err['msg']}",
                        passed=False,
                        file_path=rel_path,
                    )
                )

    return parsed, errors


__all__ = [
    "ValidationLevel",
    "ValidationResult",
    "FILE_SPECS",
    "FabricDataModel",
    "load_yaml_safe",
    "parse_all_files",
]
