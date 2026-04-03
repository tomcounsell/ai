---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/668
---

# Audit and Fix Issues from Unreviewed PRs #660-664

## Problem

PRs #660-664 were merged without proper SDLC review. A post-merge review (issue #666) found phantom references to `bridge/coach.py` -- but that was just the first thing caught. A full audit of all 5 PRs reveals additional broken tests and stale documentation that need fixing.

**Current behavior:**
- 3 unit tests in `tests/unit/test_reflection_scheduler.py` fail on main because PR #664 removed `daily-maintenance` from `config/reflections.yaml` without updating the tests
- `docs/features/reflections.md` documents `daily-maintenance` as an active reflection but the config entry was removed
- Two plan docs still reference `bridge/coach.py` as if it exists (stale but lower priority since plans are historical)

**Desired outcome:**
- All unit tests pass on main
- No documentation references deleted functionality as if it still exists
- Stale plan documents updated to reflect current state

## Prior Art

- **PR #672**: Removed the 2 phantom `bridge/coach.py` references from `bridge/pipeline_graph.py` and `docs/features/pipeline-state-machine.md`. Already merged. This plan covers the remaining issues that PR did not address.
- **Issue #666**: Post-merge review that spawned issue #668. Identified the coach.py phantom refs as the first finding.

## Architectural Impact

- **No new dependencies**: Pure cleanup work
- **No interface changes**: Only fixing tests and docs
- **Coupling**: No change -- removing stale references reduces confusion
- **Reversibility**: Trivially reversible

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Test fixes**: Update 3 test assertions in `test_reflection_scheduler.py` to match the current `config/reflections.yaml` (which no longer has `daily-maintenance`)
- **Doc fixes**: Remove or update `daily-maintenance` references in `docs/features/reflections.md`
- **Plan doc fixes**: Update stale `bridge/coach.py` references in completed/draft plan documents

### Technical Approach

1. Fix the 3 failing tests by removing assertions about `daily-maintenance`
2. Update `docs/features/reflections.md` to remove the registry table entry and description paragraph for `daily-maintenance`
3. Update `docs/plans/wire-pipeline-graph-563.md` (status: Merged) to note that `bridge/coach.py` was deleted by PR #661
4. Update `docs/plans/unify-persona-vocabulary.md` (status: Draft) to remove the stale `bridge/coach.py` reference

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is test and doc cleanup only

### Empty/Invalid Input Handling
- Not applicable -- no new functions or modified functions

### Error State Rendering
- Not applicable -- no user-visible output changes

## Test Impact

- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading::test_load_registry_from_project` -- UPDATE: remove assertion that `daily-maintenance` is in registry names
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_daily_maintenance_interval_daily` -- DELETE: entire test validates a removed config entry
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_expected_reflections_present` -- UPDATE: remove `daily-maintenance` from expected set

## Rabbit Holes

- Re-adding `daily-maintenance` to the config -- it was intentionally removed by PR #664
- Auditing PRs beyond #660-664 -- scope is limited to these 5 PRs
- Rewriting the artifact inference tests from PR #662 -- they work correctly

## Risks

### Risk 1: daily-maintenance removal was unintentional
**Impact:** If it should still be in the config, we would be compounding the error by updating tests to match
**Mitigation:** PR #664 explicitly removed the entry in a focused diff. The commit message and issue #657 confirm the dashboard was the focus. The `daily-maintenance` reflection was already `enabled: false` before removal, confirming it was inactive/deprecated.

## Race Conditions

No race conditions identified -- all operations are synchronous file edits to tests and documentation.

## No-Gos (Out of Scope)

- Re-adding `daily-maintenance` to the reflections config
- Modifying any Python source code beyond test files
- Auditing PRs outside the #660-664 range
- Changing artifact inference or dashboard behavior (confirmed working)

## Update System

No update system changes required -- this is purely test and documentation cleanup.

## Agent Integration

No agent integration required -- no bridge, MCP, or tool changes.

## Documentation

- [ ] Update `docs/features/reflections.md` to remove stale `daily-maintenance` references
- [ ] Update `docs/plans/wire-pipeline-graph-563.md` to note `bridge/coach.py` was deleted
- [ ] Update `docs/plans/unify-persona-vocabulary.md` to remove stale `bridge/coach.py` reference

## Success Criteria

- [ ] `pytest tests/unit/test_reflection_scheduler.py` passes all tests (currently 3 failures)
- [ ] `pytest tests/unit/ -x` passes with zero failures
- [ ] `grep -c 'daily-maintenance' docs/features/reflections.md` returns 0
- [ ] `grep -c 'bridge/coach.py' docs/plans/wire-pipeline-graph-563.md` returns 0
- [ ] `grep -c 'bridge/coach.py' docs/plans/unify-persona-vocabulary.md` returns 0
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Fix tests and update stale documentation
  - Agent Type: builder
  - Resume: true

- **Validator (verify)**
  - Name: cleanup-validator
  - Role: Verify all tests pass and no stale refs remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix failing reflection scheduler tests
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: tests/unit/test_reflection_scheduler.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tests/unit/test_reflection_scheduler.py`:
  - Line 50: Remove `assert "daily-maintenance" in all_names`
  - Lines 495-503: Delete `test_daily_maintenance_interval_daily` test method entirely
  - Line 516: Remove `"daily-maintenance"` from the expected set in `test_expected_reflections_present`
- Run `pytest tests/unit/test_reflection_scheduler.py -x -q` to confirm all 45+ tests pass

### 2. Update stale documentation
- **Task ID**: build-docs
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- In `docs/features/reflections.md`:
  - Remove the `daily-maintenance` row from the registry table (line 54)
  - Remove or update the paragraph at line 112 describing `daily-maintenance`
- In `docs/plans/wire-pipeline-graph-563.md`:
  - Add a note that `bridge/coach.py` was deleted by PR #661 where it is referenced
- In `docs/plans/unify-persona-vocabulary.md`:
  - Remove or annotate the `bridge/coach.py` reference at line 534

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-docs
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` and confirm zero failures
- Run `python -m ruff check .` and confirm clean
- Grep for stale references and confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_reflection_scheduler.py -x -q` | exit code 0 |
| All unit tests | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No daily-maintenance in reflections doc | `grep -c 'daily-maintenance' docs/features/reflections.md` | exit code 1 |
| No coach.py in pipeline graph plan | `grep -c 'bridge/coach.py' docs/plans/wire-pipeline-graph-563.md` | exit code 1 |
| No coach.py in persona plan | `grep -c 'bridge/coach.py' docs/plans/unify-persona-vocabulary.md` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---
