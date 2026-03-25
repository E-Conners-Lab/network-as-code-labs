"""Shared fixtures for post-change validation tests.

These fixtures provide access to the running ContainerLab devices and
the validated data model. Tests in this directory are designed to run
after a deployment and verify that the network is operating correctly.

Usage:
    uv run pytest tests/post_change/ -v
    uv run pytest tests/post_change/ -v --html=reports/post-change.html --self-contained-html
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from validators import parse_all_files

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLAB_PREFIX = "clab-nac-spine-leaf"


@dataclass
class DeviceConnection:
    """Helper for running commands on a ContainerLab device."""

    name: str
    container: str

    def exec(self, command: str) -> str:
        """Run a command inside the container and return stdout."""
        result = subprocess.run(
            ["docker", "exec", self.container, "sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip()

    def vtysh(self, command: str) -> str:
        """Run a vtysh command and return the output."""
        return self.exec(f"vtysh -c '{command}' 2>/dev/null")


@pytest.fixture(scope="session")
def base_dir() -> Path:
    """Return the project root directory."""
    return BASE_DIR


@pytest.fixture(scope="session")
def parsed_models(base_dir: Path) -> dict[str, Any]:
    """Load and validate the data model."""
    parsed, errors = parse_all_files(base_dir)
    if errors:
        messages = [f"  {e.rule_id}: {e.message}" for e in errors]
        pytest.fail("Data model errors:\n" + "\n".join(messages))
    return parsed


@pytest.fixture(scope="session")
def fabric_devices(parsed_models: dict[str, Any]) -> list[DeviceConnection]:
    """Return a DeviceConnection for each device in the topology."""
    topology = parsed_models["topology"]
    return [
        DeviceConnection(
            name=device.name,
            container=f"{CLAB_PREFIX}-{device.name}",
        )
        for device in topology.devices
    ]


@pytest.fixture(scope="session")
def device_map(fabric_devices: list[DeviceConnection]) -> dict[str, DeviceConnection]:
    """Return a dict mapping device name to its connection."""
    return {d.name: d for d in fabric_devices}


@pytest.fixture(scope="session")
def topology(parsed_models: dict[str, Any]):
    """Return the validated TopologyModel."""
    return parsed_models["topology"]


@pytest.fixture(scope="session")
def underlay(parsed_models: dict[str, Any]):
    """Return the validated UnderlayModel."""
    return parsed_models["underlay"]


@pytest.fixture(scope="session")
def overlay(parsed_models: dict[str, Any]):
    """Return the validated OverlayModel."""
    return parsed_models["overlay"]


@pytest.fixture(scope="session")
def fabric(parsed_models: dict[str, Any]):
    """Return the validated FabricConfig."""
    return parsed_models["fabric"]
