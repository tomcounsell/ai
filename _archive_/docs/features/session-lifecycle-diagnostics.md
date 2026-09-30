# Session Lifecycle Diagnostics

Structured logging at every AgentSession state transition, with stall detection for sessions stuck in transitional states.

## Overview

Every AgentSession status change emits a structured `LIFECYCLE` log entry to `bridge.log`, including the old and new status, duration in previous state, session ID, and context. This makes it possible to trace the full lifecycle of any session from the logs alone.

A stall detector runs alongside the existing session watchdog, flagging sessions that have been in a transitional state (pending, running, active) longer than expected.

## How It Works

### Lifecycle Logging

The `AgentSession.log_lifecycle_transition()` method is called at every status change point:

| Caller | Transition | Context |
|--------|-----------|---------|
| `models/session_lifecycle.finalize_session()` | →completed/failed/killed/abandoned/cancelled | All terminal transitions |
| `models/session_lifecycle.transition_status()` | →pending/running/active/dormant/waiting_for_children/superseded | All non-terminal transitions |

All lifecycle logging is centralized in `models/session_lifecycle.py`. The `finalize_session()` and `transition_status()` functions call `session.log_lifecycle_transition()` internally, so callers do not call it directly. See [Session Lifecycle](session-lifecycle.md) for the full module documentation.

Each call:
1. Emits a structured INFO log: `LIFECYCLE session=X transition=old→new session_id=Y project=Z duration_in_prev_state=Ns context="..."`
2. Appends a `[lifecycle]` entry to the session's history list (duration is derived from history timestamps)

### Log Format

```
LIFECYCLE session=tg_valor_-5051653062_6165 transition=pending→running id=abc123 project=valor duration_in_prev_state=2.3s context="worker picked up session"
```

Filter all lifecycle events: `grep LIFECYCLE logs/bridge.log`

### Stall Detection

`check_stalled_sessions()` runs in the session watchdog loop. Every 5 minutes it queries all sessions in transitional states and checks time-in-state against thresholds:

| Status | Threshold | Rationale |
|--------|-----------|-----------|
| pending | 300s (5 min) | Jobs should be picked up quickly |
| running | 2700s (45 min) | Matches agent session health monitor timeout |
| active | 600s (10 min) | No `updated_at` update = likely stalled |

For active sessions, `updated_at` is checked first — if recent activity exists within the threshold, the session is not considered stalled.

When a stall is detected:
- A `LIFECYCLE_STALL` warning is logged with session ID, status, duration, and last history entry
- The stalled session info is returned for potential alerting
- **User-visible liveness counter**: for sessions with an originating Telegram message, `_publish_liveness_ticks()` advances a wall-clock tick counter on that message via `telegram:outbox:{session_id}`, and forces a progress message at the ceiling. It is a duration signal, not a stall signal — it asserts only that the watchdog has eyes on the session. See [Session Liveness Tick Counter](session-liveness-tick-counter.md).
- **Pending stalls**: `_recover_stalled_pending()` kills the stuck worker via `_kill_stalled_worker()`, applies exponential backoff, and re-enqueues via `_enqueue_stall_retry()`. After `STALL_MAX_RETRIES` exhausted, the session is abandoned with a Telegram notification. See [stall-retry.md](stall-retry.md) for full details.

### Stale Save Guard

The `_execute_agent_session()` epilogue in `agent/agent_session_queue.py` skips saving the in-memory `agent_session` reference when `defer_reaction=True` (auto-continue). `_enqueue_continuation()` deletes and recreates the session, so saving would resurrect a ghost record in Redis and make the pending continuation invisible to the worker. The guard skips the save and logs a debug message explaining why.

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
| `tests/unit/test_session_lifecycle_consolidation.py` | Transition coverage for `finalize_session()` / `transition_status()` (terminal, non-terminal, idempotency) |
| `tests/unit/test_stall_detection.py` | Unit tests for stall detection |
| `tests/unit/test_recovery_respawn_safety.py` | Terminal-status safety across every recovery mechanism |
| `tests/unit/test_session_status.py` | Unit tests for CLI report |

