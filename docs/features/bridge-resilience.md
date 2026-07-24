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

**`_agent_session_health_check()`** in `agent/agent_session_queue.py` scans both `running` and `pending` sessions:

- **Running sessions**: If the worker for `session.worker_key` is dead/missing and the session has been running longer than the minimum threshold, recover it (reset to pending)
- **Running sessions with no progress (#944, extended by #1036, #1226, #1724, #1905, #1935)**: If the worker appears alive but the session has been running past `AGENT_SESSION_HEALTH_MIN_RUNNING` with no progress, the two-tier detector fires. Tier 1 sub-check A treats per-turn signals (`last_tool_use_at`, `last_turn_at`) fresher than `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s) as progress; `last_sdk_heartbeat_at` is no longer a progress signal (#1226). Tier 1 sub-check B, only consulted when `sdk_ever_output` is False — i.e. `agent.session_runner.liveness.derive_sdk_ever_output(entry)` is False because none of `last_tool_use_at`, `last_turn_at`, or `last_stdout_at` has ever been set (issue #1935 added `last_stdout_at`, the headless stream's per-event signal, as the third OR-input, closing the toolless-streaming zombie wedge) — treats a fresh `last_heartbeat_at` (queue-layer, 90s window) as progress, but the D0 never-started gate (`running_seconds > NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` = 150s, issue #1724, clock-consistent with sub-check B's own `running_seconds` as of #1905) denies the fast-path for never-started sessions — superseding the prior #1356 grace-to-budget band and its telemetry counter, both pruned in #1905 as unreachable. Tier 2 then checks `compacting` / `children` / `alive` via `psutil`; any passing gate reprieves the kill. See [Bridge Self-Healing §Two-tier no-progress detector](bridge-self-healing.md#two-tier-no-progress-detector) for the full design.
- **Pending sessions**: If no live worker exists for `session.worker_key` and the session has been pending longer than the minimum threshold, start a worker
- **Key invariant**: Sessions with a live worker AND at least one progress signal are never touched

### Startup Recovery

`_recover_interrupted_agent_sessions_startup()` runs once synchronously at **worker startup** (`worker/__main__.py`) before the session processing loop. It resets stale running sessions to pending. Sessions started within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds (300s) are skipped — they may belong to a worker that started in the current process before this function fired. Sessions with `started_at=None` are always recovered. This matches the same timing guard used by the periodic health check (issue #727).

### What Was Removed

| Old Mechanism | Location | Why Removed |
|--------------|----------|-------------|
| `_recover_stalled_pending()` | session_watchdog.py | Used `project_key` instead of `chat_id` |
| `_kill_stalled_worker()` | session_watchdog.py | Looked up workers by wrong key |
| `_enqueue_stall_retry()` | session_watchdog.py | Delete-and-recreate lost sessions |
| `_recover_orphaned_sessions()` | agent_session_queue.py | Complex Redis-level scanning |
| `_reset_running_sessions()` | agent_session_queue.py | Replaced by startup recovery |
| `_notify_stall_failure()` | session_watchdog.py | Retry mechanism removed |

## Startup Retry

The bridge's Telegram connection retry (`bridge/telegram_bridge.py`) now covers all Telethon errors with exponential backoff and jitter (2s to 256s cap, 8 attempts max). Previously only SQLite lock errors were retried.

## Structured Logging

`bridge/log_format.py` provides `StructuredJsonFormatter` that outputs one JSON object per line with fields: `timestamp`, `level`, `logger`, `function`, `message`, plus optional `agent_session_id`, `session_id`, `correlation_id`, `chat_id`.

## SDK Heartbeat

`BackgroundTask._watchdog` in `agent/messenger.py` emits periodic heartbeat logs every 60 seconds during SDK subprocess execution, replacing the single check at 180 seconds.

## Session Status CLI

```bash
python -m agent.agent_session_queue --status
```

Shows all sessions grouped by worker_key with worker status, session IDs, correlation IDs, and dependency health summary.

## Reflections Pre-flight

Each reflection callable is individually responsible for handling missing
prerequisites (Redis, `gh` CLI) gracefully — the callable contract in
`reflections/__init__.py` requires handling `redis.exceptions.ConnectionError`
and returning `status: "error"` or `"skipped"` rather than raising. There is no
centralized preflight class; see `docs/features/adding-reflection-tasks.md` for
the current callable + per-file architecture.

## Worker-liveness Ingestion Signal

When a Telegram message arrives, the bridge enqueues an `AgentSession` for the
worker to drain. If this machine's worker process is dead, that session sits in
`pending` forever — yet the user still sees the normal "seen" reaction (👀), so
the outage looks like work-in-progress. On 2026-05-06 this silent-failure mode
turned a 30-second worker outage into a 7-hour one (issue #1312).

To make a paused pipeline visible, the bridge checks worker liveness at
ingestion time and applies a distinct **⚠ reaction** (`REACTION_WORKER_DOWN`)
when the worker is not alive:

- **Signal source.** `agent.session_health.worker_loop_beacon_fresh(host=None)`
  reads the worker's wall-clock loop beacon `worker:loop_beacon:{host}` (published
  every 30s by `_publish_loop_beacon`). It returns `True` iff the beacon exists
  and its `wall_ts` is within `BRIDGE_WORKER_BEACON_STALE_S` (default **90s**,
  env-overridable) of now. This is the single freshness definition shared with the
  session watchdog (`monitoring/session_watchdog.py` delegates its fresh/stale read
  to it — no duplicate beacon read or threshold constant).
- **wall_ts-only (Risk 1).** Freshness keys ONLY on the wall-clock `wall_ts`,
  never the advisory monotonic `loop_beacon_age_s` (a per-process value that is
  meaningless cross-process). An unarmed-but-fresh beacon (worker up, loop not yet
  ticked) counts as alive — a startup grace that avoids false warnings.
- **Fail-closed.** A missing/expired key, malformed JSON, a missing/non-numeric
  `wall_ts`, or ANY Redis error returns `False` (worker treated as down). A bridge
  that cannot positively confirm a live worker warns the user; a spurious ⚠ during
  a Redis blip is strictly safer than a silent false "all good."
- **Never drops work.** `bridge.response.react_if_worker_down(client, chat_id,
  message_id, session_id)` runs immediately BEFORE each `dispatch_telegram_session`
  call site in `bridge/telegram_bridge.py`; the enqueue proceeds
  **unconditionally** afterward. The reaction is purely additive signalling. When
  the worker is alive, nothing changes — the happy path is byte-identical.
- **Detection window.** The beacon refreshes every 30s and reads fresh for up to
  90s, so a message in the ~90s immediately after the worker dies may still get 👀
  rather than ⚠. For the multi-hour outages this targets, 90s is negligible.
- **Recovery-time clear (#2178).** When ⚠ is set, the helper calls
  `agent.worker_down_reactions.record_worker_down_reaction(...)` so the
  already-merged worker-recovery path can clear the ⚠ once the worker returns. This
  ingestion-time signal is the companion to the recovery machinery documented in
  [Worker Liveness Recovery](worker-liveness-recovery.md).

## Related

- [Worker Liveness Recovery](worker-liveness-recovery.md) - Dead-man's-switch heartbeat + loop beacon this ingestion signal reads
- [Bridge Self-Healing](bridge-self-healing.md) - Crash recovery and watchdog
- [Session Watchdog](session-watchdog.md) - Session health monitoring
- [Agent Session Health Monitor](agent-session-health-monitor.md) - Session liveness checking
