#!/bin/bash
# Install the reflection-scheduler subprocess as a long-lived launchd service
# (issue #1828). The scheduler moved out of the worker's event loop into its own
# supervised process (KeepAlive=true + ThrottleInterval); this installs/reloads it.
# Usage: ./scripts/install_reflection_worker.sh

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

PLIST_SRC="$PROJECT_DIR/com.valor.reflection-worker.plist"
LABEL="${SERVICE_LABEL_PREFIX}.reflection-worker"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# ── Worker-role gate ────────────────────────────────────────────────────
# The reflection subprocess must install exactly where the WORKER installs — the
# worker install is ungated by role (scripts/install_worker.sh has no machine gate;
# run.py guards it only on plist existence). Gating on bridge-role would strand
# reflections on worker-only (non-Telegram) machines — the #1379 over-narrow-gating
# failure class (which gated calendar on session.slug and DROPPED all non-slug work).
#
# has_worker_role() is has_bridge_role() (install_nightly_tests.sh) MINUS the
# Telegram-block check: it qualifies as soon as ANY project's `machine` matches this
# host, regardless of whether that project has Telegram configured. Same fail-open
# contract. (The bridge-role helper additionally required a Telegram block; that clause
# is dropped here so worker-only machines still run reflections.)
has_worker_role() {
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
    if (proj.get("machine") or "").lower() == target:
        sys.exit(0)  # This host owns at least one project — qualify (worker runs here)
sys.exit(1)  # No project assigned to this host
PYEOF
}

if ! has_worker_role; then
    host=$(scutil --get ComputerName 2>/dev/null || echo unknown)
    echo "Skipping reflection-worker install (no projects assigned to '$host')"
    if [ -f "$PLIST_DST" ]; then
        echo "Removing stale reflection-worker plist from non-worker machine..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Stale reflection-worker plist removed."
    fi
    exit 0
fi
# ── End worker-role gate ────────────────────────────────────────────────

mkdir -p "$PROJECT_DIR/logs"

# ── Config prep (MOVED here from install_worker.sh, issue #1828) ──────────
# The reflection subprocess — not the worker — now owns the reflection registry, so
# the reflections.yaml copy + single-machine-ownership filter live here (single owner,
# no duplicate copy). macOS TCC blocks launchd agents from ~/Desktop, so we copy the
# iCloud vault file to config/ (readable from the terminal, which has full access) and
# the subprocess reads the local copy under VALOR_LAUNCHD=1.
_copy_config_file() {
    local src="$1" dst="$2" label="$3"
    if [ -f "$src" ]; then
        rm -f "$dst"
        cp "$src" "$dst"
        echo "Copied $label → config/$(basename "$dst")"
    else
        echo "WARNING: $src not found — reflection subprocess will use existing config/$(basename "$dst")"
    fi
}

_copy_config_file "$HOME/Desktop/Valor/reflections.yaml" "$PROJECT_DIR/config/reflections.yaml" "reflections.yaml"

# Single-machine ownership: disable any reflection carrying a project_key this machine
# does not own (per config/projects.json, copied by install_worker.sh earlier in the same
# /update). Best-effort: a non-zero exit must not abort the install.
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
    "$PROJECT_DIR/.venv/bin/python" -m tools.reflection_machine_filter \
        --reflections "$PROJECT_DIR/config/reflections.yaml" \
        --projects "$PROJECT_DIR/config/projects.json" || \
        echo "WARNING: reflection ownership filter failed — config/reflections.yaml left unfiltered"
fi
# ── End config prep ───────────────────────────────────────────────────────

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Check venv exists
if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    exit 1
fi

# Verify the subprocess starts with production env parity: the plist runtime sources
# .env (done above) and sets VALOR_LAUNCHD=1, so the dry-run exercises the SAME config
# resolution the launchd process will (local config/reflections.yaml, not the vault path).
echo "Verifying reflection subprocess configuration..."
if ! VALOR_LAUNCHD=1 "$PROJECT_DIR/.venv/bin/python" -m reflections --dry-run 2>&1; then
    echo "ERROR: reflections --dry-run failed. Fix configuration before installing."
    exit 1
fi

# Unload current version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Copy plist to LaunchAgents with path substitution
echo "Installing plist to $PLIST_DST..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$LABEL|g" "$PLIST_SRC" > "$PLIST_DST"

# Validate plist
if ! plutil -lint "$PLIST_DST" > /dev/null; then
    echo "ERROR: Generated plist is invalid"
    exit 1
fi

# Load new version (RunAtLoad starts the subprocess)
echo "Loading $LABEL..."
launchctl_bootstrap_fail_soft "gui/$(id -u)" "$PLIST_DST" "$LABEL" verify-pid || exit 1

echo ""
echo "Reflection-worker service installed successfully."
echo "Label:  $LABEL"
echo "Log:    $PROJECT_DIR/logs/reflection_worker.log"
echo "Errors: $PROJECT_DIR/logs/reflection_worker_error.log"
echo ""
echo "To run manually: python -m reflections --dry-run"
echo "To uninstall:    launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST_DST"
