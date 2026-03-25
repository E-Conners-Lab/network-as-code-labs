# Network as Code Lab Series Blueprint

## Series Overview

This lab series teaches network engineers how to manage infrastructure using code-driven workflows. Each lab builds on the previous one, taking engineers from defining network intent in YAML through automated deployment pipelines with pre and post-change validation. The series uses ContainerLab with FRR devices as the lab environment, making it accessible to anyone with a Linux machine.

What makes this series different from typical automation tutorials is that it covers multiple tooling paths. In the real world, nobody runs a pure Python shop or a pure Terraform shop. Engineers need to understand when each tool fits and how they work together. Every lab offers a Git-native path as the primary workflow, with optional paths through NetBox and Nautobot as external sources of truth.

The series is designed to serve three purposes: YouTube content for The Tech-E channel, premium guide material, and direct integration with the Engineer Brain AI agent project.

---

## Lab Environment

### Base Infrastructure

All labs run on ContainerLab with FRR routers simulating a VXLAN/EVPN-style spine-leaf fabric. The topology is intentionally kept small enough to run on modest hardware while being complex enough to demonstrate real patterns.

**Topology: 6-node spine-leaf fabric**

| Device | Role | Loopback0 | ASN |
|--------|------|-----------|-----|
| spine1 | Spine / Route Reflector | 10.0.0.1/32 | 65000 |
| spine2 | Spine / Route Reflector | 10.0.0.2/32 | 65000 |
| leaf1 | Leaf | 10.0.0.11/32 | 65000 |
| leaf2 | Leaf | 10.0.0.12/32 | 65000 |
| border1 | Border Leaf | 10.0.0.21/32 | 65000 |
| border2 | Border Leaf | 10.0.0.22/32 | 65000 |

**Underlay**: OSPF area 0 on all point-to-point fabric links
**Overlay**: iBGP with EVPN address family, spines as route reflectors
**Management**: Separate management network for SSH/API access

### Supporting Infrastructure (Progressive)

Labs 1-3 require only the ContainerLab topology and a Git repository. Later labs add components progressively:

- **Lab 4+**: GitHub Actions or GitLab CI runners
- **Lab 5+**: Robot Framework or pytest for post-change testing
- **Lab 6+**: Scheduled pipeline runs for drift detection
- **Lab 7**: Engineer Brain agent connected to the pipeline

### Optional Source of Truth Platforms

Each lab documents how to accomplish the same outcome using three different sources of truth:

1. **Git (Primary)**: YAML files in a version-controlled repository. This is the default path and requires no additional infrastructure.
2. **NetBox (Optional)**: Network data stored in NetBox, exported to YAML for pipeline consumption. Requires a running NetBox instance (can run as a container on the same host).
3. **Nautobot (Optional)**: Same concept as NetBox but using Nautobot's data model and API. Useful for shops that have standardized on Nautobot.

The Git path is always taught first. The NetBox/Nautobot paths are presented as "here's how this works if your organization already has one of these platforms" rather than requiring engineers to deploy them.

---

## Lab Series

### Lab 1: Defining Network Intent with a Data Model

**Concepts Covered**: Single source of truth, declarative data models, intent vs device-centric configuration, YAML schema design

**Tooling**: Python, YAML, Git

**What We Build**:
- A YAML-based data model that describes the fabric topology, roles, underlay routing, overlay configuration, and network services
- A schema definition (using Pydantic or Yamale) that validates the data model structure
- A Git repository structure following NaC conventions (data directory, workspaces, defaults)

**Data Model Structure**:

```
data/
  fabric.yaml          # Fabric-wide settings (ASN, underlay protocol, etc.)
  topology.yaml        # Device inventory with roles
  underlay.yaml        # OSPF/ISIS parameters, P2P links
  overlay.yaml         # BGP EVPN configuration, route reflectors
  services/
    vrfs.yaml          # VRF definitions
    networks.yaml      # L2/L3 network segments (VNIs, subnets)
    interfaces.yaml    # Host-facing interface assignments
defaults.yaml          # Default values (timers, MTU, descriptions)
```

