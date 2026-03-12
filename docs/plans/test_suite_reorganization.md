---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/379
last_comment_id:
---

# Test Suite Reorganization

## Problem

Test agents claim pre-existing failures on main because flaky tests pass sometimes and fail other times. The flat test structure (49 of 90 files in `tests/` root) makes it impossible to run "just the fast tests" without markers on every test. Running the full suite takes ~8 minutes serially, slowing feedback loops.

**Current behavior:**
- 49 test files dumped in `tests/` root with no categorization
- 5 tests fail intermittently on main, causing false "pre-existing failure" claims
- No way to run a fast subset by directory — must use the full suite or know specific file paths
- `pytest -n auto` breaks 19 tests due to shared Redis state

**Desired outcome:**
- Directory structure matches test categories: `tests/unit/`, `tests/integration/`, `tests/e2e/`
- Zero flaky tests on main
- `pytest tests/unit/` completes in <60s and is safe for parallel execution
- `pytest -n auto` works across the full suite

## Prior Art

- **Issue #363**: "Verify pre-existing test failures against main instead of hand-waving" — addressed the symptom (agents lying about failures) but not the root cause (flaky tests exist on main)
- **PR #271**: "Fix pytest-postgresql plugin crash in worktrees" — addressed a collection error, similar pattern to what we're fixing
- **PR #156**: "Skills & agents reorganization" — prior reorganization precedent, moved skills into canonical structure

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is mechanical refactoring — move files, update imports, fix 5 tests. No design decisions needed.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **File migration**: Move 49 root-level test files into `tests/unit/` (33 files) or `tests/integration/` (15 files) based on whether they use real Redis/network/subprocess
- **Import fixup**: Update any cross-test imports and conftest scoping
- **Flaky test fixes**: Address each of the 5 flaky tests at root cause
- **Parallel safety**: Ensure Redis-dependent tests use isolated key prefixes for xdist compatibility

### File Classification

**To `tests/unit/` (33 files):**
test_auto_continue, test_branch_manager, test_build_validation, test_coach, test_code_impact_finder, test_context_modes, test_cross_wire_fixes, test_doc_impact_finder, test_docs_auditor, test_duplicate_delivery, test_escape_hatch, test_features_readme_sort, test_goal_gates, test_intake_classifier, test_messenger, test_observer, test_observer_early_return, test_open_question_gate, test_pre_tool_use_hook, test_reflections, test_reflections_multi_repo, test_reflections_report, test_reflections_scheduling, test_sdk_client, test_sdk_permissions, test_sdlc_mode, test_session_stuck_pending, test_stop_hook, test_summarizer, test_telemetry, test_valor_telegram, test_work_request_classifier, test_workflow_sdk_integration

**To `tests/integration/` (15 files):**
test_agent_session_lifecycle, test_connectivity_gaps, test_cross_repo_build, test_enqueue_continuation, test_job_health_monitor, test_job_queue_race, test_job_scheduler, test_lifecycle_transition, test_redis_models, test_reflections_redis, test_remote_update, test_reply_delivery, test_silent_failures, test_stage_aware_auto_continue, test_steering, test_unthreaded_routing

**Already in subdirs (no move needed):** tests/unit/ (24), tests/e2e/ (1), tests/integration/ (2), tests/tools/ (8), tests/ai_judge/ (1), tests/performance/ (1)

### Flaky Test Fixes

| Test | Root Cause | Fix |
|------|-----------|-----|
| `test_garbage_collection` | 100MB threshold too tight for CI/dev machines | Raise threshold to 200MB or mark `@pytest.mark.slow` |
| `test_pop_job_respects_priority_order` | Popoto query hits stale Redis keys from other tests | Add `redis_test_db` fixture for key isolation |
| `test_no_stale_running_after_recovery` | Same stale key issue | Same fix |
| `test_no_stale_running_after_reset` | Same stale key issue | Same fix |
| `test_already_up_to_date` | Lock file from concurrent update script run | Clean lock file in setUp, or check for lock message in assertion |

### Technical Approach

- Use `git mv` for all file moves to preserve history
- Verify `conftest.py` fixtures are available at the right scope (some may need to move or be duplicated)
- Run the full suite after each batch of moves to catch import breakage early
- For xdist safety: ensure `redis_test_db` fixture flushes keys with a test-specific prefix

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a file reorganization

