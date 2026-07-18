#!/bin/bash
# Remote update: pull latest code, sync deps if needed, restart the worker and
# bridge on relevant changes, then verify the running release matches HEAD.
# Designed to run unattended (from Telegram /update command or launchd cron).
# Note: run.py --cron writes the worker's deferred restart flag; this shell
# performs the actual kickstarts (issue #1898).
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

# ── Planned bridge-restart marker (issue #1898) ─────────────────────
# Written just before the bridge kickstart at the end of a bridge-relevant
# cycle; the watchdog suppresses crash-logging while it is fresh and the
# fresh bridge's boot self-check clears it. The 360s freshness window must
# stay in lockstep with UPDATE_RESTART_MARKER_TTL_SECONDS
# (scripts/update/service.py: STARTUP_GRACE_SECONDS + one watchdog cycle).
RESTART_MARKER="$PROJECT_DIR/data/update-restart-in-progress"
restart_marker_fresh() {
    [ -f "$RESTART_MARKER" ] || return 1
    local marker_age=$(( $(date +%s) - $(stat -f %m "$RESTART_MARKER" 2>/dev/null || echo 0) ))
    [ "$marker_age" -lt 360 ]
}
skip_locked() {
    if restart_marker_fresh; then
        echo "Update lock held — bridge restart in progress. Skipping."
    else
        echo "Another update is already running. Skipping."
    fi
    exit 0
}

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
            skip_locked
        fi
    else
        skip_locked
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
    ( set +o pipefail; cd "$PROJECT_DIR" && npm ci --omit=dev ) || echo "[update] npm ci failed (non-fatal); continuing"
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

# ── Sync config/reflections.yaml (real file copy) ────────────────────
# Vault file at ~/Desktop/Valor/reflections.yaml takes precedence over in-repo.
# Must be a REAL COPY, never a symlink — the launchd worker's reflection
# scheduler reads it, and a symlink to ~/Desktop hangs the asyncio event loop
# under launchd TCC (June 2026 worker wedge). Idempotent: skips gracefully if
# the vault file doesn't exist (fresh machine).
"$PYTHON" -c "
from scripts.update.env_sync import sync_reflections_yaml
from pathlib import Path
result = sync_reflections_yaml(Path('$PROJECT_DIR'))
if result.skipped:
    print('reflections.yaml: vault not found, using in-repo fallback')
elif result.created:
    print('reflections.yaml: copied from vault (was symlink or stale)')
elif result.ok:
    print('reflections.yaml: OK (real file copy)')
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

