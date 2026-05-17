"""AI-assisted network operations with dual LLM support.

A lightweight assistant that uses an LLM (Claude or Ollama/Llama) to
help with network operations. It gathers context from the fabric tools
and sends structured prompts to the LLM for interpretation.

Three modes:
  - validation-assist: Explain a validation failure and suggest a fix
  - fabric-qa: Answer questions about the fabric using live data
  - drift-triage: Analyze a drift report and recommend a strategy

Two backends:
  - claude: Uses the Anthropic API (requires ANTHROPIC_API_KEY env var)
  - ollama: Uses a local Ollama instance running Llama (free, no API key)

Usage:
    uv run python -m agent.assistant validation-assist
    uv run python -m agent.assistant fabric-qa "Which device has the most routes?"
    uv run python -m agent.assistant drift-triage
    uv run python -m agent.assistant fabric-qa "Is the fabric healthy?" --backend ollama
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent

# Ollama API URL -- defaults to the Mac Mini tailnet IP; override with
# OLLAMA_URL env var to point to a different host.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.124.228.102:11434")

# ---------------------------------------------------------------------------
# LLM Backends
# ---------------------------------------------------------------------------


def _call_claude(prompt: str, system: str = "") -> str:
    """Call the Anthropic API using the anthropic SDK."""
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Set it with: export ANTHROPIC_API_KEY='your-key-here'\n"
            "Or use --backend ollama for a free local alternative."
        )

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        return f"Claude API error: {exc}"


def _detect_ollama_model() -> str:
    """Auto-detect the best available Ollama model.

    Queries the local Ollama instance for installed models and picks
    the largest one, since bigger models produce better results.
    If detection fails, falls back to a sensible default.
    """
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        response.raise_for_status()
        models = response.json().get("models", [])
        if not models:
            return "llama3.1:8b"
        # Sort by size descending, pick the largest
        models.sort(key=lambda m: m.get("size", 0), reverse=True)
        return models[0]["name"]
    except Exception:
        return "llama3.1:8b"


def _call_ollama(prompt: str, system: str = "", model: str = "") -> str:
    """Call a local Ollama instance.

    If no model is specified, auto-detects the best installed model.
    """
    if not model:
        model = _detect_ollama_model()

    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "system": system,
                "stream": False,
            },
            timeout=600.0,
        )
        response.raise_for_status()
        return response.json().get("response", "(empty response)")
    except httpx.ConnectError:
        return (
            f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}.\n"
            "Install and start Ollama:\n"
            "  curl -fsSL https://ollama.com/install.sh | sh\n"
            "  ollama pull llama3.1:8b\n"
            "  ollama serve\n"
            "\n"
            "To use a remote Ollama server (e.g., Mac with Apple Silicon):\n"
            "  export OLLAMA_URL=http://<mac-ip>:11434"
        )
    except Exception as exc:
        return f"Ollama error: {exc}"


def call_llm(
    prompt: str, system: str = "", backend: str = "claude", model: str = "llama3.1:8b"
) -> str:
    """Call the configured LLM backend."""
    if backend == "claude":
        return _call_claude(prompt, system)
    elif backend == "ollama":
        return _call_ollama(prompt, system, model)
    else:
        return f"Unknown backend: {backend}"


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------


def _run_tool(module: str, args: list[str] | None = None) -> str:
    """Run a NaC tool and return its output."""
    cmd = [sys.executable, "-m", module]
    if args:
        cmd.extend(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=60,
    )
    return result.stdout.strip()


def _get_fabric_status() -> str:
    """Get fabric status as JSON."""
    return _run_tool("scripts.fabric_status", ["--json"])


def _get_validation_output() -> str:
    """Run validation and capture output."""
    return _run_tool("validate")


def _get_drift_report() -> str:
    """Run drift detection and get JSON output."""
    return _run_tool("drift.detect", ["--json"])


def _get_route_table(device: str | None = None) -> str:
    """Get routing table as JSON."""
    args = ["--json"]
    if device:
        args.extend(["--device", device])
    return _run_tool("scripts.route_table", args)


# ---------------------------------------------------------------------------
# Assistant modes
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a network operations assistant for a spine-leaf fabric running FRR.
The fabric has 6 devices: spine1, spine2 (route reflectors), leaf1, leaf2, border1, border2.
Underlay is OSPF area 0. Overlay is iBGP with EVPN. ASN 65000.
You have access to structured data about the fabric state.
Be concise and specific. Reference device names and IPs when relevant."""


