#!/usr/bin/env python3
"""Nightly regression test runner.

Runs the default pytest collection (``COLLECTION_PATHS`` — the same set a bare
``scripts/pytest-clean.sh`` collects) with a JSON report, compares against a
prior run, and sends a Telegram alert only when new failures appear. Clean
runs are silent.

Widened collection and run-integrity guard (issue #2823)
----------------------------------------------------------
The detector used to collect only ``tests/unit/``, so a test living in
``tests/integration/`` (or ``tests/e2e/``, ``tests/tools/``,
``tests/performance/``) was invisible to it by construction — the class of bug
this widening exists to close. ``COLLECTION_PATHS`` now matches
``pyproject.toml``'s ``testpaths``, so "the default collection is red" and "the
detector is red" mean the same thing.

Widening alone is not enough: a run that could not actually execute (all
test-DB slots held by concurrent lanes, a wedged xdist controller, an
unreadable vault) must never be mistaken for a clean night.
:func:`validate_run_integrity` is the guard — it trips on a missing/corrupt
report, a signal-death or usage-error exit code, a fixture-error storm, or
(the case that matters most) a **coverage floor**: partial test-DB starvation
produces zero ``error`` outcomes and a legal exit code, so only a floor on
``total`` catches it. A tripped guard alerts loudly, writes no state, and
dispatches nothing.

Serial re-confirmation gate (issue #2180)
-----------------------------------------
`-n auto`-shaped parallel execution. The classic xdist failure mode — tests
that pass serially but collide under parallel workers on shared state (Redis
keys, temp files, fixture ordering) — produces a *shifting set* of failures a
count-based detector cannot distinguish from a real regression.

To disambiguate, after the parallel run we re-run **only the failing node IDs**
serially (`-n0`). Tests that fail in parallel but pass serially are classified as
xdist-parallelism *artifacts*; tests that fail in both are *confirmed*
regressions. The state file persists the confirmed failing **set** (not a scalar
count), so a regression alert fires only for *newly-confirmed* serial failures.
Artifacts are logged but never alerted, killing the parallel-execution alert
noise. The serial re-run targets only the already-failing node IDs, so it stays
fast and never re-runs the whole suite.

The serial pass also reports whether it can be *trusted*: a report that does
not cover every input node (a starved serial worker, a wedge) must not be read
as "these all passed" — that is the false green :func:`reconfirm_serial`'s
``serial_trusted`` return value exists to prevent.

Triage dispatch is deduped per node (issue #2559)
-------------------------------------------------
The Telegram alert and the triage dispatch answer different questions. The alert
asks "is this a regression since last night" (``compute_new_failures``); the
dispatch asks "does this node already have an issue against it"
(``compute_dispatch_set``, diffing against the persisted ``dispatched_nodes``
set). Conflating them re-triaged the entire standing failure set whenever any
one failure was new, which is how #2429, #2430 and #2462 each filed an issue
over the same dead watchdog node. A node stays suppressed while it keeps
failing and drops out of the set once it passes, so a genuine re-regression is
dispatchable again and a renamed node retires itself.

Collection-aware baseline (issue #2823)
----------------------------------------
The persisted state records which ``collection`` produced it. When the
recorded collection differs from ``COLLECTION_PATHS`` (the widening night, or
any future change of scope), the run takes the existing first-run baseline
path: it seeds ``dispatched_nodes`` with the whole currently-failing
population and dispatches **one** umbrella triage session, rather than filing
every one of those nodes individually — reopening the #2429/#2430/#2462 churn
the dedup set exists to prevent.

A post-run TTFT gate (issue #1227) reports cold-start latency regressions as
Telegram alerts without changing the exit code.

Usage:
    python scripts/nightly_regression_tests.py             # Run tests, send Telegram on regression
    python scripts/nightly_regression_tests.py --dry-run   # Preview without sending Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from agent.llm.wrapper import run_typed
from config.models import MODEL_FAST

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LOCK_FILE = DATA_DIR / "nightly_tests.lock"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Eng: Valor"
TELEGRAM_BIN = PROJECT_DIR / ".venv" / "bin" / "valor-telegram"
PYTEST_CLEAN_SH = PROJECT_DIR / "scripts" / "pytest-clean.sh"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
PYTEST_SERIAL_JSON_TMP = "/tmp/nightly_pytest_serial_report.json"

# The default collection — matches what a bare `scripts/pytest-clean.sh`
# collects (pyproject.toml's `testpaths`). "The default collection is red" and
# "the detector is red" mean the same thing now (issue #2823). No CLI flag:
# the plist is the only production caller and stays flag-free.
COLLECTION_PATHS = ["tests/"]

# Provisional/tunable. 6 is derived from this machine: 15 test-DB slots
# (tests/db_claim.py's TEST_DB_POOL_MAX) with nine left for sibling lanes.
# `-n 4` is the practical floor below which the scaled wall-clock estimate
# (~1260s unit-tier baseline x ~1.1 collection growth, inversely by worker
# count) no longer fits inside PYTEST_TIMEOUT_SECONDS. `-n auto` on a 10-core
# box demands ~10-14 of 15 slots by itself, which is what turns an unattended
# 03:00 run into a nightly "the run did not happen" alert.
NIGHTLY_XDIST_WORKERS = os.environ.get("NIGHTLY_XDIST_WORKERS", "6")

# The pre-baseline coverage floor (see validate_run_integrity). This is a
# source literal — a run cannot rewrite it — so it can only be seeded by an
# edit from a completed widening-night probe's summary.total; from night two
# onward the persisted state's own `min_expected_collected` field supplies the
# floor instead.
#
# Measurement (2026-08-20, `--dry-run` on Tom's MacBook Air, the first widened
# run to complete end to end): summary.total = 15248 collected, 15193 passed,
# 35 failed, 0 errors, in 1472s. Re-measured after rebasing onto main
# `1e398e46d`: 15296, the value used here. The delta is ordinary suite growth
# (main moved, and this branch adds tests), not measurement noise —
# `--collect-only` and a full run agreed exactly at each point.
#
# This is a FLOOR, not a running total, so it does not need updating as tests
# accumulate: growth only makes it more conservative, and a stale-low value can
# never cause a false trip. It matters on night one alone. From night two the
# persisted state's own `total` (or `min_expected_collected`) supersedes it, so
# the constant's job is to protect the single run that has no baseline to diff
# against. validate_run_integrity() trips below 0.9x this, i.e. 13766.
#
# Grain of salt: 13766 sits just *below* what `tests/unit/` alone collects, so
# a silent revert of COLLECTION_PATHS to the unit tier would clear this floor.
# The `collection` field comparison is what catches that case; the floor is
# aimed at truncation within a given collection, not at scope changes.
MIN_EXPECTED_COLLECTED = int(os.environ.get("NIGHTLY_MIN_EXPECTED_COLLECTED", "15296"))

# Measurement (2026-08-20, first widened run to complete end to end on this
# machine, 15 of 15 test-DB slots free): the parallel `-n 6` pass took 1411s
# and the whole run, including a 60s serial re-confirmation of 35 nodes, took
# 1472s (24.5 min). The 5400s ceiling is therefore ~3.7x the observed time.
#
# The estimate this replaces was accurate: it assumed ~1.1x collection growth
# over the unit tier, and the measured item counts are 15248 vs 13788, a
# ratio of 1.106. Item counts, not file counts, are what the estimate turned
# on — 624 unit files growing to 760 looks like 1.22x and is the wrong figure.
#
# Headroom is deliberately kept: 1472s was measured with the machine to
# itself. The production 03:00 run competes with sibling lanes for the same
# 15 slots, and the claim wait alone is budgeted at 300s per process.
PYTEST_TIMEOUT_SECONDS = 5400

# Serial re-confirmation only re-runs the already-failing node IDs, bounded
# further by MAX_RECONFIRM_NODES, so it is far cheaper than the full parallel
# run even at the widened collection's scale. Bound, not measurement, on the
# same rule as PYTEST_TIMEOUT_SECONDS above.
PYTEST_RECONFIRM_TIMEOUT_SECONDS = 1800  # 30 minutes max

# If a serial re-confirmation would have to re-run more than this many nodes,
# skip the serial pass entirely rather than spawn a subprocess that will
# almost certainly exhaust its own timeout. Returns every input node as
# confirmed (never an empty set) with serial_trusted=True — the same
# practical result the timeout fail-safe already produces, reached
# immediately instead of after a multi-minute wait.
MAX_RECONFIRM_NODES = 200

# Cap on how many not-yet-triaged nodes a single run will hand to
# maybe_dispatch_triage_session in one shot. Truncated nodes are logged and
# retried on a later run rather than lost — only the dispatched slice is
# recorded in dispatched_nodes.
MAX_DISPATCH_NODES = 10

# TTFT regression gate (issue #1227).
# Plan target: production 90s, nightly CI 120s (allowing slack for run-to-run noise).
TTFT_LOG_FILE = PROJECT_DIR / "logs" / "cold_start_metrics.jsonl"
TTFT_SESSION_TYPE = "pm"
TTFT_LAST_N = 10
TTFT_THRESHOLD_SECONDS = 120.0

# .env is a symlink to the iCloud vault (~/Desktop/Valor/.env). Under launchd the
# job now runs directly as the venv python, which holds the macOS Desktop-folder
# TCC grant; /bin/bash does NOT, which is why the plist no longer routes the load
# through a `source` step (issue #2327). We load the vault into os.environ here so
# the pytest / valor-telegram subprocesses this script spawns inherit API keys,
# feature flags, and DB settings — exactly what `set -a; source .env` used to do.
ENV_FILE = PROJECT_DIR / ".env"
# Provisional/tunable floor. A healthy vault load is ~115 keys; a load at or below
# this almost certainly means the file was unreadable or empty — the silent EPERM
# this fix exists to surface. Grain of salt: adjust via NIGHTLY_MIN_ENV_KEYS if the
# vault is ever intentionally slimmed.
MIN_ENV_KEYS = int(os.environ.get("NIGHTLY_MIN_ENV_KEYS", "10"))


def log(msg: str) -> None:
    """Write timestamp-prefixed message to stdout and LOG_FILE."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[nightly-tests] {timestamp} {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never crash on logging failure


