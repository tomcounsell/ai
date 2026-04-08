# Agent Session Health Monitor

Automatically detects and recovers stuck running sessions in the Redis-based agent session queue.

## Overview

The agent session health monitor runs as a periodic async task alongside the bridge process. Every 5 minutes, it scans all `running` AND `pending` AgentSessions to check:

1. Whether the associated worker coroutine is still alive
2. Whether the session has exceeded its maximum duration
3. Whether pending sessions have a worker that can process them

This is the **single unified recovery mechanism** — it replaces six competing recovery functions that previously raced against each other. See [Bridge Resilience](bridge-resilience.md) for the full refactoring context.

When a stuck running session is detected, it is automatically recovered by deleting it and re-creating it as `pending`. When an orphaned pending session is found (no live worker), a worker is started for it.

## How It Works

### Detection

- **Dead worker detection**: Checks `_active_workers[chat_id]` asyncio Task liveness via `.done()`. If the task has finished (crashed, cancelled, or completed), the session is considered orphaned.
- **Timeout detection**: Compares `started_at` timestamp against the configured max duration for the session type.
- **Race condition guard**: Jobs must be running for at least 5 minutes (`AGENT_SESSION_HEALTH_MIN_RUNNING`) before they become eligible for recovery. This prevents false positives on jobs that just started processing.

### Timeouts

| Job Type | Timeout | Detection |
|----------|---------|-----------|
| Standard | 45 minutes | Default for all sessions |
| Build | 2.5 hours | Detected by `/do-build` in `message_text` |

Timeouts are measured from `started_at` (when the session begins processing), not `created_at` (when it was enqueued). This correctly accounts for queue wait time.

### Recovery

When a stuck session is found:

1. Log a warning with the session ID, project key, and reason (dead worker or timeout)
2. Delete the orphaned AgentSession from Redis
3. Re-create it as `pending` with all original data preserved
4. Call `_ensure_worker()` to restart the processing loop for that project

### Startup Integration

The health check loop starts automatically with the **worker process** (`python -m worker`), alongside the session notify listener and session watchdog. Both the health loop and notify listener run as background asyncio tasks in the worker:

- **Session notify listener** (`_session_notify_listener()` in `agent/agent_session_queue.py`): Subscribes to the `valor:sessions:new` Redis pub/sub channel. Calls `_ensure_worker(chat_id)` immediately when a session is published — ~1s pickup latency. This is the fast path for normal operation. Uses a **dedicated** `redis.Redis` connection with `socket_timeout=None` so `pubsub.listen()` blocks indefinitely between messages, instead of inheriting the global `POPOTO_REDIS_DB` pool's `socket_timeout=5` (which would cause a reconnect cycle and a guaranteed message-loss window — issue #824). The worker also wires a `_health_task_done` callback to the health check task so silent crashes in either loop are detected and logged.
- **Agent session health monitor** (`_agent_session_health_loop()` in `agent/agent_session_queue.py`): Runs every 5 minutes. Recovers sessions missed by pub/sub (Redis restart, worker not running at publish time, bypass paths). This is the safety net.
- **Session watchdog** (`monitoring/session_watchdog.py`): Monitors `AgentSession` objects at the application level (separate from queue-level monitoring)

### Done Callback — `_health_task_done`

`health_task` is registered with a `_health_task_done` done_callback (added in #825, mirroring the identical pattern on `notify_task`):

```python
def _health_task_done(t: asyncio.Task) -> None:
    if t.cancelled():
        return  # Normal shutdown path
    exc = t.exception()
    if exc is not None:
        logger.error("Health monitor task exited unexpectedly: %s", exc)

health_task.add_done_callback(_health_task_done)
```

The callback guards against unexpected task exits that bypass the health loop's own `except Exception` handler — specifically `BaseException` subclasses (`SystemExit`, `KeyboardInterrupt`) and asyncio-internal exits. Ordinary exceptions are already caught inside the loop's `while True / try-except` block and cannot escape. On normal `SIGTERM` shutdown, `health_task.cancel()` triggers `CancelledError`, which the `if t.cancelled(): return` guard suppresses so no false ERROR is logged.

## CLI Usage

```bash
# Show current queue state
python -m agent.agent_session_queue --status

# Recover all stuck running sessions (orphaned workers)
python -m agent.agent_session_queue --flush-stuck

# Recover a specific session by ID
python -m agent.agent_session_queue --flush-session <SESSION_ID>
```

### Example `--status` output

```
=== dm ===
  Worker: alive
  [  running] abc123 (running 5m) - How do I configure...
  [  pending] def456 (queued 2m) - Please review...

Total: 2 jobs (1 pending, 1 running)
```

## Configuration

Constants in `agent/agent_session_queue.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `AGENT_SESSION_HEALTH_CHECK_INTERVAL` | 300 (5 min) | How often the health check runs |
| `AGENT_SESSION_TIMEOUT_DEFAULT` | 2700 (45 min) | Max runtime for standard sessions |
| `AGENT_SESSION_TIMEOUT_BUILD` | 9000 (2.5 hr) | Max runtime for build sessions |
| `AGENT_SESSION_HEALTH_MIN_RUNNING` | 300 (5 min) | Min runtime before recovery eligible |

## Related

- [scale-agent-session-queue-with-popoto-and-worktrees.md](scale-agent-session-queue-with-popoto-and-worktrees.md) -- The underlying Redis agent session queue
- [session-watchdog.md](session-watchdog.md) -- Session-level health monitoring (complementary layer)
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level health monitoring
- [agent-session-model.md](agent-session-model.md) -- AgentSession model fields and lifecycle
- `agent/agent_session_queue.py` -- Implementation source
- Issue #127 -- Original tracking issue