# ── Restart + verify state (issue #1898) ─────────────────────────────
# RESTART_FAILED: any worker/bridge kickstart failure — ORed into the terminal
# exit so a passing verify can never mask a failed restart.
# WORKER_STATE: per-process reload state staged into the pending report.
# VERIFY_SINCE: restart moment handed to the terminal verify's beacon poll
# (0 = nothing restarted this cycle, no poll).
RESTART_FAILED=0
WORKER_STATE="worker current"
VERIFY_SINCE=0

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

    # Liveness cross-check (#2141): `launchctl list | grep` can false-negative
    # while the worker is alive and still registered in the domain. The old
    # fallback branch then hit bootstrap-EIO and kickstart -k'd the LIVE
    # worker every cycle — bypassing the NEED_RESTART gate entirely (observed
    # as `[update] Worker restarted` on runs with BEFORE_SHA == AFTER_SHA).
    # A live worker process means "loaded" regardless of what the grep says.
    WORKER_ALIVE=false
    if pgrep -f "python -m worker" >/dev/null 2>&1; then
        WORKER_ALIVE=true
    fi
    if launchctl list | grep -q "$WORKER_LABEL" || $WORKER_ALIVE; then
        if $NEED_RESTART; then
            # Drain before restart (#2141): a PM turn legitimately runs 20+
            # minutes; killing it mid-turn discards the in-flight work and
            # orphans the harness. Poll until no sessions are running; on
            # timeout DEFER the restart to the next update cycle — the worker
            # keeps serving on the previously-deployed code (same posture the
            # bridge takes for config-validation failures). Exit 0 = idle or
            # probe failure (fail-open, warned on stderr); exit 3 = still busy.
            if "$PYTHON" -m scripts.update.drain \
                --timeout "${UPDATE_WORKER_DRAIN_TIMEOUT_S:-300}" \
                --poll "${UPDATE_WORKER_DRAIN_POLL_S:-10}"; then
                # Restart moment — handed to the terminal verify's bounded
                # beacon poll (Race 1 mitigation, issue #1898). Captured just
                # before the kickstart, after the drain window.
                RESTART_TS=$(date +%s)
                # Service is loaded — use kickstart -k to atomically kill+restart without
                # the bootout/bootstrap race condition (bootstrap error 5: label still registered).
                if launchctl kickstart -k "gui/$(id -u)/$WORKER_LABEL" 2>/dev/null; then
                    WORKER_STATE="worker restarted"
                    VERIFY_SINCE=$RESTART_TS
                else
                    # kickstart failed; fall back to bootout + bootstrap with a brief wait
                    launchctl bootout "gui/$(id -u)/$WORKER_LABEL" 2>/dev/null || true
                    sleep 2
                    if launchctl bootstrap "gui/$(id -u)" "$WORKER_DST"; then
                        WORKER_STATE="worker restarted"
                        VERIFY_SINCE=$RESTART_TS
                    else
                        # Distinct, scannable failure line + non-zero terminal exit.
                        # A swallowed `echo ERROR` here is the #1898 root-cause shape.
                        echo "RESTART FAILED: worker kickstart/bootstrap failed for $WORKER_LABEL"
                        WORKER_STATE="worker restart FAILED"
                        RESTART_FAILED=1
                    fi
                fi
            else
                echo "[update] Worker restart DEFERRED: running session(s) did not drain in ${UPDATE_WORKER_DRAIN_TIMEOUT_S:-300}s — retrying next cycle"
            fi
        else
            echo "[update] No worker-relevant changes detected — skipping restart"
        fi
    else
        # Label absent AND no live worker process (#2141 liveness cross-check
        # above) — the worker is genuinely down. Bootstrapping here is
        # RECOVERY of a dead service, not a restart: nothing is running, so
        # no drain is needed and the NEED_RESTART gate does not apply.
        # The grep can still false-negative in exotic cases; the bare
        # bootstrap then fails with `Bootstrap failed: 5: Input/output error`
        # (errno 5 = label already bootstrapped in target domain). EIO here
        # means the service IS loaded, so kickstart -k is the correct atomic
        # recovery. Only declare failure if BOTH the bootstrap and the
        # kickstart fallback fail. Capture bootstrap stderr so a recoverable
        # EIO stays out of the summary but the raw launchd error is available
        # to surface if both fail.
        RESTART_TS=$(date +%s)
        WORKER_UID=$(id -u)
        # `|| true` keeps a failing bootstrap from tripping `set -e` before we
        # can inspect BOOTSTRAP_RC and attempt the kickstart recovery below.
        BOOTSTRAP_ERR=$(launchctl bootstrap "gui/$WORKER_UID" "$WORKER_DST" 2>&1) && BOOTSTRAP_RC=0 || BOOTSTRAP_RC=$?
        if [ "$BOOTSTRAP_RC" -eq 0 ]; then
            WORKER_STATE="worker restarted"
            VERIFY_SINCE=$RESTART_TS
        elif launchctl kickstart -k "gui/$WORKER_UID/$WORKER_LABEL" 2>/dev/null; then
            # Bootstrap hit EIO because the label was already registered
            # (false-negative grep). kickstart -k reloads the running job onto
            # the freshly-written plist without the bootout/bootstrap race.
            WORKER_STATE="worker restarted"
            VERIFY_SINCE=$RESTART_TS
        else
            # Both failed — genuinely broken. Surface the launchd errno/message
            # (e.g. "Bootstrap failed: 5: Input/output error") so the failure is
            # diagnosable from the update summary, not just a generic line.
            echo "RESTART FAILED: worker bootstrap/kickstart failed for $WORKER_LABEL: ${BOOTSTRAP_ERR:-unknown launchd error}"
            WORKER_STATE="worker restart FAILED"
            RESTART_FAILED=1
        fi
    fi
fi

if [ "$WORKER_STATE" = "worker restarted" ]; then
    # Positive stdout marker: handle_update_command gates its bounded 15 x 2s
    # beacon poll on this line — a no-op /update must not burn the full poll
    # window waiting for a beacon that can never freshen.
    echo "[update] Worker restarted"
fi

# Log rotation is handled by the user-space log-rotate LaunchAgent
# (see scripts/log_rotate.py and com.valor.log-rotate.plist). The Python
# update pipeline installs it via service.install_log_rotate_agent() —
# no root/sudo required.

