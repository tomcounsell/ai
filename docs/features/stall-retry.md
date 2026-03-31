# Stall Detection and Automatic Retry

Extends the session watchdog to automatically retry stalled agent sessions with exponential backoff, rather than immediately abandoning them.

## Problem

When a Claude Code agent session hangs mid-execution (SDK subprocess stops producing output), the system previously detected the stall but only abandoned the session. The user's work was lost and they had to manually re-trigger it.

## Solution

The session watchdog now retries stalled sessions up to `STALL_MAX_RETRIES` times (default 3) with exponential backoff before abandoning. Each retry carries context about what was attempted and why it stalled.

## How It Works

### Detection Flow

1. `check_stalled_sessions()` runs every 5 minutes in the watchdog loop
2. Sessions exceeding status-specific thresholds are flagged as stalled
3. `fix_unhealthy_session()` checks if retries remain before deciding action

### Retry Flow

```
Session stalls
  -> Watchdog detects stall
  -> Kill stalled worker task (cancel asyncio task)
  -> Check retry_count < STALL_MAX_RETRIES
  -> Compute backoff: min(10s * 2^retry_count, 300s)
  -> Wait for backoff duration
  -> Re-enqueue with retry context via delete-and-recreate
  -> Worker picks up retry session
  -> Agent continues with context about prior stall
```

### Failure Flow

```
Session stalls (retry_count >= STALL_MAX_RETRIES)
  -> Watchdog detects stall
  -> Mark session as abandoned
  -> Send Telegram notification with diagnostics
  -> Human notified to re-send request
```

## Backoff Formula

```
delay = min(STALL_BACKOFF_BASE * 2^retry_count, STALL_BACKOFF_MAX)
```

With default configuration (base=10s, max=300s):

| Retry | Delay |
|-------|-------|
| 0 | 10s |
| 1 | 20s |
| 2 | 40s |
| 3+ | 300s (capped) |

**Note:** The backoff delay uses `asyncio.sleep()` inside `fix_unhealthy_session()`, which blocks the watchdog from processing other sessions during the wait. With default config and max 3 retries, the longest blocking period is 40s (retry 2), which is acceptable given the 5-minute watchdog interval. A future optimization could schedule retries asynchronously to avoid this.

## Configuration

All settings are configurable via environment variables with safe defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `STALL_MAX_RETRIES` | 3 | Maximum retry attempts before abandoning |
| `STALL_BACKOFF_BASE_SECONDS` | 10 | Base delay for exponential backoff |
| `STALL_BACKOFF_MAX_SECONDS` | 300 | Maximum backoff delay (5 minutes) |
| `STALL_TIMEOUT_SECONDS` | 600 | Fallback stall threshold for sessions without in-memory activity tracking (see [Session Watchdog Reliability](session-watchdog-reliability.md)) |

## Components

### Model Fields (`models/agent_session.py`)

- `retry_count` (int, default=0): How many times the session has been retried after stall detection
- `last_stall_reason` (str, nullable): Diagnostic context from the most recent stall

Both fields are preserved across the delete-and-recreate pattern via `_AGENT_SESSION_FIELDS` in `agent/agent_session_queue.py`.

### Functions (`monitoring/session_watchdog.py`)

- `_compute_stall_backoff(retry_count)`: Computes exponential backoff delay. Handles None (pre-existing sessions without retry_count), negative values, and Popoto Field objects (coerced to int) gracefully.
- `_kill_stalled_worker(project_key: str | Any)`: Cancels the asyncio worker task for a project, removing it from `_active_workers`. Accepts both plain strings and Popoto DB_key objects (coerced via `str()`). Returns False if no worker exists, it's already dead, or the key is None/empty.
- `_enqueue_stall_retry(session, stall_reason)`: Re-enqueues a stalled session using the delete-and-recreate pattern with incremented retry_count, stall reason context, and high priority.
- `_notify_stall_failure(session, stall_reason)`: Sends a Telegram notification via the bridge's registered send callback when retries are exhausted.

### Modified: `fix_unhealthy_session()`