def mode_validation_assist(backend: str, model: str) -> None:
    """Run validation and ask the LLM to explain any failures."""
    print("Running validation...\n")
    output = _get_validation_output()
    print(output)
    print()

    if "failed" not in output.lower() and "FAIL" not in output:
        print("All validation checks passed. No assistance needed.")
        return

    prompt = f"""The following validation output contains failures. Explain what went wrong
in plain English and suggest the specific YAML change needed to fix it.

Validation output:
{output}
"""
    print(f"Asking {'Claude' if backend == 'claude' else 'Llama'} for help...\n")
    response = call_llm(prompt, SYSTEM_PROMPT, backend, model)
    print(response)


def mode_fabric_qa(question: str, backend: str, model: str) -> None:
    """Answer a question about the fabric using live data."""
    print("Gathering fabric data...\n")

    status = _get_fabric_status()
    routes = _get_route_table()

    prompt = f"""Answer this question about the network fabric using the data provided.

Question: {question}

Fabric status (JSON):
{status[:3000]}

Route tables (JSON, truncated):
{routes[:3000]}
"""
    print(f"Asking {'Claude' if backend == 'claude' else 'Llama'}...\n")
    response = call_llm(prompt, SYSTEM_PROMPT, backend, model)
    print(response)


def mode_drift_triage(backend: str, model: str) -> None:
    """Analyze drift and recommend a reconciliation strategy."""
    print("Running drift detection...\n")

    drift_data = _get_drift_report()

    try:
        drift_list = json.loads(drift_data)
        drifted = [d for d in drift_list if d.get("has_drift")]
    except (json.JSONDecodeError, TypeError):
        drifted = []

    if not drifted:
        print("No drift detected. All devices match intended config.")
        return

    print(f"Drift found on {len(drifted)} device(s).\n")

    prompt = f"""Analyze this configuration drift report from a spine-leaf fabric.
For each drifted device, determine:
1. Whether the drift looks intentional (emergency fix) or accidental (mistake)
2. Which reconciliation strategy to use: auto-remediate, report for review, or absorb
3. The risk level if we auto-remediate

Drift report (JSON):
{json.dumps(drifted, indent=2)[:3000]}
"""
    print(f"Asking {'Claude' if backend == 'claude' else 'Llama'} for analysis...\n")
    response = call_llm(prompt, SYSTEM_PROMPT, backend, model)
    print(response)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the AI assistant."""
    parser = argparse.ArgumentParser(description="AI-assisted network operations")
    parser.add_argument(
        "--backend",
        choices=["claude", "ollama"],
        default="claude",
        help="LLM backend to use (default: claude)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Ollama model name (auto-detects largest installed model if omitted)",
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    subparsers.add_parser("validation-assist", help="Explain validation failures")

    qa_parser = subparsers.add_parser(
        "fabric-qa", help="Ask questions about the fabric"
    )
    qa_parser.add_argument("question", help="The question to ask")

    subparsers.add_parser("drift-triage", help="Analyze drift and recommend action")

    args = parser.parse_args()

    # Auto-detect or let user pick model if using Ollama
    if args.backend == "ollama" and not args.model:
        try:
            response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            models = response.json().get("models", [])
            models.sort(key=lambda m: m.get("size", 0), reverse=True)

            if len(models) == 0:
                print("No Ollama models installed. Run: ollama pull llama3.1:8b")
                sys.exit(1)
            elif len(models) == 1:
                args.model = models[0]["name"]
            else:
                print("Available Ollama models:")
                for i, m in enumerate(models, 1):
                    size_gb = m.get("size", 0) / (1024**3)
                    print(f"  {i}. {m['name']} ({size_gb:.1f} GB)")
                print()
                choice = input(f"Select model [1-{len(models)}] (default: 1): ").strip()
                if not choice:
                    args.model = models[0]["name"]
                else:
                    idx = int(choice) - 1
                    args.model = models[idx]["name"]
        except Exception:
            args.model = _detect_ollama_model()

    print()
    backend_name = (
        "Claude API" if args.backend == "claude" else f"Ollama ({args.model})"
    )
    print(f"Backend: {backend_name}")
    print(f"Mode: {args.mode}")
    print("-" * 50)
    print()

    if args.mode == "validation-assist":
        mode_validation_assist(args.backend, args.model)
    elif args.mode == "fabric-qa":
        mode_fabric_qa(args.question, args.backend, args.model)
    elif args.mode == "drift-triage":
        mode_drift_triage(args.backend, args.model)


if __name__ == "__main__":
    main()
