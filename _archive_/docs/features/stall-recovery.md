# Stall Recovery: Stall-Advisory Action Mode

**Issue:** #1768
**Status:** Shipped

## Overview

The stall-advisory reflection (`reflections/stall_advisory.py`) previously detected wedged sessions and warned to the log. It never acted. Three granite sessions on 2026-06-23 wedged in a turn-0 loop (heartbeating-but-stuck) and saturated the worker thread pool before a human intervened.

This feature promotes stall-advisory from advisory-only to a gated actor: when a session is demonstrably stuck (not just slow), the reflection kills it and re-enqueues its unanswered work via `valor-catchup`. Actuation is unconditional (issue #1855) — the consecutive-observation counter and the run/per-session kill budgets are the safety mechanism, not a dry-run flag.

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

## Probe-Set Exclusion: Non-Executable Ledgers

Before any classification, `run_stall_advisory` excludes `is_ledger=True` sessions
from the probe set (the same `not _is_ledger(s)` filter applied alongside the
terminal-status skip). `sdlc-local-{N}` SDLC pipeline anchors are ledgers that by
design never spawn an SDK subprocess, so they would classify `never_started`
(an actionable reason) and be killed — orphaning the issue lease and deadlocking
the SDLC router (`ISSUE_LOCKED / orphaned_lock`). This mirrors the health loop's
`#2042` ledger skip; see issue #2105 (residual of the #2026 umbrella).

## Action Mode: Gate Ladder

For every `stalled` finding, `_maybe_recover()` in `stall_advisory.py` runs the following checks in order:

1. **Actionable reason filter.** Only the reasons in `_ACTIONABLE_STALL_REASONS` proceed. The set is `{never_started, idle_gap_exceeded_stall}`. A `kill_transition` or other stalled reason is observed and logged but never acted on.

2. **Consecutive-observation counter.** A Redis key `{project_key}:stall-recovery:consec:{session_id}` is incremented atomically. If the count is below `stall_recovery_consecutive_observations`, the function logs "observation N/M" and returns. At the 300-second reflection cadence, 3 observations is approximately 15 minutes.

3. **Per-run budget check.** At most `stall_recovery_run_budget` sessions are killed per reflection tick. When the budget is exhausted, remaining sessions are skipped until the next tick.

4. **Per-session budget check.** A Redis key `{project_key}:stall-recovery:budget:{session_id}` (TTL 24h) caps kill attempts per session at `stall_recovery_per_session_budget`. This prevents thrash if a session keeps re-wedging.

5. **Terminal re-read race guard.** Before killing, the session is re-read from Redis. If it has transitioned to a terminal status since classification, the kill is skipped and the consecutive counter is reset.

6. **Kill and re-enqueue.** `_kill_agent_session(session)` terminates the PID and sets status to `killed`. Then `valor-catchup` is invoked as a subprocess to re-enqueue genuinely-unanswered human messages. Catchup failure is logged and counted but not fatal: the wedged session is stopped regardless.

When a session classifies healthy or suspect in a given tick, its consecutive-observation counter is deleted. A single slow-but-live turn does not accumulate toward a kill.

`suspect` sessions are never acted on. Only `stalled` sessions with an actionable reason reach the gate ladder.

## Thresholds and Break-Glass

| Setting | Default | Env var |
|---------|---------|---------|
| `FEATURES__STALL_RECOVERY_CONSECUTIVE_OBSERVATIONS` | `3` | `FEATURES__STALL_RECOVERY_CONSECUTIVE_OBSERVATIONS` |
| `FEATURES__STALL_RECOVERY_RUN_BUDGET` | `1` | `FEATURES__STALL_RECOVERY_RUN_BUDGET` |
| `FEATURES__STALL_RECOVERY_PER_SESSION_BUDGET` | `2` | `FEATURES__STALL_RECOVERY_PER_SESSION_BUDGET` |

All thresholds are marked provisional/tunable. The defaults are conservative: one kill per tick, 15 minutes of consecutive detection before acting, and a two-kill cap per session lifetime.

### Break-glass: disabling actuation without a deploy

There is no `FEATURES__STALL_RECOVERY_ENABLED` flag (removed by issue #1855 — recovery is unconditional). The remaining no-deploy kill-switch is the run budget: `stall_recovery_run_budget` is relaxed to `ge=0`, and the existing run-budget gate (`run_state["killed"] >= budget`) already short-circuits every candidate to `skipped_run_budget` when the budget is 0, since `killed` starts at 0 and `0 >= 0` is true.

Add to `~/Desktop/Valor/.env`:

```
FEATURES__STALL_RECOVERY_RUN_BUDGET=0
```

Then restart the worker:

```
./scripts/valor-service.sh worker-restart
```

Remove the line (or set it back to a positive value) to restore normal actuation.

## Audit and Telemetry

Every kill or skip decision is logged with the triggering verdict reason at `WARNING` level.

A typed `stall_recovery_action` session-event is appended via `_append_session_event` (same pattern as `granite_user_routed`) for every kill attempt. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `verdict_reason` | str | Classifier reason (`never_started`, `idle_gap_exceeded_stall`) |
| `killed` | bool | Whether the session was actually killed |
| `catchup_invoked` | bool | Whether `valor-catchup` was called |
| `catchup_ok` | bool | Whether `valor-catchup` returned exit code 0 |
| `dry_run` | bool | Always `False` — kept for schema stability of existing dashboard queries over historical (pre-#1855) events |

The reflection summary is extended with recovery counts:

```
3 running session(s): 1 stalled, 0 suspect, 2 healthy; recovery: 1 killed, 0 catchup-failed
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
