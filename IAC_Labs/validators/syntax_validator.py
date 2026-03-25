"""Syntax validation layer -- Pydantic schema enforcement.

This layer feeds each YAML file through its corresponding Pydantic model.
Pydantic checks field types, value constraints (ranges, patterns, enums),
required vs optional fields, and single-model validators like "dead
interval >= hello interval." If a file fails format validation it cannot
reach this layer, so syntax validation always operates on parseable YAML.

Rule IDs:
    SYN-01  Individual file passes its Pydantic schema
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from validators import (
    FILE_SPECS,
    ValidationLevel,
    ValidationResult,
    load_yaml_safe,
)

_LEVEL = ValidationLevel.SYNTAX


def validate_syntax(base_dir: Path) -> list[ValidationResult]:
    """Validate each data file against its Pydantic model.

    Returns one PASS result per file that validates cleanly, or one FAIL
    result per Pydantic error encountered. A single file can produce
    multiple FAIL results if it has several schema violations.
    """
    results: list[ValidationResult] = []

    for name, rel_path, model_class, root_key in FILE_SPECS:
        file_path = base_dir / rel_path

        raw, error = load_yaml_safe(file_path)
        if error is not None:
            # Format-level problem; skip (format_validator reports it)
            continue

        assert raw is not None

        try:
            data: Any = raw[root_key] if root_key else raw
        except KeyError:
            # Missing root key; format_validator reports it
            continue

        try:
            model_class.model_validate(data)
            results.append(
                ValidationResult(
                    level=_LEVEL,
                    rule_id="SYN-01",
                    message=f"{rel_path}: schema valid",
                    passed=True,
                    file_path=rel_path,
                )
            )
        except ValidationError as exc:
            for err in exc.errors():
                location = ".".join(str(p) for p in err["loc"])
                results.append(
                    ValidationResult(
                        level=_LEVEL,
                        rule_id="SYN-01",
                        message=f"{rel_path} [{location}]: {err['msg']}",
                        passed=False,
                        file_path=rel_path,
                    )
                )

    return results
