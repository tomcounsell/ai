---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/valorengels/ai/issues/1724
last_comment_id:
---

# Recover never_started sessions promptly: stop the heartbeat from blinding the recovery actor

## Problem

A granite session can be marked `running` while wedged in PM priming — it has produced **no turn** (`last_turn_at = None`, `sdk_ever_output = False`), yet its background watchdog keeps writing fresh `last_heartbeat_at` / `last_sdk_heartbeat_at` every ~60s. Two detectors look at this state and disagree:

- `reflections/stall_advisory.py::run_stall_advisory` correctly classifies it `STALLED reason=never_started` at a 120s grace, but is **advisory-only**: it logs a WARNING and optionally Telegram-alerts, then returns. It never recovers.
- `agent/session_health.py::_has_progress` sub-check B treats the fresh heartbeat as "alive" across the whole band `300s <= running_seconds <= 1800s` for a no-output session, so the actor that *can* recover considers the wedge healthy for up to 30 minutes.

**Current behavior:**
Session marked `running`, `last_turn_at = None`, heartbeats fresh. `stall-advisory` logs `never_started` at ~120s; nothing acts. `session-health` recovery considers it healthy for up to 1800s. Only a manual `valor-service.sh restart` (or eventually crossing the 1800s budget plus the 20-tick reprieve cap) frees it. Observed live on 2026-06-18 with session `tg_valor_-1003449100931_993` (~5 min wedge until manual restart).

**Desired outcome:**
A `running` session with `last_turn_at = None` and fresh heartbeats is escalated to real recovery on a timescale consistent with the advisory's grace — well under 1800s — **without** killing legitimately-slow-but-progressing sessions (large initial prompt, slow auth). The advisory grace and the actor's `never_started` grace derive from a single shared constant so they cannot drift apart again.

## Freshness Check

