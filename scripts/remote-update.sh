#!/bin/bash
# Remote update: pull latest code, sync deps if needed, write restart flag.
# Designed to run unattended (from Telegram /update command or launchd cron).
# NOT a replacement for the /update Claude Code skill — this handles only
# the automatable subset (no Ollama, no calendar, no MCP, no CLI audit).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOCK_DIR="$PROJECT_DIR/data/update.lock"
LOG_PREFIX="[remote-update]"

cd "$PROJECT_DIR"

# Ensure data directory exists
mkdir -p "$PROJECT_DIR/data"

# ── Lockfile (mkdir is atomic on POSIX) ──────────────────────────────
cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$LOG_PREFIX Another update is already running. Skipping."
    exit 0
fi
trap cleanup_lock EXIT

# ── Pre-flight: check for dirty working tree ─────────────────────────
STASHED=false
if [ -n "$(git status --porcelain)" ]; then
    echo "$LOG_PREFIX WARN: Dirty working tree detected."
    git status --porcelain | head -5 | while read -r line; do
        echo "$LOG_PREFIX   $line"
    done
    echo "$LOG_PREFIX Attempting git stash before pull..."
    STASHED=true
    git stash push -m "remote-update auto-stash $(date +%Y%m%d-%H%M%S)" >/dev/null 2>&1
    echo "$LOG_PREFIX Stashed successfully."
fi

# ── Git pull ─────────────────────────────────────────────────────────
BEFORE=$(git rev-parse HEAD)
PULL_OUTPUT=$(git pull --ff-only 2>&1) || {
    echo "$LOG_PREFIX FAIL: git pull --ff-only failed (branches diverged?)"
    echo "$LOG_PREFIX Current HEAD: $(git rev-parse --short HEAD)"
    echo "$LOG_PREFIX Remote HEAD: $(git rev-parse --short origin/main 2>/dev/null || echo 'unknown')"
    echo "$LOG_PREFIX Output: $PULL_OUTPUT"
    if [ "$STASHED" = true ]; then
        echo "$LOG_PREFIX Restoring stash..."
        git stash pop >/dev/null 2>&1 || echo "$LOG_PREFIX WARN: stash pop failed, changes in git stash list"
    fi
    exit 1
}
AFTER=$(git rev-parse HEAD)

# ── Restore stash if we stashed ──────────────────────────────────────
if [ "$STASHED" = true ]; then
    echo "$LOG_PREFIX Restoring stashed changes..."
    git stash pop >/dev/null 2>&1 || echo "$LOG_PREFIX WARN: stash pop conflict, changes remain in git stash list"
fi

# ── Check if anything changed ────────────────────────────────────────
if [ "$BEFORE" = "$AFTER" ]; then
    echo "$LOG_PREFIX Already up to date. ($(git rev-parse --short HEAD))"
    exit 0
fi

# ── Report what changed ──────────────────────────────────────────────
COMMIT_COUNT=$(git rev-list --count "$BEFORE..$AFTER")
echo "$LOG_PREFIX Pulled $COMMIT_COUNT commit(s):"
git log --oneline "$BEFORE..$AFTER" | while read -r line; do
    echo "$LOG_PREFIX   $line"
done
echo "$LOG_PREFIX"

# ── Sync dependencies (only if pyproject.toml or uv.lock changed) ───
CHANGED_FILES=$(git diff --name-only "$BEFORE..$AFTER")
if echo "$CHANGED_FILES" | grep -qE "^(pyproject\.toml|uv\.lock)$"; then
    echo "$LOG_PREFIX pyproject.toml or uv.lock changed — syncing dependencies..."
    if command -v uv &>/dev/null; then
        uv sync --all-extras 2>&1
        echo "$LOG_PREFIX Dependencies synced via uv."
    elif [ -f "$PROJECT_DIR/.venv/bin/pip" ]; then
        echo "$LOG_PREFIX uv not found, falling back to pip..."
        "$PROJECT_DIR/.venv/bin/pip" install -e "$PROJECT_DIR" 2>&1
        echo "$LOG_PREFIX Dependencies synced via pip."
    else
        echo "$LOG_PREFIX WARN: Neither uv nor pip found. Dependencies NOT synced."
    fi
else
    echo "$LOG_PREFIX No dependency file changes — skipping dep sync."
fi

# ── Signal bridge to restart when idle ────────────────────────────────
# Don't restart immediately — the bridge may be mid-response.
# Write a flag file; the job queue worker checks this between jobs
# and triggers a graceful restart only when no jobs are running.
RESTART_FLAG="$PROJECT_DIR/data/restart-requested"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $COMMIT_COUNT commit(s)" > "$RESTART_FLAG"
echo "$LOG_PREFIX Restart queued (flag written to data/restart-requested)."
echo "$LOG_PREFIX Bridge will restart after current work completes."

echo "$LOG_PREFIX"
echo "$LOG_PREFIX Update complete. $COMMIT_COUNT commit(s) pulled, restart queued."
echo "$LOG_PREFIX HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"
