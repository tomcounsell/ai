---
status: Ready
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1126
last_comment_id:
---

# Session Health: Orphan-PENDING Recovery Regression Test

## Problem

Commit `b40a2b73` fixed an `UnboundLocalError` in `agent/session_health.py::_agent_session_health_check` — the orphan-PENDING recovery branch referenced `_ensure_worker` without importing it in that scope. The fix landed direct-to-main with no PR review and no regression test. The test suite has no case that exercises the exact failure mode the fix addressed, so the import at `agent/session_health.py:1019` could be accidentally removed, relocated, or refactored away and CI would not catch the regression.

**Current behavior (after the fix, on main):**
- `agent/session_health.py:1019` imports `_ensure_worker` locally just before the call at line 1021.
- `agent/session_health.py:948` imports it locally inside the RUNNING-recovery branch.
- `_agent_session_health_check` runs cleanly in all existing test scenarios.
- **But** no test seeds the specific "orphan pending, zero running" topology, so removing either import would not fail the suite.

**Desired outcome:**
A regression test that fails on the pre-fix tree and passes on main. Specifically: with zero RUNNING sessions and ≥1 orphan PENDING session older than `AGENT_SESSION_HEALTH_MIN_RUNNING` on a non-local `worker_key`, `await _agent_session_health_check()` must complete without raising, and the orphan-PENDING call site at `agent/session_health.py:1021` must be reached (confirmed via spy on `_ensure_worker`).

## Freshness Check

**Baseline commit:** `eebe6ca32e6bec3efe54db73425c9ff71ada83f6`
**Issue filed at:** 2026-04-22T16:32:15Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_health.py:948` — RUNNING-branch local `from agent.agent_session_queue import _ensure_worker  # noqa: PLC0415` — still holds (line 948, verified).
- `agent/session_health.py:1019` — orphan-PENDING-branch local import of `_ensure_worker` — still holds (line 1019, verified).
- `agent/session_health.py:1021` — `_ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)` call site — still holds (line 1021, verified).
- `agent/session_health.py:994` — `worker_key.startswith("local")` gate — still holds (line 994, verified).
- `agent/agent_session_queue.py:1136` — `def _ensure_worker(worker_key: str, is_project_keyed: bool = False)` — still holds (signature unchanged; body hardened with try/finally in commit `9935778d` but signature and contract preserved).

**Cited sibling issues/PRs re-checked:**
- #1124 — closed 2026-04-22T16:33:10Z as already-fixed by `b40a2b73`. Points at #1126 for missing test coverage. Aligned with this plan's scope.
- #1110 — unrelated to this work despite the mis-attributed commit message on `b40a2b73`. Non-blocking.

**Commits on main since issue was filed (touching referenced files):**
- `9935778d` "Worker lifecycle cleanup: W9 alias, W10 dead handler, I4 leak hardening (#1133)" — touches `agent/session_health.py` (deletes `recover_orphaned_agent_sessions_all_projects` at line ~1319, far below the orphan-PENDING branch at 1019) and `agent/agent_session_queue.py` (hardens `_ensure_worker` with try/finally leak guard, signature unchanged). **Irrelevant to this test** — the function under test and the spied helper both behave as the issue described.

**Active plans in `docs/plans/` overlapping this area:** none. Recent `worker_lifecycle_cleanup.md`, `worker-session-lifecycle.md`, etc. are separate lifecycle work; none of them add regression tests for the orphan-PENDING-branch `_ensure_worker` import.

**Notes:** Minor drift in `_ensure_worker`'s body (try/finally hardening) does not affect the test strategy. The spy still monkeypatches `agent.agent_session_queue._ensure_worker` to bypass real worker spawning; the local `from agent.agent_session_queue import _ensure_worker` in `session_health.py` re-resolves the name from the module on every call, so monkeypatching the module attribute works whether or not the real body changed.

## Prior Art

