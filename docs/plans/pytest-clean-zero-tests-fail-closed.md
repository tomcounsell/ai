---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3195
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-07T02:48:33Z
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
The wrapper exits non-zero when a session ran and reported zero passed and zero failed,
with a distinct, unmistakable message naming the cause. Pass-through behavior for every
other case is unchanged.

The message is as load-bearing as the exit code. `scripts/pytest-clean.sh` is what every
mutation check in this repo reads, so a false green here silently confirms every guard the
repo has. Whoever reads a mutation-check transcript later — a reviewer, a merge gate, the
next agent on the lane — must not be able to mistake a zero-execution run for a pass, which
is why the diagnostic prints even when the escape hatch suppresses the exit.

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
  file protocol the implementation needs (superseded in this revision by the
  pass-through allowlist — same file, fewer branches).

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

### Revision measurements (critique round 1)

Round 1 found that spike-1..3 validated the *mechanism* but not the *rule* or the
*harness*. These runs close both, in an isolated sandbox rootdir under `/private/tmp`
with a symlinked repo `.venv`, claiming no test-DB slot.

#### spike-4: What exactly does `pytest_runtest_logreport` report, per outcome shape?

- **Assumption**: "a report that is not `skipped` means a test executed" (the plan's original rule)
- **Method**: prototype — a probe plugin dumping `(when, outcome, wasxfail, failed)` for every report
- **Finding**: **The original rule is unusable, and the critique's suggested repair is also
  incomplete.** Measured tuples:

  | Test shape | setup | call | teardown |
  |---|---|---|---|
  | passing | passed | **passed** | passed |
  | failing | passed | **failed** | passed |
  | `pytest.skip()` in the body | passed | skipped | passed |
  | fixture-level skip (the `scratch_test_db` shape) | skipped | *(no call report)* | passed |
  | `@pytest.mark.skip` | skipped | *(no call report)* | passed |
  | `@pytest.mark.xfail` that fails | passed | **skipped, `wasxfail=True`** | passed |
  | `@pytest.mark.xfail` that passes | passed | **passed, `wasxfail=True`** | passed |
  | fixture raising in setup | **failed** | *(no call report)* | passed |

  Three candidate rules run against the same rootdirs:

  | Rule | all fixture-skip | all body-skip | all marker-skip | mixed (1 pass, 1 fail, 1 xfail, 1 xpass, 1 error, 3 skip) |
  |---|---|---|---|---|
  | Plan round 1: `outcome != "skipped"` | 2 | 4 | 2 | 17 |
  | Critique note: `when == "call"` or failed setup/teardown | 0 | **2** | 0 | 6 |
  | **Settled rule** (below) | **0** | **0** | **0** | **5** |

- **Confidence**: high — every cell was executed and read back.
- **Impact on plan**: settles the counting rule as measured text, and shows the body-skip
  channel the critique's own note would still have missed.

#### spike-5: Can a `tmp_path` sandbox run a *real* pytest session through the wrapper?

- **Assumption**: "`tests/unit/test_worktree_venv_absent_guard.py`'s `--version` model can drive the new guard"
- **Method**: prototype
- **Finding**: **The `--version` model cannot, and a viable alternative was built and run.**
  A sandbox rootdir that (a) carries its own `pyproject.toml` with `[tool.pytest.ini_options]`
  so the wrapper resolves `REPO_ROOT` to the sandbox, (b) has `.git` as a **directory** so the
  #3033 worktree guard stays silent, (c) **symlinks** the repo's real `.venv` in so `PYTEST_BIN`
  is a real pytest, and (d) carries **no `.python-version`** so `check-interpreter-pin.sh`
  returns 0 at its "no pin file" early exit — runs a genuine pytest session end to end through
  `scripts/pytest-clean.sh`. Measured: an all-skip sandbox produced a real `2 skipped in 0.01s`
  and the injected plugin wrote its verdict file.

  Plugin resolution was measured directly rather than assumed. With a decoy module of the same
  name on `PYTHONPATH` behind the sandbox, `-p` resolved to the **sandbox** copy
  (`sbx/pytest_executed_count.py`); with the sandbox absent from `PYTHONPATH`, it resolved to the
  decoy. The wrapper's own `export PYTHONPATH="$REPO_ROOT:..."` (`scripts/pytest-clean.sh:168`)
  is what makes the first case true, and the second is exactly the false-pass the negative
  control has to rule out.

- **Confidence**: high
- **Impact on plan**: replaces the unusable structural model with a measured one, and supplies
  the negative control the tests must run first.

#### spike-6: End-to-end prototype of the whole guard

- **Assumption**: "the pass-through allowlist plus the settled counting rule actually produce
  the intended behavior across every channel"
- **Method**: prototype — a throwaway copy of `scripts/pytest-clean.sh` carrying the injection
  and the verdict block, driven against sandbox rootdirs
- **Finding**: **Confirmed, including the mutation check.**

  | Channel | Wrapper exit | Observed |
  |---|---|---|
  | all fixture-skip (the production trigger) | **1** | `2 skipped`, then the ZERO TESTS diagnostic |
  | all body-skip | **1** | `2 skipped`, then the diagnostic |
  | all marker-skip | **1** | `2 skipped`, then the diagnostic |
  | all passing | **0** | `2 passed`, no diagnostic |
  | mixed with a real failure | **1** | `1 failed, 1 passed, ...` — pytest's own status, unchanged |
  | zero collected | **1** | `no tests ran` (pytest's own 5 → the guard also fires; both non-zero) |
  | `--collect-only` | **0** | verdict file reads `collectonly` |
  | `--version` | **0** | no verdict file at all |
  | escape hatch set, all fixture-skip | **0** | diagnostic **still printed** |
  | nested: outer verdict file pre-seeded `count 7` | — | outer file still `count 7` after the inner run |
  | **mutation**: verdict block deleted, all fixture-skip | **0** | the bug, reproduced |

- **Confidence**: high — every row was run.
- **Impact on plan**: turns the Verification table from exit-code assertions into observed-output
  assertions with known-good expectations, and supplies the mutation-check row.


## Settled Decisions

Round 1 left three identifiers open while the Verification table already pinned one of
them by grep. They are decided here as plan text so the builder carries a decision rather
than a question, and every task, risk and verification row below uses these names.

| Thing | Settled name | Why |
|---|---|---|
| Count-file path variable | `PYTEST_CLEAN_COUNT_FILE` | Private wrapper state, **never** caller-supplied — see the unconditional-mint rule below. |
| Escape hatch | `PYTEST_ALLOW_ZERO_TESTS` | Unset means the guard is on. Reads as what it does at a call site. |
| Plugin module | `pytest_executed_count.py` at the repo root, loaded as `-p pytest_executed_count` | A repo-root module has no package `__init__` to execute. `tools/__init__.py` arms the Redis flush guard on import (`tools/__init__.py:18-20`), so `-p tools.pytest_executed_count` would run that on every pytest invocation on the machine. A single top-level file is also the smallest thing a sandbox rootdir can reproduce, which the tests depend on. |
| Script under test, in the tests | `PYTEST_CLEAN_SCRIPT`, defaulting to `REPO_ROOT/scripts/pytest-clean.sh` | The mutation check must not edit `scripts/pytest-clean.sh` in the shared checkout — six lanes run concurrently and an in-place mutation breaks all of them. Reading the script path from an env var lets the mutation run against a copy in `/tmp`. |
| Mutation seam | `# BEGIN zero-executed guard (#3195)` / `# END zero-executed guard (#3195)` around the verdict block | Makes the mutation a deterministic one-line `sed` range delete rather than a hand edit, so the Verification row is reproducible by anyone. |

## Data Flow

1. **Entry point**: an agent or human runs `scripts/pytest-clean.sh <args>` — a mutation
   check in `/do-build`, a verification row, a reviewer's re-run.
2. **Wrapper preflight**: reaps orphan xdist workers, refuses a worktree with no usable
   `.venv` (#3033), refuses an off-pin interpreter (#2617), pins `PYTHONPATH` to the
   invoking checkout (`scripts/pytest-clean.sh:168`).
3. **Wrapper mints and injects.** It **unconditionally** mints
   `PYTEST_CLEAN_COUNT_FILE="$(mktemp -t pytest-clean-count)"` and exports it — no `:-`
   default, so an inherited value from an enclosing wrapper is discarded rather than
   honored. It then injects the plugin **only if the module exists in the invoking
   checkout**: `if [ -f "$REPO_ROOT/pytest_executed_count.py" ]; then set -- -p pytest_executed_count "$@"; fi`.
4. **Wrapper → pytest**: `"$PYTEST_BIN" "$@" &`, backgrounded so `$!` is the controller
   PID for the stall watcher.
5. **Plugin, controller process**: no-ops entirely when `PYTEST_CLEAN_COUNT_FILE` is unset,
   so a bare `pytest` is untouched. Otherwise `pytest_sessionstart` writes a `started`
   sentinel; every `pytest_runtest_logreport` matching the executed-report rule increments
   a counter; `pytest_sessionfinish` overwrites the file with `collectonly` or `count N`.
6. **Plugin, worker processes**: no writes at all — every hook returns early on
   `hasattr(config, "workerinput")`. xdist forwards worker reports to the controller, so
   the controller's tally is already the aggregate (spike-3, re-measured in spike-4 under
   `-n 2`: identical count).
7. **Wrapper post-run**: `wait` yields `PYTEST_EXIT`; the stall watcher is killed; workers
   are reaped. The wrapper reads the file, deletes it, and decides.
8. **Output**: today's exit code, unless the verdict says a session ran and executed
   nothing — in which case a named diagnostic goes to stderr and the wrapper exits 1.

The count file is the only new piece of state. It is minted, written and deleted inside a
single wrapper invocation, has exactly one writer, and is never inherited.

## Architectural Impact

- **New dependencies**: none. The plugin uses only pytest's own hook API and the standard
  library.
- **Interface changes**: none to the wrapper's CLI. Callers pass the same args and read
  the same exit code; only the set of conditions producing a non-zero exit grows.
- **Coupling**: adds one edge — the wrapper now injects a repo-local pytest plugin, so it
  depends on that module being importable. `PYTHONPATH` is already pinned to `REPO_ROOT`
  (`scripts/pytest-clean.sh:168`), and the injection is gated on the module existing in the
  invoking checkout, so a checkout without it runs exactly as today rather than aborting.
- **Data ownership**: unchanged. The plugin observes reports and owns nothing.
- **Reversibility**: high. Deleting the `-p` injection and the post-run check restores
  today's behavior exactly; nothing persists between runs.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 — the scope correction is settled in the Freshness Check and Spikes.
- Review rounds: 1

The mechanism is validated end to end by spike-2. What remains is writing it in the
house style, pinning it with tests, and proving the guard actually bites.

## Prerequisites

No prerequisites — this work has no external dependencies. It touches a bash wrapper, a
new pytest plugin module, and a new test file, all inside the repo.

## Solution

### Key Elements

- **A zero-execution guard in the wrapper**: after `wait`, before `exit`, the wrapper
  decides whether the run it just supervised actually executed anything, and refuses to
  hand back a success it cannot justify.
- **An executed-count reporter plugin**: `pytest_executed_count.py` at the repo root, which
  the wrapper injects with `-p`. It counts executed test reports in the controller and
  records a verdict to the file the wrapper named. It changes no pytest behavior, prints
  nothing, and no-ops when the env var is unset.
- **One pass-through predicate, not a state enumeration**: the wrapper passes through on an
  *absent* file, a `collectonly` verdict, or a `count` of at least one. **Everything else
  fails closed** — `count 0`, a surviving `started` sentinel, an empty file, a truncated
  write, unparseable bytes. Inverting the test this way is what makes the guard total: a
  state nobody anticipated lands on the safe side by construction rather than by having
  been enumerated.

### Flow

`scripts/pytest-clean.sh tests/unit/test_guard.py` → existing preflight guards →
**wrapper mints the count file and injects `-p` if the plugin exists here** → pytest runs →
**plugin records the verdict** → wrapper reaps workers → **wrapper reads and deletes the
file** → one of:

- *`count N`, N ≥ 1* → **exit with pytest's status** (today's behavior, the overwhelming majority)
- *`collectonly`* → **exit with pytest's status**
- *file absent* → **exit with pytest's status** (no session ran; `--version` and friends)
- *anything else* → **stderr diagnostic, exit 1**

### Technical Approach

- **The counting rule, as measured (spike-4).** A report counts as an executed test when:

  ```python
  if report.when == "call" and (report.outcome != "skipped" or hasattr(report, "wasxfail")):
      executed += 1
  elif report.when in ("setup", "teardown") and report.failed:
      executed += 1
  ```

  Every clause is load-bearing and every one was measured:
  - `when == "call"` alone is **not** enough. A body-level `pytest.skip()` produces a
    *call* report with `outcome == "skipped"`, so a rootdir of body-skips counted **2**
    under that rule while pytest printed `2 skipped`.
  - `outcome != "skipped"` alone is **not** enough — the round-1 defect. Setup and teardown
    of a skipped test both report `passed`, so an only-skipped rootdir counted 2, 4, or 2
    depending on the skip shape and the guard never fired.
  - `hasattr(report, "wasxfail")` is required because an **xfail** reports
    `call/skipped/wasxfail=True` and an **xpass** reports `call/passed/wasxfail=True`.
    Both genuinely executed; dropping the clause would undercount an xfail-heavy selection
    toward the fail-closed side.
  - The `setup`/`teardown` failure clause catches a fixture that raises, which produces a
    *failed setup* report and **no call report at all**. Without it a rootdir whose every
    test errors in setup would count zero and be reported as "nothing executed" when the
    truth is "everything errored" — a misattributed diagnostic on an already-red run.

  Against the mixed rootdir (1 pass, 1 fail, 1 xfail, 1 xpass, 1 setup error, 3 skips) the
  rule counts **5**, matching pytest's own summary, identically at `-n 0` and `-n 2`.

- **Detection is a plugin, not output parsing.** Settled by spike-2: the wrapper cannot
  capture pytest's stdout without breaking the #2574 stall watcher and taking pytest off
  a TTY. The plugin route touches neither.
- **The controller is the sole writer.** Every hook returns early on
  `hasattr(config, "workerinput")`.
- **The count file is minted unconditionally and never inherited.**
  `PYTEST_CLEAN_COUNT_FILE="$(mktemp -t pytest-clean-count)"; export PYTEST_CLEAN_COUNT_FILE`
  — no `"${PYTEST_CLEAN_COUNT_FILE:-$(mktemp)}"`. This differs deliberately from
  `PYTEST_STALL_LIMIT_S`, which is a caller-tunable knob; the count file is private wrapper
  state, and honoring an inherited path would let a nested wrapper invocation overwrite its
  parent's verdict. The tests nest the wrapper inside the wrapper, so this is not
  hypothetical (Race 4).
- **The injection is gated on the plugin existing in the invoking checkout.** An
  unimportable `-p` module aborts pytest before a single test runs; measured, it exits
  **1** with `ImportError: Error importing plugin`, indistinguishable by exit code from the
  three existing refusals. Six lanes share this machine, so a bad injection on `main` takes
  all of them down at once. The gate is
  `if [ -f "$REPO_ROOT/pytest_executed_count.py" ]; then set -- -p pytest_executed_count "$@"; fi`,
  placed after `export PYTHONPATH` (line 168) and before `"$PYTEST_BIN" "$@" &` (line 287).
  `set --` is the only rewrite that preserves `"$@"` quoting; do not build a string.
  The existence test uses **`$REPO_ROOT`, never `$SCRIPT_ROOT`** — pointing it at
  `$SCRIPT_ROOT` would let the primary checkout's plugin serve a worktree run, the exact
  #3033 bleed the wrapper exists to prevent.
- **The escape hatch suppresses the exit, never the message.** `PYTEST_ALLOW_ZERO_TESTS`
  cannot be allowed to restore the defect this plan removes: a silent green on a run that
  executed nothing is indistinguishable in a transcript from a real pass, which is the
  whole issue. So the diagnostic prints either way, and only the `exit 1` is conditional:

  ```bash
  <full diagnostic to stderr>
  if [ -z "${PYTEST_ALLOW_ZERO_TESTS:-}" ]; then
      exit 1
  fi
  ```

  Measured on the prototype: with the hatch set, an all-skip run exits 0 **and** still
  prints `pytest-clean: ZERO TESTS EXECUTED (#3195) — this run proves nothing.` A reader of
  a mutation-check transcript can therefore never mistake a hatched run for a pass. The
  escape-hatch test asserts that message; asserting only the exit code is the assertion
  this whole issue proves worthless.
- **The verdict block is a single `case` with a pass-through allowlist:**

  ```bash
  case "$COUNT_VERDICT" in
      ""|collectonly)  : ;;          # no session ran, or a collect-only run
      "count "[1-9]*)  : ;;          # at least one test executed
      *)               <diagnostic>; [ -z "${PYTEST_ALLOW_ZERO_TESTS:-}" ] && exit 1 ;;
  esac
  ```

  Verified against `""`, `collectonly`, `count 0`, `count 1`, `count 10`, `started`,
  `coun` (truncated), whitespace, and `count -1`: only the first two and the positive
  counts pass through.
- **A file-absent run is a pass-through, deliberately.** `pytest --version` runs no session
  and writes nothing, and `scripts/pytest-clean.sh --version` is how the existing guard
  tests drive the wrapper. Measured: no file is created.
- **Temp-file hygiene**: `mktemp` per invocation, removed both in the verdict block and in
  the existing `cleanup` trap so an interrupted run leaves nothing behind.

## Failure Path Test Strategy

### The test harness (settled — the round-1 model could not exercise the guard)

`tests/unit/test_worktree_venv_absent_guard.py` was named in round 1 as the structural
model. It is not usable as one. That file provisions a **fake** `.venv/bin/pytest` (a shell
script echoing `pytest 0.0 (fake)`, lines 52-54) and drives the wrapper with `--version`, so
no pytest session ever starts, the plugin never loads, and every new case would land on
"file absent → pass through": the all-skip test would assert non-zero and receive 0. A
vacuous green in the tests written to prevent vacuous greens.

The harness that **was built and run** (spike-5, spike-6) is a sandbox rootdir under
`tmp_path` with four properties, each of which is required and each of which was measured:

| Property | Why |
|---|---|
| its own `pyproject.toml` with `[tool.pytest.ini_options]` and an `addopts` of its own | the wrapper resolves `REPO_ROOT` from cwd only when this file matches (`scripts/pytest-clean.sh:35`); the sandbox's own `addopts` also keeps the repo's `-n auto --timeout=420` out of a two-test run |
| `.git` as a **directory** | a `.git` *file* means linked worktree, which trips the #3033 guard (line 143) before pytest starts |
| a **symlink** to the repo's real `.venv` | makes `PYTEST_BIN` a real pytest (line 178). The fake-pytest model cannot run a session at all |
| **no** `.python-version` | `check-interpreter-pin.sh` returns 0 at its "no pin file" early exit (line 33), so the pin guard stays silent on a sandbox that has no pin of its own |
| a **copy** of `pytest_executed_count.py` written into the sandbox root | the subject under test must be the sandbox's copy, not the repo's |

Measured end to end: an all-skip sandbox driven through `scripts/pytest-clean.sh` produced a
genuine `2 skipped in 0.01s` and the injected plugin wrote its verdict file.

**Two rules the harness must follow, both of which round 1 identified as false-pass channels:**

1. **Negative control, run before any guard assertion.** Assert the plugin actually loaded
   from the sandbox:
   `subprocess.run([sandbox_venv_python, "-c", "import pytest_executed_count as m; print(m.__file__)"], env=sandbox_env)`
   and assert the printed path is under `tmp_path`. Measured why this matters: with a decoy
   module of the same name behind the sandbox on `PYTHONPATH`, `-p` resolved to the
   **sandbox** copy; with the sandbox absent from `PYTHONPATH`, it resolved to the
   **decoy**. Without this control a test can pass while exercising the repo's plugin, so a
   builder mutating the plugin would see no change.
2. **Build every subprocess env from a copy of `os.environ` with `PYTHONPATH` and
   `PYTEST_CLEAN_COUNT_FILE` removed.** The outer wrapper exports both
   (`scripts/pytest-clean.sh:168` and the new mint), and a subprocess inherits them, so an
   ambient-env test passes for the wrong reason.

A real linked worktree with its own `uv sync --extra dev` venv remains in task 4 as the
`PYTHONPATH`-resolution check the sandbox cannot make (Risk 3), not as the primary harness.

### Exception Handling Coverage
- [ ] The plugin's file writes are the only I/O that can raise. A write failure must not
      take down a test run that would otherwise have succeeded, so the write helper swallows
      `OSError` — a deliberate fail-*open* on an unwritable temp dir. Assert the observable
      consequence: with an unwritable count-file path, the run still completes and the
      wrapper still returns pytest's status.
- [ ] No `except Exception: pass` blocks are introduced. The one handler is narrow
      (`OSError`) and its behavior is pinned by the test above.
- [ ] The wrapper's read side tolerates a missing or unreadable file without a bash error:
      `COUNT_VERDICT="$(cat "$PYTEST_CLEAN_COUNT_FILE" 2>/dev/null || true)"` under `set -u`.

### Empty/Invalid Input Handling
- [ ] The pass-through allowlist is a **shell-level** unit check, not a pytest-driven one.
      The count file is minted by the wrapper and written only by the plugin, so no test can
      seed it with garbage through the wrapper's public surface; driving pytest to produce a
      truncated file is not reproducible. Assert the `case` predicate directly by sourcing
      it, or by a small bash loop over `"" collectonly "count 0" "count 1" "count 10" started coun "  "`.
      This is what makes "fail closed on everything not allowlisted" a tested claim rather
      than a stated one.
- [ ] `scripts/pytest-clean.sh` with no arguments at all: pytest runs the sandbox's
      `testpaths`, so this is an ordinary run and must behave as one.
- [ ] `--version` and `--help` pass through untouched — no session, no file, no guard.

### Error State Rendering
- [ ] The diagnostic goes to **stderr**, leads with `ZERO TESTS EXECUTED (#3195)`, states
      that the run proves nothing, names the likely cause (test-DB pool exhaustion at
      fixture setup), names the remedy (`scripts/reap-xdist.sh --apply`), and names the
      escape hatch. Assert the message text, not just the exit code.
- [ ] The message is distinguishable from the three existing wrapper refusals (`no usable
      .venv`, `off-pin interpreter`, `WEDGED`), so a caller reading stderr can tell which
      guard fired. Pin this by asserting the other three headlines are absent.
- [ ] With `PYTEST_ALLOW_ZERO_TESTS` set, the message is **still printed** while the exit is
      0. Assert the message; the exit code alone is not an assertion here.

## Test Impact

- [ ] `tests/unit/test_worktree_venv_absent_guard.py` — **UPDATE (verify only, expect no
      change)**. It drives the wrapper via `--version`, the file-absent pass-through path.
      It must keep passing untouched; if it does not, the guard is wrong about `--version`.
      Note it is **not** the structural model for the new file (see the harness section).
- [ ] `tests/unit/test_interpreter_pin_guard.py` — **UPDATE (verify only, expect no
      change)**. Same `--version` drive path, same reasoning.
- [ ] `tests/unit/test_feature_map_markers.py` — **verify only**. Confirmed at plan time:
      `resolve_marker("test_pytest_clean_zero_tests.py")` returns `(None, None)`, matching
      both sibling wrapper-guard test files, so no `FEATURE_MAP` and no `KNOWN_MISTAGS`
      entry is needed. Re-confirm after the file is named.
- [ ] `tests/conftest.py` — **no change**. `scratch_test_db`'s skip is correct behavior and
      stays; this plan changes what a *consumer* concludes from it.
- [ ] No existing test asserts the wrapper's exit code on a zero-execution run, so nothing
      is invalidated by making that case non-zero.

New coverage lands in one new file, `tests/unit/test_pytest_clean_zero_tests.py`, built on
the sandbox harness above. It must not point the wrapper at the repo's own `tests/` tree and
must not claim a test-DB slot — the sandbox rootdir keeps both true, verified in spike-5
where no slot was taken.

## Rabbit Holes

- **Making the pool-exhaustion skip a hard error instead.** The issue floats this as "a
  separate question" and it is: turning `scratch_test_db`'s skip into a failure changes
  the meaning of a shared fixture for every test that requests it, and would make a busy
  machine fail runs that are otherwise fine. This plan makes the *reader* honest; it does
  not relitigate #2628.
- **Counting skips as a threshold ("fail if >90% skipped").** Any threshold is a number
  someone has to defend, and it turns a crisp invariant — *this run proved nothing* —
  into a tuning knob. Zero is the only defensible line.
- **Teeing pytest's output to parse the summary line.** Explicitly rejected in spike-2.
  It breaks the #2574 wedge detector and takes pytest off a TTY. Do not revisit it because
  it looks like less code.
- **Auditing every caller that reads the wrapper's exit code.** The point of fixing the
  wrapper is that callers need no changes. Resist the urge to touch `/do-build`,
  `/do-pr-review`, or the verification runner.
- **Making the plugin also fix the "node down" reporting.** Crashed workers already exit
  5. Improving how that is *displayed* is a different issue.
- **"Simplifying" the counting rule.** It looks like four clauses where one would do, and
  round 1 of this plan made exactly that mistake in two different directions. Every clause
  has a measured rootdir behind it (spike-4). If it looks redundant, run the table before
  touching it.
- **Making the escape hatch quiet.** Suppressing the diagnostic along with the exit would
  restore the original defect under a different name. The hatch changes the exit code and
  nothing else.

## Risks

### Risk 1: The guard fires on a legitimate all-skipped run
**Impact:** A developer running a platform-gated or otherwise entirely-skipped selection
gets a refusal instead of a green, and reads it as a broken wrapper.
**Mitigation:** The diagnostic names `PYTEST_ALLOW_ZERO_TESTS` on the same screen. The hatch
suppresses only the exit — the message prints either way — so a hatched run can never be
mistaken for a real pass in a transcript. The message also names the pool-exhaustion cause
first, since that is the overwhelmingly likely one. Accepting this false-positive class is
the deliberate trade: a run that executed nothing proved nothing, and the whole point is
that callers stop reading it as proof.

### Risk 2: The `-p` injection collides with a caller's own `-p` argument
**Impact:** A caller passing `-p no:something` could be reordered or shadowed, changing
plugin loading in a way that is hard to attribute.
**Mitigation:** pytest accepts repeated `-p` flags and applies them in order, so prepending
via `set -- -p pytest_executed_count "$@"` leaves every caller-supplied `-p` in force. Pin
this with a test that passes `-p no:cacheprovider` through the wrapper and asserts both the
guard and the caller's flag took effect.

### Risk 3: The plugin is not importable from the invoking checkout
**Impact:** pytest aborts before any test runs, on every invocation, in every checkout —
a total outage of the test wrapper across the machine. Measured: it exits **1** with
`ImportError: Error importing plugin` (round 1 stated exit 4; that is wrong, and the
correction matters because exit 1 is indistinguishable from the three existing refusals).
Six lanes share this machine, so a bad injection on `main` takes all of them down at once.
**Mitigation, in the wrapper rather than after the fact:** the injection is gated on
`[ -f "$REPO_ROOT/pytest_executed_count.py" ]`, so a checkout without the module simply runs
as it does today instead of aborting. The gate uses `$REPO_ROOT`, never `$SCRIPT_ROOT`, so
the primary checkout's plugin can never serve a worktree run (#3033). `PYTHONPATH` is
already pinned to `REPO_ROOT` at line 168, before the run. Verified on a real linked
worktree in task 4 as a second, independent check — no longer the only one.

### Risk 4: The guard is written but never actually bites
**Impact:** The worst outcome available — a guard against false greens that is itself a
false green, exactly the failure this issue reports.
**Mitigation:** Mutation-check the guard specifically: delete the wrapper's verdict block,
confirm the new tests go **red**, restore it, confirm they go green. Already demonstrated on
the prototype (spike-6): with the block deleted an all-fixture-skip run exits **0**; with it
present the same run exits **1**. This is a Verification row, not only PR prose. Do not
accept a green test run as evidence that the guard works.

### Risk 5: The tests pass while exercising the repo's plugin instead of the sandbox's
**Impact:** The tests go green against `main`'s plugin no matter what the builder writes,
so the whole test file is decoration. This is the #3033 failure reproduced inside the tests
written to prevent a #3033-shaped bug.
**Mitigation:** The negative control in the Failure Path Test Strategy runs before any guard
assertion and asserts the resolved `pytest_executed_count.__file__` is under `tmp_path`,
and every subprocess env is built from a copy of `os.environ` with `PYTHONPATH` and
`PYTEST_CLEAN_COUNT_FILE` removed. Measured that resolution flips with `PYTHONPATH`, so
this control has been shown to be able to fail.

## Race Conditions

### Race 1: Two concurrent wrapper invocations sharing a count file
**Location:** `scripts/pytest-clean.sh`, the new mint and read.
**Trigger:** Several agents run the wrapper simultaneously — the normal state of this
machine, and the exact condition that causes the pool exhaustion in the first place.
**Data prerequisite:** Each invocation's count file must be written only by its own pytest
controller.
**State prerequisite:** No fixed path may be shared between invocations.
**Mitigation:** `mktemp` per invocation, passed through the environment, never a constant
under `/tmp` or the repo. Removed in the verdict block and in the existing `cleanup` trap.

### Race 2: The wrapper reads the file before the controller finished writing
**Location:** `scripts/pytest-clean.sh`, between `wait "$PYTEST_PID"` and the guard.
**Trigger:** The read racing the plugin's `sessionfinish` write.
**Data prerequisite:** The final verdict must be on disk before the wrapper reads it.
**State prerequisite:** pytest must have fully exited.
**Mitigation:** The read happens after `wait "$PYTEST_PID"` returns, which is after the
controller process exited and therefore after its `sessionfinish` completed. No polling or
sleep is needed, and none should be added.

### Race 3: The stall watcher kills the controller mid-write
**Location:** `scripts/pytest-clean.sh:257-279`.
**Trigger:** A wedged run is `SIGKILL`ed while the plugin is writing.
**Data prerequisite:** A truncated or absent file must not be read as a valid verdict.
**State prerequisite:** none.
**Mitigation:** Covered by construction rather than by a dedicated branch: the pass-through
allowlist admits only an empty read, `collectonly`, and `count [1-9]*`. A surviving
`started` sentinel and a truncated `coun` both fall to the fail-closed default, verified in
the shell-level predicate check.

### Race 4: A nested wrapper invocation overwrites its parent's verdict
**Location:** `scripts/pytest-clean.sh`, the count-file mint.
**Trigger:** **Guaranteed by this plan's own test design.** `tests/unit/test_pytest_clean_zero_tests.py`
runs *under* `scripts/pytest-clean.sh` and drives `scripts/pytest-clean.sh` by
`subprocess.run`, so the inner wrapper inherits the outer run's exported
`PYTEST_CLEAN_COUNT_FILE`.
**Data prerequisite:** The outer run's verdict file must reflect the outer run only.
**State prerequisite:** The wrapper must never honor an inherited count-file path.
**Mitigation:** The mint is unconditional — `PYTEST_CLEAN_COUNT_FILE="$(mktemp -t pytest-clean-count)"`
with no `:-` default — and the plugin no-ops when the variable is unset, so a bare `pytest`
writes nothing anywhere. Verified on the prototype: an outer file pre-seeded with `count 7`
still read `count 7` after a nested wrapper run completed. Pinned by a test asserting
exactly that.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2535] Fixing the underlying test-DB pool contention. Exhaustion past
  ~5 concurrent agents is the trigger for the skip channel, but relieving it is the
  concurrent-run-corruption work already tracked on #2535. This plan makes the symptom
  legible, which is a prerequisite for measuring that work, not a substitute for it.
