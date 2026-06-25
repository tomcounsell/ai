---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1792
last_comment_id:
---

# session-health: gate the never_started kill on PTY liveness (sibling of #1784)

## Problem

Granite SDLC sessions (`session_type=eng` running inside the granite PTY container) are
**killed during priming** because they don't write their first transcript entry before the
never-started grace window expires. The inner `claude` TUI is still booting the PM/Dev pair
(container spin-up + TUI boot + prime-turn relay), so neither `last_tool_use_at` nor
`last_turn_at` is set yet — `sdk_ever_output` is False — and `_never_started_past_grace()`
fires at 150s (`NEVER_STARTED_GRACE_SECS` 120 + `NEVER_STARTED_CONFIRM_MARGIN_SECS` 30).

**Current behavior:**
The psyoptimal `sdlc-543` session was launched three times on 2026-06-25 and died every
time during prime, producing no work. Two kill classifications, **one root cause**:

- `kind=tool_timeout` (`tool-wedge: Skill (default tier) older than 300s`) — already gated
  on PTY liveness by #1784 (PR #1789), so this half is largely covered; this plan must not
  regress it.
- `kind=no_progress` / `never_started past grace` — the granite pair spawned cleanly
  (`[pty-pool] slot 0: spawned per-session pair … pm_model=opus`) but the inner session
  never wrote a first transcript entry (`prime-turn: transcript read: no-new-entry`
  repeated). It ran 322s, recovered to pending, ran 170s more, then finalized `failed`
  ("2 recovery attempts, never progressed"). The never-started kill path at
  `agent/session_health.py:3111` (D0 branch, #1724) has **no PTY-liveness gate** — unlike
  the tool_timeout path 60 lines below it.

A *different* granite session in the same project/worker (`add-newsletter-01`) ran ~700s and
completed. Granite PTY is not broken — the SDLC prime is simply slow enough to trip the
never-started killer before its first transcript entry lands.

**Desired outcome:**
A granite session whose PTY process is alive and **advancing at the OS level** — the
normalized PTY buffer is still changing (genuine repaints, not just spinner frames) — must
NOT be recovered/failed by the `no_progress`/`never_started` path during priming. Liveness
during prime is judged on PTY bytes flowing, not solely on transcript/tool-output cadence.
Genuinely dead sessions (PTY exited, no repaints) are still failed within a bounded time.

## Freshness Check

**Baseline commit:** `7fb7e609f323ff0e6fc22ff435735f65b575e347`
**Issue filed at:** 2026-06-25T08:51:49Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:770` `_never_started_past_grace()` — drifted: the predicate now
  lives at `:874`; the **kill call-site** (D0 branch) is at `:3111`. Claim still holds — this
  call-site applies no PTY-liveness gate.
- `agent/session_health.py:360` `_check_tool_timeout()` — drifted to `:362`. The tool_timeout
  PTY-liveness gate (`_pty_quiescent_long_enough`) is applied at `:3168`. Holds.
- `agent/session_health.py:785` ("padded to cover worst-case granite cold-start") — the
  padding is `NEVER_STARTED_CONFIRM_MARGIN_SECS` (`agent/session_stall_classifier.py:60`,
  env-overridable, default 30). Claim holds.
- Issue cites plan `docs/plans/tool_timeout_pty_liveness_gate.md` — **does not exist**. The
  #1784 work shipped via PR #1789 / commit `2408c9d2` ("feat(session-health): gate
  default-tier tool_timeout kill on PTY liveness"). Corrected pointer.

**Cited sibling issues/PRs re-checked:**
- #1784 — CLOSED 2026-06-25T07:46:05Z. Resolution: gated the default-tier tool_timeout kill
  on `_pty_quiescent_long_enough()`, which keys off `mid_run_quiescent_since` (a MID-RUN
  signal, None during prime). This plan is the never-started sibling.
- #1724 — added the D0 never-started recovery branch (`:3111`) and the `last_pty_activity_at`
  / `last_pty_read_loop_at` durable fields. The branch was added without the PTY-liveness gate
  that #1784 later introduced on the adjacent path.

**Commits on main since issue was filed (touching `agent/session_health.py`):**
- `2408c9d2` feat(session-health): gate default-tier tool_timeout kill on PTY liveness —
  **partially addresses** (the tool_timeout half); the never-started half is untouched.

**Active plans in `docs/plans/` overlapping this area:** None directly. Adjacent:
`per_tool_timeout_tier_counters.md`, `granite_pty_production_cutover.md` (shipped). No overlap
on the never-started path.

**Notes:** The reusable liveness primitive is `last_pty_activity_at` (diff-gated by
`bridge_adapter._on_pty_read`, `:771`), NOT `_pty_quiescent_long_enough` (which keys off
`mid_run_quiescent_since`, unset during prime). See Technical Approach.

## Prior Art

- **Issue/PR #1784 / #1789**: "gate default-tier tool_timeout kill on PTY liveness" — gated the
  tool-wedge kill on `_pty_quiescent_long_enough()`. Direct sibling: same fix shape (suppress a
  health kill when the PTY is demonstrably alive), different kill path. The helper it added keys
  off `mid_run_quiescent_since`, a mid-run signal that is None during prime, so it cannot be
  reused verbatim — but its branch structure (kill-switch escape → non-PTY/SDK escape →
  staleness escape → alive-defer) is the template for the new prime-liveness helper.
- **Issue/PR #1724 / #1728**: "recover stalled never_started and mid-run-wedge granite sessions"
  — added the D0 never-started recovery branch (`:3111`) and the `last_pty_activity_at` /
  `last_pty_read_loop_at` fields. This plan adds the missing gate to the branch #1724 created.
- **Issue #1768** (PTY buffer normalization): `_normalize_pty_buffer` strips spinner glyphs,
  the elapsed-seconds counter, and cursor/blink noise before the diff that gates
  `last_pty_activity_at`. This is load-bearing — it means a fresh `last_pty_activity_at` proves
  *content* advanced, not merely that an animation ticked. Without #1768 a wedged-but-animating
  TUI would falsely look alive; with it, the signal is trustworthy as a prime-liveness gate.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1724 (D0 branch) | Added the never-started recovery branch at `:3111` with a stacked grace (`NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` = 150s). | The padding is a *fixed time budget*, not a liveness check. A prime slower than 150s is killed even though its PTY is demonstrably alive. Time-budget padding can never distinguish "slow but alive" from "dead" — only a liveness signal can. |
| #1784 (tool_timeout gate) | Gated the *tool_timeout* kill on `_pty_quiescent_long_enough`. | Fixed only the half where `current_tool_name` is set. The never-started path (no tool in flight) was left un-gated, so the same prime fails under `no_progress` whenever the tool field happens to be null. |

**Root cause pattern:** Two adjacent kill paths share the same "is this granite session
actually dead?" question, but only one consults the PTY. The never-started path relies on a
fixed time budget that cannot be tuned large enough to cover a slow prime without also
blinding the monitor to genuinely dead sessions. The fix must give the never-started path the
same liveness consultation the tool_timeout path already has — using a primitive that exists
during prime (`last_pty_activity_at`), since the tool_timeout path's primitive
(`mid_run_quiescent_since`) does not.

## Architectural Impact

- **New dependencies:** None. Reuses existing fields (`last_pty_activity_at`,
  `last_pty_read_loop_at`) and an existing constant (`HEARTBEAT_FRESHNESS_WINDOW`).
- **Interface changes:** One new pure helper in `agent/session_health.py` (e.g.
  `_prime_pty_alive(entry, now) -> bool`). No signature changes to public functions.
- **Coupling:** Unchanged. The health monitor already reads these PTY fields elsewhere
  (`_eval_mid_run_pty_stage1`, `:2976`); this adds one more reader.
- **Data ownership:** Unchanged. `last_pty_activity_at` is owned by `bridge_adapter._on_pty_read`.
- **Reversibility:** Trivially reversible — the gate is a single guarded `continue`/branch and
  carries a kill-switch (env constant `<= 0` restores age-only never-started kill).

## Appetite

**Size:** Small

**Team:** Solo dev (builder + validator pair), plus a one-shot measurement agent.

**Interactions:**
- PM check-ins: 1 (review the measurement findings before the threshold is fixed)
- Review rounds: 1

The change is small and surgical (one helper + one gate at one call-site + tests). The bottleneck
is the measurement step that justifies the threshold, and the war-room critique.

## Prerequisites

No prerequisites — this work has no external dependencies. The measurement step (Step 1) reads
existing production logs / telemetry counters; no new services or secrets.

## Solution

### Key Elements

- **Measurement (gate the design)**: Measure first-transcript-entry latency for granite SDLC
  PM/Dev prime across recent sessions, and confirm `last_pty_activity_at` is being stamped
  during that window. This decides whether a liveness gate alone suffices or the grace also
  needs a modest bump, and justifies whatever freshness threshold the gate uses.
- **Prime-liveness helper**: A pure predicate `_prime_pty_alive(entry, now)` returning True
  when the granite PTY is alive-and-advancing during prime — i.e. `last_pty_activity_at` is
  fresher than a liveness window — and False (kill-eligible) for SDK/non-granite sessions or
  when the PTY has gone quiet/stale. Mirrors `_pty_quiescent_long_enough`'s branch discipline
  (kill-switch first, then non-PTY escape, then staleness escape, then the alive case).
- **Gate at the never-started call-site**: Before `_apply_recovery_transition(...,
  reason_kind="no_progress")` at `:3111`, defer the kill when `_prime_pty_alive()` is True,
  incrementing a `…:never_started_pty_deferred` observability counter (mirroring the existing
  `tool_timeouts:default_deferred` counter at `:3182`).
- **Regression test**: An alive-but-slow-priming granite session (fresh `last_pty_activity_at`,
  `sdk_ever_output=False`, `running_seconds > 150`) is NOT recovered; a genuinely dead one
  (stale/absent `last_pty_activity_at`) IS recovered.

### Flow

Health tick (`_agent_session_health_check`, 30s loop) → running granite session in prime,
`sdk_ever_output=False`, running 150s+ → `_never_started_past_grace()` True → **new gate**:
`_prime_pty_alive()` → True (PTY repainting) ⇒ `continue` (defer, INCR deferred counter) /
False (PTY quiet or non-granite) ⇒ existing `_apply_recovery_transition` recovery.

### Technical Approach

- **Liveness primitive — reuse `last_pty_activity_at`, not `_pty_quiescent_long_enough`.**
  The tool_timeout gate keys off `mid_run_quiescent_since`, which is only set by stage-1
  (`_eval_mid_run_pty_stage1`) for sessions that have produced output and have a tool in flight
  — it is None during prime. The signal that *is* live during prime is `last_pty_activity_at`
  (`models/agent_session.py:392`), stamped by `bridge_adapter._on_pty_read` (`:771`) only when
  the **normalized** PTY buffer changes (genuine repaint; #1768 normalization strips spinner/
  elapsed-counter frames so an animating-but-wedged TUI does NOT keep it fresh). This is exactly
  AC1's "transcript file growing / pty bytes flowing" signal, available before any transcript
  entry.
- **Helper branch order (load-bearing, mirrors `_pty_quiescent_long_enough` `:425`):**
  1. Kill-switch escape FIRST: an env constant (`NEVER_STARTED_PTY_LIVENESS_SECS <= 0`) restores
     age-only never-started kill for every session. Must be first so it is never silently
     defeated by a None-field short-circuit.
  2. Non-PTY/SDK escape: `last_pty_read_loop_at is None` ⇒ no granite PTY read loop ⇒ return
     False (kill-eligible) so the SDK path keeps its 150s never-started kill. (Omitting this
     would let SDK sessions with a null activity field fall through and never be killed.)
  3. Staleness escape: `last_pty_read_loop_at` older than `HEARTBEAT_FRESHNESS_WINDOW` (90s) ⇒
     the read loop itself has died ⇒ return False (kill-eligible). A dead read loop cannot stamp
     `last_pty_activity_at`, so its staleness must not be mistaken for "quiet but alive."
  4. Alive case: return `last_pty_activity_at` is fresher than the liveness window. The window
     should be `HEARTBEAT_FRESHNESS_WINDOW` (90s) unless Step 1's measurement shows prime
     repaint gaps legitimately exceed it (then justify a larger constant). This is the
     "alive, defer the kill" case.
- **Gate placement:** Insert the gate inside the D0 branch at `:3111`, after the fresh re-read
  / re-confirm of `_never_started_past_grace(fresh_ns, now)` and BEFORE
  `_apply_recovery_transition`. Re-evaluate `_prime_pty_alive(fresh_ns, now)` on the fresh row
  (race-mitigation, matching the tool_timeout path's fresh-row re-check at `:3222`).
- **Two-classification reconciliation (AC2):** The tool_timeout path already gates on PTY
  liveness via `_pty_quiescent_long_enough` (#1784). This plan does NOT touch that path; it adds
  the parallel gate to the never-started path so the same slow prime cannot fail under whichever
  label applies. Verify (Step 1 / regression test) that during prime with `current_tool_name`
  set, the tool_timeout gate's branch-2 (`last_pty_read_loop_at is None`) is NOT taken (granite
  sessions have the read loop), so the existing gate already protects the tool_timeout half —
  no change needed there beyond a non-regression assertion.
- **Constants:** Add `NEVER_STARTED_PTY_LIVENESS_SECS` (env-overridable; default
  `HEARTBEAT_FRESHNESS_WINDOW` or the measured value) in `agent/session_stall_classifier.py`
  next to the existing never-started constants, imported into `session_health.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new helper `_prime_pty_alive` must NEVER raise (mirrors `_never_started_past_grace`'s
      "this predicate NEVER raises" contract). Wrap field reads defensively; on any unexpected
      exception return False (kill-eligible — fail toward the existing safe behavior). Add a test
      that passes a malformed entry (non-datetime fields) and asserts the helper returns False
      without raising.
- [ ] The deferred-counter INCR is best-effort (`try/except` with `logger.debug`), matching the
      `tool_timeouts:default_deferred` precedent at `:3185`. Test asserts a counter-INCR failure
      does not block the defer.

### Empty/Invalid Input Handling
- [ ] `last_pty_activity_at = None` (never stamped) ⇒ helper returns False (kill-eligible). Test
      this explicitly — a granite session that spawned but whose PTY never repainted is genuinely
      dead and must still be recovered.
- [ ] `last_pty_read_loop_at = None` (SDK / non-granite) ⇒ helper returns False. Test.
- [ ] Whitespace-only / spinner-only repaints do not stamp `last_pty_activity_at` (already
      enforced by #1768 normalization at the stamp site) — no test needed in this plan; covered by
      `tests/unit/test_bridge_adapter_pty_normalize.py`.

### Error State Rendering
- No user-visible output. The observable failure surface is the recovery transition + the
  Redis observability counters (`…:tier1_falloff:never_started_grace_exceeded` for kills,
  new `…:never_started_pty_deferred` for defers). Tests assert the correct counter moves on
  each branch.

## Test Impact

- [ ] `tests/unit/test_never_started_recovery.py::TestNeverStartedPastGrace` — UPDATE: these
      tests assert the *predicate* `_never_started_past_grace` only; they remain valid (the
      predicate is unchanged). No edit needed unless the helper changes the predicate (it does
      not). Confirm green.
- [ ] `tests/unit/test_never_started_recovery.py` — UPDATE/EXTEND: add a `TestPrimePtyAlive`
      class for the new helper (alive / non-PTY / stale-loop / quiet / kill-switch / malformed
      cases) and a recovery-path test asserting the D0 branch defers when `_prime_pty_alive` is
      True and recovers when False. The fixture already accepts `last_pty_activity_at`
      (`:19`), so no fixture change is needed.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — UPDATE: add a non-regression assertion
      that a priming granite session with `current_tool_name` set and a fresh PTY is NOT killed
      by the tool_timeout path (AC2 — guards against the two paths diverging).
- [ ] `tests/unit/test_session_stall_classifier.py` — UPDATE: assert the new
      `NEVER_STARTED_PTY_LIVENESS_SECS` constant exists, has the expected default, and is
      env-overridable (mirrors the existing `NEVER_STARTED_CONFIRM_MARGIN_SECS` test pattern).

## Rabbit Holes

- **Re-architecting the prime path to emit a transcript entry sooner.** Tempting (it would make
  the prime "start" faster) but it's a granite-container redesign, far larger than this bug, and
  doesn't fix the structural "kill alive sessions" defect. Out of scope.
- **Unifying the never-started and tool_timeout gates into one helper.** They consult different
  signals (`last_pty_activity_at` for prime vs. `mid_run_quiescent_since` for mid-run) for
  different lifecycle phases. Forcing them into one predicate would couple two concerns; keep two
  small helpers with parallel branch structure.
- **Tuning `NEVER_STARTED_GRACE_SECS` higher as the "real" fix.** A blanket grace bump blinds the
  monitor to genuinely dead sessions and is explicitly a No-Go. The grace stays; the gate is the
  fix. A *small* margin bump is acceptable only if Step 1 shows the read loop itself takes >90s
  to first stamp `last_pty_activity_at` — and only with the measurement to justify it.
- **Building new PTY instrumentation.** `last_pty_activity_at` / `last_pty_read_loop_at` already
  exist and are stamped during prime; Step 1 only needs to *read* them and existing logs, not add
  new stamps.

## Risks

### Risk 1: A genuinely-dead prime that nonetheless repaints once after the loop dies
**Impact:** A stale `last_pty_activity_at` close to the window edge could defer a dead session
for one extra tick.
**Mitigation:** The staleness escape (branch 3) keys the gate off `last_pty_read_loop_at`
freshness — if the read loop is stale (>90s), the gate returns kill-eligible regardless of
`last_pty_activity_at`. A dead read loop cannot keep either field fresh, so dead sessions are
still recovered within `HEARTBEAT_FRESHNESS_WINDOW` of the loop dying. AC5 covered.

### Risk 2: SDK / non-granite sessions accidentally protected
**Impact:** Would blind the never-started kill for the entire SDK path (regression).
**Mitigation:** Branch 2 (`last_pty_read_loop_at is None`) returns kill-eligible for any session
without a granite PTY read loop — exactly mirroring `_pty_quiescent_long_enough` branch 2. A
unit test asserts an SDK session (no read-loop field) is still recovered past grace.

### Risk 3: Measurement shows 90s is too tight (legitimate prime repaint gaps >90s)
**Impact:** A still-alive prime with a >90s repaint gap would be killed even with the gate.
**Mitigation:** Step 1 measures actual repaint cadence during prime. If gaps legitimately exceed
90s, set `NEVER_STARTED_PTY_LIVENESS_SECS` to the measured p99 + margin (env-overridable) and
record the justification in the plan + commit message. The kill-switch (`<= 0`) is the escape
hatch if the gate misbehaves in production.

## Race Conditions

### Race 1: PTY repaints between the iterator read and the recovery transition
**Location:** `agent/session_health.py:3111`–`:3147` (D0 branch).
**Trigger:** The health iterator reads `entry` (PTY quiet), then `_on_pty_read` stamps a fresh
`last_pty_activity_at`, then the monitor proceeds to recover — killing a session that just came
alive.
**Data prerequisite:** `last_pty_activity_at` must reflect the latest repaint before the gate
decides.
**State prerequisite:** The session must not be in a terminal status.
**Mitigation:** Re-read the row fresh (`fresh_ns = AgentSession.get_by_id(...)`, already present
at `:3113`), re-confirm `_never_started_past_grace(fresh_ns, now)` (already present at `:3124`),
AND re-evaluate `_prime_pty_alive(fresh_ns, now)` on the same fresh row before transitioning —
identical to the tool_timeout path's fresh-row liveness re-check at `:3222`.

### Race 2: Concurrent saves to the PTY fields from the tailer task
**Location:** `bridge_adapter._on_pty_read` (`:776`) saves with `update_fields=[...]`.
**Trigger:** The read-loop closure and the transcript tailer both `save()` the same row.
**Data prerequisite:** Neither write should clobber the other's fields.
**Mitigation:** Existing — both writers already use `update_fields=` to scope their writes
(`:776`). The health monitor only reads these fields; it never writes them. No new write contention.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1784] Changes to the tool_timeout PTY-liveness gate
  (`_pty_quiescent_long_enough`) — that path already shipped and is covered by #1784. This plan
  only adds a non-regression assertion against it, not a modification.
- Nothing else deferred — the measurement, the helper, the gate, and the regression tests are all
  in scope for this plan. No blanket grace increase (a forbidden code-level outcome — see the
  Verification anti-criterion below).

## Update System

No update system changes required — this feature is purely internal to the session-health
monitor (`agent/session_health.py` + `agent/session_stall_classifier.py`). The new env constant
`NEVER_STARTED_PTY_LIVENESS_SECS` has a safe in-code default and needs no `.env` entry unless an
operator wants to override it; if so it follows the existing env-override pattern of the
neighboring constants (no sync step needed). **Service restart:** because this changes worker /
session-health code, after merge run `./scripts/valor-service.sh worker-restart` on bridge
machines (not this skills-only machine).

## Agent Integration

No agent integration required — this is a worker/session-health-internal change. The agent
reaches no new surface; there is no new CLI entry point and the bridge does not import new code.
The behavior is observable only via the recovery transitions and Redis observability counters,
which existing tooling (dashboard, `valor-session telemetry`) already surfaces.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-session-liveness.md` (referenced from `session_health.py:11`) to
      document the never-started PTY-liveness gate alongside the existing tool_timeout gate —
      the two parallel gates and which PTY signal each consults
      (`last_pty_activity_at` vs `mid_run_quiescent_since`).
- [ ] If a granite-specific liveness doc exists (`docs/features/granite-pty-production.md`), add
      a cross-reference noting that prime liveness is judged on `last_pty_activity_at` freshness.
- [ ] No new `docs/features/README.md` index entry needed (this extends an existing feature).

### Inline Documentation
- [ ] Docstring on `_prime_pty_alive` documenting the load-bearing branch order (matching the
      `_pty_quiescent_long_enough` docstring style) and the "NEVER raises" contract.
- [ ] Comment at the `:3111` gate explaining why it consults `last_pty_activity_at` and not the
      tool_timeout helper.

## Success Criteria

- [ ] A granite session whose PTY is alive and advancing (fresh `last_pty_read_loop_at` +
      fresh `last_pty_activity_at`, `sdk_ever_output=False`, running > 150s) is NOT recovered by
      the never-started path — proven by a regression test.
- [ ] The same protection covers the `tool_timeout` path during prime (non-regression assertion
      in `test_session_health_tool_timeout.py`), so a slow prime cannot fail under either label.
- [ ] First-transcript-entry (and first-`last_pty_activity_at`-stamp) latency for granite SDLC
      prime is measured and recorded in this plan's Spike/measurement notes, with the chosen
      liveness window justified against it.
- [ ] A genuinely dead session (stale/absent PTY signals) IS still recovered within
      `HEARTBEAT_FRESHNESS_WINDOW` of the read loop dying — proven by a test.
- [ ] SDK / non-granite never-started sessions are unaffected (still recovered past grace) —
      proven by a test.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms the new gate at the `:3111` call-site references `_prime_pty_alive`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly.

### Team Members

- **Measurement (prime latency)**
  - Name: prime-measurer
  - Role: Measure granite SDLC prime first-output and first-PTY-activity latency from logs /
    telemetry; recommend the liveness window.
  - Agent Type: debugging-specialist
  - Resume: true

- **Builder (session-health gate)**
  - Name: gate-builder
  - Role: Implement `_prime_pty_alive`, the constant, and the gate at `:3111`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (regression tests)**
  - Name: gate-tester
  - Role: Add the alive/dead/SDK/kill-switch tests + the tool_timeout non-regression assertion.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (session-health gate)**
  - Name: gate-validator
  - Role: Verify all success criteria + Verification rows pass.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: gate-documentarian
  - Role: Update `docs/features/pm-session-liveness.md` and cross-refs.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(See repo defaults — debugging-specialist, builder, test-engineer, validator, documentarian.)

## Step by Step Tasks

### 1. Measure granite SDLC prime latency (gates the threshold)
- **Task ID**: measure-prime-latency
- **Depends On**: none
- **Validates**: produces a written latency finding appended to this plan's "Spike Results"
- **Assigned To**: prime-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: false
- Read recent granite SDLC sessions' logs and the `last_pty_activity_at` / `last_pty_read_loop_at`
  / `started_at` / first `last_turn_at` timestamps (via `valor-session telemetry` / dashboard /
  Redis counters `tier1_falloff:never_started_grace_exceeded`).
- Measure: (a) time from `started_at` to first `last_pty_activity_at` stamp; (b) repaint gap
  distribution during prime; (c) time from `started_at` to first transcript entry / first
  `last_turn_at`.
- Recommend `NEVER_STARTED_PTY_LIVENESS_SECS`: default to `HEARTBEAT_FRESHNESS_WINDOW` (90s)
  unless repaint gaps p99 exceed it; if so recommend p99 + margin with the data.
- Report whether a small grace bump is also needed (only if first `last_pty_activity_at` stamp
  itself routinely exceeds 90s after `started_at`).

### 2. Implement the prime-liveness gate
- **Task ID**: build-gate
- **Depends On**: measure-prime-latency
- **Validates**: tests/unit/test_never_started_recovery.py, tests/unit/test_session_stall_classifier.py
- **Informed By**: measure-prime-latency (sets the liveness window)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `NEVER_STARTED_PTY_LIVENESS_SECS` to `agent/session_stall_classifier.py` (env-overridable),
  import into `session_health.py`.
- Add pure helper `_prime_pty_alive(entry, now) -> bool` with the four-branch order (kill-switch
  → non-PTY escape → staleness escape → fresh-activity), NEVER raises.
- Gate the D0 never-started call-site (`:3111`): after the fresh re-read + re-confirm and before
  `_apply_recovery_transition`, `if _prime_pty_alive(fresh_ns, now): INCR
  …:never_started_pty_deferred; continue`.

### 3. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-gate
- **Validates**: tests/unit/test_never_started_recovery.py, tests/unit/test_session_health_tool_timeout.py, tests/unit/test_session_stall_classifier.py
- **Assigned To**: gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- `TestPrimePtyAlive`: alive (fresh activity ⇒ True), non-PTY (`last_pty_read_loop_at=None` ⇒
  False), stale read loop (⇒ False), quiet/None activity (⇒ False), kill-switch (`<=0` ⇒ False),
  malformed fields (⇒ False, no raise).
- Recovery-path test: D0 branch defers (INCR deferred counter, no transition) when alive;
  recovers when dead.
- tool_timeout non-regression: priming granite session with `current_tool_name` set + fresh PTY
  is NOT killed.
- Constant test: `NEVER_STARTED_PTY_LIVENESS_SECS` default + env override.

### 4. Validation
- **Task ID**: validate-gate
- **Depends On**: build-gate, build-tests
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands; confirm all Success Criteria.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-gate
- **Assigned To**: gate-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-session-liveness.md` (and granite cross-ref) per the Documentation
  section. Record the measured latency + chosen window.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-gate, document-feature
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; verify every Success Criterion including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_never_started_recovery.py tests/unit/test_session_health_tool_timeout.py tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py agent/session_stall_classifier.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/session_stall_classifier.py` | exit code 0 |
| Helper exists | `grep -c "_prime_pty_alive" agent/session_health.py` | output > 1 |
| Gate wired at call-site | `grep -n "_prime_pty_alive" agent/session_health.py \| grep -c "never_started_pty_deferred\|3[0-9][0-9][0-9]"` | output > 0 |
| Constant defined | `grep -c "NEVER_STARTED_PTY_LIVENESS_SECS" agent/session_stall_classifier.py` | output > 0 |
| No blanket grace bump (anti-criterion) | `git diff main -- agent/session_stall_classifier.py \| grep -E '^\+NEVER_STARTED_GRACE_SECS'` | match count == 0 |
| New deferred counter present | `grep -c "never_started_pty_deferred" agent/session_health.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Liveness window source.** Should `NEVER_STARTED_PTY_LIVENESS_SECS` default to
   `HEARTBEAT_FRESHNESS_WINDOW` (90s), or wait on Step 1's measurement to set it? (Plan assumes:
   default 90s, override only if measurement shows prime repaint gaps p99 > 90s.)
2. **Grace bump alongside the gate.** If Step 1 shows the *first* `last_pty_activity_at` stamp
   itself routinely lands >90s after `started_at` (i.e. even the read loop is slow to start), do
   we also nudge `NEVER_STARTED_CONFIRM_MARGIN_SECS` up modestly, or rely solely on the gate?
   (Plan's default: gate only; grace untouched unless data forces it.)
3. **tool_timeout path during prime.** Confirm via measurement/test that granite priming
   sessions reliably have `last_pty_read_loop_at` set (so the existing `_pty_quiescent_long_enough`
   gate already protects the tool_timeout half) — if any prime window exists where the read loop
   field is null while a tool is in flight, AC2 would need a code change to the tool_timeout path,
   not just an assertion.
