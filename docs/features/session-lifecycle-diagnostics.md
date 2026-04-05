# Session Lifecycle Diagnostics

Structured logging at every AgentSession state transition, with stall detection for sessions stuck in transitional states.

## Overview

Every AgentSession status change now emits a structured `LIFECYCLE` log entry to `bridge.log`, including the old and new status, duration in previous state, session ID, and context. This makes it possible to trace the full lifecycle of any session from the logs alone.

A stall detector runs alongside the existing session watchdog, flagging sessions that have been in a transitional state (pending, running, active) longer than expected.

## How It Works

### Lifecycle Logging

The `AgentSession.log_lifecycle_transition()` method is called at every status change point:

| Caller | Transition | Context |
|--------|-----------|---------|
| `models/session_lifecycle.finalize_session()` | →completed/failed/killed/abandoned/cancelled | All terminal transitions |
| `models/session_lifecycle.transition_status()` | →pending/running/active/dormant/waiting_for_children/superseded | All non-terminal transitions |

All lifecycle logging is now centralized in `models/session_lifecycle.py`. The `finalize_session()` and `transition_status()` functions call `session.log_lifecycle_transition()` internally, so callers no longer need to call it directly. See [Session Lifecycle](session-lifecycle.md) for the full module documentation.

Each call:
1. Emits a structured INFO log: `LIFECYCLE session=X transition=old→new session_id=Y project=Z duration_in_prev_state=Ns context="..."`
2. Appends a `[lifecycle]` entry to the session's history list (duration is derived from history timestamps)

### Log Format

```
LIFECYCLE session=tg_valor_-5051653062_6165 transition=pending→running id=abc123 project=valor duration_in_prev_state=2.3s context="worker picked up session"
```

Filter all lifecycle events: `grep LIFECYCLE logs/bridge.log`

### Stall Detection

Added to the existing session watchdog loop. Every 5 minutes, `check_stalled_sessions()` queries all sessions in transitional states and checks time-in-state against thresholds:

| Status | Threshold | Rationale |
|--------|-----------|-----------|
| pending | 300s (5 min) | Jobs should be picked up quickly |
| running | 2700s (45 min) | Matches agent session health monitor timeout |
| active | 600s (10 min) | No `updated_at` update = likely stalled |

For active sessions, `updated_at` is checked first — if recent activity exists within the threshold, the session is not considered stalled.

