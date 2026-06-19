---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1740
last_comment_id:
revision_applied: true
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
| `agent/granite_container/container.py:1235` `_successful_exits` (incl. `pm_max_turns`, `pm_floor_delivered`) | **Major drift (scope corrected)** | The literal issue instruction to "drop `pm_max_turns`" would regress #1719 — this set is the **wrap-up-guard trigger**, not a success classifier. Part (1) reframed (see Solution); the rename itself is now optional per critique NIT. Confirmed with operator before planning. Path corrected from the issue's bare `container.py`. |
| `scripts/nightly_regression_tests.py:70` `run_tests` hardcodes `tests/unit/` | **Drift (blocker found at critique)** | The nightly run never collects the real-loop integration test — installing the schedule alone is inert. Part 3a adds a second isolated integration invocation. Verified `:77` still targets only `tests/unit/`. |
| `agent/granite_container/container.py:1566`/`:1598` `if self._on_user_payload is not None:` | **New finding (CONCERN 2)** | `user_facing_routed=True` is only set inside these guards — the invariant must be scoped to callback-present cases. Verified at plan revision. |
| `tests/integration/test_granite_container_loop.py:165-175` exit-reason whitelist | **New finding (CONCERN 3)** | Omits `pm_floor_delivered`/`pm_user`; new assertions must guard on `_CLEAN_GRANITE_EXIT_REASONS`. Verified at plan revision. |
| `tests/unit/granite_container/test_wrapup_guard_floor.py` | **New finding (CONCERN 1)** | File does not exist — Test Impact corrected from UPDATE to CREATE. |
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

1. **Part 1 — (optional) rename for clarity + hard-failure TEST assertion (NOT a runtime change).**
   - *(Optional, per critique NIT)* Rename `_successful_exits` → `_wrapup_eligible_exits` in `agent/granite_container/container.py:1235` (with a one-line comment: "exits eligible for the wrap-up guard — NOT a success classifier; see `_CLEAN_GRANITE_EXIT_REASONS` in session_executor for reaction cleanliness"). The rename adds review surface for no safety gain — keep it only if it materially aids clarity. `pm_max_turns` **stays** regardless — removing it regresses #1719's floor delivery.
   - Add test assertions (unit + mocked-PTY): after `_run_wrapup_guard` runs on a wrap-up-eligible exit **with an `on_user_payload` callback present**, `result.user_facing_routed is True` and the delivered message is non-canned (unless the transcript was genuinely empty, in which case `OPERATOR_TERMINAL_MESSAGE` + `pm_no_user_message` is the *only* allowed outcome). This is the real hardening: a wrap-up-eligible exit that ends `user_facing_routed=False` (with a callback present) is a test failure. **CONCERN 2:** without a callback the container's guards (`:1566`/`:1598`) cannot route, so callback-less cases are exempt from the invariant.

2. **Part 2 — env-gated real-loop assertion covering `pm_floor_delivered`.**
   - Extend `test_granite_container_loop.py::test_cli_short_run_produces_results_json` (the `_model_reachable` path) to assert the run delivers a **non-empty, non-canned** user-facing message regardless of `exit_reason`, and add a case that exercises the `pm_floor_delivered` path (prefix-less wrap-up → floor delivery).

