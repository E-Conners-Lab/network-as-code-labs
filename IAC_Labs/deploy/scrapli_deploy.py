"""Deployment script for pushing FRR configs to ContainerLab devices.

Reads generated configuration files from the configs/ directory and pushes
them to the corresponding FRR containers using docker exec. This is the
correct approach for ContainerLab environments where the containers are on
the same host and do not run SSH daemons.

The deployment has two phases:
  1. FRR control plane: vtysh config (BGP, OSPF, VRF routing)
  2. Linux dataplane: VXLAN interfaces, bridges, SVIs (ip link/bridge)

For production deployments to real network devices with SSH access, you
would replace the docker exec calls with Scrapli SSH connections. The
script structure (async, concurrent, dry-run support) stays the same.

Usage:
    uv run python -m deploy.scrapli_deploy
    uv run python -m deploy.scrapli_deploy --configs-dir configs/ --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from schemas.models import (
    DeviceRole,
    FabricDataModel,
    NetworkSegment,
    VRFConfig,
)
from validators import parse_all_files

console = Console()

BASE_DIR = Path(__file__).resolve().parent.parent

#: ContainerLab container name prefix. Combined with the topology name
#: and device name to form the full container name.
CLAB_PREFIX = "clab-nac-spine-leaf"


@dataclass
class DeployResult:
    """Result of deploying config to a single device."""

    device: str
    success: bool
    message: str
    lines_pushed: int = 0


def _build_dataplane_commands(
    device_name: str,
    loopback_ip: IPv4Address,
    vrfs: list[VRFConfig],
    networks: list[NetworkSegment],
) -> str:
    """Build Linux networking commands for the VXLAN/EVPN dataplane.

    On a hardware EVPN switch, the ASIC handles VXLAN encap/decap natively.
    In ContainerLab with FRR, we set up the equivalent using Linux networking:
      - VRF devices for tenant isolation
      - VXLAN interfaces for overlay tunneling
      - Bridges to connect VXLAN tunnels to local segments
      - SVIs (IPs on bridges) for anycast gateway routing
    """
    cmds: list[str] = ["set -e"]

    # VRF table IDs derived from VNI (100000 -> 100, 200000 -> 200)
    for vrf in vrfs:
        table_id = vrf.vni // 1000
        cmds.append(f"# --- VRF: {vrf.name} (L3VNI {vrf.vni}) ---")

        # Create VRF device
        cmds.append(
            f"ip link add {vrf.name} type vrf table {table_id} 2>/dev/null || true"
        )
        cmds.append(f"ip link set {vrf.name} up")

        # L3VNI bridge and VXLAN for inter-subnet (Type-5) routing
        br_name = f"br-l3vni-{vrf.vni}"
        vx_name = f"vxlan{vrf.vni}"
        cmds.append(f"ip link add {br_name} type bridge 2>/dev/null || true")
        cmds.append(f"ip link set {br_name} addrgenmode none 2>/dev/null || true")
        cmds.append(f"ip link set {br_name} master {vrf.name}")
        cmds.append(f"ip link set {br_name} up")
        cmds.append(
            f"ip link add {vx_name} type vxlan id {vrf.vni} "
            f"local {loopback_ip} dstport 4789 nolearning 2>/dev/null || true"
        )
        cmds.append(f"ip link set {vx_name} master {br_name}")
        cmds.append(f"ip link set {vx_name} up")

    # Per-segment L2VNI bridges, VXLAN interfaces, and SVIs
    for net in networks:
        br_name = f"br{net.vlan_id}"
        vx_name = f"vxlan{net.vni}"
        cmds.append(
            f"# --- L2VNI: {net.name} (VNI {net.vni}, VLAN {net.vlan_id}) ---"
        )

        # Bridge for this VLAN segment
        cmds.append(f"ip link add {br_name} type bridge 2>/dev/null || true")
        cmds.append(f"ip link set {br_name} master {net.vrf}")
        cmds.append(f"ip link set {br_name} up")

        # VXLAN tunnel interface
        cmds.append(
            f"ip link add {vx_name} type vxlan id {net.vni} "
            f"local {loopback_ip} dstport 4789 nolearning 2>/dev/null || true"
        )
        cmds.append(f"ip link set {vx_name} master {br_name}")
        cmds.append(f"ip link set {vx_name} up")

        # SVI: anycast gateway on the bridge
        prefix_len = net.subnet.prefixlen
        cmds.append(
            f"ip addr add {net.gateway}/{prefix_len} dev {br_name} 2>/dev/null || true"
        )

    return "\n".join(cmds)


async def _run_docker_exec(container: str, command: str) -> tuple[int, str]:
    """Run a command inside a Docker container and return (exit_code, output)."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode() + stderr.decode()
    return proc.returncode or 0, output.strip()