Searched `gh issue list --state closed --search "regression test session_health"` — zero prior issues. Searched `gh issue list --state closed --search "_ensure_worker UnboundLocalError"` — only #1124 (the original report, closed as already-fixed).

- **#1124**: `session_health: UnboundLocalError for _ensure_worker in pending-sessions recovery path` — closed 2026-04-22 as already-fixed by `b40a2b73`. This issue (#1126) fulfills its acceptance criterion: add a regression test.

No prior attempts at this specific test exist. Relevant test scaffolding is already present in:
- `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck` — covers RUNNING-branch recovery (dead worker, no worker, timed-out) but does NOT cover the orphan-PENDING branch.
- `tests/unit/test_session_health_phantom_guard.py` — seeding patterns for AgentSession and phantom records.
- `tests/unit/test_session_health_sibling_phantom_safety.py` — patterns for calling `_agent_session_health_check` with no live workers.

## Research

No external research needed — this is purely internal repo work. No new libraries, APIs, or ecosystem patterns involved.

## Data Flow

Not applicable — this is test-only work with no production flow changes. The test under development follows the existing pattern used by `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck`: seed an `AgentSession` record in Redis, invoke `_agent_session_health_check()`, assert side effects.

## Architectural Impact

- **New dependencies**: none
- **Interface changes**: none
- **Coupling**: none (pure test)
- **Data ownership**: none
- **Reversibility**: trivial (delete the test file/case)

## Appetite

**Size:** Small

**Team:** Solo dev (test-engineer)

