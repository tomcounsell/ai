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

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

cd "$PROJECT_DIR"

# Ensure data directory exists
mkdir -p "$PROJECT_DIR/data"

# ── Lockfile (mkdir is atomic on POSIX) ──────────────────────────────
cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    # Stale lock detection: if lock is older than 10 minutes, force-remove it.
    # This prevents permanent lockout after crashes, OOM kills, or power loss.
    if [ -d "$LOCK_DIR" ]; then
        LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
        if [ "$LOCK_AGE" -gt 600 ]; then
            echo "Stale lock detected (${LOCK_AGE}s old). Removing."
            rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
            mkdir "$LOCK_DIR" 2>/dev/null || true
        else
            echo "Another update is already running. Skipping."
            exit 0
        fi
    else
        echo "Another update is already running. Skipping."
        exit 0
    fi
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
REFLECTIONS_LABEL="${SERVICE_LABEL_PREFIX}.reflections"
REFLECTIONS_DST="$HOME/Library/LaunchAgents/${REFLECTIONS_LABEL}.plist"
# Hard-pin legacy daydream cleanup to com.valor — that label only ever
# existed under the original prefix.
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
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$REFLECTIONS_LABEL|g" "$REFLECTIONS_PLIST" > "$REFLECTIONS_DST"
    if ! launchctl bootstrap "gui/$(id -u)" "$REFLECTIONS_DST"; then
        echo "ERROR: Failed to bootstrap $REFLECTIONS_LABEL"
    fi
fi

# ── Reload worker plist if present ───────────────────────────────────
WORKER_PLIST="$PROJECT_DIR/com.valor.worker.plist"
WORKER_LABEL="${SERVICE_LABEL_PREFIX}.worker"
WORKER_DST="$HOME/Library/LaunchAgents/${WORKER_LABEL}.plist"
if [ -f "$WORKER_PLIST" ] && [ -f "$WORKER_DST" ]; then
    if launchctl list | grep -q "$WORKER_LABEL"; then
        if ! launchctl bootout "gui/$(id -u)/$WORKER_LABEL"; then
            echo "ERROR: Failed to bootout $WORKER_LABEL"
        fi
    fi
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$WORKER_LABEL|g" "$WORKER_PLIST" > "$WORKER_DST"
    launchctl bootstrap "gui/$(id -u)" "$WORKER_DST"
fi

# ── Sync newsyslog log rotation config if changed ────────────────────
NEWSYSLOG_SRC="$PROJECT_DIR/config/newsyslog.conf.template"
NEWSYSLOG_DST="/etc/newsyslog.d/valor.conf"
if [ -f "$NEWSYSLOG_SRC" ]; then
    # Render template by substituting __PROJECT_DIR__
    NEWSYSLOG_RENDERED=$(sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "$NEWSYSLOG_SRC")
    if [ ! -f "$NEWSYSLOG_DST" ] || [ "$(cat "$NEWSYSLOG_DST")" != "$NEWSYSLOG_RENDERED" ]; then
        echo "$NEWSYSLOG_RENDERED" | sudo tee "$NEWSYSLOG_DST" > /dev/null 2>&1 && \
            echo "newsyslog config updated at $NEWSYSLOG_DST" || \
            echo "WARNING: Could not install newsyslog config (sudo required)"
    fi
fi
