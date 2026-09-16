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

1. **Entry point**: operator runs `scripts/pytest-clean.sh <args>`; wrapper resolves `PYTEST_BIN`, mints the count file, starts the stall watcher, and launches pytest in background.
2. **Pytest controller**: runs pytest with `-n auto --dist=loadfile`; workers execute tests and report outcomes back; today the watcher samples only the controller PID's cumulative CPU via `ps -o time=`.
3. **New progress tap**: pytest stdout is teed to a temp log the watcher can observe (line count as the progress counter). Each completed test under verbosity appends outcome lines.
4. **Watcher decision**: every 30s sample, the watcher reads CPU delta AND log line-count delta. CPU advancing OR lines advancing resets the stall clock; only both flat accrues `stalled` time toward `PYTEST_STALL_LIMIT_S`.
5. **Output**: on a true wedge the WEDGED banner still fires and TERMs the controller; on a slow-but-live module the run proceeds and stdout still streams to the operator unchanged.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (test-infra QoL, lowest urgency, fix shape already agreed in the issue)
- Review rounds: 1 (standard PR review; critique via war room before build)

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worktree venv for the build lane | `test -x .worktrees/sdlc-3317-stall-progress/.venv/bin/pytest && echo ok` | Lane tests must exercise lane code (#3033) |

## Solution

### Key Elements

- **Progress OR-signal**: the stall clock resets when the controller accrues CPU OR the observed test-outcome line count advances. Only a window with neither is a wedge.
- **Output tap with fallback**: the wrapper tees pytest stdout to a temp log for the watcher; when output cannot be observed (unwritable temp, non-teeable invocation), the watcher degrades to today's CPU-only behavior rather than failing.
- **Two-sided regression cover**: a slow-but-live synthetic module test (guard must NOT fire) plus a true-wedge test (guard MUST still fire), so neither side of the OR regresses silently.

### Flow

Operator runs wrapper → pytest output teed to temp log → watcher samples CPU + line count every 30s → either advancing resets stall clock → both flat accrues toward limit → limit reached prints WEDGED banner and TERMs controller → otherwise run completes with stdout unchanged.

### Technical Approach

- Tee pytest's stdout through `tee` (or equivalent fd-preserving tap) into `mktemp` log so the operator's console stream is byte-identical; watcher owns the log path and removes it in `cleanup()`.
- Ensure per-test outcome lines exist: if the caller's args carry no verbosity flag, the wrapper appends `-v` (only for the subprocess, never rewriting the caller's intent for other flags). `-v` line count advancing per completed test is the progress counter; exact parse format is implementation detail, count-delta only.
- Extend `watch_for_stall` to take the log path: track `mark_cpu` and `mark_lines`; each sample computes both deltas against the 1s CPU epsilon and a >0 line delta. Either delta resets `stalled` to 0 and re-marks both; else `stalled += STALL_SAMPLE_S`.
- Fallback: if the log is missing/unreadable at a sample, treat lines as unobservable for that sample and decide on CPU alone (today's behavior). Never fail a run because the tap broke.
- Keep `PYTEST_STALL_LIMIT_S=0` disable path intact. Keep the WEDGED banner text and its investigation guidance; append one line noting both CPU and outcome lines were flat.
- Tests extend the `test_pytest_clean_zero_tests.py` sandbox pattern (own rootdir, symlinked `.venv`, no `.python-version`, `PYTEST_CLEAN_COUNT_FILE`/`PYTHONPATH` scrubbed env): (a) synthetic module of 3+ tests each sleeping 30s+ on `-n 1` under a short `PYTEST_STALL_LIMIT_S` asserts exit 0 and no WEDGED; (b) synthetic wedge (controller blocked producing no output and no CPU, e.g. `sleep`-held controller stub or blocked fixture with output suppressed) asserts WEDGED fires. Time-box the sleeps so the suite stays cheap: total live-test wall time under ~120s.
- Sliceable-function discipline: keep any new shell predicate in the same multi-line `name() {` / bare-`}` form as `verdict_passes_through` so tests can slice and drive the real body.

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