**Baseline commit:** b414eed1fb7cc50fff5bd2979c376bc861b90782
**Issue filed at:** 2026-06-18T03:13:30Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/stall_advisory.py:38-161` — advisory-only, zero writes — still holds.
- `agent/session_stall_classifier.py:50` — `NEVER_STARTED_GRACE_SECS = 120` — still holds (issue attributed the constant to `stall_advisory.py`; it actually lives in the classifier — corrected here and in Technical Approach).
- `agent/session_health.py:219` — `HEARTBEAT_FRESHNESS_WINDOW = 90` — still holds.
- `agent/session_health.py:267-269` — `STARTUP_GRACE_SECONDS = 300` (aliased to `AGENT_SESSION_HEALTH_MIN_RUNNING`) — still holds.
- `agent/session_health.py:284` — `NO_OUTPUT_BUDGET_SECONDS = 1800` — still holds.
- `agent/session_health.py:784-824` — sub-check B fresh-heartbeat band — still holds.
- `agent/session_health.py:1708-1712` — main loop calls `_has_progress` only when `running_seconds > 300s` — still holds (the load-bearing constraint; see Revised in recon).

**Cited sibling issues/PRs re-checked:**
- #1356 — CLOSED 2026-05-11, introduced `NO_OUTPUT_BUDGET_SECONDS=1800` gate. Resolution unchanged. This issue is its direct follow-up.
- #1614 — CLOSED 2026-06-12, gated sticky own-progress fields on heartbeat freshness. Same bug family; reuse its test model.
- #1172 — CLOSED 2026-04-29, "surface progress or stay graceful." Design context on not over-killing slow sessions.

**Commits on main since issue was filed (touching referenced files):** None (`git log --since=2026-06-18T03:13:30Z` on the three files is empty).

**Active plans in `docs/plans/` overlapping this area:** None active. `session-heartbeat-progress-guard.md` (#1036, docs_complete), `stalled-session-user-visible-alert.md` (#1313), `progress-detector-tweaks.md` (#1159) are prior/closed-tracking work in the same module, not competing plans for #1724.

## Prior Art

- **#1356** (closed 2026-05-11): "tier-1 `_has_progress` sub-check B passes forever for no-output sessions." Introduced `NO_OUTPUT_BUDGET_SECONDS=1800`. **Outcome: partial.** Bounded the previously-infinite fast-path but set the bound at 30 min and never reconciled it with the advisory's 120s grace. This issue is the direct follow-up.
- **#1614** (closed 2026-06-12): "ungated sticky own-progress fields let a 4-day-stalled session evade recovery." **Outcome: success.** Gated own-progress fields (`turn_count`/`log_path`/`claude_session_uuid`) on heartbeat freshness within `NO_OUTPUT_BUDGET_SECONDS`. Reuse its test pattern (`tests/unit/test_session_health_inference_removed.py`) as a model for the regression test.
- **#1172** (closed 2026-04-29): "PM session liveness: surface progress or stay graceful." **Outcome: success.** Retired the wall-clock cap and the "stdout" reprieve gate; established the "do not infer death from staleness, only kill on positive no-progress evidence" principle. Constrains this fix: we must not reintroduce a naive wall-clock kill.
- **#1226** (closed): added `MAX_NO_OUTPUT_REPRIEVES` reprieve-escalation cap. The 1800s wedge is the *combination* of sub-check B's band and this 20-tick cap.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1356 | Bounded sub-check B's fresh-heartbeat fast-path at `NO_OUTPUT_BUDGET_SECONDS=1800` | Picked 1800s to stay symmetric with `MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW`, an internally-derived number with no relationship to the advisory's externally-chosen 120s grace. The two systems were never tied to one constant, so they drift. 30 min is still a long wedge for a session that has produced literally nothing. |
| #1226 | Added the 20-tick reprieve cap for no-output sessions | Operates on the *same* 1800s timescale; doesn't shorten the never-started case. |

**Root cause pattern:** Two independently-owned detectors (advisory classifier vs. recovery actor) each picked their own grace from their own internal logic, with no shared source of truth. The 1800s budget treats "session that has run 25 min and produced output then went quiet" and "session that has produced *nothing* since start" as the same case. They are not: a never-started session has zero positive progress evidence and should be recoverable far sooner than a session that demonstrably did work and then idled.

## Data Flow

1. **Entry point:** Bridge enqueues an Eng `AgentSession`; worker picks it up, sets `status=running`, `started_at=now`, spawns the granite PTY pair. Background `_heartbeat_loop` begins writing `last_heartbeat_at` / `last_sdk_heartbeat_at` every 60s. Priming runs; **no `turn_start` event, `last_turn_at` stays None, `sdk_ever_output` stays False.**
2. **Advisory path (read-only):** `run_stall_advisory` (periodic reflection) → `read_session_timeline(session_id)` → `classify_session_stall(events, session)`. With no `turn_start` and `elapsed > NEVER_STARTED_GRACE_SECS (120s)` → `StallVerdict("stalled", "never_started", ...)`. Logged/alerted. **No write. Dead end for recovery.**
3. **Actor path (can write):** `_agent_session_health_check` (5-min loop) iterates `running` sessions. Race guard: only evaluates `_has_progress(entry)` when `running_seconds > 300s`. `_has_progress` → sub-check A (per-turn freshness: absent, `sdk_ever_output=False`) → sub-check B (fresh heartbeat + `300s <= running_seconds <= 1800s` → **returns True**). So `should_recover` stays False until 1800s. After 1800s, sub-check B falls through, own-progress fields are gated on heartbeat freshness (#1614), and the 20-tick reprieve cap finally lets `_apply_recovery_transition(reason_kind="no_progress")` fire → `running -> pending` (or `failed` after `MAX_RECOVERY_ATTEMPTS`).
4. **Output:** Re-queued session is picked up cleanly on next worker tick.

The fix lands at step 3, inside `_has_progress` sub-check B (and the constant it reads), not at step 2.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** None to public signatures. One new module-level constant in `agent/session_stall_classifier.py` (the shared source of truth); `agent/session_health.py` imports it. Direction of the new import: `session_health` → `session_stall_classifier`. **This is the only allowed direction** — the classifier must never import `session_health` (enforced by `tests/integration/test_stall_advisory_e2e.py`'s `sys.modules` guard). Importing a bare `int` constant the other way is safe and does not pull in the kill machinery.
- **Coupling:** Intentionally *increases* coupling on one shared constant — that is the point (Q3: single source of truth). Both detectors read the same value.
- **Data ownership:** Unchanged. No new fields on `AgentSession`. `sdk_ever_output` continues to be derived (not stored) from `last_tool_use_at`/`last_turn_at`.
- **Reversibility:** High. The change is a tighter branch inside one function plus one constant. Revertible by restoring the old band boundary.

## Appetite

**Size:** Medium

**Team:** Solo dev (debugging-specialist for the recovery-logic change), validator, documentarian.

**Interactions:**
- PM check-ins: 1-2 (the Q1-Q4 direction decisions in Open Questions must be confirmed before build)
- Review rounds: 1 (this touches live recovery logic — one careful code-review pass on the regression-safety of the slow-priming case)

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies internal worker recovery logic only.

## Solution

### Key Elements

- **Shared never-started grace constant** — a single module-level constant (proposed home: `agent/session_stall_classifier.py`, alongside the existing `NEVER_STARTED_GRACE_SECS`) that both the advisory classifier and the recovery actor read. The recovery actor cannot physically act before its 300s race-guard floor, so this constant defines the *recovery* grace at the floor (300s) while the advisory keeps its earlier *detection* grace (120s). The relationship between the two is made explicit in one place rather than two divergent magic numbers.
- **Never-started branch in sub-check B** — when `sdk_ever_output is False` AND the session has never produced a turn, the fresh-heartbeat fast-path is denied once `running_seconds` exceeds the shared never-started recovery grace, instead of waiting for the full `NO_OUTPUT_BUDGET_SECONDS (1800s)`. The heartbeat loop runs during priming, so heartbeat freshness is **not** progress evidence for a session that has never produced a turn.
- **Preserved slow-priming reprieve** — the existing Tier-2 reprieve gates (`compacting` / `children` / `alive`) still apply. A session that is genuinely still priming and has a live SDK subprocess with children gets reprieved; a wedged session with no children and no progress does not. This is the regression guard for "slow auth / large initial prompt."

### Flow

Worker running session, no turn produced → 5-min health loop tick (running_seconds > 300s) → `_has_progress`: sub-check A absent, sub-check B sees `sdk_ever_output=False` + never-started + running_seconds > never_started_grace → **denies fast-path** → own-progress fields absent → returns False → Tier-2 reprieve evaluated → no reprieve signal → `_apply_recovery_transition(reason_kind="no_progress")` → `running -> pending` → re-queued.

Contrast (must NOT recover): same loop, but the session is genuinely priming with a live SDK child process → Tier-2 `children`/`alive` reprieve fires → kill skipped, `reprieve_count++`. If it keeps producing nothing past the reprieve cap, it eventually recovers — but the *normal* case is it produces a turn first and exits the no-output regime entirely.

### Technical Approach

Decision direction (pending Open Questions confirmation):

- **Q1 → Yes (recovery actor owns the escalation).** Keep recovery in `session_health` where the kill/requeue/reprieve machinery already lives. Add a never-started leg to sub-check B: when `not sdk_ever_output` and there is no turn evidence, gate the fresh-heartbeat pass on a tighter `running_seconds <= NEVER_STARTED_RECOVERY_GRACE` instead of `<= NO_OUTPUT_BUDGET_SECONDS`. Heartbeat freshness alone is not progress for a never-started session.
- **Q2 → No (advisory stays advisory).** Do not give `stall_advisory.py` write/kill powers. Avoiding double-action is automatic if only one actor (`session_health`) ever recovers. The advisory remains the early-warning observability surface; the actor is the single writer. This respects the classifier's hard "zero writes / never import session_health" constraint and avoids the "competing recovery functions racing" antipattern (#1036).
- **Q3 → One shared constant.** Define the recovery grace once. Proposed: a new `NEVER_STARTED_RECOVERY_GRACE_SECS` in `agent/session_stall_classifier.py` (next to `NEVER_STARTED_GRACE_SECS`), imported by `session_health`. Document the deliberate two-value relationship: detection at 120s (advisory), action at the 300s race-guard floor — i.e. set `NEVER_STARTED_RECOVERY_GRACE_SECS = STARTUP_GRACE_SECONDS` (300s) or a small explicit multiple, with a comment tying it to `NEVER_STARTED_GRACE_SECS`. The exact home/value is an Open Question; the principle (single source of truth, no silent drift) is fixed.
- **Q4 → Regression test for slow priming.** A session that produces a `turn_start` / writes `last_turn_at` within the normal startup window flips `sdk_ever_output=True`, leaves the never-started regime, and is governed by sub-check A's 1800s per-turn window — never touched by the new branch. The test asserts: (a) a never-started + fresh-heartbeat session is recovered shortly after 300s; (b) a session that produced a turn (or has a live SDK child) is NOT recovered by the new branch.

Implementation is a tight change inside `_has_progress` sub-check B (`session_health.py:784-824`) plus one constant and an import. The `tier1_falloff:no_output_budget_exceeded` Redis counter increment should be mirrored with a distinct counter (e.g. `tier1_falloff:never_started_grace_exceeded`) so dashboards distinguish the two fall-through reasons.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The only `except Exception` blocks in scope are the best-effort Redis counter increments in sub-check B and `_apply_recovery_transition` — they log at warning/debug and continue. Assert that a Redis failure on the new counter does NOT block the recovery decision (test: patch `POPOTO_REDIS_DB.incr` to raise, assert `_has_progress` still returns False and recovery still fires).
- [ ] `classify_session_stall` already swallows all exceptions → `StallVerdict("healthy", "unclassifiable")`. No change to that contract; existing tests cover it.

### Empty/Invalid Input Handling
- [ ] `_has_progress` with `started_at=None AND created_at=None` (legacy/phantom) must preserve the existing fast-path (return True) — do not regress the legacy leg. Add/keep a test asserting this.
- [ ] Negative `running_seconds` (clock skew) must preserve the fast-path (return True) — covered by the existing `running_seconds < STARTUP_GRACE_SECONDS` guard; keep a test pinning it.
- [ ] `last_heartbeat_at` absent (None) on a never-started session → sub-check B already skips; falls through to own-progress fields (gated by #1614). Pin with a test.

### Error State Rendering
- [ ] No user-visible output in this change — recovery is internal. The advisory's Telegram alert path is unchanged. State: error rendering is out of scope for this fix; the observable signal is the project-scoped Redis recovery counter and the `[session-health] Recovering session ...` log line, both asserted in tests.

## Test Impact

- [ ] `tests/unit/test_agent_session_health_monitor.py` — UPDATE: add cases for the never-started branch (recover shortly after 300s when no turn produced); verify existing no-output-budget cases still pass or are re-pinned to the new grace.
- [ ] `tests/unit/test_session_health_inference_removed.py` — UPDATE: this file already covers sub-check B / own-progress gating (#1614). Add the never-started-grace assertions here following its existing pattern; re-verify it does not assume the old 1800s boundary for the never-started case.
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: extend the end-to-end heartbeat-vs-progress scenario to assert a never-started + fresh-heartbeat session recovers on the new grace, and a turn-producing session does not.
- [ ] `tests/unit/test_session_stall_classifier.py` — UPDATE only if the shared constant is introduced in `session_stall_classifier.py`: add a test pinning the new `NEVER_STARTED_RECOVERY_GRACE_SECS` value and its relationship to `NEVER_STARTED_GRACE_SECS`.
- [ ] `tests/integration/test_stall_advisory_e2e.py` — VERIFY (no change expected): the `sys.modules` guard asserting the classifier never imports `session_health` must still hold after the new constant is added. The import direction is `session_health` → classifier, which does not violate the guard. Confirm the test still passes.

No tests are DELETEd — the change is a tightening of an existing branch, not a removal of behavior.

## Rabbit Holes

- **Rewriting the two-tier detector or the reprieve-cap math.** Tempting to "clean up" the `NO_OUTPUT_BUDGET_SECONDS = MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW` derivation. Out of scope — the never-started case is a *separate, tighter* leg; leave the output-then-idle 1800s path alone.
- **Lowering the 300s race-guard floor to match the advisory's 120s.** The 300s guard exists to avoid killing genuinely-fresh sessions and is referenced throughout the loop. Do not touch it; reconcile by acting at the floor, not by lowering it.
- **Giving `stall_advisory` kill powers.** This breaks the classifier's zero-writes/no-session_health-import contract and reintroduces the competing-recoverers race. Explicitly rejected (Q2).
- **Adding a stored `sdk_ever_output` field on `AgentSession`.** It is intentionally derived; storing it invites staleness bugs. Keep deriving it.
- **Tuning the advisory's 120s.** Detection cadence is fine; the bug is the *actor's* 1800s, not the advisory's 120s.

## Risks

### Risk 1: Killing a legitimately-slow-but-priming session
**Impact:** A session doing slow auth or digesting a large initial prompt is recovered mid-priming, wasting work and looking flaky.
**Mitigation:** The new branch only denies the *heartbeat-as-progress* fast-path. Tier-2 reprieve gates (`children`/`alive`) still run and reprieve a session whose SDK subprocess is alive with active children. A session that produces any turn flips `sdk_ever_output=True` and leaves the regime entirely. Regression test (Q4) pins both the recover and the do-not-recover cases.

### Risk 2: Recovery thrash / resurrection loop
**Impact:** A session that wedges immediately on every pickup could be recovered repeatedly.
**Mitigation:** Existing `MAX_RECOVERY_ATTEMPTS` finalizes to `failed` after N attempts (already in `_apply_recovery_transition`); `reprieve_count` reset on recovery is already handled. No new loop is introduced — the never-started case routes through the *same* `reason_kind="no_progress"` transition, inheriting all existing backstops.

### Risk 3: Constant drift reintroduced later
**Impact:** A future edit moves one grace without the other, re-opening the disagreement.
**Mitigation:** Single shared constant (Q3) plus a unit test pinning the relationship between detection grace and recovery grace, so a divergent edit fails CI.

## Race Conditions

### Race 1: Concurrent recovery vs. first turn arriving
**Location:** `agent/session_health.py` `_has_progress` / `_agent_session_health_check` (`:1708-1745`)
**Trigger:** The session emits its first `turn_start` (writing `last_turn_at`) in the same tick the health loop evaluates it.
**Data prerequisite:** `last_turn_at` / `last_tool_use_at` must be readable before `_has_progress` short-circuits sub-check A.
**State prerequisite:** Once `sdk_ever_output=True`, the never-started branch must not fire.
**Mitigation:** `_has_progress` reads `sdk_ever_output` first (sub-check A) and returns True on any fresh per-turn signal before reaching sub-check B. The recovery transition uses CAS (`expected_status="running"`) so a concurrent status change loses safely. Worst case is a single benign re-queue; the resumed session continues from its transcript.

### Race 2: Advisory and actor observing the same session in overlapping windows
**Location:** `reflections/stall_advisory.py` vs. `agent/session_health.py`
**Trigger:** Both reflections run near-simultaneously on the same never-started session.
**Data prerequisite:** None shared for writes — advisory never writes.
**State prerequisite:** Only one writer.
**Mitigation:** Advisory is read-only by contract (Q2). Only `session_health` writes. No double-action possible. (This is the explicit AC: "No double-recovery.")

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. Specifically NOT changing: the 300s race-guard floor, the 1800s output-then-idle path, the advisory's 120s detection grace, and the derived nature of `sdk_ever_output`. These are intentional preservations documented in Rabbit Holes, not deferred work.

## Update System

No update system changes required — this is purely internal worker recovery logic. No new dependencies, config files, env vars, or migration steps. The change ships with the next `/update` pull-and-restart like any code change; the running worker picks up the new grace on restart (`./scripts/valor-service.sh worker-restart`).

## Agent Integration

No agent integration required — this is a worker-internal change to recovery logic. No new CLI entry point, no MCP tool, no bridge import. The behavior is observable to operators via existing surfaces: the `[session-health] Recovering session ...` log line, the project-scoped Redis recovery counters (`{project_key}:session-health:recoveries:no_progress` and the new `tier1_falloff:never_started_grace_exceeded`), and the unchanged stall-advisory log/Telegram alert.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the session-health/recovery doc, e.g. `docs/features/bridge-self-healing.md`) to describe the never-started recovery grace and how it relates to the advisory's detection grace. Locate the canonical recovery doc during build (`grep -rl "NO_OUTPUT_BUDGET\|_has_progress\|sub-check B" docs/`).
- [ ] If a dedicated doc exists for #1356/#1614 (the two-tier detector), append the never-started leg there.

### Inline Documentation
- [ ] Update the `_has_progress` docstring's four-leg description in `session_health.py` to add the never-started leg.
- [ ] Add a comment on the new shared constant tying it to `NEVER_STARTED_GRACE_SECS` and the 300s floor, so the deliberate two-value relationship is visible at the definition site.

## Success Criteria

- [ ] A `running` session with `last_turn_at = None` and fresh heartbeats is recovered shortly after the recovery grace (at/just past the 300s floor), not after 30 min.
- [ ] The advisory's detection grace and the recovery actor's never-started grace derive from a single shared constant; a unit test pins their relationship so silent drift fails CI.
- [ ] A session that produces a turn within the normal startup window (or has a live SDK child) is NOT recovered by the new branch — regression test included and green.
- [ ] No double-recovery: advisory stays read-only; only `session_health` writes. Asserted by the unchanged `test_stall_advisory_e2e.py` import guard.
- [ ] A new distinct Redis counter (`tier1_falloff:never_started_grace_exceeded`) distinguishes never-started fall-through from the 1800s output-budget fall-through on dashboards.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `session_health.py` imports the new constant from `session_stall_classifier.py` (and NOT vice versa).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (recovery-logic)**
  - Name: recovery-builder
  - Role: Implement the never-started branch in `_has_progress` sub-check B, the shared constant, the import, and the new Redis counter.
  - Agent Type: debugging-specialist
  - Resume: true

- **Test Engineer (regression)**
  - Name: regression-tester
  - Role: Author the recover/do-not-recover regression tests and the constant-relationship pin test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (recovery-logic)**
  - Name: recovery-validator
  - Role: Verify the slow-priming session is not killed, the import direction is correct, and the advisory remains read-only.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update the recovery feature doc and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Introduce the shared never-started recovery grace constant
- **Task ID**: build-shared-constant
- **Depends On**: none
- **Validates**: tests/unit/test_session_stall_classifier.py
- **Assigned To**: recovery-builder
- **Agent Type**: debugging-specialist
- **Parallel**: false
- Add `NEVER_STARTED_RECOVERY_GRACE_SECS` to `agent/session_stall_classifier.py` next to `NEVER_STARTED_GRACE_SECS`, with a comment documenting the two-value relationship (detection 120s vs. action at the 300s floor).
- Confirm the value default and env-tunability stance match the team decision from Open Questions.

### 2. Add the never-started branch to sub-check B
- **Task ID**: build-subcheck-b
- **Depends On**: build-shared-constant
- **Validates**: tests/unit/test_agent_session_health_monitor.py, tests/unit/test_session_health_inference_removed.py
- **Informed By**: recon (300s race-guard floor is the hard minimum; sub-check A must still short-circuit on sdk_ever_output)
- **Assigned To**: recovery-builder
- **Agent Type**: debugging-specialist
- **Parallel**: false
- Import the constant into `session_health.py` (direction: session_health → classifier only).
- In sub-check B, when `not sdk_ever_output` and never-started, gate the fresh-heartbeat pass on `running_seconds <= NEVER_STARTED_RECOVERY_GRACE_SECS` instead of `<= NO_OUTPUT_BUDGET_SECONDS`.
- Add the `tier1_falloff:never_started_grace_exceeded` Redis counter on the new fall-through (best-effort, swallow + log on failure).
- Update the `_has_progress` docstring four-leg description.

### 3. Regression + constant-pin tests
- **Task ID**: build-tests
- **Depends On**: build-subcheck-b
- **Validates**: tests/unit/test_agent_session_health_monitor.py, tests/integration/test_session_heartbeat_progress.py, tests/unit/test_session_stall_classifier.py
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: never-started + fresh heartbeat → recovered at/just past 300s (NOT 1800s).
- Test: turn-producing session (sdk_ever_output=True) and live-SDK-child session → NOT recovered by the new branch.
- Test: legacy (started_at=None AND created_at=None) and clock-skew (negative running_seconds) preserve the fast-path.
- Test: Redis counter failure does not block the recovery decision.
- Test: constant relationship pinned (detection vs. recovery grace).

### 4. Validate recovery safety + import direction
- **Task ID**: validate-recovery
- **Depends On**: build-tests
- **Assigned To**: recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm slow-priming case not killed; confirm `test_stall_advisory_e2e.py` import guard still green; confirm import direction via grep.
- Run targeted tests; report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-recovery
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update the canonical recovery feature doc and the `_has_progress` docstring; add the constant comment.
- Add/update the docs index entry if applicable.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full targeted test set + ruff; verify all Success Criteria including docs and import-direction grep.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Stall classifier tests | `pytest tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| Health monitor tests | `pytest tests/unit/test_agent_session_health_monitor.py tests/unit/test_session_health_inference_removed.py -q` | exit code 0 |
| Heartbeat-progress integration | `pytest tests/integration/test_session_heartbeat_progress.py -q` | exit code 0 |
| Advisory import guard | `pytest tests/integration/test_stall_advisory_e2e.py -q` | exit code 0 |
| Import direction (no reverse import) | `grep -n "import session_health\|from agent.session_health" agent/session_stall_classifier.py` | exit code 1 |
| Constant imported correctly | `grep -n "NEVER_STARTED_RECOVERY_GRACE_SECS" agent/session_health.py` | output contains NEVER_STARTED_RECOVERY_GRACE_SECS |
| Lint clean | `python -m ruff check agent/session_health.py agent/session_stall_classifier.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/session_stall_classifier.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **(Q1 confirmation)** Confirm the recovery actor (`session_health`) owns the escalation via a tighter never-started branch in sub-check B — rather than giving the advisory write powers. The plan assumes yes.
2. **(Q3 — constant home and value)** Where should the shared `NEVER_STARTED_RECOVERY_GRACE_SECS` live, and what value? The plan proposes `agent/session_stall_classifier.py` (next to `NEVER_STARTED_GRACE_SECS`), valued at the 300s race-guard floor (`= STARTUP_GRACE_SECONDS`). Acceptable, or prefer a small explicit multiple of the detection grace (e.g. 2× 120s = 240s, clamped up to the 300s floor) with the floor enforced in `session_health`? Should it be env-tunable like the neighboring constants?
3. **(Recovery timescale)** Given the 300s race-guard floor cannot be lowered without broader risk, is "recovered at ~300-360s instead of 1800s" an acceptable realization of the AC "well under 1800s"? If a sub-300s recovery is required, that needs a separate decision to lower or special-case the race guard (explicitly a rabbit hole here).
