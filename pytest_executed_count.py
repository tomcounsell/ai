"""pytest plugin: tallies executed test reports for scripts/pytest-clean.sh (#3195).

Why this exists: scripts/pytest-clean.sh passes pytest's own exit code straight
through. That's correct for "some tests ran and (some) failed", but a run in
which every selected test is *skipped* also exits 0 -- indistinguishable, by
exit code alone, from "1500 tests passed". The wrapper needs to know whether
anything actually executed, and it can't get that by parsing pytest's stdout
without breaking the #2574 stall watcher (it backgrounds pytest and keeps `$!`
as the controller PID; teeing output would take pytest off a TTY and move `$!`
onto `tee`). This plugin reports the count via a small file instead.

The controller is the sole writer. Every hook returns immediately on
`hasattr(config, "workerinput")`, which is only set inside an xdist worker
process -- so under `-n N` the controller (which receives every worker's
`pytest_runtest_logreport` via xdist's own forwarding) writes exactly one
verdict, and no worker ever touches the file.

The plugin no-ops entirely when PYTEST_CLEAN_COUNT_FILE is unset, so a bare
`pytest` invocation (outside the wrapper) is untouched.

Verdict values written to the file at pytest_sessionfinish:
    "collectonly"   -- the session ran in an introspection-only mode
                       (--collect-only, --setup-plan, --setup-only,
                       --fixtures, --fixtures-per-test, or --cache-show);
                       no tests could execute by design
    "count N"       -- N is the number of reports counted as "executed"
    "started"       -- pytest_sessionstart ran but pytest_sessionfinish never
                       did (e.g. the wrapper's #2574 wedge watcher killed the
                       controller mid-run); the wrapper reads this as
                       fail-closed

DO NOT SIMPLIFY THE COUNTING RULE. Every clause below was measured against a
real pytest session (see docs/features/pytest-clean-zero-test-guard.md for the
full table) and each one is load-bearing:

    Test shape                              | setup   | call              | teardown
    -----------------------------------------|---------|-------------------|---------
    passing                                  | passed  | passed            | passed
    failing                                  | passed  | failed            | passed
    pytest.skip() in the body                | passed  | skipped           | passed
    fixture-level skip (scratch_test_db)     | skipped | (no call report)  | passed
    @pytest.mark.skip                        | skipped | (no call report)  | passed
    @pytest.mark.xfail that fails             | passed  | skipped, wasxfail | passed
    @pytest.mark.xfail that passes (xpass)    | passed  | passed, wasxfail  | passed
    fixture raising in setup                  | failed  | (no call report)  | passed

  - `when == "call"` alone is NOT enough: a body-level `pytest.skip()` produces
    a *call* report with outcome "skipped", so a rootdir of only body-skips
    would count as "executed" under that rule while pytest prints "N skipped".
  - `outcome != "skipped"` alone is NOT enough -- this was the round-1 defect.
    Setup and teardown of a skipped test both report "passed", so an
    only-skipped rootdir counts those passed setup/teardown reports and the
    guard never fires.
  - Checking for a `wasxfail` attribute on the report is required because an
    xfail reports call/skipped/wasxfail=True and an xpass reports
    call/passed/wasxfail=True. (Deliberately paraphrased rather than quoted
    as a literal expression here -- see commit 97333f8ac: the settled rule's
    own clause, spelled out verbatim in prose, would survive a mutation
    `sed` unchanged and make the Mutation-2 drift grep misreport a mutated
    copy as still carrying the clause. Do not "clean this up" to match the
    neighboring bullets' style.)
    Both genuinely executed; dropping this clause would undercount an
    xfail-heavy selection toward the fail-closed side.
  - The setup/teardown failure clause catches a fixture that raises, which
    produces a *failed setup* report and no call report at all. Without it, a
    rootdir whose every test errors in setup counts zero and gets reported as
    "nothing executed" when the truth is "everything errored" -- a
    misattributed diagnostic on an already-red run.
"""

import os

_COUNT_FILE = os.environ.get("PYTEST_CLEAN_COUNT_FILE")
_executed = 0
_in_worker = False


def _write(text):
    if not _COUNT_FILE:
        return
    try:
        with open(_COUNT_FILE, "w") as f:
            f.write(text)
    except OSError:
        # Fail open: an unwritable count-file path must not take down a test
        # run that would otherwise have succeeded. The wrapper's read side
        # treats an absent/unreadable file as a pass-through, so this
        # degrades to today's behavior rather than failing the run.
        pass


def pytest_configure(config):
    global _in_worker
    # Report objects carry no config reference, so the worker/controller
    # distinction is captured here (once, at configure time) and consulted
    # by the other hooks below.
    _in_worker = hasattr(config, "workerinput")


def pytest_sessionstart(session):
    if not _COUNT_FILE or _in_worker:
        return
    _write("started")


def pytest_runtest_logreport(report):
    global _executed
    if not _COUNT_FILE or _in_worker:
        return
    if report.when == "call" and (report.outcome != "skipped" or hasattr(report, "wasxfail")):
        _executed += 1
    elif report.when in ("setup", "teardown") and report.failed:
        _executed += 1


def pytest_sessionfinish(session, exitstatus):
    if not _COUNT_FILE or _in_worker:
        return
    # --collect-only is only one of six modes that legitimately run a
    # session while executing nothing by design. Derivation (re-run this
    # whenever pytest is bumped): the complete set is exactly the flags
    # whose pytest_cmdline_main hands off to _pytest.main.wrap_session --
    # four call sites on pytest 9.0.3 (`_pytest/main.py` -> `_main`,
    # `_pytest/fixtures.py` -> `_showfixtures_main` and
    # `_show_fixtures_per_test`, `_pytest/cacheprovider.py` -> `cacheshow`),
    # covering six flags/dests: --collect-only (collectonly), --setup-plan
    # (setupplan, which also flips setuponly), --setup-only (setuponly,
    # which also flips setupshow -- see the note below), --fixtures
    # (showfixtures), --fixtures-per-test (show_fixtures_per_test), and
    # --cache-show (cacheshow). --setup-show (setupshow) is deliberately
    # NOT in this set: it runs the call phase like a normal session, so
    # excluding it is correct, not an omission.
    #
    # Without all six attributes here the wrapper converts a healthy
    # introspection command into a false "ZERO TESTS EXECUTED" /
    # test-DB-pool-exhaustion failure -- measured directly against this
    # venv's pytest for each flag (see docs/features/pytest-clean-zero-test-
    # guard.md for the full table). All six attributes exist on
    # config.option at sessionfinish, but getattr with a default keeps this
    # safe against a future pytest that renames or drops one of them.
    # (Deliberately not spelling out each dest=False pairing as a literal
    # list here -- the wasxfail note above explains why: this comment would
    # survive a mutation that deletes the introspection_only expression, and
    # a drift grep for those names would misreport the mutant as still
    # carrying the clause.)
    option = session.config.option
    introspection_only = (
        option.collectonly
        or getattr(option, "setupplan", False)
        or getattr(option, "setuponly", False)
        or getattr(option, "showfixtures", False)
        or getattr(option, "show_fixtures_per_test", False)
        or getattr(option, "cacheshow", False)
    )
    if introspection_only:
        _write("collectonly")
    else:
        _write(f"count {_executed}")
