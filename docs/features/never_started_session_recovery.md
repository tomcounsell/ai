# Never-Started and Mid-Run Wedge Session Recovery

**Issue:** #1724
**Status:** Shipped

## Problem

Two classes of session wedge can leave a granite session permanently stuck in the `running` state without making progress:

### Priming Wedge (path A)

A session is enqueued, transitions to `running`, but the Claude Code TUI never fires its first tool call or turn event. This happens when:
- The granite container starts but the PTY priming sequence fails silently
- The TUI boots but hangs before the first prompt is processed
- The worker picks up the session but the SDK subprocess exits immediately with no output

These sessions have `sdk_ever_output=False` — neither `last_tool_use_at` nor `last_turn_at` is ever written. Without detection, the session holds a heartbeat-alive lock indefinitely: the queue-layer `last_heartbeat_at` keeps sub-check B in `_has_progress` returning True, preventing recovery.

### Mid-Run Wedge (path B)

A session that has started normally (has SDK output) wedges mid-execution: the PTY screen stops repainting, but the Claude Code process remains alive. The screen freeze can be caused by:
- A tool call that blocks on a hanging subprocess
- The SDK entering an infinite retry loop against a rate-limited API
- A silent crash in an MCP server that leaves the tool call open

Standard tool-timeout detection (issue #1270) catches explicit `current_tool_name` wedges, but misses wedges where the name is not set or the tool completes but the session produces no further output.

## Solution Architecture

### Path A: Never-Started Recovery

The 30-second tool-timeout sub-loop (`_agent_session_tool_timeout_loop` in `agent/session_health.py`) now includes a **D0 block** that fires before the standard tool-timeout check:

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

**Sub-check B D0 gate** (`_has_progress`): when `_never_started_past_grace` is True, a fresh `last_heartbeat_at` no longer returns True from sub-check B. This prevents the heartbeat from masking the wedge.

**Tier-2 reprieve bypass** (`_tier2_reprieve_signal`): when `_never_started_past_grace` is True, all reprieve gates (compaction, children, alive) are suppressed. A session that has never started must not be kept alive by a psutil "alive" signal.

### Path B: Mid-Run Quiescence Detection

Path B is a two-stage detector that monitors PTY screen activity for sessions that have started normally.

**Stage 1 (cheap, stateless gate):** the `_eval_mid_run_pty_stage1` function runs on every 30s tool-timeout tick. It evaluates three PTY liveness markers persisted on `AgentSession`:

| Field | Purpose |
|-------|---------|
| `last_pty_read_loop_at` | Timestamp of the most recent `_cycle_idle` call in the granite container; proves the PTY read loop is alive |
| `last_pty_activity_at` | Timestamp of the most recent non-empty PTY read (screen repaint signal) |
| `mid_run_quiescent_since` | When the screen first became quiescent on the current wedge |
| `mid_run_pty_snapshot` | Snapshot tuple `(last_pty_activity_at_iso, byte_offset)` for cross-tick comparison |

Three-state outcome:
- **ABSTAIN**: `last_pty_read_loop_at` is None or stale (loop may have exited)
- **NOT SUSPECT**: loop is fresh AND screen recently painted → clear `mid_run_quiescent_since`
- **SUSPECT**: loop is fresh AND screen frozen >= `MID_RUN_QUIESCENCE_SECS` → log warning

Path B stage-1 ships as **detect-and-log only**: `_eval_mid_run_pty_stage1` identifies suspects and emits a warning log. No recovery action is taken. Stage-2 (off-loop classifier dispatch, `MID_RUN_RECOVERY_ACTIVE` flag, recovery wiring, counters, and dashboard surface) is planned as future work in a follow-up to #1724.

## PTY Liveness Infrastructure

The PTY read-loop marker and activity marker are written by `BridgeAdapter` in `agent/granite_container/bridge_adapter.py`:

- `_make_pty_read_callback()` returns a callback passed to `Container.run()` as `on_pty_read`
- `Container._cycle_idle()` calls `on_pty_read(data)` on every read-loop iteration
- The callback stamps `last_pty_read_loop_at` on every call and `last_pty_activity_at` only when the read returns non-empty data (screen repaint)

## New AgentSession Fields

Four nullable fields added to `models/agent_session.py`:

| Field | Type | Description |
|-------|------|-------------|
| `last_pty_read_loop_at` | `DatetimeField` (nullable) | Updated each `_cycle_idle` call; proves PTY read-loop is alive |
| `last_pty_activity_at` | `DatetimeField` (nullable) | Updated on non-empty PTY reads; proves screen is repainting |
| `mid_run_quiescent_since` | `DatetimeField` (nullable) | When stage-1 first detected quiescence on the current wedge |
| `mid_run_pty_snapshot` | `Field` (nullable) | Snapshot tuple for cross-tick CAS comparison |

All fields are nullable and backward-compatible: sessions created before this feature simply have `None` for all four fields, and stage-1 abstains when `last_pty_read_loop_at` is None.

## Env-Tunable Constants

| Constant | Default | Env var | Description |
|----------|---------|---------|-------------|
| `NEVER_STARTED_GRACE_SECS` | 120 | `NEVER_STARTED_GRACE_SECS` | Base grace window before never-started detection fires |
| `NEVER_STARTED_CONFIRM_MARGIN_SECS` | 30 | `NEVER_STARTED_CONFIRM_MARGIN_SECS` | Confirmation margin added on top of grace |
| `MID_RUN_QUIESCENCE_SECS` | 180 | `MID_RUN_QUIESCENCE_SECS` | PTY screen silence duration before stage-1 flags a suspect |

All constants are marked **provisional/tunable** — the defaults are safety-chosen starting values.

`NEVER_STARTED_GRACE_SECS` and `NEVER_STARTED_CONFIRM_MARGIN_SECS` are defined in `agent/session_stall_classifier.py` (single source of truth) and imported by `agent/session_health.py`. Never redefine them locally.

## Stage-2 (Planned — Not Yet Shipped)

Path B stage-2 is deferred to a follow-up issue. When built, it will add:
- Off-loop `asyncio` task dispatch for the granite classifier on stage-1 suspects
- `MID_RUN_RECOVERY_ACTIVE` env flag (default off) to gate recovery behind observe-only logging
- `mid_run_pty_quiescent_recovery_observed` Redis counter for would-be kills while observe-only
- Dashboard fallback-rate alert for classifier unavailability
- CAS re-check precondition on the post-classifier recovery write

Until then, a session flagged by stage-1 receives only a warning log entry.

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
2. Path B stage-1 ships detect-and-log only; no recovery action is taken; stage-2 recovery is deferred to a follow-up
3. `_never_started_past_grace` NEVER raises — all exceptions swallowed, returns False on error
4. `_eval_mid_run_pty_stage1` NEVER raises — all exceptions swallowed
5. Stage-2 off-loop classifier dispatch (deferred) — when built, the classifier task slot must be removed in a `finally` block (prevents permanent slot leaks)