def load_last_run(run_file: Path | None = None) -> dict:
    """Load previous run state. Returns empty dict on missing/corrupt file (signals first run)."""
    target = run_file if run_file is not None else LAST_RUN_FILE
    if target.exists():
        try:
            return json.loads(target.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}  # Empty dict = first run


def save_last_run(state: dict, run_file: Path | None = None) -> None:
    """Atomically persist current run state to the given state file."""
    target = run_file if run_file is not None else LAST_RUN_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2) + "\n")


def _acquire_run_lock(lock_file: Path | None = None):
    """Acquire an exclusive, non-blocking ``fcntl.flock`` on the run lock file.

    Sidecar-lock-file idiom: open/create the lock file, then
    ``flock(LOCK_EX | LOCK_NB)``.

    Returns the open file handle on success -- the caller MUST keep a
    reference to it alive for the process lifetime (letting it get
    garbage-collected closes the underlying fd and releases the lock early).
    The OS releases the lock automatically on process exit, or the caller
    may explicitly ``.close()`` the handle to release it early. Returns
    ``None`` if another process already holds the lock (a concurrent
    nightly run is in progress) -- the caller must exit without running
    tests or sending Telegram.
    """
    target = lock_file if lock_file is not None else LOCK_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = open(target, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise
        fd.close()
        log("collision — another run holds the lock; exiting")
        return None
    return fd


def extract_failing_node_ids(report: dict) -> list[str]:
    """Return the node IDs of tests that failed or errored in a JSON report.

    De-duplicated and stably sorted so downstream set math and alert text are
    deterministic.
    """
    failing: set[str] = set()
    for test in report.get("tests", []):
        if test.get("outcome") in ("failed", "error"):
            nodeid = test.get("nodeid")
            if nodeid:
                failing.add(nodeid)
    return sorted(failing)


def _spawn_pytest(argv: list[str], timeout: int, env: dict | None = None) -> int:
    """Run a pytest(-wrapper) subprocess in its own process group; return its exit code.

    ``start_new_session=True`` puts the subprocess (and, through the wrapper,
    pytest and every xdist worker it spawns) into a new process group that
    this function owns. On ``TimeoutExpired``, ``subprocess.run``-style
    ``process.kill()`` only ever reaches the *direct* child — under the
    wrapper that is bash, and SIGKILL runs no trap, so
    ``scripts/pytest-clean.sh``'s ``trap cleanup EXIT INT TERM HUP PIPE``
    never fires and the controller (deliberately not `exec`'d) survives with
    its xdist workers, still holding test-DB slots. Killing the whole process
    *group* — SIGTERM, a grace window, then SIGKILL — reaches the wrapper,
    pytest, and every worker in one shot, and can never touch a sibling
    lane's run because each invocation gets its own group. This is the
    orphan-reaping guarantee Task E (wrapper routing) is supposed to buy;
    without owning the group here, a timeout would defeat it.
    """
    proc = subprocess.Popen(
        argv,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(10)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    return proc.returncode


def run_tests() -> tuple[dict | None, dict | None, int]:
    """Run the default collection (``COLLECTION_PATHS``) via the sanctioned wrapper.

    Returns ``(raw_report, summary_or_None, returncode)``:

    - ``raw_report`` — the parsed JSON report, or ``None`` if pytest never
      wrote a fresh one (the run did not happen). This is what
      :func:`validate_run_integrity` needs, since one of its trip conditions
      is a report with no ``summary`` key at all.
    - ``summary_or_None`` — the compact dict ``{passed, failed, error,
      skipped, total, failing_parallel, run_at}`` that ``main()`` consumes as
      ``current``, built only when ``raw_report`` parsed.
    - ``returncode`` — the subprocess exit code (or a signal-shaped value —
      negative or >128 — if the process group had to be killed).

    Never raises on a missing/corrupt report: that case reaches the caller as
    a normal (``None``, ``None``, ``rc``) return so
    :func:`validate_run_integrity` can classify it, rather than dying
    silently in ``main()``. A wedged run's ``subprocess.TimeoutExpired``
    *does* propagate — ``main()`` catches it explicitly and routes it
    through :func:`_fatal`, matching the timeout arm's original shape as an
    exception rather than a sentinel.
    """
    log(
        f"Starting {PYTEST_CLEAN_SH.name} {' '.join(COLLECTION_PATHS)} "
        f"-n {NIGHTLY_XDIST_WORKERS} --json-report ..."
    )

    # The report path is fixed and nothing else unlinks it -- without this, a
    # pytest that never runs (wrapper preflight refusal, wedge, crash) would
    # silently inherit last night's healthy report and read as a clean run.
    Path(PYTEST_JSON_TMP).unlink(missing_ok=True)

    argv = [
        str(PYTEST_CLEAN_SH),
        *COLLECTION_PATHS,
        "-n",
        NIGHTLY_XDIST_WORKERS,
        "--tb=no",
        "-q",
        "--json-report",
        f"--json-report-file={PYTEST_JSON_TMP}",
    ]
    # A patient claim window for the unattended 03:00 run: the interactive
    # 30s default (tests/db_claim.py) is tuned for a human at the keyboard, not
    # a scheduled job with five minutes to spare. 300s is bounded by
    # scripts/pytest-clean.sh's PYTEST_STALL_LIMIT_S (600s low-CPU window on
    # the controller), not by pyproject.toml's --timeout=420: since #2628
    # claim_test_db() polls inside tests/conftest.py::pytest_configure, before
    # collection and therefore before any per-item timer is armed.
    env = {**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}

    # TimeoutExpired propagates to main(), which routes it through _fatal() —
    # this function does not swallow it, so a wedged run pages an operator
    # instead of silently returning a sentinel.
    rc = _spawn_pytest(argv, env=env, timeout=PYTEST_TIMEOUT_SECONDS)
    log(f"pytest exit code: {rc}")

    try:
        raw_report = json.loads(Path(PYTEST_JSON_TMP).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"ERROR: Failed to parse JSON report: {exc}")
        return None, None, rc

    summary = raw_report.get("summary", {})
    current = {
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "error": summary.get("error", 0),
        "skipped": summary.get("skipped", 0),
        "total": summary.get("total", 0),
        "failing_parallel": extract_failing_node_ids(raw_report),
        "run_at": datetime.now(UTC).isoformat(),
    }
    return raw_report, current, rc


def validate_run_integrity(
    report: dict | None, returncode: int, prev: dict
) -> tuple[str | None, list[str]]:
    """Classify a completed (or not) pytest invocation as trustworthy or not.

    Returns ``(trip_reason_or_None, warnings)``. A non-``None`` reason is
    fatal: the caller must alert, skip ``save_last_run()``, skip dispatch, and
    exit non-zero. ``warnings`` never blocks anything but changes how the
    caller persists ``dispatched_nodes`` (see the shallow-shrink case below).

    The returncode alone is insufficient in **both** directions. A run that
    could not execute at all (all test-DB slots held by concurrent lanes) was
    measured, reproducibly, to exit **0** with zero tests collected — plain
    exit-code checking calls that a clean night. Conversely, pytest's exit
    code **1** ("tests failed") is deliberately *not* a trip condition: it is
    a legitimate red night, and reporting it as an infrastructure failure
    would convert every real regression into a false "the run did not
    happen" alert.

    There is deliberately no "collectors contain an error outcome" leg:
    pytest-json-report only ever writes ``passed``/``failed``/``skipped`` for
    a collector, so that condition could never fire, and widening it to
    "!= passed" would route a single broken import — a genuine red-on-main
    regression — through this fatal path every night with no baseline ever
    written. A collection failure is reported through ``main()``'s existing
    collection-error alert branch instead.

    The coverage floor is the load-bearing check. Test-DB starvation does
    **not** produce per-test ``error`` outcomes: since #2628 the claim runs in
    ``pytest_configure``, before collection, and aborts the whole session —
    a starved worker dies as "node down", contributing zero test items. Total
    starvation gives ``total == 0`` (caught below); *partial* starvation
    gives a merely-reduced ``total`` with ``error == 0`` and exit 0 — every
    other check here passes it, which is exactly the false-green shape this
    plan exists to close. The floor is read from persisted state first
    (``prev["min_expected_collected"]``, set by the collection-aware baseline
    on a re-baseline night) and only then from the module constant
    ``MIN_EXPECTED_COLLECTED``; with both unset, the widening night runs
    floorless rather than fabricating a floor with no measurement behind it.
    """
    warnings: list[str] = []

    if report is None:
        return f"pytest wrote no JSON report (exit {returncode}) — the run did not happen", warnings

    if returncode in (2, 3, 4, 5):
        return (
            f"pytest exited {returncode} "
            "(interrupted/internal error/usage error/no tests collected)",
            warnings,
        )

    if returncode < 0 or returncode > 128:
        return f"pytest died to a signal (exit {returncode}) — the run did not complete", warnings

    if "summary" not in report:
        return "pytest report has no 'summary' key — the run did not happen", warnings

    summary = report["summary"]
    total = summary.get("total", 0)
    error = summary.get("error", 0)

    if total == 0:
        return "pytest collected and ran zero tests — the run did not happen", warnings

    if error > max(50, 0.02 * total):
        return (
            f"{error} of {total} tests errored at setup — infrastructure, not a red suite",
            warnings,
        )

    same_collection = prev.get("collection") == COLLECTION_PATHS
    prev_total = prev.get("total") if same_collection else None

    floor = None
    if same_collection and prev_total:
        floor = 0.9 * prev_total
    elif prev.get("collection") is not None and not same_collection:
        # Re-baseline night: prior state records a DIFFERENT collection, so
        # neither the prior total nor the module constant describes what this
        # run should have collected. Deliberately floorless — a narrowed
        # collection must not be judged against the wide one's floor forever.
        # The `collection` comparison in main() routes this run to the seed
        # path.
        #
        # Keyed on the collection field being present and different, not on
        # `prev` merely being non-empty: state that carries a persisted
        # `min_expected_collected` but no `collection` key must still get its
        # floor.
        floor = None
    else:
        floor_base = prev.get("min_expected_collected") or MIN_EXPECTED_COLLECTED
        if floor_base:
            floor = 0.9 * floor_base

    if floor is not None and total < floor:
        return (
            f"only {total} tests ran against a floor of {floor:.0f} — "
            "the run was truncated, not green",
            warnings,
        )

    # Shallow shrink (95%-100% of the same-collection baseline) is a warning,
    # not a trip: a few percent of routine test churn (a deleted or skipped
    # file) should not page anyone, but it does need to change how
    # dispatched_nodes is carried forward — see main()'s use of this warning.
    if same_collection and prev_total and prev_total > total >= 0.9 * prev_total:
        warnings.append(
            f"total shrank from {prev_total} to {total} ({total / prev_total:.0%} of baseline) — "
            "within the routine-churn band, not blocked, but dispatched_nodes carry-forward "
            "uses the union form this run to avoid dropping an already-filed node the shrink "
            "did not reach"
        )

    return None, warnings


def reconfirm_serial(node_ids: list[str]) -> tuple[list[str], list[str], bool]:
    """Re-run the given node IDs serially (`-n0`) to disambiguate xdist noise.

    Returns ``(confirmed, artifacts, serial_trusted)``:
      - ``confirmed`` — node IDs that failed again serially (real regressions).
      - ``artifacts`` — node IDs that passed serially (xdist-parallelism
        collisions on shared state).
      - ``serial_trusted`` — whether the serial report's coverage can be
        trusted. ``False`` only for a *parsed* report that does not cover
        every input node (a starved or partially-run serial pass) — never for
        a timeout or the ``MAX_RECONFIRM_NODES`` bail, both of which are an
        absence of information rather than positive (and incomplete)
        evidence, and untrusting them would deadlock the detector into never
        writing a baseline on a machine whose re-confirm reliably exhausts
        its budget.

    Trust is keyed on **coverage**, not on the ``error`` count: a serial
    process that cannot claim a test-DB slot aborts the whole session at
    ``pytest_configure`` with zero test entries and zero errors — an
    error-keyed check would call that "trusted" and read a starved pass as
    "everything passed", reclassifying every genuinely-red node as an xdist
    artifact. That is #2823's own failure mode rebuilt inside its fix.

    Fail-safe on a timeout or an unparseable report: every input node ID is
    treated as confirmed (never emptied), so a genuine regression is never
    silently hidden behind an infrastructure hiccup.
    """
    if not node_ids:
        return [], [], True

    ordered = sorted(set(node_ids))

    if len(ordered) > MAX_RECONFIRM_NODES:
        log(
            f"Serial re-confirmation skipped: {len(ordered)} failing node(s) exceeds "
            f"MAX_RECONFIRM_NODES={MAX_RECONFIRM_NODES}; treating all as confirmed"
        )
        return ordered, [], True

    log(f"Serial re-confirmation of {len(ordered)} failing node ID(s) with -n0 ...")

    Path(PYTEST_SERIAL_JSON_TMP).unlink(missing_ok=True)

    argv = [
        str(PYTEST_CLEAN_SH),
        *ordered,
        "-n0",
        "--tb=no",
        "-q",
        "--json-report",
        f"--json-report-file={PYTEST_SERIAL_JSON_TMP}",
    ]
    env = {**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}

    try:
        rc = _spawn_pytest(argv, env=env, timeout=PYTEST_RECONFIRM_TIMEOUT_SECONDS)
        log(f"serial re-confirmation exit code: {rc}")
        report = json.loads(Path(PYTEST_SERIAL_JSON_TMP).read_text())
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        log(f"WARNING: serial re-confirmation failed ({exc}); treating all as confirmed")
        return ordered, [], True

    seen = {t.get("nodeid") for t in report.get("tests", [])}
    serial_trusted = all(n in seen for n in ordered)
    if not serial_trusted:
        log(
            "WARNING: serial re-confirmation report does not cover every input node "
            "(starved or partially-run pass) — treating as untrusted"
        )
        return [], [], False

    serial_failing = set(extract_failing_node_ids(report))
    confirmed = [n for n in ordered if n in serial_failing]
    artifacts = [n for n in ordered if n not in serial_failing]
    return confirmed, artifacts, True


def send_telegram(msg: str, dry_run: bool = False) -> None:
    """Send msg via valor-telegram. Best-effort — never crashes the script."""
    if dry_run:
        log(f"[DRY RUN] Would send Telegram: {msg}")
        return

    bin_path = TELEGRAM_BIN
    if not bin_path.exists():
        # Fallback: try PATH resolution
        import shutil

        resolved = shutil.which("valor-telegram")
        if resolved:
            bin_path = Path(resolved)
        else:
            log("WARNING: valor-telegram not found — skipping Telegram notification")
            return

    try:
        subprocess.run(
            [str(bin_path), "send", "--chat", TELEGRAM_CHAT, msg],
            capture_output=True,
            text=True,
            timeout=30,  # timeout-guard: allow
        )
        log(f"Telegram sent: {msg}")
    except Exception as exc:
        log(f"WARNING: Failed to send Telegram: {exc}")


def _fatal(reason: str, dry_run: bool) -> int:
    """Report a pre-alert failure loudly and return the exit code for ``main()``.

    Every arm that used to exit 1 silently (a timeout, a corrupt report, an
    env-load refusal, an integrity-guard trip) now routes through here so it
    pages an operator instead of a nightly job that fails quietly forever.
    No arm reaches ``save_last_run()`` — a failed run must never overwrite a
    good baseline. ``send_telegram`` is documented never-fatal, so no
    try/except is needed around it.
    """
    log(f"FATAL: {reason}")
    send_telegram(f"Nightly tests could not run: {reason}", dry_run=dry_run)
    return 1


def compute_new_failures(prev: dict, confirmed_failing: list[str]) -> list[str]:
    """Node IDs newly confirmed as failing vs. the prior run's confirmed set.

    Set-based so a *shifting* flaky set (same count, different tests) does not
    read as a regression, and a genuinely new failure does — even when the total
    count is flat.

    This drives the Telegram *alert*. Triage dispatch uses
    :func:`compute_dispatch_set` instead, which asks the different question of
    what has not been filed yet.
    """
    prev_failing = set(prev.get("failing_tests", []))
    return sorted(n for n in confirmed_failing if n not in prev_failing)


def prior_dispatched(prev: dict) -> set[str]:
    """Node IDs a previous run already handed to triage.

    Falls back to the prior run's confirmed set when ``dispatched_nodes`` is
    absent, which is the state on the first run after this tracking existed.
    Those nodes are exactly the standing set the detector used to re-dispatch on
    every run, so treating them as already filed is what stops the churn instead
    of replaying it one last time.
    """
    if "dispatched_nodes" in prev:
        return set(prev.get("dispatched_nodes") or [])
    return set(prev.get("failing_tests") or [])


def seeded_nodes(prev: dict) -> set[str]:
    """Node IDs absorbed into a baseline seed's umbrella issue.

    A seeded node has **no per-node issue**. The seed files exactly one
    umbrella titled ``Nightly regression baseline: ...``, while the per-node
    dedup contract in :func:`_build_triage_prompt` keys on a different title,
    ``Nightly regression: {node}``. The two live in separate title namespaces.

    That mismatch is what makes a *flapping* seeded node dangerous.
    :func:`carry_dispatched_nodes` drops a node from ``dispatched_nodes`` the
    moment it stops failing, so a seeded node that passes one night and fails
    the next looks unfiled to :func:`compute_dispatch_set`. It then dispatches
    against a per-node title that never existed, and the triage agent's
    search-before-file check finds nothing — so it files a fresh issue, and
    does so again on every subsequent flap. That rebuilds exactly the
    #2429 / #2430 / #2462 duplicate-issue churn this detector exists to end.

    The live population makes this concrete rather than theoretical: #2807,
    #2808 and #2809 are open guards that sweep the working tree and read stale
    ``.pyc`` files, so they are red or green depending on which interpreter
    last touched ``__pycache__``. They are simultaneously the nodes most
    likely to be in night one's seed and the most likely to flap.

    Suppression here is deliberately **sticky**: a seeded node is never
    per-node dispatched, because the umbrella remains its record. This does
    not silence a regression. :func:`compute_new_failures` keys on
    ``failing_tests``, not on dispatch state, so a seeded node that regresses
    still fires its Telegram alert — only the automatic issue-filing is
    suppressed. Trading an auto-filed issue for an alert on this population is
    the whole point, given the alternative is a duplicate per flap.
    """
    return set(prev.get("seeded_nodes") or [])


def compute_dispatch_set(prev: dict, confirmed_failing: list[str]) -> list[str]:
    """Confirmed-failing node IDs that no previous run has handed to triage.

    The detector used to dispatch the whole confirmed set whenever any failure
    was new, so a standing failure was re-triaged on every run and filed again
    each time — #2429, #2430 and #2462 each opened an issue over the same dead
    watchdog node (issue #2559). Suppressing already-filed nodes is what makes
    a second issue against the same node impossible.

    Two independent suppression sets feed this, and both are required:

    - ``dispatched_nodes`` — nodes with their own per-node issue. Dropped once
      they stop failing, so a genuine fixed-then-regressed node becomes
      dispatchable again (see :func:`carry_dispatched_nodes`).
    - ``seeded_nodes`` — nodes covered by a baseline umbrella issue and having
      no per-node issue at all. Never dropped, because a flap would otherwise
      file a duplicate (see :func:`seeded_nodes`).

    Note this is deliberately *not* ``compute_new_failures``: a node whose
    dispatch subprocess failed is still unfiled, so it stays in the set and gets
    retried on the next run rather than being lost.
    """
    already = prior_dispatched(prev) | seeded_nodes(prev)
    return sorted(n for n in set(confirmed_failing) if n not in already)


def carry_dispatched_nodes(
    prev: dict, confirmed_failing: list[str], just_dispatched: list[str]
) -> list[str]:
    """The ``dispatched_nodes`` set to persist for the next run.

    Keeps a previously-dispatched node only while it is still failing, then adds
    whatever this run dispatched. Dropping a node once it stops failing is what
    makes a genuine re-regression dispatchable again later, and it retires stale
    node IDs on its own: a renamed test (``df6097fe6`` renamed the watchdog node
    the churn kept citing) simply stops appearing in the confirmed set and falls
    out of the state file.
    """
    still_failing = prior_dispatched(prev) & set(confirmed_failing)
    return sorted(still_failing | set(just_dispatched))


class FailureSummary(BaseModel):
    summary: str


def _raw_failure_preview(node_ids: list[str]) -> str:
    """Build the raw node-ID preview text (first 5 + '+N more')."""
    preview = ", ".join(node_ids[:5])
    if len(node_ids) > 5:
        preview += f", +{len(node_ids) - 5} more"
    return preview


def summarize_failures(node_ids: list[str], report: dict) -> str:
    """Summarize newly-confirmed failures via a cheap LLM call, best-effort.

    ``node_ids`` is the newly-confirmed set from :func:`compute_new_failures`,
    which is what makes the "Newly-confirmed" heading below accurate.

    Groups failing node IDs by file and pulls short tracebacks from the
    pytest ``--json-report`` payload when available. On ANY failure (empty
    input short-circuits before the LLM call; network error, schema
    validation failure, timeout, etc. are all caught) falls back to the raw
    node-ID preview format that ``main()`` used to build inline.
    """
    if not node_ids:
        return _raw_failure_preview(node_ids)

    by_file: dict[str, list[str]] = {}
    for nodeid in node_ids:
        file_part = nodeid.split("::", 1)[0]
        by_file.setdefault(file_part, []).append(nodeid)

    tests_by_id = {t.get("nodeid"): t for t in report.get("tests", []) if t.get("nodeid")}

    lines = ["Newly-confirmed nightly test failures, grouped by file:"]
    for file_part, file_node_ids in sorted(by_file.items()):
        lines.append(f"\n{file_part}:")
        for nodeid in file_node_ids:
            lines.append(f"  - {nodeid}")
            test_entry = tests_by_id.get(nodeid, {})
            call = test_entry.get("call", {})
            traceback_text = call.get("longrepr") or test_entry.get("crash", {}).get("message", "")
            if traceback_text:
                snippet = str(traceback_text).strip().splitlines()
                if snippet:
                    lines.append(f"    {snippet[-1][:200]}")
    lines.append(
        "\nWrite a 1-3 sentence plain-English summary of what's failing and a "
        "likely root cause area, for a Telegram alert to an engineer."
    )
    prompt = "\n".join(lines)

    try:
        result = asyncio.run(run_typed(prompt, FailureSummary, model=MODEL_FAST))
        return result.summary
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: summarize_failures LLM call failed ({exc}); using raw preview")
        return _raw_failure_preview(node_ids)


def _build_triage_prompt(dispatch_nodes: list[str]) -> str:
    """Build the default per-node triage prompt with literal, computed titles.

    Titles are computed in Python — ``f"Nightly regression: {n}"`` — rather
    than left for the agent to derive, so the same failing node produces a
    byte-identical title on every machine and the prompt itself pins the
    dedup contract (#2559): the agent must search for the exact title before
    opening a new issue.
    """
    titles = [f"Nightly regression: {n}" for n in dispatch_nodes]
    lines = [
        "Nightly regression detector found confirmed test failures that have not "
        "been triaged before. For EACH node below, search open issues for the "
        "EXACT title given. If found, comment on it with any new information. If "
        "not found, open a new issue with EXACTLY that title, describing the "
        "failure, its likely cause, and suggested next steps. Do NOT attempt an "
        "auto-hotfix — this is an investigation-and-file-an-issue task only.\n",
    ]
    for node, title in zip(dispatch_nodes, titles, strict=True):
        lines.append(f'- Title: "{title}"\n  Node: {node}')
    return "\n".join(lines)


DRY_RUN_SESSION_ID = "dry-run-session"


def maybe_dispatch_triage_session(
    dispatch_nodes: list[str],
    *,
    prompt: str | None = None,
    slug_suffix: str | None = None,
    dry_run: bool = False,
) -> str | None:
    """Dispatch one triage Eng session for node IDs that have never been filed.

    ``dispatch_nodes`` normally comes from :func:`compute_dispatch_set`, so
    every entry is a node no previous run handed to triage. That per-node
    suppression is the whole dedup mechanism: a node with an issue already
    open against it cannot reach this function a second time, which is what
    ends the duplicate-issue churn of #2429 / #2430 / #2462 (issue #2559).

    ``prompt`` overrides the default per-node title-search prompt — used by
    the collection-aware baseline (issue #2823) to dispatch a single umbrella
    triage session for a re-baseline seed instead of one issue per node.
    ``slug_suffix`` overrides the sha256-derived slug, so a retried seed
    dispatch reuses one slug instead of hashing a synthetic node ID.

    ``dry_run`` short-circuits before the subprocess and returns
    :data:`DRY_RUN_SESSION_ID`. Without it ``--dry-run`` suppressed only the
    Telegram send while still spawning a real Eng session that files real
    GitHub issues — which made the one command an operator would reach for to
    preview a run the very command that could not be previewed safely. The
    sentinel is a truthy session id so the caller's success path is exercised
    exactly as it would be for real.

    Returns the dispatched session ID, or ``None`` when there is nothing to
    dispatch or the dispatch subprocess failed. The caller records which nodes
    actually went out via :func:`carry_dispatched_nodes`, so a failed dispatch
    leaves its nodes unfiled and they are retried on the next run.
    """
    if not dispatch_nodes:
        return None

    if slug_suffix is not None:
        slug = f"nightly-triage-{slug_suffix}"
    else:
        slug_hash = hashlib.sha256(",".join(sorted(set(dispatch_nodes))).encode()).hexdigest()[:8]
        slug = f"nightly-triage-{slug_hash}"

    message = prompt if prompt is not None else _build_triage_prompt(dispatch_nodes)

    if dry_run:
        log(
            f"[DRY RUN] Would dispatch triage session slug={slug} for "
            f"{len(dispatch_nodes)} node(s); no session created, no issue filed"
        )
        return DRY_RUN_SESSION_ID

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.valor_session",
                "create",
                "--role",
                "eng",
                "--slug",
                slug,
                "--json",
                "--message",
                message,
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=30,  # timeout-guard: allow
        )
    except Exception as exc:  # noqa: BLE001  # covers TimeoutExpired, FileNotFoundError, etc.
        log(f"WARNING: triage session dispatch failed ({exc})")
        return None

    try:
        session_id = json.loads(result.stdout)["session_id"]
    except Exception:  # noqa: BLE001
        log(f"WARNING: could not parse session_id from dispatch stdout: {result.stdout!r}")
        session_id = None

    log(f"Triage session dispatched: slug={slug} session_id={session_id}")
    return session_id


def load_env_or_die() -> tuple[int, str | None]:
    """Load the .env vault into os.environ, or return a refusal reason (issue #2327).

    Replaces the plist's ``/bin/bash -c "set -a; source .env; ..."`` wrapper,
    which silently EPERM'd every night because ``/bin/bash`` lacks the macOS
    Desktop-folder TCC grant the venv python holds. A silent empty environment —
    the actual defect — is now a loud non-zero exit, never a quiet degraded run.

    Non-clobbering: an already-set var (a caller's shell, or a future plist
    injection) wins over the file.

    Returns ``(applied, reason)``. On success, ``reason`` is ``None`` and
    ``applied`` is the count of keys applied. On either refusal path,
    ``reason`` is a human-readable string for the caller to route through
    :func:`_fatal` — this function never raises ``SystemExit`` itself, so a
    refusal here is no longer indistinguishable from any other silent exit(1).
    """
    from dotenv import dotenv_values

    try:
        values = dotenv_values(ENV_FILE)
    except OSError as exc:
        reason = (
            f"could not read {ENV_FILE} (resolves to {os.path.realpath(ENV_FILE)}): "
            f"{exc}. Under launchd this means the executing binary lacks the macOS "
            "Desktop-folder TCC grant — see issue #2327. Refusing to run with no environment."
        )
        return 0, reason

    applied = 0
    for key, value in values.items():
        if value is None:
            continue
        os.environ.setdefault(key, value)
        applied += 1

    if applied < MIN_ENV_KEYS:
        reason = (
            f"loaded only {applied} env vars from {ENV_FILE} (expected >= "
            f"{MIN_ENV_KEYS}). Refusing to run the nightly suite with a near-empty "
            "environment — see issue #2327."
        )
        return applied, reason

    log(f"Loaded {applied} env vars from {ENV_FILE}")
    return applied, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly regression test runner")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending Telegram")
    args = parser.parse_args()

    log("=== Nightly regression test run starting ===")

    # Load the .env vault into os.environ before any subprocess spawns, and fail
    # loudly if it cannot be read (issue #2327). This is the FIRST substantive
    # step: the pytest/valor-telegram children inherit these vars. This runs
    # BEFORE the run lock: an env refusal must never be confused with a lock
    # collision (which correctly returns 0 silently), and neither path writes
    # state.
    _applied, env_err = load_env_or_die()
    if env_err:
        return _fatal(env_err, args.dry_run)

    # Acquire the run lock first, before any other work -- a concurrent
    # nightly run holding the lock means this invocation is a collision and
    # must exit cleanly without running tests or sending Telegram.
    lock_handle = _acquire_run_lock()
    if lock_handle is None:
        return 0

    # Load previous state
    prev = load_last_run()
    is_first_run = not prev  # Empty dict means no prior state
    if is_first_run:
        log("No prior run state found — this is the first run")
    else:
        log(f"Prior run: failed={prev.get('failed', 0)}, run_at={prev.get('run_at', 'unknown')}")

    # Collection-aware baseline (issue #2823): a run whose recorded collection
    # differs from the current one is treated as a fresh baseline, same as
    # the true first run — otherwise every node absorbed by a widened
    # collection reads as a brand-new failure and reopens #2429/#2430/#2462.
    is_reseed = bool(prev) and prev.get("collection") != COLLECTION_PATHS
    is_seed_run = is_first_run or is_reseed
    if is_reseed:
        log(
            f"WARNING: recorded collection {prev.get('collection')!r} differs from "
            f"current {COLLECTION_PATHS!r} — re-baselining. Every currently-failing "
            "node is absorbed into the seed and escalated as a single umbrella issue "
            "(see #2429/#2430/#2462) rather than filed individually."
        )

    # Run the default collection through the sanctioned wrapper. A wedged run
    # raises TimeoutExpired rather than returning a sentinel — route it
    # through _fatal() the same way the pre-#2823 timeout arm did, just
    # alerting instead of dying silently.
    try:
        raw_report, current, rc = run_tests()
    except subprocess.TimeoutExpired:
        return _fatal(
            f"pytest timed out after {PYTEST_TIMEOUT_SECONDS}s (process group killed)",
            args.dry_run,
        )
    reason, integrity_warnings = validate_run_integrity(raw_report, rc, prev)
    if reason:
        return _fatal(reason, args.dry_run)
    for w in integrity_warnings:
        log(f"WARNING: {w}")

    parallel_failing = current.get("failing_parallel", [])
    log(
        f"Results (parallel -n {NIGHTLY_XDIST_WORKERS}): passed={current['passed']}, "
        f"failed={current['failed']}, error={current['error']}, total={current['total']}"
    )

    # Serial re-confirmation gate (issue #2180): re-run only the failing node IDs
    # serially to separate real regressions from xdist-parallelism artifacts.
    confirmed_failing, artifacts, serial_trusted = reconfirm_serial(parallel_failing)
    if not serial_trusted:
        return _fatal(
            "serial re-confirmation returned no result for every node — the run did not happen",
            args.dry_run,
        )
    if parallel_failing:
        log(
            f"Re-confirmation: {len(confirmed_failing)} confirmed, "
            f"{len(artifacts)} xdist artifact(s)"
        )
        if artifacts:
            log(
                "xdist-parallelism artifacts (passed serially, not alerted): "
                + ", ".join(artifacts)
            )
        if confirmed_failing:
            log("Confirmed serial failures: " + ", ".join(confirmed_failing))

    # The confirmed set is the authoritative failure signal; keep the raw parallel
    # count for observability.
    current["failed_parallel"] = current["failed"]
    current["failed"] = len(confirmed_failing)
    current["failing_tests"] = confirmed_failing
    current["artifact_tests"] = artifacts
    # Drop the transient parallel list from persisted state — the confirmed set is
    # what future runs diff against.
    current.pop("failing_parallel", None)

    current["dispatched_session_id"] = prev.get("dispatched_session_id")
    current["collection"] = COLLECTION_PATHS
    current["head_commit"] = _get_head_commit()

    new_failures = compute_new_failures(prev, confirmed_failing)
    new_errors = current.get("error", 0)
    log(
        f"Newly-confirmed failures: {len(new_failures)}; "
        f"confirmed total: {current['failed']}; collection errors: {new_errors}"
    )

    triage_session_id: str | None = None
    if is_seed_run:
        # The baseline is a declaration of the known-failing state, not a
        # finding. Seed dispatched_nodes with the WHOLE confirmed set so the
        # next run does not dispatch the entire suite's standing failures as
        # fresh discoveries, and escalate it as exactly ONE umbrella triage
        # session rather than reusing the per-node reassignment below (which
        # would wipe the seed to []).
        just_dispatched: list[str] = []
        dispatch_nodes: list[str] = []
        if confirmed_failing:
            seed_size = len(confirmed_failing)
            seed_title = (
                f"Nightly regression baseline: {seed_size} nodes absorbed "
                f"on {current['head_commit']}"
            )
            seed_prompt = (
                "Nightly regression detector re-baselined its test collection "
                f"(old={prev.get('collection')!r}, new={COLLECTION_PATHS!r}). "
                f"The following {seed_size} node(s) were already failing at the "
                "moment of the re-baseline and have been absorbed into the seed — "
                "they are NOT individually filed. Search open issues for the EXACT "
                f'title "{seed_title}". If found, comment on it. If not found, open '
                f"ONE umbrella issue with EXACTLY that title, summarizing the "
                "population, its size, and pointing at the persisted state file "
                "for the full node list. Do NOT file per-node issues for these. Do "
                "NOT attempt an auto-hotfix.\n\n"
                "Seeded node IDs:\n" + "\n".join(f"- {n}" for n in confirmed_failing)
            )
            triage_session_id = maybe_dispatch_triage_session(
                [f"seed:{len(confirmed_failing)}"],
                prompt=seed_prompt,
                slug_suffix="baseline",
                dry_run=args.dry_run,
            )
            # A failed seed dispatch must NOT write a baseline. Recording the
            # seed anyway would mark every absorbed node as filed while no
            # umbrella issue exists, so compute_dispatch_set() would suppress
            # all of them forever and the whole night-one population would be
            # lost silently — behind a Telegram message that reads like
            # success. Refusing to save state instead means the next run sees
            # no prior state, re-seeds, and retries the dispatch. This mirrors
            # _fatal()'s existing invariant that no untrusted run reaches
            # save_last_run().
            if triage_session_id is None:
                return _fatal(
                    f"seed triage dispatch failed — no umbrella issue exists for "
                    f"{seed_size} absorbed node(s); refusing to write a baseline "
                    "so the next run retries the seed",
                    args.dry_run,
                )
            current["dispatched_session_id"] = triage_session_id
            just_dispatched = list(confirmed_failing)
            current["seed_collection"] = COLLECTION_PATHS
            current["seed_size"] = len(confirmed_failing)
            current["seeded_nodes"] = list(confirmed_failing)
            current["min_expected_collected"] = current["total"]
    else:
        dispatch_nodes = compute_dispatch_set(prev, confirmed_failing)
        if len(dispatch_nodes) > MAX_DISPATCH_NODES:
            log(
                f"Truncating dispatch: {len(dispatch_nodes)} not-yet-triaged node(s) "
                f"exceeds MAX_DISPATCH_NODES={MAX_DISPATCH_NODES}; dispatching first "
                f"{MAX_DISPATCH_NODES}, retrying the rest on a later run"
            )
            dispatch_nodes = dispatch_nodes[:MAX_DISPATCH_NODES]
        just_dispatched = []
        if dispatch_nodes:
            log(f"Not yet triaged: {len(dispatch_nodes)} node(s): " + ", ".join(dispatch_nodes))
        suppressed = len(confirmed_failing) - len(dispatch_nodes) - len(just_dispatched)
        if suppressed > 0:
            log(f"Triage dispatch suppressed for {suppressed} already-filed node(s)")

        triage_session_id = maybe_dispatch_triage_session(dispatch_nodes, dry_run=args.dry_run)
        if triage_session_id is not None:
            # Record only what actually went out. A failed dispatch leaves its nodes
            # unfiled, so the next run picks them up again instead of losing them.
            just_dispatched = dispatch_nodes
            current["dispatched_session_id"] = triage_session_id

    # Carry the seed's umbrella coverage forward on EVERY run, not just seed
    # runs. Without this the set exists only in the night the seed was written
    # and is gone by night two, which would leave compute_dispatch_set() blind
    # to it exactly when the first flap arrives. Union rather than replace: a
    # later re-baseline adds a second umbrella without invalidating the first.
    current["seeded_nodes"] = sorted(seeded_nodes(prev) | set(current.get("seeded_nodes") or []))

    # The shallow-shrink warning changes how dispatched_nodes carries forward:
    # carry_dispatched_nodes()'s prior_dispatched(prev) & confirmed_failing
    # intersection silently drops any already-filed node a truncated run did
    # not reach, so on a shrink warning we union instead of intersect.
    if any("total shrank" in w for w in integrity_warnings):
        current["dispatched_nodes"] = sorted(prior_dispatched(prev) | set(just_dispatched))
    else:
        current["dispatched_nodes"] = carry_dispatched_nodes(
            prev, confirmed_failing, just_dispatched
        )

    # Alert logic — regression fires only on newly-confirmed serial failures.
    if is_seed_run:
        seed_note = " (re-baseline: prior population absorbed)" if is_reseed else ""
        msg = (
            f"Nightly regression baseline established{seed_note}: "
            f"{current['total']} tests, {current['failed']} confirmed failures."
        )
        send_telegram(msg, dry_run=args.dry_run)
    elif new_failures:
        try:
            serial_report = json.loads(Path(PYTEST_SERIAL_JSON_TMP).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            serial_report = {}
        summary_text = summarize_failures(new_failures, serial_report)
        msg = (
            f"Nightly regression: {len(new_failures)} newly-confirmed failure(s) "
            f"({current['failed']} confirmed total): {summary_text}. "
            f"Run: pytest {' '.join(COLLECTION_PATHS)} -n0"
        )
        if triage_session_id:
            msg += f" [triage session: {triage_session_id}]"
        send_telegram(msg, dry_run=args.dry_run)
    elif new_errors > 0:
        msg = (
            f"Nightly tests: collection error ({new_errors} errors). "
            f"Run: pytest {' '.join(COLLECTION_PATHS)} -n {NIGHTLY_XDIST_WORKERS}"
        )
        send_telegram(msg, dry_run=args.dry_run)
    else:
        log("Clean run (no newly-confirmed failures) — no Telegram alert sent")

    # Save state
    save_last_run(current)
    log(f"State saved to {LAST_RUN_FILE}")

    # Post-run TTFT gate (issue #1227). A TTFT regression is reported as a
    # regression (Telegram alert), not a test failure — return code unchanged.
    try:
        ttft_alert = run_ttft_gate(
            log_file=TTFT_LOG_FILE,
            session_type=TTFT_SESSION_TYPE,
            last=TTFT_LAST_N,
            threshold=TTFT_THRESHOLD_SECONDS,
        )
        if ttft_alert:
            send_telegram(ttft_alert, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        log(f"TTFT gate hook error (non-fatal): {exc}")

    log("=== Nightly regression test run complete ===")
    return 0


def _invoke_check_ttft(
    *,
    log_file: Path,
    session_type: str,
    last: int,
    threshold: float,
) -> tuple[int, str]:
    """Invoke ``scripts/check_ttft.py`` as a subprocess and return (rc, stdout).

    Subprocess invocation (not direct import) keeps the nightly runner
    decoupled from the gate's internal API and matches the plan's wording
    "post-run call to ``python scripts/check_ttft.py ...``".
    """
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIR / "scripts" / "check_ttft.py"),
            "--session-type",
            session_type,
            "--last",
            str(last),
            "--threshold",
            str(threshold),
            "--log-file",
            str(log_file),
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=60,  # timeout-guard: allow
    )
    return result.returncode, (result.stdout or "").strip()


