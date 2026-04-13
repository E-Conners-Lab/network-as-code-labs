"""Tests for the format validation layer.

Format validation is the first gate: can each YAML file be parsed at all?
These tests verify that the validator correctly identifies well-formed
files and catches common format errors like missing files, invalid YAML
syntax, and wrong top-level structure.
"""

from __future__ import annotations

import textwrap
from pathlib import Path


from validators.format_validator import validate_format


class TestFormatValidationPassing:
    """Verify that the current data model passes all format checks."""

    def test_all_files_pass(self, base_dir: Path) -> None:
        results = validate_format(base_dir)
        failed = [r for r in results if not r.passed]
        assert not failed, f"Format failures: {[r.message for r in failed]}"

    def test_returns_one_result_per_file(self, base_dir: Path) -> None:
        results = validate_format(base_dir)
        assert len(results) == 8


class TestFormatValidationFailing:
    """Verify that format validation catches broken YAML."""

    def test_missing_file(self, tmp_path: Path) -> None:
        """A completely empty directory should fail for every expected file."""
        results = validate_format(tmp_path)
        failed = [r for r in results if not r.passed]
        assert len(failed) == 8
        assert all(r.rule_id == "FMT-01" for r in failed)

    def test_invalid_yaml_syntax(self, tmp_path: Path, base_dir: Path) -> None:
        """A file with broken YAML should produce a FMT-02 error."""
        # Copy valid files, then corrupt one
        _mirror_data_files(base_dir, tmp_path)
        bad_file = tmp_path / "data" / "fabric.yaml"
        bad_file.write_text("fabric:\n  name: [unterminated\n")

        results = validate_format(tmp_path)
        fabric_results = [
            r for r in results if r.file_path and "fabric.yaml" in r.file_path
        ]
        assert any(not r.passed for r in fabric_results)

    def test_missing_root_key(self, tmp_path: Path, base_dir: Path) -> None:
        """A file missing its expected root key should produce FMT-04."""
        _mirror_data_files(base_dir, tmp_path)
        bad_file = tmp_path / "data" / "fabric.yaml"
        bad_file.write_text(
            textwrap.dedent("""\
            wrong_key:
              name: test
        """)
        )

        results = validate_format(tmp_path)
        fabric_results = [
            r for r in results if r.file_path and "fabric.yaml" in r.file_path
        ]
        assert any(r.rule_id == "FMT-04" and not r.passed for r in fabric_results)


def _mirror_data_files(src: Path, dst: Path) -> None:
    """Copy the data directory structure so we can corrupt individual files."""
    for subdir in ["data", "data/services"]:
        (dst / subdir).mkdir(parents=True, exist_ok=True)

    import shutil

    for item in (src / "data").rglob("*.yaml"):
        rel = item.relative_to(src / "data")
        target = dst / "data" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
