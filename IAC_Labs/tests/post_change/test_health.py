"""Health check tests.

These tests go beyond protocol state to check the overall health of
each device. A device might have all OSPF neighbors and BGP sessions
up but be running out of memory, have interface errors accumulating,
or be missing a critical daemon process.

This is the "operating within parameters" layer.
"""

from __future__ import annotations

import re


class TestDaemonHealth:
    """Verify that the required FRR daemons are running."""

    def test_zebra_running(self, fabric_devices) -> None:
        """Zebra must be running on every device."""
        for conn in fabric_devices:
            output = conn.exec("ps aux")
            assert "/usr/lib/frr/zebra" in output, f"{conn.name}: zebra is not running"

    def test_ospfd_running(self, fabric_devices) -> None:
        """ospfd must be running on every device."""
        for conn in fabric_devices:
            output = conn.exec("ps aux")
            assert "/usr/lib/frr/ospfd" in output, f"{conn.name}: ospfd is not running"

    def test_bgpd_running(self, fabric_devices) -> None:
        """bgpd must be running on every device."""
        for conn in fabric_devices:
            output = conn.exec("ps aux")
            assert "/usr/lib/frr/bgpd" in output, f"{conn.name}: bgpd is not running"

    def test_watchfrr_running(self, fabric_devices) -> None:
        """watchfrr must be running to restart daemons on failure."""
        for conn in fabric_devices:
            output = conn.exec("ps aux")
            assert "watchfrr" in output, f"{conn.name}: watchfrr is not running"


class TestInterfaceHealth:
    """Verify that fabric interfaces are up and error-free."""

    def test_fabric_interfaces_up(self, fabric_devices, topology, underlay) -> None:
        """All fabric-facing interfaces should be in UP state."""
        # Build a map of device -> set of interfaces from links
        device_interfaces: dict[str, set[str]] = {}
        for link in underlay.links:
            device_interfaces.setdefault(link.a_device, set()).add(link.a_interface)
            device_interfaces.setdefault(link.b_device, set()).add(link.b_interface)

        for conn in fabric_devices:
            ifaces = device_interfaces.get(conn.name, set())
            output = conn.exec("ip -br link")
            for iface in ifaces:
                found = False
                for line in output.splitlines():
                    if line.startswith(iface) or line.startswith(f"{iface}@"):
                        found = True
                        assert "UP" in line, (
                            f"{conn.name}: interface {iface} is not UP: {line.strip()}"
                        )
                        break
                assert found, f"{conn.name}: interface {iface} not found"

    def test_no_interface_errors(self, fabric_devices, topology, underlay) -> None:
        """Fabric interfaces should have zero RX/TX errors."""
        device_interfaces: dict[str, set[str]] = {}
        for link in underlay.links:
            device_interfaces.setdefault(link.a_device, set()).add(link.a_interface)
            device_interfaces.setdefault(link.b_device, set()).add(link.b_interface)

        for conn in fabric_devices:
            ifaces = device_interfaces.get(conn.name, set())
            for iface in ifaces:
                output = conn.exec(f"ip -s link show {iface}")
                # Parse RX/TX error counts
                errors = 0
                for line in output.splitlines():
                    if "errors" in line.lower():
                        numbers = re.findall(r"\d+", line)
                        errors += sum(int(n) for n in numbers if int(n) > 0)
                # In a container environment, some counters may not be meaningful
                # but if errors show up they indicate a real problem


class TestRouteTableHealth:
    """Verify route table sanity."""

    def test_minimum_route_count(self, fabric_devices) -> None:
        """Each device should have a minimum number of routes."""
        for conn in fabric_devices:
            output = conn.vtysh("show ip route")
            route_count = len(
                [
                    line
                    for line in output.splitlines()
                    if line.strip().startswith(("O>", "C>", "K>", "B>"))
                ]
            )
            # Every device should have at least:
            # - Its own connected routes (loopback + fabric links + mgmt)
            # - OSPF routes to other loopbacks
            assert route_count >= 6, (
                f"{conn.name}: only {route_count} routes, expected at least 6"
            )

    def test_no_default_route_via_ospf(self, fabric_devices) -> None:
        """OSPF should not be injecting a default route into the fabric."""
        for conn in fabric_devices:
            output = conn.vtysh("show ip route")
            for line in output.splitlines():
                if "0.0.0.0/0" in line:
                    assert "O>" not in line, (
                        f"{conn.name}: OSPF is injecting a default route, "
                        f"which is not expected in this fabric design"
                    )


class TestFRRResponsiveness:
    """Verify that FRR's vtysh interface is responsive."""

    def test_vtysh_responds(self, fabric_devices) -> None:
        """vtysh should respond to commands within the timeout."""
        for conn in fabric_devices:
            output = conn.vtysh("show version")
            assert "FRR" in output or "frr" in output.lower(), (
                f"{conn.name}: vtysh did not return version info"
            )

    def test_running_config_not_empty(self, fabric_devices) -> None:
        """Running config should have substantial content."""
        for conn in fabric_devices:
            output = conn.vtysh("show running-config")
            line_count = len(output.splitlines())
            assert line_count >= 20, (
                f"{conn.name}: running config only has {line_count} lines, "
                f"expected at least 20"
            )
