---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3195
last_comment_id:
---

# pytest-clean.sh fails closed when zero tests executed

## Problem

Every mutation check in this repo's SDLC reads `scripts/pytest-clean.sh`'s exit code as
the verdict. `/do-build` breaks a guard, runs the wrapper, and records the guard as
"bitten" when the exit is non-zero. `/do-pr-review` and `/do-plan-critique` do the same.
The whole discipline rests on one assumption: **a zero exit means the tests ran and
passed.**

That assumption has a hole. A run in which every selected test is *skipped* executes
nothing and exits **0**. The wrapper passes that straight through
(`scripts/pytest-clean.sh:311`, `exit "$PYTEST_EXIT"`), so a broken guard reads as a
bitten one and a reviewer reads a green that means nothing.

The realistic trigger is on this machine already. `tests/conftest.py:722-740`
(`scratch_test_db`) calls `pytest.skip()` when the 15-slot machine-global test-DB pool is
exhausted, which happens routinely past ~5 concurrent agents. Any mutation check whose
selected tests all depend on that fixture reports a confident green having run nothing.
Observed live on the #3170 lane (2026-09-06): two false-GREEN mutation checks during
BUILD, reproduced by the round-1 reviewer, and the lane recovered only by dropping to
`-n 2` and reading the passed count by eye on every run.

**Current behavior:**
The wrapper is a pure pass-through of pytest's status. It has no notion of how many tests
actually executed, so "0 executed, all skipped" and "1500 executed, all passed" are the
same signal to every caller.

**Desired outcome:**
The wrapper fails closed when a session ran and executed zero tests, with a distinct
message naming the cause. Every other case keeps today's pass-through exactly.

## Freshness Check

**Baseline commit:** `d26db32d90075bb2b6e7614aa03ae2beb95598d1`
**Issue filed at:** 2026-09-06T12:12:09Z
**Disposition:** **Minor drift** — the defect is real, but the mechanism named in the
issue is not the one that produces it.

**File:line references re-verified:**
- `scripts/pytest-clean.sh:311` — `exit "$PYTEST_EXIT"`, the bare pass-through — **still holds**, unchanged.
- `tests/conftest.py:722-740` — `scratch_test_db` skipping on pool exhaustion — **still holds** (the fixture survived the `#3190` conftest reshuffle intact).
- `tests/conftest.py:309-323` — `pytest_configure` calling `pytest.exit(str(exc), returncode=3)` on a failed `claim_test_db()` — **still holds**. This is the "node down" path.

**Cited sibling issues/PRs re-checked:**
- #3170 — CLOSED. Nightly-triage idempotency; unrelated in subject, it is only the lane where the false green was *observed*.
- #2535 — OPEN. Names the concurrent-run corruption family this belongs to. Its "suite lock skips targeted runs" leg is already resolved (the lock was deleted, `scripts/pytest-clean.sh:87-99`); the wedge and shared-install legs remain.
- #2628 — CLOSED. Introduced the db-claim pool and the `pytest.skip` / `pytest.exit` behavior that this issue trips over. Its design is correct; it just has no downstream consumer that notices "nothing ran".
- #2574 — CLOSED. The wedge detector at `scripts/pytest-clean.sh:200-282`. Load-bearing here: it is the reason the wrapper backgrounds pytest and holds the controller PID, which rules out the output-parsing approach the issue proposes.

**Commits on main since issue was filed (touching referenced files):**
- `d14685728` FEATURE_MAP marker-regression guard (#3190) — moved `FEATURE_MAP` out of `tests/conftest.py` into the new `tests/marker_map.py` and added `tests/unit/test_feature_map_markers.py`. **Irrelevant to the root cause**, but it changes where a new test file's marker resolution is decided (see Test Impact).
- No commit has touched `scripts/pytest-clean.sh` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** none. Two peer plans are
in flight (`agentsession-quarantine-counter-divergence`,
`feature-map-stem-anchored-strip`); neither touches the wrapper. The second touches
`tests/marker_map.py` stem handling — a coordination note, not a blocker, since this plan
adds no `FEATURE_MAP` entry.

**Notes — the drift that matters.** The issue's headline is "node down ... the wrapper
exiting **0**". That does not reproduce. Simulating the exact production path (every
worker calling `pytest.exit(..., returncode=3)` from `pytest_configure`) yields
`xdist: maximum crashed workers reached: 8` / `no tests ran` and a controller exit of
**5**. Zero-collected exits 5; `-k` deselecting everything exits 5. The wrapper already
fails closed on all three. The one channel that genuinely returns 0 with nothing executed
is **all-skipped**, measured at exit 0 both serially and under `-n 2`. The fix therefore
narrows to that channel, and the issue's proposed "test that a zero-collection run exits
non-zero" becomes a *regression pin on existing behavior* rather than a fix.


## Freshness Check

## Prior Art

- **#2628 (CLOSED)**: Built the machine-global 15-slot test-DB claim registry and made
  collision fatal rather than silent. It introduced both zero-execution paths — the
  `pytest.skip` in `scratch_test_db` and the `pytest.exit(returncode=3)` in
  `pytest_configure`. Correct in itself; it simply never asked what a downstream
  exit-code reader would conclude.
