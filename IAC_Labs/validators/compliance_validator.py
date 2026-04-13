"""Compliance validation layer -- organizational policy enforcement.

Semantic validation checks logical correctness. Compliance validation
checks whether the logically correct configuration also meets your
organization's standards. These are the rules that encode tribal
knowledge: "we always use jumbo frames on fabric links," "P2P subnets
are always /31s," "every managed interface gets the standard description
prefix." None of these are technically required for the network to
function, but violating them means the network does not match the
organization's operational expectations.

Rule IDs:
    CMP-01  Fabric links use jumbo MTU (>= 9000)
    CMP-02  P2P links use /31 subnets
    CMP-03  Description prefix matches the standard
    CMP-04  ARP suppression is enabled
    CMP-05  RR cluster IDs match device loopback addresses
    CMP-06  Default BGP holdtime >= 3x keepalive
    CMP-07  Management MTU is standard (1500)
"""

from __future__ import annotations

from typing import Any

from schemas.models import (
    DefaultsModel,
    FabricConfig,
    OverlayModel,
    TopologyModel,
    UnderlayModel,
)
from validators import ValidationLevel, ValidationResult

_LEVEL = ValidationLevel.COMPLIANCE


def _pass(rule_id: str, message: str) -> ValidationResult:
    return ValidationResult(level=_LEVEL, rule_id=rule_id, message=message, passed=True)


def _fail(
    rule_id: str, message: str, details: list[str] | None = None
) -> ValidationResult:
    return ValidationResult(
        level=_LEVEL,
        rule_id=rule_id,
        message=message,
        passed=False,
        details=details or [],
    )


def validate_compliance(parsed: dict[str, Any]) -> list[ValidationResult]:
    """Run all compliance checks against the parsed and schema-validated models.

    Expects the same dict that semantic validation uses. Missing
    components are skipped.
    """
    required_keys = {"fabric", "topology", "underlay", "overlay", "defaults"}
    if not required_keys.issubset(parsed.keys()):
        missing = required_keys - parsed.keys()
        return [
            _fail(
                "CMP-00", f"Cannot run compliance checks, missing components: {missing}"
            )
        ]

    fabric: FabricConfig = parsed["fabric"]
    topology: TopologyModel = parsed["topology"]
    underlay: UnderlayModel = parsed["underlay"]
    overlay: OverlayModel = parsed["overlay"]
    defaults: DefaultsModel = parsed["defaults"]

    results: list[ValidationResult] = []

    results.extend(_check_jumbo_mtu(defaults))
    results.extend(_check_p2p_slash31(underlay))
    results.extend(_check_description_prefix(defaults))
    results.extend(_check_arp_suppression(defaults))
    results.extend(_check_rr_cluster_ids(topology, overlay))
    results.extend(_check_bgp_timers(defaults))
    results.extend(_check_management_mtu(defaults))

    return results


# ---------------------------------------------------------------------------
# CMP-01: Fabric links use jumbo MTU (>= 9000)
# ---------------------------------------------------------------------------


def _check_jumbo_mtu(defaults: DefaultsModel) -> list[ValidationResult]:
    if defaults.fabric_link_mtu < 9000:
        return [
            _fail(
                "CMP-01",
                f"Fabric link MTU is {defaults.fabric_link_mtu}, policy requires >= 9000",
            )
        ]
    return [_pass("CMP-01", f"Fabric link MTU is {defaults.fabric_link_mtu} (jumbo)")]


# ---------------------------------------------------------------------------
# CMP-02: P2P links use /31 subnets
# ---------------------------------------------------------------------------


def _check_p2p_slash31(underlay: UnderlayModel) -> list[ValidationResult]:
    non_31: list[str] = []
    for link in underlay.links:
        prefix_len = link.a_ip.network.prefixlen
        if prefix_len != 31:
            non_31.append(f"Link '{link.name}': /{prefix_len}")
    if non_31:
        return [_fail("CMP-02", "P2P links must use /31 subnets per policy", non_31)]
    return [_pass("CMP-02", "All P2P links use /31 subnets")]


# ---------------------------------------------------------------------------
# CMP-03: Description prefix matches standard
# ---------------------------------------------------------------------------

_REQUIRED_PREFIX = "NaC-Managed"


def _check_description_prefix(defaults: DefaultsModel) -> list[ValidationResult]:
    if defaults.description_prefix != _REQUIRED_PREFIX:
        return [
            _fail(
                "CMP-03",
                f"Description prefix is '{defaults.description_prefix}', "
                f"policy requires '{_REQUIRED_PREFIX}'",
            )
        ]
    return [
        _pass("CMP-03", f"Description prefix matches standard: '{_REQUIRED_PREFIX}'")
    ]


# ---------------------------------------------------------------------------
# CMP-04: ARP suppression enabled
# ---------------------------------------------------------------------------


def _check_arp_suppression(defaults: DefaultsModel) -> list[ValidationResult]:
    if not defaults.arp_suppression:
        return [_fail("CMP-04", "ARP suppression must be enabled per policy")]
    return [_pass("CMP-04", "ARP suppression is enabled")]


# ---------------------------------------------------------------------------
# CMP-05: RR cluster IDs match device loopback addresses
# ---------------------------------------------------------------------------


def _check_rr_cluster_ids(
    topology: TopologyModel, overlay: OverlayModel
) -> list[ValidationResult]:
    loopbacks = {d.name: d.loopback.ip for d in topology.devices}
    mismatches: list[str] = []

    for rr in overlay.route_reflectors:
        expected = loopbacks.get(rr.device)
        if expected is not None and rr.cluster_id != expected:
            mismatches.append(
                f"'{rr.device}': cluster_id {rr.cluster_id}, loopback {expected}"
            )

    if mismatches:
        return [
            _fail("CMP-05", "RR cluster IDs must match device loopbacks", mismatches)
        ]
    return [_pass("CMP-05", "All RR cluster IDs match device loopbacks")]


# ---------------------------------------------------------------------------
# CMP-06: Default BGP holdtime >= 3x keepalive
# ---------------------------------------------------------------------------


def _check_bgp_timers(defaults: DefaultsModel) -> list[ValidationResult]:
    minimum = defaults.timers.bgp_keepalive * 3
    if defaults.timers.bgp_holdtime < minimum:
        return [
            _fail(
                "CMP-06",
                f"Default BGP holdtime ({defaults.timers.bgp_holdtime}) must be "
                f">= 3x keepalive ({defaults.timers.bgp_keepalive}). Minimum: {minimum}",
            )
        ]
    return [_pass("CMP-06", "Default BGP timers comply with policy")]


# ---------------------------------------------------------------------------
# CMP-07: Management MTU is standard (1500)
# ---------------------------------------------------------------------------


def _check_management_mtu(defaults: DefaultsModel) -> list[ValidationResult]:
    if defaults.management_mtu != 1500:
        return [
            _fail(
                "CMP-07",
                f"Management MTU is {defaults.management_mtu}, policy requires 1500",
            )
        ]
    return [_pass("CMP-07", "Management MTU is standard (1500)")]
