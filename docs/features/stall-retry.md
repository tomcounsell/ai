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
  -> Worker picks up retry job
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
| `STALL_TIMEOUT_SECONDS` | 600 | Active session stall threshold (overrides hardcoded 10min) |

## Components

### Model Fields (`models/agent_session.py`)

- `retry_count` (int, default=0): How many times the session has been retried after stall detection
- `last_stall_reason` (str, nullable): Diagnostic context from the most recent stall

Both fields are preserved across the delete-and-recreate pattern via `_JOB_FIELDS` in `agent/job_queue.py`.

### Functions (`monitoring/session_watchdog.py`)

- `_compute_stall_backoff(retry_count)`: Computes exponential backoff delay. Handles None (pre-existing sessions without retry_count), negative values, and Popoto Field objects (coerced to int) gracefully.
- `_kill_stalled_worker(project_key)`: Cancels the asyncio worker task for a project, removing it from `_active_workers`. Returns False if no worker exists or it's already dead.
- `_enqueue_stall_retry(session, stall_reason)`: Re-enqueues a stalled session using the delete-and-recreate pattern with incremented retry_count, stall reason context, and high priority.
- `_notify_stall_failure(session, stall_reason)`: Sends a Telegram notification via the bridge's registered send callback when retries are exhausted.

### Modified: `fix_unhealthy_session()`

Now checks `retry_count < STALL_MAX_RETRIES` before abandoning silent or long-running sessions. Critical issues (looping, error cascades) are still abandoned immediately since they are likely deterministic.

## What Is NOT Retried

- **Looping sessions**: Repeated identical tool calls indicate a deterministic bug
- **Error cascades**: High error rate suggests a systemic issue
- **User-initiated cancellations**: Only stall-detected retries, not manual cancels

## Race Condition Mitigations

- Watchdog operates on `active` sessions; job health monitor operates on `running` sessions with dead workers. Different status categories prevent overlap.
- The delete-and-recreate pattern is atomic at the Redis level.
- `_safe_abandon_session()` catches `ModelException` for concurrent modification.

## Related Features

- [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md): Foundation for stall detection (`LIFECYCLE_STALL` log entries)
- [Session Watchdog](session-watchdog.md): The monitoring loop that runs stall checks
- [Job Health Monitor](job-health-monitor.md): Complementary liveness monitor for running jobs with dead workers
- [Coaching Loop](coaching-loop.md): Auto-continue mechanism that retry builds upon