### Empty/Invalid Input Handling
- N/A — no new functions being written

### Error State Rendering
- N/A — no user-visible output changes

## Rabbit Holes

- Rewriting tests to be "better" — just move them, don't refactor test logic
- Adding markers to every individual test — directory structure is sufficient for categorization
- Making ALL tests xdist-safe — start with unit tests only, integration tests can remain serial
- Consolidating similar tests — that's a separate effort, this is purely organizational

## Risks

### Risk 1: Import breakage after moves
**Impact:** Tests fail to collect, blocking all test runs
**Mitigation:** Move in batches, run `pytest --collect-only` after each batch. Use `git mv` so rollback is trivial.

### Risk 2: conftest.py scope changes
**Impact:** Fixtures become unavailable to moved tests
**Mitigation:** Check which conftest fixtures each file uses before moving. The root `tests/conftest.py` is available to all subdirs automatically.

## Race Conditions

No race conditions identified — this is a file reorganization with no runtime behavior changes.

## No-Gos (Out of Scope)

- Rewriting test logic or assertions
- Adding new tests
- Changing test infrastructure (pytest plugins, fixtures)
- Making integration tests xdist-safe (future work)
- Consolidating overlapping test files

## Update System

No update system changes required — this is a test-internal reorganization.

## Agent Integration

No agent integration required — tests are not exposed to the agent.

## Documentation

- [ ] Update `CLAUDE.md` quick commands table if test paths change
- [ ] No feature doc needed — this is internal reorganization

## Success Criteria

- [ ] Zero test files in `tests/` root (all moved to subdirs)
- [ ] All 5 flaky tests fixed or properly marked
- [ ] `pytest tests/unit/ -q` passes with 0 failures
- [ ] `pytest tests/unit/ -n auto` passes with 0 failures (parallel-safe)
- [ ] `pytest tests/ -q` passes with 0 failures (full suite)
- [ ] All imports resolve after file moves
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (file-mover)**
  - Name: test-reorganizer
  - Role: Move test files, fix imports, fix flaky tests
  - Agent Type: builder
  - Resume: true

- **Validator (test-verifier)**
  - Name: test-verifier
  - Role: Verify all tests pass after reorganization
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Move unit test files
- **Task ID**: build-move-unit
- **Depends On**: none
- **Assigned To**: test-reorganizer
- **Agent Type**: builder
- **Parallel**: true
- `git mv` all 33 unit test files from `tests/` to `tests/unit/`
- Run `pytest --collect-only` to verify imports resolve
- Fix any import breakage

### 2. Move integration test files
- **Task ID**: build-move-integration
- **Depends On**: none
- **Assigned To**: test-reorganizer
- **Agent Type**: builder
- **Parallel**: true
- `git mv` all 15 integration test files from `tests/` to `tests/integration/`
- Ensure `redis_test_db` fixture is available via conftest
- Run `pytest --collect-only` to verify

### 3. Fix flaky tests
- **Task ID**: build-fix-flaky
- **Depends On**: build-move-unit, build-move-integration
- **Assigned To**: test-reorganizer
- **Agent Type**: builder
- **Parallel**: false
- Fix GC test threshold (raise to 200MB or mark slow)
- Add `redis_test_db` fixture to job_queue_race tests
- Fix remote_update lock file handling
- Verify all 5 previously-flaky tests pass reliably

### 4. Validate parallel safety
- **Task ID**: validate-parallel
- **Depends On**: build-fix-flaky
- **Assigned To**: test-verifier
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -n auto` — must pass
- Run `pytest tests/ -q` — full suite must pass
- Verify no test files remain in `tests/` root (only `conftest.py` and `__init__.py`)

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-parallel
- **Assigned To**: test-reorganizer
- **Agent Type**: documentarian
- **Parallel**: false
- Update CLAUDE.md if any test commands reference old paths
- Verify docs reference correct test locations

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: test-verifier
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No root test files | `ls tests/test_*.py 2>/dev/null \| wc -l` | output contains 0 |
| Unit tests parallel-safe | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Unit tests fast | `pytest tests/unit/ -q --tb=no 2>&1 \| grep -oP '\d+\.\d+s'` | output < 60 |
