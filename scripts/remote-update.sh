#!/bin/bash
# Remote update: pull latest code, sync deps if needed, write restart flag.
# Designed to run unattended (from Telegram /update command or launchd cron).
#
# This script uses the modular Python update system in scripts/update/.
# For full updates with all checks, use: python scripts/update/run.py --full

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOCK_DIR="$PROJECT_DIR/data/update.lock"

cd "$PROJECT_DIR"

# Ensure data directory exists
mkdir -p "$PROJECT_DIR/data"

# ── Lockfile (mkdir is atomic on POSIX) ──────────────────────────────
cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another update is already running. Skipping."
    exit 0
fi
trap cleanup_lock EXIT

# ── Check for Python venv ────────────────────────────────────────────
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "ERROR: No Python venv at $PYTHON"
    echo "Run: uv venv && uv sync --all-extras"
    exit 1
fi

# ── Run update in cron mode ──────────────────────────────────────────
# Output goes directly to Telegram - keep it clean for PM-style summary
"$PYTHON" "$PROJECT_DIR/scripts/update/run.py" --cron

# ── Reload reflections plist if present ──────────────────────────────
REFLECTIONS_PLIST="$PROJECT_DIR/com.valor.reflections.plist"
REFLECTIONS_DST="$HOME/Library/LaunchAgents/com.valor.reflections.plist"
REFLECTIONS_LABEL="com.valor.reflections"
# Unload old daydream service if still present (migration)
OLD_DAYDREAM_DST="$HOME/Library/LaunchAgents/com.valor.daydream.plist"
if launchctl list | grep -q "com.valor.daydream"; then
    launchctl bootout "gui/$(id -u)/com.valor.daydream" 2>/dev/null || true
    rm -f "$OLD_DAYDREAM_DST"
fi
if [ -f "$REFLECTIONS_PLIST" ]; then
    if launchctl list | grep -q "$REFLECTIONS_LABEL"; then
        if ! launchctl bootout "gui/$(id -u)/$REFLECTIONS_LABEL"; then
            echo "ERROR: Failed to bootout $REFLECTIONS_LABEL"
        fi
    fi
    cp "$REFLECTIONS_PLIST" "$REFLECTIONS_DST"
    if ! launchctl bootstrap "gui/$(id -u)" "$REFLECTIONS_DST"; then
        echo "ERROR: Failed to bootstrap $REFLECTIONS_LABEL"
    fi
fi