async def _deploy_to_device(
    device_name: str,
    config_path: Path,
    dry_run: bool = False,
    dataplane_commands: str = "",
) -> DeployResult:
    """Deploy a configuration file to a single FRR device.

    In dry-run mode, validates the config can be read and the container
    exists. In live mode, writes the config to the container's filesystem,
    loads it through vtysh, and provisions the VXLAN dataplane via Linux
    networking commands.
    """
    if not config_path.exists():
        return DeployResult(
            device=device_name,
            success=False,
            message=f"Config file not found: {config_path}",
        )

    config_text = config_path.read_text().strip()
    config_lines = [
        line
        for line in config_text.splitlines()
        if line.strip() and not line.strip().startswith("!")
    ]

    container = f"{CLAB_PREFIX}-{device_name}"

    if dry_run:
        # Verify the container is running
        rc, output = await _run_docker_exec(container, "echo ok")
        if rc != 0 or "ok" not in output:
            return DeployResult(
                device=device_name,
                success=False,
                message=f"Container '{container}' is not running or not reachable",
            )
        return DeployResult(
            device=device_name,
            success=True,
            message=f"Dry run: {len(config_lines)} lines ready, container reachable",
            lines_pushed=len(config_lines),
        )

    try:
        # Enable ospfd and bgpd if not already enabled in the daemons file
        await _run_docker_exec(
            container,
            "sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons && "
            "sed -i 's/^bgpd=no/bgpd=yes/' /etc/frr/daemons",
        )

        # Write config and load it via vtysh. We never restart FRR because
        # that destroys the veth interfaces that ContainerLab created.
        # Instead we load the config into the already-running daemons.
        rc, output = await _run_docker_exec(
            container,
            f"cat > /tmp/frr_nac.conf << 'NACEOF'\n{config_text}\nNACEOF",
        )
        if rc != 0:
            return DeployResult(
                device=device_name,
                success=False,
                message=f"Failed to write config file: {output[:200]}",
            )

        rc, output = await _run_docker_exec(
            container,
            "vtysh -f /tmp/frr_nac.conf 2>&1",
        )

        # Save config to persistent file
        await _run_docker_exec(
            container,
            "cp /tmp/frr_nac.conf /etc/frr/frr.conf && vtysh -c 'write memory' 2>&1",
        )

        # Verify FRR is responsive
        rc_check, running = await _run_docker_exec(
            container,
            "vtysh -c 'show version' 2>&1",
        )

        # Check for errors in vtysh output
        error_lines = [
            line
            for line in output.splitlines()
            if ("error" in line.lower() or "unknown" in line.lower())
            and "no error" not in line.lower()
        ]

        if error_lines:
            return DeployResult(
                device=device_name,
                success=False,
                message=f"FRR restart warnings: {'; '.join(error_lines[:3])}",
                lines_pushed=len(config_lines),
            )

        if rc_check != 0:
            return DeployResult(
                device=device_name,
                success=False,
                message=f"FRR daemons did not start: {running[:200]}",
                lines_pushed=len(config_lines),
            )

        # Phase 2: Provision VXLAN dataplane via Linux networking
        if dataplane_commands:
            rc_dp, dp_output = await _run_docker_exec(
                container,
                f"sh -c '{dataplane_commands}'",
            )
            if rc_dp != 0:
                return DeployResult(
                    device=device_name,
                    success=False,
                    message=f"Dataplane setup failed: {dp_output[:200]}",
                    lines_pushed=len(config_lines),
                )

        msg = "Config deployed, daemons restarted"
        if dataplane_commands:
            msg += ", dataplane provisioned"

        return DeployResult(
            device=device_name,
            success=True,
            message=msg,
            lines_pushed=len(config_lines),
        )

    except Exception as exc:
        return DeployResult(
            device=device_name,
            success=False,
            message=f"Deployment failed: {exc}",
        )


