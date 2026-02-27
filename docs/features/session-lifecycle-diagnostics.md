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
| `session_transcript.start_transcript()` | →active | Transcript started |
| `session_transcript.complete_transcript()` | →completed/failed/dormant | Transcript completed |
| `job_queue._push_job()` | →pending | Job enqueued |
| `job_queue._pop_job()` | →running | Worker picked up job |
| `session_watchdog.fix_unhealthy_session()` | →abandoned | Watchdog remediation |

Each call:
1. Emits a structured INFO log: `LIFECYCLE session=X transition=old→new job_id=Y project=Z duration_in_prev_state=Ns context="..."`
2. Updates `last_transition_at` timestamp on the session
3. Appends a `[lifecycle]` entry to the session's history list

### Log Format

```
LIFECYCLE session=tg_valor_-5051653062_6165 transition=pending→running job_id=abc123 project=valor duration_in_prev_state=2.3s context="worker picked up job"
```

Filter all lifecycle events: `grep LIFECYCLE logs/bridge.log`

### Stall Detection

Added to the existing session watchdog loop. Every 5 minutes, `check_stalled_sessions()` queries all sessions in transitional states and checks time-in-state against thresholds:

| Status | Threshold | Rationale |
|--------|-----------|-----------|
| pending | 300s (5 min) | Jobs should be picked up quickly |
| running | 2700s (45 min) | Matches job health monitor timeout |
| active | 600s (10 min) | No `last_activity` update = likely stalled |

For active sessions, `last_activity` is checked first — if recent activity exists within the threshold, the session is not considered stalled.

When a stall is detected:
- A `LIFECYCLE_STALL` warning is logged with session ID, status, duration, and last history entry
- The stalled session info is returned for potential alerting

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
| `models/agent_session.py` | `log_lifecycle_transition()` method, `last_transition_at` field |
| `bridge/session_transcript.py` | Lifecycle calls in start/complete |
| `agent/job_queue.py` | Lifecycle calls in push/pop |
| `monitoring/session_watchdog.py` | Stall detection (`check_stalled_sessions()`) |
| `monitoring/session_status.py` | CLI session status report |
| `tests/test_lifecycle_transition.py` | Integration tests for lifecycle logging |
| `tests/unit/test_stall_detection.py` | Unit tests for stall detection |
| `tests/unit/test_session_status.py` | Unit tests for CLI report |

## Related

- [Session Watchdog](session-watchdog.md) — Existing session health monitoring (silence, loops, errors, duration)
- [Job Health Monitor](job-health-monitor.md) — Queue-level stuck job recovery
- [Bridge Self-Healing](bridge-self-healing.md) — Process-level bridge health
- [AgentSession Model](agent-session-model.md) — Unified session lifecycle model
- Issue #216 — Tracking issue
