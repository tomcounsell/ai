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

- **Assumption**: "the three-state protocol plus the settled rule actually produces the intended
  behavior across every channel"
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
      three-state file protocol, why output parsing was rejected (#2574 stall watcher), the
      escape-hatch env var, and the measured exit-code table from spike-1 so the next
      reader does not have to re-derive which channels already fail closed.
- [ ] Add a row to the `docs/features/README.md` index table.
- [ ] Update `docs/features/test-concurrency-coordination.md` — it is the standing home for
      the pool/claim story and must now say that a pool-exhausted run fails closed at the
      wrapper instead of reading green.

### External Documentation Site
Not applicable — this repo publishes no external documentation site.

### Inline Documentation
- [ ] Header comment in the new plugin module: why it exists, why the controller is the
      sole writer, and the three file states with their meanings.
- [ ] Block comment in `scripts/pytest-clean.sh` above the guard, matching the house style
      of the #3033 and #2574 guards: the failure it prevents, the measured evidence, and
      the issue number.
- [ ] `CLAUDE.md`'s "Non-obvious behavior" bullet on `scripts/pytest-clean.sh` gains the
      zero-executed refusal alongside the existing off-pin and worktree-venv aborts.

## Success Criteria

- [ ] A fully-skipped run through the wrapper exits **non-zero** with a named diagnostic on
      stderr.
- [ ] A zero-collection run through the wrapper exits non-zero (regression pin — this is
      already true at exit 5 and must stay true).
- [ ] A run with at least one executed test exits with pytest's own status, unchanged —
      pass stays 0, failure stays non-zero.
- [ ] `--version`, `--help`, and `--collect-only` through the wrapper are unaffected.
- [ ] `tests/unit/test_worktree_venv_absent_guard.py` and
      `tests/unit/test_interpreter_pin_guard.py` pass unmodified.
- [ ] **Mutation check, per guard**: reverting the wrapper's post-run check to a bare
      `exit "$PYTEST_EXIT"` turns the new zero-executed tests red; restoring it turns them
      green. Both outputs pasted in the PR.
- [ ] The guard's escape-hatch env var disables it, verified by a test.
- [ ] Verified on a real linked worktree, not only in a `tmp_path` sandbox.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (wrapper + plugin)**
  - Name: `wrapper-builder`
  - Role: The plugin module and the wrapper's guard — the entire behavior change.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: `guard-test-builder`
  - Role: `tests/unit/test_pytest_clean_zero_tests.py` and the mutation-check evidence.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `guard-validator`
  - Role: Verifies the guard bites, on a real worktree as well as in a sandbox.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `guard-documentarian`
  - Role: The Documentation section's tasks.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Executed-count reporter plugin
- **Task ID**: build-plugin
- **Depends On**: none
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create)
- **Informed By**: spike-2 (the three-state file protocol), spike-3 (controller is the sole writer)
- **Assigned To**: wrapper-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a repo-local pytest plugin module that reads its output path from an env var and does nothing when that var is unset.
- `pytest_sessionstart`: write the `started` sentinel. Return early in workers (`hasattr(config, "workerinput")`).
- `pytest_runtest_logreport`: count reports representing an executed outcome. Passed, failed, xfailed, xpassed and setup/teardown errors count; skipped and deselected do not.
- `pytest_sessionfinish`: write `collectonly` when `config.option.collectonly` is set, otherwise the final count. Controller only.
- Wrap the writes in a narrow `OSError` handler so an unwritable path degrades to today's behavior instead of failing the run.
- Header comment per the Inline Documentation task.

### 2. Wrapper guard
- **Task ID**: build-wrapper
- **Depends On**: build-plugin
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create), tests/unit/test_worktree_venv_absent_guard.py, tests/unit/test_interpreter_pin_guard.py
- **Informed By**: spike-1 (only the all-skipped channel returns 0), spike-2 (no output capture)
- **Assigned To**: wrapper-builder
- **Agent Type**: builder
- **Parallel**: false
- Mint the count-file path with `mktemp`, export it, and remove it in the existing `cleanup` trap.
- Prepend `-p <plugin>` ahead of `"$@"` so caller-supplied `-p` flags still apply.
- After `wait "$PYTEST_PID"` and the existing reap, read the file and apply the three-state protocol: absent → pass through; `collectonly` → pass through; positive count → pass through; `count 0` → fail closed; sentinel-only, empty, or unparseable → fail closed.
- Emit a stderr diagnostic distinguishable from the three existing refusals, naming the pool-exhaustion cause, `scripts/reap-xdist.sh --apply`, and the escape-hatch env var.
- Honor the escape-hatch env var, defaulting to on, in the style of `PYTEST_STALL_LIMIT_S=0`.
- Preserve the existing exit code on every pass-through path. Do not reorder or alter the preflight guards, the stall watcher, or the reaping.

