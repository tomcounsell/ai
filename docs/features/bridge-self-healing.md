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

### 7. Agent Session Cleanup (`agent/agent_session_queue.py`)

**Problem**: Sessions with corrupted IDs (e.g., length 60 instead of expected 32 for uuid4) or invalid fields cause `ModelException` on every health check and startup recovery cycle, spamming error logs and potentially blocking worker startup.

**Solution**: `cleanup_corrupted_agent_sessions()` runs at worker startup (before recovery), during `/update` (before stale cleanup), and hourly as the `agent-session-cleanup` reflection. It detects unsaveable sessions, deletes them (with fallback to direct Redis key deletion), and rebuilds indexes to clear orphaned `$IndexF`/`$KeyF`/`$SortF` entries. See also [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated index rebuild that supplements this.

### 8. Perplexity Provider Error Handling (`tools/web/providers/perplexity.py`)

**Problem**: The Perplexity search provider had a bare `except Exception` that silently swallowed all errors, including 401 Unauthorized responses from expired API keys.

**Solution**: Added explicit `httpx.HTTPStatusError` handling before the generic catch. 401 errors now log a clear warning message directing the operator to refresh credentials in `.env`. Other HTTP errors are also logged with their status code.

### 9. Service Installation

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

### 10. Flood-Backoff Persistence (`bridge/telegram_bridge.py`)

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

### 11. Dynamic Catchup Lookback (`bridge/catchup.py`)

**Problem**: The fixed 60-minute `CATCHUP_LOOKBACK_MINUTES` means that after a multi-hour outage, messages older than 60 minutes are silently missed forever.

**Solution**: The bridge persists a `data/last_connected` ISO 8601 timestamp file. On startup, catchup reads this timestamp and uses it to compute the lookback window dynamically instead of using the fixed 60-minute default. The lookback is capped at 24 hours to avoid scanning excessive history.

**Timestamp updates**:
- Written on successful Telegram connect
- Updated every 5 minutes via the heartbeat loop
- Written on graceful shutdown (SIGTERM/SIGINT)

**Fallback**: If the file is missing or invalid, the default 60-minute lookback is used. Redis dedup (`is_duplicate_message`) prevents double-processing even if the window overlaps with already-handled messages.

**Telethon duplicate dialog guard**: Telethon's `get_dialogs()` can return the same supergroup twice — once as a channel entity and once as its linked discussion group. Without a guard, catchup would scan the same group twice and enqueue the same messages twice, causing duplicate Telegram replies. The catchup scanner deduplicates by `dialog.id` (`seen_chat_ids: set[int]`) before scanning each group.

**Logger handler guard**: `telegram_bridge.py` may execute its module-level setup twice in some launch configurations (once as `__main__`, once as `bridge.telegram_bridge`). This would add a second `RotatingFileHandler` to the root logger, doubling every log line. A guard checks for an existing handler with the same log file path before adding a new one.

### 12. Update Polling (`com.valor.update`)

**Problem**: Code pushes to main could take up to 12 hours to propagate to all machines, since the update plist only ran at 6 AM and 6 PM.

**Solution**: The `com.valor.update` launchd plist uses `StartInterval` of 1800 seconds (30 minutes) to poll for updates frequently. Each invocation runs `scripts/remote-update.sh`, which:
1. Acquires a lock (`data/update.lock`) to prevent concurrent runs
2. Runs `git fetch` + `git pull` via `scripts/update/run.py --cron`
3. If new commits arrived: syncs dependencies (if dep files changed), writes `data/restart-requested`
4. The bridge session queue detects the restart flag and triggers a graceful restart after in-flight sessions complete

**Verify polling is active**:
```bash
launchctl list | grep com.valor.update
```

**Check update logs**:
```bash
tail -f logs/update.log
```

**Manual override**: The Telegram `/update` command continues to work for immediate updates.

### 13. Bridge Hibernation (`bridge/hibernation.py`)

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
| `monitoring/bridge_watchdog.py` | External health monitor |
| `bridge/hibernation.py` | Auth-expiry hibernation: classifier, flag file, replay |
| `scripts/auto-revert.sh` | Git revert and restart |
| `data/recovery-in-progress` | Recovery lock file |
| `data/auto-revert-enabled` | Auto-revert enable flag |
| `data/bridge-auth-required` | Hibernation flag file (presence = auth required) |
| `data/flood-backoff` | Flood-backoff expiry (JSON) |
| `data/last_connected` | Last-connected timestamp (ISO 8601) |
| `logs/watchdog.log` | Watchdog output |
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
