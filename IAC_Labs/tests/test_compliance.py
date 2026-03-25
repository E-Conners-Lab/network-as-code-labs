"""Tests for the compliance validation layer.

Compliance validation enforces organizational policy. These tests verify
each of the 7 compliance rules against the current data model and then
test selected rules with policy-violating configurations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from validators.compliance_validator import validate_compliance


class TestComplianceValidationPassing:
    """Verify that the current data model meets all compliance policies."""

    def test_all_rules_pass(self, parsed_models: dict[str, Any]) -> None:
        results = validate_compliance(parsed_models)
        failed = [r for r in results if not r.passed]
        assert not failed, f"Compliance failures: {[(r.rule_id, r.message) for r in failed]}"

    def test_all_7_rules_checked(self, parsed_models: dict[str, Any]) -> None:
        results = validate_compliance(parsed_models)
        rule_ids = {r.rule_id for r in results}
        expected = {f"CMP-{i:02d}" for i in range(1, 8)}
        assert expected.issubset(rule_ids), f"Missing rules: {expected - rule_ids}"


class TestComplianceValidationFailing:
    """Verify that compliance rules catch policy violations."""

    def test_cmp01_non_jumbo_mtu(self, parsed_models: dict[str, Any]) -> None:
        """CMP-01: Fabric MTU below 9000 violates jumbo frame policy."""
        models = deepcopy(parsed_models)
        models["defaults"].fabric_link_mtu = 1500

        results = validate_compliance(models)
        cmp01 = [r for r in results if r.rule_id == "CMP-01"]
        assert any(not r.passed for r in cmp01)

    def test_cmp02_non_slash31_p2p(self, parsed_models: dict[str, Any]) -> None:
        """CMP-02: P2P links not using /31 subnets violate policy."""
        models = deepcopy(parsed_models)
        link = models["underlay"].links[0]
        # Widen from /31 to /30
        from ipaddress import IPv4Interface
        object.__setattr__(link, "a_ip", IPv4Interface("10.0.1.0/30"))
        object.__setattr__(link, "b_ip", IPv4Interface("10.0.1.1/30"))

        results = validate_compliance(models)
        cmp02 = [r for r in results if r.rule_id == "CMP-02"]
        assert any(not r.passed for r in cmp02)

    def test_cmp03_wrong_description_prefix(self, parsed_models: dict[str, Any]) -> None:
        """CMP-03: Non-standard description prefix violates policy."""
        models = deepcopy(parsed_models)
        models["defaults"].description_prefix = "CUSTOM-Prefix"

        results = validate_compliance(models)
        cmp03 = [r for r in results if r.rule_id == "CMP-03"]
        assert any(not r.passed for r in cmp03)

    def test_cmp04_arp_suppression_disabled(self, parsed_models: dict[str, Any]) -> None:
        """CMP-04: ARP suppression disabled violates policy."""
        models = deepcopy(parsed_models)
        models["defaults"].arp_suppression = False

        results = validate_compliance(models)
        cmp04 = [r for r in results if r.rule_id == "CMP-04"]
        assert any(not r.passed for r in cmp04)

    def test_cmp05_rr_cluster_id_mismatch(self, parsed_models: dict[str, Any]) -> None:
        """CMP-05: RR cluster ID not matching loopback violates policy."""
        models = deepcopy(parsed_models)
        from ipaddress import IPv4Address
        rr = models["overlay"].route_reflectors[0]
        object.__setattr__(rr, "cluster_id", IPv4Address("1.2.3.4"))

        results = validate_compliance(models)
        cmp05 = [r for r in results if r.rule_id == "CMP-05"]
        assert any(not r.passed for r in cmp05)

    def test_cmp07_non_standard_mgmt_mtu(self, parsed_models: dict[str, Any]) -> None:
        """CMP-07: Management MTU != 1500 violates policy."""
        models = deepcopy(parsed_models)
        models["defaults"].management_mtu = 9000

        results = validate_compliance(models)
        cmp07 = [r for r in results if r.rule_id == "CMP-07"]
        assert any(not r.passed for r in cmp07)
