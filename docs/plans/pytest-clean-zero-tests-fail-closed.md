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

1. **Entry point**: an agent or human runs `scripts/pytest-clean.sh <args>` — a mutation
   check in `/do-build`, a verification row, a reviewer's re-run.
2. **Wrapper preflight**: reaps orphan xdist workers, refuses a worktree with no usable
   `.venv` (#3033), refuses an off-pin interpreter (#2617), pins `PYTHONPATH` to the
   invoking checkout.
3. **Wrapper → pytest**: `"$PYTEST_BIN" "$@" &`, backgrounded so `$!` is the controller
   PID for the stall watcher. **New:** the wrapper first exports a temp-file path and
   prepends `-p <plugin>` to the args.
4. **Plugin, controller process**: `pytest_sessionstart` writes a `started` sentinel;
   every `pytest_runtest_logreport` that represents an executed (non-skipped) outcome
   increments a counter; `pytest_sessionfinish` overwrites the file with the final count,
   or with `collectonly` when `--collect-only` was requested.
5. **Plugin, worker processes**: no writes at all — workers return early on
   `hasattr(config, "workerinput")`. xdist forwards their reports to the controller, so
   the controller's count is already the aggregate (spike-3).
6. **Wrapper post-run**: `wait` yields `PYTEST_EXIT`; the stall watcher is killed; workers
   are reaped. **New:** the wrapper reads the temp file and decides.
7. **Output**: today's exit code, unless the file says a session ran and executed nothing
   — in which case a named diagnostic goes to stderr and the wrapper exits non-zero.

The temp file is the only new piece of state, it is created and deleted inside a single
wrapper invocation, and it has exactly one writer.

## Architectural Impact

- **New dependencies**: none. The plugin uses only pytest's own hook API and the standard
  library.
- **Interface changes**: none to the wrapper's CLI. Callers pass the same args and read
  the same exit code; only the set of conditions producing a non-zero exit grows.
- **Coupling**: adds one edge — the wrapper now injects a repo-local pytest plugin, so it
  depends on that module being importable. `PYTHONPATH` is already pinned to `REPO_ROOT`
  (`scripts/pytest-clean.sh:168`), which is what makes this safe; the plugin must live
  under the repo root and be resolvable from it.
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
- **An executed-count reporter plugin**: a small repo-local pytest plugin the wrapper
  injects with `-p`. It observes test reports in the controller and records the outcome
  to a file the wrapper names. It changes no pytest behavior and prints nothing.
- **A three-state protocol between them**: *absent*, *sentinel*, *verdict*. Absent means
  no session ever started (`--version`, `--help`, an argparse rejection) and the wrapper
  passes through untouched. The sentinel written at `sessionstart` and left unreplaced
  means a session began and never finished, which fails closed. A verdict of zero executed
  fails closed; a positive count or `collectonly` passes through.

### Flow

`scripts/pytest-clean.sh tests/unit/test_guard.py` → existing preflight guards →
**wrapper mints a temp file and injects `-p`** → pytest runs → **plugin records the
outcome** → wrapper reaps workers → **wrapper reads the file** → one of:

- *positive count* → **exit with pytest's status** (today's behavior, the overwhelming majority)
- *count 0* → **stderr diagnostic naming the pool-exhaustion cause, exit non-zero**
- *sentinel only* → **stderr diagnostic: the session died before finishing, exit non-zero**
- *file absent* → **exit with pytest's status** (no session ran; `--version` and friends)

### Technical Approach

- **Detection is a plugin, not output parsing.** Settled by spike-2: the wrapper cannot
  capture pytest's stdout without breaking the #2574 stall watcher and taking pytest off
  a TTY. The plugin route touches neither.
- **The controller is the sole writer.** Settled by spike-3: xdist forwards worker
  reports, so the controller's tally is the aggregate. The plugin returns early when
  `hasattr(config, "workerinput")`.
- **"Executed" means a test ran to a real outcome.** Passed, failed, xfailed, and xpassed
  all count as executed; skipped and deselected do not. Setup/teardown errors count —
  they are a genuine result and already exit non-zero, so counting them only keeps the
  guard from double-reporting.
