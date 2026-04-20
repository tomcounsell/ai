# Bridge Self-Healing & Resilience

The bridge includes a multi-layered self-healing system to recover from crashes without manual intervention.

## Import-Time Safety

**Problem**: `TELEGRAM_API_ID` set to a non-numeric value (e.g. the `.env.example` placeholder `12345****`) used to cause a `ValueError` at module import time. This crashed the bridge before any logging or graceful error handling could run, and trapped the watchdog in a restart loop because every restart attempt would also fail during import.

**Solution**: The `_parse_api_id()` helper in `bridge/telegram_bridge.py` wraps the `int()` conversion and returns `0` on any invalid or missing input, logging a warning to stderr. Module import now always succeeds regardless of env contents. The existing runtime credential check (`if not API_ID or not API_HASH`) remains the authoritative "fail loudly and exit" path once the bridge actually tries to connect.

The same defensive `try/except ValueError` pattern was applied to `tools/valor_telegram.py` and `scripts/reflections.py` where lazy `int(os.environ.get(...))` calls existed inside functions.

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
| 2 | Process running but logs stale | Kill stale + kill zombies + restart |
| 3 | Lock files present | Kill stale + kill zombies + clear locks + restart |
| 4 | Crash pattern detected | Kill stale + kill zombies + revert HEAD + restart (if enabled) |
| 5 | Recovery exhausted | Alert human via Telegram |

Zombie cleanup is integrated into recovery levels 2+ to free memory before restarting.

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

### 5. Log Rotation

Log rotation uses a dual-mechanism approach: Python-managed rotation for application logs, and shell rotation + newsyslog for launchd-managed stderr/stdout logs.

**Python-managed logs** (auto-rotate on write via `RotatingFileHandler`, 10MB max, 5 backups):
- `bridge.log` — configured in `bridge/telegram_bridge.py`
- `watchdog.log` — configured in `monitoring/bridge_watchdog.py`
- `reflections.log` — configured in `scripts/reflections.py`

**Shell-rotated logs** (`rotate_log()` in `valor-service.sh`, runs at bridge startup, 10MB max, 3 backups):
- `bridge.error.log`, `reflections_error.log`

**newsyslog safety net** (`config/newsyslog.valor.conf`, installed to `/etc/newsyslog.d/valor.conf`): Covers all 5 launchd-managed logs with hourly checks, 10MB max, 5 bzip2-compressed backups. Uses the `N` flag (no signal) because launchd holds file descriptors open. Acts as a backup if the bridge doesn't restart for extended periods.

### 6. Startup Redis Key Cleanup (`worker/__main__.py`)

**Problem**: Stale Redis entries with non-standard 60-character `agent_session_id` keys (from historical data or crashes) trigger popoto validation errors on every query scan, generating thousands of error log entries.

