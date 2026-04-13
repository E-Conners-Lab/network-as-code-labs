"""MCP server exposing Network as Code tools.

This server wraps the existing NaC scripts and modules as MCP tools,
making them callable by any MCP client (Claude Code, custom agents,
or any application that speaks the Model Context Protocol).

Each tool maps to a script or module we built in earlier labs:
  - fabric_status  -> scripts.fabric_status
  - validate       -> validate.py
  - detect_drift   -> drift.detect
  - generate_configs -> generators.python.render
  - deploy_configs -> deploy.scrapli_deploy
  - ping_mesh      -> scripts.ping_mesh
  - route_table    -> scripts.route_table
  - backup_configs -> scripts.backup_configs

Usage:
    uv run python -m agent.mcp_server

    To use with Claude Code, add to your MCP config:
    {
      "mcpServers": {
        "nac-fabric": {
          "command": "uv",
          "args": ["run", "python", "-m", "agent.mcp_server"],
          "cwd": "/path/to/network-as-code-labs"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BASE_DIR = Path(__file__).resolve().parent.parent


def _run_script(module: str, args: list[str] | None = None) -> str:
    """Run a Python module and return its output."""
    cmd = [sys.executable, "-m", module]
    if args:
        cmd.extend(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=120,
    )
    output = result.stdout
    if result.returncode != 0 and result.stderr:
        output += f"\n\nSTDERR:\n{result.stderr}"
    return output.strip() or "(no output)"


server = Server("nac-fabric")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the list of available NaC tools."""
    return [
        Tool(
            name="fabric_status",
            description=(
                "Get the current health and status of the spine-leaf fabric. "
                "Shows OSPF neighbor state, BGP peer state, interface status, "
                "and route counts for all 6 devices."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "boolean",
                        "description": "Include per-neighbor and per-peer details",
                        "default": False,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": "Output format",
                        "default": "json",
                    },
                },
            },
        ),
        Tool(
            name="validate",
            description=(
                "Run the four-layer validation pipeline against the YAML data model. "
                "Checks format, syntax, semantic, and compliance rules. "
                "Returns pass/fail for each of 39 checks."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="detect_drift",
            description=(
                "Compare intended configs against running device configs to detect "
                "configuration drift. Returns which devices have drifted and what changed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "Check a specific device (optional, checks all if omitted)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
        ),
        Tool(
            name="generate_configs",
            description=(
                "Generate FRR device configurations from the validated data model "
                "using the Jinja2 render engine. Produces one .conf file per device."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="deploy_configs",
            description=(
                "Deploy generated configs to all ContainerLab FRR devices. "
                "Loads configs via vtysh without restarting FRR."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": "Validate without actually deploying",
                        "default": True,
                    },
                },
            },
        ),
        Tool(
            name="ping_mesh",
            description=(
                "Run a loopback-to-loopback ping test across all device pairs "
                "to verify full mesh reachability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
        ),
        Tool(
            name="route_table",
            description=(
                "Show the routing table for one or all devices, optionally "
                "filtered by protocol (ospf, bgp, connected)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "Show routes for a specific device",
                    },
                    "protocol": {
                        "type": "string",
                        "description": "Filter by protocol (ospf, bgp, connected)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
        ),
        Tool(
            name="run_tests",
            description=(
                "Run the post-change validation test suite (31 tests) that checks "
                "config verification, operational state, and device health."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a NaC tool and return the result."""
    try:
        if name == "fabric_status":
            fmt = arguments.get("format", "json")
            args = ["--json"] if fmt == "json" else []
            if arguments.get("detail"):
                args.append("--detail")
            output = _run_script("scripts.fabric_status", args)

        elif name == "validate":
            output = _run_script("validate")

        elif name == "detect_drift":
            args = []
            if arguments.get("device"):
                args.extend(["--device", arguments["device"]])
            if arguments.get("format", "json") == "json":
                args.append("--json")
            output = _run_script("drift.detect", args)

        elif name == "generate_configs":
            output = _run_script("generators.python.render")

        elif name == "deploy_configs":
            args = []
            if arguments.get("dry_run", True):
                args.append("--dry-run")
            output = _run_script("deploy.scrapli_deploy", args)

        elif name == "ping_mesh":
            fmt = arguments.get("format", "json")
            args = ["--json"] if fmt == "json" else []
            output = _run_script("scripts.ping_mesh", args)

        elif name == "route_table":
            args = []
            if arguments.get("device"):
                args.extend(["--device", arguments["device"]])
            if arguments.get("protocol"):
                args.extend(["--protocol", arguments["protocol"]])
            if arguments.get("format", "json") == "json":
                args.append("--json")
            output = _run_script("scripts.route_table", args)

        elif name == "run_tests":
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/post_change/",
                    "-v",
                    "--tb=short",
                ],
                capture_output=True,
                text=True,
                cwd=str(BASE_DIR),
                timeout=120,
            )
            output = result.stdout + result.stderr

        else:
            output = f"Unknown tool: {name}"

    except subprocess.TimeoutExpired:
        output = f"Tool '{name}' timed out after 120 seconds"
    except Exception as exc:
        output = f"Error running '{name}': {exc}"

    return [TextContent(type="text", text=output)]


async def main() -> None:
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