- **Fail closed on an unfinished session.** A `sessionstart` sentinel that survives to the
  end is the case where the controller died between starting and summarizing. Today that
  can only arrive alongside a non-zero pytest exit, so the branch is defensive; writing it
  is what keeps the guard from silently degrading if that ever changes.
- **A file-absent run is a pass-through, deliberately.** `pytest --version` runs no
  session and writes nothing, and `scripts/pytest-clean.sh --version` is how the existing
  guard tests drive the wrapper (`tests/unit/test_worktree_venv_absent_guard.py`,
  `tests/unit/test_interpreter_pin_guard.py`). Failing closed on an absent file would
  break both.
- **`--collect-only` is a legitimate zero-execution run** and is recorded as such by the
  plugin from `config.option.collectonly` rather than inferred by the wrapper from args.
- **The escape hatch matches the house pattern.** `PYTEST_STALL_LIMIT_S=0` disables the
  wedge detector; an analogous env var disables this guard for the rare deliberate
  all-skip run. It defaults to on.
- **Temp-file hygiene**: created with `mktemp`, removed in the existing `cleanup` trap so
  an interrupted run leaves nothing behind.


## Architectural Impact

## Appetite

## Prerequisites

## Solution

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The plugin's file writes are the only I/O that can raise. A write failure must not
      take down a test run that would otherwise have succeeded, so `sessionstart` and
      `sessionfinish` swallow `OSError` — and that is a deliberate fail-*open* on an
      unwritable temp dir. Assert the observable consequence directly: with an unwritable
      path, the run still completes and the wrapper still returns pytest's status.
- [ ] No `except Exception: pass` blocks are introduced. The one handler is narrow
      (`OSError`) and its behavior is pinned by the test above.
- [ ] The wrapper's read side must tolerate a missing or unreadable file without a bash
      error; test by pointing the wrapper at a path it cannot create.

### Empty/Invalid Input Handling
- [ ] Empty file, whitespace-only file, and a file with unparseable contents each take the
      **fail-closed** branch, not a bash arithmetic error. A guard that crashes on garbage
      is a guard that can be bypassed by garbage.
- [ ] `scripts/pytest-clean.sh` with no arguments at all: pytest runs the full `testpaths`,
      so this is an ordinary run and must behave as one.
- [ ] `--version`, `--help`, and an invalid flag (argparse exit 4) each pass through
      untouched — no session, no file, no guard.

### Error State Rendering
- [ ] The zero-executed diagnostic goes to **stderr**, names the likely cause
      (test-DB pool exhaustion), names the remedy (`scripts/reap-xdist.sh --apply`), and
      names the escape hatch. Assert on the message text, not just the exit code — a
      guard that fires with an unattributable message costs the next agent an hour.
- [ ] The message must be distinguishable from the three existing wrapper refusals, so a
      caller reading stderr can tell which guard fired.


## Test Impact

- [ ] `tests/unit/test_worktree_venv_absent_guard.py` — **UPDATE (verify only, expect no
      change)**. It drives the wrapper via `scripts/pytest-clean.sh --version`, which is
      the file-absent pass-through path. It must keep passing untouched; if it does not,
      the guard is wrong about `--version`.
- [ ] `tests/unit/test_interpreter_pin_guard.py` — **UPDATE (verify only, expect no
      change)**. Same `--version` drive path, same reasoning.
- [ ] `tests/unit/test_feature_map_markers.py` — **verify only**. The new test file must
      resolve to a marker consistently with the guard's rules. Confirmed at plan time:
      `resolve_marker("test_pytest_clean_zero_tests.py")` returns `(None, None)`, matching
      both sibling wrapper-guard test files, so no `FEATURE_MAP` entry and no
      `KNOWN_MISTAGS` entry is needed. Re-confirm after the file is named, since the name
      is what decides this.
- [ ] `tests/conftest.py` — **no change**. `scratch_test_db`'s skip is correct behavior
      and stays; this plan changes what a *consumer* concludes from it, not the fixture.
- [ ] No existing test asserts the wrapper's exit code on a zero-execution run, so nothing
      is invalidated by making that case non-zero.

