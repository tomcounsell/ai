---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1740
last_comment_id:
---

# Granite Real-Loop Test Hardening — close the gap that let the canned-fallback regression ship silently

## Problem

The canned-fallback regression (#1719) shipped silently because the granite test coverage cannot catch this class of bug:

- `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — the only real-loop test — is `@skipUnless(_model_reachable)` (gated on `claude --print ping`). When the model is unreachable it **silently skips → green**.
- `tests/integration/test_granite_pty_production.py` uses a mocked PTY emulator that **emits compliant routing prefixes by construction**, so it can never reproduce a prefix-less / floor-delivery turn.
- There is **no nightly regression schedule** on the bridge machine, so even the real-loop test never runs unattended.

**Desired outcome:** A regression in granite's user-facing delivery (prefix decay, floor not firing, canned fallback on a non-empty turn) is caught by a test that actually runs — and a skipped real-loop test is *visible*, not silently green.

## Freshness Check

*Baseline: `main` @ `a3e63772` (post-#1719 merge), 2026-06-19. Re-verified during pre-plan recon — see issue #1740 `## Recon Summary`.*

| Reference | Disposition | Notes |
|---|---|---|
| `container.py:1235` `_successful_exits` (incl. `pm_max_turns`, `pm_floor_delivered`) | **Major drift (scope corrected)** | The literal issue instruction to "drop `pm_max_turns`" would regress #1719 — this set is the **wrap-up-guard trigger**, not a success classifier. Part (1) reframed (see Solution). Confirmed with operator before planning. |
| `session_executor.py:35` `_CLEAN_GRANITE_EXIT_REASONS` (excludes `pm_max_turns`) | **Unchanged** | The reaction/telemetry "clean" set already excludes `pm_max_turns`. The "not treated as success" concern is already satisfied here. |
| `test_granite_container_loop.py` `@skipUnless(_model_reachable)` | **Unchanged** | Still silently skips on `claude --print ping` failure. |
| `test_granite_pty_production.py` mocked PTY | **Unchanged** | Still emits compliant prefixes by construction. |
| #1719 (PR #1743) | **Merged `a3e63772`** | Added `pm_floor_delivered` clean exit + `PM_TURN_CONTRACT_REMINDER`. This plan's tests must cover the floor path it introduced. |

No active plan in `docs/plans/` overlaps this surface. This is a `chore` (test/CI hardening), not a bug fix.

## Prior Art

- **#1719 / PR #1743 (`a3e63772`)** — the regression this hardening backstops. Added `pm_floor_delivered` and the per-turn prefix reminder.
- **`docs/features/nightly-regression-tests.md`** — existing nightly framework (`scripts/nightly_regression_tests.py`, `scripts/install_nightly_tests.sh`, launchd plist). This plan schedules it on the bridge machine and adds skip-visibility.
- **`docs/features/test-coverage-standards.md`** — the silent-skip-as-green pattern is exactly the "skipped test masquerading as passing" failure class catalogued there.

## Research

No relevant external findings — purely internal test/CI/launchd hardening. No external libraries or APIs. Proceeding with codebase context.

## Architectural Impact

Three independent, low-coupling changes: (1) a rename + new test assertions in the container test module; (2) a real-loop assertion extension that runs only when `_model_reachable`; (3) a skip-visibility shim + a launchd schedule install wired into the update system. No production runtime behavior changes (the rename is cosmetic; `pm_max_turns` stays in the gate). Only test/CI/infra surfaces are touched.

## Appetite

**Medium.** Three loosely-coupled hardening items. No production routing change (explicitly — see Solution part 1). The launchd schedule + the `claude --print ping`-gated real-loop assertion are bridge-machine build-time items.

## Prerequisites

- Build's bridge-machine items (real-loop assertion validation, nightly launchd install) run on the bridge host. The rename + unit-level assertions + skip-visibility shim can be authored and validated on the skills-only machine.

## Solution

### Key Elements

1. **Part 1 — rename for clarity + hard-failure TEST assertion (NOT a runtime change).**
   - Rename `_successful_exits` → `_wrapup_eligible_exits` in `container.py:1235` (with a one-line comment: "exits eligible for the wrap-up guard — NOT a success classifier; see `_CLEAN_GRANITE_EXIT_REASONS` in session_executor for reaction cleanliness"). `pm_max_turns` **stays** — removing it regresses #1719's floor delivery.
   - Add test assertions (unit + mocked-PTY): after `_run_wrapup_guard` runs on a wrap-up-eligible exit, `result.user_facing_routed is True` and the delivered message is non-canned (unless the transcript was genuinely empty, in which case `OPERATOR_TERMINAL_MESSAGE` + `pm_no_user_message` is the *only* allowed outcome). This is the real hardening: a wrap-up-eligible exit that ends `user_facing_routed=False` is a test failure.

2. **Part 2 — env-gated real-loop assertion covering `pm_floor_delivered`.**
   - Extend `test_granite_container_loop.py::test_cli_short_run_produces_results_json` (the `_model_reachable` path) to assert the run delivers a **non-empty, non-canned** user-facing message regardless of `exit_reason`, and add a case that exercises the `pm_floor_delivered` path (prefix-less wrap-up → floor delivery).

3. **Part 3 — skip-visibility + nightly schedule (bridge machine).**
   - Make a skipped real-loop test **alert** rather than silently pass: emit a structured WARNING / record a skip marker the nightly run surfaces (so a skip reads as "needs attention," not "passed").
   - Install the nightly regression launchd schedule on the bridge machine (`scripts/install_nightly_tests.sh`) and wire it into the update system so it propagates.

### Flow

After this plan: the container test module hard-fails if any wrap-up-eligible exit ends undelivered; the real-loop test (when `claude --print ping` works) asserts a genuine non-canned round-trip including the floor path; and the nightly run on the bridge machine runs the real-loop test unattended, surfacing skips as alerts.

### Technical Approach

- **Rename**: `container.py:1235` `_successful_exits` → `_wrapup_eligible_exits`; update the single reference at `:1236`. Pure rename — no membership change.
- **Container test assertions**: in `tests/unit/granite_container/test_container.py` (and/or `test_wrapup_guard_floor.py`), parametrize the wrap-up-eligible exit reasons (`pm_complete`, `pm_user`, `pm_max_turns`, `pm_floor_delivered`) and assert post-guard `user_facing_routed is True`; assert canned only on empty transcript.
- **Real-loop assertion**: gate the new assertion behind the existing `_model_reachable` check; on the reachable path, parse `results.json` and assert the user-facing message is non-empty and `!= OPERATOR_TERMINAL_MESSAGE`.
- **Skip visibility**: replace the bare `@skipUnless` reason with a path that records a skip into the nightly report (e.g. write a skip marker file the nightly script greps, or use a custom skip that the nightly harness counts and alerts on). Exact mechanism resolved by spike-1.
- **Nightly schedule**: `scripts/install_nightly_tests.sh` install on the bridge machine + an `/update` step (see Update System) so it is idempotently (re)installed.

## Spike Results

### spike-1 (DEFERRED TO BUILD — bridge machine): skip-visibility mechanism
- **Assumption**: "The nightly harness (`scripts/nightly_regression_tests.py`) can surface a skipped real-loop test as an alert (not a silent pass)."
- **Method**: code-read of `scripts/nightly_regression_tests.py` + the pytest json-report plugin output on the bridge machine.
- **Why deferred**: the nightly harness runs on the bridge machine; the cleanest skip-visibility hook depends on how that script parses results. Resolve before implementing Part 3's alert shim.
- **Impact if false**: if the harness can't surface skips, fall back to a standalone check that fails when the real-loop test reports `skipped` in a model-reachable environment.

## Failure Path Test Strategy

### Exception Handling Coverage
- Real-loop test when `claude --print ping` raises/times out: the `_model_reachable` gate already handles this; the new skip-visibility path must record the skip rather than swallow it.

### Empty/Invalid Input Handling
- Genuinely empty PM transcript on a wrap-up-eligible exit: the *only* case where `OPERATOR_TERMINAL_MESSAGE` + `pm_no_user_message` is allowed. Asserted explicitly so the canned path stays legitimate.

### Error State Rendering
- A wrap-up-eligible exit ending `user_facing_routed=False`: now a hard test failure (the assertion this plan adds).

## Test Impact
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: add parametrized assertions that every `_wrapup_eligible_exits` reason ends `user_facing_routed=True` post-guard; canned only on empty transcript. Update any reference to the old `_successful_exits` name.
- [ ] `tests/unit/granite_container/test_wrapup_guard_floor.py` — UPDATE: extend floor coverage to assert the hard-failure invariant.
- [ ] `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — UPDATE: add non-empty/non-canned assertion on the `_model_reachable` path + `pm_floor_delivered` coverage; replace silent skip with skip-visibility.
- [ ] `tests/integration/test_granite_pty_production.py` — UPDATE: add a mocked-PTY variant with a prefix-less PM final message to exercise the floor (complements the real-loop test; the mock alone is insufficient but should still cover the branch).
- [ ] No existing test asserts the old `_successful_exits` name as a string — rename is safe; grep to confirm at build.

## Rabbit Holes

- **Do NOT remove `pm_max_turns` from the wrap-up gate.** It is the trigger for the dominant failure mode's floor delivery; removal regresses #1719. (This is the corrected scope.)
- **Do NOT rely on the mocked-PTY test as the regression guard.** It emits compliant prefixes by construction — the real-loop test is the actual guard.
- **Do NOT make the real-loop test a hard CI dependency on `claude --print ping`.** Keep it env-gated; the hardening is *visibility of the skip* + nightly execution, not forcing it in every CI run.

## Risks

### Risk 1: Rename misses a reference
A stray reference to `_successful_exits` breaks at runtime. Mitigation: it is a module-local name with a single use site (`:1236`); grep before/after.

### Risk 2: Skip-visibility shim itself becomes noisy
Alerting on every skip in a model-unreachable dev environment would be noise. Mitigation: only alert on skip when the environment *should* be model-reachable (i.e. on the bridge machine / nightly context), not in arbitrary local runs.

## Race Conditions

No race conditions — this is test/CI/infra hardening with no concurrent runtime state changes.

## No-Gos (Out of Scope)

- Any production routing/runtime behavior change to the granite container (the rename is cosmetic; `pm_max_turns` membership is unchanged).
- Re-litigating #1719's floor design — this plan only backstops it with tests.
- Forcing the real-loop test into the synchronous CI gate (it stays env-gated; nightly + skip-visibility is the mechanism).

## Update System

**Update system changes ARE required** for Part 3. The nightly regression launchd schedule must be installed on the bridge machine and stay installed across updates:
- [ ] Wire `scripts/install_nightly_tests.sh` into the update orchestrator (`scripts/update/run.py`) as an idempotent, machine-gated install step (bridge machine only), mirroring how `install_worker.sh` / `install_reflections.sh` are invoked.
- [ ] Ensure the install script substitutes `__PROJECT_DIR__` / `__HOME_DIR__` placeholders (per the existing launchd install pattern) so it is machine-portable.
- [ ] No new dependency or secret is introduced — the nightly script and pytest already exist.

## Agent Integration

No agent integration required — this is test/CI/infra hardening. No new CLI entry point in `pyproject.toml` and no bridge import. The nightly schedule invokes the existing `scripts/nightly_regression_tests.py`; the agent reaches nothing new.

## Documentation

- [ ] Update `docs/features/nightly-regression-tests.md` — document the granite real-loop test as part of the nightly suite, the skip-visibility behavior, and the bridge-machine install via `/update`.
- [ ] Update `docs/features/granite-pty-production.md` (or `granite-interactive-tui.md`) — note the `_successful_exits` → `_wrapup_eligible_exits` rename and clarify it is the wrap-up trigger, distinct from `_CLEAN_GRANITE_EXIT_REASONS`.

## Success Criteria

- A wrap-up-eligible exit (`pm_complete`/`pm_user`/`pm_max_turns`/`pm_floor_delivered`) that ends `user_facing_routed=False` is a **hard test failure** (unit/mocked-PTY).
- The real-loop test (when `_model_reachable`) asserts a non-empty, non-canned user-facing message and covers the `pm_floor_delivered` path.
- A skipped real-loop test in a should-be-reachable context produces an **alert**, not a silent green.
- The nightly regression schedule is installed on the bridge machine and re-installed idempotently by `/update`.
- `pm_max_turns` remains in the (renamed) wrap-up gate — no regression to #1719's floor delivery.

## Step by Step Tasks

### 1. spike-1 (bridge machine): skip-visibility mechanism
Read `scripts/nightly_regression_tests.py` + json-report output; pick the alert-on-skip mechanism.

### 2. Rename `_successful_exits` → `_wrapup_eligible_exits`
Pure rename at `container.py:1235`/`:1236` + clarifying comment. Grep for any other references.

### 3. Container hard-failure test assertions
Parametrize wrap-up-eligible exits; assert post-guard `user_facing_routed=True`; canned only on empty transcript. Update `test_container.py` + `test_wrapup_guard_floor.py`.

### 4. Real-loop assertion + floor coverage
Extend `test_granite_container_loop.py` (`_model_reachable` path): non-empty/non-canned assertion + `pm_floor_delivered` case. Add mocked-PTY prefix-less variant to `test_granite_pty_production.py`.

### 5. Skip-visibility shim (bridge machine)
Implement the alert-on-skip from spike-1.

### 6. Nightly schedule + update wiring (bridge machine)
Install via `scripts/install_nightly_tests.sh`; add the idempotent, machine-gated `/update` step.

### 7. Documentation
Update `nightly-regression-tests.md` + granite exit-reason docs.

### 8. Final Validation
Unit + mocked-PTY green on this machine; on the bridge machine, run the real-loop test and confirm the nightly schedule is installed and surfaces a skip as an alert.

## Verification

- `scripts/pytest-clean.sh tests/unit/granite_container tests/integration/test_granite_pty_production.py -v` → green (skills machine).
- Bridge machine: real-loop test runs and asserts non-canned delivery; `launchctl list | grep nightly` shows the schedule; a forced skip surfaces an alert in `logs/nightly_tests.log`.

## Open Questions

1. **spike-1 (build-time, bridge machine):** exact skip-visibility mechanism — does `scripts/nightly_regression_tests.py` already parse `skipped` counts (so we hook there), or do we need a standalone "should-be-reachable but skipped" check? Cannot be resolved on the skills-only machine.
