"""Shared pytest fixtures for the validation test suite.

The base_dir fixture provides the project root so every test can find the
data files. The parsed_models fixture loads and validates all YAML files
once per session and caches the result, since the data model does not
change between tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from validators import parse_all_files


@pytest.fixture(scope="session")
def base_dir() -> Path:
    """Return the project root directory (parent of data/, schemas/, etc.)."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def parsed_models(base_dir: Path) -> dict[str, Any]:
    """Load all YAML files and validate through Pydantic schemas.

    Returns the dict of validated model instances. If any file fails to
    parse or validate, the fixture itself fails with a clear message
    rather than letting downstream tests run against incomplete data.
    """
    parsed, errors = parse_all_files(base_dir)
    if errors:
        messages = [f"  {e.rule_id} {e.file_path}: {e.message}" for e in errors]
        pytest.fail(
            "Data model has format/syntax errors, cannot run validation tests:\n"
            + "\n".join(messages)
        )
    return parsed
