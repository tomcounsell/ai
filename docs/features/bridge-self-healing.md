# Bridge Self-Healing & Resilience

The bridge includes a multi-layered self-healing system to recover from crashes without manual intervention.

## Import-Time Safety

**Problem**: `TELEGRAM_API_ID` set to a non-numeric value (e.g. the `.env.example` placeholder `12345****`) used to cause a `ValueError` at module import time. This crashed the bridge before any logging or graceful error handling could run, and trapped the watchdog in a restart loop because every restart attempt would also fail during import.

**Solution**: The `_parse_api_id()` helper in `bridge/telegram_bridge.py` wraps the `int()` conversion and returns `0` on any invalid or missing input, logging a warning to stderr. Module import now always succeeds regardless of env contents. The existing runtime credential check (`if not API_ID or not API_HASH`) remains the authoritative "fail loudly and exit" path once the bridge actually tries to connect.

The same defensive `try/except ValueError` pattern was applied to `tools/valor_telegram.py` where lazy `int(os.environ.get(...))` calls existed inside functions.

The same failure class — a raise at import time trapping the watchdog in a restart loop — also applied to `projects.json` parsing; see Component 19 (Guarded Config Read) below for the fix.

## Components

### 1. Session Lock Cleanup (`bridge/telegram_bridge.py`)

**Problem**: Stale processes from prior restarts can block the bridge from starting.

**Solution**: Before attempting to connect, the bridge:
1. Uses `lsof` to find processes holding session-related files
2. Terminates stale processes (>60 seconds old) that aren't the current process using SIGTERM/SIGKILL escalation:
   - Sends SIGTERM first to request graceful shutdown
   - Waits up to 5 seconds for the process to exit
   - Falls back to SIGKILL only if the process is still alive
3. Clears orphaned lock/journal files
4. Adds jitter to prevent thundering herd on restart

**Retry Logic**: General connection retry with exponential backoff and jitter (2s to 256s cap, 8 attempts max). Covers all Telethon errors, not just SQLite locks. See [Bridge Resilience](bridge-resilience.md) for details.

### 2. Crash Tracker (`monitoring/crash_tracker.py`)

Logs bridge start/crash events with:
- Timestamp
- Current git commit SHA
- Commit age in seconds
- Crash reason (if available)

Events are stored in Redis via the crash tracker module. Previous JSONL file (`data/crash_history.jsonl`) was replaced as part of the Redis migration (2026-02-24).

**Pattern Detection**: Identifies when 3+ crashes occur within 30 minutes after a recent commit (<1 hour old), suggesting code-caused crashes.

**Usage**:
```python
from monitoring.crash_tracker import log_start, log_crash, detect_crash_pattern

# Log events
log_start()
log_crash("connection lost")

# Check for patterns
should_revert, commit_sha = detect_crash_pattern()
```

### 3. Bridge Watchdog (`monitoring/bridge_watchdog.py`)

A separate process that monitors bridge health and executes recovery. Runs via launchd every 60 seconds.

**Health Checks**:
- Process running (`pgrep -f telegram_bridge.py`)
- Logs fresh (written within 5 minutes)
- No crash pattern detected
- Zombie process detection (claude/pyright processes idle > 2 hours)
- Concurrent instance count (warns when exceeding soft limit of 5)

**Zombie Process Detection**:

Claude Code CLI subprocesses can become orphaned when their parent session ends abnormally (timeout, crash, network disconnect). These zombie processes persist indefinitely, accumulating memory pressure. The watchdog detects them using `ps -eo pid,etime,rss,command` and classifies processes as zombies when their elapsed time exceeds `ZOMBIE_THRESHOLD_SECONDS` (default: 7200 = 2 hours).

- `_enumerate_claude_processes()` scans for all `claude` and `pyright` processes system-wide
- `classify_zombies()` separates zombies from active processes based on elapsed time
- `kill_zombie_processes()` uses SIGTERM with 3-second grace period, escalating to SIGKILL
- Active instance count is tracked; a warning is logged when it exceeds `SOFT_INSTANCE_LIMIT` (default: 5)

The `--check-only` output includes zombie count, PIDs, memory usage, and active instance count.

**5-Level Recovery Escalation**:

| Level | Condition | Action |
|-------|-----------|--------|
| 1 | Process not running | Log crash event via `crash_tracker.log_crash("bridge_dead_on_watchdog_check")` + simple restart (launchd) |
| 2 | Process running but logs stale — or update loop wedged | Kill stale + kill zombies + restart (the bridge always catches up missed messages on startup — see below) |
| 3 | Lock files present | Kill stale + kill zombies + clear locks + restart |
| 4 | Crash pattern detected | Kill stale + kill zombies + revert HEAD + restart (if enabled) |
| 5 | Recovery exhausted | Alert human via Telegram |

Zombie cleanup is integrated into recovery levels 2+ to free memory before restarting.

### 3a. Update-Loop Wedged Detector (issue #1712)

