# Stall Recovery: Stall-Advisory Action Mode

**Issue:** #1768
**Status:** Shipped

## Overview

The stall-advisory reflection (`reflections/stall_advisory.py`) previously detected wedged sessions and warned to the log. It never acted. Three granite sessions on 2026-06-23 wedged in a turn-0 loop (heartbeating-but-stuck) and saturated the worker thread pool before a human intervened.

This feature promotes stall-advisory from advisory-only to a gated actor: when a session is demonstrably stuck (not just slow), the reflection kills it and re-enqueues its unanswered work via `valor-catchup`. All action is dry-run by default.

## The granite_wedged Signal

A granite PTY session can wedge in a turn-0 loop: the container starts, reads its transcript repeatedly, finds no new entry each time, and never advances a turn. During this wedge:

- `last_pty_read_loop_at` stays fresh (container is actively cycling)
- `last_heartbeat_at` stays fresh (session looks alive to the orphan reaper)
- `last_pty_activity_at` goes stale (no genuine screen repaint)
- `turn_start` events: zero (the session has never turned)

The `classify_session_stall()` function in `agent/session_stall_classifier.py` fires the `granite_wedged` verdict when all four conditions are true for a running session:

1. Session status is in `_RUNNING_PROBE_STATUSES`
2. Zero `turn_start` events in the telemetry window
3. `last_pty_read_loop_at` is fresh (within `GRANITE_WEDGED_READLOOP_FRESH_SECS`)
4. `last_pty_activity_at` is present and stale (older than `GRANITE_WEDGED_PTY_STALE_SECS`)

Fail-soft: if either field is None or unconvertible, the classifier falls through to the existing `never_started` path without fabricating a wedge verdict.

### Constants

| Constant | Default | Env var |
|----------|---------|---------|
| `GRANITE_WEDGED_PTY_STALE_SECS` | 600 | `GRANITE_WEDGED_PTY_STALE_SECS` |
| `GRANITE_WEDGED_READLOOP_FRESH_SECS` | 90 | `GRANITE_WEDGED_READLOOP_FRESH_SECS` |

Both are marked provisional/tunable. At the defaults, a session whose screen has been frozen for 10 minutes while the read-loop ticks every 90 seconds or faster is classified `granite_wedged`.

## The PTY Diff-Gate Normalization (Critical Dependency)

`last_pty_activity_at` is stamped by `_on_pty_read` in `agent/granite_container/bridge_adapter.py` only when the PTY buffer differs from the previous read. This diff-gate is what makes the field a reliable quiescence signal.

Before this feature shipped, the diff compared ANSI-stripped but not normalized buffers. A wedged TUI whose spinner glyph, elapsed-seconds counter, or cursor blink keeps animating at 1 Hz would repaint the buffer on every read, keeping `last_pty_activity_at` fresh and defeating `granite_wedged` detection entirely.

The prerequisite fix (`_normalize_pty_buffer` in `bridge_adapter.py`) strips spinner glyphs and verbs, elapsed-seconds counters, and cursor/blink/trailing-whitespace noise before the comparison. An animating-but-wedged TUI now produces a stable normalized buffer, so `last_pty_activity_at` genuinely goes stale on a wedge.

`last_pty_read_loop_at` is stamped unconditionally on every read-loop call. This field is not normalized and is not diff-gated.

**Dependency note:** if a future change makes `last_pty_activity_at` stamp unconditionally (removing the diff-gate), `granite_wedged` will stop firing silently. The normalization and the diff-gate are load-bearing for this signal. The unit test for `_normalize_pty_buffer` (spinner-only delta normalizes equal, real-content delta normalizes unequal) pins this contract.

## Action Mode: Gate Ladder

For every `stalled` finding, `_maybe_recover()` in `stall_advisory.py` runs the following checks in order:

1. **Actionable reason filter.** Only the reasons in `_ACTIONABLE_STALL_REASONS` proceed. The set is `{never_started, granite_wedged, idle_gap_exceeded_stall}`. A `kill_transition` or other stalled reason is observed and logged but never acted on.

2. **Consecutive-observation counter.** A Redis key `{project_key}:stall-recovery:consec:{session_id}` is incremented atomically. If the count is below `stall_recovery_consecutive_observations`, the function logs "observation N/M" and returns. At the 300-second reflection cadence, 3 observations is approximately 15 minutes.

3. **Per-run budget check.** At most `stall_recovery_run_budget` sessions are killed per reflection tick. When the budget is exhausted, remaining sessions are skipped until the next tick.

4. **Per-session budget check.** A Redis key `{project_key}:stall-recovery:budget:{session_id}` (TTL 24h) caps kill attempts per session at `stall_recovery_per_session_budget`. This prevents thrash if a session keeps re-wedging.

5. **Dry-run gate (default).** If `FEATURES__STALL_RECOVERY_ENABLED` is false (the default), the function logs `[stall-recovery] WOULD kill+recover session=... reason=... (dry-run)`, emits a `stall_recovery_action` session-event with `dry_run=True`, and returns without mutating anything.

6. **Terminal re-read race guard.** Before killing, the session is re-read from Redis. If it has transitioned to a terminal status since classification, the kill is skipped and the consecutive counter is reset.

7. **Kill and re-enqueue.** `_kill_agent_session(session)` terminates the PID and sets status to `killed`. Then `valor-catchup` is invoked as a subprocess to re-enqueue genuinely-unanswered human messages. Catchup failure is logged and counted but not fatal: the wedged session is stopped regardless.

When a session classifies healthy or suspect in a given tick, its consecutive-observation counter is deleted. A single slow-but-live turn does not accumulate toward a kill.

`suspect` sessions are never acted on. Only `stalled` sessions with an actionable reason reach the gate ladder.

## Feature Flag and Thresholds

| Setting | Default | Env var |
|---------|---------|---------|
| `FEATURES__STALL_RECOVERY_ENABLED` | `false` | `FEATURES__STALL_RECOVERY_ENABLED` |
| `FEATURES__STALL_RECOVERY_CONSECUTIVE_OBSERVATIONS` | `3` | `FEATURES__STALL_RECOVERY_CONSECUTIVE_OBSERVATIONS` |
| `FEATURES__STALL_RECOVERY_RUN_BUDGET` | `1` | `FEATURES__STALL_RECOVERY_RUN_BUDGET` |
| `FEATURES__STALL_RECOVERY_PER_SESSION_BUDGET` | `2` | `FEATURES__STALL_RECOVERY_PER_SESSION_BUDGET` |

All thresholds are marked provisional/tunable. The defaults are conservative: one kill per tick, 15 minutes of consecutive detection before acting, and a two-kill cap per session lifetime.

### Enabling on a machine

Add to `~/Desktop/Valor/.env`:

```
FEATURES__STALL_RECOVERY_ENABLED=true
```

Then restart the worker:

```
./scripts/valor-service.sh worker-restart
```

### Reverting

Set `FEATURES__STALL_RECOVERY_ENABLED=false` (or remove the line) and restart the worker. No data migration is needed.

### Dry-run behavior

With the flag off (the default), the reflection runs the full gate ladder up to step 5 and logs what it would have done:

```
[stall-recovery] WOULD kill+recover session=<id> reason=granite_wedged (dry-run)
```

It also emits a `stall_recovery_action` session-event with `dry_run=True` (visible on the dashboard feed and queryable). No session is killed, no counter is written to the kill-budget key, and `valor-catchup` is not invoked.

## Audit and Telemetry

Every kill or skip decision is logged with the triggering verdict reason at `WARNING` level.

A typed `stall_recovery_action` session-event is appended via `_append_session_event` (same pattern as `granite_user_routed`) for every dry-run and every kill attempt. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `verdict_reason` | str | Classifier reason (`granite_wedged`, `never_started`, etc.) |
| `killed` | bool | Whether the session was actually killed |
| `catchup_invoked` | bool | Whether `valor-catchup` was called |
| `catchup_ok` | bool | Whether `valor-catchup` returned exit code 0 |
| `dry_run` | bool | Whether this was a dry-run (flag off) |

The reflection summary is extended with recovery counts:

```
3 running session(s): 1 stalled, 0 suspect, 2 healthy; recovery: 1 killed, 0 would-kill (dry-run), 0 catchup-failed
```

A kill-succeeds-but-catchup-fails outcome surfaces as `catchup_ok=False` in the session-event and as a `catchup-failed` count in the summary. It is not silent.

## Redis Keys

The stall-recovery counters are plain Redis keys, not Popoto-managed model keys. Raw `r.get`, `r.incr`, `r.delete`, `r.expire` are permitted on these keys.

| Key pattern | Purpose | TTL |
|-------------|---------|-----|
| `{project_key}:stall-recovery:consec:{session_id}` | Cross-tick consecutive stalled observation count | 700s (~2x the 300s cadence) |
| `{project_key}:stall-recovery:budget:{session_id}` | Per-session kill-attempt count | 86400s (24h) |

The `consec` key TTL ensures the count decays if a session stops being reported as stalled (recovered or killed by another path) before the budget threshold is reached.

## Scope Boundary and Related Features

This is the session-level early-detection layer. It runs periodically (every 300 seconds) while the worker reflection scheduler is healthy.

**What it cannot do:** recover a fully-hung worker. The reflection scheduler is in-process. If the worker stops ticking, stall-advisory stops running. Worker-level recovery is the external bridge watchdog's responsibility (companion bridge-labeled issue).

**Relationship to #1724 (never-started and mid-run wedge recovery):** complementary. Issue #1724's path-B stage-1 detector handles the mid-run tool-wedge case (tool in flight, PTY screen quiescent). This feature handles the orthogonal turn-0 case: no tool in flight, no `turn_start` ever fired, heartbeat still fresh. Both reuse the same `last_pty_activity_at` and `last_pty_read_loop_at` liveness fields on `AgentSession`.

**Relationship to the orphan reaper (issue #1271):** the orphan reaper gates on `last_heartbeat_at` being older than 30 minutes. A wedged-but-heartbeating session is invisible to the reaper. Stall-advisory fills this gap.

## Source Files

- `agent/session_stall_classifier.py` -- `granite_wedged` verdict + `GRANITE_WEDGED_*` constants
- `reflections/stall_advisory.py` -- `_maybe_recover` gate ladder, `_ACTIONABLE_STALL_REASONS`, `_emit_recovery_event`
- `agent/granite_container/bridge_adapter.py` -- `_normalize_pty_buffer`, `_on_pty_read` diff-gate
- `config/settings.py` -- `FeatureSettings.stall_recovery_*` fields
