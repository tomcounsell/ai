# Bridge Self-Healing & Resilience

The bridge includes a multi-layered self-healing system to recover from crashes without manual intervention.

## Import-Time Safety

The `_parse_api_id()` helper in `bridge/telegram_bridge.py` wraps the `int()` conversion of `TELEGRAM_API_ID` and returns `0` on any invalid or missing input, logging a warning to stderr. Module import succeeds regardless of env contents, so a non-numeric placeholder (e.g. the `.env.example` value `12345****`) cannot raise a `ValueError` at import time and trap the watchdog in a restart loop. The runtime credential check (`if not API_ID or not API_HASH`) remains the authoritative "fail loudly and exit" path once the bridge actually tries to connect.

`tools/valor_telegram.py` applies the same defensive `try/except ValueError` pattern around its lazy `int(os.environ.get(...))` calls.

The same failure class — a raise at import time trapping the watchdog in a restart loop — also applies to `projects.json` parsing; see Component 19 (Guarded Config Read) below.

## Components

### 1. Session Lock Cleanup (`bridge/telegram_bridge.py`)

Before attempting to connect, the bridge:
1. Uses `lsof` to find processes holding session-related files
2. Terminates stale processes (>60 seconds old) that aren't the current process using SIGTERM/SIGKILL escalation:
   - Sends SIGTERM first to request graceful shutdown
   - Waits up to 5 seconds for the process to exit
   - Falls back to SIGKILL only if the process is still alive
3. Clears orphaned lock/journal files
4. Adds jitter to prevent thundering herd on restart

**Retry Logic**: General connection retry with exponential backoff and jitter (2s to 256s cap, 8 attempts max). Covers all Telethon errors. See [Bridge Resilience](bridge-resilience.md) for details.

### 2. Crash Tracker (`monitoring/crash_tracker.py`)

Logs bridge start/crash events with:
- Timestamp
- Current git commit SHA
- Commit age in seconds
- Crash reason (if available)

Events are stored in Redis via the crash tracker module.

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

**Log isolation.** Logging configuration (`_configure_logging()`: mkdir + the rotating
file handler + formatter) runs only from the `if __name__ == "__main__":` guard, never at import.
Importing this module — including transitively, via `scripts.update.service` → `scripts.log_rotate` —
has zero logging side effects. Every line the watchdog writes to
`logs/watchdog.log` comes from a real entry-point invocation and appears once. Submodule records
arrive unformatted via `logging.lastResort` (WARNING+) or not at all (INFO — dropped at the call
site). See [Watchdog Log Isolation](watchdog-log-isolation.md) for the full Data Flow table and the
`scripts/log_rotate.py` counterpart.

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

**4-Level Recovery Escalation, plus a decoupled crash-storm signal:**

| Level | Condition | Action |
|-------|-----------|--------|
| 1 | Process not running | Log crash event via `crash_tracker.log_crash("bridge_dead_on_watchdog_check")` + simple restart (launchd) |
| 2 | Process running but logs stale — or update loop wedged | Kill stale + kill zombies + restart (the bridge always catches up missed messages on startup — see below) |
| 3 | Lock files present | Kill stale + kill zombies + clear locks + restart |
| 4 | Crash pattern detected | Kill stale + kill zombies + revert HEAD + restart (if enabled); if auto-revert is disabled or the revert fails, falls through to `_recovery_exhausted()`, which logs `CRITICAL` and records `log_crash("Recovery exhausted")` |

`recovery_level` has no level 5. Two independent signals are computed alongside `recovery_level`, both on `HealthStatus`:

- **`human_alert_needed`** — set when `get_recent_crashes(1800)` (30 min window) returns `>= CRASH_STORM_THRESHOLD` (default 5, env-overridable) crashes. It is a **diagnostic flag only**: it drives the `--check-only` output line and nothing else. Nothing pushes a notification anywhere.

**The watchdog records; it does not deliver.** The durable crash-storm signal is the `CRITICAL` line in `logs/watchdog.log` and `log_crash()`, written synchronously in the watchdog process on every tick regardless of the state of Redis, the worker, or the bridge. Read it with `python monitoring/bridge_watchdog.py --check-only` or `tail logs/watchdog.log`. Any push notification must originate from a transport that does not depend on the worker or an LLM turn.

- **`restart_circuit_open`** — a reason-aware restart throttle for *non-wedge* storms. `CrashEvent.reason` classifies each crash; when the storm is not wedge-dominated (`wedge_count < len(recent_crashes) * WEDGE_DOMINANCE_FRACTION`, default fraction `0.9` — a bare `0.5` majority would let a 50/50 wedge+real-bug storm through), `restart_circuit_open` is set and `run_health_check()` skips `execute_recovery()` for that tick entirely. A **wedge-dominated** storm always leaves this False, so the wedge detector's capped restart (level 2, `launchctl kickstart` with `catch_up=True`) keeps running every tick with no attempt ceiling. The exemption rests on a wedge verdict being trustworthy: the detector requires positive recovery evidence and resets its silence clock on restart. Each restart is a SIGKILL plus a full startup dialog scan, so an un-throttled loop over a *false* verdict is not cheap.

Zombie cleanup is integrated into recovery levels 2+ to free memory before restarting.

### 3a. Update-Loop Wedged Detector

