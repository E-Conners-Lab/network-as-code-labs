"""Tests for the syntax validation layer.

Syntax validation runs each YAML file through its Pydantic schema. These
tests verify that the current data model is schema-valid and that the
validator catches type mismatches, constraint violations, and missing
required fields.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from validators.syntax_validator import validate_syntax


class TestSyntaxValidationPassing:
    """Verify that the current data model passes all syntax checks."""

    def test_all_files_pass(self, base_dir: Path) -> None:
        results = validate_syntax(base_dir)
        failed = [r for r in results if not r.passed]
        assert not failed, f"Syntax failures: {[r.message for r in failed]}"

    def test_returns_one_pass_per_file(self, base_dir: Path) -> None:
        results = validate_syntax(base_dir)
        passed = [r for r in results if r.passed]
        assert len(passed) == 8


class TestSyntaxValidationFailing:
    """Verify that syntax validation catches schema violations."""

    def test_invalid_asn(self, tmp_path: Path, base_dir: Path) -> None:
        """ASN outside the valid range should fail."""
        _mirror_data(base_dir, tmp_path)
        _patch_yaml(
            tmp_path / "data" / "fabric.yaml",
            lambda d: d["fabric"].__setitem__("asn", 0),
        )

        results = validate_syntax(tmp_path)
        fabric_fails = [
            r
            for r in results
            if not r.passed and r.file_path and "fabric.yaml" in r.file_path
        ]
        assert fabric_fails

    def test_invalid_device_name(self, tmp_path: Path, base_dir: Path) -> None:
        """Device names must match the lowercase alphanumeric pattern."""
        _mirror_data(base_dir, tmp_path)
        _patch_yaml(
            tmp_path / "data" / "topology.yaml",
            lambda d: d["devices"].__getitem__(0).__setitem__("name", "SPINE-1!!"),
        )

        results = validate_syntax(tmp_path)
        topo_fails = [
            r
            for r in results
            if not r.passed and r.file_path and "topology.yaml" in r.file_path
        ]
        assert topo_fails

    def test_dead_interval_less_than_hello(
        self, tmp_path: Path, base_dir: Path
    ) -> None:
        """OSPF dead interval < hello should fail the timer validator."""
        _mirror_data(base_dir, tmp_path)
        _patch_yaml(
            tmp_path / "data" / "underlay.yaml",
            lambda d: d["ospf"]["timers"].__setitem__("dead", 5),
        )

        results = validate_syntax(tmp_path)
        underlay_fails = [
            r
            for r in results
            if not r.passed and r.file_path and "underlay.yaml" in r.file_path
        ]
        assert underlay_fails

    def test_vlan_out_of_range(self, tmp_path: Path, base_dir: Path) -> None:
        """VLAN IDs outside 1-4094 should fail."""
        _mirror_data(base_dir, tmp_path)
        _patch_yaml(
            tmp_path / "data" / "services" / "interfaces.yaml",
            lambda d: d["interfaces"].__getitem__(0).__setitem__("vlans", [9999]),
        )

        results = validate_syntax(tmp_path)
        iface_fails = [
            r
            for r in results
            if not r.passed and r.file_path and "interfaces.yaml" in r.file_path
        ]
        assert iface_fails


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mirror_data(src: Path, dst: Path) -> None:
    """Copy the data directory so individual files can be modified."""
    shutil.copytree(src / "data", dst / "data")


def _patch_yaml(path: Path, mutate_fn: object) -> None:
    """Load a YAML file, apply a mutation function, and write it back."""
    with open(path) as f:
        data = yaml.safe_load(f)
    mutate_fn(data)  # type: ignore[operator]
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)
