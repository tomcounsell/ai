---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1740
last_comment_id: 
---

# Granite Real-Loop Test/CI Hardening

## Problem

A test-coverage gap let the canned-fallback regression (#1719) ship silently. The granite real-loop path — where a non-empty user message must always reach the relay — is only exercised by tests that either (a) silently **skip green** when a model is unreachable, or (b) use a mocked PTY emulator that emits compliant routing prefixes by construction, never touching the no-prefix path. Meanwhile `pm_max_turns` is classified as a "successful" exit inside the container's wrap-up gate, contradicting the executor which already treats it as a non-clean failure. And the nightly regression that would catch this class of regression is not scheduled on the bridge machine.

**Current behavior:**
- `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` is `@skipUnless(_MODEL_REACHABLE)` → silently **skips** → green. No CI/nightly signal that the real-loop test never ran.
- `agent/granite_container/container.py:1235` lists `pm_max_turns` in `_successful_exits`, so the wrap-up salvage guard fires for max-turns exits and papers them over (including with a canned `OPERATOR_TERMINAL_MESSAGE`). The executor (`_CLEAN_GRANITE_EXIT_REASONS`) disagrees — it treats `pm_max_turns` as non-clean.
- No test asserts the core invariant: *a successful-shaped exit must always have delivered a non-empty user-facing message*.
- `scripts/install_nightly_tests.sh` + `com.valor.nightly-tests.plist` exist but the installer is **not machine-gated** and **not wired** into the `/update` install path, so nothing schedules the nightly on the bridge machine.

**Desired outcome:**
- An env-gated **real** test drives the container loop and asserts a non-empty user message reaches the relay regardless of `exit_reason` (covering `pm_floor_delivered`).
- `pm_max_turns` is removed from `_successful_exits`; the container and executor agree that max-turns is a non-clean failure. A test treats a successful-shaped exit (`pm_complete` / `pm_user` / `pm_floor_delivered`) with `user_facing_routed=False` as a **hard failure**.
- The skipped real-loop test **alerts** (emits a visible signal) instead of silently passing.
- The nightly regression installer is machine-gated and wired into the install path so the bridge machine schedules it; non-bridge machines (this skills-only machine) cleanly skip.

## Freshness Check

**Baseline commit:** `996076b8fd833ad76f56c37bca4945fcbfba7627`
**Issue filed at:** `2026-06-19T07:47:39Z`
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/granite_container/container.py:1219` — issue claimed `_successful_exits` with `pm_max_turns` — **drifted to line 1235** (post-#1742/#1745); claim still holds.
- `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — `@skipUnless(_MODEL_REACHABLE)` at line 104, skips green — **confirmed unchanged**.
- `agent/session_executor.py:35` — `_CLEAN_GRANITE_EXIT_REASONS` excludes `pm_max_turns` → non-clean — **confirmed**.
- `scripts/install_nightly_tests.sh` + `com.valor.nightly-tests.plist` — **confirmed present**, installer not machine-gated, not wired into update path.

**Cited sibling issues/PRs re-checked:**
- #1719 — merged (PR #1743). The per-turn prefix-contract reminder + relaxed wrap-up floor landed. `pm_floor_delivered` exists as a clean exit reason.
- #1742 — its comment confirms `pm_max_turns` removal from `container.py` was left open for this issue; `pm_floor_delivered` added to `_CLEAN_GRANITE_EXIT_REASONS`.
- #1745 — merged; added executor-level unit tests for the messageless-session class but no real-loop coverage.

**Commits on main since issue was filed (touching referenced files):** none beyond the #1742/#1745 merges already reflected at baseline.

**Active plans in `docs/plans/` overlapping this area:** `granite_routing_prefix_floor.md` (the #1719 fix, completed). No active overlap — that work is the parent fix; this issue is its scoped-out hardening.

**Notes:** Issue line `1219` corrected to `1235` throughout this plan.

## Prior Art

- **#1719 / PR #1743**: `granite_routing_prefix_floor` — the parent fix (per-turn prefix-contract reminder + relaxed wrap-up floor). Introduced `pm_floor_delivered`. Explicitly scoped OUT the test/CI hardening that this issue now owns.
- **#1647**: introduced the wrap-up guard (`_run_wrapup_guard`) that guarantees the human always receives some output. This is the mechanism whose `_successful_exits` trigger set we are narrowing.
- **#1742**: added `pm_floor_delivered` to `_CLEAN_GRANITE_EXIT_REASONS` and a parametrized executor test over exit reasons; left the `container.py` `_successful_exits` change for this issue.
- **#1745**: added executor-level unit tests for the Fix A/Fix B messageless-session paths — unit-only, no real-loop coverage.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1743 (#1719) | Relaxed wrap-up floor + per-turn prefix reminder | Bounded by design — explicitly deferred the test/CI hardening that would have *caught* the regression before it shipped. |
| PR #1745 | Executor-level unit tests for messageless sessions | Unit-only; the mocked PTY path emits compliant prefixes by construction, so the real no-prefix delivery path stayed uncovered. |

**Root cause pattern:** the only tests touching the real-loop delivery path are gated behind a model-reachability skip that turns green when the model is absent, so the invariant ("a non-empty message always reaches the relay") was never actually asserted under real conditions in CI.

## Data Flow

1. **Entry point**: `valor-granite-loop` CLI (or `Container.run()`) starts a PM+Dev PTY pair with a user message.
2. **Loop**: PM turns are classified; `_route_pm_classification` delivers `[/user]`/`[/complete]` payloads via `_on_user_payload` / `_on_complete_payload`, setting `result.user_facing_routed=True`.
3. **Max-turns fall-through** (`container.py:1219`): if the loop exhausts turns, `result.exit_reason = "pm_max_turns"`.
4. **Wrap-up gate** (`container.py:1235`): `if result.exit_reason in _successful_exits and not result.user_facing_routed → _run_wrapup_guard(result)`. The guard floor-delivers real PM text (→ `pm_floor_delivered`), routes a prefixed message, or sends `OPERATOR_TERMINAL_MESSAGE` (→ `pm_no_user_message`); all set `user_facing_routed=True`.
5. **Executor** (`session_executor.py:1997`): on a non-clean exit_reason (`_is_non_clean_granite_exit`), sets `REACTION_ERROR`. No auto-resume.
6. **Output**: the relay (BridgeAdapter `_on_user_payload`) publishes the user-facing message; `user_facing_routed` reflects whether delivery succeeded.

The hardening targets steps 3–4 (narrow the `_successful_exits` trigger set) and adds a real test over the full path (steps 1–6).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the `pm_max_turns` behavior decision is load-bearing)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pytest-json-report` (for nightly installer) | `.venv/bin/python -m pytest --json-report --help >/dev/null 2>&1 && echo ok` | Nightly regression JSON report; already pre-checked by `install_nightly_tests.sh` |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_real_loop_test_ci_hardening.md`

## Solution

### Key Elements

- **Real-loop relay-delivery test**: a new env-gated test (same `_MODEL_REACHABLE` gate) that runs the container loop with a captured `on_user_payload`/`on_complete_payload` callback and asserts a **non-empty** payload was delivered AND `user_facing_routed=True`, regardless of which `exit_reason` resulted — explicitly including `pm_floor_delivered`.
- **`_successful_exits` narrowing**: drop `pm_max_turns` from `_successful_exits` in `container.py` so the set becomes `{"pm_complete", "pm_user", "pm_floor_delivered"}`, matching `_CLEAN_GRANITE_EXIT_REASONS`. Update the docstring/comment to reflect that `pm_max_turns` is now a non-clean failure that surfaces to the executor (no canned salvage).
- **Hard-failure invariant test**: a test asserting that any exit in the (narrowed) successful set with `user_facing_routed=False` is a hard failure — codifying the invariant that a successful-shaped exit always delivered a message.
- **Skip-alert visibility**: convert the silent `@skipUnless` skip into a **visible alert** — when `_MODEL_REACHABLE` is False, emit a `warnings.warn(...)` (and/or a stderr marker the nightly runner surfaces) so the skip is loud, not green-and-invisible. The test still skips (cannot run without a model) but the skip is now observable in CI/nightly output.
- **Machine-gated nightly installer**: add a `has_bridge_role`-style gate to `install_nightly_tests.sh` (mirroring `install_email_bridge.sh::has_email_role`) that skips + removes the stale plist on non-bridge machines, and **wire** the installer into the gated `install_service()` path in `valor-service.sh` so `/update` schedules the nightly on the bridge machine automatically.

### Flow

Model unreachable (CI/dev) → real-loop test emits a **loud skip alert** (no longer silent green) → nightly on bridge machine runs it for real → asserts non-empty relay delivery + `user_facing_routed=True` → any successful-shaped exit without delivery fails hard.

### Technical Approach

- **Task 1 (real test)**: Add `test_real_loop_delivers_nonempty_user_message` to `test_granite_container_loop.py` under the same env gate. Drive `Container.run()` directly (not just the CLI) with capturing callbacks so we can assert on the *delivered payload string*, not just the results JSON. Assert: at least one non-empty payload captured; `result.user_facing_routed is True`; if `exit_reason == "pm_floor_delivered"`, the floor-delivered text is non-empty. Keep it best-effort on which exit_reason results, but strict on delivery.
- **Task 2 (production change)**: One-line edit at `container.py:1235` removing `"pm_max_turns"`. Update the surrounding comment and `_run_wrapup_guard` docstring (line ~1498) to drop `pm_max_turns` from the "successful-shaped" enumeration. Add a unit test (mocked container, no model) asserting: (a) a `pm_max_turns` exit no longer triggers `_run_wrapup_guard`; (b) the parametrized invariant — a successful-shaped exit with `user_facing_routed=False` is treated as a hard failure (the test fails if the set ever re-admits a non-delivering exit). Verify no other reference to `_successful_exits` assumes `pm_max_turns` membership.
- **Task 3 (skip alert)**: At module load (where `_MODEL_REACHABLE` is computed), if False, `warnings.warn("granite real-loop tests skipped: model unreachable", RuntimeWarning)`. Keep the existing `RESUME_SKIP model_unreachable` token in the skip reason. Confirm `nightly_regression_tests.py` surfaces warnings/skips in its report (read it; if it filters skips, add a skip-count line to the report).
- **Task 4 (nightly schedule)**: Add `has_bridge_role()` gate to `install_nightly_tests.sh` (copy the `has_email_role` Python heredoc shape, but check `proj.get("telegram")` ownership — nightly runs where the production bridge runs). On non-bridge machines: print skip + remove stale `~/Library/LaunchAgents/<prefix>.nightly-tests.plist`, exit 0. Then call `install_nightly_tests.sh` from `install_service()` in `valor-service.sh` (after `install_update_polling`), so `/update` schedules it. The installer's own gate keeps it safe on this skills-only machine.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_run_wrapup_guard` swallows all exceptions by design (must never crash the run). The new unit test asserts the guard is **not invoked** for `pm_max_turns` rather than testing its internals.
- [ ] `install_nightly_tests.sh` gate: `has_bridge_role` fails open (returns "qualifies") when config is unreadable — mirror `has_email_role`'s safety posture; covered by a shell-level smoke assertion (gate returns 0 when `PROJECTS_CONFIG_PATH` points at a missing file).

### Empty/Invalid Input Handling
- [ ] The real-loop test explicitly asserts the delivered payload is **non-empty** (the exact gap that let the canned-fallback regression through). Empty delivery → hard failure.
- [ ] The invariant test covers `user_facing_routed=False` on a successful-shaped exit (the "message never reached relay" case).

### Error State Rendering
- [ ] Skip-alert: when the model is unreachable, the warning is emitted (observable), not swallowed. Assert the warning fires via `pytest.warns` in a meta-test, or confirm via nightly report line.
- [ ] `pm_max_turns` now renders as `REACTION_ERROR` at the executor (existing behavior, now reached because no salvage masks it) — covered by the existing executor parametrized test (`pm_max_turns` expected non-clean = True).

## Test Impact

- [ ] `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — UPDATE: the `payload["exit_reason"]` allowed-set assertion (lines 165-175) currently lists `pm_max_turns`; keep it (the CLI may still legitimately report `pm_max_turns` now that it's non-clean), but add `pm_floor_delivered` and `pm_no_user_message` to the allowed set so a salvage-path exit doesn't false-fail.
- [ ] `tests/unit/test_session_executor_granite.py::TestExecutor*` (the #1742/#1745 parametrized exit-reason tests) — VERIFY (no change expected): `pm_max_turns` is already expected non-clean=True; confirm narrowing `_successful_exits` in the container doesn't contradict these.
- [ ] `tests/integration/test_granite_pty_production.py` — VERIFY (no change expected): mocked PTY emits compliant prefixes; the new real test covers the path this one cannot. Confirm it still passes unchanged.
- [ ] No existing test asserts `_successful_exits` membership of `pm_max_turns` directly (grep confirms) — so dropping it breaks no current assertion; new tests add the coverage.

## Rabbit Holes

- **Rewriting the mocked PTY emulator** (`test_granite_pty_production.py`) to exercise the no-prefix path. The issue scopes a *real* env-gated test, not a mock rewrite. Leave the mock as-is.
- **Adding a watchdog for the nightly launchd job.** `install_autoexperiment.sh` / `install_nightly_tests.sh` rely on `KeepAlive`/`StartCalendarInterval` alone; do not invent a watchdog.
- **Re-architecting `_successful_exits` into a richer state machine.** The change is a one-element set removal plus comment/docstring updates. Resist generalizing.
- **The #1719 fix itself** (per-turn prefix reminder + relaxed wrap-up floor) — explicitly out of scope.

## Risks

### Risk 1: Dropping `pm_max_turns` removes the wrap-up salvage for legitimate max-turns runs
**Impact:** A run that exhausts max turns but had real PM text to floor-deliver will now surface as `REACTION_ERROR` with no delivered message, instead of being salvaged into `pm_floor_delivered`. For some workloads this means the human sees an error reaction rather than the PM's partial output.
**Mitigation:** This is the issue's explicit intent (#1719 shipped a *canned* fallback silently; making max-turns a visible failure is the goal). The executor already treats `pm_max_turns` as non-clean, so this aligns the two layers. **Surface this as the load-bearing decision for the critique war-room and the PM check-in** before building — if the desired behavior is "still floor-deliver real PM text on max-turns but never the canned message," the change is different (split the set into a salvage-trigger set vs. a clean-exit set rather than dropping the entry). Proceed on the literal interpretation (drop) unless the PM redirects.

### Risk 2: Wiring the nightly installer into `install_service()` runs on every `/update`
**Impact:** A non-idempotent or non-gated installer could break `/update` on non-bridge machines.
**Mitigation:** The installer is bootout-then-bootstrap idempotent and the new `has_bridge_role` gate exits 0 (and removes stale plist) on non-bridge machines. Invoke it with `|| true` semantics matching the other gated installs so a nightly-install failure never blocks the rest of `install_service()`.

## Race Conditions

No race conditions identified — the test changes are synchronous assertions; the `_successful_exits` change is a constant-set edit read single-threaded inside `Container.run()`; the installer is a one-shot shell script. `_MODEL_REACHABLE` is already cached at module load specifically to avoid xdist fork races (existing comment at `test_granite_container_loop.py:71`).

## No-Gos (Out of Scope)

- [ORDERED] Actually loading the nightly launchd job on the bridge machine — this skills-only machine cannot reach the bridge machine; the installer is wired + machine-gated here, and the bridge machine schedules it on its next `/update`. The gated install is the deliverable; the live launchctl bootstrap happens on the bridge machine's update cycle.
- The #1719 fix itself (per-turn prefix-contract reminder + relaxed wrap-up floor) — parent fix, already merged (PR #1743).

## Update System

- **`scripts/valor-service.sh::install_service`** gains a call to `install_nightly_tests.sh` (gated). Since `/update` → `valor-service.sh install`, the bridge machine schedules the nightly automatically on its next update.
- **`scripts/install_nightly_tests.sh`** gains a `has_bridge_role` machine-gate so it is safe to invoke unconditionally from the install path — it self-skips on non-bridge machines and removes any stale plist.
- No new dependencies beyond `pytest-json-report` (already required and pre-checked by the installer).
- Migration for existing installations: none required — the next `/update` on the bridge machine installs the schedule; non-bridge machines clean up any stale plist.

## Agent Integration

No agent integration required — this is test/CI hardening plus a launchd install wiring change. No new CLI entry point, no MCP server, no bridge import. The existing `valor-granite-loop` entry point (already in `[project.scripts]`) is exercised by the integration test but is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document that `pm_max_turns` is a non-clean failure (surfaces as `REACTION_ERROR`, no canned salvage) and that `_successful_exits` now equals `_CLEAN_GRANITE_EXIT_REASONS`.
- [ ] Update the CLAUDE.md / `docs/features` note on nightly regression to state it is machine-gated to the bridge machine and installed via `/update` (`valor-service.sh install`).

### Inline Documentation
- [ ] Update the comment block at `container.py:1228-1235` and the `_run_wrapup_guard` docstring (line ~1498) to drop `pm_max_turns` from the "successful-shaped" enumeration.
- [ ] Header comment in `install_nightly_tests.sh` documenting the machine-gate (mirror `install_email_bridge.sh`).

## Success Criteria

- [ ] New env-gated real test asserts a non-empty user message reaches the relay regardless of `exit_reason` (covers `pm_floor_delivered`); fails hard on empty delivery.
- [ ] `pm_max_turns` removed from `_successful_exits` (`container.py:1235`); set equals `{"pm_complete", "pm_user", "pm_floor_delivered"}`.
- [ ] A test treats a successful-shaped exit with `user_facing_routed=False` as a hard failure.
- [ ] The model-unreachable skip emits a visible alert (warning/stderr marker) instead of silent green.
- [ ] `install_nightly_tests.sh` is machine-gated and invoked from `valor-service.sh::install_service`; it cleanly skips on this skills-only machine.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n '"pm_max_turns"' agent/granite_container/container.py` no longer shows it inside `_successful_exits`.

## Team Orchestration

The lead (Dev session) orchestrates; builders execute. Given the small, tightly-coupled surface, a single builder handles the code+tests, a second handles the installer wiring, then a validator verifies.

### Team Members

- **Builder (container+tests)**
  - Name: `container-builder`
  - Role: Tasks 1-3 — real-loop test, `_successful_exits` narrowing + invariant test, skip-alert.
  - Agent Type: builder
  - Resume: true

- **Builder (nightly installer)**
  - Name: `installer-builder`
  - Role: Task 4 — machine-gate `install_nightly_tests.sh` + wire into `valor-service.sh::install_service`.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `hardening-validator`
  - Role: Verify all success criteria; run targeted tests; confirm gate self-skips here.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Real-loop relay-delivery test + invariant test + skip alert
- **Task ID**: build-container-tests
- **Depends On**: none
- **Validates**: `tests/integration/test_granite_container_loop.py`, `tests/unit/test_session_executor_granite.py`
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `test_real_loop_delivers_nonempty_user_message` (env-gated) driving `Container.run()` with capturing callbacks; assert non-empty delivery + `user_facing_routed=True` regardless of exit_reason (incl. `pm_floor_delivered`).
- Drop `"pm_max_turns"` from `_successful_exits` at `container.py:1235`; update comment + `_run_wrapup_guard` docstring.
- Add unit test: `pm_max_turns` no longer triggers `_run_wrapup_guard`; successful-shaped exit with `user_facing_routed=False` is a hard failure.
- Add module-load `warnings.warn(...)` when `_MODEL_REACHABLE` is False; keep `RESUME_SKIP model_unreachable` token.
- Widen the existing CLI test's allowed `exit_reason` set to include `pm_floor_delivered` / `pm_no_user_message`.

### 2. Machine-gate + wire nightly installer
- **Task ID**: build-nightly-installer
- **Depends On**: none
- **Validates**: `scripts/install_nightly_tests.sh`, `scripts/valor-service.sh`
- **Assigned To**: installer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `has_bridge_role` Python-heredoc gate to `install_nightly_tests.sh` (mirror `install_email_bridge.sh::has_email_role`, checking `proj.get("telegram")`); skip + remove stale plist on non-bridge machines; fail open on unreadable config.
- Call `install_nightly_tests.sh` from `valor-service.sh::install_service` after `install_update_polling`, with non-blocking error semantics.
- Verify on this machine the gate prints a skip and exits 0.

### 3. Validation
- **Task ID**: validate-hardening
- **Depends On**: build-container-tests, build-nightly-installer
- **Assigned To**: hardening-validator
- **Agent Type**: validator
- **Parallel**: false
- Run targeted unit tests + the env-gated integration test (will skip-with-alert here; confirm the alert fires).
- Confirm `grep '"pm_max_turns"' container.py` no longer shows it in `_successful_exits`.
- Confirm `install_nightly_tests.sh` self-skips on this machine and `valor-service.sh install` invokes it without error.
- Report pass/fail against every success criterion.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hardening
- **Assigned To**: hardening-validator (documentarian pass)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and the nightly-regression note per the Documentation section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| pm_max_turns dropped from successful set | `grep -n '_successful_exits = ' agent/granite_container/container.py` | output does not contain `pm_max_turns` |
| Targeted unit tests pass | `.venv/bin/python -m pytest tests/unit/test_session_executor_granite.py -q` | exit code 0 |
| Integration test imports/collects | `.venv/bin/python -m pytest tests/integration/test_granite_container_loop.py --collect-only -q` | exit code 0 |
| Installer self-skips here | `bash scripts/install_nightly_tests.sh` | output contains `Skipping` (non-bridge machine), exit code 0 |
| Format clean | `python -m ruff format --check agent/ tests/ scripts/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`pm_max_turns` salvage behavior (load-bearing).** Dropping `pm_max_turns` from `_successful_exits` means max-turns runs no longer floor-deliver real PM text via the wrap-up guard — they surface as `REACTION_ERROR` with no delivered message. Is the desired behavior (a) the literal drop (max-turns = visible failure, no salvage), or (b) keep floor-delivering *real* PM text on max-turns but never the canned `OPERATOR_TERMINAL_MESSAGE` (which would mean splitting the set rather than dropping the entry)? Plan proceeds on (a) per the issue's literal text and anti-canned-fallback intent; confirm before build.
