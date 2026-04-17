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

# Copy iCloud vault files to config/ so the launchd worker can read them without TCC hangs.
# macOS TCC blocks open()/stat() on ~/Desktop files from launchd agents, causing indefinite
# hangs that freeze the asyncio event loop. The worker reads these local copies when
# VALOR_LAUNCHD=1 (bridge/routing.py and agent/reflection_scheduler.py skip the iCloud path).
# We rm -f the destination first to avoid "identical (not copied)" errors when the destination
# is a symlink pointing back to the source (set -euo pipefail would otherwise abort the script).
_copy_config_file() {
    local src="$1" dst="$2" label="$3"
    if [ -f "$src" ]; then
        rm -f "$dst"
        cp "$src" "$dst"
        echo "Copied $label → config/$(basename "$dst")"
    else
        echo "WARNING: $src not found — launchd worker will use existing config/$(basename "$dst")"
    fi
}

_copy_config_file "$HOME/Desktop/Valor/projects.json"     "$PROJECT_DIR/config/projects.json"     "projects.json"
_copy_config_file "$HOME/Desktop/Valor/reflections.yaml"  "$PROJECT_DIR/config/reflections.yaml"  "reflections.yaml"

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

# Install worker watchdog (checks heartbeat every 120s, kills hung worker so launchd restarts it)
WATCHDOG_LABEL="${SERVICE_LABEL_PREFIX}.worker-watchdog"
WATCHDOG_PLIST="$HOME/Library/LaunchAgents/${WATCHDOG_LABEL}.plist"

cat > "$WATCHDOG_PLIST" << WATCHDOGEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${WATCHDOG_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PROJECT_DIR}/.venv/bin/python</string>
        <string>${PROJECT_DIR}/monitoring/worker_watchdog.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${PROJECT_DIR}/.venv/bin:${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>StartInterval</key>
    <integer>120</integer>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/worker_watchdog.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/worker_watchdog.log</string>
</dict>
</plist>
WATCHDOGEOF

launchctl bootout "gui/$(id -u)/$WATCHDOG_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$WATCHDOG_PLIST"
echo "Worker watchdog installed (checks heartbeat every 120s)"

echo ""
echo "Worker service installed successfully."
echo "  Logs: $PROJECT_DIR/logs/worker.log"
echo "  Errors: $PROJECT_DIR/logs/worker_error.log"
echo "  Output: $PROJECT_DIR/logs/worker/ (per-session)"
echo "  Watchdog: $PROJECT_DIR/logs/worker_watchdog.log"
echo ""
echo "To check status: launchctl list | grep worker"
echo "To stop: launchctl bootout gui/$(id -u)/$LABEL"
echo "To run manually: python -m worker"
