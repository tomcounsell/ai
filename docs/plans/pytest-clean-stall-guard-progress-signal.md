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
- **Plugin heartbeat, not stdout parsing**: the existing `pytest_executed_count.py` plugin (already injected via `-p` with the wrapper-minted path convention) gains a progress tap — its controller-side `pytest_runtest_logreport` appends one line per report to a wrapper-minted progress file. No `-v` injection, no `tee`, no stdout changes. The plugin's own docstring records why: teeing would move `$!` off the controller PID and break the #2574 watcher.
- **CPU-only fallback**: when the progress file is absent/unwritable/unreadable, the watcher decides on CPU alone (today's behavior). A broken tap never fails a run.
- **Two-sided regression cover**: a slow-but-live synthetic module test (guard must NOT fire) plus a true-wedge test (guard MUST still fire), so neither side of the OR regresses silently.

### Flow

Operator runs wrapper → wrapper mints progress file, exports path, starts watcher with path → controller-side plugin hook appends one line per received test report → watcher samples CPU + line count every 30s → either advancing resets stall clock → both flat accrues toward limit → limit reached prints WEDGED banner and TERMs controller → otherwise run completes with stdout and exit code untouched.

### Technical Approach

- Mint `PYTEST_CLEAN_PROGRESS_FILE` via `mktemp` next to the count-file mint; export it; remove it in `cleanup()` (tracked with its own `*_MINTED` variable so an inherited env value from an enclosing wrapper is never deleted — same discipline as `COUNT_FILE_MINTED`).
- Plugin: in `pytest_runtest_logreport`, after the existing `_count_file() or _in_worker` early return, append one line to the progress path when set. Progress counts every controller-side report arrival (any `when`/outcome — even a skip proves the worker is delivering), not just the executed-counting rule; liveness and executed-tally are separate questions. Swallow `OSError` (fail open, same as `_write`). No-op entirely when the env var is unset, so bare `pytest` is untouched.
- Extend `watch_for_stall` to take the progress path: track `mark_cpu` and `mark_lines` (via `wc -l`); each sample computes CPU delta against the 1s epsilon and line-count delta > 0. Either delta resets `stalled` to 0 and re-marks both; else `stalled += STALL_SAMPLE_S`. Missing/unreadable file at a sample means lines unobservable for that sample — decide on CPU alone.
- Keep `PYTEST_STALL_LIMIT_S=0` disable path intact. Keep the WEDGED banner text and its investigation guidance; append one line noting both CPU and outcome lines were flat.
- Tests extend the `test_pytest_clean_zero_tests.py` sandbox pattern (own rootdir, symlinked `.venv`, no `.python-version`, `PYTEST_CLEAN_COUNT_FILE`/`PYTHONPATH` scrubbed env, plus the new progress var): (a) synthetic module of 3+ tests each sleeping 30s+ on `-n 1` under a short `PYTEST_STALL_LIMIT_S` asserts exit 0 and no WEDGED; (b) synthetic wedge (controller blocked producing no reports and no CPU) asserts WEDGED fires. Time-box the sleeps so the suite stays cheap: total live-test wall time under ~120s.
- Sliceable-function discipline: keep any new shell predicate in the same multi-line `name() {` / bare-`}` form as `verdict_passes_through` so tests can slice and drive the real body.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope — the change is bash (`wc -l`, `ps` sampling) plus one append in the plugin's existing fail-open `_write` style; the failure mode is a broken tap, which must fail OPEN to CPU-only (covered by a dedicated test asserting a live run with an unwritable progress path still completes).

### Empty/Invalid Input Handling
- [ ] Empty progress file at startup (zero reports received yet) must not read as "no progress": the watcher marks baselines on the first sample after launch and only accrues `stalled` on consecutive flat samples.
- [ ] Plugin absent from the checkout (the `-p` injection gate's false branch): the watcher must run CPU-only exactly as today; test asserts a live run without the plugin file completes with no WEDGED.
- [ ] Bare `pytest` outside the wrapper: progress env var unset, plugin no-ops entirely; assert no progress file is created and behavior is unchanged.

### Error State Rendering
- [ ] True wedge still renders exactly one headline: the WEDGED banner stays the only headline (zero-tests guard already passes through on non-zero pytest exit; assert no ZERO TESTS diagnostic alongside WEDGED).
- [ ] False-positive path renders nothing new: the slow-live test asserts WEDGED is absent from stderr.

## Test Impact

No existing tests affected — the stall watcher currently has no dedicated regression test and the zero-tests file pins the watcher off, so the change is additive pending new tests below.

New coverage (new file, e.g. `tests/unit/test_pytest_clean_stall_progress.py`, reusing the sandbox harness): slow-live module test (guard must NOT fire) and true-wedge test (guard MUST still fire), plus tap-broken fail-open, plugin-absent fallback, and nested-invocation isolation cases from the Failure Path and Risks sections.

## Rabbit Holes

- Parsing test outcome semantics (passed/failed counts, per-test names): the watcher needs count-delta only, not result interpretation. Do not build a TAP/JUnit parser.
- Raising `PYTEST_STALL_LIMIT_S` instead: papers over this incident; the next ~20-test arm-spawning module hits the same wall at any ceiling.
- Splitting more modules: per-module relief that leaves the general false positive in place.
- Watching worker PIDs instead of the controller: workers come and go under xdist; the controller plus its consolidated output stream is the stable observation point.

## Risks

### Risk 1: Progress append perturbs the controller or the count verdict
**Impact:** an exception in the hook breaks a live run, or the heartbeat write races the sessionfinish verdict write.
**Mitigation:** append is O(1) with `OSError` swallowed (same fail-open as `_write`); heartbeat and verdict use separate files so neither write can truncate the other; the full zero-tests suite runs green under the wrapper as the regression gate.

### Risk 2: Nested wrapper invocations cross the progress file
**Impact:** an inner run appends to (or deletes) the outer run's progress file, resetting or freezing the wrong stall clock.
**Mitigation:** same discipline as the count file — unconditional `mktemp` mint per invocation, own `*_MINTED` tracker for cleanup, never honor an inherited value; nested-invocation test mirrors the count-file one.

### Risk 3: True wedge stops being detected (OR-branch too permissive)
**Impact:** a wedged run that dribbles output without progress never fires.
**Mitigation:** the true-wedge regression test pins firing; line-count must advance, not merely exist, to reset the clock.

## Race Conditions

No race conditions identified — the watcher samples two monotonically increasing counters (cumulative CPU, progress-file line count) from a single subshell; baselines are marked on first sample and both re-marked on every reset, so a torn read only delays one 30s sample and cannot invert the verdict. The plugin appends (never truncates) while the watcher only reads line counts, so concurrent access needs no locking; the verdict file's sessionfinish `"count N"` write lands in a separate file.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal test infrastructure. The wrapper and plugin ship in-repo (no propagated config, no new dependency, no migration); every checkout picks the change up on its next pull. The only operator-visible surface is the existing `PYTEST_STALL_LIMIT_S` env knob, whose semantics are unchanged (still the low-CPU-and-no-progress window).

## Agent Integration

No agent integration required — this is a test-runner-internal change. No new CLI entry point, no bridge import, no MCP surface: the agent keeps invoking `scripts/pytest-clean.sh` exactly as today. The new `PYTEST_CLEAN_PROGRESS_FILE` env var is minted and consumed inside the wrapper run, never set by callers.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pytest-clean-stall-guard.md` describing the CPU-or-progress OR-signal, the plugin heartbeat progress file, and the CPU-only fallback when the tap is unobservable
- [ ] Add entry to `docs/features/README.md` index table for the stall-guard page

### Inline Documentation
- [ ] Comment the watcher OR-condition in `scripts/pytest-clean.sh` explaining why either CPU or outcome lines reset the stall clock
- [ ] Document `PYTEST_STALL_LIMIT_S=0` escape hatch behavior next to the new output-observation knob

## Success Criteria

- [ ] Slow-but-live synthetic module (outcomes arriving within each window) completes with exit 0 and no WEDGED in stderr
- [ ] Synthetic true wedge (blocked `pytest_sessionfinish`, no reports, no CPU) still prints the WEDGED banner and exits non-zero
- [ ] Tap-broken run (unwritable progress path) completes live via the CPU-only fallback
- [ ] `tests/unit/test_pytest_clean_zero_tests.py` still green (no interference with the count verdict)
- [ ] Documentation updated (`/do-docs`): `docs/features/pytest-clean-stall-guard.md` created and indexed
- [ ] New tests add under ~120s wall time to the unit suite

## Team Orchestration

Solo builder plus a read-only validator. The lead NEVER builds directly — it deploys the pair below and coordinates.

### Team Members

- **Builder (stall-guard)**
  - Name: stall-guard-builder
  - Role: wrapper + plugin + new tests, per Step by Step Tasks
  - Agent Type: builder
  - Resume: true

- **Validator (stall-guard)**
  - Name: stall-guard-validator
  - Role: verifies Success Criteria against the Verification table, checks no existing tests regressed
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

- [ ] TC1 (builder): mint/export/clean up `PYTEST_CLEAN_PROGRESS_FILE` in `scripts/pytest-clean.sh` (own `*_MINTED` tracker, `cleanup()` removal, never honor inherited value)
- [ ] TC2 (builder): add the controller-side heartbeat append in `pytest_executed_count.py` (`pytest_runtest_logreport`, after the existing early return, `OSError`-swallowed, no-op when unset)
- [ ] TC3 (builder): extend `watch_for_stall` with the progress path — `mark_cpu`/`mark_lines`, either-delta-resets logic, per-sample CPU-only fallback, one-line banner addition
- [ ] TC4 (builder): write `tests/unit/test_pytest_clean_stall_progress.py` (slow-live, true-wedge, tap-broken fail-open, plugin-absent fallback, nested-invocation isolation) reusing the zero-tests sandbox pattern
- [ ] TC5 (builder): docs — create `docs/features/pytest-clean-stall-guard.md`, index it in `docs/features/README.md`, comment the watcher OR-condition and the `PYTEST_STALL_LIMIT_S=0` hatch
- [ ] TC6 (validator): run the Verification table end to end and confirm every Success Criterion

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Slow-live module not flagged | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_stall_progress.py -k slow_live` (under short `PYTEST_STALL_LIMIT_S`) | exit 0, stderr contains no WEDGED |
| True wedge still caught | same harness, `-k true_wedge` | non-zero exit, stderr contains WEDGED, no ZERO TESTS headline |
| Zero-tests suite unaffected | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py` | exit 0 |
| Lint clean | `python -m ruff check pytest_executed_count.py` | exit code 0 |
| Format clean | `python -m ruff format --check pytest_executed_count.py` | exit code 0 |
| No tee on pytest stdout | `grep -c "tee" scripts/pytest-clean.sh` | match count == 0 |
| Caller verbosity untouched | `grep -cE 'set -- [^;]*"-v"' scripts/pytest-clean.sh` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Heartbeat line must be newline-terminated or wc -l never advances: wc -l counts newlines, so a payload without a trailing newline leaves the sampled count flat and the OR-branch silently degrades to the CPU-only behavior being fixed. | pending | Write with an explicit newline (open(path, "a").write(".\n")) inside the existing OSError-swallowed helper; assert in the slow-live test that wc -l before vs after one sleep window strictly increases. |
| CONCERN | Risk & Robustness | Slow-live test needs a negative control or it can pass vacuously: the reused sandbox harness pins PYTEST_STALL_LIMIT_S=0 in every subprocess env (tests/unit/test_pytest_clean_zero_tests.py:162), so a TC4 copy inheriting that pin passes with the watcher off and never exercises the OR-branch. | pending | Negative control through the existing mutation seam (PYTEST_CLEAN_SCRIPT pointing at a wrapper copy with TC3 reverted, or progress path at /dev/null so wc -l reads 0 on every sample): assert WEDGED fires there while the real run stays clean, mirroring the TestNegativeControl pattern. |
| CONCERN | Scope & Value | The ~120s wall-time budget does not fit five sandbox scenarios: slow-live alone needs about 90s of sleeps plus startup, and true-wedge needs a short limit above sandbox startup plus the watcher's 10s TERM wait (roughly 70s or more). | pending | Raise the budget (e.g. under ~300s) or shrink the sleeps with numbers that keep both regression directions testable (3 tests at 15s under PYTEST_STALL_LIMIT_S=30: old code fires at the first sample, new code resets on each report burst); state which scenarios share a sandbox run. |
| CONCERN | History & Consistency | Data Flow step 3 says one line per executed report while the Technical Approach states every controller-side report arrival of any when/outcome, explicitly not the executed-counting rule. A builder following Data Flow gates heartbeats on the executed rule, under which an all-skip module emits zero progress and reintroduces the false positive for the pool-exhaustion shape #3195 documents. | pending | Reword Data Flow step 3 to one line per controller-side report arrival (any when/outcome); the normative rule stays in the Technical Approach after the _count_file() or _in_worker early return, and Open Question 2's every-report default already agrees. |
| NIT | Risk & Robustness | The plan appends a line to the WEDGED banner but no Verification row asserts it, so a builder could drop the line and every listed check would still pass. | pending | Extend the true-wedge Verification row to assert stderr contains the new flat-CPU-and-no-outcomes line in addition to the WEDGED headline. |
| NIT | Scope & Value | Open Questions carry silent-accept defaults but remain open in the plan body, so a builder cannot tell settled decisions from live ones without re-reading the issue thread. | pending | Fold the accepted defaults into the Technical Approach wording at revision time and delete or mark-answered the Open Questions section. |
| NIT | History & Consistency | TC4 scrubs the new progress variable in the new file's sandbox env, but the pre-existing zero-tests file builds its subprocess envs from a scrub list written before the variable existed, so an outer wrapped run's exported progress path leaks into the old file's bare-pytest sandbox sessions. | pending | Pop PYTEST_CLEAN_PROGRESS_FILE in the zero-tests _base_env wherever PYTEST_CLEAN_COUNT_FILE is already removed; the stray-append direction is fail-open so this is hygiene, not a behavior fix. |

---

## Open Questions

1. Progress-file env var name (`PYTEST_CLEAN_PROGRESS_FILE`): acceptable, or prefer folding into the existing count file as a second line? Default: separate file (no truncation race with the verdict write). Say nothing to accept.
2. Heartbeat granularity (every controller-side report vs. executed-only per the counting rule)? Default: every report — any arrival proves liveness, including skips. Say nothing to accept.
3. The issue's fix shape suggested `-v` line counting; this plan uses plugin heartbeats instead (no stdout coupling, no TTY risk, no caller-visible `-v`). Confirm the deviation is welcome.
4. Two agents filled this plan concurrently on main (see commit history). Before critique, confirm no section carries a stale tee/`-v` reference the other author intended differently.
