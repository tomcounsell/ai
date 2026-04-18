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

- **Dead worker detection**: Checks `_active_workers[worker_key]` asyncio Task liveness via `.done()`. If the task has finished (crashed, cancelled, or completed), the session is considered orphaned.
- **No-progress detection (issue #944, extended by #1036)**: Even when the worker is alive, a running session past the 300s startup guard is recovered if it shows no progress. `_has_progress(entry)` now uses a **two-tier** detector — see [Bridge Self-Healing §Two-tier no-progress detector](bridge-self-healing.md#two-tier-no-progress-detector) for the full design. In brief:
  - **Tier 1 (dual heartbeat):** either `last_heartbeat_at` (queue-layer) or `last_sdk_heartbeat_at` (messenger-layer) fresh within 90s counts as progress. Both must be stale for the session to be flagged. The original three own-progress signals (`turn_count > 0`, non-empty `log_path`, non-empty `claude_session_uuid`) and the #963 child-activity check are preserved.
  - **Tier 2 (reprieve gates, `no_progress` only):** if Tier 1 flags a session, `_tier2_reprieve_signal()` checks process-alive / has-children / recent-stdout via `psutil`. Any one passing gate reprieves the kill, increments `reprieve_count`, and emits a `tier2_reprieve_total:{alive|children|stdout}` counter. `worker_dead` and `timeout` recoveries skip Tier 2 entirely.
  - **Kill path:** cancels `handle.task` from `_active_sessions` registry; increments `recovery_attempts`; finalizes as `failed` at `MAX_RECOVERY_ATTEMPTS=2` (history preserved); otherwise transitions `running → pending`. `DISABLE_PROGRESS_KILL=1` suppresses kills while keeping flagging active.
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

1. Log a warning with the session ID, project key, and reason (dead worker, no progress signal, or timeout)
2. Increment the project-scoped Redis counter `{project_key}:session-health:recoveries:{worker_dead|no_progress|timeout}` for observability (non-fatal on failure)
3. For `no_progress` recoveries: run Tier 2 reprieve gates — if any gate passes, skip recovery this cycle (reprieve)
4. Cancel the session task via `_active_sessions` registry and wait up to `TASK_CANCEL_TIMEOUT` (0.25s)
5. Increment `recovery_attempts`; if `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (2), finalize as `failed` (history preserved); otherwise transition to `pending` (local sessions finalize as `abandoned`)
6. Call `_ensure_worker()` to restart the processing loop for that project

### Startup Integration

The health check loop starts automatically with the **worker process** (`python -m worker`), alongside the session notify listener and session watchdog. Both the health loop and notify listener run as background asyncio tasks in the worker:

- **Session notify listener** (`_session_notify_listener()` in `agent/agent_session_queue.py`): Subscribes to the `valor:sessions:new` Redis pub/sub channel. Extracts `worker_key` from the payload and calls `_ensure_worker(worker_key, is_project_keyed)` immediately — ~1s pickup latency. This is the fast path for normal operation. Uses a **dedicated** `redis.Redis` connection with `socket_timeout=None` so `pubsub.listen()` blocks indefinitely between messages, instead of inheriting the global `POPOTO_REDIS_DB` pool's `socket_timeout=5` (which would cause a reconnect cycle and a guaranteed message-loss window — issue #824).
- **Agent session health monitor** (`_agent_session_health_loop()` in `agent/session_health.py`, re-exported from `agent_session_queue.py`): Runs every 5 minutes. Recovers sessions missed by pub/sub (Redis restart, worker not running at publish time, bypass paths). This is the safety net. The task is named `session-health-monitor` and registers a `done_callback` (`_health_task_done`) that logs ERROR if the loop exits unexpectedly with an exception (cancellation during shutdown is ignored). This mirrors the `_notify_task_done` pattern on `notify_task` and prevents silent loss of health monitoring.
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

Constants in `agent/session_health.py` (re-exported from `agent_session_queue.py`):

| Constant | Default | Description |
|----------|---------|-------------|
| `AGENT_SESSION_HEALTH_CHECK_INTERVAL` | 300 (5 min) | How often the health check runs |
| `AGENT_SESSION_TIMEOUT_DEFAULT` | 2700 (45 min) | Max runtime for standard sessions |
| `AGENT_SESSION_TIMEOUT_BUILD` | 9000 (2.5 hr) | Max runtime for build sessions |
| `AGENT_SESSION_HEALTH_MIN_RUNNING` | 300 (5 min) | Min runtime before recovery eligible |
| `HEARTBEAT_FRESHNESS_WINDOW` | 90s | Either heartbeat within this window = progress |
| `STDOUT_FRESHNESS_WINDOW` | 90s | `last_stdout_at` within this window = Tier 2 reprieve |
| `HEARTBEAT_WRITE_INTERVAL` | 60s | How often `_heartbeat_loop` writes `last_heartbeat_at` |
| `MAX_RECOVERY_ATTEMPTS` | 2 | Kills before session is finalized as `failed` |
| `TASK_CANCEL_TIMEOUT` | 0.25s | Grace period after `handle.task.cancel()` |

## Related

- [scale-agent-session-queue-with-popoto-and-worktrees.md](scale-agent-session-queue-with-popoto-and-worktrees.md) -- The underlying Redis agent session queue
- [session-watchdog.md](session-watchdog.md) -- Session-level health monitoring (complementary layer)
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level health monitoring
- [agent-session-model.md](agent-session-model.md) -- AgentSession model fields and lifecycle
- `agent/session_health.py` -- Health monitor and startup recovery implementation
- `agent/agent_session_queue.py` -- Queue entry points (re-exports from session_health and other modules)
- Issue #127 -- Original tracking issue
- Issue #944 -- No-progress recovery for sessions stuck behind a shared-worker-key PM
- Issue #1036 -- Two-tier no-progress detector (dual heartbeat + Tier 2 reprieve gates)
