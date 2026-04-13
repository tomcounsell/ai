#!/bin/bash
# Install the standalone worker as a launchd service.
# Usage: ./scripts/install_worker.sh
#
# The worker processes AgentSession records from Redis without Telegram.
# Dev workstations run this instead of the bridge.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

# Source-of-truth template name remains com.valor.worker.plist for
# recognizability; installed copy uses ${SERVICE_LABEL_PREFIX}.worker.plist
# so the on-disk filename matches the internal Label.
PLIST_SRC="$PROJECT_DIR/com.valor.worker.plist"
LABEL="${SERVICE_LABEL_PREFIX}.worker"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/logs/worker"

# Copy projects.json to config/ so launchd worker can read it without iCloud TCC.
# macOS TCC blocks open()/stat() on ~/Desktop files from launchd agents, causing hangs.
# This local copy is read when VALOR_LAUNCHD=1 (bridge/routing.py skips the iCloud path).
PROJECTS_SRC="$HOME/Desktop/Valor/projects.json"
PROJECTS_DST="$PROJECT_DIR/config/projects.json"
if [ -f "$PROJECTS_SRC" ]; then
    cp "$PROJECTS_SRC" "$PROJECTS_DST"
    echo "Copied projects.json → config/projects.json"
else
    echo "WARNING: $PROJECTS_SRC not found — launchd worker will use existing config/projects.json"
fi

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Check .env exists
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example and configure it."
    exit 1
fi

# Check venv exists
if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    echo "Run: python3 -m venv $PROJECT_DIR/.venv && $PROJECT_DIR/.venv/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

# Verify worker can start (dry-run)
echo "Verifying worker configuration..."
if ! "$PROJECT_DIR/.venv/bin/python" -m worker --dry-run 2>&1; then
    echo "ERROR: Worker dry-run failed. Fix configuration before installing."
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

# Inject env vars from .env directly into the plist so the worker process
# never needs to open the iCloud-synced .env file at runtime. macOS TCC
# blocks launchd agents from accessing ~/Desktop files, causing open() to
# hang indefinitely. Reading here (from the terminal, which has full access)
# and baking into the plist avoids the hang entirely.
#
# We use Python's dotenv parser so quoting/escaping is handled correctly.
echo "Injecting env vars from .env into plist..."
export PROJECT_DIR PLIST_DST
"$PROJECT_DIR/.venv/bin/python" - <<'PYEOF'
import os, sys, plistlib
from pathlib import Path
from dotenv import dotenv_values

project_dir = Path(os.environ.get("PROJECT_DIR", "."))
plist_dst = Path(os.environ.get("PLIST_DST", ""))
env_file = project_dir / ".env"

if not plist_dst:
    print("PLIST_DST not set, skipping env injection", file=sys.stderr)
    sys.exit(0)

# Parse .env (follows symlinks, works with iCloud file from terminal)
try:
    env_vars = dotenv_values(env_file)
except Exception as e:
    print(f"Warning: could not parse .env: {e}", file=sys.stderr)
    sys.exit(0)

# Load the plist
with open(plist_dst, "rb") as f:
    plist = plistlib.load(f)

# Merge env vars into EnvironmentVariables (plist values take precedence for PATH/HOME)
existing = plist.setdefault("EnvironmentVariables", {})
injected = 0
for key, value in env_vars.items():
    if key not in existing and value is not None:
        existing[key] = value
        injected += 1

with open(plist_dst, "wb") as f:
    plistlib.dump(plist, f)

print(f"  Injected {injected} env vars into plist")
PYEOF

# Validate plist
if ! plutil -lint "$PLIST_DST" > /dev/null; then
    echo "ERROR: Generated plist is invalid"
    exit 1
fi

# Load new version
echo "Loading $LABEL..."
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo ""
echo "Worker service installed successfully."
echo "  Logs: $PROJECT_DIR/logs/worker.log"
echo "  Errors: $PROJECT_DIR/logs/worker_error.log"
echo "  Output: $PROJECT_DIR/logs/worker/ (per-session)"
echo ""
echo "To check status: launchctl list | grep worker"
echo "To stop: launchctl bootout gui/$(id -u)/$LABEL"
echo "To run manually: python -m worker"
