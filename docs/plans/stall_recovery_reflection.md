---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1768
last_comment_id:
---

# Stall-Recovery Reflection + `granite_wedged` Signal

## Problem

On 2026-06-23, three `running` sessions wedged after a worker restart: their granite
containers looped on `transcript read: no-new-entry … using unknown classification` and
never advanced a turn, while still emitting fresh heartbeats. The existing `stall-advisory`
reflection detected two of them but is **advisory only** — it warns and never acts. No
reflection killed the wedged sessions or re-drove their work; a human had to. Three
simultaneous hung granite reads saturated the worker's thread pool and ultimately hung the
whole worker.

**Current behavior:**
- PR #1728 (merged 2026-06-18) shipped the **substrate**: PTY-liveness fields on
  `AgentSession` (`last_pty_read_loop_at`, `last_pty_activity_at`, `mid_run_quiescent_since`,
  `mid_run_pty_snapshot`), and a **stage-1 observe-only** detector
  `_eval_mid_run_pty_stage1()` (`agent/session_health.py:2584`) that stamps
  `mid_run_quiescent_since` and logs confirmed suspects. Its docs
  (`docs/features/never_started_session_recovery.md`) **explicitly mark stage-2 recovery as
  deferred to a separate issue** — which #1768 now delivers.
- The classifier `classify_session_stall()` (`agent/session_stall_classifier.py:114`) emits
  `never_started`, `idle_gap_exceeded_stall`, `kill_transition` — but has **no granite-wedge
  verdict** and does not read the PTY-liveness markers stage-1 maintains.
- The no-new-entry loop (the exact 2026-06-23 failure) is **not** captured by stage-1, which
  requires a tool in flight (`current_tool_name`). The container increments an in-memory
  `transcript_fallback_count` (container.py:1124,1198,1423,1536,1601) but **never persists
  it**, so no consumer can see the streak.

**Desired outcome:**
A heartbeating-but-wedged session (no turn progress, stale PTY activity, or repeated
`no-new-entry` cycles) is detected via a `granite_wedged` verdict and recovered automatically
— killed and its unanswered human messages re-enqueued via `valor-catchup` — under
conservative, well-gated rules, dry-run by default behind `FEATURES__STALL_RECOVERY_ENABLED`.

## Freshness Check

