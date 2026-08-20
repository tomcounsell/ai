#!/bin/bash
# Install the nightly regression test launchd service (runs daily at 03:00).
# Usage: ./scripts/install_nightly_tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/launchctl.sh"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

PLIST_SRC="$PROJECT_DIR/com.valor.nightly-tests.plist"
LABEL="${SERVICE_LABEL_PREFIX}.nightly-tests"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# ── Worktree refusal (issue #2823) ──────────────────────────────────────
# The plist is machine-global and hardcodes an absolute PROJECT_DIR. Installing
# from a lane worktree would aim the fleet's nightly detector at a directory
# that `/do-build` cleanup deletes once the lane's PR merges. Refuse before the
# role gate — a worktree's `.git` is a FILE containing `gitdir: <main-repo>/
# .git/worktrees/<slug>`, never a directory.
if [ -f "$PROJECT_DIR/.git" ] && grep -qE '^gitdir:.*/.git/worktrees/' "$PROJECT_DIR/.git" 2>/dev/null; then
    echo "Skipping nightly-tests install: running from a worktree checkout ($PROJECT_DIR)"
    echo "The nightly detector must only be installed from a machine's main checkout."
    exit 0
fi
# ── End worktree refusal ─────────────────────────────────────────────────

# ── Unreadable-config refusal (fail closed) ──────────────────────────────
# Installing a nightly full-suite run is not a safe default, so an unreadable
# projects config must not qualify a machine. This gate used to fail *open*,
# which was tolerable only while `scripts/update/run.py` wrapped the call in a
# second, independent `if has_bridge:` check; that wrapper is gone, leaving
# this as the only gate. `projects.json` lives in the iCloud-synced vault and
# `scripts/remote-update.sh` already warns that directory is intermittently
# unreadable, so a transient stall would otherwise install the detector on a
# machine the gate meant to exclude.
#
# Deliberately NOT the role-gate skip path below: that one removes an existing
# plist, and a transient stall must not uninstall a correctly-running detector
# every time iCloud hiccups. Unreadable config => change nothing.
PROJECTS_CONFIG="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
if [ ! -f "$PROJECTS_CONFIG" ]; then
    echo "Skipping nightly-tests install: projects config unreadable at $PROJECTS_CONFIG"
    echo "Failing closed — not installing, and leaving any existing plist untouched."
    exit 0
fi
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "Skipping nightly-tests install: no venv python at $PROJECT_DIR/.venv/bin/python"
    echo "Failing closed — not installing, and leaving any existing plist untouched."
    exit 0
fi
# ── End unreadable-config refusal ────────────────────────────────────────

# ── Worker-role gate ─────────────────────────────────────────────────────
# Running the test suite requires a checkout and a worker, not a Telegram
# bridge — gating on has_bridge_role() (this script's prior form) stranded the
# detector on zero of 20 fleet projects, since none carry a truthy `telegram`
# key. has_worker_role() is has_bridge_role() minus the Telegram-block clause,
# the same fix #1379 applied to the reflection-worker installer
# (scripts/install_reflection_worker.sh). Any machine that owns a project
# qualifies, regardless of whether that project bridges Telegram.
# Exit codes: 0 = this host owns a project (qualify), 1 = parsed cleanly and
# this host owns nothing (a real answer — safe to remove a stale plist),
# 2 = could not determine (scutil or JSON parse failed). 2 is distinct from 1
# precisely so an *unanswerable* check never reaches the removal path below:
# "I don't know" must not uninstall a correctly-running detector.
has_worker_role() {
    local config="$PROJECTS_CONFIG"
    "$PROJECT_DIR/.venv/bin/python" - "$config" <<'PYEOF'
import json, subprocess, sys

try:
    host = subprocess.check_output(
        ["scutil", "--get", "ComputerName"], text=True
    ).strip()
except Exception:
    sys.exit(2)  # Undeterminable — fail closed, change nothing

try:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
except Exception:
    sys.exit(2)  # Undeterminable — fail closed, change nothing

target = host.lower()
for proj in cfg.get("projects", {}).values():
    if (proj.get("machine") or "").lower() == target:
        sys.exit(0)  # This host owns at least one project — qualify
sys.exit(1)  # No project assigned to this host
PYEOF
}

role_rc=0
has_worker_role || role_rc=$?

# Only rc=1 is a real "this host owns nothing" answer. Everything that is not
# 0 or 1 — rc=2 from the script's own undeterminable paths, but equally a
# SIGSEGV, a dyld failure, or any other interpreter death — means the check did
# not answer. Treating "did not answer" as "owns nothing" would take the
# removal path below and uninstall a healthy detector from a qualifying host.
# A broken-but-executable `.venv/bin/python` is a live condition here: the
# `-x` test above passes straight through a half-finished `uv sync` or an
# off-pin interpreter.
if [ "$role_rc" -ne 0 ] && [ "$role_rc" -ne 1 ]; then
    echo "Skipping nightly-tests install: could not determine host role (rc=$role_rc)"
    echo "Failing closed — not installing, and leaving any existing plist untouched."
    exit 0
fi

if [ "$role_rc" -ne 0 ]; then
    host=$(scutil --get ComputerName 2>/dev/null || echo unknown)
    echo "Skipping nightly-tests install (no projects assigned to '$host')"
    if [ -f "$PLIST_DST" ]; then
        echo "Removing stale nightly-tests plist from non-worker machine..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Stale nightly-tests plist removed."
    fi
    exit 0
fi
# ── End worker-role gate ─────────────────────────────────────────────────

# Prerequisite: pytest-json-report must be installed
if ! "$PROJECT_DIR/.venv/bin/python" -m pytest --json-report --help > /dev/null 2>&1; then
    echo "ERROR: pytest-json-report not installed. Run: uv pip install pytest-json-report"
    exit 1
fi

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload existing version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Copy plist to LaunchAgents with path substitution
echo "Installing plist to $PLIST_DST..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$LABEL|g" "$PLIST_SRC" > "$PLIST_DST"

# Load new version
echo "Loading $LABEL..."
launchctl_bootstrap_fail_soft "gui/$(id -u)" "$PLIST_DST" "$LABEL" || exit 1

echo ""
echo "Nightly regression test service installed successfully."
echo "Label:    $LABEL"
echo "Schedule: daily at 03:00 local time"
echo "Log:      $PROJECT_DIR/logs/nightly_tests.log"
echo "Errors:   $PROJECT_DIR/logs/nightly_tests_error.log"
echo ""
echo "To run manually: python scripts/nightly_regression_tests.py --dry-run"
echo "To uninstall:    launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST_DST"