def run_ttft_gate(
    *,
    log_file: Path,
    session_type: str,
    last: int,
    threshold: float,
) -> str | None:
    """Run the TTFT regression gate as a post-test check.

    Returns:
        ``None`` on PASS or when no data is available yet (first deploy /
        no PM sessions logged); a Telegram-ready alert string on FAIL.
        Per the plan, a TTFT regression is reported as a regression
        (Telegram alert), not a test failure — the caller does not change
        its return code based on this gate.

    All exceptions are swallowed: the TTFT gate must never crash the
    nightly run.
    """
    if not log_file.exists():
        log(f"TTFT gate skipped: {log_file} not present (no data yet)")
        return None

    try:
        rc, stdout = _invoke_check_ttft(
            log_file=log_file,
            session_type=session_type,
            last=last,
            threshold=threshold,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"TTFT gate error (non-fatal): {exc}")
        return None

    log(f"TTFT gate result: rc={rc} stdout={stdout!r}")
    if rc == 0:
        return None

    # Failure path — surface as a regression alert, not a test failure.
    detail = stdout if stdout else "no detail"
    return (
        f"TTFT regression (issue #1227): {detail} "
        f"[session_type={session_type} last={last} threshold={threshold:g}s]"
    )


def _get_head_commit() -> str | None:
    """Return ``git rev-parse HEAD`` at run start, for attribution only.

    Best-effort: never crashes the run. Captured once per run rather than
    per-write, since the working tree does not change mid-run.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,  # timeout-guard: allow
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


if __name__ == "__main__":
    sys.exit(main())
