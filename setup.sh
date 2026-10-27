#!/usr/bin/env bash
# setup.sh — First-time setup for the GitBug-Java + EvoSuite experiment.
# Run this once on a new machine before anything else.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; }

echo "══════════════════════════════════════════════"
echo "  GitBug-Java + EvoSuite — First-time setup"
echo "══════════════════════════════════════════════"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
echo "── Checking prerequisites ───────────────────"

check_cmd() {
    if command -v "$1" &>/dev/null; then ok "$1 found"; else fail "$1 not found — install it first"; exit 1; fi
}

check_cmd git
check_cmd docker
check_cmd python3
check_cmd poetry
check_cmd java

echo ""
echo "── Optional Python packages ─────────────────────────"

if python3 -c "import pygit2" &>/dev/null; then
    ok "pygit2 found (enables --checkout-mode json)"
else
    warn "pygit2 not found — only needed for --checkout-mode json"
    warn "Install with: pip install pygit2"
fi

# ── 2. gitbug-java repo ───────────────────────────────────────────────────────
echo ""
echo "── gitbug-java repo ─────────────────────────"

if [ -z "${GITBUG_REPO:-}" ]; then
    read -rp "  Path to cloned gitbug-java repo (leave blank to clone now): " GITBUG_REPO
fi

if [ -z "$GITBUG_REPO" ]; then
    read -rp "  Where to clone it? [~/gitbug-java]: " CLONE_DIR
    CLONE_DIR="${CLONE_DIR:-$HOME/gitbug-java}"
    git clone https://github.com/gitbugactions/gitbug-java.git "$CLONE_DIR"
    GITBUG_REPO="$CLONE_DIR"
fi

if [ -d "$GITBUG_REPO" ]; then
    ok "gitbug-java found at $GITBUG_REPO"
else
    fail "Directory not found: $GITBUG_REPO"
    exit 1
fi

echo "  Installing gitbug-java dependencies (poetry install)..."
(cd "$GITBUG_REPO" && poetry install -q)
ok "gitbug-java dependencies installed"

# ── 3. EvoSuite jar ───────────────────────────────────────────────────────────
echo ""
echo "── EvoSuite jar ─────────────────────────────"

if [ -z "${EVOSUITE_JAR:-}" ]; then
    read -rp "  Path to evosuite-1.2.0.jar: " EVOSUITE_JAR
fi

if [ -f "$EVOSUITE_JAR" ]; then
    ok "EvoSuite jar found at $EVOSUITE_JAR"
else
    fail "Not found: $EVOSUITE_JAR"
    echo "  Download from: https://github.com/EvoSuite/evosuite/releases/tag/v1.2.0"
    exit 1
fi

# ── 4. JUnit / Hamcrest (auto-detected from ~/.m2) ───────────────────────────
echo ""
echo "── JUnit / Hamcrest ─────────────────────────"

JUNIT_JAR=$(find ~/.m2/repository/junit/junit -name "junit-[0-9]*.jar" 2>/dev/null | sort | tail -1 || true)
HAMCREST_JAR=$(find ~/.m2/repository/org/hamcrest/hamcrest-core -name "hamcrest-core-[0-9]*.jar" 2>/dev/null | sort | tail -1 || true)

if [ -n "$JUNIT_JAR" ]; then
    ok "JUnit jar: $JUNIT_JAR"
else
    warn "JUnit not found in ~/.m2. Run any Maven build first to download it, or install manually."
fi

if [ -n "$HAMCREST_JAR" ]; then
    ok "Hamcrest jar: $HAMCREST_JAR"
else
    warn "Hamcrest not found in ~/.m2. It will be downloaded automatically on first Maven run."
fi

# ── 5. Docker images ──────────────────────────────────────────────────────────
echo ""
echo "── Docker images ────────────────────────────"

build_image() {
    local image="$1"
    if docker image inspect "$image" &>/dev/null; then
        ok "Image $image already exists"
    else
        warn "Image $image not found — building now..."
        bash "$SCRIPT_DIR/docker/build.sh"
    fi
}

build_image gitbug-java8
build_image gitbug-java11

# ── 6. Final check ────────────────────────────────────────────────────────────
echo ""
echo "── Running final check ──────────────────────"

python3 "$SCRIPT_DIR/run_screened_bugs.py" \
    --gitbug-repo "$GITBUG_REPO" \
    --evosuite-jar "$EVOSUITE_JAR" \
    --check

echo ""
echo "══════════════════════════════════════════════"
echo "  Setup complete! To run the experiment:"
echo ""
echo "  cd $GITBUG_REPO"
echo "  poetry run python $SCRIPT_DIR/run_screened_bugs.py \\"
echo "    --gitbug-repo $GITBUG_REPO \\"
echo "    --evosuite-jar $EVOSUITE_JAR \\"
echo "    --bugs-csv $SCRIPT_DIR/bugs_java_assigned.csv \\"
echo "    --seeds 3147383999447 \\"
echo "    --workers 4"
echo "══════════════════════════════════════════════"
