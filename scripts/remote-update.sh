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

# ── Ensure .env → ~/Desktop/Valor/.env symlink ──────────────────────
# The vault file is the single source of truth for secrets. On a fresh machine
# or after accidental deletion, create the symlink before sourcing .env so the
# rest of this script always reads from the vault.
VAULT_ENV="$HOME/Desktop/Valor/.env"
REPO_ENV="$PROJECT_DIR/.env"
if [ -n "$VAULT_ENV" ] && [ -f "$VAULT_ENV" ] && [ ! -L "$REPO_ENV" ]; then
    echo "[update] Creating .env symlink → $VAULT_ENV"
    [ -f "$REPO_ENV" ] && rm -f "$REPO_ENV"
    ln -sf "$VAULT_ENV" "$REPO_ENV"
elif [ ! -f "$VAULT_ENV" ] && [ ! -L "$REPO_ENV" ]; then
    echo "[update] WARN: Vault .env not found at $VAULT_ENV — iCloud may not have synced yet"
fi

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

# ── Unload legacy reflections launchd service (issue #748) ───────────
# The com.valor.reflections launchd service has been deleted (scripts/reflections.py
# monolith removed). Unload and remove the plist if still installed on this machine.
# Must source .env to read SERVICE_LABEL_PREFIX (not hardcoded to avoid prefix mismatch).
REFLECTIONS_LABEL="${SERVICE_LABEL_PREFIX}.reflections"
REFLECTIONS_DST="$HOME/Library/LaunchAgents/${REFLECTIONS_LABEL}.plist"
if [ -f "$REFLECTIONS_DST" ]; then
    if launchctl list | grep -q "$REFLECTIONS_LABEL"; then
        launchctl bootout "gui/$(id -u)/$REFLECTIONS_LABEL" 2>/dev/null || true
    fi
    rm -f "$REFLECTIONS_DST"
    echo "Removed legacy reflections launchd service: $REFLECTIONS_LABEL"
fi

# ── Sync config/reflections.yaml symlink ─────────────────────────────
# Vault file at ~/Desktop/Valor/reflections.yaml takes precedence over in-repo.
# Idempotent: skips gracefully if vault file doesn't exist (fresh machine).
"$PYTHON" -c "
from scripts.update.env_sync import sync_reflections_yaml
from pathlib import Path
result = sync_reflections_yaml(Path('$PROJECT_DIR'))
if result.skipped:
    print('reflections.yaml: vault not found, using in-repo fallback')
elif result.created:
    print('reflections.yaml: symlink created → ~/Desktop/Valor/reflections.yaml')
elif result.symlink_ok:
    print('reflections.yaml: symlink OK')
elif result.error:
    print(f'reflections.yaml: WARNING - {result.error}')
" 2>/dev/null || true

# Hard-pin legacy daydream cleanup to com.valor — that label only ever
# existed under the original prefix.
OLD_DAYDREAM_DST="$HOME/Library/LaunchAgents/com.valor.daydream.plist"
if launchctl list | grep -q "com.valor.daydream"; then
    launchctl bootout "gui/$(id -u)/com.valor.daydream" 2>/dev/null || true
    rm -f "$OLD_DAYDREAM_DST"
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
    if ! launchctl bootstrap "gui/$(id -u)" "$WORKER_DST"; then
        echo "ERROR: Failed to bootstrap $WORKER_LABEL"
    fi
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