**Problem**: Telethon can stop delivering `NewMessage` events silently — the bridge process is alive, TCP is connected (the reconciler's `get_dialogs()` succeeds), but the update loop has stopped firing. No error, no disconnect, no log. Messages are silently dropped until the bridge is manually restarted.

**Solution**: Two positive liveness signals written to Redis, read by the watchdog on every 60-second tick:

| Redis Key | Writer | Meaning |
|-----------|--------|---------|
| `bridge:last_update_received` | NewMessage handler in `bridge/telegram_bridge.py`, before dedup | A Telethon update event was delivered to the bridge |
| `bridge:last_probe_ok` | Reconciler in `bridge/reconciler.py`, after successful `get_dialogs()` | The Telegram API/TCP layer is reachable |

Both keys are managed by `bridge/liveness.py` (freeform Redis keys, not Popoto-managed; raw get/set is correct). Both writers are best-effort — any exception logs a WARNING and never raises, matching the safety contract from `bridge.dedup.record_last_event`.

**Detection logic** (`assess_update_flow()` in `monitoring/bridge_watchdog.py`):

The PRIMARY rule fires when all four conditions are true simultaneously:
1. Bridge process is alive
2. `bridge:last_probe_ok` is fresh (API/TCP layer is healthy)
3. `bridge:last_update_received` is older than `UPDATE_STALENESS_CEILING` (or absent)
4. Bridge is past the startup grace window (`STARTUP_GRACE_SECONDS`)

When these conditions hold, `last_probe_ok` being fresh rules out the simple disconnect case — the API layer is up, but Telethon has stopped delivering events. This is the wedge signature.

A SECONDARY accelerator fires at `UPDATE_STALENESS_WARN` (before the ceiling) to give an earlier signal on clearly active bridges.

**Key design decisions**:
- **PRIMARY ceiling-based trigger**: no per-chat precondition. The ceiling fires regardless of whether any specific group has seen traffic. This avoids false negatives on quiet-but-monitored bridges.
- **`last_probe_ok` as disconfirmation guard**: if the probe itself is stale, the bridge may be disconnected. A disconnect should be recovered by level 1 (process dead) or resolved by Telethon's reconnect — not treated as a wedge. Restarting on disconnect when Telethon is mid-reconnect would interrupt the reconnection attempt. The wedge detector only fires when probe is fresh.
- **Startup grace window**: `bridge:last_update_received` is absent on cold start (bridge has not received any messages yet). The grace window prevents false wedge verdicts during startup before Telegram delivers the first event.
- **`None` process-start = fail-safe**: if `get_bridge_process_start_ts()` returns `None` (process info unavailable), the detector treats the verdict as inconclusive and suppresses the restart. This avoids a restart based on incomplete information.

**Recovery**: when `update_flow_live=False`, the watchdog sets `recovery_level = max(recovery_level, 2)` and calls the standard `restart_bridge()` (a `launchctl kickstart` — it takes no arguments). The level cap of 2 is hard — the wedge detector never escalates to level 4 (auto-revert), regardless of how many consecutive wedge ticks occur. Lossless backfill is inherent to bridge startup, not a flag the watchdog passes: the bridge unconditionally initializes Telethon with `catch_up=True` and runs a missed-message catchup scan on every connect, so any restart recovers the messages that arrived during the wedge window.

**Log signals**:
```
[WARNING] bridge_update_loop_wedged: update loop stopped delivering events while process is running and API layer is healthy. Issue: update loop wedged: last_update_received=2.3h ago, last_probe_ok=2m ago — Telethon stopped delivering events while API layer is healthy
```

**Observable via**:
```bash
python monitoring/bridge_watchdog.py --check-only
# Output includes: Update flow live: True/False
```

**Auto-Revert** (Level 4):
- Disabled by default
- Enable: `touch data/auto-revert-enabled`
- Creates a git revert commit and pushes to remote
- Sends Telegram alert about the revert

### 4. Session Watchdog Duplicate Key Guard (`monitoring/session_watchdog.py`)

**Problem**: The session watchdog calls `session.save()` in `fix_unhealthy_session()` to mark stuck sessions as abandoned. When a session has been concurrently deleted or modified by another process, popoto raises a `ModelException` (duplicate key / unique constraint violation). These errors accounted for 98% of all error log entries (~22,400 occurrences).

**Solution**: The `_safe_abandon_session()` helper wraps each `session.save()` call in `fix_unhealthy_session()` with a `ModelException` catch. When the save fails due to a stale/duplicate key:
1. The error is logged at WARNING level (visible in `bridge.log` for monitoring, but not spamming `bridge.error.log`)
2. The watchdog continues processing the next session instead of propagating the error up to the loop-level handler
3. The outer `check_all_sessions()` still has a `ModelException` catch as a safety net for any other save paths

This is distinct from the loop-level crash guard (which marks sessions as `failed`). The `_safe_abandon_session()` helper handles the common case of race conditions during the abandon flow itself.

### 4a. User-Visible Stall Alerts (`monitoring/session_watchdog.py`, issue #1313)

**Problem**: When `check_stalled_sessions()` detected a stalled session, it logged a `LIFECYCLE_STALL` warning to `worker.log` and did nothing else. The user (CEO) saw silence on Telegram and assumed "agent is thinking." Silent failure compounded short outages into long ones because no human-visible signal triggered an investigation.

**Solution**: After the existing `LIFECYCLE_STALL` warning fires, the watchdog also calls `_apply_stall_reaction(session)`, which queues a ⏳ reaction emoji on the user's originating Telegram message. The bridge's existing `bridge/telegram_relay.py::_send_queued_reaction` drain delivers it on the next poll. The warning log is preserved unchanged — the reaction is an *additional* user-visible channel.

**How the queueing works**:
- The watchdog stays Telethon-free. It writes a reaction payload (`type: "reaction"`, `chat_id`, `reply_to`, `emoji: "⏳"`, `session_id`, `timestamp`) directly to `telegram:outbox:{session_id}` via `RPUSH` + `EXPIRE` (3600s TTL, matches `OutputHandler.OUTBOX_TTL`).
- The payload schema is byte-for-byte identical to `agent/output_handler.py::_build_reaction_payload`. A unit test (`test_payload_matches_build_reaction_payload`) enforces this so any drift fails CI.
- The bridge relay drains the same outbox key on its normal poll loop and calls `set_reaction` over Telethon.

**Idempotency**:
- A single atomic `SET NX EX` on `watchdog:stall_reaction_applied:{session_id}` (TTL = 1 day, `STALL_REACTION_DEDUP_TTL`) ensures exactly one reaction per stall period. Same shape as the per-reason cooldowns from issue #1128.
- When the next watchdog tick observes the session in a healthy (non-stall) state, the dedup key is `DELETE`d so a re-stall queues a fresh reaction. There is a ≤5-minute window where ⏳ can briefly persist after recovery before the next tick clears the dedup; the user will see the bot's recovery message land before the reaction is reset, so this is acceptable.

**Skip conditions** (return False, no Redis writes, no log spam):
- `WATCHDOG_STALL_REACTION_ENABLED` env var is set falsy (`0`, `false`, `no`). Default is on.
- Session has no `chat_id` (e.g. local Claude Code sessions, no Telegram origin).
- Session has no `telegram_message_id` (originating message not captured).
- Session has no resolvable `session_id`/`agent_session_id`.

**Failure modes**:
- Redis exception → fail-quiet `logger.warning`, watchdog loop continues.
- Bridge relay down longer than `OUTBOX_TTL` → outbox key expires, reaction lost. The warning log still fires, and the next tick re-queues once the bridge returns and the dedup key TTL expires. A bridge-down >TTL is a bigger-than-watchdog incident.
- ⏳ not in Telegram's allowed reactions for a chat → the relay's `set_reaction` already handles unknown-emoji failure (logs and moves on). Swap `STALL_REACTION_EMOJI = "⚠️"` is a one-line change.

**Configuration**:
- `WATCHDOG_STALL_REACTION_ENABLED`: default on. Set to `0`/`false`/`no` to disable, mirror of `WATCHDOG_AUTO_STEER_ENABLED`.
- Constants live at the top of `monitoring/session_watchdog.py` near `STEER_COOLDOWN`: `STALL_REACTION_EMOJI`, `STALL_REACTION_DEDUP_TTL`, `STALL_REACTION_OUTBOX_TTL`.

### 5. Log Rotation

Log rotation uses a three-layer approach: Python-managed rotation for application logs, shell rotation at service startup for launchd-managed stderr/stdout logs, and a user-space LaunchAgent for between-restart coverage. See [Log Rotation](log-rotation.md) for the full design.

**Python-managed logs** (auto-rotate on write via `RotatingFileHandler`, 10MB max, 5 backups):
- `bridge.log` — configured in `bridge/telegram_bridge.py`
- `watchdog.log` — configured in `monitoring/bridge_watchdog.py`

**Shell-rotated logs** (`rotate_log()` in `valor-service.sh`, runs at bridge startup, 10MB max, 3 backups):
- `bridge.error.log`, `reflections_error.log`

**User-space LaunchAgent safety net** (`com.valor.log-rotate.plist` + `scripts/log_rotate.py`): runs every 30 minutes under the user's launchd session and rotates any `logs/*.log` file over 10 MB (3 backups retained). Covers all launchd-managed logs between service restarts — no root needed. Replaces the previous newsyslog config that required `sudo` to install.

### 6. Startup Redis Key Cleanup (`worker/__main__.py`)

**Problem**: Stale Redis entries with non-standard 60-character `agent_session_id` keys (from historical data or crashes) trigger popoto validation errors on every query scan, generating thousands of error log entries.

**Solution**: On worker startup, `AgentSession.rebuild_indexes()` (SCAN-based, production-safe) purges Redis set entries that point to missing or invalid objects. This is the first step in the worker's startup sequence. The bridge does not call `rebuild_indexes()` — index management is the worker's exclusive responsibility. See [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated cleanup reflection that supplements this startup check.

### 7. Agent Session Cleanup (`agent/session_health.py`)

**Problem**: Sessions with corrupted IDs (e.g., length 60 instead of expected 32 for uuid4) or invalid fields cause `ModelException` on every health check and startup recovery cycle, spamming error logs and potentially blocking worker startup.

**Solution**: `cleanup_corrupted_agent_sessions()` runs at worker startup (before recovery), during `/update` (before stale cleanup), and hourly as the `agent-session-cleanup` reflection. It detects unsaveable sessions and deletes them via the ORM. As of issue #1271 it also performs a cross-process orphan reap pass against the OS process table at the end of each call, returning `{"corrupted": int, "orphans": int}` for both legs of work; reaper failures are logged at WARNING and reported as `orphans=0` so they never abort the corrupted-record pass. See also [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated index rebuild that supplements this, and [Cross-Process Orphan Reap (#1271)](#cross-process-orphan-reap-1271) below for the reap mechanics.

#### Cross-Process Orphan Reap (#1271)

**Problem**: When a worker dies ungracefully (panic, SIGKILL, restart-without-graceful-shutdown), its `claude_agent_sdk/_bundled/claude` child and the 4+ `mcp_servers/*.py` grandchildren are reparented to launchd (PID 1) and persist indefinitely. They hold file handles, consume RAM/CPU, and keep an Anthropic API session warm. Reaping only between worker restarts allowed orphans to accumulate for hours or days at a time.

**Solution**: `_reap_orphan_session_processes()` (in `agent/session_health.py`) runs hourly inside the `agent-session-cleanup` reflection (and is also the body of the worker startup shim). It scans the OS process table via psutil for processes whose `cmdline` matches `claude_agent_sdk/_bundled/claude` or `mcp_servers/*.py` AND whose `PPID == 1`, then for each candidate:

1. **Self-suicide guard** — builds a skip-set from `os.getpid()` plus every value under the `worker:registered_pid:*` Redis key prefix (TTL 24h, written by `register_worker_pid()` at worker startup and refreshed every health-loop tick). Any worker whose PID is in the skip-set is never touched. This is structural — even if the cmdline regex were ever extended to match the worker pattern, live workers cannot be self-killed. Required because under `launchd KeepAlive=true` every live worker has `PPID == 1` by design.
2. **Per-PID heartbeat gate** — looks up the owning `AgentSession` via the indexed `claude_pid` field (set in `_on_sdk_started`, cleared in `finalize_session`). If the owning session has `last_heartbeat_at` younger than `ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS` (1800s = 30 min), the kill is skipped. MCP candidates without a direct `claude_pid` mapping inherit their parent process's session via `proc.parent().pid`.
3. **Descendant-tree walk** — `proc.children(recursive=True)` is captured BEFORE `terminate()` so MCP grandchildren are reaped along with the parent.
4. **Two-tick SIGKILL escalation with create-time verification** — parent and descendants get SIGTERM and `(pid, create_time)` is staged on the module-level `_pending_sigkill_orphans: set[tuple[int, float]]`. At the start of the next reflection tick the set is drained: each PID's `proc.create_time()` is compared against the staged value within `1e-3` epsilon; on match `proc.kill()` (SIGKILL); on mismatch the SIGKILL is skipped because macOS recycled the PID. The staged set is always cleared after drain — a PID never lives across more than one tick.
5. **Two-counter scheme** — when the owning session is known, increment `{project_key}:session-health:orphan_process_reaped` (project-scoped). When unknown, increment `session-health:orphan_process_reaped:{worker_hostname}` (hostname-scoped) so true unowned orphans are not falsely attributed to a project.

**Kill switch**: `DISABLE_ORPHAN_PROCESS_REAP=1` short-circuits the entire pass (parity with `DISABLE_ORPHAN_REAP` for the in-process reaper from #1218 and `DISABLE_PROGRESS_KILL` for the no-progress detector).

**Distinction from sibling reapers**:
- vs. `_pending_sigkill` reap (#1218 in-process): the in-process reaper iterates `_active_sessions` (handles known to THIS worker) and asks "is the owning row terminal?". It cannot detect orphans whose parent worker is gone — that gap is exactly what the cross-process reap covers.
- vs. `monitoring/bridge_watchdog.py::kill_zombie_processes()`: the watchdog runs every 60s and kills `claude`/`pyright` processes older than 2h via raw `os.kill`. The cross-process reap runs every 60min, scopes by PPID==1 + heartbeat-stale + signature, walks descendant trees, and uses psutil for PID-reuse safety. Both swallow `ProcessLookupError`/`NoSuchProcess` so double-kill is safe.

**Worker process reaping is intentionally OUT OF SCOPE.** Stranded sibling workers are reparented by launchd already; the worker-signature + PPID==1 filter would self-suicide every live worker on every reflection tick. See [agent-session-health-monitor.md](agent-session-health-monitor.md) for the canonical write-up of all three orphan reapers.

**Phantom-record guard (issue #1069):** Before any iteration, results from `AgentSession.query.all()` pass through `_filter_hydrated_sessions()` to drop phantom instances — records whose fields are still Popoto `Field` descriptors, produced when orphan `$IndexF:AgentSession:*` members reference deleted hashes. Phantoms must never reach the mutation path: attribute access returns a descriptor repr (~60 chars), the length check mis-flags it as corrupt, and `.delete()` damages real records whose indexed-field values happen to match. After the mutation pass, `AgentSession.repair_indexes()` (instead of `rebuild_indexes()`) clears orphan `$IndexF` members at the source before rebuilding indexes from surviving hashes. The same filter is applied to five sibling iterators (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) to close the blind spot across the reflection fleet. The ORM-only policy is now strict: no raw-Redis `scan_iter`/`delete` fallback exists anywhere in `session_health.py`.

### 8. Health-Check Delivery Guard (`agent/agent_session_queue.py`)

**Problem**: When a worker crashes or is cancelled mid-execution, the session stays in `running` state for startup recovery. After `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s), the health check resets it to `pending` and the worker re-runs the session from scratch — including delivering a duplicate response. If each re-run also fails to complete cleanly, this repeats indefinitely, producing 6+ duplicate Telegram messages per session (#918).

**Solution**: `send_to_chat` now stamps `response_delivered_at` (a `DatetimeField` on `AgentSession`) when a response is successfully delivered to Telegram. The `_agent_session_health_check` inspects this field before recovering a session: if `response_delivered_at` is set, the session already delivered its final response and re-queuing would cause a duplicate. Instead, it calls `finalize_session(entry, "completed")` to mark it done.

Both the delivery stamp and the health-check guard are wrapped in `try/except` so that failures are logged but never crash the worker or health-check loop.

**Key fields**:
- `AgentSession.response_delivered_at` — nullable `DatetimeField`, set once on successful delivery
- Health-check path: `_agent_session_health_check()` → `should_recover` → delivery guard → `finalize_session()`

#### 8a. No-Progress Recovery for Shared-Worker-Key Sessions (#944)

**Problem**: A slugless dev session shares `worker_key` with any co-running PM session under the same project (both resolve to `project_key` via `AgentSession.worker_key`). `_agent_session_health_check` determined liveness via `worker_alive = _active_workers.get(worker_key) is not None and not worker.done()`. When a PM was alive under the same project, `worker_alive = True` even though the stuck dev session was not actually being handled — so the `not worker_alive` branch was skipped, and the dev session was only recovered after the wall-clock cap fired (since retired by issue #1172). The fix below — own-progress fields evaluated under `_has_progress` — remains the canonical answer; with the wall-clock cap gone, the no-progress path is the only inference-free recovery branch and runs at the 5-minute health-check cadence.

**Solution**: A new `elif` branch in `_agent_session_health_check` recovers sessions that are `worker_alive=True`, past the `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) startup guard, AND have no progress signal. Progress is evaluated by `_has_progress(entry)` which returns True if ANY of three fields is set: `turn_count > 0`, a non-empty `log_path`, or a non-empty `claude_session_uuid`. Together these cover the full SDK subprocess warmup arc:

- `claude_session_uuid` — set when the SDK subprocess authenticates with the Claude API (seconds after launch)
- `log_path` — set once the session writes its first log entry (first tool call)
- `turn_count` — incremented on each full agent turn completion

**Note (#1614):** these own-progress fields are now **gated on heartbeat freshness** — they are only evaluated when `last_heartbeat_at` is within `NO_OUTPUT_BUDGET_SECONDS` (1800s). A session whose `_heartbeat_loop` has exited (heartbeat frozen) will no longer pass this check via a sticky `claude_session_uuid` alone.

A legitimately slow-starting BUILD session that takes 600s before its first turn will still have `claude_session_uuid` populated within seconds of auth, so the no-progress branch does not fire (the heartbeat is fresh during legitimate long-running turns). The recovered session routes through the existing delivery guard, then the `is_local` split: local sessions become `abandoned`, project-keyed sessions become `pending` (re-queued with `priority=high` and a fresh `_ensure_worker` call). The PM-associated project-keyed worker will pop and execute the re-queued dev session because `_pop_agent_session` filters only by `project_key`/`status`, not by `session_type`.

**Observability**: Each recovery increments a project-scoped Redis counter keyed `{project_key}:session-health:recoveries:{reason_kind}` where `reason_kind` is one of `worker_dead`, `no_progress`, or `tool_timeout` (the previous `timeout` reason was retired by #1172; `tool_timeout` was added by #1270 for the per-tool timeout sub-loop, routed through the shared `_apply_recovery_transition` helper). The counter write is wrapped in `try/except` — failure cannot block recovery.

**Diagnosing no-progress recoveries**:

- Log grep: `grep "worker alive but no progress signal" logs/worker.log` — each hit is one no-progress recovery and includes `turn_count`, `log_path`, and `claude_session_uuid` for the affected session.
- Expected rate ceiling: ≤ 1 no-progress recovery per project per hour under normal operation. Bursts of no-progress recoveries for sessions that should be healthy indicate the `AGENT_SESSION_HEALTH_MIN_RUNNING` guard is too short or the progress signal is too narrow.
- Redis counter: `redis-cli GET {project_key}:session-health:recoveries:no_progress` (note: reading via `redis-cli` is observability-only; never mutate Popoto-managed keys directly).

**Accepted race**: The recovery path does NOT protect progress fields under CAS — only `status`. In the tight window between reading `entry` and calling `transition_status("pending")`, a worker writing progress can have its in-flight work re-queued. This is rare and benign: the worker pops the re-queued session and runs from scratch. See the `test_progress_written_between_check_and_transition_is_lost_but_session_retries` unit test for the locked-in behavior.

### 9. Perplexity Provider Error Handling (`tools/web/providers/perplexity.py`)

**Problem**: The Perplexity search provider had a bare `except Exception` that silently swallowed all errors, including 401 Unauthorized responses from expired API keys.

**Solution**: Added explicit `httpx.HTTPStatusError` handling before the generic catch. 401 errors now log a clear warning message directing the operator to refresh credentials in `.env`. Other HTTP errors are also logged with their status code.

### 10. Service Installation

The watchdog is installed alongside the bridge:
```bash
./scripts/valor-service.sh install
# Installs:
# - com.valor.bridge (main bridge, with log rotation on startup)
# - com.valor.worker (standalone session worker, KeepAlive)
# - com.valor.update (polls every 30 minutes)
# - com.valor.bridge-watchdog (every 60s)
```

The worker can also be installed separately via `./scripts/install_worker.sh`. See [Worker Service](worker-service.md) for details.

### 11. Flood-Backoff Persistence (`bridge/telegram_bridge.py`)

**Problem**: When the bridge hits a Telegram `FloodWaitError` with a long duration, launchd restarts compound the problem. Each restart triggers a new connection attempt, which increments Telegram's flood counter, escalating the wait from seconds to hours.

**Solution**: On `FloodWaitError`, the bridge writes a `data/flood-backoff` JSON file containing the expiry timestamp. On startup, before attempting to connect, the bridge checks this file and sleeps until the flood period clears. This makes launchd restarts harmless.

**File format** (`data/flood-backoff`):
```json
{"expiry_ts": 1711382400.0, "seconds": 300}
```

**Safety guards**:
- Expired entries are ignored and the file is deleted
- Stale files (older than 24 hours based on mtime) are ignored and deleted
- Corrupt or empty files are treated as "no backoff"
- The file is deleted on successful connect
- All writes use atomic temp-file + `os.replace` to prevent corruption

### 12. Dynamic Catchup Lookback (`bridge/catchup.py`)

**Problem**: The fixed 60-minute `CATCHUP_LOOKBACK_MINUTES` means that after a multi-hour outage, messages older than 60 minutes are silently missed forever.

**Solution**: The bridge persists a `data/last_connected` ISO 8601 timestamp file. On startup, catchup reads this timestamp and uses it to compute the lookback window dynamically instead of using the fixed 60-minute default. The lookback is capped at 24 hours to avoid scanning excessive history.

**Timestamp updates**:
- Written on successful Telegram connect
- Updated every 5 minutes via the heartbeat loop
- Written on graceful shutdown (SIGTERM/SIGINT)

**Fallback**: If the file is missing or invalid, the default 60-minute lookback is used. Redis dedup (`is_duplicate_message`) prevents double-processing even if the window overlaps with already-handled messages.

**Telethon duplicate dialog guard**: Telethon's `get_dialogs()` can return the same supergroup twice — once as a channel entity and once as its linked discussion group. Without a guard, catchup would scan the same group twice and enqueue the same messages twice, causing duplicate Telegram replies. The catchup scanner deduplicates by `dialog.id` (`seen_chat_ids: set[int]`) before scanning each group.

**Logger handler guard**: `telegram_bridge.py` may execute its module-level setup twice in some launch configurations (once as `__main__`, once as `bridge.telegram_bridge`). This would add a second `RotatingFileHandler` to the root logger, doubling every log line. A guard checks for an existing handler with the same log file path before adding a new one.

### 12a. Silent Telethon Update Gap Handling (`bridge/dedup.py`, `bridge/catchup.py`, `bridge/reconciler.py`, `bridge/silent_stream.py`)

**Problem** (issue #1408): Telethon can stop delivering `NewMessage` events for a specific chat with no error and no disconnect — the bridge believes it is connected, but the event handler simply stops firing for that chat (known unresolved upstream bugs; the Telethon library was archived 2026-02-21). Three compounding failures previously turned this into permanent message loss:

1. **Catchup dead zone** — Section 12's catchup cutoff is `data/last_connected`, which advances on every 5-minute heartbeat. A message sent *inside* the connection window but silently dropped by Telethon falls *before* the cutoff on restart and is excluded from catchup.
2. **Reconciler lookback too short** — The reconciler's fixed 10-minute lookback aged out messages before they could be recovered across multiple restarts while the worker was down.
3. **Silent failure invisibility** — No log, no alert; the gap was undetectable until a human noticed a dropped message.

**Solution** — three coordinated mechanisms, all best-effort (failures log a WARNING and fall back to prior behavior; they never crash the live handler, reconciler, or catchup):

1. **Per-chat last-processed cursor** (`models/last_processed.py` `LastProcessedRecord`, `bridge/dedup.py` `record_last_processed` / `get_last_processed`). A Redis-backed Popoto model (30-day TTL) tracks the latest message ID + timestamp the bridge actually *dispatched* for each chat. It is distinct from `DedupRecord` (a *set* of recent IDs for membership checks) — this is a monotonic *cursor*. Written by the live handler (via `bridge/dispatch.py::dispatch_telegram_session`), the reconciler, and catchup on every successful dispatch. The cursor advances monotonically: an older message ID is a no-op, so concurrent writes from the live handler and the reconciler cannot regress it.

2. **Smarter catchup cutoff** (`bridge/catchup.py`). For each chat, catchup computes `per_chat_cutoff = min(global_cutoff, last_processed_dt - 60s)`. It uses `min()` — never `max()` — so the scan looks back *at least* as far as the global `last_connected` cutoff, and *further* when the per-chat cursor is older (closing the dead zone). The 60-second safety margin guards against off-by-a-message edges, and the 24-hour global cap (Section 12) still bounds total lookback. If the cursor read fails or no cursor exists, catchup falls back to the global cutoff — today's behavior.

3. **Extended reconciler lookback** (`bridge/reconciler.py`). `RECONCILE_LOOKBACK_MINUTES` is 30 (raised from 10) and `RECONCILE_MESSAGE_LIMIT` is 30 (raised from 20). The 30-minute window covers the worst-case multi-restart scenario; the limit bump keeps the window covered in busy chats while remaining a single `get_messages()` API call per chat per 3-minute scan (no increase in API call *rate*).

4. **Silent-stream check** (`bridge/silent_stream.py` `check_silent_chat` / `check_silent_streams`, `SilentStreamState`). The silent-gap check **rides the reconciler's existing dialog pass** — it does *not* run its own loop. The reconciler already calls `client.get_dialogs()` every 180s and iterates every monitored group; `reconcile_once` invokes `check_silent_chat` for each dialog it already fetched, threading a shared `SilentStreamState` (bridge start timestamp + per-chat warning timestamps) across passes. This adds **no** recurring `get_dialogs()` call beyond the reconciler's existing one — a deliberate constraint of issue #1408 (must not increase the steady-state Telegram API call rate). The check compares the per-chat `bridge:last_event:{chat_id}` Redis key (set on *every* incoming event, before dedup/routing) against the silence threshold and logs a single `[silent-stream] WARNING` when a `respond_to_unaddressed: true` chat has had no events for 15+ minutes while the bridge has been continuously connected and the chat had prior activity in the session. **Observability only** — it does not re-dispatch (the reconciler and catchup own recovery), and a failure in the check is caught so it never interrupts the reconciler's recovery scan. False-positive suppression: only `respond_to_unaddressed` chats are watched; a chat with no `last_event` baseline is skipped; no warning fires within the first 15 minutes after startup; each chat warns at most once per 30-minute window.

**Recovery latency**: a message sent 25 minutes before a restart is recovered within 30 minutes — either the extended reconciler lookback catches it during live connection, or the per-chat catchup cutoff catches it on the next restart.

**Observable in `logs/bridge.log`** via the existing `[catchup] Found missed message` / `[reconciler] Recovered` lines and the new `[silent-stream]` WARNING lines.

These mechanical scanners address message **ingestion** gaps (a message that was never enqueued). For the complementary **response-failure** case — a message that *was* enqueued but whose session hung or was killed without replying — see [Agent-Judgment Catchup](agent-judgment-catchup.md), an LLM-driven recovery layer (`valor-catchup`) that reads the actual thread and decides which messages genuinely need a reply.

### 13. Update Polling (`com.valor.update`)

**Problem**: Code pushes to main could take up to 12 hours to propagate to all machines, since the update plist only ran at 6 AM and 6 PM.

**Solution**: The `com.valor.update` launchd plist uses `StartInterval` of 1800 seconds (30 minutes) to poll for updates frequently. Each invocation runs `scripts/remote-update.sh`, which:
1. Acquires a lock (`data/update.lock`) to prevent concurrent runs
2. Runs `git pull --ff-only` directly in bash (before invoking Python), so the orchestrator and all update scripts are loaded fresh from disk
3. Invokes `scripts/update/run.py --cron --no-pull` (the `--no-pull` flag skips the redundant internal pull since bash already pulled)
4. If new commits arrived: syncs dependencies (if dep files changed), writes `data/restart-requested`
5. The bridge session queue detects the restart flag and triggers a graceful restart after in-flight sessions complete

**Restart flag TTL**: The flag file embeds an ISO 8601 timestamp. `_check_restart_flag()` ignores (and deletes) flags older than 1 hour. This prevents stale flags from a previous update session from triggering a self-destruct on worker-only machines where no bridge is running to consume the flag promptly. Malformed or empty flag content is also safely ignored and deleted.

**Verify polling is active**:
```bash
launchctl list | grep com.valor.update
```

**Check update logs**:
```bash
tail -f logs/update.log
```

**Manual override**: The Telegram `/update` command continues to work for immediate updates.

### 14. Bridge Hibernation (`bridge/hibernation.py`)

**Problem**: The bridge has no distinction between two fundamentally different failure modes:
1. **Auth expiry** — Telegram session token expired or revoked; requires human intervention (`python scripts/telegram_login.py`). The bridge cannot self-recover.
2. **Transient connectivity** — network blip, DC migration, short Telegram outage. Launchd restart + Telethon reconnect handles this automatically.

Without this distinction, auth expiry hits the same 8-attempt retry loop, exits with code 1, and causes the watchdog to restart the bridge indefinitely — making the situation worse and producing no actionable signal.

**Solution**: `bridge/hibernation.py` classifies errors and implements a hibernation state:

**Permanent auth errors → hibernation**:
`AuthKeyUnregisteredError`, `AuthKeyError`, `AuthKeyInvalidError`, `AuthKeyPermEmptyError`, `SessionExpiredError`, `SessionRevokedError`, `UnauthorizedError`

**Transient errors → existing retry loop**:
`NetworkMigrateError`, `ConnectionError`, `OSError`, `FloodWaitError`

**Hibernation sequence** (auth expiry detected):
1. `enter_hibernation()` writes `data/bridge-auth-required` flag file atomically (temp + `os.replace`)
2. macOS notification fires via `osascript` with the exact command to run
3. Bridge logs: "Bridge hibernating: auth required. Run 'python scripts/telegram_login.py'..."
4. Bridge exits with **code 2** (distinct from crash exit code 1)
5. Watchdog detects flag file on next 60s check, logs hibernation state, and **suppresses restart loop**
6. Worker continues executing queued sessions; `TelegramRelayOutputHandler` writes to Redis outbox (undeliverable while bridge is down) and dual-writes to `logs/worker/{session_id}.log` via `FileOutputHandler`

**Recovery sequence** (human re-authenticates):
1. Human runs `python scripts/telegram_login.py` — session file updated
2. Human runs `./scripts/valor-service.sh restart`
3. Bridge connects → `is_user_authorized()` succeeds → `exit_hibernation()` clears flag file
4. `replay_buffered_output(client)` scans `logs/worker/*.log` files from last 24h
5. Files modified < 5 minutes ago are skipped (may still be active sessions)
6. Each replayed entry is delivered to Telegram with a header: `--- Buffered output from {timestamp} ---`
7. `.replayed` marker files prevent duplicate delivery on subsequent reconnects

**Safety guards**:
- `enter_hibernation()` is non-fatal: if `data/` dir is missing or read-only, logs warning and continues to `SystemExit(2)`
- `osascript` failure is non-fatal and logged as warning — `bridge.log` always contains hibernation message
- `replay_buffered_output()` skips unreadable/malformed log files per file with warning
- `is_auth_error(None)` returns False safely (no TypeError)

**Watchdog integration** (`monitoring/bridge_watchdog.py`):
- `run_health_check()` checks `is_hibernating()` before any recovery action
- If hibernating: logs "Bridge hibernating: auth required. Run 'python scripts/telegram_login.py'..." and returns True (suppresses all recovery levels)
- `--check-only` output includes `Hibernating: True/False` and recovery instructions

**Sentry noise suppression** (`before_send` filter in `bridge/telegram_bridge.py`):

When the bridge is hibernating, the watchdog or launchd may still restart the process repeatedly. Each restart hits the same auth error and reports it to Sentry, generating thousands of duplicate events. The `_sentry_before_send` callback registered on `sentry_sdk.init()` checks `is_hibernating()` and drops all events while the flag file is present. When the bridge is not hibernating, all events pass through unchanged. The callback includes a `try/except` safety net so that if `is_hibernating()` itself raises, events still pass through rather than being silently lost.

**Check hibernation state**:
```bash
python monitoring/bridge_watchdog.py --check-only
# Output includes: Hibernating: True/False

ls data/bridge-auth-required  # flag file presence
```

**Manual recovery**:
```bash
python scripts/telegram_login.py  # re-authenticate
./scripts/valor-service.sh restart  # restart bridge
```

### 15. Graceful Shutdown Task Cancellation (`bridge/telegram_bridge.py`)

**Problem**: When the bridge receives SIGTERM, `_graceful_shutdown()` disconnects the Telegram client and `main()` returns. However, `asyncio.run()` then tries to clean up remaining tasks — six background tasks with infinite `while True` loops that are never cancelled. The process hangs indefinitely, preventing launchd from restarting the bridge.

**Solution**: All background tasks created in `main()` are tracked in a module-level `_background_tasks` list. During `_graceful_shutdown()`, all tracked tasks are explicitly cancelled and awaited before disconnecting the Telegram client. A `sys.exit(1)` safety net after `run_until_disconnected()` guarantees process termination.

This follows the proven cancellation pattern from the worker graceful shutdown (PR #742) and the exit-code-1 pattern for launchd ThrottleInterval (PR #789).

**Tracked tasks** (6 total):
- `_run_catchup()` — startup message catchup scan
- `reconciler_loop()` — periodic message gap detection
- `watchdog_loop()` — session health monitoring
- `message_query_loop()` — message query request polling
- `relay_loop()` — PM message relay (outbox queue processing)
- `heartbeat_loop()` — periodic liveness signal for external watchdog

**Shutdown sequence**:
1. Signal handler sets `SHUTTING_DOWN = True`, schedules `_graceful_shutdown()`
2. `_graceful_shutdown()` stops knowledge watcher, writes final `last_connected`
3. Cancels all tracked background tasks via `task.cancel()`
4. `await asyncio.gather(*_background_tasks, return_exceptions=True)` — swallows `CancelledError`
5. Disconnects Telegram client
6. `main()` returns, `sys.exit(1)` terminates process
7. launchd restarts bridge after ThrottleInterval

### 16. Bridge Env Var Injection (`scripts/valor-service.sh` + `bridge/telegram_bridge.py`)

**Problem**: The bridge launchd plist only provided `PATH` and `HOME` in `EnvironmentVariables`. At startup, `load_dotenv()` followed the repo `.env` symlink to `~/Desktop/Valor/.env` on iCloud Drive. macOS TCC blocks `open()` on iCloud Drive files from launchd agents, causing a silent indefinite hang before any bridge code could run.

**Solution**: `scripts/valor-service.sh` (and `scripts/install_worker.sh`) now inject all `.env` variables directly into the installed plist at install time using Python's `dotenv_values()` parser. The bridge and worker detect `VALOR_LAUNCHD=1` in their environment and skip `load_dotenv()` entirely — env vars are already present in the process environment.

```python
# bridge/telegram_bridge.py
if not os.environ.get("VALOR_LAUNCHD"):
    load_dotenv(env_path)   # only runs outside launchd
```

This is a one-time injection at install time; updating `.env` secrets requires re-running the install script (or `/update`) to re-bake the plist.

### 17. Worker Status Heartbeat Check (`scripts/valor-service.sh`)

**Problem**: After the worker shuts down or hangs, `worker-status` reports `RUNNING` because the old PID still exists in the process table (zombie/sleeping state). `worker-start` refuses to launch a new process, leaving the queue silently unattended.

**Solution**: `status_worker()` now reads the `data/last_worker_connected` heartbeat file (written by `_write_worker_heartbeat()` on every health loop tick). If the heartbeat age exceeds 360 seconds (matching the dashboard threshold), the status is reported as `STALE` instead of `RUNNING`, with exit code 2. This distinguishes a healthy worker (exit 0), a stopped worker (exit 1), and a hung/zombie worker (exit 2).

### 18. Worker Watchdog (`monitoring/worker_watchdog.py`)

**Problem**: A worker process can appear alive (PID exists, launchd does not restart it) but have a frozen asyncio event loop — for example, when a reflection callable calls `subprocess.run()` without `await`, blocking the loop indefinitely. The bridge watchdog only monitors the bridge; no equivalent existed for the worker.

**Solution**: `monitoring/worker_watchdog.py` runs as a separate launchd service (`com.valor.worker-watchdog`, `StartInterval: 300`) alongside the worker. It checks the `data/last_worker_connected` heartbeat file on every tick:

| Heartbeat age | Status | Action |
|--------------|--------|--------|
| < threshold | `ok` | Log debug, exit. Reset down-tick counter if present. |
| Missing (file absent) | `starting` | Skip — worker may be initializing |
| Worker PID absent | `down` | **Active recovery via 4-level escalation (issue #1311)** — see below |
| ≥ threshold | `stale` | **Verified-kill escalation ladder W1→W5 (issue #1767)** — see below |

**Stale-heartbeat threshold (issue #1767):** `HEARTBEAT_THRESHOLD` defaults to `180` seconds (= 6× the 30-second heartbeat write interval) and is env-tunable for conservative rollout (e.g. `HEARTBEAT_THRESHOLD=300` on first deploy). The ≥6× multiplier is the new false-positive guard: because the heartbeat is now written by a **dedicated daemon thread** (`worker-heartbeat`, started in `worker/__main__.py`) that runs outside the asyncio event loop, thread-pool exhaustion can no longer starve heartbeat writes. A stale heartbeat therefore reliably means the worker process is genuinely wedged (not just loop-busy), justifying a lower threshold and an aggressive kill ladder.

**Heartbeat thread isolation (issue #1767):** `_heartbeat_thread_main()` in `worker/__main__.py` runs as a `threading.Thread(name="worker-heartbeat", daemon=True)` — outside the asyncio event loop. It wakes every `WORKER_HEARTBEAT_INTERVAL` seconds (default 30, env-tunable) and calls `_write_worker_heartbeat()`. The thread is started at worker startup and is stopped via `_heartbeat_stop_event` on worker shutdown. Prior to issue #1767 the heartbeat write happened inside `_agent_session_health_loop()` (inside the event loop), so thread-pool saturation could delay or skip writes entirely — the stale-heartbeat signal was unreliable. The dedicated thread removes that dependency: the only way the heartbeat can go stale now is if the worker process itself is hung.

**Verified-kill escalation ladder** (when heartbeat is stale — issue #1767):

When the watchdog detects `status == "stale"`, it calls `recover(status)`, which runs a five-rung escalation ladder. Every rung verifies the kill via `_poll_pid_dead(pid, timeout, interval=0.5)`, which loops `os.kill(pid, 0)` and treats `ProcessLookupError` or `PermissionError` as confirmed dead — the kill is never assumed.

| Rung | Action | Poll timeout | Disposition |
|------|--------|-------------|-------------|
| W1 | `SIGTERM` | 5.0 s | If dead → done (launchd respawns) |
| W2 | `SIGKILL` | 10.0 s | May queue against a U-state process; if dead → done |
| W3 | `launchctl bootout gui/<uid>/com.valor.worker` | 10.0 s | Removes the launchd job so the kernel cleans the fd table on exit, allowing a hung blocking syscall in U-state to return and the process to exit; if dead → done |
| W4 | Write `worker:watchdog:critical:{host}` (TTL 1 h) | — | CRITICAL log; operator alert. |
| W5 | Final CRITICAL log; no further automated action | — | launchd will respawn the worker once the U-state process exits (the blocking syscall returns). Session sweep runs at next startup. |

**Check U-state critical signal:**
```bash
redis-cli GET worker:watchdog:critical:$(hostname)
```

W4 no longer writes a separate `pty_close_required` side-channel key — that
signal existed to prompt operator PTY-fd cleanup, which has no analog for a
`claude -p` subprocess (a session-execution turn is a short-lived, self-reaping
child; there is no long-lived PTY master fd for a U-state hang to be blocked
on in the first place, issue #1924).

**Confirmed KEPT, unchanged (#1926 post-teardown scar-tissue removal):** the
W1-W5 verified-kill ladder above and its issue-#1767 U-state rationale carry
no PTY-specific narrative (`grep -ni "pty master|master fd"
monitoring/worker_watchdog.py` == 0) — the mechanism is substrate-agnostic. A
headless `claude -p` subprocess can still wedge in uninterruptible sleep on a
blocking syscall exactly as any process can, so the ladder is a confirmation
surface, not an edit target. Likewise the bridge watchdog's 5-level
escalation ladder + revert-commit (Component 3 above) is kept unchanged —
orthogonal to PTY, it supervises the bridge process (Telethon connectivity,
hibernation). See the [Removed Defenses Ledger](../removed-defenses.md) for
what #1926 actually removed elsewhere (the stall-recovery dry-run flag).

**Post-restart dead-worker session sweep (issue #1767):** When the worker restarts after a hung-worker incident, `_sweep_dead_worker_sessions()` in `agent/session_health.py` runs as **Step 3a** in `worker/__main__.py`, **before** `_recover_interrupted_agent_sessions_startup` (Step 3b). The ordering is critical: Step 3b transitions all remaining `running` sessions → `pending` without checking PID liveness; if the sweep ran after, there would be no `running` sessions left to inspect. The sweep handles the dead-worker subset (dead `claude_pid`) first, finalizing those sessions to `killed`; Step 3b then re-queues the remaining genuinely-interruptible sessions (alive PID or no PID yet). The sweep:

1. Enumerates all sessions with `status="running"`.
2. Skips sessions with no `claude_pid` (not yet assigned a subprocess).
3. Skips sessions started within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds (300 s) — the recency guard preventing the fresh worker's own new sessions from being swept.
4. Checks `os.kill(pid, 0)` liveness for each remaining session; treats `OSError` as dead.
5. Calls `finalize_session(entry, "killed", reason="dead-worker-sweep: ...")` (CAS via `expected_status='running'` — a concurrent fresh-worker pickup wins and the session is skipped via `StatusConflictError`).
6. When any sessions are swept, triggers `bridge.agent_catchup` as a subprocess so unanswered human messages re-enqueue as fresh sessions — no silently dropped messages.

Returns the count of sessions swept. A non-zero result is logged at INFO.

**Active recovery escalation** (when worker process is missing — issue #1311):

Prior to issue #1311 the watchdog only logged `Worker not running — launchd handles restart` and exited, relying on launchd `KeepAlive=true` to bring the worker back. On 2026-05-06 the worker died at 08:37 UTC and KeepAlive failed to restart it for 7+ hours, leaving every layer logging the failure but none recovering. The watchdog now actively escalates.

A Redis counter (`worker:watchdog:down_ticks:{hostname}`) tracks consecutive missing-worker ticks using `POPOTO_REDIS_DB.incr` + `expire(3600)` (atomic by Redis semantics, no file-lock needed). Each watchdog tick is a fresh launchd invocation so the counter must survive outside the process — Redis is the natural fit. TTL of 1h auto-clears stale state (e.g. after a prolonged outage where the counter was never explicitly cleared).

| Level | Trigger | Action |
|-------|---------|--------|
| L1 | First down tick (count == 1) | Log `Worker missing — giving launchd one tick to restart` and exit. Give launchd a chance. |
| L2 | Second consecutive down tick (count >= 2) | `launchctl kickstart -k gui/<uid>/com.valor.worker`, then poll `pgrep` for up to 10s. On success, clear counter. |
| L2.5 | L2 returned rc=113 / `Could not find service` AND `~/Library/LaunchAgents/com.valor.worker.plist` exists (issue #1407) | `launchctl bootstrap gui/<uid> <plist>` to re-register the service in the gui domain, then retry kickstart and verify. On success, clear counter. Heals the case where `start_worker()` registered the service via `launchctl load`, leaving it invisible to `gui/<uid>/` queries. Plist-existence gate ensures uninstalled hosts fall through cleanly. |
| L3 | L2/L2.5 verify failed | `launchctl enable gui/<uid>/com.valor.worker` (clears sticky-disable from `worker-disable`) + kickstart + verify. On success, clear counter. |
| L4 | L3 verify failed AND count >= 3 | Log CRITICAL with hostname + tick count. Reason string includes `bootstrap+kickstart+enable all failed` when L2.5 was attempted, otherwise `kickstart+enable both failed`. Write `worker:watchdog:critical:{hostname}` Redis key (TTL 1h, JSON payload `{hostname, tick_count, last_attempt_at, reason}`). Counter persists; subsequent ticks repeat L4 idempotently. |

**Why L2.5 was needed (issue #1407)**: prior to the fix, `scripts/valor-service.sh::start_worker()` used `launchctl load` which registered the worker in a domain outside `gui/<uid>/`. After any `worker-stop && worker-start` cycle, `KeepAlive` no longer fired and the watchdog's `kickstart gui/<uid>/...` returned rc=113. `start_worker()` was also modernized to use `bootout + bootstrap gui/<uid> <plist>` so the registration always lands in the gui domain on day one. L2.5 is the defense-in-depth — if any future code path regresses, the watchdog now self-heals.

**Operator-disable short-circuit**: the watchdog detects sticky-disable via `launchctl print-disabled gui/<uid>` at the very top of `main()`. If `"com.valor.worker" => disabled` appears in the output, it logs `Worker disabled by operator (launchctl print-disabled) — skipping check`, clears the down-tick counter (so a future re-enable starts fresh), and returns without touching launchctl. This is the only authoritative source — `worker-disable` in `valor-service.sh` calls `launchctl disable` directly; no sidecar flag file exists. Operator check precedes the down-counter increment so a disabled worker never accumulates ticks.

**Single-handler logger**: the previous module called `logging.basicConfig()` AND attached a rotating file handler to a named logger that propagated to root, while the launchd plist redirected stdout/stderr to the same log file. Net result: every line written twice. The fix configures the named logger explicitly with `propagate = False` and exactly one rotating file handler. Regression test: `len(monitoring.worker_watchdog.logger.handlers) == 1`.

**Check status**:
```bash
./scripts/valor-service.sh worker-status   # surfaces watchdog recovery state inline (Task 4b)
python monitoring/worker_watchdog.py --check   # standalone: print status, exit 0=ok, 1=stale/down
tail -f logs/worker_watchdog.log

# Inspect the critical signal (L4):
redis-cli GET worker:watchdog:critical:$(hostname)

# Reset escalation counter manually (e.g. after fixing the underlying cause):
redis-cli DEL "worker:watchdog:down_ticks:$(hostname)"
```

**`worker-status` watchdog surface** (Task 4b): `./scripts/valor-service.sh worker-status` now reads the Redis down-tick counter (`worker:watchdog:down_ticks:{hostname}`) and critical-state key (`worker:watchdog:critical:{hostname}`) and prints a one-line summary alongside the process/heartbeat info. Best-effort — Redis unavailability is silently ignored so `worker-status` always completes.

**Installed by** `scripts/install_worker.sh` as `${SERVICE_LABEL_PREFIX}.worker-watchdog`.

### 19. Guarded Config Read (`bridge/routing.py`, issue #1817 workstream C4)

**Problem**: `load_config()` and `telegram_bridge.py`'s import-time `_get_active_projects()` both parsed `projects.json` with a bare `json.load()`. A launchd `KeepAlive` respawn can race a mid-iCloud-write of `projects.json`, producing a partial/truncated file. The bare parse raised `JSONDecodeError` (or, on a partial read, `OSError`/`UnicodeDecodeError`) straight out of `_get_active_projects()`, which runs at `telegram_bridge.py` import time (`ACTIVE_PROJECTS = _get_active_projects()`) — crashing the bridge before any logging or recovery could run and trapping the watchdog in the same restart-loop failure mode described under Import-Time Safety above.

**Solution**: `bridge/routing.py::_guarded_json_load()` wraps the parse in a `try/except (json.JSONDecodeError, OSError, UnicodeDecodeError)`. On success it caches the parsed config to a last-known-good sidecar (`data/projects.last_known_good.json`), written atomically via temp-file + `os.replace` — the same idiom used by `data/flood-backoff` (Component 11) and `agent/session_health.py`. On a parse failure it logs an ERROR and serves the last-known-good sidecar instead of raising; if no sidecar exists yet, it falls back to empty defaults (`{"projects": {}, "defaults": {}}`). `_guarded_json_load()` never raises. Both `load_config()` and `telegram_bridge.py::_get_active_projects()` (including its import-time module-level call) route through this shared helper, so a transiently corrupt config no longer crash-loops either the bridge or the worker's config reads.

**Files**:
- `data/projects.last_known_good.json` — last successfully-parsed config, refreshed on every successful read

**Tests**: `tests/unit/test_routing.py::TestGuardedConfigRead` (9 cases) — successful reads cache the sidecar atomically; a corrupt read falls back to the sidecar and logs; a corrupt read with no sidecar falls back to empty defaults and logs; malformed/binary input never raises; `load_config()` falls back correctly end-to-end; sidecar read/write helpers handle missing-file and write-failure cases without raising.

See [Config Architecture](config-architecture.md) for how `projects.json` fits into the broader config system.

### 20. Update Release Verification (issue #1898)

**Problem**: The Telegram `/update` command (and the 30-minute polling cron, both routed through `scripts/remote-update.sh`) reported `✅ update OK @ {sha}` from the shell's exit code and `git rev-parse --short HEAD` alone. Nothing restarted the bridge on this path (only the worker had a kickstart block), and nothing verified that the running processes were actually executing the pulled code. A bridge that booted once and then survived every later pull kept reporting `✅` on each cycle while running commits old, because no restart and no verification ever touched it.

**Solution**: Four coordinated pieces close the gap: a boot-SHA beacon each process writes at startup, a shared classifier that reads both beacons, a bridge restart block in the shell (mirroring the pre-existing worker block), and a report path that survives the bridge's own restart.

**Boot-SHA beacon** (`monitoring/boot_beacon.py::write_boot_beacon`): at startup the bridge writes `data/bridge_boot_sha` and the worker writes `data/worker_boot_sha`, each a two-line file containing the short git SHA (via `scripts/update/git.py::get_short_sha`, the same helper the classifier compares against) and an ISO 8601 timestamp. The write is best-effort: any failure logs a warning and never crashes startup. A missing or malformed beacon can only ever downgrade classification to `unknown`, never invert into a false failure.

**Relevant-range classifier** (`scripts/update/service.py::verify_running_release`): for each in-role process, `_classify_process()` reads its beacon, gets the process's absolute start time via `get_process_start_ts(pid)` (a `ps -o lstart` parser, generalized from the bridge watchdog's `get_bridge_process_start_ts`), and classifies:

| Classification | Condition |
|---|---|
| `matches` | beacon belongs to the current process image (`beacon_ts > process_start_ts`) AND `git log {boot_sha}..HEAD -- <relevant paths>` is empty |
| `stale` | beacon belongs to the current image AND that relevant-range log is non-empty |
| `unknown` | beacon missing/malformed, no PID, `process_start_ts` unavailable, an orphaned beacon (`beacon_ts <= process_start_ts`), or `boot_sha` unresolvable by git |

Staleness is positive-only and scoped to each process's own relevant path set (bridge: `bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`; worker: `worker/ agent/ mcp_servers/ models/ tools/ bridge/ reflections/ pyproject.toml`), the same sets the restart gates diff, so classifier and restart gate agree by construction. A raw `boot_sha == HEAD` comparison is deliberately never used: docs-only or plan-migration commits advance HEAD past a healthy, correctly-un-restarted process, and a literal-equality check would false-fail on the majority of this repo's commit stream. `unknown` never fails a run and never triggers a restart. Only a positive, confirmed staleness escalates.

**Bridge kickstart in `remote-update.sh`** (the core fix: a bridge restart on this path did not exist before). After the pull and the existing worker kickstart, the shell computes `NEED_BRIDGE_RESTART` from a `BEFORE_SHA..AFTER_SHA` diff of the bridge-relevant paths, gated on the bridge plist being installed on this machine (`[ -f "$BRIDGE_DST" ]`; a skills-only machine has no bridge plist and skips the block entirely). When true, it runs `launchctl kickstart -k {prefix}.bridge` as the **last** thing the script does. This is safe because the bridge holds no agent sessions (the worker is the sole session executor) and its Telethon `catch_up=True` scan backfills anything missed during the brief restart. It is the last act because the kickstart SIGKILLs the whole bridge launchd job, including `handle_update_command` and the `remote-update.sh` child it spawned, since they share the job's process group. Nothing in the shell runs after a successful kickstart. Both worker and bridge kickstart failures now surface as a distinct `RESTART FAILED` line and a non-zero terminal exit (`RESTART_FAILED || VERIFY_FAILED`), replacing the previous swallowed `echo ERROR`.

**Worker restart primitive selection and the EIO-recovery fallback.** The worker restart block picks its primitive from `launchctl list | grep -q "$WORKER_LABEL"`. When the label is listed it uses the race-free `launchctl kickstart -k` (with a `bootout` + `sleep 2` + `bootstrap` fallback). When the grep reports the label absent it treats it as a first install and `bootstrap`s. That grep can **false-negative** — the label is in fact still registered in the gui domain (e.g. a stale worker process still holding it) while `launchctl list` momentarily omits it — in which case the bare `bootstrap` fails with `Bootstrap failed: 5: Input/output error` (errno 5 = the service is already bootstrapped in the target domain). Because that EIO *proves* the service is loaded, the not-loaded branch recovers with `launchctl kickstart -k` — the same primitive the loaded branch prefers — and only declares `RESTART FAILED` when **both** the bootstrap and the kickstart fail. On success it sets `VERIFY_SINCE=$RESTART_TS` exactly like the loaded branch, so the terminal release verify still runs once against the restart moment. Recoverable bootstrap stderr is suppressed so a transient EIO does not leak the raw launchd error into the update summary; on the genuine both-fail path the captured launchd errno/message is appended to the `RESTART FAILED` line for diagnosability. Regression tests live in `tests/unit/test_remote_update_shell.py` (`test_worker_bootstrap_eio_recovers_via_kickstart`, `test_worker_bootstrap_and_kickstart_both_fail_reports_failure`). This fixed a real `/update` failure on the "Valor the Bald" machine where a 17-hour-old worker held the label, the grep false-negatived, and the bare bootstrap EIO left the stale worker running code 8 commits behind HEAD.

Before the kickstart, the shell releases `data/update.lock` explicitly (`rmdir "$LOCK_DIR"`), because the `trap cleanup_lock EXIT` that normally releases it never fires on SIGKILL. Without the explicit release, every bridge-relevant update would orphan the lock for up to 600 seconds, and any retry or the next cron cycle in that window would hit the "already running" skip branch with no pull and no verify.

**Terminal verify runs every cycle**: `python -m scripts.update.verify_release` (`scripts/update/verify_release.py`) is the shell's terminal step on every invocation, including no-op cron cycles with no new commits. This re-classifies a starved or never-restarted process instead of only checking right after a restart. It is scoped to the worker only (`--skip-bridge`) when a bridge restart is queued this cycle, since the about-to-restart bridge is not escalated as stale. It takes a `--since <epoch>` restart moment and polls (bounded, 15 attempts x 2 seconds) for the worker beacon to freshen past it before classifying, because a `kickstart -k` returns before the freshly-spawned process has written its own beacon, so an immediate read would otherwise see the pre-restart beacon and misclassify `unknown`. Exit code 1 on any positive staleness, 0 otherwise (`unknown` prints a warning but does not fail the run).

**Report path splits on whether the bridge restarts this cycle** (the survivable-channel design: a bridge kickstart kills the process that ran `/update`, so it cannot always be the reporter):

- **Worker-only or no-op update (no bridge restart)**: `handle_update_command` (`bridge/update.py`) survives. It re-verifies via `verify_running_release()` after the shell returns, gates `✅` on `returncode == 0 AND` no in-role `stale`, and appends per-process reload state (e.g. `(bridge current, worker restarted)`). A stale process reports `❌ update FAILED @ {sha}: {process} running {short} but HEAD is {short}` and still spawns the existing fix session. All stdout lines are scanned for `warning`/`ERROR` (previously only the first line was checked).
- **Bridge-relevant update (bridge restart triggered)**: `handle_update_command` will be SIGKILLed, so before the kickstart the shell stages the originating chat id, reply-to message id, pulled HEAD short-SHA, and worker reload state to `data/update-pending-report` (only when a Telegram chat context is present; the pure 30-minute cron cycle has none, so nothing is staged). The **fresh bridge**, at startup right after writing its own boot-SHA beacon, calls `run_boot_release_check()` (`bridge/update.py`), which unconditionally verifies its own release, then, if the pending report exists, reuses that check to compose the `✅`/FAILED reply, sends it to the staged chat, and deletes the file.
- On a bridge-plist machine, `handle_update_command` also sends a best-effort interim notice before invoking the shell, so the human is not left staring at a bare 👀 reaction for the multi-minute window between the bridge's self-kill and the fresh bridge's boot flush. A send failure here never blocks the update.

**`--full` verify** (`scripts/update/run.py::run_release_verify`): the synchronous `/update --full` path calls `verify_running_release()` as the terminal step of the `do_service_restart=True` branch, after `install_service`'s restart. Any in-role `stale` sets `result.success = False` (non-zero exit) and names both short-SHAs; `unknown` only warns. A clean pass that finds the bridge positively `matches` clears any earlier failure sentinel (below).

**Out-of-band signals for a bridge that never comes back**: the report path above depends on the fresh bridge coming up. If it crash-loops or launchd fails to relaunch it, there is no live channel to report on, reproducing the original #1898 symptom. Two backstops, both read by `monitoring/bridge_watchdog.py::check_update_release_signals()` on its normal 60-second cycle:

- A fresh bridge that boots but self-classifies its own beacon `stale` writes `data/update-release-failed` (SHA lag + timestamp) via the unconditional self-check in `run_boot_release_check()`. This runs at every bridge boot regardless of whether a pending report exists, so the pure-cron trigger path (which stages nothing) still gets the backstop. A subsequent healthy boot (`matches`) clears the sentinel.
- A `data/update-pending-report` left undrained past `UPDATE_REPORT_TTL_SECONDS` (`STARTUP_GRACE_SECONDS + 60`, i.e. the watchdog's 5-minute startup grace plus one 60-second watchdog cycle, defined once in `scripts/update/service.py` and re-imported by the watchdog), measured against the report's own staged timestamp, signals that the fresh bridge never came up to flush it.

Both checks are logged at `logger.critical("[update-release] ...")` on every watchdog tick while the condition holds.

**Watchdog suppression for the planned restart**: the bridge kickstart is the first *deliberate* SIGKILL of the bridge process, and without a suppression the independent 60-second watchdog would log a crash and could itself call `restart_bridge()` mid-window. `remote-update.sh` writes `data/update-restart-in-progress` (a timestamp) immediately before the kickstart. `run_health_check()` checks the marker's age against `UPDATE_RESTART_MARKER_TTL_SECONDS` (the same `STARTUP_GRACE_SECONDS + 60` formula as the report TTL, so the suppression window can never expire before the boot window it protects) and early-returns healthy while the marker is fresh, before `check_bridge_health()` runs, so neither the crash log nor the recovery-level bump fires. The fresh bridge's boot self-check clears the marker; an aged-out marker resumes normal health checking.

**Files**:
- `data/bridge_boot_sha`, `data/worker_boot_sha`: boot-SHA beacons (SHA + ISO timestamp), written at startup
- `data/update-pending-report`: staged chat context for the fresh bridge's boot flush, deleted after a successful flush (or left in place if the fresh bridge itself boots stale)
- `data/update-restart-in-progress`: planned-restart marker, cleared by the fresh bridge's boot self-check
- `data/update-release-failed`: out-of-band sentinel for a bridge that boots stale, cleared on a subsequent healthy boot

**Check state**:
```bash
cat data/bridge_boot_sha data/worker_boot_sha
python -m scripts.update.verify_release          # manual classification against current HEAD
cat data/update-release-failed 2>/dev/null       # present only after a stale boot
```

**Corrected docstrings**: `agent/agent_session_queue.py`'s `_trigger_restart()` and the `_check_restart_flag()` log line previously said "restarting bridge," but both actually SIGTERM the **worker** process (launchd respawns the worker, not the bridge). These are documentation-only corrections; the SIGTERM target is unchanged. The worker's deferred `data/restart-requested` flag remains independent of the bridge kickstart described above. This feature does not add bridge consumption of that flag.

### 21. Hardened `launchctl bootstrap` Call Sites (`scripts/lib/launchctl.sh`, issue #2013)

**Problem**: The worker-restart EIO recovery described in Component 20 above (issue #1898)
hardened one `launchctl bootstrap` call site: the not-loaded branch of the worker restart
block in `remote-update.sh`. Every other bare `launchctl bootstrap` call in the codebase
still had no recovery. On machine "Valor the Bald," a `/update` run hit the same errno-5
race on a bare bootstrap in `scripts/valor-service.sh` and aborted mid-service-install with
`Bootstrap failed: 5: Input/output error` and no further `[update]` output.
`scripts/valor-service.sh` runs under `set -e`, so a single transient errno 5 on any of its
three bare `launchctl bootstrap` calls terminated the whole `install`/`restart`/`worker-start`
invocation, leaving remaining services uninstalled and the machine on stale code. The
`scripts/install_*.sh` helper scripts shared the identical bare-bootstrap shape.

**Solution**: `scripts/lib/launchctl.sh` exports one shared function,
`launchctl_bootstrap_fail_soft <domain> <plist> <label>`, implementing the same
bootstrap-then-`kickstart -k` recovery already proven in Component 20: `launchctl bootstrap`
first, and only on bootstrap failure a `launchctl kickstart -k` fallback against the same
label. The helper deliberately does NOT bootout the label itself — an unconditional internal
bootout would kill and recreate an already-loaded, healthy service on every call. Any
preceding bootout is owned by the call site, matching the `remote-update.sh` pattern:
`bootstrap_plist_idempotent` and `worker-start` in `scripts/valor-service.sh` each already
boot out before calling the helper, exactly as `remote-update.sh` boots out before its own
bootstrap. `kickstart -k` is the correct recovery here because an errno-5 bootstrap failure
specifically means the label is already registered in the domain, the same reasoning as the
worker-restart fallback in Component 20. The function returns 0 as soon as the service ends
up loaded (first-try bootstrap, or kickstart recovery) and returns 1 with a distinct,
greppable `WARNING: launchctl bootstrap+kickstart failed for <label>` to stderr only on a
genuine double-failure, so a truly dead service is never silently masked.

`scripts/valor-service.sh` (three call sites: `bootstrap_plist_idempotent`, the bridge
install, and `worker-start`) and five `install_*.sh` helpers
(`install_worker.sh`: main and watchdog, plus `install_reflection_worker.sh`,
`install_nightly_tests.sh`, `install_email_bridge.sh`, `install_sdlc_reflection.sh`) now
source the shared helper and call it instead of a bare `launchctl bootstrap`. In
`valor-service.sh`, a genuine double-failure warns and lets the install continue rather than
hard-aborting under `set -e`: the fix's core behavior change. The happy path (bootstrap
succeeds on the first try) is observably identical to before: the helper returns 0 with no
`kickstart` call.

This is the sibling hardening to Component 20's `remote-update.sh` fix (issue #1898): that
work covered the worker-restart not-loaded branch of `remote-update.sh` only; issue #2013
closes the gap across the remaining bare-bootstrap call sites in `valor-service.sh` and the
`install_*.sh` helpers, using the same `kickstart -k` recovery primitive.

**Files**:
- `scripts/lib/launchctl.sh`: shared `launchctl_bootstrap_fail_soft` helper

**Tests**: `tests/unit/test_valor_service_bootstrap.py` and
`tests/unit/test_install_scripts_bootstrap.py` are stubbed-`launchctl` harnesses asserting
the errno-5 recovery path, the genuine double-failure WARNING, and the unchanged happy path.

## Idle SDK Teardown (issue #1128) — Retired in #2000

The Claude Agent SDK's persistent `ClaudeSDKClient` connections used to
die silently after roughly 48 hours of idle (fleet-ops research, #1104),
so the worker ran an idle sweeper (`worker/idle_sweeper.py::run_idle_sweep`)
that proactively tore down those clients and rebuilt them via `--resume`
on the next query. Every production session (PM / Dev / Teammate) has
run through the short-lived `claude -p stream-json` subprocess-per-turn
harness path for some time, which never held a persistent connection —
so the sweeper always found nothing to tear down there and was
dead-by-consequence. #2000 deleted the sweeper, its `worker/__main__.py`
supervision wiring, the `_active_clients` registry it inspected, and the
rest of the dead Claude Agent SDK path wholesale (see
[HarnessAdapter Seam](harness-adapter.md)). There is no idle-teardown
concern on the harness path — a fresh `claude -p` subprocess is spawned
per turn regardless of how long the session was dormant.

## Two-tier no-progress detector

The periodic `_agent_session_health_check` (every 5 minutes) decides whether a
long-running session is making progress. To minimize **false-negatives**
(killing a working session) while still reaping genuinely wedged sessions, the
detector uses two independent tiers. (Issues #1036 and #1046.)

### Tier 1 — per-turn signals (sub-check A) + bounded startup-window heartbeat (sub-check B)

`_has_progress()` evaluates two sub-checks. Either passing → progress.

**Sub-check A — per-turn SDK progress (issue #1226).**

| Field | Writer | When |
|-------|--------|------|
| `last_tool_use_at` | `agent/hooks/liveness_writers.py::record_tool_boundary` (PreToolUse / PostToolUse) | Per tool call boundary |
| `last_turn_at` | `agent/sdk_client.py` on `result` event | End of each turn |

Either field fresher than `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, 30 min)
counts as progress. `last_sdk_heartbeat_at` (the BackgroundTask watchdog
tick) is intentionally NOT a progress signal — it proves only that the
subprocess exists.

**Sub-check B — startup-window executor-alive fallback (issue #1036, narrowed by #1226 / #1724 / #1905).**

| Field | Writer | When |
|-------|--------|------|
| `last_heartbeat_at` | Queue-layer `_heartbeat_loop` inside `_execute_agent_session` | Every `HEARTBEAT_WRITE_INTERVAL` (60s) |

When `sdk_ever_output` is False (neither per-turn field has ever been set),
`last_heartbeat_at` fresh within `HEARTBEAT_FRESHNESS_WINDOW` (90s) counts
as progress, **subject to the D0 never-started gate (issue #1724)**. The
gate (`_never_started_past_grace`, called with the same trusted `now_utc`
clock sub-check B uses — issue #1905) is the authoritative never-started
bound: it returns True once `running_seconds > NEVER_STARTED_GRACE_SECS
(120) + NEVER_STARTED_CONFIRM_MARGIN_SECS (30)` (150s), and sub-check B
returns False immediately when it fires. For gate survivors, the function
uses `started_ref = entry.started_at or entry.created_at` so that recovered
sessions (whose `started_at` is nulled by the recovery path) cannot
silently re-enter the original fast-path:

| `started_ref` state | Verdict |
|---|---|
| both `started_at` and `created_at` are None (phantom record from older format) | fresh heartbeat passes |
| `running_seconds < STARTUP_GRACE_SECONDS` (300s, aliased to `AGENT_SESSION_HEALTH_MIN_RUNNING`, env-tunable) | fresh heartbeat passes — unconditional for D0-gate survivors, since a survivor's `running_seconds` (<= 150s) is always below this 300s window |

The #1356 grace-to-budget band (`STARTUP_GRACE_SECONDS <=
running_seconds <= NO_OUTPUT_BUDGET_SECONDS`) and its `tier1_falloff`
budget-exceeded telemetry counter that used to fire beyond it were removed
in issue #1905: once the D0 gate and this leg share one clock, every gate
survivor unconditionally satisfies the 300s leg above, so the old band was
provably unreachable.

The D0 gate bounds the previously-unbounded fresh-heartbeat fast-path that
allowed cwd-disappearance and similar wedges (parent investigation #1246) to
hold Tier 1 open indefinitely. Sessions that have produced any SDK output
(`sdk_ever_output=True`) are not subject to sub-check B at all — sub-check A
is authoritative for them.

**Own-progress fields and child-activity check (#1614):** `turn_count > 0`, non-empty `log_path`, and non-empty
`claude_session_uuid` are evaluated only when `sdk_ever_output` is False
AND `last_heartbeat_at` is within the last `NO_OUTPUT_BUDGET_SECONDS`
(1800s). These fields are sticky once set, but are now **gated on
heartbeat freshness** — a stale or absent heartbeat means the executor
loop has likely exited, so own-progress fields must not keep the session
alive indefinitely (#1614 Branch 2 fix). The #963 child-activity check
(a PM session with any non-terminal child is not stuck) is unconditional
and evaluated regardless of heartbeat freshness.

> **Retired by issue #1172:** the stdout-stale Tier 1 extension from #1046
> (`STDOUT_FRESHNESS_WINDOW`, `FIRST_STDOUT_DEADLINE`) has been removed
> along with the per-session wall-clock cap (`AGENT_SESSION_TIMEOUT_*`,
> `_get_agent_session_timeout`). Stdout silence is no longer a kill signal
> — long-thinking turns and large tool outputs produce legitimate stdout
> silence. See [PM Session Liveness](pm-session-liveness.md) for the
> evidence-only philosophy and cost-monitoring backstop.
>
> **Retired by issue #1226:** the symmetric "dual heartbeat" Tier 1
> (either `last_heartbeat_at` or `last_sdk_heartbeat_at` fresh = progress)
> was rewritten as sub-check A above. `last_sdk_heartbeat_at` is now
> watchdog-only.

**Constants:**

| Constant | Default | Env var | Purpose |
|----------|---------|---------|---------|
| `SDK_PROGRESS_FRESHNESS_WINDOW` | 1800s (30 min) | `SDK_PROGRESS_FRESHNESS_WINDOW_SECS` | Sub-check A freshness window for `last_tool_use_at` / `last_turn_at` (issue #1226) |
| `MAX_NO_OUTPUT_REPRIEVES` | 20 | — (derived) | Tier-2 reprieve cap for `sdk_ever_output=False` sessions; also feeds `NO_OUTPUT_BUDGET_SECONDS` (issues #1226 / #1356) |
| `NO_OUTPUT_BUDGET_SECONDS` | 1800s (30 min) | — (derived) | `MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW`. Outside sub-check B's scope (its grace-to-budget band was pruned in issue #1905, subsumed by the D0 gate); still used by the #1614 own-progress heartbeat gate and the Tier-2 reprieve cap |
| `STARTUP_GRACE_SECONDS` | 300s (= `AGENT_SESSION_HEALTH_MIN_RUNNING`) | `STARTUP_GRACE_SECONDS` | Below this `running_seconds`, sub-check B's fresh-heartbeat fast-path is unconditional for D0-gate survivors (issue #1356, gate added by #1724) |
| `COMPACT_REPRIEVE_WINDOW_SEC` | 600s | `COMPACT_REPRIEVE_WINDOW_SECS` | Tier 2 `compacting` reprieve window — `last_compaction_ts` within this window reprieves the kill (issue #1099 Mode 3) |

**Operator alert:** After 3 Tier 2 reprieves, the reprieve log message is
escalated from `INFO` to `WARNING`, signaling that the session may be in an
indefinite alive-but-silent reprieve loop.

### Tier 2 — activity-positive reprieve gates

When Tier 1 flags a session, the health check calls `_tier2_reprieve_signal()`
which evaluates three gates — one compaction-aware and two OS-level liveness
checks via `psutil`. The previous fourth `stdout` gate was retired by issue
#1172 along with `STDOUT_FRESHNESS_WINDOW`.

| Gate | Check | Return |
|------|-------|--------|
| compacting | `AgentSession.last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (600s). Evaluated first so post-compaction idle periods are never misread as hangs. Companion writer: `agent/hooks/pre_compact.py::pre_compact_hook` (PR #1135). Added by issue #1099 Mode 3. | `"compacting"` |
| children   | `psutil.Process(pid).children()` non-empty (tool execution active) | `"children"` (preferred over `"alive"`) |
| alive      | `psutil.Process(pid).status()` not in `{zombie, dead, stopped}` | `"alive"` |

Any **one** passing gate reprieves the kill. The reprieve signal is logged and
`reprieve_count` on the AgentSession is incremented for post-hoc analysis.
`recovery_attempts` is NOT incremented on reprieve.

**Scope:** Tier 2 reprieve applies **only** to `no_progress` recoveries.
`worker_dead` recoveries skip Tier 2 entirely and proceed directly to the
kill path — there is no live worker to deliver any future progress signal,
so an "active children" reprieve would only prolong a hung session.

> The previous `timeout` recovery branch (and its skip-Tier-2 carve-out)
> was retired by issue #1172 along with the wall-clock cap. Only
> `no_progress` and `worker_dead` reason kinds remain.

The pid is populated via the `on_sdk_started` callback that the messenger
invokes once the SDK subprocess spawns; see "Messenger callbacks" below.

### Kill path

If Tier 1 flags stuck AND all Tier 2 gates fail:

1. Look up `handle = _active_sessions.get(agent_session_id)` — the per-session
   `SessionHandle(task, pid)` registered at the top of `_execute_agent_session`.
2. Cancel `handle.task` and wait up to `TASK_CANCEL_TIMEOUT` (0.25s) for
   propagation. `CancelledError` flows through `BackgroundTask._task` →
   `asyncio.create_subprocess_exec`, terminating the SDK subprocess cleanly.
3. Increment `entry.recovery_attempts`.
4. If `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (2) → `finalize_session(entry, "failed", ...)`
   so the session reaches a terminal status with full audit history. Otherwise
   transition to `pending` and re-ensure a worker.
5. `StatusConflictError` from the transition is caught and logged at WARNING
   (race with the worker's own `CancelledError` handler is tolerated).

### Kill-switch

Set `DISABLE_PROGRESS_KILL=1` in the worker environment to suppress the kill
transition while **keeping** Tier 1 flagging and Tier 2 evaluation active. The
detector still logs a WARNING `[session-health] Would kill session ...`
for each would-be kill. This lets operators collect real data on detector
behavior before enabling kills during rollout.

### Metrics

Redis counters keyed by `<project_key>:session-health:`:

* `tier1_flagged_total` — every time `_has_progress` returned False (no
  fresh heartbeats AND no own-progress AND no live children). The
  previous `tier1_flagged_stdout_stale` counter was retired by issue
  #1172 with the stdout-stale path itself.
* `tier2_reprieve_total:{compacting|alive|children}` — reprieve by signal. The `compacting` gate was added by issue #1099 Mode 3; the two OS-level gates were introduced in #1036. The previous fourth `stdout` gate was retired by #1172.
* `kill_total` — actual kills (after Tier 2 failed and kill-switch off).
* `recoveries:{worker_dead|no_progress|tool_timeout}` — recoveries by reason
  kind. The previous `timeout` reason was retired by #1172. `tool_timeout`
  was added by #1270 for the per-tool timeout sub-loop and is recorded by
  the shared `_apply_recovery_transition` helper.
* `recoveries:zombie_uuid_no_output` — subset of `recoveries:no_progress`:
  emitted when the recovered session matches the zombie profile
  (`claude_session_uuid` set but `sdk_ever_output=False`, heartbeat stale
  past `NO_OUTPUT_BUDGET_SECONDS`). Distinguishes stale-zombie recoveries
  from normal startup-window recoveries (#1614).
* `tool_timeouts:{internal|mcp|default}` — per-tier hits from the per-tool
  timeout sub-loop (#1270, parallel 30s loop). Internal tier: lightweight
  built-ins (`Read`/`Glob`/`Grep`/`Edit`/`Write`/`NotebookEdit`/`ToolSearch`,
  30s budget). MCP tier: any `mcp__*` tool (120s budget). Default tier:
  everything else, including `Bash`/`Task`/`Skill` (300s budget, flat
  age-only kill). Each tier budget is env-tunable via
  `TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`,
  `TOOL_TIMEOUT_DEFAULT_SEC`. Sub-loop is gated by `TOOL_TIMEOUT_TIERS_DISABLED`
  (parity with `DISABLE_PROGRESS_KILL`). The `mid_run_quiescent_since`
  screen-liveness deferral that used to gate the default tier for granite PTY
  sessions (issue #1784) was deleted with the PTY substrate (issue #1924) —
  there is no screen to defer on for a `claude -p` subprocess turn, so the
  flat age-only kill now applies uniformly to every session.

**Distinguishing kill causes in dashboards:**
- `tier1_flagged_total` high → heartbeat writers are dying (clock/event-loop issue) OR sessions are genuinely stuck
- `tier2_reprieve_total:alive` high → processes alive but silent; monitor `reprieve_count` for operator warnings

### Per-session fields

| Field | Type | Purpose |
|-------|------|---------|
| `last_heartbeat_at` | DatetimeField | Queue-layer heartbeat |
| `last_sdk_heartbeat_at` | DatetimeField | Messenger watchdog heartbeat |
| `last_stdout_at` | DatetimeField | Last SDK stdout event — informational only since #1172 (no longer a kill or reprieve signal) |
| `started_at` | DatetimeField | Session start time |
| `recovery_attempts` | IntField | Kills only; finalizes at `MAX_RECOVERY_ATTEMPTS` |
| `reprieve_count` | IntField | Tier 2 saves — diagnostic only; triggers WARNING log after 3 |
| `current_tool_name` | Field (str, null) | Pillar A (#1172): name of the tool currently in flight, or None between tools |
| `last_tool_use_at` | DatetimeField | Pillar A (#1172): bumped at every tool boundary by pre/post tool-use hooks |
| `last_turn_at` | DatetimeField | Pillar A (#1172): bumped on every SDK `result` event |
| `recent_thinking_excerpt` | Field (str, null) | Pillar A (#1172): last 280 chars of extended-thinking content |

Pillar B's `self_report_sent_at` frequency-cap field was deleted by the schema diet (#1927) — the PM mid-work self-report it gated (`_emit_pm_self_report`) was retired 2026-05-06 and had no live writer.

All fields are included in `_AGENT_SESSION_FIELDS` so they round-trip
through delete-and-recreate paths (retry, orphan-fix, continuation fallback).

### Messenger callbacks (ORM-free)

`BossMessenger` defines three optional callback slots (`on_sdk_started`,
`on_heartbeat_tick`, `on_stdout_event`) with `notify_*` wrappers that catch
callback exceptions and log at WARNING, but only `on_sdk_started` and
`on_heartbeat_tick` are wired at its construction site — `on_stdout_event`'s
prior wiring was a dead, unlanded attempt at the headless-runner stdout
liveness signal and was removed by issue #1935; `last_stdout_at` is now
written by `SessionRunner._stamp_stdout_liveness` instead (see
[Headless Session Runner § Liveness signals](headless-session-runner.md#liveness-signals-sdk_ever_output-issue-1935)).
The messenger imports nothing from `models/`; the queue layer
(`_execute_agent_session`) defines closures that bump ORM fields and passes
them into the `BossMessenger` constructor.

`_active_sessions: dict[str, SessionHandle]` is the per-session registry the
health check uses to look up cancellable tasks and subprocess pids. It is
registered at the very top of `_execute_agent_session` (before any raise
site) and cleaned up via `task.add_done_callback` so the entry is always
popped — regardless of exception, `CancelledError`, or early return.

## Recovery Lock

During recovery, `data/recovery-in-progress` is created to prevent:
- Concurrent recovery attempts
- Updates running during recovery

The lock auto-expires after 5 minutes.

## Manual Operations

**Restart everything (bridge + watchdog + worker + web UI)**:
```bash
./scripts/valor-service.sh restart
```

The web UI leg is verified, not assumed (#2123): `restart_webui` kills *all*
listeners on the UI port, respawns `ui.app`, then bounded-polls until a PID is
bound on the port **and** `/health` answers before printing
`Web UI restarted (PID: ...)`. Serving is the primary success signal; if the
serving PID matches a pre-kill PID, an advisory PID-reuse warning goes to
stderr but the restart still succeeds. If the port never serves within the
verify window, a loud `WARNING: Web UI restart failed` goes to stderr and
`restart` exits non-zero — bridge and worker restarts always complete first
(the webui call is guarded so a webui-only failure cannot abort them under
`set -e`). Port and poll windows are env-overridable (`WEBUI_PORT`,
`WEBUI_POLL_INTERVAL`, `WEBUI_PORT_FREE_RETRIES`, `WEBUI_SERVE_RETRIES`,
`WEBUI_CURL_TIMEOUT`).

**Check health**:
```bash
python monitoring/bridge_watchdog.py --check-only
```

**View crash history**:
```python
from monitoring.crash_tracker import get_recent_crashes
crashes = get_recent_crashes(3600)  # last hour
```

**Enable auto-revert** (use with caution):
```bash
touch data/auto-revert-enabled
```

**Disable auto-revert**:
```bash
rm data/auto-revert-enabled
```

**Manual revert**:
```bash
./scripts/auto-revert.sh
```

## Hierarchy Health Check — Terminal Parent Skip (#1208)

The periodic `_agent_session_hierarchy_health_check()` in `agent/session_health.py` walks parents matched by `AgentSession.query.filter(status="waiting_for_children")` and finalizes any whose children are all terminal (delivering a Telegram summary on the success path via `schedule_pipeline_completion`).

**Stale-index defense**: index entries can lag behind the authoritative hash status. If a parent was killed but its `waiting_for_children` index entry was not srem'd at kill time, the parent will still appear in the candidate list. Without a guard, the check would draft and ship a final summary to the operator's chat for an already-killed session — the exact failure mode tracked in #1208.

The fix re-reads the parent's hash status (`get_authoritative_session(session_id)`) **at the top of every loop iteration**. If the hash status is in `TERMINAL_STATUSES`, the loop logs at INFO and `continue`s. This is defense-in-depth analogous to the running-index fix in #1006 — the underlying index corruption is a separate Popoto-layer concern, but the operational symptom (Telegram-spam after kill) is masked by the re-read.

```text
[session-health] Skipping terminal parent <agent_session_id> (status=killed) — index entry stale
```

If you see this line repeatedly for the same parent, the underlying index entry is stuck and warrants investigation. The plan tracks this as a follow-up to #1208.

The runner-entry guard in `agent/session_completion.py` (`_deliver_pipeline_completion` and `schedule_pipeline_completion`) is the second layer of the same defense — even if a stale-index call slips past the health-check guard, the runner short-circuits on the same terminal-status check before drafting or queuing any message. See [Session Lifecycle: Kill-is-Terminal Invariant](session-lifecycle.md#kill-is-terminal-invariant) for the full layered-defense write-up.

## Files

| File | Purpose |
|------|---------|
| `monitoring/crash_tracker.py` | Crash event logging and pattern detection |
| `monitoring/bridge_watchdog.py` | External health monitor (bridge process); includes `assess_update_flow()` and wedged-update-loop recovery |
| `bridge/liveness.py` | Positive liveness signal writers/readers: `record_update_received()`, `get_last_update_received()`, `record_probe_ok()`, `get_last_probe_ok()` |
| `monitoring/worker_watchdog.py` | External health monitor (worker process — heartbeat-based hung detection + active recovery via launchctl kickstart) |
| `bridge/hibernation.py` | Auth-expiry hibernation: classifier, flag file, replay |
| `scripts/auto-revert.sh` | Git revert and restart |
| `data/recovery-in-progress` | Recovery lock file |
| `data/auto-revert-enabled` | Auto-revert enable flag |
| `data/bridge-auth-required` | Hibernation flag file (presence = auth required) |
| `data/flood-backoff` | Flood-backoff expiry (JSON) |
| `data/last_connected` | Last-connected timestamp (ISO 8601) |
| `data/last_worker_connected` | Worker heartbeat file (mtime checked by `worker-status` and `worker_watchdog.py`) |
| `data/projects.last_known_good.json` | Last successfully-parsed `projects.json`, served on a partial/corrupt config read (Component 19) |
| `scripts/lib/launchctl.sh` | Shared `launchctl_bootstrap_fail_soft` helper: fail-soft recovery for `launchctl bootstrap` errno-5 races (Component 21) |
| `logs/watchdog.log` | Bridge watchdog output |
| `logs/worker_watchdog.log` | Worker watchdog output |
| `logs/worker/{session_id}.log` | FileOutputHandler dual-write output (persisted even during bridge downtime) |

## Design Principles

- **No complex process management** - Just kill, clean, restart
- **No deep git analysis** - Only HEAD~1 revert
- **No monitoring dashboards** - Telegram alerts only
- **No configuration** - Hardcoded 60s watchdog, sensible defaults
- **No external services** - Self-contained recovery
- **Minimal file-based state** - Flood-backoff and last-connected use simple files in `data/` for cross-restart persistence; all other state in Redis

## Related

- [Message Pipeline](message-pipeline.md) — deferred enrichment and zero-loss restart mechanisms
- [Message Reconciler](message-reconciler.md) — periodic scan for messages missed during live connection (complements startup catchup)
- [Session Transcripts](session-transcripts.md) — session lifecycle logging via AgentSession model
- [Sustainable Self-Healing](sustainable-self-healing.md) — circuit breaker and queue governance for long-term system health under load