**Key Teaching Points**:
- Why a data model matters: the paper cites 80%+ of network issues come from misconfigurations and change management failures. A validated data model catches those before they hit the network.
- Intent-based vs device-centric: Show the same BGP route reflector config expressed both ways. In the intent model, you declare spine1 as a route reflector and the automation builds all the peering. In the device model, you specify every neighbor statement per device.
- Schema enforcement: Demonstrate catching errors like overlapping subnets, invalid VLAN ranges, or referencing a VRF that doesn't exist in the model.

**Optional NetBox/Nautobot Path**:
- Show how the same data lives in NetBox (devices, interfaces, IP addresses, VRFs, VLANs)
- Write a Python script that exports NetBox data into the same YAML structure
- Discuss when Git-native vs NetBox-backed makes more sense (team size, existing tooling, brownfield vs greenfield)

**Deliverables**:
- Complete data model YAML files for the 6-node fabric
- Pydantic models for schema validation
- Git repository with proper structure
- Export script for NetBox/Nautobot (optional path)

---

### Lab 2: Pre-Change Validation

**Concepts Covered**: Shift-left testing, format/syntax/semantic/compliance validation, nac-validate equivalent

**Tooling**: Python (Pydantic, custom validators), pytest

**What We Build**:
- A validation framework that runs four levels of checks against the data model
- Custom compliance rules specific to our fabric design
- pytest integration so validations run as proper test cases with reporting

**Validation Layers**:

1. **Format Validation**: Is the YAML well-formed? Can it be parsed without errors?
2. **Syntax Validation**: Does each field match the expected type and constraints? Is the ASN within the valid range? Are IP addresses properly formatted? Are required fields present?
3. **Semantic Validation**: Are there logical inconsistencies? Do referenced objects exist (e.g., a VRF referenced by a network actually defined)? Are there overlapping subnets? Is every spine-leaf link defined bidirectionally?
4. **Compliance Validation**: Does the configuration meet organizational policy? Examples: naming conventions enforced, no BGP peers without authentication configured, all management interfaces in the correct VRF, OSPF authentication required on all fabric links.

**Key Teaching Points**:
- The cost of catching an error in validation vs catching it in production. A subnet overlap caught in YAML review costs zero downtime. The same overlap caught after deployment costs an outage.
- Custom compliance rules as code: Show engineers how to write Python functions that encode their organization's standards. This is the part that turns tribal knowledge into enforceable policy.
- Integration with CI: These validations become gates in the pipeline. A failing semantic check blocks the merge request.

**Deliverables**:
- Validation library with four layers
- At least 10 semantic validation rules
- At least 5 compliance validation rules
- pytest test suite with HTML report generation
- Example of a failing validation with clear error messages

---

### Lab 3: Configuration Generation from Intent

**Concepts Covered**: Template-based config generation, intent-to-device translation, role-based configuration, Jinja2 templating vs Terraform HCL

**Tooling**: Python + Jinja2 (primary), Ansible (alternative), Terraform (alternative)

**What We Build**:
- Jinja2 templates that translate the data model into per-device FRR configurations
- Role-aware generation where declaring a device as "spine" with "route_reflector: true" produces the correct BGP neighbor statements for every leaf
- A config rendering pipeline that reads the validated data model and outputs device-specific configs

**Three Tooling Paths** (build all three, compare):

**Path A: Python + Jinja2**
- Most control, most flexibility
- Direct mapping from YAML data model to templates
- Good for understanding what's happening under the hood
- Best fit when you need custom logic during generation

**Path B: Ansible**
- Uses Ansible roles mapped to device roles (spine, leaf, border)
- Jinja2 templates inside Ansible templates/ directory
- group_vars and host_vars populated from the data model
- Good for teams already running Ansible
- Demonstrates the imperative/procedural approach the paper discusses