- **#2574 (CLOSED)**: Added the stall watcher (`scripts/pytest-clean.sh:200-282`) after a
  wedged controller sat at 0% CPU with no summary. Same defect *class* as this issue —
  the instrument fails without saying so — and the closest prior art for the remedy
  shape: detect the pathological state inside the wrapper and exit non-zero with a named
  message rather than letting it read as anything else.
- **#3033 (shipped as `ffa5ed381` / `72ea67bba`)**: Made the wrapper refuse a worktree
  with no usable `.venv`, because imports silently resolved to the primary checkout and
  the run "reported green on code it never loaded". Identical failure shape — biased
  toward green — and its test file `tests/unit/test_worktree_venv_absent_guard.py` is the
  direct structural model for this plan's tests.
- **#2617 (`scripts/check-interpreter-pin.sh`)**: The wrapper's third fail-closed guard.
  Establishes the house pattern: guards live in the wrapper, print a named diagnostic to
  stderr, and `exit 1`.
- **#2535 (OPEN)**: The parent umbrella for concurrent-run corruption. This issue is a
  new member of that family — the pool contention #2535 describes is precisely what
  makes the skip channel fire in practice.

**No prior attempt to fix this specific problem exists.** The wrapper has gained three
fail-closed guards, all of them preconditions checked *before* pytest starts. This is the
first one that has to read a *result*.

## Research

No relevant external findings — this is entirely internal: the behavior of a repo-owned
bash wrapper, this repo's conftest, and pytest/xdist exit-code semantics, all of which
were measured directly rather than looked up. No external library, API, or ecosystem
pattern is involved.


## Research

## Spike Results

Three spikes ran against `.venv/bin/pytest` (pytest-xdist 3.8.0) in an isolated sandbox
rootdir, so no repo test-DB slot was claimed and no peer run was disturbed.

### spike-1: Which zero-execution channels actually return 0?
- **Assumption**: "node down / no tests ran exits 0" (the issue's headline claim)
- **Method**: prototype
- **Finding**: **The assumption is false, and a different channel is true.** Measured:

  | Channel | How it was produced | Exit |
  |---|---|---|
  | Zero collected | empty test file | 5 |
  | All deselected | `-k nomatch_xyz` | 5 |
  | All workers crash in `pytest_configure` | `pytest.exit(..., returncode=3)` in every worker, `-n 2` | **5** |
  | **All skipped** | `pytest.skip()` in every test, `-n 0` and `-n 2` | **0** |
  | Mixed pass + skip | one of each, `-n 2` | 0 (correct) |

- **Confidence**: high — each row was run and its exit code captured directly.
- **Impact on plan**: The fix narrows from four channels to one. Three of the four the
  issue worries about already fail closed, so the change is small and its blast radius is
  confined to runs that today exit 0 having executed nothing.

### spike-2: Can the wrapper learn the executed count without capturing pytest's output?
- **Assumption**: "the wrapper must parse pytest's final summary line" (the issue's proposed fix)
- **Method**: prototype
- **Finding**: **Rejected, and a better mechanism validated.** The wrapper deliberately
  does not capture pytest's output: it backgrounds pytest (`scripts/pytest-clean.sh:287`)
  and keeps `$!` as the controller PID so the #2574 stall watcher can sample its CPU
  time. Teeing would take pytest off a TTY (losing progress and colour) and move `$!` onto
  `tee`, disabling the wedge detector. A `-p`-loaded plugin implementing
  `pytest_sessionstart` / `pytest_runtest_logreport` / `pytest_sessionfinish` and writing
  a small file named by an env var sidesteps both problems. Validated end to end:

  | Invocation | file contents | disposition |
  |---|---|---|
  | `--version` | absent (no session) | pass through |
  | `--collect-only -n 2` | `collectonly` | pass through |
  | `-n 2`, mixed pass + skip | `count 1` | pass through |
  | `-n 2`, all skipped | `count 0` | **fail closed** |
  | all workers crash | absent, exit 5 | already non-zero |

- **Confidence**: high — every row above was executed and the file read back.
- **Impact on plan**: Fixes the mechanism (plugin, not output parsing) and supplies the
  three-state file protocol the implementation needs.

### spike-3: Under xdist, does the controller see the workers' test reports?
- **Assumption**: "the controller can count executed tests, so no per-worker file merging is needed"
- **Method**: prototype
- **Finding**: **Confirmed.** xdist forwards each worker's `pytest_runtest_logreport` to
  the controller, so a plugin counting reports in the controller process gets the
  aggregate. A mixed `-n 2` run over two files reported `count 1` (one passed, one
  skipped) from the controller alone. The plugin must return early when
  `hasattr(config, "workerinput")` so workers do not each overwrite the file.
- **Confidence**: high
- **Impact on plan**: One file, one writer. No merging, no locking, no per-worker
  temp-file cleanup.


## Data Flow

## Architectural Impact

## Appetite

## Prerequisites

## Solution

## Failure Path Test Strategy

## Test Impact

## Rabbit Holes

## Risks

## Race Conditions

## No-Gos (Out of Scope)

## Update System

## Agent Integration

## Documentation

## Success Criteria

## Step by Step Tasks

## Verification

## Critique Results

---

## Open Questions
