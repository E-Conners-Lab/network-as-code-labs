"""Format validation layer -- YAML well-formedness checks.

This is the cheapest validation layer. It answers one question per file:
can the YAML be parsed into a Python dict without errors? A file that
fails format validation is unreadable, so no further layers can run
against it.

Rule IDs:
    FMT-01  File exists and is not empty
    FMT-02  YAML parses without syntax errors
    FMT-03  Top-level structure is a mapping (dict), not a list or scalar
    FMT-04  Required top-level key is present (for files that nest under a key)
"""

from __future__ import annotations

from pathlib import Path

from validators import (
    FILE_SPECS,
    ValidationLevel,
    ValidationResult,
    load_yaml_safe,
)

_LEVEL = ValidationLevel.FORMAT


def validate_format(base_dir: Path) -> list[ValidationResult]:
    """Run format checks against every data file in the model.

    Returns one result per file. A passing result means the file exists,
    parses cleanly, and has the expected top-level structure. A failing
    result includes the specific error so the engineer knows exactly what
    to fix.
    """
    results: list[ValidationResult] = []

    for _name, rel_path, _model_class, root_key in FILE_SPECS:
        file_path = base_dir / rel_path

        # FMT-01: file exists and is not empty
        if not file_path.exists():
            results.append(
                ValidationResult(
                    level=_LEVEL,
                    rule_id="FMT-01",
                    message=f"Required file not found: {rel_path}",
                    passed=False,
                    file_path=rel_path,
                )
            )
            continue

        # FMT-02 / FMT-03: parseable YAML that produces a mapping
        raw, error = load_yaml_safe(file_path)
        if error is not None:
            # load_yaml_safe covers parse errors, empty files, and non-mapping
            rule_id = "FMT-02" if "parse error" in error.lower() else "FMT-03"
            results.append(
                ValidationResult(
                    level=_LEVEL,
                    rule_id=rule_id,
                    message=f"{rel_path}: {error}",
                    passed=False,
                    file_path=rel_path,
                )
            )
            continue

        assert raw is not None

        # FMT-04: required top-level key present
        if root_key and root_key not in raw:
            results.append(
                ValidationResult(
                    level=_LEVEL,
                    rule_id="FMT-04",
                    message=(
                        f"{rel_path}: missing required top-level key '{root_key}'"
                    ),
                    passed=False,
                    file_path=rel_path,
                )
            )
            continue

        results.append(
            ValidationResult(
                level=_LEVEL,
                rule_id="FMT-01",
                message=f"{rel_path}: well-formed YAML",
                passed=True,
                file_path=rel_path,
            )
        )

    return results
