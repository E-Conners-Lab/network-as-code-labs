"""Tests for the semantic validation layer.

Semantic validation checks cross-file logical consistency. These tests
verify each of the 16 semantic rules against the current data model (all
should pass) and then test selected rules with intentionally broken data
to confirm they catch the expected errors.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from schemas.models import (
    Device,
    DeviceRole,
    InterfaceAssignment,
    InterfaceMode,
    NetworkSegment,
    P2PLink,
)
from validators.semantic_validator import validate_semantic


class TestSemanticValidationPassing:
    """Verify that the current data model passes all semantic checks."""

    def test_all_rules_pass(self, parsed_models: dict[str, Any]) -> None:
        results = validate_semantic(parsed_models)
        failed = [r for r in results if not r.passed]
        assert not failed, f"Semantic failures: {[(r.rule_id, r.message) for r in failed]}"

    def test_all_16_rules_checked(self, parsed_models: dict[str, Any]) -> None:
        results = validate_semantic(parsed_models)
        rule_ids = {r.rule_id for r in results}
        expected = {f"SEM-{i:02d}" for i in range(1, 17)}
        assert expected.issubset(rule_ids), f"Missing rules: {expected - rule_ids}"


class TestSemanticValidationFailing:
    """Verify that semantic rules catch specific logical errors."""

    def test_sem01_link_references_unknown_device(self, parsed_models: dict[str, Any]) -> None:
        """SEM-01: A link referencing a device not in topology should fail."""
        models = deepcopy(parsed_models)
        bad_link = P2PLink(
            name="ghost-link",
            a_device="nonexistent",
            a_interface="eth1",
            a_ip="10.0.1.100/31",
            b_device="leaf1",
            b_interface="eth99",
            b_ip="10.0.1.101/31",
        )
        models["underlay"].links.append(bad_link)

        results = validate_semantic(models)
        sem01 = [r for r in results if r.rule_id == "SEM-01"]
        assert any(not r.passed for r in sem01)

    def test_sem04_network_references_undefined_vrf(self, parsed_models: dict[str, Any]) -> None:
        """SEM-04: A network referencing a VRF not in vrfs.yaml should fail."""
        models = deepcopy(parsed_models)
        bad_network = NetworkSegment(
            name="phantom-net",
            vni=99999,
            vlan_id=999,
            subnet="10.99.0.0/24",
            gateway="10.99.0.1",
            vrf="STAGING",
            description="This VRF does not exist",
        )
        models["networks"].networks.append(bad_network)

        results = validate_semantic(models)
        sem04 = [r for r in results if r.rule_id == "SEM-04"]
        assert any(not r.passed for r in sem04)

    def test_sem07_interface_on_spine(self, parsed_models: dict[str, Any]) -> None:
        """SEM-07: Host-facing interface on a spine should fail."""
        models = deepcopy(parsed_models)
        bad_iface = InterfaceAssignment(
            device="spine1",
            interface="eth99",
            mode=InterfaceMode.ACCESS,
            vlans=[10],
            description="Should not be on a spine",
        )
        models["interfaces"].interfaces.append(bad_iface)

        results = validate_semantic(models)
        sem07 = [r for r in results if r.rule_id == "SEM-07"]
        assert any(not r.passed for r in sem07)

    def test_sem13_incomplete_mesh(self, parsed_models: dict[str, Any]) -> None:
        """SEM-13: Removing a spine-leaf link should break the full mesh check."""
        models = deepcopy(parsed_models)
        # Remove the last link (spine2-to-border2)
        models["underlay"].links.pop()

        results = validate_semantic(models)
        sem13 = [r for r in results if r.rule_id == "SEM-13"]
        assert any(not r.passed for r in sem13)

    def test_sem14_spine_to_spine_link(self, parsed_models: dict[str, Any]) -> None:
        """SEM-14: A direct spine-to-spine link should fail."""
        models = deepcopy(parsed_models)
        bad_link = P2PLink(
            name="spine-to-spine",
            a_device="spine1",
            a_interface="eth99",
            a_ip="10.0.1.200/31",
            b_device="spine2",
            b_interface="eth99",
            b_ip="10.0.1.201/31",
        )
        models["underlay"].links.append(bad_link)

        results = validate_semantic(models)
        sem14 = [r for r in results if r.rule_id == "SEM-14"]
        assert any(not r.passed for r in sem14)

    def test_sem16_vrf_with_no_networks(self, parsed_models: dict[str, Any]) -> None:
        """SEM-16: A VRF with zero network segments should fail."""
        models = deepcopy(parsed_models)
        # Remove all MANAGEMENT networks
        models["networks"].networks = [
            n for n in models["networks"].networks if n.vrf != "MANAGEMENT"
        ]

        results = validate_semantic(models)
        sem16 = [r for r in results if r.rule_id == "SEM-16"]
        assert any(not r.passed for r in sem16)