### 3. Guard tests
- **Task ID**: build-tests
- **Depends On**: build-wrapper
- **Validates**: tests/unit/test_pytest_clean_zero_tests.py (create)
- **Informed By**: spike-1 (the measured exit-code table is the oracle)
- **Assigned To**: guard-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Build sandbox rootdirs under `tmp_path` (a `pyproject.toml` with `[tool.pytest.ini_options]`, a `tests/` dir, a `.venv/bin/pytest`), following `tests/unit/test_worktree_venv_absent_guard.py`. Never point the wrapper at the repo's own `tests/` tree, and never claim a test-DB slot.
- Cases: all-skipped → non-zero with the diagnostic; zero-collected → non-zero; at least one passing test → 0; a failing test → pytest's own non-zero; `--collect-only` → 0; `--version` → 0; escape hatch set → all-skipped returns 0; a caller's own `-p no:cacheprovider` still applies.
- Failure-path cases from the Failure Path Test Strategy: unwritable count-file path, empty file, garbage file.
- Pin the diagnostic's text, not only the exit code.

### 4. Validate the guard actually bites
- **Task ID**: validate-guard
- **Depends On**: build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Mutation check: revert the wrapper's post-run check to a bare `exit "$PYTEST_EXIT"`, run the new tests, confirm **red**. Restore, confirm **green**. Capture both outputs verbatim for the PR.
- Re-run `tests/unit/test_worktree_venv_absent_guard.py` and `tests/unit/test_interpreter_pin_guard.py` unmodified and confirm they pass.
- Run `tests/unit/test_feature_map_markers.py` and confirm the new test file's marker resolution needs no `FEATURE_MAP` or `KNOWN_MISTAGS` entry.
- Provision a real linked worktree with its own `.venv` and confirm the plugin loads and the guard fires there — the `tmp_path` sandbox does not prove `PYTHONPATH` resolution in a real worktree (Risk 3).
- Run a normal targeted suite through the wrapper and confirm the exit code and terminal output are unchanged from today.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: guard-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every task in the Documentation section.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table.
- Confirm every Success Criteria checkbox, including the pasted mutation-check evidence.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard tests pass | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q` | exit code 0 |
| Wrapper refuses an all-skipped run | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q -k all_skipped` | exit code 0 |
| Wrapper still passes a run that executed tests | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q -k executed` | exit code 0 |
| Escape hatch disables the guard | `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q -k escape_hatch` | exit code 0 |
| Sibling wrapper guards still pass | `scripts/pytest-clean.sh tests/unit/test_worktree_venv_absent_guard.py tests/unit/test_interpreter_pin_guard.py -q` | exit code 0 |
| Marker guard still passes | `scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q` | exit code 0 |
| Wrapper unaffected by --version | `scripts/pytest-clean.sh --version` | exit code 0 |
| Guard is wired into the wrapper | `grep -c PYTEST_EXECUTED scripts/pytest-clean.sh` | output > 2 |
| Feature doc exists | `test -f docs/features/pytest-clean-zero-test-guard.md` | exit code 0 |
| Feature doc is indexed | `grep -c pytest-clean-zero-test-guard docs/features/README.md` | output > 0 |
| Output parsing was not introduced (anti-criterion, spike-2) | `grep -cE "tee " scripts/pytest-clean.sh` | match count == 0 |
| Count file is minted per run, not a fixed path (anti-criterion, Race 1) | `grep -c "COUNT_FILE=/tmp/" scripts/pytest-clean.sh` | match count == 0 |
| conftest's scratch_test_db skip untouched (anti-criterion, No-Gos) | `git diff --quiet origin/main -- tests/conftest.py` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

Round 1 — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), **sequential lenses** (the Agent tool is absent in this stage-runner context, so no finding below was independently corroborated), at plan hash `sha256:52bc6ad4…`. Verdict: **NEEDS REVISION** (2 blockers, 5 concerns, 2 nits).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Task 1's counting rule makes the guard unable to fire. `pytest_runtest_logreport` fires three times per test (setup, call, teardown) and on a SKIPPED test the non-call phases report `outcome == "passed"`. Measured with a probe plugin implementing exactly the plan's rule: a rootdir of only-skipped tests produced `NAIVE_COUNT=3` while pytest printed `2 skipped`. A body-level `pytest.skip()` gives `setup=passed, call=skipped, teardown=passed`; the fixture-level skip `tests/conftest.py:722-740` actually performs gives `setup=skipped, teardown=passed`. Either way the count is positive and the false green survives — Risk 4 realized in the spec. | pending | Count a report only when `report.when == "call"`, or `report.when in ("setup","teardown") and report.failed` for collection/teardown errors. Never branch on `report.outcome != "skipped"` or `report.passed` alone. spike-2 measured `count 0`, so the spike code already had this filter and the plan text lost it. Pin it with a test whose sandbox skips at FIXTURE setup (the `scratch_test_db` shape), not only one calling `pytest.skip()` in the body — the two produce different phase/outcome tuples and a body-skip-only test passes against an implementation that still misses the real trigger. |
| BLOCKER | History & Consistency | The named structural model cannot exercise the guard, and the obvious repair reproduces #3033 inside the test written to prevent a #3033-shaped bug. `tests/unit/test_worktree_venv_absent_guard.py:52-54` provisions a FAKE `.venv/bin/pytest` (a shell script echoing `pytest 0.0 (fake)`) driven with `--version`; a sandbox on that model runs no session, so the plugin never loads and every new case lands on "file absent -> pass through" — the all-skipped test asserts non-zero and gets 0. Symlinking the repo `.venv` into the sandbox is worse: measured from a `tmp_path` rootdir with `PYTHONPATH` unset, `import tests` and `import tools` both resolve to `/Users/tomcounsell/src/ai/...` via `_editable_impl_valor_bridge.pth`, which hard-codes the PRIMARY checkout. A lane worktree would exercise main's plugin and a builder mutating the plugin would see no change. | pending | Decide the harness in the plan. Either write a COPY of the plugin module into the sandbox rootdir with a real isolated interpreter, or drive a real linked worktree with its own `uv sync --extra dev` venv (task 4's worktree provisioning becomes load-bearing for the primary assertion, not a supplement). Either way add a negative control that runs BEFORE any guard assertion: `subprocess.run([sandbox_python, "-c", "import <plugin>; print(<plugin>.__file__)"])` and assert the path is under `tmp_path`. Build every sandbox subprocess env from a COPY of `os.environ` with `PYTHONPATH` and the count-file var REMOVED — the outer wrapper exports `PYTHONPATH="$REPO_ROOT:..."` at `scripts/pytest-clean.sh:168` and it is inherited, so an ambient-env test passes for the wrong reason. |
| CONCERN | Risk & Robustness | Race Conditions covers concurrent SIBLING invocations but not NESTED ones, which the test design guarantees: `tests/unit/test_pytest_clean_zero_tests.py` runs under `scripts/pytest-clean.sh` and drives `scripts/pytest-clean.sh` by `subprocess.run`, so the inner wrapper inherits the outer run's exported count-file path. If the wrapper reads the var before minting (`"${VAR:-$(mktemp)}"`, the natural bash shape and the one `PYTEST_STALL_LIMIT_S` uses), every inner run overwrites the outer run's verdict file and the outer guard reads a sandbox's count. A new false-green/false-red channel introduced by the fix itself. | pending | Mandate an UNCONDITIONAL mint in the plan text — the wrapper never honors an inherited count-file path: `COUNT_FILE="$(mktemp -t pytest-clean-count)"; export <VAR>="$COUNT_FILE"` with no `:-` default — and have the plugin no-op when the var is unset (`if not os.environ.get(VAR): return`) so a bare `pytest` writes nothing. This differs deliberately from `PYTEST_STALL_LIMIT_S`, a caller-tunable knob; the count file is private wrapper state and must never be caller-supplied. Pin it with a test asserting a nested wrapper invocation leaves the outer file untouched. |
| CONCERN | Risk & Robustness | Risk 3's mitigation is "verify on a real worktree before merge" — an after-the-fact check for a failure whose blast radius is every concurrent agent on the machine. An unimportable `-p` module aborts the wrapper before any test runs, on every invocation, in every checkout. Measured: it exits **1** with `ImportError: Error importing plugin`, not the exit 4 the plan states, so a caller reading exit codes cannot distinguish it from the three existing refusals. Six lanes share this machine; a bad injection on `main` takes all of them down at once and the plan carries no in-wrapper containment. | pending | Gate the injection on the plugin file's presence in the invoking checkout: `if [ -f "$REPO_ROOT/<plugin path>.py" ]; then set -- -p <module> "$@"; fi`, placed after `export PYTHONPATH` (`scripts/pytest-clean.sh:168`) and before `"$PYTEST_BIN" "$@" &` (line 287). `set --` is the only rewrite preserving `"$@"` quoting; do not build a string. The existence test must use `$REPO_ROOT`, never `$SCRIPT_ROOT` — pointing it at `$SCRIPT_ROOT` lets the primary checkout's plugin serve a worktree run, the exact #3033 bleed the wrapper prevents. Correct Risk 3's "pytest exits 4" to exit 1. |
| CONCERN | Scope & Value | The escape hatch is the one element that can restore the exact defect this plan removes, and it is specified as a silent off switch defaulting to on. `PYTEST_STALL_LIMIT_S=0` is not the right precedent: disabling the wedge detector costs a hang, which is loud; disabling this guard costs a confident exit 0 on a run that executed nothing, indistinguishable in a transcript from a real pass. An agent hitting the refusal mid-mutation-check has every incentive to set the var. The plan cites no measured legitimate all-skip run in this repo, so the hatch is added for a hypothetical while carrying the original bug's full weight. | pending | The hatch must suppress the EXIT, never the MESSAGE. Two branches: `if [ "$count" = "0" ]; then if [ -n "${<HATCH_VAR>:-}" ]; then echo "pytest-clean: 0 tests executed; guard disabled by <HATCH_VAR> — this run proves nothing" >&2; else <full diagnostic>; exit 1; fi; fi`. Assert the message text in the escape-hatch test; asserting only that exit 0 came back is the assertion this whole issue shows is worthless. Settle Open Question 3 in the plan — the builder needs a decided default, not a question. |
| CONCERN | History & Consistency | The Verification table pins an identifier the plan never chooses. `grep -c PYTEST_EXECUTED scripts/pytest-clean.sh` is the only place `PYTEST_EXECUTED` appears in the document, while Open Question 1 explicitly leaves the env-var name open and Open Question 2 leaves the plugin's module path open. A builder who answers those questions with any other name fails a row that reads as a wiring check; one who satisfies the row has silently answered Open Question 1 by grep. The same ambiguity leaves `-p <plugin>` unresolvable in every task and risk that depends on the module path. | pending | Settle both env-var names and the plugin module path as plan text, then rewrite the row to grep the settled name. Open questions with "a stated default the builder can carry" must state the default as text, not encode it in a grep. Add a second leg, since counting an env-var string proves only that a variable is mentioned: `grep -q 'set -- -p ' scripts/pytest-clean.sh`. Keep the anti-criterion `grep -cE "tee " scripts/pytest-clean.sh` — verified 0 on `main` today, so it is a real pin rather than a vacuous one. |
| CONCERN | History & Consistency | Every Verification row reads exit code alone, the signal this plan establishes is untrustworthy from this wrapper — and the table is executed BY the wrapper under change, so it cannot detect its own bootstrap failure. Two rows are worse than uninformative: "Wrapper refuses an all-skipped run" and "Escape hatch disables the guard" both expect `exit code 0`, so a run in which the whole file is skipped (the pool-exhaustion condition motivating this issue, live on this machine past ~5 concurrent agents) satisfies both rows having asserted nothing. Success Criteria carry the mutation check as the answer to Risk 4, but the machine-readable Verification table has no row for it. | pending | Express the guard rows as passed-count assertions the runner can check: `scripts/pytest-clean.sh tests/unit/test_pytest_clean_zero_tests.py -q 2>&1 \| tail -2` with expected `matches "[1-9][0-9]* passed"`, and state the minimum count per row. Retain an exit-code-only row solely for `--version`, where the pass-through path is the assertion. Add a Verification row for the mutation check so the evidence Risk 4 depends on is machine-checked rather than only pasted into the PR. |
| NIT | Scope & Value | Two of the three protocol states are specified for conditions the plan's own spikes show nothing produces. The plan concedes the sentinel branch is unreachable today ("Today that can only arrive alongside a non-zero pytest exit, so the branch is defensive"), and the empty/whitespace/garbage-file cases have no producer now that the count file is minted per run by `mktemp` with a single writer. Three test cases and a wrapper branch are written against states that cannot occur. | pending | — |
| NIT | Scope & Value | Four named agents with distinct roles are declared for an `appetite: Small` change whose six tasks are a strictly serial chain — every task carries `Parallel: false` and depends on the one before it. The roster buys no concurrency and adds four handoffs to one bash hunk, one small module, and one test file. | pending | — |

---

## Open Questions

None of these block the build — each has a stated default the builder can carry, and
each is a good target for the critique round.

1. **Escape-hatch naming.** The plan assumes an env var in the `PYTEST_*` family matching
   `PYTEST_STALL_LIMIT_S`'s precedent. Any objection to that family, or a preferred name?
2. **Where the plugin module lives.** `tests/` keeps it beside the conftest it is
   reasoning about, but the wrapper is also run from directories whose `tests/` tree is not
   this repo's. A repo-root module is more clearly the wrapper's own. Preference?
3. **Should a fully-skipped run be non-zero for humans too, or only under agent
   invocation?** The plan says always, with an escape hatch, on the grounds that a
   run proving nothing should never read as proof. Worth confirming that a human hitting
   a platform-gated all-skip selection getting a refusal is acceptable.