When a stall is detected:
- A `LIFECYCLE_STALL` warning is logged with session ID, status, duration, and last history entry
- The stalled session info is returned for potential alerting
- **Pending stalls** (#342, #402): `_recover_stalled_pending()` kills the stuck worker via `_kill_stalled_worker()`, applies exponential backoff, and re-enqueues via `_enqueue_stall_retry()`. After `STALL_MAX_RETRIES` exhausted, the session is abandoned with a Telegram notification. See [stall-retry.md](stall-retry.md) for full details.

### Stale Save Guard (#342)

The `_execute_agent_session()` epilogue in `agent/agent_session_queue.py` previously saved a stale in-memory `agent_session` reference when `defer_reaction=True` (auto-continue). Since `_enqueue_continuation()` already deleted and recreated the session, this save resurrected a ghost record in Redis, causing the pending continuation to become invisible to the worker. The fix skips the save entirely and logs a debug message explaining why.

### CLI Status Report

`monitoring/session_status.py` provides a quick view of all active sessions:

```bash
python monitoring/session_status.py           # Active sessions
python monitoring/session_status.py --all     # Include completed
python monitoring/session_status.py --stalled # Only stalled sessions
```

Output format:
```
SESSION STATUS REPORT (3 active)
================================================================================
  tg_valor_123_456                          running        12m  project=valor
  tg_dm_789_012                             pending         3m  project=dm
  tg_valor_345_678                          active         45m  project=valor ⚠️  STALLED
    └─ last: [lifecycle] active→active: transcript started
```

## Configuration

Constants in `monitoring/session_watchdog.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `STALL_THRESHOLD_PENDING` | 300 (5 min) | Max time in pending before stall alert |
| `STALL_THRESHOLD_RUNNING` | 2700 (45 min) | Max time in running before stall alert |
| `STALL_THRESHOLD_ACTIVE` | 600 (10 min) | Max time with no activity before stall alert |

## Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | `log_lifecycle_transition()` method (duration derived from history entries) |
| `bridge/session_transcript.py` | Lifecycle calls in start/complete |
| `agent/agent_session_queue.py` | Lifecycle calls in push/pop |
| `monitoring/session_watchdog.py` | Stall detection (`check_stalled_sessions()`) |
| `monitoring/session_status.py` | CLI session status report |
| `tests/test_lifecycle_transition.py` | Integration tests for lifecycle logging |
| `tests/unit/test_stall_detection.py` | Unit tests for stall detection |
| `tests/unit/test_pending_recovery.py` | Tests for stale save guard, pending recovery, and kill+retry (#342) |
| `tests/unit/test_session_status.py` | Unit tests for CLI report |

## Error Summary Enforcement (#434)

When sessions fail, the `summary` field on `AgentSession` is now populated with error context from the exception that caused the failure. This ensures the reflections system (`scripts/reflections.py`) receives actionable data instead of empty strings.

**Failure paths that capture error summaries:**

| Caller | Summary format | Example |
|--------|---------------|---------|
| `agent/sdk_client.py` crash guard | `{ExceptionType}: {message}` | `ConnectionError: Redis refused` |
| `monitoring/session_watchdog.py` ModelException handler | `Watchdog: {ExceptionType}: {message}` | `Watchdog: ModelException: unique constraint` |

**Reflections guard:** `scripts/reflections.py` skips failed sessions with empty summaries (logging a warning), preventing vague "empty error summary" issues from being auto-filed.

Summaries are truncated to 500 characters at capture time. The `AgentSession.summary` field supports up to 50,000 characters, but concise one-line summaries are preferred since full tracebacks are available in `bridge.log`.

## Crash-Path Diagnostic Snapshot (#626)

When a session terminates (whether by failure, cancellation, or normal completion), the worker `finally` block saves a diagnostic snapshot **before** calling `_complete_agent_session()`. A nudge guard then re-reads the session from Redis: if the session status is `"pending"` (nudge enqueued) or the session no longer exists (nudge fallback recreated it), completion is skipped to avoid overwriting the nudge. Otherwise, `_complete_agent_session()` proceeds normally. See [Session Lifecycle](session-lifecycle.md) for the full zombie loop prevention design.

### What Gets Captured

The `save_session_snapshot()` call records:

| Field | Source | Purpose |
|-------|--------|---------|
| `event` | `"crash"` or `"complete"` | Distinguishes failure from clean exit |
| `session_id` | `session.session_id` | Links to bridge session context |
| `agent_session_id` | `session.agent_session_id` | Links to queue-level record |
| `project_key` | `session.project_key` | Scopes to project |
| `tool_count` | `get_activity()` | Number of tools invoked during session |
| `trigger` | `"finally_block"` | Identifies snapshot origin |

### Tool Count Fallback

The `get_activity()` function in `agent/hooks/session_registry.py` retrieves tool counts for heartbeat and snapshot reporting. It uses a reverse lookup from `_uuid_to_bridge_id` to find the session's activity record. When this reverse lookup fails (e.g., the pending-to-UUID promotion never happened because the session crashed early), the function now falls back to `health_check._tool_counts`, which is the authoritative counter incremented on every tool call.

```
# Normal path: reverse UUID lookup finds activity in _sessions dict
get_activity("bridge_session_123") -> {"tool_count": 42, "last_tools": ["Read", "Bash"]}

# Fallback path: UUID lookup fails, reads from health_check counter
get_activity("bridge_session_123") -> {"tool_count": 42, "last_tools": []}
```

The fallback logs a WARNING so the underlying registration gap can be investigated:
```
[session_registry] get_activity reverse lookup failed for bridge_session_123, falling back to health_check count=42
```

### Task Await (Exception Propagation)

The `_execute_agent_session()` function previously used a `while task.is_running` / `sleep(2)` polling loop to wait for the background task. Exceptions that escaped `BackgroundTask._run_work` were silently swallowed because the polling loop only checked `is_running`, not the task's exception state.

The fix replaces the polling loop with `await task._task`, which directly awaits the asyncio future. Any exception that escapes `_run_work` propagates immediately to the caller, where it is caught and stored in `task._error` for downstream handling.

### Troubleshooting

**Heartbeat shows stale tool count (0 tools when session clearly ran tools)**

This occurs when the session registry's reverse UUID lookup fails. The `get_activity()` fallback to `health_check._tool_counts` was added to address this. If you see the WARNING log `get_activity reverse lookup failed`, the fallback is working. The root cause is that `_uuid_to_bridge_id` was never populated -- typically because the session crashed before the pending-to-UUID promotion in `_pop_agent_session()` completed.

**Session dies with no trace in logs or snapshots**

Before #626, if a session crashed after `_complete_agent_session()` ran but before any snapshot was saved, the session vanished. The crash snapshot in the `finally` block now runs before completion, ensuring at least one diagnostic record exists for every terminated session.

### Files

| File | Change |
|------|--------|
| `agent/agent_session_queue.py` | Crash snapshot in finally, task await, lifecycle logging |
| `agent/hooks/session_registry.py` | Tool count fallback to `health_check._tool_counts` |
| `tests/unit/test_crash_snapshot.py` | Tests for snapshot saving on all termination paths |
| `tests/unit/test_session_registry_fallback.py` | Tests for tool count fallback behavior |

## Related

- [Session Watchdog](session-watchdog.md) — Existing session health monitoring (silence, loops, errors, duration)
- [Agent Session Health Monitor](agent-session-health-monitor.md) — Queue-level stuck session recovery
- [Bridge Self-Healing](bridge-self-healing.md) — Process-level bridge health
- [AgentSession Model](agent-session-model.md) — Unified session lifecycle model
- [Agent Session Queue Reliability](agent-session-queue.md) — Queue-level reliability fixes
- [Session Lifecycle](session-lifecycle.md) — Session state machine, zombie loop prevention
- Issue #216 — Original tracking issue
- Issue #626 — Silent session death fixes
