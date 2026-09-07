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
    "collectonly"   -- config.option.collectonly was set; no tests could run
    "count N"       -- N is the number of reports counted as "executed"

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
  - `hasattr(report, "wasxfail")` is required because an xfail reports
    call/skipped/wasxfail=True and an xpass reports call/passed/wasxfail=True.
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
    if session.config.option.collectonly:
        _write("collectonly")
    else:
        _write(f"count {_executed}")