New coverage lands in one new file, `tests/unit/test_pytest_clean_zero_tests.py`, modeled
structurally on `tests/unit/test_worktree_venv_absent_guard.py`: build a throwaway rootdir
in `tmp_path`, invoke the real script against it with `subprocess.run`, assert on exit code
and stderr. It must **not** use the repo's own `tests/` tree as its subject, and must not
claim a test-DB slot — the sandbox rootdir keeps both true.

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

## Risks

### Risk 1: The guard fires on a legitimate all-skipped run
**Impact:** A developer running a platform-gated or otherwise entirely-skipped selection
gets a refusal instead of a green, and reads it as a broken wrapper.
**Mitigation:** The diagnostic names the escape-hatch env var on the same screen, matching
`PYTEST_STALL_LIMIT_S=0`'s precedent. The message also names the pool-exhaustion cause
first, since that is the overwhelmingly likely one. Accepting this false-positive class is
the deliberate trade: a run that executed nothing genuinely proved nothing, and the
whole point is that callers stop reading it as proof.

### Risk 2: The `-p` injection collides with a caller's own `-p` argument
**Impact:** A caller passing `-p no:something` could be reordered or shadowed, changing
plugin loading in a way that is hard to attribute.
**Mitigation:** pytest accepts repeated `-p` flags and applies them in order, so prepending
the plugin ahead of `"$@"` leaves every caller-supplied `-p` in force. Pin this with a test
that passes `-p no:cacheprovider` through the wrapper and asserts both the guard and the
caller's flag took effect.

### Risk 3: The plugin is not importable from the invoking checkout
**Impact:** pytest exits 4 (usage error) on every run — a total outage of the test wrapper
across every worktree on the machine.
**Mitigation:** `PYTHONPATH` is already pinned to `REPO_ROOT` at
`scripts/pytest-clean.sh:168`, before the run, and the plugin ships in the repo so every
checkout has it. Pin it with a test that runs the wrapper from a linked-worktree-shaped
sandbox and asserts the plugin loaded. Verify on a real worktree before merge.

### Risk 4: The guard is written but never actually bites
**Impact:** The worst outcome available — a guard against false greens that is itself a
false green, exactly the failure this issue reports.
**Mitigation:** Mutation-check the guard specifically: revert the wrapper's post-run check
to a bare `exit "$PYTEST_EXIT"`, confirm the new tests go **red**, restore it, confirm they
go green. Paste both outputs into the PR. Do not accept a green test run as evidence that
the guard works.

## Race Conditions

### Race 1: Two concurrent wrapper invocations sharing a count file
**Location:** `scripts/pytest-clean.sh`, the new temp-file mint and read.
**Trigger:** Several agents run the wrapper simultaneously — the normal state of this
machine, and the exact condition that causes the pool exhaustion in the first place.
**Data prerequisite:** Each invocation's count file must be written only by its own pytest
controller.
**State prerequisite:** No fixed path may be shared between invocations.
**Mitigation:** Mint the path with `mktemp` per invocation and pass it through the
environment, never a constant under `/tmp` or the repo. Remove it in the existing `cleanup`
trap.

### Race 2: The wrapper reads the file before the controller finished writing
**Location:** `scripts/pytest-clean.sh`, between `wait "$PYTEST_PID"` and the guard.
**Trigger:** The read racing the plugin's `sessionfinish` write.
**Data prerequisite:** The final count must be on disk before the wrapper reads it.
**State prerequisite:** pytest must have fully exited.
**Mitigation:** The read happens after `wait "$PYTEST_PID"` returns, which is after the
controller process has exited and therefore after its `sessionfinish` completed. No
polling or sleep is needed, and none should be added.

### Race 3: The stall watcher kills the controller mid-write
**Location:** `scripts/pytest-clean.sh:257-279`.
**Trigger:** A wedged run is `SIGKILL`ed while the plugin is writing.
**Data prerequisite:** A truncated or absent file must not be read as a valid verdict.
**State prerequisite:** none.
**Mitigation:** This is exactly the sentinel state — a `started` file with no verdict — and
the three-state protocol already fails closed on it. A truncated/garbage file is covered
by the unparseable-contents case in the failure-path strategy, which also fails closed.


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