Now checks `retry_count < STALL_MAX_RETRIES` before abandoning silent or long-running sessions. Critical issues (looping, error cascades) are still abandoned immediately since they are likely deterministic.

## What Is NOT Retried

- **Looping sessions**: Repeated identical tool calls indicate a deterministic bug
- **Error cascades**: High error rate suggests a systemic issue
- **User-initiated cancellations**: Only stall-detected retries, not manual cancels

## Pending Session Recovery

Originally added in #342, enhanced in #402. When a pending session is stalled (exceeds `STALL_THRESHOLD_PENDING` of 5 minutes), the watchdog now kills the stuck worker, applies exponential backoff, and re-enqueues the session for retry. After `STALL_MAX_RETRIES` exhausted, the session is abandoned with a Telegram notification.

### Why Ensure-Only Was Insufficient

The original #342 fix called `_ensure_worker()` for stalled pending sessions. This handled Race 2 (worker exits before pickup) but was a no-op when the worker was alive but stuck processing a different session — `_ensure_worker()` saw the existing worker as "alive" and did nothing. Sessions would stall indefinitely with the watchdog logging the same warning every 5 minutes.

### Recovery Flow

```
Pending session stalled > 5 min
  -> check_stalled_sessions() detects stall
  -> _recover_stalled_pending() filters for pending stalls
  -> Load full AgentSession from Redis
  -> Check retry_count < STALL_MAX_RETRIES
  -> _kill_stalled_worker(project_key) cancels stuck worker
  -> asyncio.sleep(backoff) with exponential delay
  -> _enqueue_stall_retry(session, reason) re-enqueues with retry context
  -> Fresh worker picks up the re-enqueued session
  -> Session progresses normally
```

### Failure Flow

```
Pending session stalled (retry_count >= STALL_MAX_RETRIES)
  -> _safe_abandon_session() marks session as abandoned
  -> _notify_stall_failure() sends Telegram notification
  -> Human notified to re-send request
```

### Stale Save Guard

Also added in #342. The `_execute_agent_session()` epilogue previously called `agent_session.save()` when `defer_reaction=True` (auto-continue deferred). This resurrected a ghost session record in Redis because `_enqueue_continuation()` had already deleted the old session and created a fresh pending one. The stale save is now skipped entirely with a debug log.

### Function: `_recover_stalled_pending(stalled)`

In `monitoring/session_watchdog.py`. Called from the watchdog loop after `check_stalled_sessions()`. For each pending stall:
1. Loads the full `AgentSession` from Redis to check retry state
2. If retries remain: kills worker via `_kill_stalled_worker()`, applies backoff, re-enqueues via `_enqueue_stall_retry()`
3. If retries exhausted: abandons via `_safe_abandon_session()` and notifies via `_notify_stall_failure()`
4. Handles edge cases: session deleted between detection and recovery, missing project keys, per-session exception isolation

## Race Condition Mitigations

- Watchdog operates on `active` sessions; agent session health monitor operates on `running` sessions with dead workers. Different status categories prevent overlap.
- The delete-and-recreate pattern is atomic at the Redis level.
- `_safe_abandon_session()` catches `ModelException` for concurrent modification.
- **Stale save guard** (#342): When `defer_reaction=True`, the epilogue skips `agent_session.save()` to prevent resurrecting deleted sessions.
- **Pending recovery** (#402): `_recover_stalled_pending()` kills the stuck worker before re-enqueuing, preventing the no-op scenario where `_ensure_worker()` sees an alive-but-stuck worker. The `_enqueue_stall_retry()` delete-and-recreate pattern handles concurrent modifications safely.

## Related Features

- [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md): Foundation for stall detection (`LIFECYCLE_STALL` log entries)
- [Session Watchdog](session-watchdog.md): The monitoring loop that runs stall checks
- [Agent Session Health Monitor](agent-session-health-monitor.md): Complementary liveness monitor for running sessions with dead workers
- [Bridge Workflow Gaps](bridge-workflow-gaps.md): Auto-continue mechanism that retry builds upon
- [Session Watchdog Reliability](session-watchdog-reliability.md): Activity-based stall detection and observer circuit breaker