**Baseline commit:** `ea3742338ab194b3c199612133dbfb9ccc81adeb`
**Issue filed at:** 2026-06-23T06:01:25Z
**Disposition:** Minor drift (builds on prior merged PR #1728 — substrate shipped, recovery deferred)

**File:line references re-verified:**
- `classify_session_stall()` — issue cites `agent/session_health.py`; **drifted** → now
  `agent/session_stall_classifier.py:114` (extracted). Plan targets the new location.
- `agent/granite_container/container.py:454-469,1192-1199` — no-new-entry / unknown
  classification cycle — **still holds**. `transcript_fallback_count` increments at
  1124, 1198, 1423, 1536, 1601.
- `agent/session_health.py` orphan reaper `ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS = 1800` —
  **still holds** (30-min heartbeat gate skips fresh-heartbeat sessions).
- `agent/sustainability.py:132 session_recovery_drip()` — **still holds** (only
  paused/paused_circuit, never killed).
- `AgentSession.last_pty_activity_at` / `last_heartbeat_at` — **still holds**
  (models/agent_session.py:367,392). #1728 added `last_pty_read_loop_at`,
  `mid_run_quiescent_since`, `mid_run_pty_snapshot` (383-405).

**Cited sibling issues/PRs re-checked:**
- #1724 — CLOSED 2026-06-18 by PR #1728. Shipped never_started recovery (Path A) + mid-run
  stage-1 detection (Path B, observe-only). **Stage-2 recovery explicitly deferred** — the
  exact scope of #1768.
- #1539 — crash-autoresume policy. `FeatureSettings.crash_autoresume_*` + `crash_recovery.py`
  are the canonical gated-dry-run actor template this plan mirrors.

**Commits on main since issue was filed (touching referenced files):** none after baseline
(issue filed 06-23; #1728 merged 06-18, before filing — its effect is already accounted for).

**Active plans in `docs/plans/` overlapping this area:** `never_started_session_recovery.md`
(shipped via #1728). This plan is its declared stage-2 follow-up; no conflict.

**Notes:** #1768 is NOT already fixed — the recovery actor genuinely does not exist
(`grep granite_wedged|stall_recovery|STALL_RECOVERY` across agent/, reflections/, config/,
tests/ → zero hits). The plan extends #1728's substrate rather than re-detecting.

## Prior Art

- **PR #1728** (merged): `feat(#1724): recover stalled never_started and mid-run-wedge granite
  sessions`. Shipped PTY-liveness fields + stage-1 observe-only detector. Deferred stage-2
  recovery → this plan. **Reuse:** PTY fields, `mid_run_quiescent_since`,
  `_apply_recovery_transition()`, constants.
- **PR #1539 / `reflections/crash_recovery.py`**: gated dry-run actor reading
  `settings.features.crash_autoresume_*` at run time. Template for the stall-recovery actor's
  flag/budget/telemetry structure.
- **#1762 / PR #1769** (`tool_timeout_recovery_loop_fix`): recent session-health requeue work;
  no overlap with PTY-wedge path.

## Research

No relevant external findings — this is purely internal worker/reflection plumbing. Proceeding
with codebase context.

## Data Flow

1. **Granite container loop** (`container.py`): on each PM-classify cycle, either a real PM turn
   is classified (progress) OR the transcript read returns empty → `transcript_fallback_count += 1`
   and `_unknown_classification()`. **New:** on empty read, increment a persisted
   `granite_no_new_entry_streak`; on a real classified turn, reset it to 0.
2. **BridgeAdapter persistence** (`bridge_adapter.py`): the existing `_make_pty_read_callback`
   already persists `last_pty_*` fields fail-silent. **New:** a sibling callback persists
   `granite_no_new_entry_streak` via `save(update_fields=[...])`.
3. **Classifier** (`session_stall_classifier.py`): `classify_session_stall(events, session=…)`
   reads `session.status`, `last_heartbeat_at`, `last_pty_activity_at`,
   `mid_run_quiescent_since`, `granite_no_new_entry_streak`. **New:** emits
   `StallVerdict("stalled", "granite_wedged", {...})` when a `running` session has a fresh
   heartbeat AND (`mid_run_quiescent_since` aged ≥ grace OR streak ≥ M).
4. **stall-recovery reflection** (`reflections/stall_recovery.py`, NEW): for each probe-status
   session, calls the classifier; for `stalled` + reason ∈ {never_started, granite_wedged,
   idle_gap_exceeded_stall}, applies gates → (dry-run: log only) / (enforce:
   `running → killed` + `valor-catchup` re-enqueue). Telemetry on every decision.
5. **Output**: session transitioned to `killed`, catchup subprocess re-enqueues unanswered
   human messages; Redis counters + structured logs record every kill/skip.

## Architectural Impact

- **New dependencies**: reflection → `agent.session_stall_classifier`, `agent.session_telemetry`,
  `tools.valor_session` (kill) / `bridge.agent_catchup` (catchup via subprocess). No new libs.
- **Interface changes**: `classify_session_stall` gains one verdict (additive, no signature
  change). One nullable `AgentSession` field added. `FeatureSettings` gains `stall_recovery_*`.
- **Coupling**: keeps detection (classifier, pure/read-only) and action (reflection) separate —
  preserves `stall-advisory`'s test-enforced zero-write contract. Does NOT add writes to the
  classifier or advisory.
- **Data ownership**: the granite container owns `granite_no_new_entry_streak`; the reflection
  owns the kill/catchup transition and its Redis budget counters.
- **Reversibility**: feature flag default-off ⇒ enforce path dormant until explicitly enabled;
  fully reversible by unsetting `FEATURES__STALL_RECOVERY_ENABLED`.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (resolve new-reflection-vs-action-mode — resolved: new reflection)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. (Redis + worker already required by
the reflection system.)

## Solution

### Key Elements

- **`granite_no_new_entry_streak` field** (AgentSession, nullable IntegerField): persisted
  count of consecutive `no-new-entry`/`unknown-classification` granite cycles; reset on real
  turn progress. Makes the streak readable by the classifier.
- **`granite_wedged` verdict** (classifier): the heartbeating-but-stuck signal — fresh heartbeat
  + stale PTY activity (via `mid_run_quiescent_since`) OR streak ≥ M. Read-only.
- **`stall-recovery` reflection** (NEW, gated actor): consumes classifier verdicts; kills +
  catchups `stalled` sessions under conservative gates; dry-run by default.
- **`FeatureSettings.stall_recovery_*`**: `_enabled` (default False), `_run_budget` (K/run),
  `_per_session_kill_budget`, `_consecutive_observations` (N), `_grace_secs`,
  `_no_new_entry_threshold` (M).

### Flow

Worker reflection tick (`stall-recovery`, every 300s) → query probe-status sessions →
classify each → for `stalled` + actionable reason: increment N-consecutive Redis counter →
if N reached AND under K-per-run AND under per-session kill budget AND flag enabled →
`running → killed` + spawn `valor-catchup` → record telemetry. If flag disabled → log
intended action only (dry-run). Non-stalled → reset that session's N-counter.

### Technical Approach

- **Container streak** (`container.py`): at the existing 5 `transcript_fallback_count += 1`
  sites, also bump an instance counter; on a successful PM-classified turn (the
  `classify_pm_prefix` branch / `_on_turn` hook), reset it. Surface the current streak to
  BridgeAdapter via a small hook (mirror `on_pty_read`), persisted fail-silent. Keep the
  load-bearing greppable substrings unchanged.
- **Classifier verdict** (`session_stall_classifier.py`): add a granite-wedge check in the
  probe-status branch, BEFORE the idle-gap analysis. Guard on
  `session.status == "running"`, heartbeat fresh (`now - last_heartbeat_at <
  HEARTBEAT_FRESHNESS_WINDOW`), and (`mid_run_quiescent_since` set & `now - it >=
  STALL_RECOVERY_GRACE_SECS`) OR (`granite_no_new_entry_streak >= M`). Fully fail-soft (wrapped
  by existing try/except). New env-tunable constants with provisional-tunable comments.
- **Reflection** (`reflections/stall_recovery.py`): structured on `crash_recovery.py`. Reads
  `settings.features.stall_recovery_*` at run time. Phase 1: classify + N-counter bookkeeping
  (Redis `{project_key}:stall-recovery:consecutive:{session_id}`, plain counter — consistent
  with existing `session-health:` counters, not Popoto-managed). Phase 2 (enforce only):
  kill via `AgentSession` status transition (Popoto ORM `.save()`), then
  `subprocess.run(["valor-catchup", ...])`. Per-session kill budget tracked via a nullable
  `stall_recovery_kill_count` field (mirror `crash_autoresume_max_attempts`). Every decision
  emits a structured log + Redis telemetry counter.
- **Cadence**: add a `stall-recovery` entry to `~/Desktop/Valor/reflections.yaml` (vault
  symlink, gitignored) — `every: 300s`, `callable: reflections.stall_recovery.run_stall_recovery`,
  `enabled: true`. The actor is still dry-run until the FEATURES flag flips, so registering the
  cadence is safe.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Classifier's granite-wedge branch lives inside the existing `try/except` in
  `_classify` — assert it returns `healthy/unclassifiable` (never raises) on malformed
  session attributes (e.g. `mid_run_quiescent_since` not a datetime).
- [ ] Container streak increment/reset and its persistence callback must be fail-silent
  (mirror `on_pty_read`); test that a raising `save` does not crash the cycle.
- [ ] Reflection per-session loop swallows exceptions and continues; test one session raising
  does not abort the run and the run still returns a summary dict.

### Empty/Invalid Input Handling
- [ ] Classifier with `session=None` or missing PTY fields → no `granite_wedged` (falls through
  to existing logic). Test.
- [ ] Reflection with zero probe-status sessions → returns `status="ok"`, no kills. Test.
- [ ] `granite_no_new_entry_streak` is None (pre-#1768 sessions) → treated as 0, no false wedge.

### Error State Rendering
- [ ] Reflection summary dict reports counts of killed/skipped/dry-run with reasons; test the
  dry-run path logs the intended kill WITHOUT mutating session status.
- [ ] Telemetry counter increments are asserted for both kill and skip branches.

## Test Impact

- [ ] `tests/unit/test_session_stall_classifier.py` (if present; else create) — UPDATE/ADD:
  add `granite_wedged` cases. Verify existing verdicts unaffected.
- [ ] `tests/unit/test_never_started_recovery.py` — no change expected (stage-1 untouched);
  re-run as regression guard. If the streak reset hook touches the `_on_turn` path, UPDATE any
  affected assertion.
- [ ] `tests/unit/test_stall_advisory.py` (if present) — verify advisory's zero-write contract
  still holds after classifier change; UPDATE only if a verdict-count assertion shifts.
- [ ] NEW `tests/unit/test_stall_recovery.py` — reflection gates, dry-run, enforce, telemetry.
- [ ] NEW `tests/unit/test_granite_no_new_entry_streak.py` — container increment/reset +
  persistence (or fold into an existing container test module).

No existing test is expected to be deleted — changes are additive (one new verdict, one new
field, one new reflection). The classifier change is purely additive to a fail-soft function.

## Rabbit Holes

- **Don't re-detect mid-run quiescence in the classifier.** Stage-1 already maintains
  `mid_run_quiescent_since`; the classifier should READ it, not re-derive PTY freshness.
- **Don't refactor `stall-advisory` into an actor.** Its zero-write contract is test-enforced;
  action belongs in the new reflection.
- **Don't touch worker-level U-state recovery** — that's the companion `bridge` issue.
- **Don't normalize the PTY buffer further** — #1728 noted full cursor-normalization is a
  stage-2-only concern; the no-new-entry streak doesn't need it.
- **Don't build a generic "consecutive observation" framework** — a single Redis counter per
  session is sufficient; resist over-engineering.

## Risks

### Risk 1: False-positive kill of a slow-but-live turn
**Impact:** A legitimately long turn (extended thinking, large tool output) gets killed.
**Mitigation:** Dry-run default (flag off); N-consecutive-observation gate; `granite_wedged`
requires BOTH fresh heartbeat AND stale PTY/streak (a live turn repaints the PTY ⇒
`last_pty_activity_at` fresh ⇒ `mid_run_quiescent_since` cleared by stage-1). Grace window sized
≥ `MID_RUN_QUIESCENCE_SECS` (180s).

### Risk 2: Kill-thrash (session killed, resumed, wedges again, killed…)
**Impact:** Resource churn, repeated catchup re-enqueues.
**Mitigation:** Per-session kill budget (`stall_recovery_kill_count` field); K-per-run cap;
telemetry surfaces thrash for human review.

### Risk 3: catchup re-enqueue floods the queue
**Impact:** Many killed sessions each spawn catchup.
**Mitigation:** K-per-run cap bounds kills/run; `valor-catchup` itself uses agent-judgment to
only re-enqueue genuinely-unanswered messages.

## Race Conditions

### Race 1: Session advances a turn between classification and kill
**Location:** `reflections/stall_recovery.py` (classify → kill window)
**Trigger:** A wedged session unwedges (real turn) in the seconds between the classifier verdict
and the kill transition.
**Data prerequisite:** Verdict must reflect current state at kill time.
**State prerequisite:** Session still `running` and still wedged at transition.
**Mitigation:** Re-read the session and re-confirm `status == "running"` AND re-confirm the
wedge signal (streak/quiescent_since) immediately before the kill (CAS-style re-check). Skip if
state changed. N-consecutive gate already requires sustained wedge across ticks.

### Race 2: Concurrent reflection ticks double-killing
**Location:** reflection scheduler
**Trigger:** Overlapping runs of the same reflection.
**Mitigation:** Reflection scheduler runs callables serially on one asyncio loop; kill
transition is idempotent (terminal status check rejects re-kill). Per-session kill budget caps
total attempts.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1768-companion] Worker-level `U`-state recovery in the external watchdog —
  filed separately under the `bridge` label per the issue's scope boundary. This issue is the
  session-level prevention layer only.
- Nothing else deferred — the `granite_wedged` signal, the recovery actor, the gates, the flag,
  telemetry, and docs are all in scope for this plan.

<!-- Note: the companion bridge issue is referenced in #1768's body ("companion issue ... filed
separately under the bridge label"); it is a genuine separate-system, separately-tracked item. -->

## Update System

- **`reflections.yaml` (vault symlink):** add the `stall-recovery` cadence entry to
  `~/Desktop/Valor/reflections.yaml`. This file is a gitignored iCloud-synced symlink
  (`config/reflections.yaml` → vault), so the entry propagates to every machine via iCloud, not
  via git. **Document this in `docs/features/stall-recovery.md`** with the exact YAML block to
  add, so any machine missing the entry can be fixed by hand.
- **No `scripts/update/` changes required** — no new dependency, binary, or config file checked
  into the repo. `FEATURES__STALL_RECOVERY_ENABLED` defaults False, so machines pick up the
  dormant (dry-run) reflection automatically on next worker restart after pulling.
- **Enabling enforce mode** is a documented, reversible per-machine step: set
  `FEATURES__STALL_RECOVERY_ENABLED=1` in `~/Desktop/Valor/.env` on exactly ONE designated
  machine (mirror the crash-autoresume one-machine policy), restart the worker.

## Agent Integration

- **No new CLI entry point required.** The reflection runs inside the worker's reflection
  scheduler (in-process), not via the agent's Bash tool. The recovery action reuses the existing
  `valor-catchup` CLI (`pyproject.toml:79`) and the `AgentSession` ORM for the kill.
- **No bridge import changes.** The bridge does not call this code; the worker's
  `reflection_scheduler` does, via the `reflections.yaml` `callable` entry.
- **Integration test:** a test that constructs a wedged `AgentSession`, runs
  `run_stall_recovery()` in enforce mode (flag patched on), and asserts the session transitions
  to `killed` and a catchup invocation is attempted — verifies the in-process path end to end.
- This is a worker-internal reflection; it is invisible to the conversational agent surface by
  design (it operates ON sessions, not as a tool the agent calls).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/stall-recovery.md` — document the `granite_wedged` signal, the
  recovery actor, all gates (N/K/per-session budget), the `FEATURES__STALL_RECOVERY_ENABLED`
  flag, the dry-run→enforce enablement step, the telemetry counters, and the exact
  `reflections.yaml` cadence block.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/never_started_session_recovery.md` — change the "Stage-2 (Planned —
  Not Yet Shipped)" section to "Shipped via #1768", pointing to the new doc.
- [ ] Update `docs/features/session-recovery-mechanisms.md` — note `mid_run_quiescent_since` is
  now consumed by the `granite_wedged` verdict + stall-recovery actor.

### Inline Documentation
- [ ] Docstring on `run_stall_recovery` describing gates + dry-run semantics (mirror
  `crash_recovery.py`).
- [ ] Comments on new threshold constants marking them provisional/env-tunable
  (per the magic-numbers convention).

## Success Criteria

- [ ] `classify_session_stall()` emits `granite_wedged` for heartbeating sessions with stale
  PTY activity / streak ≥ M, with unit tests.
- [ ] `stall-recovery` reflection kills `stalled` sessions (reason ∈ {never_started,
  granite_wedged, idle_gap_exceeded_stall}) and triggers `valor-catchup`, gated by N-consecutive,
  K-per-run, and per-session kill budget.
- [ ] Ships dry-run by default behind `FEATURES__STALL_RECOVERY_ENABLED`; enabling is documented
  and reversible.
- [ ] Telemetry records every kill/skip decision with the triggering verdict.
- [ ] Tests cover: granite-wedged detection, dry-run emits-but-doesn't-act, enforce-mode
  kills + re-enqueues, conservative gates (suspect never killed, cap respected).
- [ ] `docs/features/stall-recovery.md` created; `never_started_session_recovery.md` updated.
- [ ] Existing `tests/unit/test_never_started_recovery.py` still green (regression).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classifier+field)**
  - Name: classifier-builder
  - Role: Add `granite_no_new_entry_streak` field, container increment/reset + persistence, and
    the `granite_wedged` verdict + constants.
  - Agent Type: builder
  - Resume: true

- **Builder (reflection+settings)**
  - Name: reflection-builder
  - Role: Add `FeatureSettings.stall_recovery_*`, the `stall-recovery` reflection actor, and its
    gates/telemetry.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: stall-validator
  - Role: Verify acceptance criteria, run narrow unit tests, confirm zero-write contract intact.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: stall-doc
  - Role: Create stall-recovery.md, update README + never_started doc.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Field + container streak persistence
- **Task ID**: build-streak
- **Depends On**: none
- **Validates**: tests/unit/test_granite_no_new_entry_streak.py (create)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add nullable `granite_no_new_entry_streak` IntegerField to `models/agent_session.py`.
- In `container.py`, increment an instance streak at the 5 `transcript_fallback_count += 1`
  sites; reset to 0 on a real PM-classified turn. Surface via a persistence hook (mirror
  `on_pty_read`); persist fail-silent in `bridge_adapter.py`.

### 2. `granite_wedged` verdict
- **Task ID**: build-verdict
- **Depends On**: build-streak
- **Validates**: tests/unit/test_session_stall_classifier.py (create/extend)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the granite-wedge check + env-tunable constants to `session_stall_classifier.py`,
  reading `last_heartbeat_at`, `mid_run_quiescent_since`, `granite_no_new_entry_streak`.
- Unit tests for the verdict and its fail-soft behavior.

### 3. Settings + reflection actor
- **Task ID**: build-reflection
- **Depends On**: none
- **Validates**: tests/unit/test_stall_recovery.py (create)
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `FeatureSettings.stall_recovery_*` to `config/settings.py`.
- Create `reflections/stall_recovery.py` (mirror `crash_recovery.py`): classify, N-counter,
  gates, dry-run vs enforce, kill + catchup, telemetry.

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-verdict, build-reflection
- **Assigned To**: stall-validator
- **Agent Type**: validator
- **Parallel**: false
- Run new unit tests + `test_never_started_recovery.py` regression. Confirm gates + zero-write.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: stall-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/stall-recovery.md`; update README index + never_started doc +
  session-recovery-mechanisms doc; include the `reflections.yaml` block.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New unit tests pass | `/Users/valorengels/src/ai/.venv/bin/python -m pytest tests/unit/test_stall_recovery.py tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| Regression green | `/Users/valorengels/src/ai/.venv/bin/python -m pytest tests/unit/test_never_started_recovery.py -q` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ reflections/ config/ models/` | exit code 0 |
| Verdict wired | `grep -rn granite_wedged agent/session_stall_classifier.py` | output contains granite_wedged |
| Flag present | `grep -n stall_recovery_enabled config/settings.py` | output contains stall_recovery_enabled |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **New reflection vs action-mode on stall-advisory** — RESOLVED in recon: new reflection
   (`stall-advisory`'s zero-write contract is test-enforced). Confirm acceptable.
2. **Per-session kill budget storage** — proposed nullable `stall_recovery_kill_count` field
   vs Redis counter. Field chosen for durability/visibility; confirm acceptable.
3. **`granite_wedged` grace window default** — proposed ≥ `MID_RUN_QUIESCENCE_SECS` (180s);
   confirm the M (no-new-entry streak) default (proposed 3) is conservative enough.