3. **Part 3 — nightly harness collects the real-loop test + skip-visibility + schedule (bridge machine).**
   - **(3a) BLOCKER FIX — the nightly harness must actually run the real-loop test.** Today `scripts/nightly_regression_tests.py::run_tests` (line ~70) hardcodes `pytest tests/unit/` as its *sole* target. The only real-loop test lives in `tests/integration/test_granite_container_loop.py`, which is **never collected** by the nightly run — so installing the schedule (Part 3c) does nothing to exercise it, recreating the original silent-skip-as-green failure at the harness layer. **Fix:** add a *second, isolated* `subprocess.run` invocation in the nightly runner that targets the integration real-loop test specifically (`pytest tests/integration/test_granite_container_loop.py --json-report ...`), kept separate from the unit-suite run (its own JSON report file, its own delta/alert bookkeeping) so the two suites do not contaminate each other's baselines. A new failure (or a should-be-reachable skip — see 3b) in the integration run produces a Telegram regression alert exactly like the unit run does.
   - **(3b) skip-visibility.** Make a skipped real-loop test **alert** rather than silently pass: in a should-be-reachable context (bridge/nightly), a `skipped` result for the real-loop test is surfaced as an alert (not counted as a pass). Because 3a runs the integration test through the json-report harness, the harness already sees the `skipped` outcome in the report `summary` — the skip-visibility logic hooks the integration-run summary's `skipped` count and alerts when it is non-zero in the nightly (should-be-reachable) context.
   - **(3c) schedule install.** Install the nightly regression launchd schedule on the bridge machine (`scripts/install_nightly_tests.sh`) and wire it into the update system so it propagates.

### Flow

After this plan: the container test module hard-fails if any wrap-up-eligible exit ends undelivered; the real-loop test (when `claude --print ping` works) asserts a genuine non-canned round-trip including the floor path; and the nightly run on the bridge machine runs the real-loop test unattended, surfacing skips as alerts.

