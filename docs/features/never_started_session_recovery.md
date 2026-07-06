# Never-Started Session Recovery

**Issue:** #1724
**Status:** Shipped

## Problem

A session is enqueued, transitions to `running`, but the harness subprocess
never fires its first tool call or turn event. This happens when:
- The `claude -p` subprocess exits immediately with no output
- The subprocess hangs before the first turn is processed

These sessions have `sdk_ever_output=False` — neither `last_tool_use_at` nor
`last_turn_at` is ever written. Without detection, the session holds a
heartbeat-alive lock indefinitely: the queue-layer `last_heartbeat_at` keeps
sub-check B in `_has_progress` returning True, preventing recovery.

## Solution: D0 Never-Started Gate

The 30-second tool-timeout sub-loop (`_agent_session_tool_timeout_loop` in
`agent/session_health.py`) includes a **D0 block** that fires before the
standard tool-timeout check:

```
D0: _never_started_past_grace(entry) → True
    → re-read fresh (CAS race mitigation)
    → confirm predicate on fresh
    → incr {project_key}:session-health:tier1_falloff:never_started_grace_exceeded
    → _apply_recovery_transition(reason_kind="no_progress")
```

The predicate `_never_started_past_grace` returns True when:
1. `sdk_ever_output=False` (neither `last_tool_use_at` nor `last_turn_at` is set)
2. `running_seconds > NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`

Default threshold: 120s + 30s = 150 seconds.

Two companion guards prevent false kills:

**Sub-check B D0 gate** (`_has_progress`): when `_never_started_past_grace` is
True, a fresh `last_heartbeat_at` no longer returns True from sub-check B.
This prevents the heartbeat from masking the wedge.

**Tier-2 reprieve bypass** (`_tier2_reprieve_signal`): when
`_never_started_past_grace` is True, all reprieve gates (compaction,
children, alive) are suppressed. A session that has never started must not
be kept alive by a psutil "alive" signal.

## Superseded: the PTY-liveness deferral and mid-run quiescence detector

Two mechanisms this feature originally shipped were deleted with the granite
PTY substrate (issue #1924), since they existed to distinguish "screen still
painting" from "screen frozen" — a distinction that has no meaning once a
turn is a single short-lived `claude -p` subprocess rather than a long-lived
interactive TUI:

- **`_prime_pty_alive` PTY-liveness gate (issue #1792):** used to defer the D0
  never-started kill while the granite PTY read loop was still fresh. The D0
  gate is now a flat age-only kill for every session — there is no priming
  phase to distinguish from a genuine hang.
- **Path B mid-run quiescence detector** (`_eval_mid_run_pty_stage1`,
  `mid_run_quiescent_since`, `mid_run_pty_snapshot`, `last_pty_read_loop_at`,
  `last_pty_activity_at`): a two-stage detector that watched PTY screen
  repaint activity to catch a session wedged mid-execution. The [headless
  session runner](headless-session-runner.md) replaces this with a
  role-aware **per-turn timeout** — a turn that exceeds its ceiling is a
  graceful preempt (`turn_end_source="timeout"`), not a silent wedge, so
  there is no equivalent "detect-and-log" stage to build on.

The four PTY-liveness `AgentSession` fields these mechanisms used were
removed in the same cutover.

## Env-Tunable Constants

| Constant | Default | Env var | Description |
|----------|---------|---------|-------------|
| `NEVER_STARTED_GRACE_SECS` | 120 | `NEVER_STARTED_GRACE_SECS` | Base grace window before never-started detection fires |
| `NEVER_STARTED_CONFIRM_MARGIN_SECS` | 30 | `NEVER_STARTED_CONFIRM_MARGIN_SECS` | Confirmation margin added on top of grace |

Both constants are marked **provisional/tunable** — the defaults are
safety-chosen starting values, defined in `agent/session_stall_classifier.py`
(single source of truth) and imported by `agent/session_health.py`. Never
redefine them locally.

## Telemetry Counters

| Redis key | When incremented |
|-----------|-----------------|
| `{project_key}:session-health:tier1_falloff:never_started_grace_exceeded` | D0 block fires on a session past grace |

## Import Direction

The import direction is strictly one-way:

```
agent/session_health.py → agent/session_stall_classifier.py
```

`session_stall_classifier.py` must NEVER import from `session_health.py`.

## Safety Invariants

1. Recovery reason string must contain "no progress signal" so `reason_kind` resolves to `"no_progress"` in `_apply_recovery_transition`
2. `_never_started_past_grace` NEVER raises — all exceptions swallowed, returns False on error