Telethon can stop delivering `NewMessage` events silently — the bridge process is alive, TCP is connected (the reconciler's `get_dialogs()` succeeds), but the update loop has stopped firing. No error, no disconnect, no log. Messages are silently dropped until the bridge is restarted.

Three liveness signals written to Redis, read by the watchdog on every 60-second tick:

| Redis Key | Writer | Meaning |
|-----------|--------|---------|
| `bridge:last_update_received` | NewMessage handler in `bridge/telegram_bridge.py`, before dedup | A Telethon update event was delivered to the bridge |
| `bridge:last_probe_ok` | Reconciler in `bridge/reconciler.py`, after successful `get_dialogs()` | The Telegram API/TCP layer is reachable |
| `bridge:last_missed_recovery` | Reconciler in `bridge/reconciler.py`, when a scan recovers ≥1 message | Telegram had messages the live update path never delivered |

All three are managed by `bridge/liveness.py` (freeform Redis keys, not Popoto-managed; raw get/set is correct). Every writer is best-effort — any exception logs a WARNING and never raises, matching the safety contract from `bridge.dedup.record_last_event`.

**Detection logic** (`assess_update_flow()` in `monitoring/bridge_watchdog.py`):

The PRIMARY rule fires when all of these hold:
1. Bridge process is alive and its start time is readable
2. `bridge:last_probe_ok` is fresh (API/TCP layer is healthy)
3. The live update path has delivered nothing for `UPDATE_STALENESS_CEILING`, measured from the **later** of `bridge:last_update_received` and the bridge process's own start time
4. Bridge is past the startup grace window (`STARTUP_GRACE_SECONDS`)
5. `bridge:last_missed_recovery` is within the same window **and postdates this process's startup grace** — the reconciler actually recovered a message the live path missed, and did so after the window in which a restart's own backfill lands

A SECONDARY accelerator applies the same shape at `UPDATE_STALENESS_WARN`, requiring the recovery evidence to be that recent too.

**Key design decisions**:
- **Silence is not evidence.** Conditions 2–4 alone describe a quiet account just as well as a wedged one: the reconciler refreshes `last_probe_ok` every 180s, so it is always fresh on a connected bridge, and four hours with nobody sending anything is routine.
- **The corroborating signal must come from outside the handler under suspicion.** `bridge:last_update_received` and the per-chat `bridge:last_event:*` keys are both written by the `NewMessage` handler, so neither can testify that the handler has stopped — their silence is equally consistent with an idle account. The reconciler reaches Telegram over an independent API path, so a message it recovers is proof the live path missed one, and a genuinely quiet account produces no such evidence.
- **The evidence is bounded to this process too.** The evidence window and the silence window are the same length, so a stamp made just before a routine restart would stay admissible for a full ceiling afterwards. That matters because every restart *creates* a gap and catchup plus the reconciler recover it on the way back up — a restart reliably stamps this key inside its own grace window. Four quiet hours after any restart would then yield one spurious verdict. Requiring the stamp to postdate `start_ts + STARTUP_GRACE_SECONDS` excludes the restart's own backfill, on the same principle as the silence clock: bound the verdict to the process it accuses.
- **Measuring silence from process start.** Nothing seeds `bridge:last_update_received` on restart, so a verdict measured from the beacon alone survives the restart meant to cure it and re-fires on the first tick past the grace window — one SIGKILL every ~6 minutes (5-minute grace, 60-second tick), indefinitely. Taking the later of the beacon and process start means a restart clears the accusation and a real recurrence still re-fires after another full ceiling of silence.
- **`last_probe_ok` as disconfirmation guard**: if the probe itself is stale, the bridge may be disconnected. A disconnect should be recovered by level 1 (process dead) or resolved by Telethon's reconnect — not treated as a wedge. Restarting on disconnect when Telethon is mid-reconnect would interrupt the reconnection attempt. The wedge detector only fires when probe is fresh.
- **Startup grace window**: `bridge:last_update_received` is absent on cold start (bridge has not received any messages yet). The grace window prevents false wedge verdicts during startup before Telegram delivers the first event.
- **What this detector cannot see, and why that trade is accepted.** Requiring positive evidence buys the end of the storm at the cost of a real blind spot.

  `record_probe_ok()` fires at `bridge/reconciler.py:149`, straight after `get_dialogs()` and **before** the per-chat scan loop at `:151`, whose body ends in `except Exception ... continue` (`:403-405`). So a **half-wedged client** — dialogs resolve, but every per-chat history fetch throws — keeps `last_probe_ok` fresh, recovers nothing, and stamps no evidence. The detector sees fresh probe + silence + no evidence, which is byte-identical to a quiet account, and stays quiet. `test_quiet_account_past_ceiling_is_not_wedged` passes on that state for exactly that reason.

  No cleverer detector removes this. Any signal the wedged component itself produces is circular, and the only independent observer is the reconciler — when *it* is the thing failing, there is nothing left to ask. Some false negative is the unavoidable price of refusing to treat silence as evidence, and a blind spot in one failure mode is a better trade than a SIGKILL every six minutes across every quiet night. A restart in this state re-arms the same verdict six minutes later, so unless the restart happens to cure the fault it produces the storm rather than a fix.

  What bounds the exposure: **nothing does, automatically.** Nothing monitors the scan loop, so the state persists for as long as the fault does — until some unrelated cause happens to restart the bridge, which is luck rather than a mechanism. The failure is loud but unwatched: `[reconciler] Error scanning %s` at ERROR with a traceback, once per chat per 180-second scan, which means the evidence is sitting in the logs the whole time with nothing reading it. Treat that as the honest status, not as a safety net. Turning that log signal into a monitored one is an open design question (whether persistent all-chat scan failure should re-enter the restart rule as positive evidence in its own right, or should only page) that deserves deliberate treatment.

  The evidence floor in the bullet above narrows the admissible window further; in practice the cost is small. A genuinely wedged bridge that is still receiving traffic gets its evidence re-stamped by every 180-second scan, so the floor only discards the first stamp and evidence returns within a scan or two of the grace window closing. The floor delays nothing in the half-wedge case, where no stamp is ever written at all.

- **Unreadable process age = fail-safe**: if there is no bridge pid, or `get_process_start_ts()` returns `None`, the detector treats the verdict as inconclusive and suppresses the restart. Process age is not merely the grace window — it is the floor for the silence measurement, so without it the verdict cannot be bounded to the current process.

**Recovery**: when `update_flow_live=False`, the watchdog sets `recovery_level = max(recovery_level, 2)` and calls the standard `restart_bridge()` (a `launchctl kickstart` — it takes no arguments). The level cap of 2 is hard — the wedge detector never escalates to level 4 (auto-revert), regardless of how many consecutive wedge ticks occur. Lossless backfill is inherent to bridge startup, not a flag the watchdog passes: the bridge unconditionally initializes Telethon with `catch_up=True` and runs a missed-message catchup scan on every connect, so any restart recovers the messages that arrived during the wedge window. A recurring wedge always gets its capped restart on every tick — the crash-count storm this produces (all `bridge_update_loop_wedged`-reason crashes) is wedge-dominated, so `restart_circuit_open` stays False and the restart is never throttled.

**Log signals**:
```
[WARNING] bridge_update_loop_wedged: update loop stopped delivering events while process is running and API layer is healthy. Issue: update loop wedged: no live update for 262m (threshold 240m, last_update_received=310m ago), last_probe_ok=2m ago, and the reconciler recovered missed messages 4m ago — Telethon stopped delivering events while the API layer is healthy
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

The `_safe_abandon_session()` helper wraps each `session.save()` call in `fix_unhealthy_session()` with a `ModelException` catch. When a save fails due to a stale/duplicate key (a session concurrently deleted or modified by another process):
1. The error is logged at WARNING level (visible in `bridge.log` for monitoring, but not spamming `bridge.error.log`)
2. The watchdog continues processing the next session instead of propagating the error up to the loop-level handler
3. The outer `check_all_sessions()` still has a `ModelException` catch as a safety net for any other save paths

This is distinct from the loop-level crash guard (which marks sessions as `failed`). The `_safe_abandon_session()` helper handles the common case of race conditions during the abandon flow itself.

### 4a. Session Liveness Tick Counter (`monitoring/session_watchdog.py`)

`_publish_liveness_ticks()` advances a counter on the session's originating Telegram message every `HEARTBEAT_TICK_INTERVAL_SECONDS`, and at a hard ceiling refuses to advance and steers the session to publish a progress message instead. The counter is pure wall-clock duration: it asserts that the watchdog has eyes on the session and makes no stall claim. A single reaction slot has a single writer.

Full design, precedence table, Redis keys, and degradation path: [Session Liveness Tick Counter](session-liveness-tick-counter.md).

### 5. Log Rotation

Log rotation uses a three-layer approach: Python-managed rotation for application logs, shell rotation at service startup for launchd-managed stderr/stdout logs, and a user-space LaunchAgent for between-restart coverage. See [Log Rotation](log-rotation.md) for the full design.

**Python-managed logs** (auto-rotate on write via `RotatingFileHandler`, 10MB max, 5 backups):
- `bridge.log` — configured in `bridge/telegram_bridge.py`
- `watchdog.log` — configured in `monitoring/bridge_watchdog.py`

**Shell-rotated logs** (`rotate_log()` in `valor-service.sh`, runs at bridge startup, 10MB max, 3 backups):
- `bridge.error.log`, `reflections_error.log`

**User-space LaunchAgent safety net** (`com.valor.log-rotate.plist` + `scripts/log_rotate.py`): runs every 30 minutes under the user's launchd session and rotates any `logs/*.log` file over 10 MB (3 backups retained). Covers all launchd-managed logs between service restarts — no root needed.

### 6. Startup Redis Key Cleanup (`worker/__main__.py`)

On worker startup, `AgentSession.rebuild_indexes()` (SCAN-based, production-safe) purges Redis set entries that point to missing or invalid objects. This is the first step in the worker's startup sequence. The bridge does not call `rebuild_indexes()` — index management is the worker's exclusive responsibility. See [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated cleanup reflection that supplements this startup check.

### 7. Agent Session Cleanup (`agent/session_health.py`)

`cleanup_corrupted_agent_sessions()` runs at worker startup (before recovery), during `/update` (before stale cleanup), and hourly as the `agent-session-cleanup` reflection. It detects invalid sessions via `is_valid()` and deletes them via the ORM; healthy rows are left field-untouched, receiving only a targeted `EXPIRE` keepalive. It also performs a cross-process orphan reap pass against the OS process table at the end of each call, returning `{"corrupted": int, "orphans": int}` for both legs of work; reaper failures are logged at WARNING and reported as `orphans=0` so they never abort the corrupted-record pass. See also [Popoto Index Hygiene](popoto-index-hygiene.md) for the daily automated index rebuild that supplements this, and [Cross-Process Orphan Reap](#cross-process-orphan-reap) below for the reap mechanics.

#### Cross-Process Orphan Reap

When a worker dies ungracefully (panic, SIGKILL, restart-without-graceful-shutdown), its `claude_agent_sdk/_bundled/claude` child and the 4+ `mcp_servers/*.py` grandchildren are reparented to launchd (PID 1) and persist indefinitely. They hold file handles, consume RAM/CPU, and keep an Anthropic API session warm.

`_reap_orphan_session_processes()` (in `agent/session_health.py`) runs hourly inside the `agent-session-cleanup` reflection (and is also the body of the worker startup shim). It scans the OS process table via psutil for processes whose `cmdline` matches `claude_agent_sdk/_bundled/claude` or `mcp_servers/*.py` AND whose `PPID == 1`, then for each candidate:

1. **Self-suicide guard** — builds a skip-set from `os.getpid()` plus every value under the `worker:registered_pid:*` Redis key prefix (TTL 24h, written by `register_worker_pid()` at worker startup and refreshed every health-loop tick). Any worker whose PID is in the skip-set is never touched. This is structural — even if the cmdline regex were ever extended to match the worker pattern, live workers cannot be self-killed. Required because under `launchd KeepAlive=true` every live worker has `PPID == 1` by design.
2. **Per-PID heartbeat gate** — looks up the owning `AgentSession` via `find_live_session_by_pid` (a bounded forward scan over the low-cardinality `status` index — there is no indexed pid field). The pid is the fenced `exec_pid` stamped at spawn; it is not cleared between turns, and staleness is detected by comparing `pid_create_time` via `agent/pid_fence.py::fence_is_live` rather than by absence. If the owning session has `last_heartbeat_at` younger than `ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS` (1800s = 30 min), the kill is skipped. MCP candidates without a direct `exec_pid` mapping inherit their parent process's session via `proc.parent().pid`.
3. **Descendant-tree walk** — `proc.children(recursive=True)` is captured BEFORE `terminate()` so MCP grandchildren are reaped along with the parent.
4. **Two-tick SIGKILL escalation with create-time verification** — parent and descendants get SIGTERM and `(pid, create_time)` is staged on the module-level `_pending_sigkill_orphans: set[tuple[int, float]]`. At the start of the next reflection tick the set is drained: each PID's `proc.create_time()` is compared against the staged value within `1e-3` epsilon; on match `proc.kill()` (SIGKILL); on mismatch the SIGKILL is skipped because macOS recycled the PID. The staged set is always cleared after drain — a PID never lives across more than one tick.
5. **Two-counter scheme** — when the owning session is known, increment `{project_key}:session-health:orphan_process_reaped` (project-scoped). When unknown, increment `session-health:orphan_process_reaped:{worker_hostname}` (hostname-scoped) so true unowned orphans are not falsely attributed to a project.

**Kill switch**: `DISABLE_ORPHAN_PROCESS_REAP=1` short-circuits the entire pass (parity with `DISABLE_ORPHAN_REAP` for the in-process reaper and `DISABLE_PROGRESS_KILL` for the no-progress detector).

**Distinction from sibling reapers**:
- vs. the in-process reaper: the in-process reaper iterates `_active_sessions` (handles known to THIS worker) and asks "is the owning row terminal?". It cannot detect orphans whose parent worker is gone — that gap is what the cross-process reap covers.
- vs. `monitoring/bridge_watchdog.py::kill_zombie_processes()`: the watchdog runs every 60s and kills `claude`/`pyright` processes older than 2h via raw `os.kill`. The cross-process reap runs every 60min, scopes by PPID==1 + heartbeat-stale + signature, walks descendant trees, and uses psutil for PID-reuse safety. Both swallow `ProcessLookupError`/`NoSuchProcess` so double-kill is safe.

**Worker process reaping is intentionally OUT OF SCOPE.** Stranded sibling workers are reparented by launchd already; the worker-signature + PPID==1 filter would self-suicide every live worker on every reflection tick. See [agent-session-health-monitor.md](agent-session-health-monitor.md) for the canonical write-up of all three orphan reapers.

**Phantom-record guard:** Before any iteration, results from `AgentSession.query.all()` pass through `_filter_hydrated_sessions()` to drop phantom instances — records whose fields are still Popoto `Field` descriptors, produced when orphan `$IndexF:AgentSession:*` members reference deleted hashes. Phantoms must never reach the mutation path: attribute access returns a descriptor repr (~60 chars), the length check mis-flags it as corrupt, and `.delete()` damages real records whose indexed-field values happen to match. After the mutation pass, `AgentSession.repair_indexes()` (instead of `rebuild_indexes()`) clears orphan `$IndexF` members at the source before rebuilding indexes from surviving hashes. The same filter is applied to five sibling iterators (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) to close the blind spot across the reflection fleet. The ORM-only policy is strict: no raw-Redis `scan_iter`/`delete` fallback exists anywhere in `session_health.py`.

### 8. Health-Check Delivery Guard (`agent/agent_session_queue.py`)

`send_to_chat` stamps `response_delivered_at` (a `DatetimeField` on `AgentSession`) when a response is successfully delivered to Telegram. The `_agent_session_health_check` inspects this field before recovering a session: if `response_delivered_at` is set, the session already delivered its final response and re-queuing would cause a duplicate. Instead, it calls `finalize_session(entry, "completed")` to mark it done.

Both the delivery stamp and the health-check guard are wrapped in `try/except` so that failures are logged but never crash the worker or health-check loop.

**Key fields**:
- `AgentSession.response_delivered_at` — nullable `DatetimeField`, set once on successful delivery
- Health-check path: `_agent_session_health_check()` → `should_recover` → delivery guard → `finalize_session()`

#### 8a. No-Progress Recovery for Shared-Worker-Key Sessions

A slugless dev session shares `worker_key` with any co-running PM session under the same project (both resolve to `project_key` via `AgentSession.worker_key`). Liveness is determined by `worker_alive = _active_workers.get(worker_key) is not None and not worker.done()`. A no-progress `elif` branch in `_agent_session_health_check` recovers sessions that are `worker_alive=True`, past the `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) startup guard, AND have no progress signal, so a stuck dev session sharing a key with a live PM is still recovered rather than skipped. Progress is evaluated by `_has_progress(entry)` which returns True if ANY of three fields is set: `turn_count > 0`, a non-empty `log_path`, or a non-empty `claude_session_uuid`. Together these cover the full SDK subprocess warmup arc:

- `claude_session_uuid` — set when the SDK subprocess authenticates with the Claude API (seconds after launch)
- `log_path` — set once the session writes its first log entry (first tool call)
- `turn_count` — incremented on each full agent turn completion

These own-progress fields are **gated on heartbeat freshness** — they are only evaluated when `last_heartbeat_at` is within `NO_OUTPUT_BUDGET_SECONDS` (1800s). A session whose `_heartbeat_loop` has exited (heartbeat frozen) does not pass this check via a sticky `claude_session_uuid` alone.

A legitimately slow-starting BUILD session that takes 600s before its first turn still has `claude_session_uuid` populated within seconds of auth, so the no-progress branch does not fire (the heartbeat is fresh during legitimate long-running turns). The recovered session routes through the existing delivery guard, then the `is_local` split: local sessions become `abandoned`, project-keyed sessions become `pending` (re-queued with `priority=high` and a fresh `_ensure_worker` call). The PM-associated project-keyed worker pops and executes the re-queued dev session because `_pop_agent_session` filters only by `project_key`/`status`, not by `session_type`.

**Observability**: Each recovery increments a project-scoped Redis counter keyed `{project_key}:session-health:recoveries:{reason_kind}` where `reason_kind` is one of `worker_dead`, `no_progress`, or `tool_timeout`. `tool_timeout` covers the per-tool timeout sub-loop and is recorded by the shared `_apply_recovery_transition` helper. The counter write is wrapped in `try/except` — failure cannot block recovery.

**Diagnosing no-progress recoveries**:

- Log grep: `grep "worker alive but no progress signal" logs/worker.log` — each hit is one no-progress recovery and includes `turn_count`, `log_path`, and `claude_session_uuid` for the affected session.
- Expected rate ceiling: ≤ 1 no-progress recovery per project per hour under normal operation. Bursts of no-progress recoveries for sessions that should be healthy indicate the `AGENT_SESSION_HEALTH_MIN_RUNNING` guard is too short or the progress signal is too narrow.
- Redis counter: `redis-cli GET {project_key}:session-health:recoveries:no_progress` (note: reading via `redis-cli` is observability-only; never mutate Popoto-managed keys directly).

**Accepted race**: The recovery path does NOT protect progress fields under CAS — only `status`. In the tight window between reading `entry` and calling `transition_status("pending")`, a worker writing progress can have its in-flight work re-queued. This is rare and benign: the worker pops the re-queued session and runs from scratch. See the `test_progress_written_between_check_and_transition_is_lost_but_session_retries` unit test for the locked-in behavior.

### 9. Perplexity Provider Error Handling (`tools/web/providers/perplexity.py`)

`tools/web/providers/perplexity.py` handles `httpx.HTTPStatusError` explicitly before its generic catch. 401 errors log a clear warning message directing the operator to refresh credentials in `.env`. Other HTTP errors are also logged with their status code.

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

On `FloodWaitError`, the bridge writes a `data/flood-backoff` JSON file containing the expiry timestamp. On startup, before attempting to connect, the bridge checks this file and sleeps until the flood period clears, so launchd restarts do not compound the wait.

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

The bridge persists a `data/last_connected` ISO 8601 timestamp file. On startup, catchup reads this timestamp and uses it to compute the lookback window dynamically, capped at 24 hours to avoid scanning excessive history.

**Timestamp updates**:
- Written on successful Telegram connect
- Updated every 5 minutes via the heartbeat loop
- Written on graceful shutdown (SIGTERM/SIGINT)

**Fallback**: If the file is missing or invalid, the default 60-minute lookback is used. Redis dedup (`is_duplicate_message`) prevents double-processing even if the window overlaps with already-handled messages.

**Telethon duplicate dialog guard**: Telethon's `get_dialogs()` can return the same supergroup twice — once as a channel entity and once as its linked discussion group. Without a guard, catchup would scan the same group twice and enqueue the same messages twice, causing duplicate Telegram replies. The catchup scanner deduplicates by `dialog.id` (`seen_chat_ids: set[int]`) before scanning each group.

**Logger handler guard**: `telegram_bridge.py` may execute its module-level setup twice in some launch configurations (once as `__main__`, once as `bridge.telegram_bridge`). This would add a second `RotatingFileHandler` to the root logger, doubling every log line. A guard checks for an existing handler with the same log file path before adding a new one.

### 12a. Silent Telethon Update Gap Handling (`bridge/dedup.py`, `bridge/catchup.py`, `bridge/reconciler.py`, `bridge/silent_stream.py`)

Telethon can stop delivering `NewMessage` events for a specific chat with no error and no disconnect — the bridge believes it is connected, but the event handler stops firing for that chat. Four coordinated mechanisms, all best-effort (failures log a WARNING and fall back to prior behavior; they never crash the live handler, reconciler, or catchup), cover this gap:

1. **Per-chat last-processed cursor** (`models/last_processed.py` `LastProcessedRecord`, `bridge/dedup.py` `record_last_processed` / `get_last_processed`). A Redis-backed Popoto model (30-day TTL) tracks the latest message ID + timestamp the bridge actually *dispatched* for each chat. It is distinct from `DedupRecord` (a *set* of recent IDs for membership checks) — this is a monotonic *cursor*. Written by the live handler (via `bridge/dispatch.py::dispatch_telegram_session`), the reconciler, and catchup on every successful dispatch. The cursor advances monotonically: an older message ID is a no-op, so concurrent writes from the live handler and the reconciler cannot regress it.

2. **Smarter catchup cutoff** (`bridge/catchup.py`). For each chat, catchup computes `per_chat_cutoff = min(global_cutoff, last_processed_dt - 60s)`. It uses `min()` — never `max()` — so the scan looks back *at least* as far as the global `last_connected` cutoff, and *further* when the per-chat cursor is older. The 60-second safety margin guards against off-by-a-message edges. The 24-hour cap (Component 12) applies only to the `lookback_override` path and does **not** bound the cursor-extended reach — time reach is unbounded; recovery *depth* is bounded instead: catchup pages backwards to the per-chat cutoff via the shared `bridge/history_fetch.py::fetch_messages_back_to`, hard-capped at `CATCHUP_MAX_MESSAGES_PER_CHAT` with a `TRUNCATED` WARNING when the cap binds, exactly like the reconciler. If the cursor read fails or no cursor exists, catchup falls back to the global cutoff.

3. **Extended reconciler lookback** (`bridge/reconciler.py`). `RECONCILE_LOOKBACK_MINUTES` (30) is a *floor*: the per-chat cutoff extends back to the last-dispatched cursor when that cursor is older. The scan pages backwards to reach that cutoff, bounded by `RECONCILE_MAX_MESSAGES_PER_CHAT` and by `DedupRecord._MAX_IDS` retention, and logs a `TRUNCATED` WARNING when the bound binds. A quiet chat still costs one `get_messages()` call per scan — no increase in steady-state API call *rate*. See [Message Reconciler](message-reconciler.md#fetch-depth-is-bounded-by-dedup-retention-issue-2476).

4. **Silent-stream check** (`bridge/silent_stream.py` `check_silent_chat` / `check_silent_streams`, `SilentStreamState`). The silent-gap check **rides the reconciler's existing dialog pass** — it does *not* run its own loop. The reconciler calls `client.get_dialogs()` every 180s and iterates every monitored group; `reconcile_once` invokes `check_silent_chat` for each dialog it already fetched, threading a shared `SilentStreamState` (bridge start timestamp + per-chat warning timestamps) across passes. This adds **no** recurring `get_dialogs()` call beyond the reconciler's existing one. The check compares the per-chat `bridge:last_event:{chat_id}` Redis key (set on *every* incoming event, before dedup/routing) against the silence threshold and logs a single `[silent-stream] WARNING` when a `respond_to_unaddressed: true` chat has had no events for 15+ minutes while the bridge has been continuously connected and the chat had prior activity in the session. **Observability only** — it does not re-dispatch (the reconciler and catchup own recovery), and a failure in the check is caught so it never interrupts the reconciler's recovery scan. False-positive suppression: only `respond_to_unaddressed` chats are watched; a chat with no `last_event` baseline is skipped; no warning fires within the first 15 minutes after startup; each chat warns at most once per 30-minute window.

**Recovery latency**: a message sent 25 minutes before a restart is recovered within 30 minutes — either the extended reconciler lookback catches it during live connection, or the per-chat catchup cutoff catches it on the next restart.

**Observable in `logs/bridge.log`** via the `[catchup] Found missed message` / `[reconciler] Recovered` lines and the `[silent-stream]` WARNING lines.

These mechanical scanners address message **ingestion** gaps (a message that was never enqueued). For the complementary **response-failure** case — a message that *was* enqueued but whose session hung or was killed without replying — see [Agent-Judgment Catchup](agent-judgment-catchup.md), an LLM-driven recovery layer (`valor-catchup`) that reads the actual thread and decides which messages genuinely need a reply.

### 13. Update Polling (`com.valor.update`)

The `com.valor.update` launchd plist uses `StartInterval` of 1800 seconds (30 minutes) to poll for updates. Each invocation runs `scripts/remote-update.sh`, which:
1. Acquires a lock (`data/update.lock`) to prevent concurrent runs
2. Fast-forwards `main` directly in bash (`git fetch origin main` + `git merge --ff-only origin/main`, never a bare `git pull`), so the orchestrator and all update scripts are loaded fresh from disk
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

The bridge classifies connection failures into two modes:

1. **Auth expiry** — Telegram session token expired or revoked; requires human intervention (`python scripts/telegram_login.py`). The bridge cannot self-recover.
2. **Transient connectivity** — network blip, DC migration, short Telegram outage. Launchd restart + Telethon reconnect handles this automatically.

`bridge/hibernation.py` classifies errors and implements a hibernation state:

**Permanent auth errors → hibernation**:
`AuthKeyUnregisteredError`, `AuthKeyError`, `AuthKeyInvalidError`, `AuthKeyPermEmptyError`, `SessionExpiredError`, `SessionRevokedError`, `UnauthorizedError`

**Transient errors → retry loop**:
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

All background tasks created in `main()` are tracked in a module-level `_background_tasks` list. During `_graceful_shutdown()`, all tracked tasks are explicitly cancelled and awaited before disconnecting the Telegram client. A `sys.exit(1)` safety net after `run_until_disconnected()` guarantees process termination.

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

`scripts/valor-service.sh` (and `scripts/install_worker.sh`) inject all `.env` variables directly into the installed plist at install time using Python's `dotenv_values()` parser. The bridge and worker detect `VALOR_LAUNCHD=1` in their environment and skip `load_dotenv()` entirely — env vars are already present in the process environment.

```python
# bridge/telegram_bridge.py
if not os.environ.get("VALOR_LAUNCHD"):
    load_dotenv(env_path)   # only runs outside launchd
```

This is a one-time injection at install time; updating `.env` secrets requires re-running the install script (or `/update`) to re-bake the plist.

### 17. Worker Status Heartbeat Check (`scripts/valor-service.sh`)

`status_worker()` reads the `data/last_worker_connected` heartbeat file (written by `_write_worker_heartbeat()` on every health loop tick). If the heartbeat age exceeds 360 seconds (matching the dashboard threshold), the status is reported as `STALE` instead of `RUNNING`, with exit code 2. This distinguishes a healthy worker (exit 0), a stopped worker (exit 1), and a hung/zombie worker (exit 2).

### 18. Worker Watchdog (`monitoring/worker_watchdog.py`)

A worker process can appear alive (PID exists, launchd does not restart it) but have a frozen asyncio event loop — for example, when a reflection callable calls `subprocess.run()` without `await`, blocking the loop indefinitely.

`monitoring/worker_watchdog.py` runs as a separate launchd service (`com.valor.worker-watchdog`, `StartInterval: 300`) alongside the worker. It checks the `data/last_worker_connected` heartbeat file on every tick:

| Heartbeat age | Status | Action |
|--------------|--------|--------|
| < threshold | `ok` | Log debug, exit. Reset down-tick counter if present. |
| Missing (file absent) | `starting` | Skip — worker may be initializing |
| Worker PID absent | `down` | **Active recovery via 4-level escalation** — see below |
| ≥ threshold | `stale` | **Verified-kill escalation ladder W1→W5** — see below |

**Stale-heartbeat threshold:** `HEARTBEAT_THRESHOLD` defaults to `180` seconds (= 6× the 30-second heartbeat write interval) and is env-tunable. The ≥6× multiplier is the false-positive guard: the heartbeat is written by a **dedicated daemon thread** (`worker-heartbeat`, started in `worker/__main__.py`) that runs outside the asyncio event loop, so thread-pool exhaustion cannot starve heartbeat writes. A stale heartbeat therefore reliably means the worker process is genuinely wedged (not just loop-busy).

**Heartbeat thread isolation:** `_heartbeat_thread_main()` in `worker/__main__.py` runs as a `threading.Thread(name="worker-heartbeat", daemon=True)` — outside the asyncio event loop. It wakes every `WORKER_HEARTBEAT_INTERVAL` seconds (default 30, env-tunable) and calls `_write_worker_heartbeat()`. The thread is started at worker startup and is stopped via `_heartbeat_stop_event` on worker shutdown. The only way the heartbeat can go stale is if the worker process itself is hung.

**Verified-kill escalation ladder** (when heartbeat is stale):

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

W4 writes no separate `pty_close_required` side-channel key — a session-execution turn is a short-lived, self-reaping child; there is no long-lived PTY master fd for a U-state hang to be blocked on.

The W1-W5 verified-kill ladder is substrate-agnostic. A headless `claude -p` subprocess can wedge in uninterruptible sleep on a blocking syscall exactly as any process can. The bridge watchdog's escalation ladder + revert-commit (Component 3 above) supervises the bridge process (Telethon connectivity, hibernation) and is orthogonal to PTY.

**Post-restart dead-worker session sweep:** When the worker restarts after a hung-worker incident, `_sweep_dead_worker_sessions()` in `agent/session_health.py` runs as **Step 3a** in `worker/__main__.py`, **before** `_recover_interrupted_agent_sessions_startup` (Step 3b). The ordering is critical: Step 3b transitions all remaining `running` sessions → `pending` without checking PID liveness; if the sweep ran after, there would be no `running` sessions left to inspect. The sweep handles the dead-worker subset (dead fenced `exec_pid`) first, finalizing those sessions to `killed`; Step 3b then re-queues the remaining genuinely-interruptible sessions (alive PID or no PID yet). The sweep:

1. Enumerates all sessions with `status="running"`.
2. Skips sessions with no `exec_pid` (not yet assigned a subprocess).
3. Skips sessions started within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds (300 s) — the recency guard preventing the fresh worker's own new sessions from being swept.
4. Checks liveness for each remaining session via the create-time fence (`agent/pid_fence.py::fence_is_live(exec_pid, pid_create_time)`); a `False` verdict is treated as dead.
5. Calls `finalize_session(entry, "killed", reason="dead-worker-sweep: ...")` (CAS via `expected_status='running'` — a concurrent fresh-worker pickup wins and the session is skipped via `StatusConflictError`).
6. When any sessions are swept, triggers `bridge.agent_catchup` as a subprocess so unanswered human messages re-enqueue as fresh sessions — no silently dropped messages.

Returns the count of sessions swept. A non-zero result is logged at INFO.

**Active recovery escalation** (when worker process is missing):

A Redis counter (`worker:watchdog:down_ticks:{hostname}`) tracks consecutive missing-worker ticks using `POPOTO_REDIS_DB.incr` + `expire(3600)` (atomic by Redis semantics, no file-lock needed). Each watchdog tick is a fresh launchd invocation so the counter must survive outside the process — Redis is the natural fit. TTL of 1h auto-clears stale state (e.g. after a prolonged outage where the counter was never explicitly cleared).

| Level | Trigger | Action |
|-------|---------|--------|
| L1 | First down tick (count == 1) | Log `Worker missing — giving launchd one tick to restart` and exit. Give launchd a chance. |
| L2 | Second consecutive down tick (count >= 2) | `launchctl kickstart -k gui/<uid>/com.valor.worker`, then poll `pgrep` for up to 10s. On success, clear counter. |
| L2.5 | L2 returned rc=113 / `Could not find service` AND `~/Library/LaunchAgents/com.valor.worker.plist` exists | `launchctl bootstrap gui/<uid> <plist>` to re-register the service in the gui domain, then retry kickstart and verify. On success, clear counter. Heals the case where the service was registered via `launchctl load`, leaving it invisible to `gui/<uid>/` queries. Plist-existence gate ensures uninstalled hosts fall through cleanly. |
| L3 | L2/L2.5 verify failed | `launchctl enable gui/<uid>/com.valor.worker` (clears sticky-disable from `worker-disable`) + kickstart + verify. On success, clear counter. |
| L4 | L3 verify failed AND count >= 3 | Log CRITICAL with hostname + tick count. Reason string includes `bootstrap+kickstart+enable all failed` when L2.5 was attempted, otherwise `kickstart+enable both failed`. Write `worker:watchdog:critical:{hostname}` Redis key (TTL 1h, JSON payload `{hostname, tick_count, last_attempt_at, reason}`). Counter persists; subsequent ticks repeat L4 idempotently. |

L2.5 heals the case where `scripts/valor-service.sh::start_worker()` registered the worker via `launchctl load` in a domain outside `gui/<uid>/`; `start_worker()` registers via `bootout + bootstrap gui/<uid> <plist>` so the registration lands in the gui domain. L2.5 is defense-in-depth — if any code path regresses, the watchdog self-heals.

**Operator-disable short-circuit**: the watchdog detects sticky-disable via `launchctl print-disabled gui/<uid>` at the very top of `main()`. If `"com.valor.worker" => disabled` appears in the output, it logs `Worker disabled by operator (launchctl print-disabled) — skipping check`, clears the down-tick counter (so a future re-enable starts fresh), and returns without touching launchctl. This is the only authoritative source — `worker-disable` in `valor-service.sh` calls `launchctl disable` directly; no sidecar flag file exists. Operator check precedes the down-counter increment so a disabled worker never accumulates ticks.

**Single-handler logger**: the watchdog configures a named logger explicitly with `propagate = False` and exactly one rotating file handler. That configuration happens in `_configure_logger()`, called from the `__main__` guard only, so a bare import attaches no handler at all — the invariant is "exactly one *owned* handler after `_configure_logger()` runs", not "one handler always". Regression test: `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration::test_logger_no_duplicate_handlers`, which calls `_configure_logger()` and asserts exactly one handler tagged `_watchdog_owned`.

**Check status**:
```bash
./scripts/valor-service.sh worker-status   # surfaces watchdog recovery state inline
python monitoring/worker_watchdog.py --check   # standalone: print status, exit 0=ok, 1=stale/down
tail -f logs/worker_watchdog.log

# Inspect the critical signal (L4):
redis-cli GET worker:watchdog:critical:$(hostname)

# Reset escalation counter manually (e.g. after fixing the underlying cause):
redis-cli DEL "worker:watchdog:down_ticks:$(hostname)"
```

**`worker-status` watchdog surface**: `./scripts/valor-service.sh worker-status` reads the Redis down-tick counter (`worker:watchdog:down_ticks:{hostname}`) and critical-state key (`worker:watchdog:critical:{hostname}`) and prints a one-line summary alongside the process/heartbeat info. Best-effort — Redis unavailability is silently ignored so `worker-status` always completes.

**Installed by** `scripts/install_worker.sh` as `${SERVICE_LABEL_PREFIX}.worker-watchdog`.

### 19. Guarded Config Read (`bridge/routing.py`)

`bridge/routing.py::_guarded_json_load()` wraps `projects.json` parsing in a `try/except (json.JSONDecodeError, OSError, UnicodeDecodeError)`. On success it caches the parsed config to a last-known-good sidecar (`data/projects.last_known_good.json`), written atomically via temp-file + `os.replace` — the same idiom used by `data/flood-backoff` (Component 11) and `agent/session_health.py`. On a parse failure it logs an ERROR and serves the last-known-good sidecar instead of raising; if no sidecar exists yet, it falls back to empty defaults (`{"projects": {}, "defaults": {}}`). `_guarded_json_load()` never raises. Both `load_config()` and `telegram_bridge.py::_get_active_projects()` (including its import-time module-level call) route through this shared helper, so a transiently corrupt config does not crash-loop either the bridge or the worker's config reads.

**Files**:
- `data/projects.last_known_good.json` — last successfully-parsed config, refreshed on every successful read

**Tests**: `tests/unit/test_routing.py::TestGuardedConfigRead` (9 cases) — successful reads cache the sidecar atomically; a corrupt read falls back to the sidecar and logs; a corrupt read with no sidecar falls back to empty defaults and logs; malformed/binary input never raises; `load_config()` falls back correctly end-to-end; sidecar read/write helpers handle missing-file and write-failure cases without raising.

See [Config Architecture](config-architecture.md) for how `projects.json` fits into the broader config system.

### 20. Update Release Verification

Four coordinated pieces verify that the running processes actually execute the pulled code: a boot-SHA beacon each process writes at startup, a shared classifier that reads both beacons, a bridge restart block in the shell, and a report path that survives the bridge's own restart.

**Boot-SHA beacon** (`monitoring/boot_beacon.py::write_boot_beacon`): at startup the bridge writes `data/bridge_boot_sha` and the worker writes `data/worker_boot_sha`, each a two-line file containing the short git SHA (via `scripts/update/git.py::get_short_sha`, the same helper the classifier compares against) and an ISO 8601 timestamp. The write is best-effort: any failure logs a warning and never crashes startup. A missing or malformed beacon can only ever downgrade classification to `unknown`, never invert into a false failure.

**Relevant-range classifier** (`scripts/update/service.py::verify_running_release`): for each in-role process, `_classify_process()` reads its beacon, gets the process's absolute start time via `get_process_start_ts(pid)` (a `ps -o lstart` parser), and classifies:

| Classification | Condition |
|---|---|
| `matches` | beacon belongs to the current process image (`beacon_ts > process_start_ts`) AND `git log {boot_sha}..HEAD -- <relevant paths>` is empty |
| `stale` | beacon belongs to the current image AND that relevant-range log is non-empty |
| `unknown` | beacon missing/malformed, no PID, `process_start_ts` unavailable, an orphaned beacon (`beacon_ts <= process_start_ts`), or `boot_sha` unresolvable by git |

**Boot-window settle poll** (`scripts/update/service.py::verify_running_release_settled`): the classification table above is read at one instant, and a process that is *mid-boot* at that instant — exec'd, but not yet at its beacon write — presents exactly like a broken beacon (missing, or orphaned by the previous image). Before classifying, the verify re-polls any process whose `unknown` is mid-boot (running, start ts readable, exec'd less than `settings.timeouts.beacon_settle_timeout_s` ago, beacon absent or orphaned) every `settings.timeouts.beacon_settle_interval_s` until it resolves or the window elapses. Both are provisional and env-overridable (`TIMEOUTS__BEACON_SETTLE_TIMEOUT_S`, `TIMEOUTS__BEACON_SETTLE_INTERVAL_S`). This is the generalization of the worker's `--since` poll: it covers a restart by *any* actor — this run's own kickstart, the watchdog's recovery chain, a plain launchd relaunch — not just the ones able to set the planned-restart skip signal, which is why a bridge restarting outside the update's control no longer degrades the verdict to `unknown`. Terminal unknowns (no PID, unreadable start ts, a long-running process with no beacon, an unresolvable `boot_sha`) return immediately and never burn the window.

Staleness is positive-only and scoped to each process's own relevant path set (bridge: `bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`; worker: `worker/ agent/ mcp_servers/ models/ tools/ bridge/ reflections/ pyproject.toml`), the same sets the restart gates diff, so classifier and restart gate agree by construction. A raw `boot_sha == HEAD` comparison is never used: docs-only or plan-migration commits advance HEAD past a healthy, correctly-un-restarted process, and a literal-equality check would false-fail on the majority of this repo's commit stream. `unknown` never fails a run and never triggers a restart. Only a positive, confirmed staleness escalates.

**Bridge kickstart in `remote-update.sh`**: After the pull and the worker kickstart, the shell computes `NEED_BRIDGE_RESTART` from a `BEFORE_SHA..AFTER_SHA` diff of the bridge-relevant paths, gated on the bridge plist being installed on this machine (`[ -f "$BRIDGE_DST" ]`; a skills-only machine has no bridge plist and skips the block entirely). When true, it runs `launchctl kickstart -k {prefix}.bridge` as the **last** thing the script does. This is safe because the bridge holds no agent sessions (the worker is the sole session executor) and its Telethon `catch_up=True` scan backfills anything missed during the brief restart. It is the last act because the kickstart SIGKILLs the whole bridge launchd job, including `handle_update_command` and the `remote-update.sh` child it spawned, since they share the job's process group. Nothing in the shell runs after a successful kickstart. Both worker and bridge kickstart failures surface as a distinct `RESTART FAILED` line and a non-zero terminal exit (`RESTART_FAILED || VERIFY_FAILED`).

**Worker restart primitive selection and the EIO-recovery fallback.** The worker restart block picks its primitive from `launchctl list | grep -q "$WORKER_LABEL"`. When the label is listed it uses the race-free `launchctl kickstart -k` (with a `bootout` + `sleep 2` + `bootstrap` fallback). When the grep reports the label absent it treats it as a first install and `bootstrap`s. That grep can **false-negative** — the label is in fact still registered in the gui domain (e.g. a stale worker process still holding it) while `launchctl list` momentarily omits it — in which case the bare `bootstrap` fails with `Bootstrap failed: 5: Input/output error` (errno 5 = the service is already bootstrapped in the target domain). Because that EIO *proves* the service is loaded, the not-loaded branch recovers with `launchctl kickstart -k` — the same primitive the loaded branch prefers — and only declares `RESTART FAILED` when **both** the bootstrap and the kickstart fail. On success it sets `VERIFY_SINCE=$RESTART_TS` exactly like the loaded branch, so the terminal release verify still runs once against the restart moment. Recoverable bootstrap stderr is suppressed so a transient EIO does not leak the raw launchd error into the update summary; on the genuine both-fail path the captured launchd errno/message is appended to the `RESTART FAILED` line for diagnosability. Regression tests live in `tests/unit/test_remote_update_shell.py` (`test_worker_bootstrap_eio_recovers_via_kickstart`, `test_worker_bootstrap_and_kickstart_both_fail_reports_failure`).

Before the kickstart, the shell releases `data/update.lock` explicitly (`rmdir "$LOCK_DIR"`), because the `trap cleanup_lock EXIT` that normally releases it never fires on SIGKILL. Without the explicit release, every bridge-relevant update would orphan the lock for up to 600 seconds, and any retry or the next cron cycle in that window would hit the "already running" skip branch with no pull and no verify.

**Terminal verify runs every cycle**: `python -m scripts.update.verify_release` (`scripts/update/verify_release.py`) is the shell's terminal step on every invocation, including no-op cron cycles with no new commits. This re-classifies a starved or never-restarted process instead of only checking right after a restart. It is scoped to the worker only (`--skip-bridge`) when a bridge restart is queued this cycle, since the about-to-restart bridge is not escalated as stale. It takes a `--since <epoch>` restart moment and polls (bounded, 15 attempts x 2 seconds) for the worker beacon to freshen past it before classifying, because a `kickstart -k` returns before the freshly-spawned process has written its own beacon, so an immediate read would otherwise see the pre-restart beacon and misclassify `unknown`. Exit code 1 on any positive staleness, 0 otherwise (`unknown` prints a warning but does not fail the run).

**Report path splits on whether the bridge restarts this cycle** (the survivable-channel design: a bridge kickstart kills the process that ran `/update`, so it cannot always be the reporter):

- **Worker-only or no-op update (no bridge restart)**: `handle_update_command` (`bridge/update.py`) survives. It re-verifies via `verify_running_release()` after the shell returns, gates `✅` on `returncode == 0 AND` no in-role `stale`, and appends per-process reload state (e.g. `(bridge current, worker restarted)`). A stale process reports `❌ update FAILED @ {sha}: {process} running {short} but HEAD is {short}` and still spawns the fix session. All stdout lines are scanned for `warning`/`ERROR`.
- **Bridge-relevant update (bridge restart triggered)**: `handle_update_command` will be SIGKILLed, so before the kickstart the shell stages the originating chat id, reply-to message id, pulled HEAD short-SHA, and worker reload state to `data/update-pending-report` (only when a Telegram chat context is present; the pure 30-minute cron cycle has none, so nothing is staged). The **fresh bridge**, at startup right after writing its own boot-SHA beacon, calls `run_boot_release_check()` (`bridge/update.py`), which unconditionally verifies its own release, then, if the pending report exists, reuses that check to compose the `✅`/FAILED reply, sends it to the staged chat, and deletes the file.
- On a bridge-plist machine, `handle_update_command` also sends a best-effort interim notice before invoking the shell, so the human is not left staring at a bare 👀 reaction for the multi-minute window between the bridge's self-kill and the fresh bridge's boot flush. A send failure here never blocks the update.

**`--full` verify** (`scripts/update/run.py::run_release_verify`): the synchronous `/update --full` path calls `verify_running_release()` as the terminal step of the `do_service_restart=True` branch, after `install_service`'s restart. Any in-role `stale` sets `result.success = False` (non-zero exit) and names both short-SHAs; `unknown` only warns. A clean pass that finds the bridge positively `matches` clears any earlier failure sentinel (below).

**Worker self-heal before alerting**: the full path's Step 5 worker install (`service.install_worker`) is content-idempotent — it returns early without restarting when the plist is unchanged, which is the case for any code-only pull. So a manual `/update` run right after merging worker-relevant code does NOT restart the worker (unlike the cron path, `remote-update.sh`, which kickstarts on a worker-relevant diff *before* verifying). `run_release_verify` self-heals a `stale` worker in place before alerting: drain → `service.kickstart_worker()` (`launchctl kickstart -k`) → bounded poll (`WORKER_SELF_HEAL_POLL_ATTEMPTS` × `WORKER_SELF_HEAL_POLL_INTERVAL_S`, default 15 × 2s, env-overridable) for a worker beacon fresher than the restart moment. Outcomes: `healed` re-verifies (the worker is on new code — no alert); a busy-drain `deferred` warns only and drops the worker from alert consideration (never kill an in-flight PM turn — the 30-min cron restarts it next tick); `failed` (restart ran but no fresh beacon) falls through to the hard-fail + Sentry path, a genuine "worker won't come up on new code" signal. The bridge is never self-restarted here — a bridge kickstart would SIGKILL the `/update` process itself.

**Out-of-band signals for a bridge that never comes back**: the report path above depends on the fresh bridge coming up. If it crash-loops or launchd fails to relaunch it, there is no live channel to report on. Two backstops, both read by `monitoring/bridge_watchdog.py::check_update_release_signals()` on its normal 60-second cycle:

- A fresh bridge that boots but self-classifies its own beacon `stale` writes `data/update-release-failed` (SHA lag + timestamp) via the unconditional self-check in `run_boot_release_check()`. This runs at every bridge boot regardless of whether a pending report exists, so the pure-cron trigger path (which stages nothing) still gets the backstop. A subsequent healthy boot (`matches`) clears the sentinel.
- A `data/update-pending-report` left undrained past `UPDATE_REPORT_TTL_SECONDS` (`STARTUP_GRACE_SECONDS + 60`, i.e. the watchdog's 5-minute startup grace plus one 60-second watchdog cycle, defined once in `scripts/update/service.py` and re-imported by the watchdog), measured against the report's own staged timestamp, signals that the fresh bridge never came up to flush it.

Both checks are logged at `logger.critical("[update-release] ...")` on every watchdog tick while the condition holds.

**Watchdog suppression for the planned restart**: the bridge kickstart is a *deliberate* SIGKILL of the bridge process, and without a suppression the independent 60-second watchdog would log a crash and could itself call `restart_bridge()` mid-window. `remote-update.sh` writes `data/update-restart-in-progress` (a timestamp) immediately before the kickstart. `run_health_check()` checks the marker's age against `UPDATE_RESTART_MARKER_TTL_SECONDS` (the same `STARTUP_GRACE_SECONDS + 60` formula as the report TTL, so the suppression window can never expire before the boot window it protects) and early-returns healthy while the marker is fresh, before `check_bridge_health()` runs, so neither the crash log nor the recovery-level bump fires. The fresh bridge's boot self-check clears the marker; an aged-out marker resumes normal health checking.

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

`_trigger_restart()` in `agent/agent_session_queue.py` and the `_check_restart_flag()` log line SIGTERM the **worker** process (launchd respawns the worker, not the bridge). The worker's deferred `data/restart-requested` flag is independent of the bridge kickstart described above; this feature does not add bridge consumption of that flag.

### 21. Hardened `launchctl bootstrap` Call Sites (`scripts/lib/launchctl.sh`)

`scripts/lib/launchctl.sh` exports one shared function, `launchctl_bootstrap_fail_soft <domain> <plist> <label>`, implementing bootstrap-then-`kickstart -k` recovery: `launchctl bootstrap` first, and only on bootstrap failure a `launchctl kickstart -k` fallback against the same label. The helper deliberately does NOT bootout the label itself — an unconditional internal bootout would kill and recreate an already-loaded, healthy service on every call. Any preceding bootout is owned by the call site: `bootstrap_plist_idempotent` and `worker-start` in `scripts/valor-service.sh` each boot out before calling the helper, as does `remote-update.sh` before its own bootstrap. `kickstart -k` is the correct recovery here because an errno-5 bootstrap failure specifically means the label is already registered in the domain. The function returns 0 as soon as the service ends up loaded (first-try bootstrap, or kickstart recovery) and returns 1 with a distinct, greppable `WARNING: launchctl bootstrap+kickstart failed for <label>` to stderr only on a genuine double-failure, so a truly dead service is never silently masked.

`scripts/valor-service.sh` (three call sites: `bootstrap_plist_idempotent`, the bridge install, and `worker-start`) and five `install_*.sh` helpers (`install_worker.sh`: main and watchdog, plus `install_reflection_worker.sh`, `install_nightly_tests.sh`, `install_email_bridge.sh`, `install_sdlc_reflection.sh`) source the shared helper and call it instead of a bare `launchctl bootstrap`. In `valor-service.sh`, a genuine double-failure warns and lets the install continue rather than hard-aborting under `set -e`. The happy path (bootstrap succeeds on the first try) is identical: the helper returns 0 with no `kickstart` call.

**Files**:
- `scripts/lib/launchctl.sh`: shared `launchctl_bootstrap_fail_soft` helper

**Tests**: `tests/unit/test_valor_service_bootstrap.py` and `tests/unit/test_install_scripts_bootstrap.py` are stubbed-`launchctl` harnesses asserting the errno-5 recovery path, the genuine double-failure WARNING, and the unchanged happy path.

## Two-tier no-progress detector

The periodic `_agent_session_health_check` (every 5 minutes) decides whether a
long-running session is making progress. To minimize **false-negatives**
(killing a working session) while still reaping genuinely wedged sessions, the
detector uses two independent tiers.

### Tier 1 — per-turn signals (sub-check A) + bounded startup-window heartbeat (sub-check B)

`_has_progress()` evaluates two sub-checks. Either passing → progress.

**Sub-check A — per-turn SDK progress.**

| Field | Writer | When |
|-------|--------|------|
| `last_tool_use_at` | `agent/hooks/liveness_writers.py::record_tool_boundary` (PreToolUse / PostToolUse) | Per tool call boundary |
| `last_turn_at` | `agent/sdk_client.py` on `result` event | End of each turn |

Either field fresher than `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, 30 min)
counts as progress. `last_sdk_heartbeat_at` (the BackgroundTask watchdog
tick) is intentionally NOT a progress signal — it proves only that the
subprocess exists.

**Sub-check B — startup-window executor-alive fallback.**

| Field | Writer | When |
|-------|--------|------|
| `last_heartbeat_at` | Queue-layer `_heartbeat_loop` inside `_execute_agent_session` | Every `HEARTBEAT_WRITE_INTERVAL` (60s) |

When `sdk_ever_output` is False (neither per-turn field has ever been set),
`last_heartbeat_at` fresh within `HEARTBEAT_FRESHNESS_WINDOW` (90s) counts
as progress, **subject to the D0 never-started gate**. The
gate (`_never_started_past_grace`, called with the same trusted `now_utc`
clock sub-check B uses) is the authoritative never-started
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

Once the D0 gate and this leg share one clock, every gate survivor
unconditionally satisfies the 300s leg above.

The D0 gate bounds the fresh-heartbeat fast-path, so cwd-disappearance and
similar wedges cannot hold Tier 1 open indefinitely. Sessions that have produced
any SDK output (`sdk_ever_output=True`) are not subject to sub-check B at all —
sub-check A is authoritative for them.

**Own-progress fields and child-activity check:** `turn_count > 0`, non-empty `log_path`, and non-empty
`claude_session_uuid` are evaluated only when `sdk_ever_output` is False
AND `last_heartbeat_at` is within the last `NO_OUTPUT_BUDGET_SECONDS`
(1800s). These fields are sticky once set, but are **gated on
heartbeat freshness** — a stale or absent heartbeat means the executor
loop has likely exited, so own-progress fields must not keep the session
alive indefinitely. The child-activity check
(a PM session with any non-terminal child is not stuck) is unconditional
and evaluated regardless of heartbeat freshness.

**Constants:**

| Constant | Default | Env var | Purpose |
|----------|---------|---------|---------|
| `SDK_PROGRESS_FRESHNESS_WINDOW` | 1800s (30 min) | `SDK_PROGRESS_FRESHNESS_WINDOW_SECS` | Sub-check A freshness window for `last_tool_use_at` / `last_turn_at` |
| `MAX_NO_OUTPUT_REPRIEVES` | 20 | — (derived) | Tier-2 reprieve cap for `sdk_ever_output=False` sessions; also feeds `NO_OUTPUT_BUDGET_SECONDS` |
| `NO_OUTPUT_BUDGET_SECONDS` | 1800s (30 min) | — (derived) | `MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW`. Used by the own-progress heartbeat gate and the Tier-2 reprieve cap |
| `STARTUP_GRACE_SECONDS` | 300s (= `AGENT_SESSION_HEALTH_MIN_RUNNING`) | `STARTUP_GRACE_SECONDS` | Below this `running_seconds`, sub-check B's fresh-heartbeat fast-path is unconditional for D0-gate survivors |
| `COMPACT_REPRIEVE_WINDOW_SEC` | 600s | `COMPACT_REPRIEVE_WINDOW_SECS` | Tier 2 `compacting` reprieve window — `last_compaction_ts` within this window reprieves the kill |

**Operator alert:** After 3 Tier 2 reprieves, the reprieve log message is
escalated from `INFO` to `WARNING`, signaling that the session may be in an
indefinite alive-but-silent reprieve loop.

### Tier 2 — activity-positive reprieve gates

When Tier 1 flags a session, the health check calls `_tier2_reprieve_signal()`
which evaluates three gates — one compaction-aware and two OS-level liveness
checks via `psutil`.

| Gate | Check | Return |
|------|-------|--------|
| compacting | `AgentSession.last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (600s). Evaluated first so post-compaction idle periods are never misread as hangs. Companion writer: `agent/hooks/pre_compact.py::pre_compact_hook`. | `"compacting"` |
| children   | `psutil.Process(pid).children()` non-empty (tool execution active) | `"children"` (preferred over `"alive"`) |
| alive      | `psutil.Process(pid).status()` not in `{zombie, dead, stopped}` | `"alive"` |

Any **one** passing gate reprieves the kill. The reprieve signal is logged and
`reprieve_count` on the AgentSession is incremented for post-hoc analysis.
`recovery_attempts` is NOT incremented on reprieve.

**Scope:** Tier 2 reprieve applies **only** to `no_progress` recoveries.
`worker_dead` recoveries skip Tier 2 entirely and proceed directly to the
kill path — there is no live worker to deliver any future progress signal,
so an "active children" reprieve would only prolong a hung session.

Only `no_progress` and `worker_dead` reason kinds remain.

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
  fresh heartbeats AND no own-progress AND no live children).
* `tier2_reprieve_total:{compacting|alive|children}` — reprieve by signal.
* `kill_total` — actual kills (after Tier 2 failed and kill-switch off).
* `recoveries:{worker_dead|no_progress|tool_timeout}` — recoveries by reason
  kind. `tool_timeout` covers the per-tool timeout sub-loop and is recorded by
  the shared `_apply_recovery_transition` helper.
* `recoveries:zombie_uuid_no_output` — subset of `recoveries:no_progress`:
  emitted when the recovered session matches the zombie profile
  (`claude_session_uuid` set but `sdk_ever_output=False`, heartbeat stale
  past `NO_OUTPUT_BUDGET_SECONDS`). Distinguishes stale-zombie recoveries
  from normal startup-window recoveries.
* `tool_timeouts:{internal|mcp|default}` — per-tier hits from the per-tool
  timeout sub-loop (parallel 30s loop). Internal tier: lightweight
  built-ins (`Read`/`Glob`/`Grep`/`Edit`/`Write`/`NotebookEdit`/`ToolSearch`,
  30s budget). MCP tier: any `mcp__*` tool (120s budget). Default tier:
  everything else, including `Bash`/`Task`/`Skill` (300s budget, flat
  age-only kill). Each tier budget is env-tunable via
  `TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`,
  `TOOL_TIMEOUT_DEFAULT_SEC`. Sub-loop is gated by `TOOL_TIMEOUT_TIERS_DISABLED`
  (parity with `DISABLE_PROGRESS_KILL`). The flat age-only kill applies
  uniformly to every session.

**Distinguishing kill causes in dashboards:**
- `tier1_flagged_total` high → heartbeat writers are dying (clock/event-loop issue) OR sessions are genuinely stuck
- `tier2_reprieve_total:alive` high → processes alive but silent; monitor `reprieve_count` for operator warnings

### Per-session fields

| Field | Type | Purpose |
|-------|------|---------|
| `last_heartbeat_at` | DatetimeField | Queue-layer heartbeat |
| `last_sdk_heartbeat_at` | DatetimeField | Messenger watchdog heartbeat |
| `last_stdout_at` | DatetimeField | Last SDK stdout event — informational only |
| `started_at` | DatetimeField | Session start time |
| `recovery_attempts` | IntField | Kills only; finalizes at `MAX_RECOVERY_ATTEMPTS` |
| `reprieve_count` | IntField | Tier 2 saves — diagnostic only; triggers WARNING log after 3 |
| `current_tool_name` | Field (str, null) | Name of the tool currently in flight, or None between tools |
| `last_tool_use_at` | DatetimeField | Bumped at every tool boundary by pre/post tool-use hooks |
| `last_turn_at` | DatetimeField | Bumped on every SDK `result` event |
| `recent_thinking_excerpt` | Field (str, null) | Last 280 chars of extended-thinking content |

Every model field round-trips through the delete-and-recreate paths (retry,
orphan-fix, continuation fallback): both payloads derive their field set from
`AgentSession._meta` at runtime, so a field added to the model is covered
immediately. See [Two contracts, two field payloads](agent-session-queue.md#two-contracts-two-field-payloads-2563).

### Messenger callbacks (ORM-free)

`BossMessenger` defines three optional callback slots (`on_sdk_started`,
`on_heartbeat_tick`, `on_stdout_event`) with `notify_*` wrappers that catch
callback exceptions and log at WARNING, but only `on_sdk_started` and
`on_heartbeat_tick` are wired at its construction site. `last_stdout_at` is
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

The web UI leg is verified, not assumed: `restart_webui` kills *all*
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

## Hierarchy Health Check — Terminal Parent Skip

The periodic `_agent_session_hierarchy_health_check()` in `agent/session_health.py` walks parents matched by `AgentSession.query.filter(status="waiting_for_children")` and finalizes any whose children are all terminal (delivering a Telegram summary on the success path via `schedule_pipeline_completion`).

**Stale-index defense**: index entries can lag behind the authoritative hash status. If a parent was killed but its `waiting_for_children` index entry was not srem'd at kill time, the parent will still appear in the candidate list. Without a guard, the check would draft and ship a final summary to the operator's chat for an already-killed session.

The check re-reads the parent's hash status (`get_authoritative_session(session_id)`) **at the top of every loop iteration**. If the hash status is in `TERMINAL_STATUSES`, the loop logs at INFO and `continue`s. This is defense-in-depth: the underlying index corruption is a separate Popoto-layer concern, but the operational symptom (Telegram-spam after kill) is masked by the re-read.

```text
[session-health] Skipping terminal parent <agent_session_id> (status=killed) — index entry stale
```

If you see this line repeatedly for the same parent, the underlying index entry is stuck and warrants investigation.

The runner-entry guard in `agent/session_completion.py` (`_deliver_pipeline_completion` and `schedule_pipeline_completion`) is the second layer of the same defense — even if a stale-index call slips past the health-check guard, the runner short-circuits on the same terminal-status check before drafting or queuing any message. See [Session Lifecycle: Kill-is-Terminal Invariant](session-lifecycle.md#kill-is-terminal-invariant) for the full layered-defense write-up.

## Files

| File | Purpose |
|------|---------|
| `monitoring/crash_tracker.py` | Crash event logging and pattern detection |
| `monitoring/bridge_watchdog.py` | External health monitor (bridge process); includes `assess_update_flow()` and wedged-update-loop recovery |
| `bridge/liveness.py` | Liveness signal writers/readers: `record_update_received()`, `get_last_update_received()`, `record_probe_ok()`, `get_last_probe_ok()`, `record_missed_recovery()`, `get_last_missed_recovery()` |
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
