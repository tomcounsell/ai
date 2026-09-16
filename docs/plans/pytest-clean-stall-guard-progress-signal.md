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

1. **Entry point**: operator runs `scripts/pytest-clean.sh <args>`; wrapper resolves `PYTEST_BIN`, mints the count file AND a progress file, starts the stall watcher, and launches pytest in background (stdout untouched — still the operator's TTY, still `$!` as the controller PID).
2. **Pytest controller**: runs pytest with `-n auto --dist=loadfile`; workers execute tests and xdist forwards each `pytest_runtest_logreport` to the controller; today the watcher samples only the controller PID's cumulative CPU via `ps -o time=`.
3. **New progress tap**: the existing `pytest_executed_count.py` plugin gains a heartbeat — its controller-side `pytest_runtest_logreport` (same hook, same `_in_worker` guard the #3222 count already relies on) appends one line per executed report to the wrapper-minted progress file. Line count is the progress counter; no `-v`, no tee, no stdout parsing.
4. **Watcher decision**: every sample, the watcher reads CPU delta AND progress-file line-count delta. CPU advancing OR lines advancing resets the stall clock; only both flat accrues `stalled` time toward `PYTEST_STALL_LIMIT_S`.
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

### Exception Handling Coverage
- [ ] No exception handlers in scope — the change is bash (`tee`, `wc -l`, `ps` sampling); the failure mode is a broken tap, which must fail OPEN to CPU-only (covered by a dedicated test asserting a live run with an unobservable log still completes).

### Empty/Invalid Input Handling
- [ ] Empty log at startup (zero tests completed yet) must not read as "no progress": the watcher marks baselines on the first sample after launch and only accrues `stalled` on consecutive flat samples.
- [ ] Caller passing their own `-v`/`-q`/verbosity flags: wrapper must not double-append or strip; test asserts caller flags survive (mirrors the existing `-p` injection test).

### Error State Rendering
- [ ] True wedge still renders exactly one headline: the WEDGED banner stays the only headline (zero-tests guard already passes through on non-zero pytest exit; assert no ZERO TESTS diagnostic alongside WEDGED).
- [ ] False-positive path renders nothing new: the slow-live test asserts WEDGED is absent from stderr.

## Test Impact

No existing tests affected — the stall watcher currently has no dedicated regression test and the zero-tests file pins the watcher off, so the change is additive pending new tests below.

New coverage (new file, e.g. `tests/unit/test_pytest_clean_stall_progress.py`, reusing the sandbox harness): slow-live module test (guard must NOT fire) and true-wedge test (guard MUST still fire), plus tap-broken fail-open and caller-verbosity-passthrough cases from the Failure Path section.

## Rabbit Holes

- Parsing test outcome semantics (passed/failed counts, per-test names): the watcher needs count-delta only, not result interpretation. Do not build a TAP/JUnit parser.
- Raising `PYTEST_STALL_LIMIT_S` instead: papers over this incident; the next ~20-test arm-spawning module hits the same wall at any ceiling.
- Splitting more modules: per-module relief that leaves the general false positive in place.
- Watching worker PIDs instead of the controller: workers come and go under xdist; the controller plus its consolidated output stream is the stable observation point.

## Risks

### Risk 1: Tee changes operator-visible output or exit semantics
**Impact:** buffered/lost output, wrong exit code, broken pipe behavior on Ctrl-C.
**Mitigation:** preserve byte-identical stdout via `tee` with `pipefail` discipline; assert exit code passthrough in tests; keep the EXIT/INT/TERM trap ordering (tap cleanup inside existing `cleanup()`).

### Risk 2: `-v` injection alters collection or timing for huge suites
**Impact:** verbose output slows or perturbs full-suite runs.
**Mitigation:** append `-v` only when no verbosity flag is present; line-count delta is O(1) per sample (`wc -l`); full-suite behavior verified by the existing zero-tests suite running green under the wrapper.

### Risk 3: True wedge stops being detected (OR-branch too permissive)
**Impact:** a wedged run that dribbles output without progress never fires.
**Mitigation:** the true-wedge regression test pins firing; line-count must advance, not merely exist, to reset the clock.

## Race Conditions

No race conditions identified — the watcher samples two monotonically increasing counters (cumulative CPU, log line count) from a single subshell; baselines are marked on first sample and both re-marked on every reset, so a torn read only delays one 30s sample and cannot invert the verdict.

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
