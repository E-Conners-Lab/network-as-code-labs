"""Operational state tests.

These tests verify that the network is not just configured correctly but
actually working. A device can have the right BGP config but the session
might be stuck in Connect because the underlay is broken. Configuration
verification would pass; these tests would catch it.

This is the "working correctly" layer.
"""

from __future__ import annotations

import re


from schemas.models import DeviceRole


class TestOSPFState:
    """Verify OSPF adjacencies are formed and in Full state."""

    def test_ospf_neighbor_count(self, fabric_devices, topology, underlay) -> None:
        """Each device should have the expected number of OSPF neighbors."""
        # Count expected neighbors per device from the link data
        expected: dict[str, int] = {}
        for device in topology.devices:
            count = 0
            for link in underlay.links:
                if link.a_device == device.name or link.b_device == device.name:
                    count += 1
            expected[device.name] = count

        for conn in fabric_devices:
            output = conn.vtysh("show ip ospf neighbor")
            full_count = output.count("Full")
            exp = expected[conn.name]
            assert full_count == exp, (
                f"{conn.name}: expected {exp} OSPF neighbors in Full state, "
                f"found {full_count}"
            )

    def test_all_ospf_neighbors_full(self, fabric_devices) -> None:
        """No OSPF neighbor should be in a non-Full state."""
        for conn in fabric_devices:
            output = conn.vtysh("show ip ospf neighbor")
            for line in output.splitlines():
                if re.match(r"\d+\.\d+\.\d+\.\d+", line.strip()):
                    assert "Full" in line, (
                        f"{conn.name}: OSPF neighbor not in Full state: {line.strip()}"
                    )


class TestBGPState:
    """Verify BGP sessions are established."""

    def test_bgp_peers_established(self, fabric_devices, topology, overlay) -> None:
        """All BGP peers should be in Established state."""
        rr_names = {rr.device for rr in overlay.route_reflectors}

        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show bgp summary")

            if device.name in rr_names:
                expected_peers = len(topology.devices) - 1
            else:
                expected_peers = len(rr_names)

            # Count established peers (lines with time format like 00:05:30)
            established = 0
            for line in output.splitlines():
                if re.search(r"\d+\.\d+\.\d+\.\d+", line):
                    # Established peers show uptime; non-established show state name
                    if re.search(r"\d+:\d+:\d+", line):
                        established += 1

            assert established >= expected_peers, (
                f"{device.name}: expected {expected_peers} BGP peers Established, "
                f"found {established}"
            )

    def test_no_bgp_peers_in_connect(self, fabric_devices) -> None:
        """No BGP peer should be stuck in Connect or Active state."""
        for conn in fabric_devices:
            output = conn.vtysh("show bgp summary")
            for line in output.splitlines():
                if re.search(r"\d+\.\d+\.\d+\.\d+", line):
                    assert "Connect" not in line and "Active" not in line, (
                        f"{conn.name}: BGP peer not established: {line.strip()}"
                    )

    def test_evpn_address_family_active(self, fabric_devices) -> None:
        """The L2VPN EVPN address family should be present in BGP summary."""
        for conn in fabric_devices:
            output = conn.vtysh("show bgp summary")
            assert "L2VPN EVPN" in output, (
                f"{conn.name}: L2VPN EVPN address family not active"
            )


class TestRoutePresence:
    """Verify that expected routes are in the routing table."""

    def test_all_loopbacks_reachable(self, fabric_devices, topology) -> None:
        """Every device should have OSPF routes to all other loopbacks."""
        loopbacks = {d.name: str(d.loopback) for d in topology.devices}

        for conn in fabric_devices:
            output = conn.vtysh("show ip route")
            for peer_name, peer_lo in loopbacks.items():
                if peer_name == conn.name:
                    continue
                peer_ip = peer_lo.split("/")[0]
                assert peer_ip in output, (
                    f"{conn.name}: loopback {peer_ip} ({peer_name}) "
                    f"not in routing table"
                )

    def test_ospf_routes_present(self, fabric_devices) -> None:
        """Each device should have at least one OSPF-learned route."""
        for conn in fabric_devices:
            output = conn.vtysh("show ip route")
            assert "O>" in output or "O " in output, (
                f"{conn.name}: no OSPF routes in routing table"
            )


class TestPingMesh:
    """Verify loopback-to-loopback reachability across the fabric."""

    def test_spine_to_leaf_ping(self, device_map, topology) -> None:
        """Spines should be able to ping all leaf loopbacks."""
        spines = [d for d in topology.devices if d.role == DeviceRole.SPINE]
        leafs = [d for d in topology.devices if d.role == DeviceRole.LEAF]

        for spine in spines:
            conn = device_map[spine.name]
            for leaf in leafs:
                result = conn.exec(f"ping -c 1 -W 2 {leaf.loopback.ip}")
                assert "1 packets received" in result or "1 received" in result, (
                    f"{spine.name} cannot ping {leaf.name} ({leaf.loopback.ip})"
                )

    def test_spine_to_border_ping(self, device_map, topology) -> None:
        """Spines should be able to ping all border loopbacks."""
        spines = [d for d in topology.devices if d.role == DeviceRole.SPINE]
        borders = [d for d in topology.devices if d.role == DeviceRole.BORDER_LEAF]

        for spine in spines:
            conn = device_map[spine.name]
            for border in borders:
                result = conn.exec(f"ping -c 1 -W 2 {border.loopback.ip}")
                assert "1 packets received" in result or "1 received" in result, (
                    f"{spine.name} cannot ping {border.name} ({border.loopback.ip})"
                )

    def test_leaf_to_leaf_ping(self, device_map, topology) -> None:
        """Leafs should be able to ping each other through the spines."""
        leafs = [d for d in topology.devices if d.role == DeviceRole.LEAF]

        for i, src in enumerate(leafs):
            for dst in leafs[i + 1 :]:
                conn = device_map[src.name]
                result = conn.exec(f"ping -c 1 -W 2 {dst.loopback.ip}")
                assert "1 packets received" in result or "1 received" in result, (
                    f"{src.name} cannot ping {dst.name} ({dst.loopback.ip})"
                )