# ── Bridge restart decision (issue #1898) ────────────────────────────
# Computed BEFORE the terminal verify because the verify's scope flag depends
# on it: the deliberately-about-to-restart bridge must not be escalated as
# stale on the mainline success path. Restarting the bridge is safe — it
# holds no agent sessions (the worker is the sole session executor) and its
# Telethon catchup scan backfills any messages missed during the brief
# restart window. Gated on a bridge-relevant diff (mirrors the #1091 worker
# gate) and on the bridge plist being installed on this machine.
BRIDGE_LABEL="${SERVICE_LABEL_PREFIX}.bridge"
BRIDGE_DST="$HOME/Library/LaunchAgents/${BRIDGE_LABEL}.plist"
NEED_BRIDGE_RESTART=false
if [ "$BEFORE_SHA" != "$AFTER_SHA" ] && [ -f "$BRIDGE_DST" ]; then
    if git -C "$PROJECT_DIR" diff "$BEFORE_SHA" "$AFTER_SHA" -- \
        bridge/ agent/ mcp_servers/ models/ tools/ config/ \
        pyproject.toml | grep -q . ; then
        NEED_BRIDGE_RESTART=true
    fi
fi

# ── Terminal release verify (issue #1898) ────────────────────────────
# Runs on EVERY cycle — including no-op cron cycles with no new commits — so
# a starved/never-restarted process is re-classified every 30 minutes instead
# of silently reporting OK forever. --since polls (bounded 15 x 2s) for a
# fresh worker beacon after a kickstart (Race 1); --skip-bridge scopes the
# verify to the worker when the bridge is about to be deliberately restarted
# below. Exit is captured, never swallowed.
VERIFY_FAILED=0
VERIFY_ARGS=(--since "$VERIFY_SINCE")
if $NEED_BRIDGE_RESTART; then
    VERIFY_ARGS+=(--skip-bridge)
fi
if ! "$PYTHON" -m scripts.update.verify_release "${VERIFY_ARGS[@]}"; then
    VERIFY_FAILED=1
fi

# ── Stage the pending report for the fresh bridge (issue #1898) ──────
# A bridge kickstart SIGKILLs this shell (and handle_update_command) by
# process-group semantics, so the doomed process cannot report. When a
# Telegram chat context is present in the env (exported by
# handle_update_command), stage the report for the fresh bridge's boot flush.
# The pure 30-min cron path has no chat context and stages nothing.
if $NEED_BRIDGE_RESTART && [ -n "${UPDATE_REPORT_CHAT_ID:-}" ] && [ -n "${UPDATE_REPORT_REPLY_TO:-}" ]; then
    AFTER_SHORT=$(git -C "$PROJECT_DIR" rev-parse --short "$AFTER_SHA")
    # RESTART_FAILED / VERIFY_FAILED are both known at staging time; the fresh
    # bridge's boot flush must force a FAILED report when either bit is set —
    # otherwise a worker whose kickstart failed (or that crash-looped before
    # its beacon write) would flush a green OK (review blocker, PR #1914).
    printf '{"chat_id": "%s", "reply_to": "%s", "sha": "%s", "worker_state": "%s", "staged_ts": %s, "restart_failed": %s, "verify_failed": %s}\n' \
        "$UPDATE_REPORT_CHAT_ID" "$UPDATE_REPORT_REPLY_TO" "$AFTER_SHORT" \
        "$WORKER_STATE" "$(date +%s)" "$RESTART_FAILED" "$VERIFY_FAILED" \
        > "$PROJECT_DIR/data/update-pending-report"
    echo "[update] Staged update-pending-report for chat $UPDATE_REPORT_CHAT_ID"
fi

# ── Bridge kickstart LAST (issue #1898) ──────────────────────────────
# The kickstart SIGKILLs the bridge launchd job — and, by process-group
# semantics, this shell too when it was spawned by handle_update_command
# inside the bridge. It MUST therefore be the final act: nothing after a
# successful kickstart runs. Sequence: (a) planned-restart marker so the 60s
# watchdog does not log the deliberate restart as a crash; (b) release the
# lock while still alive (the EXIT trap never fires on SIGKILL — an orphaned
# lock would green-skip retries and the next cron cycle for up to 600s);
# (c) kickstart.
if $NEED_BRIDGE_RESTART; then
    echo "[update] Bridge-relevant changes detected — restarting bridge"
    date +%s > "$RESTART_MARKER"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! launchctl kickstart -k "gui/$(id -u)/$BRIDGE_LABEL" 2>/dev/null; then
        # Only reachable when the kickstart itself failed (a successful one
        # kills this shell): surface it loudly and withdraw the marker + the
        # staged report — no restart happened, the still-alive bridge's
        # handle_update_command reports inline.
        echo "RESTART FAILED: bridge kickstart failed for $BRIDGE_LABEL"
        RESTART_FAILED=1
        rm -f "$RESTART_MARKER" "$PROJECT_DIR/data/update-pending-report"
    fi
fi

# Terminal exit ORs both failure sources: a passing verify must never mask a
# kickstart failure (the #1898 root-cause shape), and vice versa.
exit $(( RESTART_FAILED || VERIFY_FAILED ))