async def deploy_all(
    configs_dir: Path,
    base_dir: Path,
    dry_run: bool = False,
) -> list[DeployResult]:
    """Deploy configs to all devices concurrently."""
    parsed, errors = parse_all_files(base_dir)
    if errors:
        for e in errors:
            console.print(f"  [red]FAIL[/red]  {e.rule_id}  {e.message}")
        console.print(
            "\n[bold red]Data model has errors. Fix them before deploying.[/bold red]"
        )
        sys.exit(1)

    model = FabricDataModel.model_validate(parsed)

    # Build per-device dataplane commands from the data model
    device_map = {d.name: d for d in model.topology.devices}

    tasks = []
    for device in sorted(model.topology.devices, key=lambda d: d.name):
        dp_cmds = ""

        if device.role != DeviceRole.SPINE:
            # Determine which VRFs and networks this device needs
            if device.role == DeviceRole.BORDER_LEAF:
                dev_vrfs = list(model.vrfs.vrfs)
                dev_networks = list(model.networks.networks)
            else:
                # Leaf: only VRFs/networks matching assigned VLANs
                dev_vlans: set[int] = set()
                for iface in model.interfaces.interfaces:
                    if iface.device == device.name:
                        dev_vlans.update(iface.vlans)

                vrf_names: set[str] = set()
                dev_networks = []
                for net in model.networks.networks:
                    if net.vlan_id in dev_vlans:
                        vrf_names.add(net.vrf)
                        dev_networks.append(net)

                dev_vrfs = [v for v in model.vrfs.vrfs if v.name in vrf_names]

            dp_cmds = _build_dataplane_commands(
                device.name,
                device.loopback.ip,
                dev_vrfs,
                dev_networks,
            )

        tasks.append(
            _deploy_to_device(
                device.name,
                configs_dir / f"{device.name}.conf",
                dry_run,
                dp_cmds,
            )
        )

    return await asyncio.gather(*tasks)


def _print_results(results: list[DeployResult], dry_run: bool) -> bool:
    """Print deployment results. Returns True if all succeeded."""
    table = Table(title="Dry Run Results" if dry_run else "Deployment Results")
    table.add_column("Device", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Lines", justify="right")
    table.add_column("Message")

    all_ok = True
    for r in sorted(results, key=lambda x: x.device):
        status = "[green]OK[/green]" if r.success else "[red]FAIL[/red]"
        if not r.success:
            all_ok = False
        table.add_row(r.device, status, str(r.lines_pushed), r.message)

    console.print(table)
    return all_ok


def main() -> None:
    """Run the deployment pipeline."""
    parser = argparse.ArgumentParser(
        description="Deploy FRR configs to ContainerLab devices"
    )
    parser.add_argument(
        "--configs-dir",
        type=str,
        default="configs",
        help="Directory containing generated device configs (default: configs/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configs and container reachability without deploying",
    )
    args = parser.parse_args()

    configs_dir = BASE_DIR / args.configs_dir
    mode = "DRY RUN" if args.dry_run else "LIVE DEPLOYMENT"

    console.print()
    console.print(Panel(f"[bold]Network as Code -- {mode}[/bold]", expand=False))
    console.print()

    if not configs_dir.exists():
        console.print(f"[red]Configs directory not found: {configs_dir}[/red]")
        console.print(
            "Run the config generator first: uv run python -m generators.python.render"
        )
        sys.exit(1)

    if not args.dry_run:
        console.print("[yellow]Deploying configs to live devices...[/yellow]\n")

    results = asyncio.run(deploy_all(configs_dir, BASE_DIR, args.dry_run))
    all_ok = _print_results(results, args.dry_run)

    console.print()
    if all_ok:
        succeeded = len([r for r in results if r.success])
        console.print(
            f"[bold green]{succeeded}/{len(results)} devices {'validated' if args.dry_run else 'deployed'} successfully.[/bold green]"
        )
    else:
        failed = len([r for r in results if not r.success])
        console.print(f"[bold red]{failed}/{len(results)} devices failed.[/bold red]")
        sys.exit(1)
    console.print()


if __name__ == "__main__":
    main()
