#!/usr/bin/env bash
# End-to-end dry run for all 7 NaC labs.
#
# Brings up the ContainerLab topology, exercises each lab's key scripts in
# order, then tears everything back down. Run this on the Linux VM where
# ContainerLab and Docker are installed.
#
# Usage:
#   chmod +x dry_run.sh
#   ./dry_run.sh
#
# Prerequisites:
#   - containerlab installed (https://containerlab.dev/install/)
#   - Docker running
#   - uv installed (https://docs.astral.sh/uv/getting-started/installation/)
#   - ANTHROPIC_API_KEY set if you want Lab 7 to use the Claude backend
#     (Lab 7 falls back to Ollama automatically if the key is missing)

set -euo pipefail

TOPO="containerlab/topology.yaml"
REPORT_DIR="reports"

# Always destroy the topology on exit, even if the script fails mid-run.
cleanup() {
    echo ""
    echo -e "${CYAN}${BOLD}Cleanup: destroying topology...${RESET}"
    sudo containerlab destroy --topo "$TOPO" 2>/dev/null || true
}
trap cleanup EXIT

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

step() {
    echo ""
    echo -e "${CYAN}${BOLD}══════════════════════════════════════════${RESET}"
    echo -e "${CYAN}${BOLD}  $1${RESET}"
    echo -e "${CYAN}${BOLD}══════════════════════════════════════════${RESET}"
    echo ""
}

ok() { echo -e "${GREEN}✓ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }

# ---------------------------------------------------------------------------
# Phase 0: Install Python dependencies
# ---------------------------------------------------------------------------
step "Phase 0: Install dependencies"
uv sync
ok "Dependencies installed"

# ---------------------------------------------------------------------------
# Phase 1: Deploy ContainerLab topology
# ---------------------------------------------------------------------------
step "Phase 1: ContainerLab — deploy topology"
sudo containerlab deploy --topo "$TOPO"
ok "Topology deployed — 6-node spine-leaf fabric is up"

# Give FRR daemons a moment to initialise before we start sending configs
sleep 5

# ---------------------------------------------------------------------------
# Phase 2 (Lab 1 / Lab 2): Data model validation
# ---------------------------------------------------------------------------
step "Phase 2 (Lab 1 + 2): Data model validation"

echo "--- Four-layer validation ---"
uv run python validate.py
ok "All four validation layers passed"

echo ""
echo "--- pytest validation suite ---"
uv run python -m pytest tests/ --ignore=tests/post_change -v
ok "Validation test suite passed"

# ---------------------------------------------------------------------------
# Phase 3 (Lab 3): Config generation
# ---------------------------------------------------------------------------
step "Phase 3 (Lab 3): Config generation"
uv run python -m generators.python.render
ok "Device configs generated in configs/"

# ---------------------------------------------------------------------------
# Phase 4 (Lab 4): Deployment — dry run first, then live
# ---------------------------------------------------------------------------
step "Phase 4 (Lab 4): Deployment"

echo "--- Dry run: verify containers are reachable ---"
uv run python -m deploy.scrapli_deploy --dry-run
ok "All containers reachable"

echo ""
echo "--- Live deploy: push configs to all devices ---"
uv run python -m deploy.scrapli_deploy
ok "Configs deployed to all 6 devices"

# Give routing protocols time to converge
echo ""
echo "Waiting 30 seconds for OSPF/BGP to converge..."
sleep 30

# ---------------------------------------------------------------------------
# Phase 5 (Lab 5): Post-change testing
# ---------------------------------------------------------------------------
step "Phase 5 (Lab 5): Post-change testing"

mkdir -p "$REPORT_DIR"
uv run python -m pytest tests/post_change/ -v \
    --html="$REPORT_DIR/post-change-report.html" \
    --self-contained-html
ok "Post-change tests passed — report saved to $REPORT_DIR/post-change-report.html"

# ---------------------------------------------------------------------------
# Phase 6 (Lab 6): Drift detection
# ---------------------------------------------------------------------------
step "Phase 6 (Lab 6): Drift detection"

echo "--- Fabric status ---"
uv run python -m scripts.fabric_status

echo ""
echo "--- Drift detection ---"
# drift.detect exits 1 when drift is found — that's informational here, not fatal.
uv run python -m drift.detect || true
ok "Drift detection complete"

# ---------------------------------------------------------------------------
# Phase 7 (Lab 7): AI assistant
# ---------------------------------------------------------------------------
step "Phase 7 (Lab 7): AI assistant"

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    BACKEND="claude"
else
    echo "ANTHROPIC_API_KEY not set — falling back to Ollama backend"
    BACKEND="ollama"
fi

echo "--- fabric-qa: fabric health check ---"
uv run python -m agent.assistant --backend "$BACKEND" fabric-qa "Is the fabric healthy? Summarise BGP and OSPF state."

echo ""
echo "--- validation-assist: confirm no failures ---"
uv run python -m agent.assistant --backend "$BACKEND" validation-assist

echo ""
echo "--- drift-triage: confirm clean state ---"
uv run python -m agent.assistant --backend "$BACKEND" drift-triage

ok "AI assistant exercises complete"

echo ""
echo -e "${GREEN}${BOLD}Dry run complete. All 7 labs exercised successfully.${RESET}"
echo ""
# Topology teardown happens automatically via the EXIT trap above.