**File-path note:** the granite container lives at `agent/granite_container/container.py` (the issue's bare `container.py:NNNN` references resolve here). The `_successful_exits` literal is at `agent/granite_container/container.py:1235`, used once at `:1236`.

- **Rename** *(optional per critique NIT — keep only if it materially aids clarity; the safety win is in the test assertions, not the rename)*: `agent/granite_container/container.py:1235` `_successful_exits` → `_wrapup_eligible_exits`; update the single reference at `:1236` and the comment at `:1234`. Pure rename — no membership change. `pm_max_turns` stays.
- **Container test assertions**: in `tests/unit/granite_container/test_container.py` (existing `TestWrapupGuard` class, ~line 1097) and a **new** `tests/unit/granite_container/test_wrapup_guard_floor.py`, parametrize the wrap-up-eligible exit reasons (`pm_complete`, `pm_user`, `pm_max_turns`, `pm_floor_delivered`) and assert post-guard `user_facing_routed is True` — **only in cases where `on_user_payload` is not None**. See the CONCERN-2 invariant note below: when `_on_user_payload is None`, the container's own guards at `:1566`/`:1598` cannot set `user_facing_routed=True`, so the invariant does not hold and asserting it would false-fail callback-less tests. Assert canned (`OPERATOR_TERMINAL_MESSAGE` + `pm_no_user_message`) only on a genuinely empty transcript. **Build must first audit whether the existing `TestWrapupGuard` coverage (test_container.py ~lines 1097-1480, which already asserts `user_facing_routed` and the `pm_floor_delivered`/`pm_no_user_message` paths) already satisfies Part 1** — if so, the new file only needs to add the *parametrized-across-all-eligible-reasons* invariant that is not yet present, not duplicate existing cases.
- **CONCERN-2 invariant scope (load-bearing):** the central invariant "wrap-up-eligible exit ⇒ `user_facing_routed=True` post-guard" holds **only when `_on_user_payload is not None`**. At `agent/granite_container/container.py:1566` and `:1598` the assignment `result.user_facing_routed = True` is inside `if self._on_user_payload is not None:`. A test built with no `on_user_payload` callback can legitimately end `user_facing_routed=False`. Every new assertion of this invariant must be guarded `if c._on_user_payload is not None:` (or simply only built with a callback present).
- **Real-loop assertion**: gate the new assertion behind the existing `_model_reachable` check; on the reachable path, parse `results.json` and assert the user-facing message is non-empty and `!= OPERATOR_TERMINAL_MESSAGE`.
- **CONCERN-3 exit-reason whitelist**: the real-loop test's current exit-reason whitelist (`test_granite_container_loop.py:165-175`) omits `pm_floor_delivered` and `pm_user`. Any new floor-delivery / non-canned assertion must guard on the **shared** `_CLEAN_GRANITE_EXIT_REASONS` (`agent/session_executor.py:35` = `{"pm_complete", "pm_user", "pm_floor_delivered"}`) rather than the narrow local whitelist — import it (or assert the broader set) so a legitimate `pm_floor_delivered`/`pm_user` real-loop exit is not flagged as failure. Also extend the existing local whitelist at `:165-175` to include `pm_floor_delivered` and `pm_user` so the pre-existing shape assertion stays consistent.
- **Nightly harness wiring (BLOCKER, plan-correctness — MUST land):** add a second `subprocess.run` in `scripts/nightly_regression_tests.py` targeting `tests/integration/test_granite_container_loop.py` with its own `--json-report-file` (e.g. `/tmp/nightly_granite_realloop_report.json`), its own last-run state key, and its own delta/alert path mirroring the existing unit-suite flow. Surface integration `failed`/`error` (and, in should-be-reachable context, `skipped`) as a Telegram regression alert. Keep the two reports isolated so the unit baseline is never polluted by integration outcomes.
- **Skip visibility**: with the integration test now collected through the json-report harness (above), hook the integration run's `summary.skipped` count; in the bridge/nightly (should-be-reachable) context a non-zero skip raises an alert instead of being silently counted as a pass. Exact wiring (does the existing report `summary` carry `skipped`, or is a standalone "should-be-reachable but skipped" check needed) resolved by spike-1.
- **Nightly schedule**: `scripts/install_nightly_tests.sh` install on the bridge machine + an `/update` step (see Update System) so it is idempotently (re)installed.

## Spike Results

### spike-1 (DEFERRED TO BUILD — bridge machine): skip-visibility mechanism on the integration run
- **Assumption**: "Once the nightly harness collects `test_granite_container_loop.py` through `--json-report` (Part 3a), the report `summary` carries a `skipped` count the harness can surface as an alert (not a silent pass)."
- **Method**: code-read of the extended `scripts/nightly_regression_tests.py` integration-run path + the pytest json-report plugin `summary` output on the bridge machine.
- **Why deferred**: the nightly harness runs on the bridge machine; the cleanest skip-visibility hook depends on the json-report `summary` shape for the integration invocation. Resolve before implementing Part 3b's alert.
- **Impact if false**: if the report `summary` doesn't carry `skipped`, fall back to a standalone check that fails when the real-loop test reports `skipped` in a should-be-reachable environment.
- **Note**: the harness-wiring fix (Part 3a) is **not** a spike — it is plan-required correctness and lands regardless of spike outcome. The spike only resolves the *skip alert* wiring on top of it.

## Failure Path Test Strategy

### Exception Handling Coverage
- Real-loop test when `claude --print ping` raises/times out: the `_model_reachable` gate already handles this; the new skip-visibility path must record the skip rather than swallow it.

### Empty/Invalid Input Handling
- Genuinely empty PM transcript on a wrap-up-eligible exit: the *only* case where `OPERATOR_TERMINAL_MESSAGE` + `pm_no_user_message` is allowed. Asserted explicitly so the canned path stays legitimate.

### Error State Rendering
- A wrap-up-eligible exit ending `user_facing_routed=False`: now a hard test failure (the assertion this plan adds).

## Test Impact
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: add the parametrized invariant that every wrap-up-eligible exit reason ends `user_facing_routed=True` post-guard **(guarded to `on_user_payload`-is-not-None cases per CONCERN 2)**; canned only on empty transcript. The existing `TestWrapupGuard` class (~line 1097) already covers several individual paths — build **must audit whether it already satisfies Part 1** before adding the new file, and avoid duplicating existing cases. Update any reference to the old `_successful_exits` name only if the rename (optional NIT) is performed.
- [ ] `tests/unit/granite_container/test_wrapup_guard_floor.py` — **CREATE** (file does not exist today — confirmed via `ls tests/unit/granite_container/`): house the parametrized-across-all-eligible-reasons invariant if it is not already satisfied by the existing `TestWrapupGuard` audit above. Assertions guarded to `on_user_payload`-is-not-None cases.
- [ ] `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — UPDATE: extend the exit-reason whitelist (`:165-175`) to include `pm_floor_delivered` and `pm_user`; add non-empty/non-canned assertion on the `_model_reachable` path guarded by `_CLEAN_GRANITE_EXIT_REASONS` (CONCERN 3); add `pm_floor_delivered` coverage; replace silent skip with skip-visibility.
- [ ] `tests/integration/test_granite_pty_production.py` — UPDATE: add a mocked-PTY variant with a prefix-less PM final message to exercise the floor (complements the real-loop test; the mock alone is insufficient but should still cover the branch).
- [ ] `scripts/nightly_regression_tests.py` — UPDATE (BLOCKER fix, Part 3a): add a second isolated integration-test `subprocess.run` so the real-loop test is actually collected by the nightly run, with its own json-report file + last-run state + alert path. **No existing test asserts the harness only runs `tests/unit/`**, but verify no test pins the single-`run_tests` shape before splitting it.
- [ ] No existing test asserts the old `_successful_exits` name as a string — rename (if performed) is safe; grep to confirm at build.

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
- [ ] **The harness-wiring fix (Part 3a) ships as a code change to `scripts/nightly_regression_tests.py` and propagates via the normal `git pull` step of `/update`** — no separate update-system wiring is needed for the harness change itself; it travels with the repo. (Called out here so it is not mistaken for a bridge-machine-only build step: the harness fix is plain repo code and lands on every machine, even though the *schedule that invokes it* is bridge-gated.)
- [ ] Wire `scripts/install_nightly_tests.sh` into the update orchestrator (`scripts/update/run.py`) as an idempotent, machine-gated install step (bridge machine only), mirroring how `install_worker.sh` / `install_reflections.sh` are invoked.
- [ ] Ensure the install script substitutes `__PROJECT_DIR__` / `__HOME_DIR__` placeholders (per the existing launchd install pattern) so it is machine-portable.
- [ ] No new dependency or secret is introduced — the nightly script and pytest already exist.

## Agent Integration

No agent integration required — this is test/CI/infra hardening. No new CLI entry point in `pyproject.toml` and no bridge import. The nightly schedule invokes the existing `scripts/nightly_regression_tests.py`; the agent reaches nothing new.

## Documentation

- [ ] Update `docs/features/nightly-regression-tests.md` — document the granite real-loop test as part of the nightly suite, the skip-visibility behavior, and the bridge-machine install via `/update`.
- [ ] Update `docs/features/granite-pty-production.md` (or `granite-interactive-tui.md`) — note the `_successful_exits` → `_wrapup_eligible_exits` rename and clarify it is the wrap-up trigger, distinct from `_CLEAN_GRANITE_EXIT_REASONS`.

## Success Criteria

- A wrap-up-eligible exit (`pm_complete`/`pm_user`/`pm_max_turns`/`pm_floor_delivered`) that ends `user_facing_routed=False` **when `on_user_payload` is not None** is a **hard test failure** (unit/mocked-PTY). (Callback-less cases are exempt — the container's own guards cannot route without a callback; see CONCERN-2 invariant note.)
- The real-loop test (when `_model_reachable`) asserts a non-empty, non-canned user-facing message, covers the `pm_floor_delivered` path, and guards its exit-reason check on `_CLEAN_GRANITE_EXIT_REASONS` (not the narrow local whitelist).
- **The nightly harness actually collects and runs `tests/integration/test_granite_container_loop.py` as a second isolated invocation** — verified by reading `scripts/nightly_regression_tests.py` and confirming a deliberate real-loop failure produces a regression alert. (This is the BLOCKER fix; without it the schedule install is inert.)
- A skipped real-loop test in a should-be-reachable context produces an **alert**, not a silent green.
- The nightly regression schedule is installed on the bridge machine and re-installed idempotently by `/update`.
- `pm_max_turns` remains in the wrap-up gate — no regression to #1719's floor delivery.

## Step by Step Tasks

### 1. spike-1 (bridge machine): skip-visibility mechanism on the integration run
Read the extended `scripts/nightly_regression_tests.py` integration-run path + json-report `summary` output; pick the alert-on-skip mechanism. (Depends on task 2.)

### 2. Nightly harness wiring — BLOCKER fix (plan-correctness, lands on every machine)
Add a second isolated `subprocess.run` to `scripts/nightly_regression_tests.py` targeting `tests/integration/test_granite_container_loop.py` with its own `--json-report-file`, last-run state key, and delta/alert path. Keep it isolated from the unit-suite run. This is the fix that makes the schedule install meaningful — without it the real-loop test is never collected. Authorable + unit-testable on this machine (the harness code change is plain repo code).

### 3. (Optional NIT) Rename `_successful_exits` → `_wrapup_eligible_exits`
Pure rename at `agent/granite_container/container.py:1235`/`:1236` + clarifying comment at `:1234`. Grep for any other references. **Keep only if it materially aids clarity** — the safety win is in the test assertions, not the rename. `pm_max_turns` stays regardless.

### 4. Container hard-failure test assertions
First **audit** whether the existing `TestWrapupGuard` (test_container.py ~line 1097-1480) already satisfies Part 1. Then add the parametrized-across-all-eligible-reasons invariant (post-guard `user_facing_routed=True`, **guarded to `on_user_payload`-is-not-None cases** per CONCERN 2; canned only on empty transcript) in `test_container.py` and the **new** `test_wrapup_guard_floor.py`, without duplicating existing cases.

### 5. Real-loop assertion + floor coverage
Extend `test_granite_container_loop.py`: widen the exit-reason whitelist (`:165-175`) to include `pm_floor_delivered`/`pm_user`; on the `_model_reachable` path add a non-empty/non-canned assertion guarded by `_CLEAN_GRANITE_EXIT_REASONS` (`agent/session_executor.py:35`) + a `pm_floor_delivered` case. Add a mocked-PTY prefix-less variant to `test_granite_pty_production.py`.

### 6. Skip-visibility shim (bridge machine)
Implement the alert-on-skip from spike-1, hooking the integration-run json-report `summary.skipped`.

### 7. Nightly schedule + update wiring (bridge machine)
Install via `scripts/install_nightly_tests.sh`; add the idempotent, machine-gated `/update` step.

### 8. Documentation
Update `nightly-regression-tests.md` (note the new integration real-loop invocation + skip-visibility) + granite exit-reason docs.

### 9. Final Validation
Unit + mocked-PTY green on this machine; confirm the harness now collects the integration test (read `scripts/nightly_regression_tests.py`). On the bridge machine: run the real-loop test, confirm the nightly schedule is installed, and confirm a forced skip / deliberate failure surfaces an alert in `logs/nightly_tests.log`.

## Verification

- `scripts/pytest-clean.sh tests/unit/granite_container tests/integration/test_granite_pty_production.py -v` → green (skills machine).
- Harness wiring (skills machine): read `scripts/nightly_regression_tests.py` and confirm a second `subprocess.run` targets `tests/integration/test_granite_container_loop.py` with its own json-report file and alert path.
- Bridge machine: real-loop test runs (collected by the nightly harness) and asserts non-canned delivery; `launchctl list | grep nightly` shows the schedule; a forced skip / deliberate failure surfaces an alert in `logs/nightly_tests.log`.

## Open Questions

1. **spike-1 (build-time, bridge machine):** exact skip-visibility mechanism — once the integration test is collected through `--json-report` (Part 3a), does the report `summary` carry `skipped` counts (so we hook there), or do we need a standalone "should-be-reachable but skipped" check? Cannot be resolved on the skills-only machine. (The harness-wiring fix itself — Part 3a — is *not* an open question; it is required plan-correctness and lands on this machine.)