## Error Summary Enforcement

When sessions fail, the `summary` field on `AgentSession` is populated with error context from the exception that caused the failure. This ensures the reflections system (`reflections/session_intelligence.py`) receives actionable data instead of empty strings.

**Failure paths that capture error summaries:**

| Caller | Summary format | Example |
|--------|---------------|---------|
| `agent/sdk_client.py` crash guard | `{ExceptionType}: {message}` | `ConnectionError: Redis refused` |
| `monitoring/session_watchdog.py` ModelException handler | `Watchdog: {ExceptionType}: {message}` | `Watchdog: ModelException: unique constraint` |

**Reflections guard:** `reflections/session_intelligence.py` skips failed sessions with empty summaries (logging a warning), preventing vague "empty error summary" issues from being auto-filed.

Summaries are truncated to 500 characters at capture time. The `AgentSession.summary` field supports up to 50,000 characters, but concise one-line summaries are preferred since full tracebacks are available in `bridge.log`.

## Crash-Path Diagnostic Snapshot

When a session terminates (whether by failure, cancellation, or normal completion), the worker `finally` block saves a diagnostic snapshot **before** calling `_complete_agent_session()`. A nudge guard then re-reads the session from Redis: if the session status is `"pending"` (nudge enqueued) or the session is absent (nudge fallback recreated it), completion is skipped to avoid overwriting the nudge. Otherwise, `_complete_agent_session()` proceeds normally. See [Session Lifecycle](session-lifecycle.md) for the full zombie loop prevention design.

### What Gets Captured

The `save_session_snapshot()` call records:

| Field | Source | Purpose |
|-------|--------|---------|
| `event` | `"crash"` or `"complete"` | Distinguishes failure from clean exit |
| `session_id` | `session.session_id` | Links to bridge session context |
| `agent_session_id` | `session.agent_session_id` | Links to queue-level record |
| `project_key` | `session.project_key` | Scopes to project |
| `tool_count` | `health_check._tool_counts` | Number of tools invoked during session |
| `trigger` | `"finally_block"` | Identifies snapshot origin |

### Tool Counts

`health_check._tool_counts` is the authoritative per-session tool counter, incremented on every tool call and read directly by heartbeat and snapshot reporting.

### Task Await (Exception Propagation)

The `_execute_agent_session()` function awaits the background task directly via `await task._task` rather than a `while task.is_running` / `sleep(2)` polling loop. A polling loop checks only `is_running`, not the task's exception state, so exceptions escaping `BackgroundTask._run_work` would be silently swallowed. Awaiting the asyncio future directly propagates any exception from `_run_work` immediately to the caller, where it is caught and stored in `task._error` for downstream handling.

### Troubleshooting

**Session dies with no trace in logs or snapshots**

The crash snapshot in the `finally` block runs before completion, ensuring at least one diagnostic record exists for every terminated session — including a session that crashes after `_complete_agent_session()` runs but before any snapshot is saved.

### Files

| File | Purpose |
|------|---------|
| `agent/agent_session_queue.py` | Crash snapshot in finally, task await, lifecycle logging |
| `agent/health_check.py` | `_tool_counts`, the authoritative per-session tool counter |
| `tests/unit/test_crash_snapshot.py` | Tests for snapshot saving on all termination paths |

## Related

- [Session Watchdog](session-watchdog.md) — Existing session health monitoring (silence, loops, errors, duration)
- [Agent Session Health Monitor](agent-session-health-monitor.md) — Queue-level stuck session recovery
- [Bridge Self-Healing](bridge-self-healing.md) — Process-level bridge health
- [AgentSession Model](agent-session-model.md) — Unified session lifecycle model
- [Agent Session Queue Reliability](agent-session-queue.md) — Queue-level reliability fixes
- [Session Lifecycle](session-lifecycle.md) — Session state machine, zombie loop prevention
