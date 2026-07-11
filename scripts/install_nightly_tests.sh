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

# ── Bridge-role gate ────────────────────────────────────────────────────
# Nightly-test alerts route through the Telegram bridge, so the schedule is
# only meaningful on a machine that has at least one Telegram-configured
# (bridge) project assigned to it. Non-bridge machines (e.g. skills-only
# laptops) skip the install and remove any stale plist from a prior install.
#
# Mirrors the has_email_role() pattern from scripts/install_email_bridge.sh.
has_bridge_role() {
    local config="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
    if [ ! -f "$config" ]; then
        return 0  # Fail open when config is unreadable
    fi
    if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
        return 0  # Fail open when venv is missing
    fi
    "$PROJECT_DIR/.venv/bin/python" - "$config" <<'PYEOF'
import json, subprocess, sys

try:
    host = subprocess.check_output(
        ["scutil", "--get", "ComputerName"], text=True
    ).strip()
except Exception:
    sys.exit(0)  # Fail open on scutil error

try:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
except Exception:
    sys.exit(0)  # Fail open on config parse error

target = host.lower()
for proj in cfg.get("projects", {}).values():
    if (proj.get("machine") or "").lower() != target:
        continue
    if proj.get("telegram"):
        sys.exit(0)  # At least one bridge-role project found — qualify
sys.exit(1)  # No bridge-role project found for this host
PYEOF
}

if ! has_bridge_role; then
    host=$(scutil --get ComputerName 2>/dev/null || echo unknown)
    echo "Skipping nightly-tests install (no bridge projects assigned to '$host')"
    if [ -f "$PLIST_DST" ]; then
        echo "Removing stale nightly-tests plist from non-bridge machine..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Stale nightly-tests plist removed."
    fi
    exit 0
fi
# ── End bridge-role gate ────────────────────────────────────────────────

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
