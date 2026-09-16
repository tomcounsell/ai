---
status: Planning
type: bug
appetite: Small
owner: Dev (lane session/sdlc-3317-stall-progress)
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3317
last_comment_id:
---

# pytest-clean stall guard progress signal

## Problem

During lane 4 REVIEW (#3216, PR #3309), `tests/unit/test_improvement_eval_runner.py` (30 tests near 25s each, each spawning two `redis-server` arms, about 12.6 minutes for the module) landed alone on one `--dist loadfile` xdist worker. The `scripts/pytest-clean.sh` stall guard (`PYTEST_STALL_LIMIT_S`, default 600, cumulative controller CPU sampled every 30s with a 1s floor) reported a wedge. The controller was alive: the worker delivered one result about every 25s, but a single slow worker gives the controller almost nothing to do, so its CPU delta over ten minutes stayed under the floor.

**Current behavior:** `watch_for_stall` (`scripts/pytest-clean.sh:282-321`) watches cumulative controller CPU only. A long I/O-bound module alone on one worker is live with a controller CPU profile near the wedged one, so the guard false-positives and the operator reruns a healthy suite.

**Desired outcome:** progress is the signal, not controller CPU alone. A live run either accrues controller CPU or emits test outcomes. A window with no new outcome lines AND no meaningful CPU is a wedge; either alone is not. The CPU-only path stays as the fallback when output cannot be observed.

## Freshness Check

**Baseline commit:** `6e22a9ec5` (main at plan time; skeleton `471a42301`)
**Issue filed at:** 2026-09-14T15:39:37Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/pytest-clean.sh:263` — `PYTEST_STALL_LIMIT_S` default 600 — still holds
- `scripts/pytest-clean.sh:282-321` — `watch_for_stall` CPU-delta only — still holds
- `pyproject.toml:203` — addopts `--tb=short -p no:postgresql -n auto --dist=loadfile --timeout=420 --timeout-method=thread` (no `-v`) — still holds

**Cited sibling issues/PRs re-checked:**
- #3177 — still OPEN (recursive self-improvement controller, parent ref)
- #3216 / PR #3309 — lane-4 context where the false positive surfaced
- #2574 — CLOSED 2026-08-06 (wedge-detector origin; the guard this plan extends)
- #3195 / PR #3222 — CLOSED 2026-09-07 (zero-tests guard; its test file pins the watcher off)

**Commits on main since issue was filed (touching referenced files):**
- none touching `scripts/pytest-clean.sh` or `tests/unit/test_pytest_clean_zero_tests.py`

**Active plans in `docs/plans/` overlapping this area:** none (no `pytest-clean-stall*` plan existed)

**Notes:** the issue says "alongside the existing wedge-detection test" but no WEDGED regression test exists anywhere (the zero-tests file says so explicitly); the plan adds both sides.

## Prior Art

- **Issue #2574**: Full `tests/unit/` run wedged at 99% (worker died, controller at 0% CPU, no summary). Created the CPU-delta `watch_for_stall` guard. Relevance: this plan keeps its true-wedge detection intact while adding a progress OR-branch.
- **Issue #3195 / PR #3222**: pytest-clean exits 0 when zero tests ran (pool exhaustion); wrapper now fails closed via `pytest_executed_count.py` count file. Relevance: its test file (`tests/unit/test_pytest_clean_zero_tests.py`) disables the watcher per-subprocess and is the harness pattern the new tests extend; also proves output observation must not confuse the zero-tests verdict.
- **Lane 4 module split (#3216 / PR #3309)**: split the 30-test arm-spawning module in two so no single `loadfile` unit exceeds the window. Relevance: per-module relief, not general; this plan is the general fix so the next ~20-test arm-spawning file does not hit the same wall.

## Research

No relevant external findings — proceeding with codebase context and training data.

## Data Flow

TODO fill.

## Appetite

TODO fill.

## Prerequisites

TODO fill.

## Solution

TODO fill.

## Failure Path Test Strategy

TODO fill.

## Test Impact

No existing tests affected — the stall watcher currently has no dedicated regression test and the zero-tests file pins the watcher off, so the change is additive pending new tests below.

## Rabbit Holes

TODO fill.

## Risks

TODO fill.

## Race Conditions

TODO fill.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

TODO fill.

## Agent Integration

TODO fill.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pytest-clean-stall-guard.md` describing the CPU-or-progress OR-signal, the `-v` line-count observation, and the CPU-only fallback when output is unobservable
- [ ] Add entry to `docs/features/README.md` index table for the stall-guard page

### Inline Documentation
- [ ] Comment the watcher OR-condition in `scripts/pytest-clean.sh` explaining why either CPU or outcome lines reset the stall clock
- [ ] Document `PYTEST_STALL_LIMIT_S=0` escape hatch behavior next to the new output-observation knob

## Success Criteria

TODO fill.

## Team Orchestration

TODO fill.

## Step by Step Tasks

TODO fill.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Placeholder check replaced during fill | `echo ok` | output contains ok |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

TODO fill.
