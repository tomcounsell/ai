# pytest-clean Zero-Executed Guard

**Issue:** [#3195](https://github.com/tomcounsell/ai/issues/3195) · **Plan:** `docs/plans/pytest-clean-zero-tests-fail-closed.md`

`scripts/pytest-clean.sh` exits non-zero when a run pytest itself called green
executed no tests at all. Every mutation check in this repo's SDLC reads that
exit code as a verdict, so a green that proves nothing is the one answer the
wrapper must never give.

## The hole this closes

A run in which every selected test is *skipped* executes nothing and exits **0**.
Before this guard the wrapper passed that straight through, so "0 executed, all
skipped" and "1500 executed, all passed" were the same signal to every caller:
`/do-build` recorded a broken guard as bitten, and `/do-pr-review` read a green
that meant nothing.

The trigger is routine on this machine. `tests/conftest.py`'s `scratch_test_db`
fixture calls `pytest.skip()` when the 15-slot machine-global test-DB pool is
exhausted, which happens past roughly five concurrent agents. Any mutation check
whose selected tests all depend on that fixture reported a confident green having
run nothing. It was observed live on the #3170 lane on 2026-09-06: two false-GREEN
mutation checks during BUILD, reproduced by the round-1 reviewer.

## Which channels already failed closed

Measured directly against pytest-xdist 3.8.0. Only one of the four zero-execution
channels ever returned 0, which is why the guard is narrow:

| Channel | How it was produced | Exit |
|---|---|---|
| Zero collected | empty test file | 5 |
| All deselected | `-k nomatch_xyz` | 5 |
| All workers crash in `pytest_configure` | `pytest.exit(..., returncode=3)` in every worker, `-n 2` | 5 |
| **All skipped** | `pytest.skip()` in every test, `-n 0` and `-n 2` | **0** |
| Mixed pass + skip | one of each, `-n 2` | 0 (correct) |

## The guard only ever converts a green into a red

The verdict block runs only when pytest itself exited **0**
(`[ "$PYTEST_EXIT" -eq 0 ]`). That gate is load-bearing, not an optimization.
A `count 0` verdict is produced by three channels with three different meanings:

| Channel | pytest exit | Count file | Ungated | Gated on `PYTEST_EXIT -eq 0` |
|---|---|---|---|---|
| all fixture-skip (the production trigger) | **0** | `count 0` | fires → 1 | **fires → 1**, the target |
| collection error (`1 error during collection`) | **2** | `count 0` | rewrites 2 → 1 and prints a pool-exhaustion headline over a syntax error | **passes through → 2** |
| zero collected (`no tests ran`) | **5** | `count 0` | rewrites 5 → 1 | **passes through → 5** |
| wedge (`watch_for_stall` kills the controller) | non-zero | `started` or truncated | prints a second headline contradicting the `WEDGED` banner | **passes through**, one headline |

Do not "fix" the gate away. Without it an already-red run gets a second,
misattributed headline, and the zero-collection regression pin at exit 5 is lost.

## The pass-through allowlist

The wrapper's predicate is expressed as an allowlist, not an enumeration of bad
states:

```bash
verdict_passes_through() {
    case "$1" in
        ""|collectonly)  return 0 ;;   # no session ran, or a collect-only run
        "count "[1-9]*)  return 0 ;;   # at least one test executed
        *)               return 1 ;;   # count 0, started, truncated, garbage
    esac
}
```

Inverting the test this way is what makes the guard total. A state nobody
anticipated (`count 0`, a surviving `started` sentinel, an empty file, a
truncated write, unparseable bytes) lands on the safe side by construction
rather than by having been enumerated. Verified under `/bin/bash` 3.2.57 against
`""`, `collectonly`, `count 0`, `count 1`, `count 10`, `started`, `coun`,
whitespace and `count -1`: only the first two and the positive counts pass.

Two shapes here are deliberate and both are pinned by tests:

- **A named function, not an inline `case`.** The test slices the real body out
  of the script under test with `sed -n '/^verdict_passes_through()/,/^}/p'` and
  sources it, so the check drives the wrapper's own predicate. A test that
  retypes the patterns passes unchanged with the wrapper's `case` deleted.
- **The multi-line form with a bare `}` at column 0.** The script has no line
  starting with `}` after the guard, so a one-line `esac; }` definition makes the
  slice run to EOF, swallow `exit "$PYTEST_EXIT"`, and die under `set -u` on
  `PYTEST_EXIT: unbound variable` before the test's own refusals can fire. The
  test refuses with `SLICE_OVERRUN` if that ever happens.

## Why a plugin, not output parsing

The obvious fix, teeing pytest's stdout and reading the summary line, was
rejected by measurement. The wrapper deliberately backgrounds pytest and keeps
`$!` as the controller PID so the #2574 stall watcher can sample its CPU time.
Teeing would take pytest off a TTY (losing progress and colour) and move `$!`
onto `tee`, disabling the wedge detector.

Instead `pytest_executed_count.py` at the repo root is injected with `-p` and
writes a verdict to a file the wrapper names via `PYTEST_CLEAN_COUNT_FILE`:

- The **controller is the sole writer**. Every hook returns early on
  `hasattr(config, "workerinput")`. xdist forwards each worker's
  `pytest_runtest_logreport` to the controller, so the controller's tally is
  already the aggregate. One file, one writer, no merging or locking.
- The file is **minted unconditionally** with `mktemp -t pytest-clean-count` and
  never inherited (no `:-` default). The guard's own tests run the wrapper under
  the wrapper, so a nested invocation honoring an inherited path would overwrite
  its parent's verdict.
- The plugin **no-ops entirely** when `PYTEST_CLEAN_COUNT_FILE` is unset, so a
  bare `pytest` is untouched.
- Injection is **gated on the module existing in the invoking checkout**:
  `[ -f "$REPO_ROOT/pytest_executed_count.py" ]`, using `$REPO_ROOT` and never
  `$SCRIPT_ROOT`. An unimportable `-p` module aborts pytest before a single test
  runs (measured: exit 1, `ImportError: Error importing plugin`), and six lanes
  share this machine. Using `$SCRIPT_ROOT` would let the primary checkout's
  plugin serve a worktree run, the exact #3033 bleed the wrapper exists to
  prevent. A checkout predating the plugin runs exactly as it did before.

## The counting rule

```python
if report.when == "call" and (report.outcome != "skipped" or hasattr(report, "wasxfail")):
    executed += 1
elif report.when in ("setup", "teardown") and report.failed:
    executed += 1
```

**Do not simplify this.** Round 1 of the plan tried, in two different directions,
and both attempts reinstated the bug. Every clause has a measured report tuple
behind it:

| Test shape | setup | call | teardown |
|---|---|---|---|
| passing | passed | passed | passed |
| failing | passed | failed | passed |
| `pytest.skip()` in the body | passed | skipped | passed |
| fixture-level skip (`scratch_test_db`) | skipped | (no call report) | passed |
| `@pytest.mark.skip` | skipped | (no call report) | passed |
| `@pytest.mark.xfail` that fails | passed | skipped, `wasxfail` | passed |
| `@pytest.mark.xfail` that passes (xpass) | passed | passed, `wasxfail` | passed |
| fixture raising in setup | failed | (no call report) | passed |

- `when == "call"` alone is not enough: a body-level `pytest.skip()` produces a
  *call* report with outcome `skipped`, so a rootdir of body-skips counted 2
  while pytest printed `2 skipped`.
- `outcome != "skipped"` alone is not enough, and this was the round-1 defect.
  Setup and teardown of a skipped test both report `passed`, so an only-skipped
  rootdir counted 2, 4, or 2 depending on the skip shape and the guard never
  fired.
- The `wasxfail` attribute check is required because an xfail reports
  `call/skipped/wasxfail=True` and an xpass reports `call/passed/wasxfail=True`.
  Both genuinely executed.
- The setup/teardown failure clause catches a fixture that raises, which produces
  a *failed setup* report and no call report at all. Without it, a rootdir whose
  every test errors in setup counts zero and gets a "nothing executed"
  diagnostic when the truth is "everything errored".

Against a mixed rootdir (1 pass, 1 fail, 1 xfail, 1 xpass, 1 setup error, 3
skips) the rule counts **5**, matching pytest's own summary, identically at
`-n 0` and `-n 2`.

## `PYTEST_ALLOW_ZERO_TESTS` suppresses the exit, never the message

A legitimately all-skipped selection (platform-gated tests, say) can set
`PYTEST_ALLOW_ZERO_TESTS` to keep the exit at 0. The diagnostic prints either
way:

```
pytest-clean: ZERO TESTS EXECUTED (#3195) — this run proves nothing.
```

Only the `exit 1` is conditional. A silent green on a run that executed nothing
is indistinguishable in a transcript from a real pass, which is the whole defect,
so making the hatch quiet would restore it under a different name. Whoever reads
a mutation-check transcript later cannot mistake a hatched run for a pass.

The diagnostic goes to stderr, names the likely cause (test-DB pool exhaustion at
fixture setup), the remedy (`scripts/reap-xdist.sh --apply`), and the hatch. It
is distinguishable from the wrapper's three other refusals (`no usable .venv`,
off-pin interpreter, `WEDGED`), and the tests pin that.