**Path C: Terraform + Custom Provider (Conceptual)**
- Show how Terraform's declarative approach works for this use case
- Use the local_file provider to generate configs as a demonstration
- Discuss state management and why Terraform's stateful approach matters for detecting drift
- Reference the actual Cisco NaC Terraform modules and how they work

**Key Teaching Points**:
- The route reflector example from the Cisco paper: Adding a second route reflector spine should automatically update every leaf's BGP config. Show this working with each tooling path.
- Underlay routing protocol swap: Change one parameter in the data model (routing_protocol: ospf to routing_protocol: isis) and regenerate all device configs. This demonstrates the power of intent-based automation.
- Default values: Show how a defaults.yaml file reduces repetition. If 90% of your bridge domains use the same flooding settings, define it once.

**Deliverables**:
- Complete Jinja2 template set for FRR (OSPF underlay, BGP EVPN overlay, VRFs, interfaces)
- Ansible playbook and roles for the same
- Terraform configuration demonstrating the declarative approach
- Side-by-side comparison of generated configs from all three paths
- Demo showing the route reflector auto-peering behavior

---

### Lab 4: CI/CD Pipeline for Network Changes

**Concepts Covered**: GitOps workflow, CI/CD for networking, branching strategy, pipeline stages, automated gates

**Tooling**: GitHub Actions (primary), Python, Scrapli (deployment), Git

**What We Build**:
- A GitHub Actions workflow that implements the full NaC CI/CD pipeline
- Feature branch workflow: push to branch triggers validation, PR triggers plan, merge to main triggers deployment
- Integration with Scrapli for pushing configs to ContainerLab FRR devices
- Webex/Slack notifications at each stage (optional)

**Pipeline Stages**:

```
Stage 1: Feature Branch Push
  → YAML lint
  → Format validation
  → Syntax validation  
  → Semantic validation
  → Compliance validation
  → Notification: "Validation passed/failed"

Stage 2: Pull Request
  → All Stage 1 checks
  → Config generation (diff against current)
  → Plan output (what will change on each device)
  → Pre-change analysis
  → Notification: "PR ready for review with change summary"

Stage 3: Merge to Main
  → Config deployment via Scrapli
  → Post-change testing (Lab 5)
  → Notification: "Deployment complete, tests passed/failed"
```

