# Session Watchdog Reliability

Hardened reliability fixes for the session watchdog, SDK stall detection, and observer error handling. Addresses three interrelated failure modes that degraded session reliability.

## Problem

1. **Stalled push-* sessions** triggered `AttributeError: 'str' object has no attribute 'redis_key'` every 5 minutes indefinitely because the watchdog assumed `project_key` was always a Popoto DB_key object.
2. **Hard SDK timeouts** (600s) killed in-progress work regardless of whether the session was actively producing output, wasting compute on timeout-steer-timeout cycles.
3. **Observer import crashes** (`load_principal_context` ImportError) went unhandled with no circuit breaker, causing infinite retry loops.

## Solution

### 1. Watchdog Type Guards (monitoring/session_watchdog.py)

- `str()` coercion on `project_key` in `_kill_stalled_worker()` and `fix_unhealthy_session()` prevents AttributeError on plain string values
- `_recover_stalled_pending()` handles None and empty project_key gracefully
- Orphan push-* sessions stuck >1 hour with no history are automatically abandoned and the user is notified
- Guard: if session status changed since stall detection, skip recovery (prevents double-processing)
- **query.filter fix**: `_recover_stalled_pending()` uses `AgentSession.query.filter(session_id=...)` instead of `query.get()` because `session_id` is a `Field` (not a `KeyField`). Using `query.get()` on a non-key field silently returns no results, causing stalled pending sessions to be skipped instead of recovered. (Commit `7e503655`)

### 2. Activity-Based Stall Detection (agent/sdk_client.py)

Instead of hard wall-clock timeouts, the system now tracks session activity:

- `_last_activity_timestamps` dict tracks the `updated_at` timestamp of last tool call or log output per session
- `record_session_activity(session_id)` updates the timestamp on each text block output and result message during SDK query execution
- `get_session_last_activity(session_id)` exposes the timestamp for watchdog consumption
- `clear_session_activity(session_id)` cleans up when a session completes
- `SDK_INACTIVITY_TIMEOUT_SECONDS` env var (default: 300s) configures the inactivity threshold
- Active sessions producing tool calls/logs are never interrupted regardless of total runtime

### 3. Observer Circuit Breaker (bridge/observer.py)

Errors are classified and handled with escalating backoff:

- **Error classification**: `_classify_observer_error()` categorizes errors as `retryable` (API/outage: overloaded, rate_limit, timeout, 5xx) or `non_retryable` (import errors, config issues, logic bugs)
- **Exponential backoff**: Retryable errors get backoff delays of 30s, 60s, 120s, 240s, 480s (capped)
- **Escalation**: After 5 consecutive retryable failures OR on any non-retryable error, escalates to Telegram with actionable error details
- **Counter reset**: Successful observer runs reset the failure counter
- **Import guard**: `_build_observer_system_prompt()` wraps `load_principal_context` import in try/except; on ImportError, builds prompt without principal context

### 4. Escalation Handling (agent/agent_session_queue.py)

The session queue processes circuit breaker signals from observer error results:

- `retry_after`: Sleeps for the backoff duration, then re-runs the observer
- `should_escalate`: Sends an escalation notice to Telegram with session ID, failure count, and error details, then delivers raw worker output as fallback

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SDK_INACTIVITY_TIMEOUT_SECONDS` | 300 | Seconds of inactivity before a session is considered stalled |
| `STALL_TIMEOUT_SECONDS` | 600 | Fallback stall threshold for sessions without activity tracking |

## Architecture

```
Watchdog Cycle (5 min)
├── check_stalled_sessions()
│   ├── _recover_stalled_pending()  ← type guards, orphan cleanup
│   └── fix_unhealthy_session()     ← str(project_key) coercion
│
├── Activity Check
│   └── get_session_last_activity() → time_since_last_activity
│       ├── Active (< threshold): skip
│       └── Inactive (> threshold): kill + retry
│
Observer Error Path
├── _classify_observer_error()
│   ├── retryable → observer_record_failure() → {retry_after, should_retry}
│   └── non_retryable → observer_record_failure() → {should_escalate}
│
└── Job Queue Handler
    ├── retry_after → sleep → re-run observer
    └── should_escalate → Telegram notice + raw output delivery
```

## Testing

- `tests/unit/test_pending_recovery.py` - String project_key, None handling, orphan push-* cleanup, status change detection
- `tests/unit/test_session_watchdog.py` - _kill_stalled_worker guards (None, empty, '?')
- `tests/unit/test_observer.py` - Error classification, backoff schedule, circuit breaker state, import guard
- `tests/unit/test_sdk_client_sdlc.py` - Activity tracking: record, get, clear, inactivity detection

## Watchdog Hardening (issue #1128)

Three additive reliability features layered on top of the detection
signals documented above. None of them change the heartbeat or activity
detection paths — they extend the actuator surface.

1. **Automatic loop-break steering.** `detect_repetition` and
   `detect_error_cascade` no longer log and return — they enqueue a
   targeted steering message via `push_steering_message(...,
   sender="watchdog")`. A per-reason atomic Redis `SET NX EX` cooldown
   prevents flooding. Drain timing is the PostToolUse-hook turn
   boundary. See [Session Watchdog](session-watchdog.md) for thresholds
   and env-var gating.

2. **Two-path per-session token tracking.** Every SDK ResultMessage AND
   every harness `result` event feeds into a single
   `agent/sdk_client.py::accumulate_session_tokens` helper, which
   persists `total_input_tokens`, `total_output_tokens`,
   `total_cache_read_tokens`, and `total_cost_usd` onto `AgentSession`.
   Dashboards (`/dashboard.json`) surface the four fields. The watchdog
   is READ-ONLY for these fields — writes happen only in the worker
   process. A soft-threshold alert triggers a `token_alert` steer when
   cumulative tokens cross `TOKEN_ALERT_THRESHOLD` on a running session.

3. **Worker-internal idle SDK-client teardown.** A new
   `worker/idle_sweeper.py` task runs inside the worker process,
   snapshots `agent.sdk_client._active_clients`, and proactively closes
   persistent SDK clients on dormant / paused / paused_circuit sessions
   whose `updated_at` age exceeds `IDLE_TEARDOWN_THRESHOLD` (default 24h).
   This sits well inside the ~48h Anthropic silent-death window (#1104).
   The session-watchdog process is intentionally NOT involved — the
   registry is worker-process-local.

## Related

- [Session Watchdog](session-watchdog.md) - Base watchdog implementation
- [Session Steering](session-steering.md) - Steering queue + `sender` attribution
- [Bridge Worker Architecture](bridge-worker-architecture.md) - Process topology for idle teardown
- [Bridge Self-Healing](bridge-self-healing.md) - Broader crash recovery system
- [Stall Retry](stall-retry.md) - Retry mechanism for stalled sessions
- [Chat Dev Session Architecture](pm-dev-session-architecture.md) - Session routing architecture
- [SDLC Pipeline Integrity](sdlc-pipeline-integrity.md) - Worker post-completion stage state injection and pipeline state feedback
- [Session Isolation: Raw-String Session Lookup](session-isolation.md#model-fields) - Canonical `AgentSession.get_by_id()` pattern (issue #765 systemic fix)