- [SEPARATE-SLUG #2535] Turning `scratch_test_db`'s pool-exhaustion `pytest.skip` into a
  hard error under CI-style runs. The issue itself flags this as a separate question. It
  changes a shared fixture's contract for every consumer and belongs with the pool work.
- Nothing else is deferred. Every item the issue raises that this plan can finish — the
  zero-collection regression pin, the fully-skipped test, the wrapper change, the docs — is
  in scope and enumerated in Step by Step Tasks.

## Update System

No update system changes required. `scripts/pytest-clean.sh` and the new plugin module are
both plain repo files that arrive with any `git pull`; `/update` already syncs the checkout
and runs `uv sync`. The plugin adds no dependency, no config file, and no migration, and
the guard needs no state carried across versions. Existing installations get the new
behavior on their next pull with no action.

## Agent Integration

No agent integration required. `scripts/pytest-clean.sh` is already the sanctioned way
every agent runs tests (CLAUDE.md, Commands), invoked through the Bash tool. This change
alters what that existing surface returns; it adds no CLI entry point in
`pyproject.toml [project.scripts]`, no MCP tool, and nothing the bridge imports.

The one integration-shaped consequence is worth stating so it is not mistaken for missing
work: **every caller that reads the wrapper's exit code inherits the fix without being
touched.** `/do-build`'s mutation checks, `/do-pr-review`'s and `/do-plan-critique`'s
verification rows, and the machine-readable `## Verification` runner all already branch on
that exit code. That is precisely why the fix belongs in the wrapper.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pytest-clean-zero-test-guard.md` — what the guard detects, the
      pass-through allowlist and why it is expressed as an allowlist, the measured
      report-tuple table from spike-4 (the single most re-derivable thing in this change),
      why output parsing was rejected (#2574 stall watcher), what `PYTEST_ALLOW_ZERO_TESTS`
      does and does not suppress, and the measured exit-code table from spike-1 so the next
      reader does not have to re-derive which channels already fail closed.
- [ ] Add a row to the `docs/features/README.md` index table.
- [ ] Update `docs/features/test-concurrency-coordination.md` — it is the standing home for
      the pool/claim story and must now say that a pool-exhausted run fails closed at the
      wrapper instead of reading green.

### External Documentation Site
Not applicable — this repo publishes no external documentation site.

### Inline Documentation
- [ ] Header comment in `pytest_executed_count.py`: why it exists, why the controller is
      the sole writer, the verdict values it can write, and the spike-4 report-tuple table
      with a plain instruction not to simplify the counting rule.
- [ ] Block comment in `scripts/pytest-clean.sh` above the guard, matching the house style
      of the #3033 and #2574 guards: the failure it prevents, the measured evidence, and
      the issue number.
- [ ] `CLAUDE.md`'s "Non-obvious behavior" bullet on `scripts/pytest-clean.sh` gains the
      zero-executed refusal alongside the existing off-pin and worktree-venv aborts.

## Success Criteria

- [ ] A run in which every test skips exits **non-zero** with the named diagnostic on
      stderr, for all three skip shapes: fixture-level (the `scratch_test_db` shape),
      body-level `pytest.skip()`, and `@pytest.mark.skip`.
- [ ] A zero-collection run exits non-zero (regression pin — already true at exit 5).
- [ ] A run with at least one executed test exits with pytest's own status, unchanged —
      an all-passing run stays 0, a run with a failure stays non-zero.
- [ ] `--version`, `--help`, and `--collect-only` through the wrapper are unaffected.
- [ ] `tests/unit/test_worktree_venv_absent_guard.py` and
      `tests/unit/test_interpreter_pin_guard.py` pass unmodified.
- [ ] **Mutation check, per guard**: deleting the wrapper's verdict block turns the new
      zero-executed tests red; restoring it turns them green. Both outputs pasted in the PR,
      and the check is also a Verification row.
- [ ] **Negative control passes**: the sandbox's own `pytest_executed_count` is the module
      that loads, proven by its `__file__` resolving under `tmp_path`.
- [ ] `PYTEST_ALLOW_ZERO_TESTS` suppresses the exit **and still prints the diagnostic**,
      verified by asserting the message text.
- [ ] A nested wrapper invocation leaves the outer run's count file untouched.
- [ ] Verified on a real linked worktree, not only in a `tmp_path` sandbox.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

One agent, one serial chain. Round 1 declared four named agents for an `appetite: Small`
change whose every task is `Parallel: false` and depends on the one before it — a roster
that bought no concurrency and added three handoffs across one bash hunk, one small module,
and one test file. The one split worth keeping is the validator, because the mutation check
must be run by someone who did not write the guard.

### Team Members

- **Builder**
  - Name: `guard-builder`
  - Role: the plugin module, the wrapper hunk, the test file, and the documentation.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `guard-validator`
  - Role: the mutation check and the real-worktree verification. Runs against the builder's
    output without having written it.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Executed-count reporter plugin
- **Task ID**: build-plugin
- **Depends On**: none
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create)
- **Informed By**: spike-2 (the file protocol), spike-3 and spike-4 (controller is the sole writer, aggregate under xdist), spike-4 (the counting rule)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `pytest_executed_count.py` at the **repo root** (not under `tools/` — see Settled Decisions).
- Read the output path from `PYTEST_CLEAN_COUNT_FILE`; when it is unset, every hook returns
  immediately so a bare `pytest` is untouched.
- Return early in workers on `hasattr(config, "workerinput")` in every hook.
- `pytest_sessionstart`: write the `started` sentinel.
- `pytest_runtest_logreport`: count with the exact measured rule —
  `report.when == "call" and (report.outcome != "skipped" or hasattr(report, "wasxfail"))`,
  or `report.when in ("setup", "teardown") and report.failed`. Do **not** simplify this to
  `when == "call"` alone (counts body-skips) or to `outcome != "skipped"` (the round-1 defect).
- `pytest_sessionfinish`: write `collectonly` when `config.option.collectonly` is set,
  otherwise `count N`.
- Wrap the writes in a narrow `OSError` handler so an unwritable path degrades to today's
  behavior instead of failing the run.
- Header comment per the Inline Documentation task, including the measured report-tuple
  table from spike-4 so the rule is not "simplified" back into the bug.

### 2. Wrapper guard
- **Task ID**: build-wrapper
- **Depends On**: build-plugin
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create), tests/unit/test_worktree_venv_absent_guard.py, tests/unit/test_interpreter_pin_guard.py
- **Informed By**: spike-1 (only the all-skipped channel returns 0), spike-2 (no output capture), spike-6 (the prototype)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- After `export PYTHONPATH` (line 168) and before `"$PYTEST_BIN" "$@" &` (line 287):
  mint `PYTEST_CLEAN_COUNT_FILE="$(mktemp -t pytest-clean-count)"` **unconditionally**,
  export it, and add its removal to the existing `cleanup` trap.
- Gate the injection on the module existing in the invoking checkout:
  `if [ -f "$REPO_ROOT/pytest_executed_count.py" ]; then set -- -p pytest_executed_count "$@"; fi`.
  `$REPO_ROOT`, never `$SCRIPT_ROOT`. `set --`, never a string.
- After `wait "$PYTEST_PID"` and the existing reap, read the file, delete it, and apply the
  pass-through allowlist `case`: `""|collectonly` and `"count "[1-9]*` pass through;
  everything else prints the diagnostic and exits 1 unless `PYTEST_ALLOW_ZERO_TESTS` is set.
- The diagnostic goes to stderr, leads with `ZERO TESTS EXECUTED (#3195)`, and is printed
  **whether or not** the hatch is set.
- Bracket the verdict block with `# BEGIN zero-executed guard (#3195)` and
  `# END zero-executed guard (#3195)` so the mutation check is a deterministic `sed` range
  delete against a copy, never a hand edit of the shared checkout.
- Preserve the existing exit code on every pass-through path. Do not reorder or alter the
  preflight guards, the stall watcher, or the reaping.

### 3. Guard tests
- **Task ID**: build-tests
- **Depends On**: build-wrapper
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create)
- **Informed By**: spike-5 (the harness), spike-6 (the expected behavior matrix)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Resolve the script under test as `os.environ.get("PYTEST_CLEAN_SCRIPT", REPO_ROOT / "scripts" / "pytest-clean.sh")`
  so the mutation check can point the same tests at a mutated copy in `/tmp` instead of
  editing the shared checkout out from under five peer lanes.
- Build the sandbox rootdir exactly as the Failure Path Test Strategy specifies: own
  `pyproject.toml`, `.git` as a **directory**, a **symlink** to the repo `.venv`, no
  `.python-version`, and a **copy** of `pytest_executed_count.py`.
- Run the **negative control first**: assert the resolved `pytest_executed_count.__file__`
  is under `tmp_path`. Build every subprocess env from a copy of `os.environ` with
  `PYTHONPATH` and `PYTEST_CLEAN_COUNT_FILE` removed.
- Cases, with the spike-6 expectations: fixture-skip / body-skip / marker-skip → non-zero
  with the diagnostic; zero-collected → non-zero; all passing → 0 with no diagnostic; a
  failing test → pytest's own non-zero; `--collect-only` → 0; `--version` → 0; hatch set →
  0 **and the message still printed**; a caller's `-p no:cacheprovider` still applies;
  nested invocation leaves a pre-seeded outer file untouched; an unwritable count-file path
  still completes and returns pytest's status.
- A shell-level check of the pass-through allowlist over
  `"" collectonly "count 0" "count 1" "count 10" started coun "  "`.
- Pin the diagnostic's text and assert the other three refusal headlines are absent.

### 4. Validate the guard actually bites
- **Task ID**: validate-guard
- **Depends On**: build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Mutation check, **against a copy**: `sed` the `BEGIN`/`END` guard range out of
  `scripts/pytest-clean.sh` into `/tmp/pc-mutated.sh`, run the new tests with
  `PYTEST_CLEAN_SCRIPT=/tmp/pc-mutated.sh`, confirm **red**. Re-run without the override,
  confirm **green**. Never edit `scripts/pytest-clean.sh` in place — peer lanes are running. Capture both outputs verbatim, reading the **passed count off
  the summary line**, not the exit code.
- Re-run `tests/unit/test_worktree_venv_absent_guard.py` and
  `tests/unit/test_interpreter_pin_guard.py` unmodified and confirm they pass.
- Run `tests/unit/test_feature_map_markers.py` and confirm the new file needs no
  `FEATURE_MAP` or `KNOWN_MISTAGS` entry.
- Provision a real linked worktree with its own `uv sync --extra dev` venv and confirm the
  plugin loads and the guard fires there — the sandbox does not prove `PYTHONPATH`
  resolution in a real worktree (Risk 3).
- Confirm the injection gate degrades safely: temporarily rename the plugin in a scratch
  checkout and confirm the wrapper runs as it does today rather than aborting.
- Run a normal targeted suite through the wrapper and confirm the exit code and terminal
  output are unchanged from today.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Execute every task in the Documentation section.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table and record the **observed output** for every row, not the exit
  code.
- Confirm every Success Criteria checkbox, including the pasted mutation-check evidence.

## Verification

Every row that could be satisfied by a run in which nothing executed asserts on **observed
output** instead of exit status. That is not stylistic: this table is executed *by the
wrapper under change*, so an exit-code-only table cannot detect its own bootstrap failure,
and on this machine past ~5 concurrent agents a whole file legitimately skips. `TC` numbers
below refer to the case names in `tests/unit/test_pytest_clean_zero_tests.py`.

| Check | Command | Expected |
|-------|---------|----------|
| Guard tests actually ran and passed | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q 2>&1 \| tail -2` | matches `[1-9][0-9]* passed`, at least 12 passed, and no `ZERO TESTS EXECUTED` |
| Sibling wrapper guards actually ran and passed | `scripts/pytest-clean.sh tests/unit/test_worktree_venv_absent_guard.py tests/unit/test_interpreter_pin_guard.py -q 2>&1 \| tail -2` | matches `[1-9][0-9]* passed`, at least 15 passed |
| Marker guard actually ran and passed | `scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q 2>&1 \| tail -2` | matches `[1-9][0-9]* passed` |
| **Mutation check: the guard bites** | `sed '/# BEGIN zero-executed guard (#3195)/,/# END zero-executed guard (#3195)/d' scripts/pytest-clean.sh > /tmp/pc-mutated.sh && chmod +x /tmp/pc-mutated.sh && PYTEST_CLEAN_SCRIPT=/tmp/pc-mutated.sh scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q 2>&1 | tail -3` | with the verdict block removed the output matches `[1-9][0-9]* failed` — the tests go **red**. A `passed`-only summary here means the guard is wired to nothing and the whole test file is decoration (Risk 4). Demonstrated on the prototype in spike-6. |
| Zero-executed run is refused, end to end | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q -k skip_shape 2>&1 \| tail -2` | matches `[1-9][0-9]* passed` and no `no tests ran` |
| Wrapper unaffected by `--version` | `scripts/pytest-clean.sh --version` | exit code 0 (the pass-through path *is* the assertion here — no session, so there is no summary line to read) |
| Guard is wired, not merely mentioned | `grep -c 'PYTEST_CLEAN_COUNT_FILE' scripts/pytest-clean.sh` | output ≥ 3 |
| Injection uses the arg-preserving form | `grep -c 'set -- -p pytest_executed_count' scripts/pytest-clean.sh` | output == 1 |
| Injection is gated on the invoking checkout | `grep -c 'REPO_ROOT/pytest_executed_count.py' scripts/pytest-clean.sh` | output == 1 |
| Injection gate does **not** use SCRIPT_ROOT (anti-criterion, #3033) | `grep -c 'SCRIPT_ROOT/pytest_executed_count' scripts/pytest-clean.sh` | match count == 0 |
| Count file is never inherited (anti-criterion, Race 4) | `grep -cE 'PYTEST_CLEAN_COUNT_FILE:-' scripts/pytest-clean.sh` | match count == 0 |
| Count file is not a fixed path (anti-criterion, Race 1) | `grep -c 'mktemp -t pytest-clean-count' scripts/pytest-clean.sh` | output == 1 |
| Output parsing was not introduced (anti-criterion, spike-2) | `grep -cE "tee " scripts/pytest-clean.sh` | match count == 0 (verified 0 on `main` today, so the pin is real rather than vacuous) |
| Hatch suppresses the exit, not the message (anti-criterion, critique row 5) | `grep -c 'ZERO TESTS EXECUTED' scripts/pytest-clean.sh` | output ≥ 1, and it appears **before** the `PYTEST_ALLOW_ZERO_TESTS` test in the file: `awk '/ZERO TESTS EXECUTED/{z=NR} /PYTEST_ALLOW_ZERO_TESTS/{a=NR} END{print (z && a && z<a)}'` prints `1` |
| conftest's `scratch_test_db` skip untouched (anti-criterion, No-Gos) | `git diff --quiet origin/main -- tests/conftest.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/pytest-clean-zero-test-guard.md` | exit code 0 |
| Feature doc is indexed | `grep -c pytest-clean-zero-test-guard docs/features/README.md` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

Round 2 — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), **sequential lenses** (the Agent tool is absent in this stage-runner context, so no finding below was independently corroborated by a separate agent; where two lenses converged it is noted in the Critic column), at plan hash `sha256:cd00ac21…`. Verdict: **NEEDS REVISION** (1 blocker, 3 concerns, 3 nits). Round 1's nine rows were all closed by the revision recorded at `revision_applied_at: 2026-09-07T02:48:33Z`; both round-1 blockers were re-measured in round 2 and confirmed genuinely fixed — the settled counting rule counts 0 on all three skip shapes at `-n 0` and `-n 2` and 5 on the mixed rootdir, and the sandbox harness runs a real session through the real wrapper (`3 skipped in 0.01s`).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness; History & Consistency | The mutation check drives a COPY of the wrapper at `/tmp/pc-mutated.sh`, but `scripts/pytest-clean.sh:34` derives `SCRIPT_ROOT` from `${BASH_SOURCE[0]}/..` and line 195 runs `"$SCRIPT_ROOT/scripts/check-interpreter-pin.sh"`. A copy at `/tmp` gives `SCRIPT_ROOT=/`, so it aborts with `//scripts/check-interpreter-pin.sh: No such file or directory` and `refusing to run against an off-pin interpreter` BEFORE pytest starts. Measured directly. Every test then goes red for a reason unrelated to the deleted verdict block, so the check that proves the guard bites confirms nothing. No house precedent exists: `tests/unit/test_worktree_venv_absent_guard.py:25-26` always invokes the real script by absolute path. | pending | Place the mutated copy at `<tmpdir>/scripts/pytest-clean.sh` with `scripts/check-interpreter-pin.sh` copied alongside: `M=$(mktemp -d); mkdir -p "$M/scripts"; sed '/# BEGIN zero-executed guard (#3195)/,/# END zero-executed guard (#3195)/d' scripts/pytest-clean.sh > "$M/scripts/pytest-clean.sh"; cp scripts/check-interpreter-pin.sh "$M/scripts/"; chmod +x "$M/scripts/"*.sh` then `PYTEST_CLEAN_SCRIPT="$M/scripts/pytest-clean.sh"`. Measured working: that layout ran a genuine `3 skipped in 0.01s` session. The control leg is not optional. The mutation run must ALSO show the all-passing case still exits 0 with a real `N passed` summary under the mutated script, otherwise any breakage of the copy reads as the guard biting. Add the sibling-layout constraint to the `PYTEST_CLEAN_SCRIPT` row in Settled Decisions. |
| CONCERN | Risk & Robustness; History & Consistency | The guard fires on runs that already failed for an unrelated reason and rewrites their exit code and headline. Measured: a rootdir with a syntax error yields `1 error during collection`, pytest exit 2, verdict `count 0`, so the wrapper would print `ZERO TESTS EXECUTED (#3195)` naming pool exhaustion and exit 1. Same after a wedge: `watch_for_stall` (`scripts/pytest-clean.sh:277-279`) kills the controller and returns, so the `WEDGED` banner is followed by a contradictory second headline. This also contradicts the plan itself: Problem promises "Pass-through behavior for every other case is unchanged" and Success Criteria pins zero-collection at exit 5, while spike-6 records the wrapper exiting 1 on that channel. | pending | Gate the verdict block on pytest's own success: `if [ "$PYTEST_EXIT" -eq 0 ]; then case "$COUNT_VERDICT" in ... esac; fi`. Every Success Criterion survives: the all-skip channel is exit 0 today and is exactly what the guard must catch, the zero-collection pin keeps its measured 5 instead of being rewritten to 1, and a wedge or collection error keeps its own diagnostic as the only headline. Correct spike-6's "zero collected -> 1" row to "-> 5 (the guard does not fire on an already-red run)". If the plan instead keeps the guard unconditional, delete "unchanged" from the Desired outcome and state that exit 2/5 runs become 1. |
| CONCERN | Risk & Robustness | The mutation seam covers the wrapper's verdict block only. The counting rule in `pytest_executed_count.py` is round 1's blocker site and the thing the plan most fears being simplified back into the bug, yet task 3's harness copies the plugin from a hard-coded `REPO_ROOT/pytest_executed_count.py` with no override, so mutating the rule requires editing the shared checkout, which task 4 forbids because peer lanes run there. The one guard the plan says must never be simplified is the one with no way to prove it bites. | pending | Read the plugin source as `os.environ.get("PYTEST_EXECUTED_COUNT_SOURCE", REPO_ROOT / "pytest_executed_count.py")` before copying it into the sandbox root, mirroring `PYTEST_CLEAN_SCRIPT`. Add a second mutation row that writes a copy whose rule is replaced by the round-1 defect (`if report.outcome != "skipped": executed += 1`) and asserts the three skip-shape cases go red. Measured reachable: the settled rule yields `count 0` on an all-skip rootdir at both `-n 0` and `-n 2`; the round-1 rule yields a non-zero count. Both mutation rows need the passing-case control leg from row 1. |
| CONCERN | Scope & Value | The Empty/Invalid Input Handling bullet offers "Assert the `case` predicate directly by sourcing it, or by a small bash loop over ...". `scripts/pytest-clean.sh` cannot be sourced (sourcing it runs pytest), so the builder takes the bash loop, which retypes the `case` patterns in the test. That check passes unchanged with the wrapper's own `case` deleted. It exercises a copy of the predicate, not the predicate, so "fail closed on everything not allowlisted" stays a stated claim rather than a tested one. | pending | Make the predicate a named function inside the BEGIN/END seam: `verdict_passes_through() { case "$1" in ""\|collectonly) return 0 ;; "count "[1-9]*) return 0 ;; *) return 1 ;; esac; }`, called from the verdict block. The test drives the real body by slicing it out of the script under test at run time: `bash -c 'source <(sed -n "/^verdict_passes_through()/,/^}/p" "$SCRIPT"); verdict_passes_through "count 0"'`. Include this check in the mutation run: with the seam deleted the `sed` slice comes back empty, and the check must fail rather than silently pass on an empty source. |
| NIT | Scope & Value | The Verification preamble says "`TC` numbers below refer to the case names" but no `TC` number appears in any row, and the end-to-end row selects with `-k skip_shape` while no task requires the three skip-shape cases to carry `skip_shape` in their names. | pending | Drop the orphaned `TC` sentence; state in task 3 that the fixture-, body- and marker-skip cases are named so `-k skip_shape` resolves. |
| NIT | History & Consistency | The escape hatch appears in two shapes a few paragraphs apart in Technical Approach: an `if [ -z "${PYTEST_ALLOW_ZERO_TESTS:-}" ]; then exit 1; fi` block and the one-line `[ -z ... ] && exit 1` inside the `case`. They are equivalent only because `scripts/pytest-clean.sh:29` sets `set -u` and not `set -e`, and because line 311 re-exits explicitly. | pending | Keep the `if` form and delete the `&&` variant, or note that the `&&` form leaves the `case` status at 1 and is safe only because the script does not use `set -e`. |
| NIT | Scope & Value | For an `appetite: Small` fix, provisioning a real linked worktree with its own `uv sync --extra dev` is the most expensive step in the plan, and Risk 3's in-wrapper gate already contains the failure it checks for. | pending | Keep it, but state in task 4 that the worktree leg is a one-shot PYTHONPATH-resolution confirmation and that an existing lane worktree may be reused rather than provisioned fresh. |
---

## Settled Questions

Round 1's three Open Questions are answered; none remain open, and the answers live in
**## Settled Decisions** and Technical Approach rather than here.

1. **Escape-hatch naming** → `PYTEST_ALLOW_ZERO_TESTS`, unset meaning the guard is on. It
   reads as what it does at a call site, and unlike `PYTEST_STALL_LIMIT_S` it suppresses only
   the exit, never the message.
2. **Where the plugin module lives** → `pytest_executed_count.py` at the repo root, loaded as
   `-p pytest_executed_count`. A repo-root module has no package `__init__` to execute;
   `tools/__init__.py` arms the Redis flush guard on import, which would then run on every
   pytest invocation on the machine. It is also the smallest thing a sandbox rootdir can
   reproduce, which the tests depend on.
3. **Should a fully-skipped run be non-zero for humans too?** → Yes, always, with the hatch.
   A run that executed nothing proved nothing regardless of who invoked it, and the hatch
   keeps the message so a human who sets it still sees what they gave up.
