---
status: Implemented
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-02
tracking: https://github.com/tomcounsell/ai/issues/24
---

# Remote Update: Telegram Command + Auto-Sync Cron

## Problem

We update Valor's codebase almost daily, but changes only take effect on the machine where they're committed. The other 3 machines running Valor keep serving stale code until someone manually SSHs in or runs `/update` locally via Claude Code.

**Current behavior:**
- Push a fix to main on machine A
- Machines B, C, D keep running old code indefinitely
- To update: must either be physically at the machine or SSH in and run the `/update` Claude Code skill manually
- Easy to forget, machines drift

**Desired outcome:**
- Type `/update` in any Telegram group Valor monitors to trigger an immediate pull+restart on that machine
- A 12-hour cron ensures every machine catches up even if no one triggers it manually
- Both paths use the same update script

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

## Solution

### Key Elements

- **`scripts/remote-update.sh`**: Single shell script that does the essential update — git pull first (in bash, before Python loads), then delegates to the Python orchestrator. Does NOT restart the bridge itself. Stripped-down version of the Claude Code `/update` skill (no calendar config, no MCP checks, no CLI tool audit — those are setup concerns, not update concerns).
- **Bridge command intercept**: Before any message processing, check if the raw text is `/update`. Run the script, reply with the result. If code changed, queue a restart (don't restart immediately).
- **Queued restart**: Instead of killing the bridge mid-response, the update writes a restart flag file. The session queue worker checks for this flag between sessions and triggers a graceful restart only when idle.
- **Launchd cron plist**: A second launchd job that runs `remote-update.sh` every 12 hours. If code changed, it writes the restart flag. Independent of the bridge — runs even if the bridge is down (in that case, launchd's `KeepAlive` will restart the bridge with the new code anyway).

### Flow

**Manual trigger:**

Supervisor types `/update` in any monitored Telegram group → Bridge intercepts before `should_respond_async` → Runs `scripts/remote-update.sh` → Replies with result summary → If code changed, writes restart flag → Session queue worker picks up flag between sessions → Graceful restart when idle

**Automatic cron:**

Launchd fires every 12 hours → Runs `scripts/remote-update.sh` → Logs result to `logs/update.log` → If code changed, writes restart flag → Bridge picks up flag when idle and restarts (or if bridge is down, launchd's `KeepAlive` starts it with new code on next crash/reboot)

**Restart flag lifecycle:**

1. Update script (or bridge handler) creates `data/restart-requested` flag file
2. Session queue worker checks for flag after completing 
3. If flag exists and no jobs are running → trigger graceful shutdown via `SHUTTING_DOWN` + disconnect
4. Launchd's `KeepAlive` (or `valor-service.sh restart`) brings bridge back with new code
5. On startup, bridge deletes the flag file if still present

### projects.json validation gate

Before bouncing any services, the update orchestrator validates `~/Desktop/Valor/projects.json` against the single-machine-ownership rule (every bridge-contact identifier resolves to exactly one machine). Implemented as Step 4.6 in `scripts/update/run.py`:

```python
result.projects_json_check = verify.check_projects_json(project_dir)
if not result.projects_json_check.available:
    # Log the validator error and suppress the rest of Step 5
    config = replace(config, do_service_restart=False)
```

**On pass:** logs `projects.json: valid (N DM contacts, M groups, K email patterns)` and proceeds to restart.

**On fail:** logs the full validator error, records a warning, and suppresses the service restart for the rest of the run. The running bridge keeps serving on the previously-validated config until the operator fixes the file. A bad push fails *forward*: the new config is rejected, the old config keeps working.

Bridge code does *not* validate on its own startup — that would crash the live process when a bad config lands on disk via iCloud sync. The gate is exclusively in the update path. See [Single-Machine Ownership](single-machine-ownership.md) for the full validator scope.

### Technical Approach

#### 1. `scripts/remote-update.sh`

This is a **new standalone shell script** — not a wrapper around the existing `/update` Claude Code skill. The `/update` skill (`.claude/commands/update.md`) is a set of instructions for Claude to follow interactively (Ollama model check, SDK auth verification, calendar config, MCP audit, CLI tool verification). Those are `/setup`-level concerns that require Claude intelligence. This script handles the narrow, automatable subset: pull code, sync deps if needed, restart bridge.

```bash
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

# ── Lockfile (mkdir is atomic on POSIX) ──────────────────────────────
cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another update is already running. Skipping."
    exit 0
fi
trap cleanup_lock EXIT

# ── Git pull FIRST — before invoking any Python ──────────────────────
# Pull here so the Python orchestrator (run.py) and all update scripts are
# up to date before they execute. Without this, a Telegram /update or cron
# run always executes the pre-pull version of the orchestrator; changes to
# the update scripts only take effect on the next run.
# run.py --cron is then called with --no-pull to skip the redundant pull.
echo "[update] Pulling latest changes..."
if git -C "$PROJECT_DIR" pull --ff-only 2>&1; then
    echo "[update] Pull complete"
else
    echo "[update] WARN: git pull failed or had conflicts — continuing with current code"
fi

# ── Run update in cron mode ──────────────────────────────────────────
# Output goes directly to Telegram - keep it clean for PM-style summary
# --no-pull: git pull already done above; orchestrator skips its own pull step
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/update/run.py" --cron --no-pull
```

**Key design decisions:**

| Decision | Rationale |
|----------|-----------|
| `set -euo pipefail` | Strict mode — fail on any error, undefined var, or broken pipe |
| `mkdir`-based lockfile | Atomic on POSIX, no `flock` dependency. `trap EXIT` ensures cleanup even on failure |
| Pull in bash before Python | `run.py` and all update scripts are loaded from disk after the pull, so fixes to the update system take effect immediately (not on the next run). `run.py` receives `--no-pull` to skip the redundant internal pull |
| `--ff-only` | Prevents surprise merges. If branches diverged, fail loudly and let the operator handle it |
| Delegate to `run.py --cron` | Dep sync, restart flag, and Telegram summary logic live in the Python orchestrator (`scripts/update/run.py`). The shell script stays thin — pull + invoke |
| Exit 0 on "already up to date" | No restart flag, no dep sync, no noise. Cron is silent when nothing changed |
| Restart flag instead of immediate restart | The bridge may be mid-response. Writing a flag lets the bridge finish current work and restart when idle. Flag file contains timestamp + commit count for debugging |
| `$LOG_PREFIX` on every line | Makes output parseable when mixed into bridge logs or Telegram messages |

**What this script does NOT do** (handled by `/update` Claude Code skill or `/setup`):
- Ollama model checks/pulls
- SDK authentication verification
- Google Calendar config generation
- MCP server validation
- CLI tool verification (`gh`, `claude`, etc.)
- Virtual environment creation (assumes `.venv` exists)

#### 2. Bridge command intercept (`bridge/telegram_bridge.py`)

Insert the check at the very top of the message handler, after extracting `text` but **before** message storage, `should_respond_async`, media processing, or any other work:

```python
@client.on(events.NewMessage)
async def handler(event):
    if event.out:
        return
    if SHUTTING_DOWN:
        return

    message = event.message
    text = message.text or ""

    # === BRIDGE COMMANDS (bypass agent entirely) ===
    if text.strip().lower() == "/update":
        await _handle_update_command(client, event)
        return

    # ... rest of existing handler
```

The `_handle_update_command` function:
```python
async def _handle_update_command(client, event):
    """Run remote update script and reply with results.

    The script pulls code and syncs deps but does NOT restart the bridge.
    If code changed, it writes a restart flag that the session queue picks up
    between sessions for a graceful restart when idle.
    """
    await set_reaction(client, event.chat_id, event.message.id, REACTION_RECEIVED)

    try:
        result = subprocess.run(
            ["bash", "scripts/remote-update.sh"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        # Truncate if too long for Telegram
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await client.send_message(event.chat_id, output, reply_to=event.message.id)
    except subprocess.TimeoutExpired:
        await client.send_message(
            event.chat_id, "Update timed out after 120s", reply_to=event.message.id
        )
    except Exception as e:
        await client.send_message(
            event.chat_id, f"Update failed: {e}", reply_to=event.message.id
        )
```

Why intercept before everything:
- `/update` is infrastructure, not a conversation. It shouldn't be stored in message history, classified by Ollama, or processed for media/links.
- Keeps the command handling simple and isolated.

Authorization: Any message in a monitored group works. The bridge already only listens to groups configured in `ACTIVE_PROJECTS`. If someone outside those groups types `/update`, the bridge never sees it. No additional auth needed.

#### 2b. Queued restart (`agent/agent_session_queue.py` + `bridge/telegram_bridge.py`)

The update script writes `data/restart-requested` instead of calling `valor-service.sh restart`. The bridge picks this up when idle.

**Session queue worker — check after each session completes:**

```python
# In agent/agent_session_queue.py, after session completion (in the worker loop)

RESTART_FLAG = Path(PROJECT_DIR) / "data" / "restart-requested"

def _check_restart_flag(project_key: str) -> bool:
    """Check if a restart has been requested and no jobs are running."""
    if not RESTART_FLAG.exists():
        return False

    # Don't restart if other jobs are still running
    running = AgentSession.query.filter(project_key=project_key, status="running")
    if running:
        logger.info(f"[{project_key}] Restart requested but {len(running)} session(s) still running — deferring")
        return False

    # Also check all projects, not just this one
    for pkey in ACTIVE_PROJECTS:
        if pkey == project_key:
            continue
        other_running = AgentSession.query.filter(project_key=pkey, status="running")
        if other_running:
            logger.info(f"[{project_key}] Restart requested but {pkey} has running sessions — deferring")
            return False

    flag_content = RESTART_FLAG.read_text().strip()
    logger.info(f"[{project_key}] Restart flag found ({flag_content}), no running sessions — restarting bridge")
    return True
```

**Bridge startup — clean up stale flag:**

```python
# In bridge startup, after connecting to Telegram
restart_flag = Path(PROJECT_DIR) / "data" / "restart-requested"
if restart_flag.exists():
    restart_flag.unlink()
    logger.info("Cleared stale restart flag from previous update")
```

**Triggering the actual restart:**

When `_check_restart_flag()` returns True, the worker calls:

```python
import os, signal

def _trigger_restart():
    """Trigger graceful bridge restart by sending SIGTERM to self."""
    RESTART_FLAG.unlink(missing_ok=True)
    logger.info("Triggering graceful restart...")
    os.kill(os.getpid(), signal.SIGTERM)
    # SIGTERM is caught by the existing _shutdown_handler which sets SHUTTING_DOWN=True
    # and calls _graceful_shutdown(). Launchd KeepAlive restarts the process.
```

**Why this approach:**
- No mid-response interruption — restart only happens between sessions
- Uses existing graceful shutdown machinery (`SIGTERM` → `_shutdown_handler` → `_graceful_shutdown`)
- Launchd `KeepAlive` brings the bridge back automatically with new code
- Flag file is simple, no Redis dependency, survives bridge crashes
- Stale flag cleanup on startup prevents restart loops

**Edge case — cron fires but bridge is down:**
- `remote-update.sh` writes the flag and exits. No bridge to restart.
- When launchd eventually restarts the bridge (via `KeepAlive`), it loads the new code anyway.
- Startup cleans up the stale flag. No unnecessary second restart.

#### 3. Launchd cron plist

Install alongside the existing `com.valor.bridge` plist:

**Plist: `com.valor.update`**
```xml
<key>StartCalendarInterval</key>
<array>
    <dict><key>Hour</key><integer>6</integer></dict>
    <dict><key>Hour</key><integer>18</integer></dict>
</array>
```

Runs at 06:00 and 18:00 local time. Logs to `logs/update.log`.

Install/uninstall via `valor-service.sh`:
- `valor-service.sh install` installs both plists (bridge + update cron)
- `valor-service.sh uninstall` removes both

## Rabbit Holes & Risks

### Risk 1: Bridge restarts itself mid-response (RESOLVED by design)
**Impact:** If the bridge is mid-response to a user message when `/update` triggers a restart, that response is lost.
**Mitigation:** Eliminated by design. The update script no longer restarts the bridge directly. It writes a restart flag file. The session queue worker checks the flag between sessions and only triggers a restart when all projects have no running sessions. The bridge finishes its current work before restarting. Worst case: if the bridge is perpetually busy, the restart is deferred until a quiet moment. A very long-running session (2+ hours) would delay the update — acceptable tradeoff vs losing a response.

### Risk 2: Cron and manual trigger race
**Impact:** If someone types `/update` at the same moment the cron fires, two `git pull` + restart sequences run simultaneously.
**Mitigation:** Use a lockfile in `remote-update.sh`. `flock` or a simple `mkdir`-based lock. If lock is held, exit 0 with "Update already in progress."

### Risk 3: `git pull --ff-only` fails on dirty working tree
**Impact:** If the machine has uncommitted local changes (e.g., from a running agent session), `git pull` fails.
**Mitigation:** The script auto-stashes before pulling and pops after (matching the existing `/update` Claude Code skill behavior). If stash pop has conflicts, the code is still updated but the warning is surfaced. Agent sessions commit and push before completing, so dirty trees should be rare. Worst case: operator sees "stash pop conflict" in the output and resolves manually.

### Risk 4: `uv` not installed on all machines
**Impact:** New machines might not have `uv` yet.
**Mitigation:** Fall back to `pip install -e .` if `uv` is not found. Add a check at the top of the script.

## No-Gos (Out of Scope)

- **Full `/update` skill parity** — No calendar config, MCP validation, CLI audit, or Ollama model checks. Those are `/setup` concerns, not daily update concerns.
- **Rollback capability** — If an update breaks something, fix forward or manually `git checkout` on the machine. Automatic rollback adds complexity beyond the appetite.
- **Cross-machine coordination** — No waiting for all machines to update, no versioning, no deployment orchestration. Each machine independently pulls main.
- **Redis pubsub** — Deferred. Requires shared Redis infrastructure that doesn't exist yet. Worth revisiting when we need real-time cross-machine coordination.
- **Other bridge commands** — Only `/update` for now. If we add more later (e.g., `/status`, `/restart`), we'll extract a command handler registry. One command doesn't justify the abstraction.

## Success Criteria

- [ ] `/update` in any monitored Telegram group triggers git pull + dep sync
- [ ] Bridge replies with update result (commits pulled, or "already up to date")
- [ ] If code changed, restart flag is written; bridge restarts only after current sessions complete
- [ ] No restart if already up to date (no flag, no bounce)
- [ ] Cron runs every 12 hours and updates if new commits exist on main
- [ ] Lockfile prevents concurrent update runs
- [ ] Restart flag cleaned up on startup (no restart loops)
- [ ] `valor-service.sh install` installs both bridge and update cron plists
- [ ] Works on all 4 machines without machine-specific configuration

## Files to Modify

| File | Change |
|------|--------|
| `scripts/remote-update.sh` | **NEW** — Core update script (pull, sync deps, write restart flag) |
| `bridge/telegram_bridge.py` | Add `/update` command intercept at top of handler; clean restart flag on startup |
| `agent/agent_session_queue.py` | Add `_check_restart_flag()` after session completion; `_trigger_restart()` via SIGTERM |
| `scripts/valor-service.sh` | Add update cron plist to `install`/`uninstall` commands |
| `tests/test_remote_update.py` | **NEW** — Test script output parsing, bridge intercept, restart flag lifecycle |