## Mutation seams

`# BEGIN zero-executed guard (#3195)` / `# END zero-executed guard (#3195)`
bracket the verdict block so a mutation check is a deterministic `sed` range
delete against a **copy**, never a hand edit of the shared checkout. The copy
must live at `<tmpdir>/scripts/pytest-clean.sh` with `check-interpreter-pin.sh`
beside it: `SCRIPT_ROOT` is derived from `${BASH_SOURCE[0]}/..`, so a flat copy
aborts before pytest starts and reddens everything for the wrong reason.

Two env seams let the tests bind to a mutant without touching the checkout:

| Variable | Mutates | Default |
|---|---|---|
| `PYTEST_CLEAN_SCRIPT` | the wrapper's verdict block | `REPO_ROOT/scripts/pytest-clean.sh` |
| `PYTEST_EXECUTED_COUNT_SOURCE` | the plugin's counting rule | `REPO_ROOT/pytest_executed_count.py` |

Only `tests/unit/test_pytest_clean_zero_tests.py` honors them, so it is the only
selection that can serve as a mutation-check control leg. The sibling guard files
(`test_worktree_venv_absent_guard.py`, `test_interpreter_pin_guard.py`) hard-code
the script path and pass even against a copy that does nothing but `exit 97`.

## Files

| Path | Role |
|---|---|
| `pytest_executed_count.py` | The reporter plugin. Repo root, not under `tools/` — `tools/__init__.py` arms the Redis flush guard on import, which would then run on every pytest invocation on the machine. |
| `scripts/pytest-clean.sh` | Mints the count file, injects `-p`, reads the verdict, decides. |
| `tests/unit/test_pytest_clean_zero_tests.py` | Sandbox-rootdir harness driving real pytest sessions through the wrapper. |

## See Also

| Resource | Purpose |
|----------|---------|
| [Test concurrency coordination](test-concurrency-coordination.md) | The test-DB pool whose exhaustion triggers the skip channel |
| [Per-worktree venv isolation](worktree-venv-isolation.md) | The wrapper's other fail-closed guards |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |
