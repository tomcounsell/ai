# Stall Recovery: Stall-Advisory Action Mode

**Issue:** #1768
**Status:** Shipped

## Overview

The stall-advisory reflection (`reflections/stall_advisory.py`) previously detected wedged sessions and warned to the log. It never acted. Three granite sessions on 2026-06-23 wedged in a turn-0 loop (heartbeating-but-stuck) and saturated the worker thread pool before a human intervened.

This feature promotes stall-advisory from advisory-only to a gated actor: when a session is demonstrably stuck (not just slow), the reflection kills it and re-enqueues its unanswered work via `valor-catchup`. All action is dry-run by default.

## Actionable Stall Reasons

`classify_session_stall()` in `agent/session_stall_classifier.py` currently
fires two actionable reasons that reach the gate ladder below:
`never_started` and `idle_gap_exceeded_stall`. Both are protocol-derived (no
screen to watch): they read telemetry events and heartbeat freshness, never
PTY-specific signals.

**Superseded (issue #1924):** this feature originally shipped a third
verdict, `granite_wedged`, that detected a granite PTY session wedged in a
turn-0 loop by watching two PTY-specific fields (`last_pty_read_loop_at`
staying fresh while `last_pty_activity_at` went stale) plus a normalization
fix for the diff-gate that made the signal reliable against an animating
spinner. `granite_wedged`, its two constants
(`GRANITE_WEDGED_PTY_STALE_SECS`, `GRANITE_WEDGED_READLOOP_FRESH_SECS`), and
the PTY-field diff-gate normalization it depended on were all deleted with
the granite PTY substrate — a `claude -p` turn has no persistent screen to
stall in a turn-0 loop on. See [Headless Session
Runner](headless-session-runner.md#liveness) for the current liveness model.

## Action Mode: Gate Ladder

For every `stalled` finding, `_maybe_recover()` in `stall_advisory.py` runs the following checks in order:

1. **Actionable reason filter.** Only the reasons in `_ACTIONABLE_STALL_REASONS` proceed. The set is `{never_started, idle_gap_exceeded_stall}`. A `kill_transition` or other stalled reason is observed and logged but never acted on.

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
[stall-recovery] WOULD kill+recover session=<id> reason=never_started (dry-run)
```

It also emits a `stall_recovery_action` session-event with `dry_run=True` (visible on the dashboard feed and queryable). No session is killed, no counter is written to the kill-budget key, and `valor-catchup` is not invoked.

## Audit and Telemetry

Every kill or skip decision is logged with the triggering verdict reason at `WARNING` level.

A typed `stall_recovery_action` session-event is appended via `_append_session_event` (same pattern as `granite_user_routed`) for every dry-run and every kill attempt. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `verdict_reason` | str | Classifier reason (`never_started`, `idle_gap_exceeded_stall`) |
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

**Relationship to #1724 (never-started session recovery):** complementary. [Never-Started Session Recovery](never_started_session_recovery.md) handles the D0 never-started case (`sdk_ever_output=False` past a grace window) inside the tool-timeout sub-loop itself; this feature handles the broader `stalled`-verdict classification and the kill+re-enqueue action on top of it. The mid-run PTY-quiescence detector #1724 used to also carry (Path B) was deleted with the granite PTY substrate — see that doc's Superseded section.

**Relationship to the orphan reaper (issue #1271):** the orphan reaper gates on `last_heartbeat_at` being older than 30 minutes. A wedged-but-heartbeating session is invisible to the reaper. Stall-advisory fills this gap.

## Source Files

- `agent/session_stall_classifier.py` -- `classify_session_stall()`, `never_started` / `idle_gap_exceeded_stall` verdicts
- `reflections/stall_advisory.py` -- `_maybe_recover` gate ladder, `_ACTIONABLE_STALL_REASONS`, `_emit_recovery_event`
- `config/settings.py` -- `FeatureSettings.stall_recovery_*` fields
