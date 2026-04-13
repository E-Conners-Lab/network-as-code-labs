"""Configuration verification tests.

These tests compare the intended configuration (from the data model)
against what is actually running on each device. A passing test means
the device is configured as intended. A failure means something is
different -- either the deployment did not fully apply, or someone
changed something manually.

This is the "configured correctly" layer. It does not check whether
the protocols are actually working, only that the right commands are
present in the running config.
"""

from __future__ import annotations


class TestHostnames:
    """Verify that each device has the correct hostname."""

    def test_hostnames_match_topology(self, fabric_devices, topology) -> None:
        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show running-config")
            assert f"hostname {device.name}" in output, (
                f"{device.name}: hostname not found in running config"
            )


class TestInterfaceAddressing:
    """Verify that interfaces have the correct IP addresses."""

    def test_loopback_addresses(self, fabric_devices, topology) -> None:
        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show running-config")
            expected_ip = str(device.loopback)
            assert expected_ip in output, (
                f"{device.name}: loopback {expected_ip} not in running config"
            )

    def test_fabric_link_addresses(self, fabric_devices, topology, underlay) -> None:
        for link in underlay.links:
            # Check A-side
            conn_a = next(d for d in fabric_devices if d.name == link.a_device)
            output_a = conn_a.vtysh("show running-config")
            assert str(link.a_ip) in output_a, (
                f"{link.a_device}: link IP {link.a_ip} not in running config"
            )

            # Check B-side
            conn_b = next(d for d in fabric_devices if d.name == link.b_device)
            output_b = conn_b.vtysh("show running-config")
            assert str(link.b_ip) in output_b, (
                f"{link.b_device}: link IP {link.b_ip} not in running config"
            )


class TestOSPFConfig:
    """Verify that OSPF is configured correctly on each device."""

    def test_ospf_router_id(self, fabric_devices, topology) -> None:
        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show running-config")
            expected_rid = str(device.loopback.ip)
            assert f"ospf router-id {expected_rid}" in output, (
                f"{device.name}: OSPF router-id {expected_rid} not configured"
            )

    def test_ospf_on_fabric_interfaces(self, fabric_devices, underlay) -> None:
        for link in underlay.links:
            conn = next(d for d in fabric_devices if d.name == link.a_device)
            output = conn.vtysh("show running-config")
            assert "ip ospf area" in output, (
                f"{link.a_device}: no OSPF area config on fabric interfaces"
            )

    def test_ospf_point_to_point(self, fabric_devices, underlay) -> None:
        for link in underlay.links:
            conn = next(d for d in fabric_devices if d.name == link.a_device)
            output = conn.vtysh("show running-config")
            assert "ip ospf network point-to-point" in output, (
                f"{link.a_device}: OSPF point-to-point not configured"
            )


class TestBGPConfig:
    """Verify that BGP is configured correctly on each device."""

    def test_bgp_asn(self, fabric_devices, fabric) -> None:
        for conn in fabric_devices:
            output = conn.vtysh("show running-config")
            assert f"router bgp {fabric.asn}" in output, (
                f"{conn.name}: BGP ASN {fabric.asn} not configured"
            )

    def test_bgp_router_id(self, fabric_devices, topology) -> None:
        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show running-config")
            expected_rid = str(device.loopback.ip)
            assert f"bgp router-id {expected_rid}" in output, (
                f"{device.name}: BGP router-id {expected_rid} not configured"
            )

    def test_rr_cluster_id(self, fabric_devices, topology, overlay) -> None:
        for rr in overlay.route_reflectors:
            conn = next(d for d in fabric_devices if d.name == rr.device)
            output = conn.vtysh("show running-config")
            assert f"bgp cluster-id {rr.cluster_id}" in output, (
                f"{rr.device}: cluster-id {rr.cluster_id} not configured"
            )

    def test_rr_client_designation(self, fabric_devices, topology, overlay) -> None:
        rr_names = {rr.device for rr in overlay.route_reflectors}
        non_rr = [d for d in topology.devices if d.name not in rr_names]

        for rr_name in rr_names:
            conn = next(d for d in fabric_devices if d.name == rr_name)
            output = conn.vtysh("show running-config")

            for client in non_rr:
                expected = f"neighbor {client.loopback.ip} route-reflector-client"
                assert "route-reflector-client" in output, (
                    f"{rr_name}: route-reflector-client not configured for {client.name}"
                )

    def test_bgp_neighbor_count(self, fabric_devices, topology, overlay) -> None:
        rr_names = {rr.device for rr in overlay.route_reflectors}

        for device in topology.devices:
            conn = next(d for d in fabric_devices if d.name == device.name)
            output = conn.vtysh("show running-config")

            if device.name in rr_names:
                # RR peers with everyone else
                expected_count = len(topology.devices) - 1
            else:
                # Non-RR peers only with RRs
                expected_count = len(rr_names)

            actual_count = output.count("remote-as")
            assert actual_count >= expected_count, (
                f"{device.name}: expected >= {expected_count} BGP neighbors, "
                f"found {actual_count}"
            )