**Solution**: On worker startup, `AgentSession.rebuild_indexes()` (SCAN-based, production-safe) purges Redis set entries that point to missing or invalid objects. This is the first step in the worker's startup sequence. The bridge does not call `rebuild_indexes()` — index management is the worker's exclusive responsibility. See [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated cleanup reflection that supplements this startup check.

### 7. Agent Session Cleanup (`agent/session_health.py`)

**Problem**: Sessions with corrupted IDs (e.g., length 60 instead of expected 32 for uuid4) or invalid fields cause `ModelException` on every health check and startup recovery cycle, spamming error logs and potentially blocking worker startup.

**Solution**: `cleanup_corrupted_agent_sessions()` runs at worker startup (before recovery), during `/update` (before stale cleanup), and hourly as the `agent-session-cleanup` reflection. It detects unsaveable sessions and deletes them via the ORM. See also [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated index rebuild that supplements this.

**Phantom-record guard (issue #1069):** Before any iteration, results from `AgentSession.query.all()` pass through `_filter_hydrated_sessions()` to drop phantom instances — records whose fields are still Popoto `Field` descriptors, produced when orphan `$IndexF:AgentSession:*` members reference deleted hashes. Phantoms must never reach the mutation path: attribute access returns a descriptor repr (~60 chars), the length check mis-flags it as corrupt, and `.delete()` damages real records whose indexed-field values happen to match. After the mutation pass, `AgentSession.repair_indexes()` (instead of `rebuild_indexes()`) clears orphan `$IndexF` members at the source before rebuilding indexes from surviving hashes. The same filter is applied to five sibling iterators (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) to close the blind spot across the reflection fleet. The ORM-only policy is now strict: no raw-Redis `scan_iter`/`delete` fallback exists anywhere in `session_health.py`.

### 8. Health-Check Delivery Guard (`agent/agent_session_queue.py`)

**Problem**: When a worker crashes or is cancelled mid-execution, the session stays in `running` state for startup recovery. After `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s), the health check resets it to `pending` and the worker re-runs the session from scratch — including delivering a duplicate response. If each re-run also fails to complete cleanly, this repeats indefinitely, producing 6+ duplicate Telegram messages per session (#918).

**Solution**: `send_to_chat` now stamps `response_delivered_at` (a `DatetimeField` on `AgentSession`) when a response is successfully delivered to Telegram. The `_agent_session_health_check` inspects this field before recovering a session: if `response_delivered_at` is set, the session already delivered its final response and re-queuing would cause a duplicate. Instead, it calls `finalize_session(entry, "completed")` to mark it done.

Both the delivery stamp and the health-check guard are wrapped in `try/except` so that failures are logged but never crash the worker or health-check loop.

**Key fields**:
- `AgentSession.response_delivered_at` — nullable `DatetimeField`, set once on successful delivery
- Health-check path: `_agent_session_health_check()` → `should_recover` → delivery guard → `finalize_session()`

#### 8a. No-Progress Recovery for Shared-Worker-Key Sessions (#944)

**Problem**: A slugless dev session shares `worker_key` with any co-running PM session under the same project (both resolve to `project_key` via `AgentSession.worker_key`). `_agent_session_health_check` determined liveness via `worker_alive = _active_workers.get(worker_key) is not None and not worker.done()`. When a PM was alive under the same project, `worker_alive = True` even though the stuck dev session was not actually being handled — so the `not worker_alive` branch was skipped, and the dev session was only recovered after the 45-minute timeout (`AGENT_SESSION_TIMEOUT_DEFAULT` / `AGENT_SESSION_TIMEOUT_BUILD`) — 9x the intended 5-minute cadence.

**Solution**: A new `elif` branch in `_agent_session_health_check` recovers sessions that are `worker_alive=True`, past the `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) startup guard, AND have no progress signal. Progress is evaluated by `_has_progress(entry)` which returns True if ANY of three fields is set: `turn_count > 0`, a non-empty `log_path`, or a non-empty `claude_session_uuid`. Together these cover the full SDK subprocess warmup arc:

- `claude_session_uuid` — set when the SDK subprocess authenticates with the Claude API (seconds after launch)
- `log_path` — set once the session writes its first log entry (first tool call)
- `turn_count` — incremented on each full agent turn completion

A legitimately slow-starting BUILD session that takes 600s before its first turn will still have `claude_session_uuid` populated within seconds of auth, so the no-progress branch does not fire. The recovered session routes through the existing delivery guard, then the `is_local` split: local sessions become `abandoned`, project-keyed sessions become `pending` (re-queued with `priority=high` and a fresh `_ensure_worker` call). The PM-associated project-keyed worker will pop and execute the re-queued dev session because `_pop_agent_session` filters only by `project_key`/`status`, not by `session_type`.

**Observability**: Each recovery increments a project-scoped Redis counter keyed `{project_key}:session-health:recoveries:{reason_kind}` where `reason_kind` is one of `worker_dead`, `no_progress`, or `timeout`. The counter write is wrapped in `try/except` — failure cannot block recovery.

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

**Solution**: `monitoring/worker_watchdog.py` runs as a separate launchd service (`com.valor.worker-watchdog`, `StartInterval: 120`) alongside the worker. It checks the `data/last_worker_connected` heartbeat file on every tick:

| Heartbeat age | Status | Action |
|--------------|--------|--------|
| < 600s | `ok` | Log debug, exit |
| Missing (file absent) | `starting` | Skip — worker may be initializing |
| Worker PID absent | `down` | Log info — launchd handles restart |
| ≥ 600s (10 min) | `stale` | Kill worker (SIGTERM → SIGKILL if needed) so launchd restarts |

The threshold (600s = 2× health-loop interval of 300s) gives a healthy worker plenty of slack while catching genuine hangs within two watchdog ticks (240s).

**Check status**:
```bash
python monitoring/worker_watchdog.py --check   # print status, exit 0=ok, 1=stale/down
tail -f logs/worker_watchdog.log
```

**Installed by** `scripts/install_worker.sh` as `${SERVICE_LABEL_PREFIX}.worker-watchdog`.

## Two-tier no-progress detector

The periodic `_agent_session_health_check` (every 5 minutes) decides whether a
long-running session is making progress. To minimize **false-negatives**
(killing a working session) while still reaping genuinely wedged sessions, the
detector uses two independent tiers. (Issues #1036 and #1046.)

### Tier 1 — dual heartbeat + stdout-stale kill signal

Two independent 60-second writers update separate AgentSession fields:

| Field | Writer | When |
|-------|--------|------|
| `last_heartbeat_at` | Queue-layer `_heartbeat_loop` inside `_execute_agent_session` | Every `HEARTBEAT_WRITE_INTERVAL` (60s) |
| `last_sdk_heartbeat_at` | Messenger-layer `BackgroundTask._watchdog` via `on_heartbeat_tick` callback | Every 60s while SDK subprocess runs |

`_has_progress()` returns `True` if **either** heartbeat is within
`HEARTBEAT_FRESHNESS_WINDOW` (90s) **and** stdout is fresh. Tier 1 flags a
session as potentially stuck when **both** heartbeats are stale, OR when both
heartbeats are fresh but stdout has been absent for too long (see below).

**Stdout-stale kill signal (#1046):** The `_has_progress()` function includes a
Tier 1 extension to catch the **alive-but-silent failure mode**: a `claude -p`
subprocess can emit heartbeats every 60s (appearing healthy) yet produce zero
stdout for hours — e.g. when the Claude API hangs or an MCP tool blocks. Even
with both heartbeats fresh, `_has_progress()` returns `False` when:

1. **`last_stdout_at` is set and stale** — `(now - last_stdout_at) >=
   STDOUT_FRESHNESS_WINDOW` (600s = 10 min). The session is flagged even with
   fresh heartbeats; Tier 2 gate (c) "alive" will typically reprieve it while
   the subprocess is still running. Once the process goes non-alive, all Tier 2
   gates fail and the kill path executes.

2. **`last_stdout_at` is None** (session never produced stdout) **and `started_at`
   is older than `FIRST_STDOUT_DEADLINE`** (300s = 5 min) — the session has not
   emitted any stdout for 5+ minutes of runtime. This preserves warmup tolerance
   from #1036 (young sessions with no stdout are fine) while bounding the
   "silent from the start" failure case.

**Constants** (both env-tunable):

| Constant | Default | Env var | Purpose |
|----------|---------|---------|---------|
| `STDOUT_FRESHNESS_WINDOW` | 600s | `STDOUT_FRESHNESS_WINDOW_SECS` | Tier 1 stdout-stale threshold; also Tier 2 gate (e) reprieve window |
| `FIRST_STDOUT_DEADLINE` | 300s | `FIRST_STDOUT_DEADLINE_SECS` | Tier 1 deadline for sessions that have never produced stdout |

**Reprieve behavior for alive-but-silent sessions:** When Tier 1 stdout-stale
fires, `_tier2_reprieve_signal()` is called. Gate (c) "alive" will reprieve the
session as long as the subprocess is running — this is intentional; a running
process should not be killed prematurely. The actual kill latency for a hung-
but-alive process is bounded to `STDOUT_FRESHNESS_WINDOW + one health-check tick`
after the process eventually goes non-alive or the absolute session timeout fires.

**Operator alert:** After 3 Tier 2 reprieves, the reprieve log message is
escalated from `INFO` to `WARNING`, signaling that the session may be in an
indefinite alive-but-silent reprieve loop.

### Tier 2 — activity-positive reprieve gates

When Tier 1 flags a session, the health check calls `_tier2_reprieve_signal()`
which evaluates three OS-level liveness checks via `psutil`:

| Gate | Check | Return |
|------|-------|--------|
| (c) alive    | `psutil.Process(pid).status()` not in `{zombie, dead, stopped}` | `"alive"` |
| (d) children | `psutil.Process(pid).children()` non-empty (tool execution active) | `"children"` (preferred) |
| (e) stdout   | `last_stdout_at` within `STDOUT_FRESHNESS_WINDOW` (600s) | `"stdout"` |

Any **one** passing gate reprieves the kill. The reprieve signal is logged and
`reprieve_count` on the AgentSession is incremented for post-hoc analysis.
`recovery_attempts` is NOT incremented on reprieve.

**Scope:** Tier 2 reprieve applies **only** to `no_progress` recoveries.
`worker_dead` and `timeout` recoveries skip Tier 2 entirely and proceed
directly to the kill path. The rationale:

* `worker_dead` — there is no live worker to deliver any future progress
  signal, so an "active children" reprieve would only prolong a hung session.
* `timeout` — the configured session timeout is an absolute cap. Allowing an
  activity-positive gate to defeat the timeout would make the cap
  unenforceable; a runaway session that keeps spawning child processes would
  never be killed.

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

* `tier1_flagged_total` — every time both heartbeats were stale (heartbeat-stale path).
* `tier1_flagged_stdout_stale` — every time Tier 1 fired due to stale stdout or missed `FIRST_STDOUT_DEADLINE` (stdout-stale path, #1046). Use this counter to distinguish the alive-but-silent failure mode from dead-heartbeat kills in dashboards.
* `tier2_reprieve_total:{alive|children|stdout}` — reprieve by signal.
* `kill_total` — actual kills (after Tier 2 failed and kill-switch off).

**Distinguishing kill causes in dashboards:**
- `tier1_flagged_total` high, `tier1_flagged_stdout_stale` low → heartbeat writers are dying (clock/event-loop issue)
- `tier1_flagged_stdout_stale` high → sessions hang silently (API/MCP tool issue)
- `tier2_reprieve_total:alive` high → processes alive but silent; monitor `reprieve_count` for operator warnings

### Per-session fields

| Field | Type | Purpose |
|-------|------|---------|
| `last_heartbeat_at` | DatetimeField | Queue-layer heartbeat |
| `last_sdk_heartbeat_at` | DatetimeField | Messenger watchdog heartbeat |
| `last_stdout_at` | DatetimeField | Last SDK stdout event; Tier 1 stdout-stale input (#1046) |
| `started_at` | DatetimeField | Session start time; `FIRST_STDOUT_DEADLINE` anchor (#1046) |
| `recovery_attempts` | IntField | Kills only; finalizes at `MAX_RECOVERY_ATTEMPTS` |
| `reprieve_count` | IntField | Tier 2 saves — diagnostic only; triggers WARNING log after 3 |

All fields are included in `_AGENT_SESSION_FIELDS` so they round-trip
through delete-and-recreate paths (retry, orphan-fix, continuation fallback).

### Messenger callbacks (ORM-free)

`BossMessenger` exposes three optional callbacks (`on_sdk_started`,
`on_heartbeat_tick`, `on_stdout_event`) with `notify_*` wrappers that catch
callback exceptions and log at WARNING. The messenger imports nothing from
`models/`; the queue layer (`_execute_agent_session`) defines closures that
bump ORM fields and passes them into the `BossMessenger` constructor.

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

## Files

| File | Purpose |
|------|---------|
| `monitoring/crash_tracker.py` | Crash event logging and pattern detection |
| `monitoring/bridge_watchdog.py` | External health monitor (bridge process) |
| `monitoring/worker_watchdog.py` | External health monitor (worker process — heartbeat-based hung detection) |
| `bridge/hibernation.py` | Auth-expiry hibernation: classifier, flag file, replay |
| `scripts/auto-revert.sh` | Git revert and restart |
| `data/recovery-in-progress` | Recovery lock file |
| `data/auto-revert-enabled` | Auto-revert enable flag |
| `data/bridge-auth-required` | Hibernation flag file (presence = auth required) |
| `data/flood-backoff` | Flood-backoff expiry (JSON) |
| `data/last_connected` | Last-connected timestamp (ISO 8601) |
| `data/last_worker_connected` | Worker heartbeat file (mtime checked by `worker-status` and `worker_watchdog.py`) |
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