**Interactions:**
- PM check-ins: 0 (scope is locked by issue #1126)
- Review rounds: 1 (standard PR review)

This is a 1-test regression addition. Scope is narrow and locked by the issue.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis available on test db | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Redis-backed AgentSession seeding |
| pytest & pytest-asyncio installed | `python -c "import pytest, pytest_asyncio"` | Async test runner |

## Solution

### Key Elements

- **One new test case**: seeds the "0 running + ≥1 orphan pending past threshold + non-local `worker_key`" topology and asserts `_agent_session_health_check()` does not raise.
- **Spy on `_ensure_worker`**: monkeypatch `agent.agent_session_queue._ensure_worker` so the test confirms the orphan-PENDING call site was reached without actually spawning a real worker task.
- **Test placement**: add to `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck` as `test_recovers_orphan_pending_with_no_running_sessions`. This class already has a `_cleanup_workers` autouse fixture that cancels tasks spawned into `_active_workers`, and it already imports `_agent_session_health_check`. Adding the new case here is strictly additive.

### Flow

Test setup → seed one pending AgentSession with non-local `worker_key` and an old `created_at` → monkeypatch `_ensure_worker` to a spy → `await _agent_session_health_check()` → assert no exception → assert spy was called with the seeded `worker_key` → teardown (autouse fixture cleans `_active_workers`, autouse `redis_test_db` fixture flushes Redis).

### Technical Approach

- **Placement**: new method `test_recovers_orphan_pending_with_no_running_sessions` inside `TestJobHealthCheck` in `tests/integration/test_agent_session_health_monitor.py`.
- **Seeding**: call the existing module-level `_create_test_session(...)` helper with:
  - `status="pending"`
  - `chat_id="789"` (any non-`"local"`-prefixed string; the helper defaults `chat_id="123"` which is already non-local, but an explicit value keeps the topology self-documenting and avoids collision with sibling tests in the same class that use `"123"` as `WORKER_KEY`)
  - `created_at=time.time() - (AGENT_SESSION_HEALTH_MIN_RUNNING + 60)` (60 seconds past the 5-minute threshold)
  - `session_id="orphan_pending_session"`
  - (The default `session_type` for the `_create_test_session` helper produces a session whose `worker_key` resolves to `chat_id`. Verify at test time via assertion.)
- **Pre-assertion of topology**: before invoking the health check, assert `AgentSession.query.filter(status="running")` is empty. This defends the test from regressions where helper defaults change and accidentally seed a RUNNING record.
- **Spy**: `monkeypatch.setattr("agent.agent_session_queue._ensure_worker", spy)` where `spy` is a plain callable that records `(worker_key, is_project_keyed)` tuples on a list. Because `session_health.py:1019` does `from agent.agent_session_queue import _ensure_worker` inside the function, the name is re-resolved from the module on every call — monkeypatching the module attribute is sufficient and does NOT require patching `session_health.py`'s global namespace.
- **Worker-key expectation**: the test derives the expected `worker_key` from the seeded session (`seeded_session.worker_key`) rather than hard-coding a string. This prevents the test from silently passing if `worker_key` semantics change (e.g., a new session_type defaulting rule).
- **Assertions**:
  1. `_agent_session_health_check()` completes without raising (specifically would have raised `UnboundLocalError: cannot access local variable '_ensure_worker'` on the pre-fix tree).
  2. The spy was called exactly once with `(seeded_session.worker_key, seeded_session.is_project_keyed)`.
  3. No real worker was published to `_active_workers[seeded_session.worker_key]` (the spy replaced `_ensure_worker`, so no task should exist).
- **Cleanup**: handled by the class-level autouse `_cleanup_workers` fixture and the root-level autouse `redis_test_db` fixture. No additional teardown needed.

### Why integration over unit

The issue's preferred placement is the integration module because (a) that class already has the `_cleanup_workers` fixture that protects `_active_workers` from cross-test pollution, (b) `_create_test_session` and the class layout are already set up for this kind of topology, and (c) the test exercises the real `_agent_session_health_check` against real Redis-backed `AgentSession` records — which is the appropriate level for a regression guarding an import-scoping bug. A unit-level version would need to mock away more scaffolding for no additional signal.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `agent/session_health.py:1023-1027` has `except Exception: logger.exception(...)` around the pending-session loop body. This would have caught the pre-fix `UnboundLocalError` and continued — but the fix is at the per-entry level inside the `try`, and the handler logs with `logger.exception` (observable). **The new test verifies the happy path reaches the call site; it does not need to assert on the exception-handler because on the pre-fix tree the error would bubble up as the raised `UnboundLocalError` before the handler's observable write.** Actually — verify this claim: on the pre-fix tree, would the `UnboundLocalError` be caught by the per-entry `except Exception:` block and logged, or would it propagate out of `_agent_session_health_check`? The `try:` begins at line 974 (before `worker_key = entry.worker_key`) and the `except Exception:` at line 1023 covers the entire pending-entry body. So `UnboundLocalError` would be CAUGHT by the per-entry handler. That means the pre-fix tree would log the exception via `logger.exception` and continue to the next entry, **NOT** raise out of the function. The test must therefore assert on observable side effects, not on "does not raise."
  - **Corrected assertion strategy:** assert that the spy WAS called with the seeded `worker_key`. On the pre-fix tree, the spy is never called because the `UnboundLocalError` fires on the `from agent.agent_session_queue import _ensure_worker` line itself (Python treats `_ensure_worker` as a function-local binding due to the RUNNING-branch import, so the import statement at line 1019 raises before the call at line 1021). On the fixed tree, the spy is called exactly once. This is the unambiguous failure signal.
  - **Additionally assert** `caplog` at `WARNING` or `ERROR` level does NOT contain `"UnboundLocalError"` or `"cannot access local variable '_ensure_worker'"`. On the pre-fix tree, the per-entry `except Exception: logger.exception(...)` writes this string to the log. On the fixed tree, no such log record exists. This is a belt-and-braces check that catches the case where someone reintroduces the bug AND silently swallows the log.

### Empty/Invalid Input Handling
- [x] No new functions are being added — the test exercises existing code. Empty/None inputs to `_agent_session_health_check` are handled by existing guards (e.g., `created_ts is None` check at line 990).

### Error State Rendering
- [x] Not user-visible — this is an internal periodic coroutine. The failure mode is an `UnboundLocalError` that corrupts the health-check loop, not a user-facing error.

## Test Impact

- [ ] `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck` — UPDATE: add one new method `test_recovers_orphan_pending_with_no_running_sessions`. Strictly additive; no changes to existing tests.

No other existing tests are affected — this change is purely additive. The new test does not modify production code, does not change shared fixtures, and does not alter any function's signature or behavior.

## Rabbit Holes

- **Refactoring the two function-local imports into a module-level import.** Called out as out-of-scope by the issue. The circular-import risk appears low (because `agent.agent_session_queue` does not import from `agent.session_health`), but this belongs in its own issue so the refactor is reviewed independently. **Do not touch the production imports in this PR.**
- **Adding a symmetric test for the RUNNING-branch import at line 948.** That branch is already exercised by `test_recovers_job_with_dead_worker`, `test_recovers_job_with_no_worker`, and `test_recovers_timed_out_job_with_alive_worker`. These tests pass through the recovery path that calls `_ensure_worker` at line 950, so the RUNNING-branch import is covered. **Do not add a duplicate test.**
- **Writing both a unit test and an integration test.** The issue lists two candidate homes in order of preference; pick ONE. The integration module is preferred because the class already has the cleanup fixture.
- **Expanding the test to assert teardown behavior of `_active_workers`.** The autouse `_cleanup_workers` fixture already does this; asserting it inside the test duplicates scaffolding and adds brittleness.

## Risks

### Risk 1: Spy doesn't intercept because of import resolution order

**Impact:** Test silently passes on the pre-fix tree because the monkeypatch is applied to the wrong name, so the spy is never called and the test's existence signal is meaningless.

**Mitigation:** Patch `agent.agent_session_queue._ensure_worker` (the module-attribute form), NOT `agent.session_health._ensure_worker`. The production code at line 1019 does a function-local `from agent.agent_session_queue import _ensure_worker`, which resolves the name from the `agent.agent_session_queue` module on every call. Monkeypatching the module attribute guarantees the spy is seen. Additionally, the test asserts the spy was called with the correct `worker_key` — if the spy is never invoked, the assertion fails loudly.

**Verification step in acceptance:** locally revert the import at `agent/session_health.py:1019` and confirm the new test fails with a recorded assertion error (either "spy was not called" OR "caplog contains UnboundLocalError"). Restore before commit.

### Risk 2: Helper `_create_test_session` default produces a `"local"`-prefixed `worker_key`

**Impact:** Test would exercise the `worker_key.startswith("local")` branch (abandoned local session) instead of the orphan-PENDING-with-`_ensure_worker` branch, and the spy would never be called.

**Mitigation:** Explicitly pass `chat_id="789"` (non-`"local"` prefix). Assert at test setup that `seeded_session.worker_key` does NOT start with `"local"`. If it does, fail the test loudly rather than silently taking the wrong branch.

### Risk 3: `session_type` default produces a PM session, whose `worker_key == project_key == "test"` — still non-local, but `is_project_keyed=True`

**Impact:** The spy's call signature would be `("test", True)` instead of `("789", False)`. The test still passes, but the topology is less representative of the original bug scenario (which was reported for a non-project-keyed worker).

**Mitigation:** This is acceptable. The bug is about import scoping, not about `is_project_keyed` semantics. What matters is that the call site is reached. The test derives the expected `worker_key` from `seeded_session.worker_key` rather than hard-coding `"789"`, so the assertion is robust against whichever path the helper defaults produce — as long as `worker_key` is non-local. Add the non-local assertion as a guard.

### Risk 4: Global `_active_workers` state pollution from a prior test leaks a "live" worker for the seeded `worker_key`

**Impact:** The health check sees `worker_alive=True` at line 977, skips the orphan-PENDING branch entirely, and the spy is never called. Test fails, but for the wrong reason.

**Mitigation:** The class-level `_cleanup_workers` autouse fixture pops all entries from `_active_workers` after each test. Additionally, at test setup, explicitly `_active_workers.pop(seeded_session.worker_key, None)` before invoking the health check. This is already the pattern used by `test_recovers_job_with_no_worker` (line 200).

## Race Conditions

No race conditions identified — the test is single-threaded, runs in a single event loop invocation, and does not start any real background tasks (the spy replaces `_ensure_worker`). The autouse `_cleanup_workers` fixture cancels any tasks that accidentally escape, and the autouse `redis_test_db` fixture flushes Redis between tests.

## No-Gos (Out of Scope)

- Refactoring the two function-local `_ensure_worker` imports into a single module-level import. (Separate issue; circular-import risk review required.)
- Adding a unit-test counterpart in `tests/unit/test_session_health_orphan_pending_recovery.py`. (The issue lists it as a backup option; integration placement is preferred and sufficient.)
- Modifying any production file. This is test-only.
- Fixing the `#1110` mis-attribution on the fix commit message. (Non-blocking housekeeping.)
- Adding coverage for `finalize_session` on the `worker_key.startswith("local")` branch (abandoned-local flow). That's a different branch; not what the bug was in.

## Update System

No update system changes required — this is a test-only addition. No new dependencies, no config files, no deployment implications. `/update` is unaffected.

## Agent Integration

No agent integration required — this test exercises internal worker-queue machinery and is not exposed to the Telegram-facing agent. The bridge does not call `_agent_session_health_check` directly (it's scheduled from the worker service), and nothing in `mcp_servers/` or `.mcp.json` wraps session-health internals.

## Documentation

No documentation changes needed — this is a regression test that guards an existing bug fix. The fix itself is already on main (commit `b40a2b73`), and the production behavior is unchanged. `docs/features/` entries for session health (e.g., `docs/features/session-lifecycle.md`) describe the contract, not the internal import structure that this test guards.

If a future `docs/features/` entry is added specifically for `_agent_session_health_check`'s recovery paths, the new test file can be referenced there as a regression anchor — but that's out of scope for this plan.

Explicit statement for the hook: **No documentation changes needed.** This test-only change does not alter any public contract, does not introduce any new concept, and does not modify any behavior described in existing docs. The regression test is a safety net for the fix in `b40a2b73`, which itself did not update docs.

## Success Criteria

- [ ] New test `test_recovers_orphan_pending_with_no_running_sessions` added to `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck`.
- [ ] Test seeds zero RUNNING sessions and one orphan PENDING session with a non-local `worker_key` and `created_at` past `AGENT_SESSION_HEALTH_MIN_RUNNING`.
- [ ] Test monkeypatches `agent.agent_session_queue._ensure_worker` to a spy and asserts the spy was invoked exactly once with the seeded `worker_key` and correct `is_project_keyed` flag.
- [ ] Test asserts `caplog` contains NO record matching `UnboundLocalError` or `cannot access local variable '_ensure_worker'` at any level.
- [ ] Test pre-asserts `AgentSession.query.filter(status="running")` is empty and `seeded_session.worker_key` does not start with `"local"`.
- [ ] **Verification**: locally revert the import at `agent/session_health.py:1019` and confirm the new test fails with a spy-call-count or caplog assertion (specifically surfaces the `UnboundLocalError`). Restore before commit.
- [ ] `pytest tests/unit/ tests/integration/test_agent_session_health_monitor.py` passes end-to-end.
- [ ] No production code changes — `git diff main -- agent/` returns empty.
- [ ] Format clean (`python -m ruff format tests/integration/test_agent_session_health_monitor.py`). Note: per repo conventions (user instruction), do NOT run ruff lint; only `ruff format`.
- [ ] Tests pass (`/do-test`).
- [ ] No documentation updates needed (confirmed in Documentation section).

## Team Orchestration

### Team Members

- **Builder (test-author)**
  - Name: orphan-pending-test-author
  - Role: Add the new test case to the integration suite
  - Agent Type: test-engineer
  - Resume: true

- **Validator (test-reviewer)**
  - Name: orphan-pending-test-reviewer
  - Role: Verify test fails on reverted tree and passes on main
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the new test case

- **Task ID**: build-regression-test
- **Depends On**: none
- **Validates**: `tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck::test_recovers_orphan_pending_with_no_running_sessions` (new)
- **Informed By**: issue #1126 topology requirements; seeding patterns in `tests/integration/test_agent_session_health_monitor.py` lines 161-209
- **Assigned To**: orphan-pending-test-author
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_recovers_orphan_pending_with_no_running_sessions` method inside `TestJobHealthCheck` in `tests/integration/test_agent_session_health_monitor.py`.
- Seed one PENDING `AgentSession` with `chat_id="789"`, `created_at=time.time() - (AGENT_SESSION_HEALTH_MIN_RUNNING + 60)`, `session_id="orphan_pending_session"`.
- Pre-assert: `AgentSession.query.filter(project_key="test", status="running")` is empty; `seeded_session.worker_key` does NOT start with `"local"`; `_active_workers.get(seeded_session.worker_key)` is None.
- Monkeypatch `agent.agent_session_queue._ensure_worker` to a spy that records `(worker_key, is_project_keyed)` tuples on a captured list (use `pytest`'s `monkeypatch` fixture).
- `await _agent_session_health_check()`.
- Assert spy was called exactly once with `(seeded_session.worker_key, seeded_session.is_project_keyed)`.
- Assert `caplog.records` contains no record whose message matches `UnboundLocalError` or `cannot access local variable '_ensure_worker'` (use `caplog` fixture at `WARNING` level).
- Run `python -m ruff format tests/integration/test_agent_session_health_monitor.py`.

### 2. Verify the test catches the original bug

- **Task ID**: validate-regression-test
- **Depends On**: build-regression-test
- **Assigned To**: orphan-pending-test-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run the new test on current HEAD: `pytest tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck::test_recovers_orphan_pending_with_no_running_sessions -xvs`. Expect PASS.
- Locally revert the import: `sed -i.bak '/from agent.agent_session_queue import _ensure_worker.*noqa: PLC0415/d' agent/session_health.py` — but only the second occurrence (line 1019). Or, more precisely: use `python` to edit the file, removing only the line at 1019 region in the orphan-PENDING branch.
- Re-run the test; expect FAIL with either "spy not called" or "caplog contains UnboundLocalError".
- Restore the file: `mv agent/session_health.py.bak agent/session_health.py` (or `git checkout agent/session_health.py`).
- Re-run the test; expect PASS again.
- Run full unit + this integration file: `pytest tests/unit/ tests/integration/test_agent_session_health_monitor.py -q`. Expect zero failures.
- Verify `git diff main -- agent/` is empty (no production changes snuck in).
- Report pass/fail status to lead.

### 3. Final Validation

- **Task ID**: validate-all
- **Depends On**: build-regression-test, validate-regression-test
- **Assigned To**: orphan-pending-test-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff format --check tests/integration/test_agent_session_health_monitor.py` — expect exit 0.
- Run `pytest tests/unit/ tests/integration/test_agent_session_health_monitor.py -q` — expect exit 0.
- Run `git diff main -- agent/` — expect empty.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New test passes | `pytest tests/integration/test_agent_session_health_monitor.py::TestJobHealthCheck::test_recovers_orphan_pending_with_no_running_sessions -xvs` | exit code 0 |
| Full integration file passes | `pytest tests/integration/test_agent_session_health_monitor.py -q` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -q` | exit code 0 |
| Format clean | `python -m ruff format --check tests/integration/test_agent_session_health_monitor.py` | exit code 0 |
| No production code changes | `git diff main -- agent/ \| wc -l` | output contains `0` |
| Test references orphan-PENDING topology | `grep -c 'orphan_pending\|orphan-pending\|orphan_PENDING' tests/integration/test_agent_session_health_monitor.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the scope is fully locked by issue #1126. Test placement preference (integration over unit) is stated explicitly in the Solution section. The "verification by local revert" step is in the Success Criteria. No supervisor input needed before /do-plan-critique.
