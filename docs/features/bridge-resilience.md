# Bridge Resilience

Graceful degradation and recovery pipeline for the Telegram bridge.

## Overview

The bridge resilience system provides circuit breaker protection for external dependencies, a unified session recovery mechanism, general startup retry, structured logging, and pre-flight validation for reflections.

## Circuit Breaker Pattern

`bridge/resilience.py` provides a reusable `CircuitBreaker` class with three states:

- **CLOSED**: Normal operation, all requests pass through
- **OPEN**: Dependency is down, requests fail fast without calling the dependency
- **HALF_OPEN**: After a cooldown period, one probe request is allowed through

### Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `failure_threshold` | Failures within window to trigger OPEN | 5 |
| `failure_window` | Time window for counting failures (seconds) | 60.0 |
| `half_open_interval` | Wait before probing after OPEN (seconds) | 30.0 |

### Usage

The Anthropic circuit breaker in `agent/sdk_client.py` protects against sustained API failures:

1. Before each SDK query, the circuit is checked
2. If OPEN, a `CircuitOpenError` is raised immediately
3. The worker loop catches `CircuitOpenError` and leaves the session as **pending** (not failed)
4. The unified health check starts a worker when the circuit closes

## Dependency Health

`bridge/health.py` provides `DependencyHealth`, a registry of all circuit breakers. Used by the session status CLI to show health summary.

## Unified Recovery Loop

The six competing recovery mechanisms from the old system were replaced with one:

**`_job_health_check()`** in `agent/agent_session_queue.py` scans both `running` and `pending` jobs:

- **Running sessions**: If the worker for `session.chat_id` is dead/missing and the session has been running longer than the minimum threshold, recover it (delete-and-recreate as pending)
- **Pending sessions**: If no live worker exists for `session.chat_id` and the session has been pending longer than the minimum threshold, start a worker
- **Key invariant**: Jobs with a live worker on the same `chat_id` are never touched

### Startup Recovery

`_recover_interrupted_sessions_startup()` runs once synchronously at bridge startup before the event loop. It resets ALL running sessions to pending unconditionally (at startup, all running sessions are orphaned from the previous process).

### What Was Removed

| Old Mechanism | Location | Why Removed |
|--------------|----------|-------------|
| `_recover_stalled_pending()` | session_watchdog.py | Used `project_key` instead of `chat_id` |
| `_kill_stalled_worker()` | session_watchdog.py | Looked up workers by wrong key |
| `_enqueue_stall_retry()` | session_watchdog.py | Delete-and-recreate lost jobs |
| `_recover_orphaned_sessions()` | agent_session_queue.py | Complex Redis-level scanning |
| `_reset_running_jobs()` | agent_session_queue.py | Replaced by startup recovery |
| `_notify_stall_failure()` | session_watchdog.py | Retry mechanism removed |

## Startup Retry

The bridge's Telegram connection retry (`bridge/telegram_bridge.py`) now covers all Telethon errors with exponential backoff and jitter (2s to 256s cap, 8 attempts max). Previously only SQLite lock errors were retried.

## Structured Logging

`bridge/log_format.py` provides `StructuredJsonFormatter` that outputs one JSON object per line with fields: `timestamp`, `level`, `logger`, `function`, `message`, plus optional `job_id`, `session_id`, `correlation_id`, `chat_id`.

## SDK Heartbeat

`BackgroundTask._watchdog` in `agent/messenger.py` emits periodic heartbeat logs every 60 seconds during SDK subprocess execution, replacing the single check at 180 seconds.

## Job Status CLI

```bash
python -m agent.job_queue --status
```

Shows all jobs grouped by chat_id with worker status, session IDs, correlation IDs, and dependency health summary.

## Reflections Pre-flight

`ReflectionRunner._preflight_check()` validates prerequisites (Redis, gh CLI) before each reflection step, logging a single warning line on failure instead of crashing with a traceback.

## Related

- [Bridge Self-Healing](bridge-self-healing.md) - Crash recovery and watchdog
- [Session Watchdog](session-watchdog.md) - Session health monitoring
- [Agent Session Health Monitor](agent-session-health-monitor.md) - Session liveness checking
