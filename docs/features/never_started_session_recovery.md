# Never-Started Session Recovery

**Issue:** #1724, widened + hang-probe #2069
**Status:** Shipped

## Problem

A session is enqueued, transitions to `running`, but the harness subprocess
never fires its first tool call or turn event. This happens when:
- The `claude -p` subprocess exits immediately with no output
- The subprocess hangs before the first turn is processed

These sessions have `sdk_ever_output=False` — none of `last_tool_use_at`,
`last_turn_at`, or `last_stdout_at` is ever written. Without detection, the
session holds a heartbeat-alive lock indefinitely: the queue-layer
`last_heartbeat_at` keeps sub-check B in `_has_progress` returning True,
preventing recovery.

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
1. `sdk_ever_output=False` — none of `last_tool_use_at`, `last_turn_at`, or
   `last_stdout_at` is set, per the single authoritative derivation
   `agent.session_runner.liveness.derive_sdk_ever_output` (issue #1935; see
   the "Liveness signals" subsection of
   [headless-session-runner.md](headless-session-runner.md)).
   `last_stdout_at` closes the toolless-streaming zombie wedge: a headless
   turn that streams the `init` event and produces assistant output with no
   tool call within the grace window is real, demonstrated output — it must
   not be misclassified as never-started.
2. `running_seconds > NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`

Default threshold: **1200s + 30s = 1230 seconds (~20 min)**.

### Why 20 minutes (widened 2026-07-13, #2069)

The original 150s window killed cold-starting turns before they could start.
An Opus SDLC turn's cold-start-to-first-token runs **15-20 minutes** in the
heavy-context case (large system prompt + MCP-fleet boot, #1227), yet the D0
gate fired at 150s — before `claude -p` even emits its `system/init` event.
Each recovery re-spawned a fresh subprocess (stale-UUID resume → full context
reload → slower), a self-reinforcing thrash that finalized the session
`failed`. The grace is now set to **4x a ~5-min normal cold start** (~20 min),
which also envelopes the documented 15-20 min worst case.

Widening the window means output silence can no longer be a *timely* death
signal inside it — so a separate [short-term hang probe](#short-term-subprocess-hang-probe)
supplies fast, evidence-based hang detection that does not depend on model
output.

**Ordering note (inversion):** the D0 threshold (1230s) is now LARGER than
`STARTUP_GRACE_SECONDS` (300s), inverting the pre-widening ordering. A
no-output session now tiers as: 0-300s → heartbeat fast-path; 300-1230s →
evidence-based reprieve (subprocess liveness, not a bare heartbeat); >1230s →
recover. Genuinely-dead *orphans* (no in-scope handle) are still recovered at
the 300s `#944` guard, so widening the D0 window does not slow dead-worker
recovery.

Two companion guards prevent false kills:

**Sub-check B D0 gate** (`_has_progress`): when `_never_started_past_grace` is
True, a fresh `last_heartbeat_at` no longer returns True from sub-check B.
This prevents the heartbeat from masking the wedge.

**Tier-2 reprieve gate** (`_tier2_reprieve_signal`): a session past the hard
never-started ceiling (`_never_started_past_grace` True) is recovered
regardless of subprocess liveness. *Below* the ceiling, the evidence-based
subprocess-hang probe decides: positive liveness reprieves (superseding the
count-based #1226 cap), a confirmed hang recovers immediately, and an
inconclusive probe falls back to the count cap.

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

`last_pty_read_loop_at` itself was a per-stream-read liveness signal with no
headless equivalent when the cutover shipped (#1843 Gap B) — that gap is what
let a toolless-streaming headless turn go undetected as "producing output"
for its first ~150s, misclassifying it `zombie_uuid_no_output` (issue #1935).
`last_stdout_at`, stamped by `SessionRunner._stamp_stdout_liveness` on every
`init`/stdout event, is the headless replacement; see
[headless-session-runner.md](headless-session-runner.md#liveness) for the
write side.

## Short-Term Subprocess-Hang Probe

**Function:** `agent.session_runner.liveness.subprocess_hang_verdict(pid, session_key)`
**Issue:** #2069

Because the never-started window is ~20 min, a genuine hang must be caught by
direct subprocess evidence rather than by waiting out the window. The probe
reads the harness subprocess tree via `psutil` — **no model output required** —
and classifies each poll:

| Verdict | Evidence | Caller action |
|---------|----------|---------------|
| `progressing` | a live child process (tool/MCP subprocess), advancing CPU time, or an ESTABLISHED outbound HTTPS socket (a model call in flight — CPU is legitimately idle during the first-token wait) | reprieve for the full widened window |
| `hung` | alive but flat CPU **and** no children **and** (sockets readable and) no HTTPS socket, sustained across `HANG_CONFIRM_SAMPLES` consecutive polls; or the pid is gone / zombie | recover now (~60-90s at the 30s poll cadence) |
| `unknown` | no pid, `psutil` unavailable, or sockets unreadable while CPU is flat (cannot disprove a network wait) | fall back to the caller's own bounded reprieve/count logic — **never** a false hang |

Design rule (issue #1172): kill only on *positive* hang evidence, never on the
mere absence of output. The socket check is what prevents a false hang during
the legitimate first-token network wait; when sockets cannot be read the
verdict degrades to `unknown`, not `hung`. A per-session CPU baseline is keyed
by `(session_key, pid)` so a session that recovers and respawns a new
subprocess re-baselines instead of comparing CPU across two unrelated
processes.

**Wiring:**
- `agent/agent_session_queue.py` — Fix #3's 30s owned-task poll loop runs the
  probe every tick (gated on `not derive_sdk_ever_output`) so an in-scope
  cold-start hang is recovered in ~2 polls, independent of the 1800s progress
  deadline.
- `agent/session_health.py::_tier2_reprieve_signal` — the reprieve gate consults
  the probe for orphan / no-in-scope-handle sessions; positive evidence
  supersedes the count-based #1226 escalation guard.

## Env-Tunable Constants

| Constant | Default | Env var | Description |
|----------|---------|---------|-------------|
| `NEVER_STARTED_GRACE_SECS` | 1200 | `NEVER_STARTED_GRACE_SECS` | Base grace window before never-started detection fires (~20 min incl. margin) |
| `NEVER_STARTED_CONFIRM_MARGIN_SECS` | 30 | `NEVER_STARTED_CONFIRM_MARGIN_SECS` | Confirmation margin added on top of grace |
| `HANG_CONFIRM_SAMPLES` | 2 | `HANG_CONFIRM_SAMPLES` | Consecutive flat-CPU polls before a live subprocess is declared hung |

All three constants are marked **provisional/tunable** — the defaults are
safety-chosen starting values. `NEVER_STARTED_*` are defined in
`agent/session_stall_classifier.py` (single source of truth) and imported by
`agent/session_health.py`; `HANG_CONFIRM_SAMPLES` lives with the probe in
`agent/session_runner/liveness.py`. Never redefine them locally.

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
