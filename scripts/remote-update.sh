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
# iCloud-synced .env causes bash-level EINTR that aborts the shell before || can catch it.
# Fix: cp to a temp file first (cp handles EINTR at the C level), then source the local copy.
if [ -f "$PROJECT_DIR/.env" ]; then
    _env_tmp=$(mktemp /tmp/valor-env-XXXXXX)
    if cp "$PROJECT_DIR/.env" "$_env_tmp" 2>/dev/null; then
        source "$_env_tmp" 2>/dev/null || echo "[update] WARN: .env source failed, using defaults"
    else
        echo "[update] WARN: .env copy interrupted (iCloud sync?), using defaults"
    fi
    rm -f "$_env_tmp"
fi
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

# ── Git pull FIRST — before invoking any Python ──────────────────────
# Pull here so the Python orchestrator (run.py) and all update scripts are
# up to date before they execute. Without this, a Telegram /update or cron
# run always executes the pre-pull version of the orchestrator; changes to
# the update scripts only take effect on the next run.
# run.py --cron is then called with --no-pull to skip the redundant pull.
# Capture SHA before pull so we can detect whether code actually changed.
BEFORE_SHA=$(git -C "$PROJECT_DIR" rev-parse HEAD)

echo "[update] Pulling latest changes..."
if git -C "$PROJECT_DIR" pull --ff-only 2>&1; then
    echo "[update] Pull complete"
else
    echo "[update] WARN: git pull failed or had conflicts — continuing with current code"
fi

AFTER_SHA=$(git -C "$PROJECT_DIR" rev-parse HEAD)

# ── Check for Python venv ────────────────────────────────────────────
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "ERROR: No Python venv at $PYTHON"
    echo "Run: uv venv && uv sync --all-extras"
    exit 1
fi

# ── Sync design-system Node toolchain (soft dep) ─────────────────────
# @google/design.md pinned in package.json powers `python -m tools.design_system_sync`.
# Guarded: requires BOTH package.json AND `npm` on PATH. Wrapped in a
# non-pipefail subshell so a missing npm or a transient install failure
# cannot abort the parent update script.
if [ -f "$PROJECT_DIR/package.json" ] && command -v npm >/dev/null 2>&1; then
    ( set +o pipefail; cd "$PROJECT_DIR" && npm ci --only=prod ) || echo "[update] npm ci failed (non-fatal); continuing"
fi

# ── Run update in cron mode ──────────────────────────────────────────
# Output goes directly to Telegram - keep it clean for PM-style summary
# --no-pull: git pull already done above; orchestrator skips its own pull step
"$PYTHON" "$PROJECT_DIR/scripts/update/run.py" --cron --no-pull

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
# Only restart the worker when the pull actually landed new commits that touch
# worker-loaded code.  This prevents killing in-flight sessions every 30 minutes
# when com.valor.update runs on a timer (issue #1091).
WORKER_PLIST="$PROJECT_DIR/com.valor.worker.plist"
WORKER_LABEL="${SERVICE_LABEL_PREFIX}.worker"
WORKER_DST="$HOME/Library/LaunchAgents/${WORKER_LABEL}.plist"
if [ -f "$WORKER_PLIST" ] && [ -f "$WORKER_DST" ]; then
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$WORKER_LABEL|g" "$WORKER_PLIST" > "$WORKER_DST"

    # Inject .env vars into EnvironmentVariables so launchd-spawned worker
    # processes see VALOR_PROJECT_KEY and other secrets (issue #1171). Without
    # this, the cron-driven /update path produces a worker plist with only the
    # template placeholders (PATH/HOME/VALOR_LAUNCHD), breaking the recovery
    # reflections' project_key namespace alignment. Mirrors the injection block
    # in scripts/install_worker.sh and scripts/update/service.py::install_worker.
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        PROJECT_DIR="$PROJECT_DIR" PLIST_DST="$WORKER_DST" \
            "$PROJECT_DIR/.venv/bin/python" - <<'PYEOF' || echo "[update] WARNING: worker .env injection failed; plist unchanged"
import os, sys, plistlib
from pathlib import Path
try:
    from dotenv import dotenv_values
except Exception as e:
    print(f"[update] dotenv unavailable ({e}); skipping worker .env injection", file=sys.stderr)
    sys.exit(0)
project_dir = Path(os.environ["PROJECT_DIR"])
plist_dst = Path(os.environ["PLIST_DST"])
env_file = project_dir / ".env"
if not env_file.exists() or not plist_dst.exists():
    sys.exit(0)
try:
    env_vars = dotenv_values(env_file)
    with open(plist_dst, "rb") as f:
        plist = plistlib.load(f)
    existing = plist.setdefault("EnvironmentVariables", {})
    injected = 0
    for key, value in env_vars.items():
        if key not in existing and value is not None:
            existing[key] = value
            injected += 1
    with open(plist_dst, "wb") as f:
        plistlib.dump(plist, f)
    print(f"[update] Injected {injected} env vars into worker plist")
except Exception as e:
    print(f"[update] worker .env injection error: {e}", file=sys.stderr)
PYEOF
    fi

    NEED_RESTART=false
    if [ "$BEFORE_SHA" != "$AFTER_SHA" ]; then
        # Check whether the diff touches directories/files the worker loads.
        if git -C "$PROJECT_DIR" diff "$BEFORE_SHA" "$AFTER_SHA" -- \
            worker/ agent/ mcp_servers/ models/ tools/ bridge/ reflections/ \
            pyproject.toml | grep -q . ; then
            NEED_RESTART=true
        fi
    fi

    if launchctl list | grep -q "$WORKER_LABEL"; then
        if $NEED_RESTART; then
            # Service is loaded — use kickstart -k to atomically kill+restart without
            # the bootout/bootstrap race condition (bootstrap error 5: label still registered).
            if ! launchctl kickstart -k "gui/$(id -u)/$WORKER_LABEL" 2>/dev/null; then
                # kickstart failed; fall back to bootout + bootstrap with a brief wait
                launchctl bootout "gui/$(id -u)/$WORKER_LABEL" 2>/dev/null || true
                sleep 2
                if ! launchctl bootstrap "gui/$(id -u)" "$WORKER_DST"; then
                    echo "ERROR: Failed to bootstrap $WORKER_LABEL"
                fi
            fi
        else
            echo "[update] No worker-relevant changes detected — skipping restart"
        fi
    else
        # Service not yet loaded (first install) — always bootstrap.
        if ! launchctl bootstrap "gui/$(id -u)" "$WORKER_DST"; then
            echo "ERROR: Failed to bootstrap $WORKER_LABEL"
        fi
    fi
fi

# Log rotation is handled by the user-space log-rotate LaunchAgent
# (see scripts/log_rotate.py and com.valor.log-rotate.plist). The Python
# update pipeline installs it via service.install_log_rotate_agent() —
# no root/sudo required.
