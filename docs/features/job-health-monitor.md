# Job Health Monitor

Automatically detects and recovers stuck running jobs in the Redis-based job queue.

## Overview

The job health monitor runs as a periodic async task alongside the bridge process. Every 5 minutes, it scans all `running` RedisJobs to check:

1. Whether the associated worker coroutine is still alive
2. Whether the job has exceeded its maximum duration

When a stuck job is detected, it is automatically recovered by deleting it and re-creating it as `pending`, allowing a new worker to pick it up.

## How It Works

### Detection

- **Dead worker detection**: Checks `_active_workers[project_key]` asyncio Task liveness via `.done()`. If the task has finished (crashed, cancelled, or completed), the job is considered orphaned.
- **Timeout detection**: Compares `started_at` timestamp against the configured max duration for the job type.
- **Race condition guard**: Jobs must be running for at least 5 minutes (`JOB_HEALTH_MIN_RUNNING`) before they become eligible for recovery. This prevents false positives on jobs that just started processing.

### Timeouts

| Job Type | Timeout | Detection |
|----------|---------|-----------|
| Standard | 45 minutes | Default for all jobs |
| Build | 2.5 hours | Detected by `/do-build` in `message_text` |

Timeouts are measured from `started_at` (when the job begins processing), not `created_at` (when it was enqueued). This correctly accounts for queue wait time.

### Recovery

When a stuck job is found:

1. Log a warning with the job ID, project key, and reason (dead worker or timeout)
2. Delete the orphaned RedisJob from Redis
3. Re-create it as `pending` with all original data preserved
4. Call `_ensure_worker()` to restart the processing loop for that project

### Startup Integration

The health check loop starts automatically with the bridge process, alongside the existing session watchdog. Both run at 5-minute intervals but monitor different concerns:

- **Session watchdog** (`monitoring/session_watchdog.py`): Monitors `AgentSession` objects at the application level
- **Job health monitor** (`agent/job_queue.py`): Monitors `RedisJob` status at the queue level

## CLI Usage

```bash
# Show current queue state
python -m agent.job_queue --status

# Recover all stuck running jobs (orphaned workers)
python -m agent.job_queue --flush-stuck

# Recover a specific job by ID
python -m agent.job_queue --flush-job <JOB_ID>
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

Constants in `agent/job_queue.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `JOB_HEALTH_CHECK_INTERVAL` | 300 (5 min) | How often the health check runs |
| `JOB_TIMEOUT_DEFAULT` | 2700 (45 min) | Max runtime for standard jobs |
| `JOB_TIMEOUT_BUILD` | 9000 (2.5 hr) | Max runtime for build jobs |
| `JOB_HEALTH_MIN_RUNNING` | 300 (5 min) | Min runtime before recovery eligible |

## Related

- [redis-job-queue.md](redis-job-queue.md) -- The underlying Redis job queue
- [session-watchdog.md](session-watchdog.md) -- Session-level health monitoring (complementary layer)
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level health monitoring
- `agent/job_queue.py` -- Implementation source
- Issue #127 -- Original tracking issue