**Branching Strategy**:
- main: Production configs, protected branch, requires PR approval
- feature/*: Individual changes (e.g., feature/add-vrf-production)
- hotfix/*: Emergency changes with expedited review

**Key Teaching Points**:
- The paper talks about how CI/CD transforms network changes from manual, error-prone processes into repeatable, validated workflows. This lab is where that concept becomes tangible.
- Pipeline as a safety net: Even if an engineer makes a mistake in the YAML, the pipeline catches it before it touches the network.
- Change visibility: Every change has a PR with a clear diff, a generated plan, and a reviewer. Compare this to someone SSHing into a device and typing commands.

**Alternative CI/CD Platforms**:
- Show equivalent GitLab CI configuration
- Discuss Jenkins for shops that have existing Jenkins infrastructure
- Note that the pipeline logic is the same regardless of the CI/CD platform

**Deliverables**:
- Complete GitHub Actions workflow files
- Scrapli-based deployment script (async, handles multiple devices)
- Pipeline notification integration
- Documentation on branching strategy
- GitLab CI equivalent configuration

---

### Lab 5: Post-Change Validation and Testing

**Concepts Covered**: Post-deployment verification, operational state testing, health checks, test-driven automation, nac-test equivalent

**Tooling**: Python, Scrapli, pytest, Robot Framework (alternative)

**What We Build**:
- A test framework that validates the network after every deployment
- Three categories of tests: configuration verification, operational state, and health checks
- Integration with the CI/CD pipeline from Lab 4

**Test Categories**:

**Configuration Verification**:
- Compare intended config (from data model) against actual running config on each device
- Verify OSPF process exists with correct parameters
- Verify BGP neighbors are configured as intended
- Verify VRF and network segment configurations match intent

**Operational State Tests**:
- OSPF neighbors: Are all expected adjacencies in FULL state?
- BGP peers: Are all iBGP sessions established with correct address families?
- VXLAN tunnels: Are VTEP endpoints reachable?
- Route verification: Are expected prefixes present in the routing table?
- Ping tests: Can leaf1 reach leaf2 through the overlay?

**Health Tests**:
- Interface errors: Check for CRC errors, input/output drops on fabric links
- Resource utilization: CPU/memory on each device within thresholds
- Log analysis: Any critical/error severity messages since last deployment?
- Convergence timing: How long did routing protocols take to converge after the change?

**Two Tooling Paths**:

**Path A: pytest + Scrapli**
- Python-native, integrates cleanly with the existing codebase
- Custom fixtures for device connections
- Parametrized tests that iterate over the data model
- HTML report generation

**Path B: Robot Framework**
- Keyword-driven testing, more accessible to non-programmers
- Built-in reporting and logging
- Aligns with Cisco's use of Robot Framework in their NaC testing
- Custom keywords wrapping Scrapli calls

**Key Teaching Points**:
- The difference between "configured correctly" and "working correctly." A BGP neighbor might be configured but stuck in Active state due to a firewall rule. Configuration verification would pass, but operational state testing would catch it.
- Health tests as regression detection: Your change might have successfully added a new VRF, but it inadvertently increased CPU utilization on the spines by 30%. Without health tests, you wouldn't know until something breaks.
- Living documentation: The test suite itself documents what "healthy" means for your network.

**Deliverables**:
- pytest test suite with all three test categories
- Robot Framework equivalent test suite
- Test report templates (HTML)
- Integration with the CI/CD pipeline (tests run automatically post-deployment)
- Failure examples showing how each test category catches different issues

---

### Lab 6: Drift Detection and Reconciliation

**Concepts Covered**: Configuration drift, out-of-band changes, brownfield challenges, state reconciliation, hybrid operations

**Tooling**: Python, Scrapli, Git, scheduled CI/CD jobs

**What We Build**:
- A drift detection engine that periodically compares the network's actual state against the declared intent in Git
- Automated alerting when drift is detected
- A reconciliation workflow that brings the network back in line with the source of truth
- A brownfield import tool that captures existing device configs and translates them into the data model

**Drift Detection Workflow**:

```
Scheduled Job (every N minutes/hours):
  → Connect to all devices via Scrapli
  → Collect running configurations
  → Parse configs into structured data
  → Compare against data model intent
  → If drift detected:
      → Generate drift report (what changed, on which device)
      → Alert via Slack/Webex
      → Optionally auto-remediate OR create a PR for review
```

**Reconciliation Strategies**:
1. **Auto-remediate**: Push the intended config immediately. Best for low-risk changes (someone added a description to an interface).
2. **PR-based remediation**: Create a PR showing the drift and proposed fix. Requires human review. Best for anything that could affect traffic.
3. **Absorb the change**: Update the data model to reflect the out-of-band change. Used when the manual change was intentional and correct (emergency fix scenario).

**Brownfield Import**:
- Script that connects to each device, collects the running config, and translates it into YAML data model format
- Handles the messy reality of inconsistent configs across devices
- Produces a "starting point" data model that can then be cleaned up and standardized

**Key Teaching Points**:
- The paper is clear that out-of-band changes are the enemy of NaC. This lab shows why: the next pipeline run will detect the difference and try to overwrite the manual change. If you're going to do NaC, you have to commit to it.
- Emergency change workflow: Sometimes you have to fix something manually. This lab shows the proper process for reconciling that change back into the data model afterward.
- Brownfield reality: Most networks aren't greenfield. This lab addresses the practical challenge of "I have 200 switches configured over 10 years by different engineers, how do I get this into a data model?"

**Deliverables**:
- Drift detection script with structured diff output
- Scheduled GitHub Actions workflow for periodic drift checks
- Reconciliation scripts for all three strategies
- Brownfield import tool for FRR configurations
- Drift report template (shows what changed, where, and when)

---

### Lab 7: AI Agent Integration

**Concepts Covered**: AI-assisted network operations, agent-driven diagnostics, natural language to config translation, the Engineer Brain concept

**Tooling**: Python, Claude API, Engineer Brain agent, MCP tools

**What We Build**:
- Integration between the Engineer Brain agent and the NaC pipeline
- AI-assisted validation failure diagnosis: when a semantic check fails, the agent explains why and suggests a fix
- Natural language config generation: "Add a new production VRF with subnet 10.100.0.0/24 on all leaf switches" translated into data model YAML
- Post-deployment failure analysis: agent reasons about test failures and suggests remediation

**Integration Points**:

1. **Validation Assistant**: When nac-validate equivalent fails, the agent reads the error, the data model, and the schema, then explains the issue in plain English and generates a corrected YAML snippet.

2. **Config Generator**: Engineer describes intent in natural language. Agent generates the YAML data model changes, runs them through validation, and creates a PR.

3. **Deployment Diagnostics**: When post-change tests fail, the agent SSHs into the relevant devices (read-only), gathers diagnostic data, correlates it with the change that was just made, and produces a root cause analysis.

4. **Drift Analyzer**: When drift is detected, the agent determines whether the drift was likely intentional (emergency fix) or accidental (someone fat-fingered a command) and recommends the appropriate reconciliation strategy.

**Demo Scenarios**:
- Scenario A: Agent cannot reach a device via SSH, so it reasons about the failure gracefully and reports what it can vs what it can't determine (this ties directly to the NetRegion demo concept)
- Scenario B: Agent has full SSH access, diagnoses a BGP peering failure after a config change, identifies the root cause (mismatched ASN), and generates a fix

**Key Teaching Points**:
- AI agents need structured, machine-readable data to be effective. The entire NaC pipeline we built in Labs 1-6 creates exactly the kind of environment where AI agents can operate reliably.
- The GAIT audit trail ensures every agent action is logged and reviewable. This is critical for trust in production environments.
- Earned autonomy: The SNA framework concepts apply here. The agent starts with read-only access and suggestions. As confidence builds, it can be granted more authority.

**Deliverables**:
- Engineer Brain integration with the NaC pipeline
- Validation assistant module
- Natural language config generator
- Post-deployment diagnostic workflow
- GAIT audit logging for all agent actions
- Two demo scenarios (SSH unavailable vs active troubleshooting)

---

## Tooling Matrix

This table shows which tools are used in each lab and why:

| Lab | Python | Ansible | Terraform | Scrapli | pytest | Robot | GitHub Actions |
|-----|--------|---------|-----------|---------|--------|-------|---------------|
| 1   | ✅ Schema | - | - | - | - | - | - |
| 2   | ✅ Validators | - | - | - | ✅ Test runner | - | - |
| 3   | ✅ Jinja2 | ✅ Roles | ✅ Demo | - | - | - | - |
| 4   | ✅ Deploy script | ✅ Alt deploy | ✅ Alt deploy | ✅ SSH | - | - | ✅ Pipeline |
| 5   | ✅ Tests | - | - | ✅ Data collect | ✅ Primary | ✅ Alt | ✅ Integration |
| 6   | ✅ Drift engine | - | ✅ State compare | ✅ Collection | ✅ Drift tests | - | ✅ Scheduled |
| 7   | ✅ Agent | - | - | ✅ Diagnostics | - | - | ✅ Triggers |

## Source of Truth Options

| Lab | Git (Primary) | NetBox (Optional) | Nautobot (Optional) |
|-----|---------------|-------------------|---------------------|
| 1   | YAML files in repo | Export devices/IPs/VRFs to YAML | Same as NetBox path |
| 2   | Validate YAML directly | Validate exported YAML | Same as NetBox path |
| 3   | Generate from YAML | Generate from NetBox API or exported YAML | Same pattern |
| 4   | Pipeline reads from Git | Pipeline syncs NetBox to Git, then proceeds | Same pattern |
| 5   | Tests reference Git YAML | Tests can query NetBox for expected state | Same pattern |
| 6   | Drift compared to Git YAML | Drift compared to NetBox records | Same pattern |
| 7   | Agent reads Git data model | Agent queries NetBox API directly | Same pattern |

## Content Mapping

### YouTube Episodes (Season 2: AI/Automation)

Each lab maps to 2-3 YouTube episodes:

- Lab 1: "Building a Network Data Model from Scratch" (1 episode)
- Lab 2: "Catching Network Misconfigs Before They Hit Production" (1 episode)
- Lab 3: "Three Ways to Generate Network Configs" (2 episodes: Python+Jinja2, then Ansible+Terraform)
- Lab 4: "Your First Network CI/CD Pipeline" (2 episodes: setup, then walkthrough)
- Lab 5: "Automated Testing for Network Changes" (1-2 episodes)
- Lab 6: "Detecting and Fixing Config Drift" (1 episode)
- Lab 7: "AI Agent Meets Network Automation" (2 episodes: integration, then demo scenarios)

Total: ~12-15 episodes

### Premium Guide Material

Labs 1-3 form the core of a "Network as Code Fundamentals" guide. Labs 4-6 extend into a "Network CI/CD Pipeline" guide. Lab 7 ties into the existing "Building with AI" guide.

### Engineer Brain Integration

Lab 7 is the direct integration point, but the entire series builds the foundation that makes the agent effective. The structured data model (Lab 1), validation framework (Lab 2), and testing suite (Lab 5) are all components that the Engineer Brain agent relies on.

---

## Repository Structure

```
network-as-code-labs/
├── README.md
├── containerlab/
│   └── topology.yaml              # ContainerLab topology definition
├── data/
│   ├── defaults.yaml
│   ├── fabric.yaml
│   ├── topology.yaml
│   ├── underlay.yaml
│   ├── overlay.yaml
│   └── services/
│       ├── vrfs.yaml
│       ├── networks.yaml
│       └── interfaces.yaml
├── schemas/
│   └── models.py                  # Pydantic schema definitions
├── validators/
│   ├── format_validator.py
│   ├── syntax_validator.py
│   ├── semantic_validator.py
│   └── compliance_validator.py
├── generators/
│   ├── python/
│   │   ├── render.py
│   │   └── templates/
│   │       ├── frr_base.j2
│   │       ├── frr_ospf.j2
│   │       ├── frr_bgp.j2
│   │       └── frr_vrf.j2
│   ├── ansible/
│   │   ├── playbook.yaml
│   │   └── roles/
│   │       ├── spine/
│   │       ├── leaf/
│   │       └── border/
│   └── terraform/
│       └── main.tf
├── deploy/
│   ├── scrapli_deploy.py          # Async deployment via Scrapli
│   └── ansible_deploy.yaml        # Ansible deployment alternative
├── tests/
│   ├── pytest/
│   │   ├── test_config_verify.py
│   │   ├── test_operational.py
│   │   └── test_health.py
│   └── robot/
│       └── post_change.robot
├── drift/
│   ├── detect.py
│   ├── reconcile.py
│   └── brownfield_import.py
├── agent/
│   ├── validation_assistant.py
│   ├── config_generator.py
│   └── diagnostic_workflow.py
├── integrations/
│   ├── netbox_export.py
│   └── nautobot_export.py
├── .github/
│   └── workflows/
│       ├── validate.yaml          # Feature branch validation
│       ├── plan.yaml              # PR change plan
│       ├── deploy.yaml            # Main branch deployment
│       └── drift_check.yaml       # Scheduled drift detection
└── docs/
    ├── lab1-data-model.md
    ├── lab2-validation.md
    ├── lab3-config-generation.md
    ├── lab4-cicd-pipeline.md
    ├── lab5-post-change-testing.md
    ├── lab6-drift-detection.md
    └── lab7-ai-agent.md
```

## Next Steps

Start with Lab 1: Define the ContainerLab topology and build the YAML data model with Pydantic schema validation. This establishes the foundation that every subsequent lab builds on.