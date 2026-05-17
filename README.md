# Network as Code Lab Series

A 7-lab series that teaches network engineers how to manage infrastructure using code-driven workflows. Starting from a YAML data model and ending with AI-assisted operations, each lab builds on the previous one using a ContainerLab spine-leaf fabric as the working environment.

This series accompanies [The Tech-E](https://youtube.com/@TheTech-E) YouTube channel. Each lab maps to one or more video episodes where you follow along on your own hardware.

## What Makes This Different

Most network automation tutorials pick one tool and show you how to use it. This series covers multiple tooling paths because that is how real environments work. Nobody runs a pure Python shop or a pure Terraform shop. You will use Python, Ansible, and Terraform on the same data model and see when each one fits.

The labs use FRR routers in ContainerLab, which means you can run the entire environment on a single VM. No expensive licenses, no cloud accounts, no vendor-specific equipment.

## Lab Environment

A 6-node VXLAN/EVPN spine-leaf fabric running on ContainerLab:

| Device | Role | Loopback | ASN |
|--------|------|----------|-----|
| spine1 | Spine / Route Reflector | 10.0.0.1/32 | 65000 |
| spine2 | Spine / Route Reflector | 10.0.0.2/32 | 65000 |
| leaf1 | Leaf | 10.0.0.11/32 | 65000 |
| leaf2 | Leaf | 10.0.0.12/32 | 65000 |
| border1 | Border Leaf | 10.0.0.21/32 | 65000 |
| border2 | Border Leaf | 10.0.0.22/32 | 65000 |

Underlay: OSPF area 0 on all point-to-point fabric links. Overlay: iBGP with EVPN address family, spines as route reflectors.

## The Labs

### Lab 1: Defining Network Intent with a Data Model

Build a YAML-based data model that describes the entire fabric: topology, underlay routing, overlay configuration, VRFs, network segments, and interface assignments. Validate everything with Pydantic schemas. Initialize a Git repository.

**What you learn:** Single source of truth, declarative data models, schema validation, version control from day one.

**Build guide:** [docs/lab1-build-guide.md](IAC_Labs/docs/lab1-build-guide.md)

### Lab 2: Pre-Change Validation

Build a four-layer validation framework (format, syntax, semantic, compliance) with 39 checks and stable rule IDs. Integrate with pytest for test-driven validation and HTML reporting.

**What you learn:** Shift-left testing, layered validation, compliance as code, catching errors before they reach the network.

**Build guide:** [docs/lab2-build-guide.md](IAC_Labs/docs/lab2-build-guide.md)

### Lab 3: Configuration Generation from Intent

Translate the validated data model into per-device FRR configurations using three tooling paths: Python + Jinja2 (primary), Ansible (alternative), and Terraform (alternative). Demonstrate route reflector auto-peering where changing one flag in the data model rewires the entire overlay.

**What you learn:** Intent-to-device translation, template-based config generation, role-aware automation, comparing tooling approaches.

**Build guide:** [docs/lab3-build-guide.md](IAC_Labs/docs/lab3-build-guide.md)

### Lab 4: CI/CD Pipeline for Network Changes

Wire the validation and generation steps into a GitHub Actions pipeline. Feature branch pushes trigger validation, pull requests trigger config generation with diffs, and merges to main trigger deployment via Scrapli.

**What you learn:** GitOps for networking, multi-stage pipeline gates, branching strategy, automated config diffing on PRs.

**Build guide:** [docs/lab4-build-guide.md](IAC_Labs/docs/lab4-build-guide.md)

### Lab 5: Post-Change Validation and Testing

Build a test framework that validates the network after every deployment: configuration verification, operational state checks, and health tests.

**What you learn:** Difference between "deployed correctly" and "working correctly", data-model-driven test generation, pytest parametrization, HTML reporting.

**Build guide:** [docs/lab5-build-guide.md](IAC_Labs/docs/lab5-build-guide.md)

### Lab 6: Drift Detection and Reconciliation

Detect when the network's actual state drifts from the declared intent, alert on it, and reconcile using three strategies: auto-remediate, PR-based review, or absorb the change.

**What you learn:** Config normalization for meaningful comparison, scheduled drift jobs, brownfield import, reconciliation trade-offs.

**Build guide:** [docs/lab6-build-guide.md](IAC_Labs/docs/lab6-build-guide.md)

### Lab 7: AI-Assisted Network Operations

Build an MCP server exposing all NaC tools, and an AI assistant that answers network operations questions using live fabric data. Supports two LLM backends: Claude API (cloud) and Ollama/Llama 3.1 (local, free). Three modes: validation assistant, fabric Q&A, and drift triage.

**What you learn:** Model Context Protocol (MCP), connecting LLMs to infrastructure tools, running open-source models locally, dual-backend architecture.

**Build guide:** [docs/lab7-build-guide.md](IAC_Labs/docs/lab7-build-guide.md)

## Requirements

A Linux machine (or VM) with:

| Tool | Version | Purpose |
|------|---------|---------|
| Docker | 20.10+ | Container runtime for FRR nodes |
| ContainerLab | 0.62+ | Lab topology orchestration |
| Python | 3.11 - 3.13 | Automation scripts and schemas |
| uv | Latest | Python package management |
| Git | 2.x | Version control |
| Ansible | 2.15+ | Alternative config generation path (Lab 3) |
| Terraform | 1.5+ | Alternative config generation path (Lab 3) |
| Ollama | Latest | Local LLM for AI assistant (Lab 7) |

A Proxmox VM with 2 vCPUs, 4 GB RAM, and 32 GB disk runs the entire lab comfortably. See the [Lab 1 build guide](IAC_Labs/docs/lab1-build-guide.md) for step-by-step VM setup.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/E-Conners-Lab/network-as-code-labs.git
cd network-as-code-labs/IAC_Labs

# Install Python dependencies
uv sync

# Validate the data model
uv run python validate.py

# Generate FRR configs for all 6 devices
uv run python -m generators.python.render

# Deploy the ContainerLab topology
sudo containerlab deploy --topo containerlab/topology.yaml
```

## Ollama Setup (Lab 7)

Lab 7's AI assistant supports two backends. Use whichever fits your setup.

**Option A: Claude API (cloud)**

Set your Anthropic API key before running the assistant:

```bash
export ANTHROPIC_API_KEY="your-key-here"
uv run python -m agent.assistant fabric-qa "Is the fabric healthy?"
```

**Option B: Ollama (local, free)**

Install Ollama on the same machine running the labs:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
```

Then run the assistant with the Ollama backend:

```bash
uv run python -m agent.assistant --backend ollama fabric-qa "Is the fabric healthy?"
```

By default the assistant connects to Ollama at `http://localhost:11434`. If Ollama is running on a different host, override it with the `OLLAMA_URL` environment variable:

```bash
export OLLAMA_URL=http://192.168.1.10:11434
uv run python -m agent.assistant --backend ollama fabric-qa "Is the fabric healthy?"
```

## Repository Structure

```
IAC_Labs/
  containerlab/
    topology.yaml             # ContainerLab 6-node spine-leaf topology
  data/
    fabric.yaml               # Fabric-wide settings (ASN, protocols, IP ranges)
    topology.yaml             # Device inventory with roles and addressing
    underlay.yaml             # OSPF configuration and P2P links
    overlay.yaml              # BGP EVPN and route reflector configuration
    defaults.yaml             # Default values (MTU, timers, flooding)
    services/
      vrfs.yaml               # VRF definitions
      networks.yaml           # L2/L3 network segments (VLANs, VNIs, subnets)
      interfaces.yaml         # Host-facing interface assignments
  schemas/
    models.py                 # Pydantic v2 schema definitions
  validators/
    format_validator.py       # Layer 1: YAML well-formedness
    syntax_validator.py       # Layer 2: Pydantic schema enforcement
    semantic_validator.py     # Layer 3: Cross-file logical consistency (16 rules)
    compliance_validator.py   # Layer 4: Organizational policy (7 rules)
  generators/
    python/
      render.py               # Config rendering engine
      templates/              # Jinja2 templates for FRR
    ansible/
      playbook.yaml           # Ansible config generation path
      templates/              # Per-role Jinja2 templates
    terraform/
      main.tf                 # Terraform config generation path
      templates/              # Terraform template for FRR
  tests/
    test_format.py            # Format validation tests
    test_syntax.py            # Syntax validation tests
    test_semantic.py          # Semantic validation tests
    test_compliance.py        # Compliance validation tests
  validate.py                 # CLI entry point for all validation layers
  docs/
    lab1-build-guide.md       # Lab 1 walkthrough
    lab2-build-guide.md       # Lab 2 walkthrough
    lab3-build-guide.md       # Lab 3 walkthrough
```

## Contributing

This project is part of [E-Conners-Lab](https://github.com/E-Conners-Lab). If you find issues while following the labs, open an issue on this repository.

## License

MIT
