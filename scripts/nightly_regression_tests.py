#!/usr/bin/env python3
"""Nightly regression test runner.

Runs the default pytest collection (``COLLECTION_PATHS`` — the same set a bare
``scripts/pytest-clean.sh`` collects) with a JSON report, compares against a
prior run, and records what it finds on the **GitHub issue tracker**.

The tracker is the detector's only output surface. It sends no Telegram, no
mail, and no notification of any kind — not for a regression, not for a
baseline, not for an infrastructure failure. A night with nothing new to say
says nothing anywhere except this script's log. Everything it does have to say
becomes at most one issue per distinct finding, and a finding that already has
an open issue becomes a **comment on that issue** rather than a second one.

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
``total`` catches it.

A tripped guard means the *measurement* is untrustworthy, not that there is
nothing to report — the 2026-09-03 storm tripped nothing only because the
ceiling was unreachable, and the thing it was hiding was a real defect. So a
trip does not swallow the night: it still collapses the failures into cascade
umbrellas and comments-or-files those (:func:`dispatch_findings` with
``cascades_only=True``), then stops. It deliberately does **not** overwrite the
baseline totals or the confirmed-failing set — those numbers are the ones it
just declared untrustworthy — and persists only what was filed, so a storm that
recurs for a week accretes comments on one issue instead of re-filing it.

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
count), so a *newly-confirmed* serial failure is distinguishable from the
standing population. Artifacts are logged but never filed, killing the
parallel-execution noise. The serial re-run targets only the already-failing
node IDs, so it stays fast and never re-runs the whole suite.

The serial pass also reports whether it can be *trusted*: a report that does
not cover every input node (a starved serial worker, a wedge) must not be read
as "these all passed" — that is the false green :func:`reconfirm_serial`'s
``serial_trusted`` return value exists to prevent.

Triage dispatch is deduped per node (issue #2559)
-------------------------------------------------
Two different questions live in this script and must not be conflated.
:func:`compute_new_failures` asks "is this a regression since last night"; the
dispatch asks "does this node already have an issue against it"
(``compute_dispatch_set``, diffing against the persisted ``dispatched_nodes``
set). Conflating them re-triaged the entire standing failure set whenever any
one failure was new, which is how #2429, #2430 and #2462 each filed an issue
over the same dead watchdog node. A node stays suppressed while it keeps
failing and drops out of the set once it passes, so a genuine re-regression is
dispatchable again and a renamed node retires itself.

One issue per distinct finding, comments for the rest (issues #3131, #3134)
---------------------------------------------------------------------------
Per-node dedup answers "has this node been filed", which says nothing about
whether two nodes are the same defect. One poisoned xdist worker — a leaked
global Redis pool that makes the autouse ``redis_test_db`` fixture raise for
every subsequent test on that worker — produced 278 identical setup errors on
2026-09-03 and 26 GitHub issues. Four things now stand between that shape and
the tracker.

1. An absolute ``NIGHTLY_MAX_SETUP_ERRORS`` ceiling that calls such a night
   infrastructure rather than a red suite. The old ceiling was
   ``max(50, 0.02 * total)``, whose relative term *raises* the bar — at the
   widened collection's ~16k items it stood at 325 and could not fire.
2. :func:`group_setup_error_cascades`, which turns nodes sharing one normalized
   setup-error message into ONE umbrella finding.
3. **Comment-over-create.** A finding whose issue is already open is never
   filed again: :func:`comment_on_issue` posts the recurrence (run timestamp,
   HEAD, blast radius, worker ids) on the existing issue instead. Suppressing
   the duplicate silently — the first cut of this fix — loses the one signal
   that says a defect is still live, so a recurring cascade should be one issue
   accreting one comment per night, forever.
4. A ``NIGHTLY_MAX_ISSUES_PER_RUN`` budget both issue shapes draw from, so a run
   cannot exceed it by splitting findings across the two. Everything suppressed
   or deferred is logged rather than silently dropped.

Identity is what makes (3) work, and it is deliberately **not** the rendered
issue title. A cascade is identified by its normalized setup-error signature —
the same key :func:`cascade_title` hashes — and the signature is persisted
alongside the issue number it produced in ``cascade_issues``. A title is a
rendering that can be edited by a human or changed by a future version of this
script; matching on one is how the same defect gets filed twice. The recorded
number is the primary lookup and the title match is the fallback that bootstraps
it, since the triage session, not this script, is what actually opens the issue,
so its number is only discoverable on a later run.

``cascade_issues`` is per-machine state, like ``dispatched_nodes``. The open-issue
read (:func:`open_issues`) is the only check that spans machines, and it is a
read-then-act check with no lock — two machines dispatching inside the same
window still both file.

Collection-aware baseline (issue #2823)
----------------------------------------
The persisted state records which ``collection`` produced it. When the
recorded collection differs from ``COLLECTION_PATHS`` (the widening night, or
any future change of scope), the run takes the existing first-run baseline
path: it seeds ``dispatched_nodes`` with the whole currently-failing
population and dispatches **one** umbrella triage session, rather than filing
every one of those nodes individually — reopening the #2429/#2430/#2462 churn
the dedup set exists to prevent.

A post-run TTFT gate (issue #1227) checks cold-start latency and logs a
regression without changing the exit code. It used to page; it now only logs,
because the tracker is this script's only output surface (#3134). The gate
itself is retained — the measurement and its evidence are still worth having —
but nothing carries its verdict off this machine.

Usage:
    python scripts/nightly_regression_tests.py             # Run tests, file/comment on findings
    python scripts/nightly_regression_tests.py --dry-run   # Preview; files nothing, writes nothing
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LOCK_FILE = DATA_DIR / "nightly_tests.lock"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
PYTEST_CLEAN_SH = PROJECT_DIR / "scripts" / "pytest-clean.sh"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
PYTEST_SERIAL_JSON_TMP = "/tmp/nightly_pytest_serial_report.json"
# The baseline classifier gets its OWN report path and must never write either
# path above. main() re-reads PYTEST_SERIAL_JSON_TMP *after* the classifier runs
# to build the human's alert text (summarize_failures); overwriting it with the
# baseline commit's results would summarize the alert from a report in which
# every newly-broken node passed.
PYTEST_BASELINE_JSON_TMP = "/tmp/nightly_pytest_baseline_report.json"

# The persistent, provisioned worktree the classifier re-runs failing node IDs
# in, checked out detached at the prior run's HEAD SHA. It needs its own .venv:
# scripts/pytest-clean.sh refuses a linked worktree without one (#3033) and
# refuses an off-pin interpreter (#2617). It is protected from the worktree
# sweeper by tools/disk_reclaim.py's PROTECTED_WORKTREE_SLUGS.
BASELINE_WORKTREE = PROJECT_DIR / ".worktrees" / "nightly-baseline"
# Records the uv.lock digest the worktree's .venv was last provisioned against,
# so `uv sync` re-runs only when the lockfile actually moved.
BASELINE_PROVISION_MARKER = ".nightly-baseline-provisioned"

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

# The baseline classifier re-runs only the newly-confirmed failing node IDs
# (capped by NIGHTLY_FIX_MAX_FAILURES) serially in the baseline worktree, so it
# is bounded by the same reasoning as PYTEST_RECONFIRM_TIMEOUT_SECONDS above.
# Deliberately a plain module int rather than an env knob, matching both
# neighbours' convention.
PYTEST_BASELINE_TIMEOUT_SECONDS = 1800  # 30 minutes max

# Provisioning the baseline worktree must never hang the nightly run: every
# git/uv subprocess carries one of these explicit bounds, and a TimeoutExpired
# is bucketed `inconclusive` exactly like a non-zero exit. Provisional/tunable —
# a `git worktree add` is seconds on a warm checkout, and a cold `uv sync`
# minutes.
BASELINE_GIT_TIMEOUT_SECONDS = 300
BASELINE_UV_SYNC_TIMEOUT_SECONDS = 900

# If a serial re-confirmation would have to re-run more than this many nodes,
# skip the serial pass entirely rather than spawn a subprocess that will
# almost certainly exhaust its own timeout. Returns every input node as
# confirmed (never an empty set) with serial_trusted=True — the same
# practical result the timeout fail-safe already produces, reached
# immediately instead of after a multi-minute wait.
MAX_RECONFIRM_NODES = 200

# Hard ceiling on how many GitHub issues one run may cause — cascade umbrellas
# and per-node issues draw from the SAME budget, so a night cannot exceed it by
# splitting its findings across the two shapes. Suppressed nodes are logged and
# retried on a later run rather than lost: only what actually went out is
# recorded in dispatched_nodes.
#
# Provisional/tunable via NIGHTLY_MAX_ISSUES_PER_RUN. 10 is inherited from the
# old per-node cap and is a blast-radius bound, not a measurement — with
# cascade collapsing in front of it (see group_setup_error_cascades) a healthy
# night should never come close to it, so a run that hits the cap is itself a
# signal worth reading in the log.
#
# Like the NIGHTLY_FIX_* knobs below, this and its two siblings are read at
# CALL time via resolve_int_knob(), never at import: `.env` only reaches
# os.environ through load_env_or_die() inside main(), so an import-time read
# would freeze the in-code default and make the vault setting inert on the one
# surface that matters.
MAX_ISSUES_PER_RUN_DEFAULT = 10

# A group of nodes sharing one normalized setup-error message on one xdist
# worker is a cascade — one defect, not N — once it reaches this size. Below it
# the nodes are filed individually, because two or three co-failing setups are
# as likely to be genuinely distinct bugs as one poisoned worker.
#
# Provisional/tunable via NIGHTLY_CASCADE_MIN_GROUP_SIZE. 3 is a first guess:
# the motivating incident (#3131) was 278 nodes on one worker, three orders of
# magnitude above any plausible threshold, so the exact value is uncontested by
# the evidence that exists.
CASCADE_MIN_GROUP_SIZE_DEFAULT = 3

# A group of nodes whose test BODIES fail with the same normalized first error
# line is one root cause once it reaches this size (#3075: 2026-08-24 filed 39
# issues over what were two causes, both body failures the setup-cascade path
# could not see). Deliberately higher than the setup threshold: body failures
# sharing a message are more often coincidence than setup storms are, and the
# false-merge cost (two distinct bugs behind one umbrella) is real. The rule is
# exact equality of the normalized first `E ` line — the 2026-08-24 second
# cause (nine worker-key nodes inside a 66-node TypeError batch) had a
# different first line and stays a separate group under it.
#
# Provisional/tunable via NIGHTLY_BODY_CASCADE_MIN_GROUP_SIZE.
BODY_CASCADE_MIN_GROUP_SIZE_DEFAULT = 5

# Ceiling on setup-error outcomes before validate_run_integrity calls the run
# infrastructure rather than a red suite.
#
# This was `max(50, 0.02 * total)`, which was a bug: the relative term RAISES
# the bar, so once COLLECTION_PATHS widened to ~16k items the ceiling became
# 325 and the intended absolute floor of 50 was unreachable. The night of
# 2026-09-03 put 278 identical fixture errors through it as a legitimately red
# suite (#3131). An absolute ceiling is the honest form: 50 tests erroring in
# *setup* is infrastructure at any collection size.
#
# Provisional/tunable via NIGHTLY_MAX_SETUP_ERRORS. Raising it does not make a
# storm quieter — the cascade collapsing below handles the sub-ceiling case —
# it only decides whether the night pages an operator instead of filing.
MAX_SETUP_ERRORS_DEFAULT = 50

# How many open issues the pre-file dedup read pulls. The `gh issue list` REST
# path is used deliberately over `--search`, which is index-backed and lags
# behind issue creation by minutes — exactly the window in which two machines'
# triage sessions file the same title twice.
OPEN_ISSUE_LIST_LIMIT = 1000
OPEN_ISSUE_LIST_TIMEOUT_SECONDS = 60

# How many closed issues the closed-state dedup read pulls (#3075 defect 1:
# a node whose exact-title issue was closed as a duplicate was re-reported as
# "previously untriaged" forever, because dedup consulted open issues only).
# `gh issue list` returns most-recently-updated first, so the window covers
# the closures that can plausibly recur.
CLOSED_ISSUE_LIST_LIMIT = 1000

# Posting one recurrence comment. Bounded on the same rule as the list read
# above; a comment that cannot be posted is logged and the finding is left
# unrecorded, so the next run retries it rather than losing the recurrence.
GH_COMMENT_TIMEOUT_SECONDS = 60

# How many node IDs a recurrence comment lists before truncating to a count.
# GitHub rejects an issue comment body over 65536 characters outright, and the
# motivating cascade (278 nodes) already renders ~31KB — a whole-suite poisoning
# would exceed the limit and post NOTHING, losing the recurrence entirely.
#
# Provisional/tunable. 200 keeps the worst case near 25KB with generous headroom
# for long parametrized node IDs. It is deliberately a plain module int and not
# an env knob: nobody tunes this per machine, and the counts above the list —
# not the list — are what the comment is actually for.
MAX_COMMENT_NODES_LISTED = 200

# Autonomous-fix gate mode (issue #2334). Two values ship:
#   off     — skip classification, the gate, and the verdict log entirely;
#             the detector behaves exactly as it did before this feature.
#   shadow  — classify, gate, and LOG the verdict that would have been acted
#             on, while paging a human with byte-identical alert text. Nothing
#             is fixed.
# Anything else is treated as `off` (fail toward today's behavior).
# Provisional/tunable: `shadow` is the default because the whole point of this
# tier is to accumulate verdict evidence; flip to `off` on a machine where the
# extra bounded pytest run is unwelcome. Acting on the verdict is #3076.
#
# Both NIGHTLY_FIX_* env knobs are read at CALL time (resolve_fix_mode /
# resolve_fix_max_failures), never at module import: `.env` only reaches
# os.environ via load_env_or_die() inside main(), and the nightly launchd job
# supplies just PATH and HOME, so an import-time read would freeze the in-code
# defaults and make the vault-`.env` off switch inert on the only surface that
# matters.
NIGHTLY_FIX_MODE_DEFAULT = "shadow"

# Volume ceiling on the newly-confirmed set the gate will consider, checked
# BEFORE any classification work so the cost is never paid first. Deliberately
# NOT reconciled with MAX_DISPATCH_NODES above: that one truncates the
# triage-filing set, never new_failures, and folding it in here would kill every
# configured value above 10 and disqualify this feature's own motivating case
# (#2399 had 11 newly-confirmed failures). Provisional/tunable via
# NIGHTLY_FIX_MAX_FAILURES — 15 is a first guess, to be tuned from shadow data.
NIGHTLY_FIX_MAX_FAILURES_DEFAULT = 15

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
# the pytest / gh / triage-session subprocesses this script spawns inherit API keys,
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


def resolve_int_knob(name: str, default: int) -> int:
    """Read an integer knob from the environment at CALL time.

    Module-scope ``os.environ`` reads freeze config at import, and this script
    only populates ``os.environ`` from the vault ``.env`` inside ``main()``
    (:func:`load_env_or_die`) — so an import-time read of a nightly knob is
    always the in-code default, whatever the vault says. Same rule and same
    failure mode as :func:`resolve_fix_mode` / :func:`resolve_fix_max_failures`,
    generalized so the noise-control knobs cannot drift from it.

    A malformed value degrades to ``default`` with a warning rather than
    raising: a bad knob must never take down the nightly.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"WARNING: malformed {name}={raw!r} — using default {default}")
        return default


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


def _spawn_pytest(
    argv: list[str], timeout: int, env: dict | None = None, cwd: Path | str = PROJECT_DIR
) -> int:
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

    ``cwd`` defaults to ``PROJECT_DIR``, preserving both existing callers
    (``run_tests`` and ``reconfirm_serial``), which pass nothing.
    """
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
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
    plan exists to close.

    Floor precedence, in the order the branches actually run:

    1. ``0.9 * prev["total"]`` when the recorded ``collection`` matches the
       current one. This is the live floor on every ordinary night, and it
       takes precedence over everything below.
    2. No floor at all when prior state records a *different* collection — a
       re-baseline night, where neither the old total nor a constant measured
       against a different scope describes what should have been collected.
    3. ``0.9 * (prev["min_expected_collected"] or MIN_EXPECTED_COLLECTED)``
       otherwise, which in practice means the true first run. With both unset
       that night is floorless rather than judged against a fabricated number.

    Note that (1) shadows the persisted ``min_expected_collected`` on every
    subsequent same-collection night, so for state this version writes, that
    field is near-unreachable. It is retained because it is the only floor
    source that survives a state file written by a future version that stops
    recording ``total``.
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

    max_setup_errors = resolve_int_knob("NIGHTLY_MAX_SETUP_ERRORS", MAX_SETUP_ERRORS_DEFAULT)
    if error > max_setup_errors:
        return (
            f"{error} of {total} tests errored at setup (ceiling {max_setup_errors}) — "
            "infrastructure, not a red suite",
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


def _run_provision_step(argv: list[str], *, cwd: Path | str, timeout: int) -> bool:
    """Run one bounded baseline-provisioning subprocess; return True on success.

    Every provisioning step (``git worktree add``, ``git checkout --detach``,
    ``uv sync``) goes through here so that all of them carry an explicit
    ``timeout=``. A ``TimeoutExpired`` is reported as failure exactly like a
    non-zero exit: an unbounded ``uv sync`` at 03:00 on a cold or
    network-stalled cache otherwise has no bound and no route to a bucket.

    Same process-group shape as :func:`_spawn_pytest`: ``start_new_session``
    plus ``killpg`` on timeout. A ``subprocess.run(timeout=)`` here would kill
    only the direct child and then block in ``communicate()`` while a
    surviving ``uv`` build grandchild held the captured pipe — hanging the
    nightly indefinitely before the page.
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        log(f"WARNING: baseline provisioning step {argv!r} failed: {exc}")
        return False
    try:
        _stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(10)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        log(f"WARNING: baseline provisioning step {argv!r} failed: {exc}")
        return False
    if proc.returncode != 0:
        log(
            f"WARNING: baseline provisioning step {argv!r} exited "
            f"{proc.returncode}: {(stderr or '').strip()}"
        )
        return False
    return True


def provision_baseline_worktree(
    baseline_sha: str,
    *,
    repo_root: Path = PROJECT_DIR,
    worktree_path: Path = BASELINE_WORKTREE,
) -> bool:
    """Point the persistent baseline worktree at ``baseline_sha`` with a usable ``.venv``.

    Creates the worktree with ``git worktree add --detach`` plus a full
    ``uv sync`` when it is absent; otherwise re-points it with
    ``git -C <path> checkout --detach`` and re-runs ``uv sync`` **only when
    ``uv.lock`` changed** since the last provision (recorded in the
    ``BASELINE_PROVISION_MARKER`` file inside the worktree). That is what keeps
    the amortized cost near zero on the common night.

    The ``.venv`` is mandatory, not an optimization: ``scripts/pytest-clean.sh``
    refuses a linked worktree that has none (#3033) and refuses an off-pin
    interpreter (#2617), and the committed ``.python-version`` is what makes a
    bare ``uv sync`` land on the pinned interpreter.

    Returns ``False`` on any failure. The caller buckets everything
    ``inconclusive`` in that case and **never** falls back to ``PROJECT_DIR`` —
    a fallback would import HEAD's source and classify every node
    ``pre_existing``, which looks exactly like a working classifier.
    """
    # Hardening: the SHA comes from last_run.json (state this script writes
    # itself), but a non-hex value — or one starting with `-` — must never
    # reach git argv. A `--` separator is NOT usable here: for `git checkout`
    # it marks the pathspec boundary, so `checkout --detach -- <sha>` would
    # reinterpret the SHA as a path. Validating the shape is the correct guard.
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", baseline_sha):
        log(f"WARNING: baseline SHA {baseline_sha!r} is not a valid hex SHA")
        return False

    def _point_at_baseline() -> bool:
        if worktree_path.exists():
            return _run_provision_step(
                ["git", "-C", str(worktree_path), "checkout", "--detach", baseline_sha],
                cwd=repo_root,
                timeout=BASELINE_GIT_TIMEOUT_SECONDS,
            )
        return _run_provision_step(
            ["git", "worktree", "add", "--detach", str(worktree_path), baseline_sha],
            cwd=repo_root,
            timeout=BASELINE_GIT_TIMEOUT_SECONDS,
        )

    if not _point_at_baseline():
        # Self-heal a desynced worktree admin entry: a directory removed
        # without `git worktree remove` leaves a registered-but-missing entry
        # that makes `git worktree add` fail forever ("missing but already
        # registered"), and a directory whose entry is gone fails `checkout`
        # symmetrically. Without this, every subsequent night buckets 100%
        # inconclusive — an inert classifier that looks safe. Prune, retry
        # once, and log the recovery so a persistent failure stays visible.
        log("baseline worktree re-point failed — running `git worktree prune` and retrying once")
        _run_provision_step(
            ["git", "worktree", "prune"],
            cwd=repo_root,
            timeout=BASELINE_GIT_TIMEOUT_SECONDS,
        )
        if not _point_at_baseline():
            return False
        log("baseline worktree recovered after `git worktree prune`")

    marker = worktree_path / BASELINE_PROVISION_MARKER
    try:
        lock_digest = hashlib.sha256((worktree_path / "uv.lock").read_bytes()).hexdigest()
    except OSError as exc:
        log(f"WARNING: baseline worktree uv.lock unreadable: {exc}")
        return False

    venv_ok = (worktree_path / ".venv" / "bin" / "pytest").exists()
    try:
        synced_digest = marker.read_text().strip()
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError from a corrupt marker — this
        # optional cache read must never kill the run (it sits on the
        # pre-page path); a bad marker just costs one unnecessary re-sync.
        synced_digest = ""

    if venv_ok and synced_digest == lock_digest:
        return True

    log(f"Provisioning baseline worktree venv at {worktree_path} (uv sync) ...")
    if not _run_provision_step(
        ["uv", "sync"],
        cwd=worktree_path,
        timeout=BASELINE_UV_SYNC_TIMEOUT_SECONDS,
    ):
        return False

    try:
        marker.write_text(lock_digest + "\n")
    except OSError as exc:
        # Only costs an unnecessary re-sync next run; not a classification failure.
        log(f"WARNING: could not write baseline provision marker: {exc}")
    return True


def empty_classification() -> dict[str, list[str]]:
    """The three discrete buckets, all empty — the shape every caller sees."""
    return {"newly_broken": [], "pre_existing": [], "inconclusive": []}


def classify_against_baseline(
    node_ids: list[str],
    baseline_sha: str,
    *,
    repo_root: Path = PROJECT_DIR,
    worktree_path: Path = BASELINE_WORKTREE,
    wrapper: Path = PYTEST_CLEAN_SH,
    report_path: str = PYTEST_BASELINE_JSON_TMP,
) -> dict[str, list[str]]:
    """Bucket each node ID by whether it was already failing at ``baseline_sha``.

    Synchronous and in-process: it provisions the baseline worktree, re-runs
    exactly ``node_ids`` there through ``scripts/pytest-clean.sh``, and reads
    the JSON report. No subagent, no spawned session, no Task tool.

    ``baseline_sha`` is **the prior run's HEAD SHA** (``prev["head_commit"]``),
    never bare ``main`` and never described as "last-green": that key is
    written on every non-fatal run and nothing in the detector records
    greenness. The soundness argument is per-node and narrower — a
    *newly-confirmed* failure was by definition absent from the prior run's
    confirmed-failing set, so at that SHA the node was not failing. The SHA is
    interpolated as a literal argument, so no shell parameter default can
    silently resolve to ``main``.

    Buckets:

    - ``newly_broken`` — passed at ``baseline_sha`` and fails at HEAD.
    - ``pre_existing`` — failed at ``baseline_sha`` too.
    - ``inconclusive`` — **every** failure path: a missing SHA, worktree
      provisioning failure, collection error, timeout, an unparseable or
      missing report, a node absent from the report, or any raised exception.
      It never guesses and never falls back to running at ``PROJECT_DIR``.

    The four keyword-only parameters are the injection seam the non-stubbed
    fixture test drives without monkeypatching module globals:

    - ``repo_root`` — the checkout ``git worktree add`` runs from.
    - ``worktree_path`` — the provisioned baseline worktree to run pytest in.
    - ``wrapper`` — the ``pytest-clean.sh`` the run routes **through**; its
      ``.venv``, interpreter-pin and rootdir guards are load-bearing here.
    - ``report_path`` — the classifier's own JSON report target, defaulting to
      ``PYTEST_BASELINE_JSON_TMP``. It must never be
      ``PYTEST_SERIAL_JSON_TMP`` or ``PYTEST_JSON_TMP``.

    Their production defaults keep ``main()``'s call site a two-argument call.
    """
    result = empty_classification()
    ordered = sorted(set(node_ids))
    if not ordered:
        return result

    if not baseline_sha:
        log("WARNING: no baseline SHA — classifying every node inconclusive")
        result["inconclusive"] = ordered
        return result

    if not provision_baseline_worktree(
        baseline_sha, repo_root=repo_root, worktree_path=worktree_path
    ):
        log(
            f"WARNING: baseline worktree provisioning failed at {baseline_sha} — "
            "classifying every node inconclusive (no PROJECT_DIR fallback)"
        )
        result["inconclusive"] = ordered
        return result

    # Pre-filter node IDs whose test FILE does not exist at the baseline
    # checkout — the newly-ADDED failing test, the single most common shape of
    # a newly-confirmed failure. Passing such a node to pytest makes the whole
    # invocation exit as a usage error and would poison the entire batch to
    # `inconclusive`, silently suppressing the newly_broken/pre_existing
    # evidence this tier exists to collect. Only the absent nodes go
    # `inconclusive`; the rest still classify.
    present: list[str] = []
    for node in ordered:
        rel_file = node.split("::", 1)[0]
        if (worktree_path / rel_file).exists():
            present.append(node)
        else:
            result["inconclusive"].append(node)
    if len(present) < len(ordered):
        log(
            f"{len(ordered) - len(present)} node(s) have no test file at baseline "
            f"{baseline_sha} — bucketed inconclusive without a baseline run: "
            f"{','.join(n for n in ordered if n not in present)}"
        )
    if not present:
        return result

    log(f"Classifying {len(present)} node ID(s) against baseline {baseline_sha} ...")
    try:
        Path(report_path).unlink(missing_ok=True)
        argv = [
            str(wrapper),
            *present,
            "-n0",
            "--tb=no",
            "-q",
            "--json-report",
            f"--json-report-file={report_path}",
        ]
        # Verbatim from run_tests() and reconfirm_serial(): the test-DB claim
        # happens in tests/conftest.py::pytest_configure, before collection and
        # therefore before any per-item timer is armed, so the interactive 30s
        # default would abort the whole session under contention and bucket
        # every node inconclusive.
        env = {**os.environ, "TEST_DB_CLAIM_WAIT_S": "300"}
        rc = _spawn_pytest(
            argv, env=env, timeout=PYTEST_BASELINE_TIMEOUT_SECONDS, cwd=worktree_path
        )
        log(f"baseline classification exit code: {rc}")
        report = json.loads(Path(report_path).read_text())
    except Exception as exc:
        log(f"WARNING: baseline classification run failed ({exc}) — every node inconclusive")
        result["inconclusive"] = ordered
        return result

    outcomes = {t.get("nodeid"): t.get("outcome") for t in report.get("tests", [])}
    for node in present:
        outcome = outcomes.get(node)
        if outcome == "passed":
            result["newly_broken"].append(node)
        elif outcome in ("failed", "error"):
            result["pre_existing"].append(node)
        else:
            # Absent from the baseline report (never collected, filtered, or
            # the run died early) is inconclusive — never assumed-passed.
            result["inconclusive"].append(node)
    return result


@dataclass(frozen=True)
class GateCaps:
    """The volume ceilings the decision gate enforces."""

    max_failures: int = NIGHTLY_FIX_MAX_FAILURES_DEFAULT


@dataclass(frozen=True)
class RunFlags:
    """The run-shape and data facts the decision gate refuses to act against."""

    is_seed_run: bool = False
    integrity_warnings: list[str] | None = None
    dry_run: bool = False
    baseline_sha: str = ""


def classify_precondition_reason(
    new_failures: list[str], caps: GateCaps, run_flags: RunFlags
) -> str | None:
    """Return the first failing classification precondition, or ``None``.

    These are the last five of the seven ``CLASSIFY_PRECONDITIONS`` (the first
    two — mode is not ``off`` and ``new_failures`` is non-empty — are the
    caller's business). They are evaluated at the call site **before**
    :func:`classify_against_baseline` runs, so a refused night does no git and
    no pytest work, and they are the first five clauses of
    :func:`gate_reason` in the same order, so the ``reason=`` token is
    identical whichever side reports it.

    The ``seed_run`` clause is unreachable from today's only production call
    site (the shadow block lives in the ``elif new_failures:`` arm of
    ``if is_seed_run:``, so ``is_seed_run`` is provably ``False`` there). It
    is retained as defense-in-depth: it is cheap, it keeps this function and
    :func:`gate_reason` sharing one clause list, and it means a future caller
    outside that ``elif`` cannot regress into gating a re-baseline night.
    """
    if run_flags.is_seed_run:
        return "seed_run"
    if run_flags.integrity_warnings:
        return "integrity_warnings"
    if run_flags.dry_run:
        return "dry_run"
    if not run_flags.baseline_sha:
        return "no_baseline_sha"
    if len(new_failures) > caps.max_failures:
        return "over_max_failures"
    return None


def gate_reason(
    classification: dict[str, list[str]],
    new_failures: list[str],
    caps: GateCaps,
    run_flags: RunFlags,
) -> str:
    """Return the first failing gate condition's token, or ``"none"``.

    The clause order below IS the ``reason=`` token vocabulary, and it is
    short-circuited in exactly this order: ``seed_run``,
    ``integrity_warnings``, ``dry_run``, ``no_baseline_sha``,
    ``over_max_failures``, ``pre_existing``, ``inconclusive``,
    ``not_all_newly_broken``, and ``none`` when every clause holds.

    There is deliberately **no ``MAX_DISPATCH_NODES`` clause**: that constant
    truncates the triage-filing set, not ``new_failures``, so folding it in
    would make ``NIGHTLY_FIX_MAX_FAILURES`` dead config and disqualify this
    feature's own 11-failure motivating case.
    """
    precondition = classify_precondition_reason(new_failures, caps, run_flags)
    if precondition is not None:
        return precondition
    if classification.get("pre_existing"):
        return "pre_existing"
    if classification.get("inconclusive"):
        return "inconclusive"
    if set(new_failures) != set(classification.get("newly_broken") or []):
        return "not_all_newly_broken"
    return "none"


def decide_fix_or_escalate(
    classification: dict[str, list[str]],
    new_failures: list[str],
    caps: GateCaps,
    run_flags: RunFlags,
) -> str:
    """Return ``"autonomous-fix"`` iff every gate condition holds, else ``"escalate"``.

    Pure: no I/O, no subprocess, no state. The conditions and their order are
    documented on :func:`gate_reason`, which reports which one failed first; a
    verdict of ``"escalate"`` always has a matching ``reason=`` token.

    It consumes ``new_failures`` (``compute_new_failures``) — the population
    this feature is about — never ``compute_dispatch_set``'s output, which
    answers the different question of what has never been filed.

    In this tier the verdict is computed and logged only. ``"autonomous-fix"``
    means "the gate would have attempted a fix"; it triggers nothing (#3076).
    """
    return (
        "autonomous-fix"
        if gate_reason(classification, new_failures, caps, run_flags) == "none"
        else "escalate"
    )


_FIX_MODE_WARNED = False


def resolve_fix_mode(raw: str | None = None) -> str:
    """Normalize ``NIGHTLY_FIX_MODE`` to ``"off"`` or ``"shadow"``.

    Reads ``os.environ`` at call time (default ``NIGHTLY_FIX_MODE_DEFAULT``)
    so a value set by ``load_env_or_die()`` inside ``main()`` — the only way
    the vault ``.env`` reaches the launchd-run nightly — is honored. An
    unrecognized value is treated as ``"off"`` — failing toward the detector's
    pre-feature behavior — and warned about once per process.
    """
    global _FIX_MODE_WARNED
    if raw is None:
        raw = os.environ.get("NIGHTLY_FIX_MODE", NIGHTLY_FIX_MODE_DEFAULT)
    value = raw.strip().lower()
    if value in ("off", "shadow"):
        return value
    if not _FIX_MODE_WARNED:
        _FIX_MODE_WARNED = True
        log(f"WARNING: unrecognized NIGHTLY_FIX_MODE={value!r} — treating as 'off'")
    return "off"


def resolve_fix_max_failures() -> int:
    """Read ``NIGHTLY_FIX_MAX_FAILURES`` from the environment at call time.

    Same call-time rule as :func:`resolve_fix_mode`. A malformed value
    degrades to ``NIGHTLY_FIX_MAX_FAILURES_DEFAULT`` with a warning rather
    than raising — a bad knob must never take down the nightly.
    """
    raw = os.environ.get("NIGHTLY_FIX_MAX_FAILURES", "")
    if not raw:
        return NIGHTLY_FIX_MAX_FAILURES_DEFAULT
    try:
        return int(raw)
    except ValueError:
        log(
            f"WARNING: malformed NIGHTLY_FIX_MAX_FAILURES={raw!r} — "
            f"using default {NIGHTLY_FIX_MAX_FAILURES_DEFAULT}"
        )
        return NIGHTLY_FIX_MAX_FAILURES_DEFAULT


def log_shadow_verdict(
    new_failures: list[str],
    caps: GateCaps,
    run_flags: RunFlags,
    *,
    classify=None,
) -> None:
    """Compute and log the shadow verdict for a non-``off`` run. Changes nothing else.

    Emits the byte-stable verdict line on **every** call, including the nights
    a precondition skipped classification (whose ``reason=`` names that
    precondition), plus the sibling bucket line only when classification
    actually ran — the verdict line alone answers "would the gate have fired?"
    but not "would it have been right?".

    ``classify`` is the keyword-only injection seam threading straight through
    to :func:`classify_against_baseline` (the default, resolved at call time),
    so verdict-path tests never need to patch the module attribute.
    """
    skip_reason = classify_precondition_reason(new_failures, caps, run_flags)
    if skip_reason is not None:
        verdict, reason = "escalate", skip_reason
    else:
        classify_fn = classify if classify is not None else classify_against_baseline
        classification = classify_fn(new_failures, run_flags.baseline_sha)
        reason = gate_reason(classification, new_failures, caps, run_flags)
        verdict = "autonomous-fix" if reason == "none" else "escalate"
        not_newly_broken = sorted(
            set(classification["pre_existing"]) | set(classification["inconclusive"])
        )
        log(
            "nightly-fix shadow-buckets: "
            f"newly_broken={len(classification['newly_broken'])} "
            f"pre_existing={len(classification['pre_existing'])} "
            f"inconclusive={len(classification['inconclusive'])} "
            f"not_newly_broken={','.join(not_newly_broken)}"
        )
    log(f"nightly-fix shadow-verdict: {verdict} reason={reason} nodes={len(new_failures)}")


def _fatal(reason: str) -> int:
    """Record a run-level failure in the log and return the exit code for ``main()``.

    Every arm that cannot produce a trustworthy result (a timeout, a corrupt
    report, an env-load refusal, an untrusted serial pass) routes through here.
    No arm reaches ``save_last_run()`` — a failed run must never overwrite a
    good baseline.

    This used to send a Telegram. It no longer notifies anything: the tracker is
    this script's only output surface (#3134). That places a real obligation on
    the callers — an arm that both refuses to report *and* refuses to file has
    made the night invisible. The integrity-guard arm therefore does not route
    through here at all; it files its cascades first (see ``main()``).
    """
    log(f"FATAL: {reason}")
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
    per-node dispatched, because the umbrella remains its record.

    Be precise about what that costs, because the obvious summary overstates
    the safety. A seeded node that regresses is **alerted once, then never
    filed**. :func:`compute_new_failures` keys on ``failing_tests`` rather than
    dispatch state, so the night it re-fails does fire a Telegram alert — but
    from the following night it is in ``failing_tests`` and is no longer
    "new", so no further alert fires and no issue is ever opened. The durable
    record for a non-seeded node is a GitHub issue; for a seeded node it is a
    single line in a chat log, and the umbrella may long since be closed.

    This set also never retires entries, unlike ``dispatched_nodes``, which
    drops a node once it stops failing. A renamed or deleted test stays in
    ``seeded_nodes`` forever. Both are accepted deliberately: the alternative
    is a duplicate issue on every flap of a population that is known to flap
    (#2807/#2808/#2809). Repairing the seeded population is a separate lane,
    and closing that lane is what makes this suppression stop mattering.
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
        "been triaged before. For EACH node below, search ALL issues — open AND "
        "closed — for the EXACT title given. If an OPEN issue exists, comment on "
        "it with any new information. If a CLOSED issue exists, the close reason "
        "decides: closed as not-planned (duplicate/consolidated) — comment there "
        "pointing at the recurrence and do NOT open a new issue, the consolidation "
        "target is the live tracker; closed as completed (fixed) — the failure "
        "recurring after a fix is new information, open a new issue with EXACTLY "
        "the title and link the closed one. If no issue exists in any state, open "
        "a new issue with EXACTLY that title, describing the failure, its likely "
        "cause, and suggested next steps. Do NOT attempt an auto-hotfix — this is "
        "an investigation-and-file-an-issue task only.\n",
    ]
    for node, title in zip(dispatch_nodes, titles, strict=True):
        lines.append(f'- Title: "{title}"\n  Node: {node}')
    return "\n".join(lines)


_WORKER_RE = re.compile(r"\[(gw\d+)\]")
_DIGITS_RE = re.compile(r"\d+")


def _phase_text(test: dict, phase: str) -> str:
    """Concatenate the longrepr and crash message of one report phase."""
    entry = test.get(phase) or {}
    if not isinstance(entry, dict):
        return ""
    crash = entry.get("crash") or {}
    parts = [entry.get("longrepr") or "", crash.get("message") if isinstance(crash, dict) else ""]
    return "\n".join(str(p) for p in parts if p)


def setup_error_signature(test: dict) -> tuple[str, str] | None:
    """Return ``(worker_id, normalized_message)`` for a setup error, else ``None``.

    A cascade is specifically a **setup**-phase storm: one poisoned xdist
    worker whose autouse fixture raises for every test that lands on it after
    the poisoning. A node that failed in its own test body is a finding in its
    own right and is deliberately excluded, however many siblings share its
    message — collapsing those would hide genuinely distinct bugs behind one
    umbrella.

    The message is normalized so incidental variation does not split one
    cascade into many groups: only the first line is kept, whitespace is
    collapsed, and digit runs become ``#`` (worker numbers, ports, pids, temp
    directory suffixes). The worker id comes from the ``[gwN]`` prefix
    pytest-xdist writes into the phase longreprs; it is ``""`` for a serial
    run, which groups every node together — correct, since a serial run has
    exactly one worker.
    """
    if (test.get("setup") or {}).get("outcome") not in ("failed", "error"):
        return None

    message = _phase_text(test, "setup")
    if not message:
        return None

    worker = ""
    for phase in ("setup", "teardown", "call"):
        match = _WORKER_RE.search(_phase_text(test, phase))
        if match:
            worker = match.group(1)
            break

    normalized = _normalized_first_error_line(message)
    if not normalized:
        return None
    return worker, normalized


def _normalized_first_error_line(message: str) -> str:
    """First substantive line of a failure message, normalized for grouping.

    Shared by the setup-cascade and body-failure signatures so the two cannot
    drift in what counts as "the same error". Skips xdist's ``[gwN]`` banner
    line (which would otherwise BE the signature for every node), strips
    pytest's ``E `` error-line marker, collapses whitespace, and replaces digit
    runs with ``#`` (ports, pids, worker numbers, temp-dir suffixes).
    """
    first_line = ""
    for line in message.splitlines():
        stripped = line.strip()
        if not stripped or _WORKER_RE.search(stripped):
            continue
        first_line = stripped
        break
    if not first_line:
        return ""
    if first_line.startswith("E ") or first_line == "E":
        first_line = first_line[1:].strip()
    return _DIGITS_RE.sub("#", " ".join(first_line.split()))


def body_failure_signature(test: dict) -> str | None:
    """Normalized first error line for a test that failed in its own BODY, else ``None``.

    The complement of :func:`setup_error_signature`: setup must have passed and
    the ``call`` phase must have failed, so the two signatures partition a
    failure set rather than double-counting it.

    Unlike a setup cascade, a body-failure group is keyed on the message alone,
    never the worker: one broken shared dependency (the 2026-08-24 case was a
    ``TypeError`` at argument binding inside the LLM wrapper) fails its callers
    on every worker at once, and a per-worker key would split one cause into
    ten groups all below threshold.
    """
    if (test.get("setup") or {}).get("outcome") != "passed":
        return None
    if (test.get("call") or {}).get("outcome") not in ("failed", "error"):
        return None
    message = _phase_text(test, "call")
    if not message:
        return None
    return _normalized_first_error_line(message) or None


# Failure text that names a NETWORK operation failing — DNS, TLS, connection
# refused/reset, connect-phase timeouts — is environmental (#3075 defect 3,
# carried from #2932): the code did not regress, the world around the run did.
# Deliberately narrow: a bare TimeoutError / asyncio.TimeoutError is NOT here,
# because unit-test timeouts are routinely genuine code regressions and
# classifying them environmental would silence exactly the failures the
# detector exists to catch. Matching is substring-on-normalized-text, ordered
# by specificity none of which overlaps.
_ENVIRONMENTAL_MARKERS = (
    "socket.gaierror",
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "ssl.sslerror",
    "sslcertverificationerror",
    "certificate_verify_failed",
    "ssl handshake",
    "connectionrefusederror",
    "connection refused",
    "connectionreseterror",
    "connection reset by peer",
    "connecttimeout",
    "httpx.connecterror",
    "requests.exceptions.connectionerror",
)


def is_environmental_failure(test: dict) -> bool:
    """True when the failure text names a network-layer fault, not a code path.

    Checked across every phase, lowercased. A node classified environmental is
    logged and excluded from filing — no issue, no umbrella — and deliberately
    NOT recorded as dispatched, so it re-evaluates every night and starts
    filing again the moment its failure text stops looking environmental.
    """
    text = "\n".join(_phase_text(test, phase) for phase in ("setup", "call", "teardown")).lower()
    return any(marker in text for marker in _ENVIRONMENTAL_MARKERS)


def cascade_title(message: str) -> str:
    """The byte-stable umbrella title for one normalized cascade message.

    Keyed on the message alone — never the worker id or the node count, both of
    which shift from night to night and from machine to machine. A title that
    moves is a title the open-issue dedup cannot match, which is how the same
    defect gets filed twice (#3131).
    """
    digest = hashlib.sha256(message.encode()).hexdigest()[:8]
    return f"Nightly regression cascade [{digest}]: {message[:80]}"


_BODY_CASCADE_KEY_PREFIX = "body::"


def body_cascade_title(message: str) -> str:
    """The byte-stable umbrella title for one normalized body-failure group.

    A separate namespace from :func:`cascade_title` so a body-failure group and
    a setup cascade that happen to share a normalized message can never collide
    on one title — they are different defects by construction (setup poisoning
    vs. shared-dependency breakage) and merging their recurrence threads would
    hide one behind the other.
    """
    digest = hashlib.sha256((_BODY_CASCADE_KEY_PREFIX + message).encode()).hexdigest()[:8]
    return f"Nightly regression group [{digest}]: {message[:80]}"


def cascade_state_key(cascade: dict) -> str:
    """The ``cascade_issues`` persistence key for one cascade dict."""
    if cascade.get("kind") == "body":
        return _BODY_CASCADE_KEY_PREFIX + cascade["message"]
    return cascade["message"]


def title_for_state_key(key: str) -> str:
    """Recompute the umbrella title a persisted ``cascade_issues`` key filed under."""
    if key.startswith(_BODY_CASCADE_KEY_PREFIX):
        return body_cascade_title(key[len(_BODY_CASCADE_KEY_PREFIX) :])
    return cascade_title(key)


def group_body_failure_cascades(
    report: dict,
    node_ids: list[str],
    *,
    min_group_size: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Split ``node_ids`` into same-root-cause body-failure groups and singles.

    The 39-for-2 fix (#3075): nodes whose test bodies fail with an identical
    normalized first error line are one root cause once the group is large.
    Returns ``(groups, singles)`` in the same ``{"message", "title", "workers",
    "nodes", "kind"}`` shape as :func:`group_setup_error_cascades`, so the two
    share one filing path.

    **False-merge risk, stated plainly:** two genuinely distinct defects that
    raise the same exception with byte-identical normalized first lines will
    merge into one umbrella. That is accepted as the smaller harm — the
    triage session reads the node list and can split the issue, whereas 39
    separate issues drowned the two real causes entirely. The grouping rule is
    exact equality of the normalized line, nothing fuzzier, precisely to keep
    that risk small: the 2026-08-24 second cause (worker-key assertions inside
    a TypeError batch) had a different first line and stays separate under it.
    """
    if min_group_size is None:
        min_group_size = resolve_int_knob(
            "NIGHTLY_BODY_CASCADE_MIN_GROUP_SIZE", BODY_CASCADE_MIN_GROUP_SIZE_DEFAULT
        )

    tests_by_id = {t.get("nodeid"): t for t in report.get("tests", []) if t.get("nodeid")}

    by_message: dict[str, list[str]] = {}
    for node in node_ids:
        signature = body_failure_signature(tests_by_id.get(node) or {})
        if signature is None:
            continue
        by_message.setdefault(signature, []).append(node)

    groups = []
    for message in sorted(by_message):
        nodes = by_message[message]
        if len(nodes) < min_group_size:
            continue
        workers = set()
        for node in nodes:
            match = _WORKER_RE.search(_phase_text(tests_by_id.get(node) or {}, "call"))
            if match:
                workers.add(match.group(1))
        groups.append(
            {
                "message": message,
                "title": body_cascade_title(message),
                "workers": sorted(workers),
                "nodes": sorted(nodes),
                "kind": "body",
            }
        )

    grouped = {n for g in groups for n in g["nodes"]}
    return groups, [n for n in node_ids if n not in grouped]


def group_setup_error_cascades(
    report: dict,
    node_ids: list[str],
    *,
    min_group_size: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Split ``node_ids`` into cascade groups and ungrouped singles.

    Returns ``(cascades, singles)``. Each cascade is
    ``{"message", "title", "workers", "nodes"}`` and stands for exactly ONE
    issue; ``singles`` keeps the per-node path it always had.

    Detection groups by ``(worker, message)`` — a poisoned worker is a
    per-worker phenomenon, and keying on the message alone would let three
    unrelated one-off setup errors that happen to share a message on three
    different workers masquerade as a cascade. **Filing** then merges the
    qualifying groups by message, so a night that poisons four workers
    identically produces one umbrella rather than four issues racing for the
    same title.
    """
    if min_group_size is None:
        min_group_size = resolve_int_knob(
            "NIGHTLY_CASCADE_MIN_GROUP_SIZE", CASCADE_MIN_GROUP_SIZE_DEFAULT
        )

    tests_by_id = {t.get("nodeid"): t for t in report.get("tests", []) if t.get("nodeid")}

    by_worker_message: dict[tuple[str, str], list[str]] = {}
    signature_by_node: dict[str, tuple[str, str]] = {}
    for node in node_ids:
        signature = setup_error_signature(tests_by_id.get(node) or {})
        if signature is None:
            continue
        signature_by_node[node] = signature
        by_worker_message.setdefault(signature, []).append(node)

    cascade_messages = {
        message
        for (_worker, message), nodes in by_worker_message.items()
        if len(nodes) >= min_group_size
    }

    by_message: dict[str, dict] = {}
    for node in node_ids:
        signature = signature_by_node.get(node)
        if signature is None or signature[1] not in cascade_messages:
            continue
        worker, message = signature
        cascade = by_message.setdefault(
            message,
            {
                "message": message,
                "title": cascade_title(message),
                "workers": set(),
                "nodes": [],
                "kind": "setup",
            },
        )
        cascade["workers"].add(worker)
        cascade["nodes"].append(node)

    cascades = []
    for message in sorted(by_message):
        cascade = by_message[message]
        cascade["workers"] = sorted(w for w in cascade["workers"] if w)
        cascade["nodes"] = sorted(cascade["nodes"])
        cascades.append(cascade)

    grouped = {n for c in cascades for n in c["nodes"]}
    return cascades, [n for n in node_ids if n not in grouped]


def _build_cascade_prompt(cascade: dict) -> str:
    """Build the umbrella prompt for one cascade — ONE issue, node list collapsed."""
    nodes = cascade["nodes"]
    workers = ", ".join(cascade["workers"]) or "serial run"
    if cascade.get("kind") == "body":
        shape = (
            f"{len(nodes)} test node(s) whose test BODIES all failed with the same "
            "normalized error line — one shared root cause (a broken common "
            "dependency), not independent findings"
        )
        error_label = "Shared failure line (normalized)"
        cause_hint = "the shared root cause"
    else:
        shape = (
            f"{len(nodes)} test node(s) that all errored in fixture SETUP with the same "
            f"message, on xdist worker(s) {workers}"
        )
        error_label = "Shared setup error (normalized)"
        cause_hint = "the likely cause of the poisoning"
    return (
        "Nightly regression detector found a CASCADE: "
        f"{shape}. This is ONE defect, not "
        f"{len(nodes)}. Search ALL issues — open AND closed — for the EXACT title "
        "below. If an OPEN one exists, comment on it with the new occurrence. If a "
        "CLOSED one exists: closed as not-planned means comment there and do NOT "
        "re-file (its consolidation target is the live tracker); closed as "
        "completed means the recurrence is new information — open exactly ONE new "
        "issue with EXACTLY that title, linking the closed one. If none exists, "
        "open exactly ONE new issue with EXACTLY that title. Put the full node "
        "list inside a collapsed <details> section — the body must lead with the "
        f"shared error and {cause_hint}, not with the node list. Do NOT open "
        "per-node issues for any node below. Do NOT attempt an auto-hotfix — "
        "investigate and file only.\n\n"
        f'Title: "{cascade["title"]}"\n\n'
        f"{error_label}: {cascade['message']}\n\n"
        "Affected node IDs:\n" + "\n".join(f"- {n}" for n in nodes)
    )


def open_issues(
    *,
    limit: int = OPEN_ISSUE_LIST_LIMIT,
    timeout: int = OPEN_ISSUE_LIST_TIMEOUT_SECONDS,
) -> dict[str, int] | None:
    """Map ``title -> number`` for every open GitHub issue, or ``None`` if unreadable.

    This is the dedup the triage agent's own search-before-file instruction
    cannot provide. ``dispatched_nodes`` is per-machine state, so a second
    machine running the same nightly re-dispatches titles this machine already
    filed; and GitHub's search index lags issue creation by minutes, so even a
    single machine's retry can miss its own issue. Reading the REST list
    endpoint (``gh issue list``, not ``--search``) sees an issue the instant it
    exists.

    The **number** is what makes comment-over-create possible: knowing that a
    title is taken only lets a run stay silent, which is what loses the
    recurrence signal (#3134).

    ``None`` means "could not tell" and the caller **fails open** — dispatching
    a possible duplicate is a smaller harm than silently filing nothing on the
    night an unrelated ``gh`` hiccup coincides with a real regression.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,  # timeout-guard: allow
        )
    except Exception as exc:  # noqa: BLE001  # TimeoutExpired, FileNotFoundError, ...
        log(f"WARNING: could not list open issues for dedup ({exc})")
        return None

    if result.returncode != 0:
        log(
            f"WARNING: `gh issue list` exited {result.returncode} for dedup: "
            f"{(result.stderr or '').strip()}"
        )
        return None

    try:
        return {
            row["title"]: int(row["number"])
            for row in json.loads(result.stdout)
            if row.get("title") and row.get("number") is not None
        }
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: could not parse open issues for dedup ({exc})")
        return None


def closed_issue_dispositions(
    *,
    limit: int = CLOSED_ISSUE_LIST_LIMIT,
    timeout: int = OPEN_ISSUE_LIST_TIMEOUT_SECONDS,
) -> dict[str, tuple[int, str]] | None:
    """Map ``title -> (number, state_reason)`` for closed issues, or ``None``.

    The closed half of the dedup (#3075 defect 1). ``stateReason`` is what
    makes closed state usable at all — closed-as-duplicate and closed-as-fixed
    demand opposite responses:

    - ``NOT_PLANNED`` (duplicate, consolidated, won't-fix): the finding is
      already on record somewhere; re-filing it is the churn this exists to
      end. The caller comments on the closed issue instead.
    - ``COMPLETED`` (fixed): the node failing AGAIN after a fix is genuinely
      new information and deserves a fresh issue. The caller re-files.
    - Empty/unknown reason: treated like ``NOT_PLANNED`` — a comment preserves
      the recurrence signal either way, and a comment on the wrong side costs
      nothing where a duplicate issue costs tracker noise forever.

    ``None`` means "could not tell"; the caller fails open by filing, matching
    :func:`open_issues`.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "closed",
                "--limit",
                str(limit),
                "--json",
                "number,title,stateReason",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,  # timeout-guard: allow
        )
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: could not list closed issues for dedup ({exc})")
        return None

    if result.returncode != 0:
        log(
            f"WARNING: `gh issue list --state closed` exited {result.returncode} for dedup: "
            f"{(result.stderr or '').strip()}"
        )
        return None

    try:
        return {
            row["title"]: (int(row["number"]), str(row.get("stateReason") or ""))
            for row in json.loads(result.stdout)
            if row.get("title") and row.get("number") is not None
        }
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: could not parse closed issues for dedup ({exc})")
        return None


def partition_closed_matches(
    nodes: list[str], closed_map: dict[str, tuple[int, str]] | None
) -> tuple[list[str], list[tuple[str, int, str]]]:
    """Split unfiled candidates into ``(to_file, closed_matches)``.

    Runs AFTER :func:`partition_already_open`, on the nodes with no open issue.
    ``closed_matches`` holds ``(node, number, state_reason)`` for nodes whose
    exact title matches a closed issue that must NOT be re-filed
    (``NOT_PLANNED`` or unknown reason). A ``COMPLETED`` closure stays in
    ``to_file`` — a failure recurring after its fix is new information, the
    one legitimate re-file case.

    ``closed_map`` of ``None`` (unreadable) disables the suppression rather
    than guessing, same fail-open posture as the open partition.
    """
    if closed_map is None:
        return list(nodes), []
    to_file: list[str] = []
    closed_matches: list[tuple[str, int, str]] = []
    for node in nodes:
        entry = closed_map.get(f"Nightly regression: {node}")
        if entry is None or entry[1] == "COMPLETED":
            to_file.append(node)
        else:
            closed_matches.append((node, entry[0], entry[1] or "unknown"))
    return to_file, closed_matches


def closed_recurrence_comment(
    node: str, state_reason: str, *, run_at: str, head_commit: str | None
) -> str:
    """Recurrence comment for a node whose issue is CLOSED and not re-filed.

    Says explicitly why no new issue was opened, so a human reading the closed
    issue understands the detector saw the recurrence and where the live
    tracker is expected to be (a NOT_PLANNED closure normally points at its
    consolidation target in its own close comment).
    """
    return "\n".join(
        [
            *_recurrence_header(run_at, head_commit),
            f"- Node: `{node}`",
            "",
            f"This issue is closed ({state_reason}), so no duplicate was filed. If this "
            "closure consolidated into another issue, that issue is the live tracker for "
            "the recurrence; if this node should be re-filed on recurrence instead, close "
            "as completed rather than not-planned.",
        ]
    )


def comment_on_issue(number: int, body: str, *, dry_run: bool = False) -> bool:
    """Post ``body`` as a comment on issue ``number``. Returns success.

    The body goes in on **stdin** (``--body-file -``), never as an argv value: a
    cascade comment carries a collapsed list of every affected node, which for
    the motivating incident was 278 of them and well past a comfortable argument
    length.

    Failure is reported and returns ``False`` rather than raising. The caller
    must then leave the finding unrecorded so the next run retries it — a
    recurrence that could not be written down has not been reported, and
    recording it as filed would lose it permanently.
    """
    if dry_run:
        log(f"[DRY RUN] Would comment on issue #{number} ({len(body)} chars); nothing posted")
        return True

    try:
        result = subprocess.run(
            ["gh", "issue", "comment", str(number), "--body-file", "-"],
            cwd=PROJECT_DIR,
            input=body,
            capture_output=True,
            text=True,
            timeout=GH_COMMENT_TIMEOUT_SECONDS,  # timeout-guard: allow
        )
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: could not comment on issue #{number} ({exc})")
        return False

    if result.returncode != 0:
        log(
            f"WARNING: `gh issue comment` on #{number} exited {result.returncode}: "
            f"{(result.stderr or '').strip()}"
        )
        return False

    log(f"Commented recurrence on issue #{number}")
    return True


def _recurrence_header(run_at: str, head_commit: str | None) -> list[str]:
    """The two facts every recurrence comment leads with: when, and against what."""
    return [
        "Recurred on the nightly regression run.",
        "",
        f"- Run: `{run_at}`",
        f"- HEAD: `{head_commit or 'unknown'}`",
    ]


def cascade_recurrence_comment(cascade: dict, *, run_at: str, head_commit: str | None) -> str:
    """The comment body recording one more night of an already-filed cascade.

    Carries the blast radius (node and file counts, worker ids) because that is
    the part that moves between nights and the part that says whether the defect
    is getting worse. The node list itself is collapsed — an issue that accretes
    a comment per night must stay readable — and truncated, because the counts
    above it are the load-bearing part and a comment that exceeds GitHub's body
    limit posts nothing at all.
    """
    nodes = cascade["nodes"]
    files = sorted({n.split("::", 1)[0] for n in nodes})
    workers = ", ".join(cascade["workers"]) or "serial run"
    listed = nodes[:MAX_COMMENT_NODES_LISTED]
    lines = [
        *_recurrence_header(run_at, head_commit),
        f"- Blast radius: {len(nodes)} node(s) across {len(files)} file(s)",
        f"- xdist worker(s): {workers}",
        f"- Shared setup error: `{cascade['message']}`",
        "",
        "<details><summary>Affected node IDs</summary>",
        "",
        *[f"- `{n}`" for n in listed],
    ]
    if len(nodes) > len(listed):
        lines.append(f"- ...and {len(nodes) - len(listed)} more")
    lines += ["", "</details>"]
    return "\n".join(lines)


def node_recurrence_comment(node: str, *, run_at: str, head_commit: str | None) -> str:
    """The comment body recording one more night of an already-filed single node."""
    return "\n".join([*_recurrence_header(run_at, head_commit), f"- Node: `{node}`"])


def resolve_cascade_issue(
    cascade: dict,
    open_issue_map: dict[str, int] | None,
    prev_cascade_issues: dict[str, int | None],
) -> int | None:
    """The number of the open issue already representing this cascade, or ``None``.

    Lookup order, and the order matters:

    1. The number recorded against this cascade's **normalized signature** in
       ``cascade_issues``, if that issue is still open. The signature is stable
       across nights; the rendered title is not guaranteed to be, because a human
       can retitle an issue and a future version of this script can change
       :func:`cascade_title`. Keying recurrence on the title alone is how the
       same defect gets filed twice.
    2. The title match, which is the bootstrap. This script does not open issues
       itself — a triage session does — so the number is undiscoverable on the
       night of filing and can only be learned by matching the title on a later
       run. Once learned it is persisted and (1) takes over.

    ``None`` from an unreadable open-issue list means "could not tell", and the
    caller fails open by filing.
    """
    if open_issue_map is None:
        return None
    recorded = prev_cascade_issues.get(cascade_state_key(cascade))
    if isinstance(recorded, int) and recorded in set(open_issue_map.values()):
        return recorded
    return open_issue_map.get(cascade["title"])


def partition_already_open(
    nodes: list[str], open_issue_map: dict[str, int] | None
) -> tuple[list[str], list[tuple[str, int]]]:
    """Split per-node candidates into ``(to_file, already_open)``.

    ``already_open`` pairs each node with the number of its existing issue, so
    the caller can comment on it rather than merely stay quiet.

    Keyed on the same ``Nightly regression: {node}`` title
    :func:`_build_triage_prompt` emits, so the check and the filing contract
    cannot drift. A per-node finding has no signature to key on the way a
    cascade does — the node id *is* its identity, and it is already embedded in
    the title verbatim, so the title is a faithful key here in a way it is not
    for a cascade.

    ``open_issue_map`` of ``None`` (unreadable) disables the suppression
    entirely rather than guessing.
    """
    if open_issue_map is None:
        return list(nodes), []
    to_file: list[str] = []
    already_open: list[tuple[str, int]] = []
    for node in nodes:
        number = open_issue_map.get(f"Nightly regression: {node}")
        if number is None:
            to_file.append(node)
        else:
            already_open.append((node, number))
    return to_file, already_open


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


@dataclass
class DispatchOutcome:
    """What one pass of :func:`dispatch_findings` actually did.

    ``recorded`` is the node set the caller may mark as filed — it holds only
    nodes whose finding reached the tracker, either as a new issue's dispatch or
    as a comment that posted successfully. A node whose dispatch or comment
    failed is deliberately absent, so the next run retries it rather than
    suppressing it forever against a record that was never written.
    """

    recorded: list[str]
    cascade_issues: dict[str, int | None]
    session_id: str | None = None
    issues_filed: int = 0
    comments_posted: int = 0
    # Nodes classified environmental (network-layer failure text) and filed
    # nowhere. Deliberately never merged into ``recorded``: an environmental
    # node re-evaluates every night and files normally the moment its failure
    # stops looking environmental.
    environmental: list[str] = field(default_factory=list)


def carry_cascade_issues(
    prev_cascade_issues: dict[str, int | None],
    open_issue_map: dict[str, int] | None,
) -> dict[str, int | None]:
    """The ``cascade_issues`` map to start this run from.

    Prior entries are kept while their issue is demonstrably still open, and a
    ``None`` (pending — dispatched, number not yet known) entry is upgraded to a
    real number the moment its title appears in the open set. That upgrade is
    the whole reason the pending state exists: a triage session, not this
    script, opens the issue, so the number can only be learned later.

    A pending entry that cannot be resolved is dropped. It means either the
    dispatch never produced an issue, or the issue was filed and has since been
    closed — and in both cases the correct response to the cascade recurring is
    to file it again, not to stay silent against a record of nothing.

    An unreadable open-issue list (``None``) keeps the map verbatim: it is
    "could not tell", never evidence that anything closed.
    """
    if open_issue_map is None:
        return dict(prev_cascade_issues)

    open_numbers = set(open_issue_map.values())
    carried: dict[str, int | None] = {}
    for key, number in prev_cascade_issues.items():
        if number is None:
            resolved = open_issue_map.get(title_for_state_key(key))
            if resolved is not None:
                carried[key] = resolved
            continue
        if number in open_numbers:
            carried[key] = number
    return carried


def dispatch_findings(
    report: dict,
    dispatch_nodes: list[str],
    prev: dict,
    *,
    run_at: str,
    head_commit: str | None,
    dry_run: bool = False,
    cascades_only: bool = False,
) -> DispatchOutcome:
    """Turn this run's unfiled findings into at most a few issues and some comments.

    The single place that decides what reaches the tracker, shared by the
    ordinary path and the integrity-trip path so the two cannot drift in how
    they dedup. The order is deliberate:

    1. Collapse the blast radius **before** anything is filed: setup storms via
       :func:`group_setup_error_cascades` (278 errors once became 26 issues),
       then environmental exclusion (:func:`is_environmental_failure` — network
       faults file nothing), then same-root-cause body failures via
       :func:`group_body_failure_cascades` (39 issues once covered 2 causes).
    2. Read the open and closed issue sets once each, and only when there is
       something to file — a clean night must not shell out to ``gh`` at all.
    3. For every finding that already has an open issue, comment. For one whose
       issue is CLOSED as not-planned, comment there — never re-file
       (#3075 defect 1); a closure marked completed re-files, because failing
       again after a fix is new information. For the rest, spend the budget.

    ``cascades_only`` suppresses per-node filing entirely. The integrity-trip
    path passes it: on a night this script has just declared infrastructural,
    the individual nodes are collateral by definition and the cascade is the
    whole finding.

    Cascade umbrellas and per-node issues draw from ONE budget, so a run cannot
    exceed ``NIGHTLY_MAX_ISSUES_PER_RUN`` by splitting findings across the two
    shapes. Comments do not spend budget: the cap exists to bound how much
    *new* tracker surface one night creates, and a comment creates none.
    """
    cascades, single_nodes = group_setup_error_cascades(report, dispatch_nodes)

    tests_by_id = {t.get("nodeid"): t for t in report.get("tests", []) if t.get("nodeid")}
    environmental = [n for n in single_nodes if is_environmental_failure(tests_by_id.get(n) or {})]
    if environmental:
        env_set = set(environmental)
        single_nodes = [n for n in single_nodes if n not in env_set]
        log(
            f"{len(environmental)} node(s) classified ENVIRONMENTAL (network-layer "
            "failure text) — no issue filed, re-evaluated next run: " + ", ".join(environmental)
        )

    if not cascades_only:
        body_cascades, single_nodes = group_body_failure_cascades(report, single_nodes)
        cascades = cascades + body_cascades

    for cascade in cascades:
        shared = "one setup error" if cascade.get("kind") != "body" else "one failure line"
        log(
            f"Cascade collapsed: {len(cascade['nodes'])} node(s) on worker(s) "
            f"{','.join(cascade['workers']) or 'serial'} share {shared} — "
            f"one finding, not {len(cascade['nodes'])}: {cascade['title']}"
        )

    open_issue_map = open_issues() if dispatch_nodes else None
    if dispatch_nodes and open_issue_map is None:
        log("Dedup disabled for this run (open issues unreadable) — failing open")
    closed_issue_map = closed_issue_dispositions() if dispatch_nodes else None
    if dispatch_nodes and closed_issue_map is None:
        log("Closed-state dedup disabled for this run (closed issues unreadable) — failing open")

    outcome = DispatchOutcome(
        recorded=[],
        cascade_issues=carry_cascade_issues(prev.get("cascade_issues") or {}, open_issue_map),
        environmental=environmental,
    )

    max_issues = resolve_int_knob("NIGHTLY_MAX_ISSUES_PER_RUN", MAX_ISSUES_PER_RUN_DEFAULT)
    issue_budget = max_issues
    deferred_cascades: list[dict] = []

    for cascade in cascades:
        existing = resolve_cascade_issue(cascade, open_issue_map, outcome.cascade_issues)
        if existing is not None:
            posted = comment_on_issue(
                existing,
                cascade_recurrence_comment(cascade, run_at=run_at, head_commit=head_commit),
                dry_run=dry_run,
            )
            if posted:
                outcome.comments_posted += 1
                outcome.recorded.extend(cascade["nodes"])
                outcome.cascade_issues[cascade_state_key(cascade)] = existing
            continue
        closed_entry = (closed_issue_map or {}).get(cascade["title"])
        if closed_entry is not None and closed_entry[1] != "COMPLETED":
            # Closed as not-planned (duplicate/consolidated): the umbrella is
            # already on record; re-filing it is the churn. Comment the
            # recurrence there and deliberately record NO cascade_issues entry
            # — the issue is closed, and a COMPLETED closure would have fallen
            # through to a legitimate re-file instead.
            number, reason = closed_entry
            body = (
                cascade_recurrence_comment(cascade, run_at=run_at, head_commit=head_commit)
                + f"\n\nThis issue is closed ({reason or 'unknown'}), so no duplicate was "
                "filed. If this closure consolidated into another issue, that issue is the "
                "live tracker; close as completed instead if recurrence should re-file."
            )
            if comment_on_issue(number, body, dry_run=dry_run):
                outcome.comments_posted += 1
                outcome.recorded.extend(cascade["nodes"])
            continue
        if issue_budget <= 0:
            deferred_cascades.append(cascade)
            continue
        session_id = maybe_dispatch_triage_session(
            [f"cascade:{cascade['title']}"],
            prompt=_build_cascade_prompt(cascade),
            slug_suffix=hashlib.sha256(cascade_state_key(cascade).encode()).hexdigest()[:8],
            dry_run=dry_run,
        )
        if session_id is not None:
            issue_budget -= 1
            outcome.issues_filed += 1
            outcome.recorded.extend(cascade["nodes"])
            outcome.session_id = session_id
            # Pending: the triage session opens the issue, so its number is not
            # knowable here. carry_cascade_issues() upgrades this on a later run.
            outcome.cascade_issues[cascade_state_key(cascade)] = None

    if deferred_cascades:
        log(
            f"Issue budget reached: deferring {len(deferred_cascades)} cascade umbrella(s) "
            "to a later run: " + ", ".join(c["title"] for c in deferred_cascades)
        )

    if cascades_only:
        if single_nodes:
            log(
                f"Per-node filing suppressed for {len(single_nodes)} node(s): this run was "
                "classified as infrastructure, so only the cascade finding is filed"
            )
        return outcome

    single_nodes, already_open = partition_already_open(single_nodes, open_issue_map)
    for node, number in already_open:
        if comment_on_issue(
            number,
            node_recurrence_comment(node, run_at=run_at, head_commit=head_commit),
            dry_run=dry_run,
        ):
            outcome.comments_posted += 1
            outcome.recorded.append(node)
    if already_open:
        log(
            f"{len(already_open)} node(s) already have an open issue — commented the "
            "recurrence instead of filing: " + ", ".join(n for n, _ in already_open)
        )

    single_nodes, closed_matches = partition_closed_matches(single_nodes, closed_issue_map)
    for node, number, reason in closed_matches:
        if comment_on_issue(
            number,
            closed_recurrence_comment(node, reason, run_at=run_at, head_commit=head_commit),
            dry_run=dry_run,
        ):
            outcome.comments_posted += 1
            outcome.recorded.append(node)
    if closed_matches:
        log(
            f"{len(closed_matches)} node(s) have a CLOSED not-planned issue — commented "
            "the recurrence there instead of re-filing (#3075): "
            + ", ".join(n for n, _, _ in closed_matches)
        )

    if len(single_nodes) > issue_budget:
        log(
            f"Issue budget reached: {len(single_nodes)} per-node issue(s) wanted but only "
            f"{issue_budget} of MAX_ISSUES_PER_RUN={max_issues} left "
            f"({outcome.issues_filed} spent on cascade umbrella(s)); "
            f"deferring {len(single_nodes) - issue_budget} node(s) to a later run: "
            + ", ".join(single_nodes[issue_budget:])
        )
        single_nodes = single_nodes[:issue_budget]

    session_id = maybe_dispatch_triage_session(single_nodes, dry_run=dry_run)
    if session_id is not None:
        outcome.issues_filed += len(single_nodes)
        outcome.recorded.extend(single_nodes)
        outcome.session_id = session_id

    return outcome


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


def _handle_integrity_trip(
    reason: str,
    report: dict | None,
    current: dict | None,
    prev: dict,
    *,
    dry_run: bool = False,
) -> int:
    """File the finding a tripped integrity guard is sitting on, then fail the run.

    A trip says the *measurement* cannot be trusted, not that nothing happened.
    The 2026-09-03 storm is the proof: 278 setup errors from one poisoned xdist
    worker, which is both an untrustworthy run and a genuine defect. Before this
    script had a way to say that in one issue, it said it in 26. Now the guard
    trips — and the old behavior, alert-and-drop-everything, would say it
    nowhere at all, since nothing alerts any more (#3134).

    So the trip still reaches the tracker, under three restrictions:

    - **Cascades only.** On a night classified as infrastructure the individual
      nodes are collateral by definition; the shared defect is the finding.
    - **Comment-over-create**, exactly as on an ordinary night, so a storm that
      recurs nightly accretes comments on one issue.
    - **No baseline write.** Only ``dispatched_nodes`` and ``cascade_issues``
      are persisted, merged onto the prior state. The totals and the
      confirmed-failing set are precisely the numbers just declared
      untrustworthy, and overwriting a good baseline with them is what
      ``_fatal()``'s no-state-write invariant has always existed to prevent.

    Returns 1: the run did fail, whatever it managed to file.
    """
    log(f"FATAL: {reason}")

    failing = extract_failing_node_ids(report or {})
    dispatch_nodes = compute_dispatch_set(prev, failing)
    if not (report and dispatch_nodes):
        log("Integrity trip has no unfiled findings to record — nothing filed, no state written")
        return 1

    outcome = dispatch_findings(
        report,
        dispatch_nodes,
        prev,
        run_at=(current or {}).get("run_at") or datetime.now(UTC).isoformat(),
        head_commit=_get_head_commit(),
        dry_run=dry_run,
        cascades_only=True,
    )
    log(
        f"Integrity trip recorded: {outcome.issues_filed} issue(s) filed, "
        f"{outcome.comments_posted} recurrence comment(s) posted"
    )

    if dry_run:
        log(f"[DRY RUN] Would merge dispatch records into {LAST_RUN_FILE} (not written)")
        return 1
    if not outcome.recorded and outcome.cascade_issues == (prev.get("cascade_issues") or {}):
        return 1

    # Merge onto prev rather than replacing it: everything except what was just
    # filed must survive this run untouched.
    state = dict(prev)
    state["dispatched_nodes"] = sorted(prior_dispatched(prev) | set(outcome.recorded))
    state["cascade_issues"] = outcome.cascade_issues
    save_last_run(state)
    log(f"Dispatch records merged into {LAST_RUN_FILE} (baseline left untouched)")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly regression test runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview: file nothing, comment nothing, write nothing",
    )
    args = parser.parse_args()

    log("=== Nightly regression test run starting ===")

    # Load the .env vault into os.environ before any subprocess spawns, and fail
    # loudly if it cannot be read (issue #2327). This is the FIRST substantive
    # step: the pytest and `gh` children inherit these vars. This runs BEFORE
    # the run lock: an env refusal must never be confused with a lock collision
    # (which correctly returns 0 silently), and neither path writes state.
    _applied, env_err = load_env_or_die()
    if env_err:
        return _fatal(env_err)

    # Acquire the run lock first, before any other work -- a concurrent
    # nightly run holding the lock means this invocation is a collision and
    # must exit cleanly without running tests or touching the tracker.
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
    # through _fatal() the same way the pre-#2823 timeout arm did.
    try:
        raw_report, current, rc = run_tests()
    except subprocess.TimeoutExpired:
        return _fatal(f"pytest timed out after {PYTEST_TIMEOUT_SECONDS}s (process group killed)")
    reason, integrity_warnings = validate_run_integrity(raw_report, rc, prev)
    if reason:
        return _handle_integrity_trip(reason, raw_report, current, prev, dry_run=args.dry_run)
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
            "serial re-confirmation returned no result for every node — the run did not happen"
        )
    if parallel_failing:
        log(
            f"Re-confirmation: {len(confirmed_failing)} confirmed, "
            f"{len(artifacts)} xdist artifact(s)"
        )
        if artifacts:
            log("xdist-parallelism artifacts (passed serially, not filed): " + ", ".join(artifacts))
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
            # lost silently. Refusing to save state instead means the next run
            # sees no prior state, re-seeds, and retries the dispatch. This
            # mirrors _fatal()'s existing invariant that no untrusted run
            # reaches save_last_run().
            if triage_session_id is None:
                return _fatal(
                    f"seed triage dispatch failed — no umbrella issue exists for "
                    f"{seed_size} absorbed node(s); refusing to write a baseline "
                    "so the next run retries the seed"
                )
            current["dispatched_session_id"] = triage_session_id
            just_dispatched = list(confirmed_failing)
            current["seed_collection"] = COLLECTION_PATHS
            current["seed_size"] = len(confirmed_failing)
            current["seeded_nodes"] = list(confirmed_failing)
            current["min_expected_collected"] = current["total"]
    else:
        dispatch_nodes = compute_dispatch_set(prev, confirmed_failing)
        if dispatch_nodes:
            log(f"Not yet triaged: {len(dispatch_nodes)} node(s): " + ", ".join(dispatch_nodes))
        already_filed = len(confirmed_failing) - len(dispatch_nodes)
        if already_filed > 0:
            log(f"Triage dispatch suppressed for {already_filed} already-filed node(s)")

        outcome = dispatch_findings(
            raw_report,
            dispatch_nodes,
            prev,
            run_at=current["run_at"],
            head_commit=current["head_commit"],
            dry_run=args.dry_run,
        )
        just_dispatched = outcome.recorded
        current["cascade_issues"] = outcome.cascade_issues
        if outcome.session_id is not None:
            triage_session_id = outcome.session_id
            current["dispatched_session_id"] = outcome.session_id
        log(
            f"Tracker: {outcome.issues_filed} issue(s) filed, "
            f"{outcome.comments_posted} recurrence comment(s) posted"
        )

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

    # Carry the cascade signature -> issue map across a seed night too. The seed
    # path files an umbrella of its own and never touches the map, so without
    # this a re-baseline would forget every cascade issue this machine knows
    # about and re-file each one on its next occurrence.
    current.setdefault("cascade_issues", prev.get("cascade_issues") or {})

    # The shadow tier (issue #2334) classifies newly-confirmed failures against
    # the prior run's HEAD and logs the verdict the eventual active tier (#3076)
    # would act on. It used to sit before the human page and is now the last
    # thing the run does with the failure set, since there is no page. Non-fatal
    # by construction: an exception here must not change the run's outcome.
    if new_failures and not is_seed_run and resolve_fix_mode() != "off":
        try:
            log_shadow_verdict(
                new_failures,
                GateCaps(max_failures=resolve_fix_max_failures()),
                RunFlags(
                    is_seed_run=is_seed_run,
                    integrity_warnings=list(integrity_warnings),
                    dry_run=args.dry_run,
                    baseline_sha=prev.get("head_commit") or "",
                ),
            )
        except Exception as exc:
            log(f"nightly-fix shadow tier error (non-fatal): {exc}")

    # Nothing is notified anywhere: the tracker is this script's only output
    # surface (#3134). What the run found is already either an issue, a comment
    # on one, or a logged deferral; these lines just make the shape of the night
    # legible in the log.
    if is_seed_run:
        seed_note = " (re-baseline: prior population absorbed)" if is_reseed else ""
        log(
            f"Baseline established{seed_note}: {current['total']} tests, "
            f"{current['failed']} confirmed failures"
        )
    elif new_failures:
        log(f"{len(new_failures)} newly-confirmed failure(s), {current['failed']} confirmed total")
    elif new_errors > 0:
        log(f"Collection error ({new_errors} errors)")
    else:
        log("Clean run (no newly-confirmed failures)")

    # Save state — never under --dry-run.
    #
    # The dry-run dispatch short-circuit returns a truthy sentinel so the
    # caller's success path is exercised realistically. That makes persisting
    # actively dangerous: on a seed night the success path writes
    # `seeded_nodes`, and because that set is sticky, compute_dispatch_set()
    # would suppress the whole absorbed population permanently against an
    # umbrella issue that was never filed. That is precisely the failure the
    # failed-seed `_fatal()` branch above exists to prevent, reachable through
    # a command whose entire purpose is to change nothing.
    if args.dry_run:
        log(f"[DRY RUN] Would save state to {LAST_RUN_FILE} (not written)")
    else:
        save_last_run(current)
        log(f"State saved to {LAST_RUN_FILE}")

    # Post-run TTFT gate (issue #1227). A TTFT regression is not a test failure,
    # so the return code is unchanged. It used to page; it now only logs, since
    # nothing this script produces notifies anyone (#3134). The gate is kept
    # rather than deleted: the measurement and its evidence still have value,
    # and re-notifying is a one-line change if a cold-start regression ever
    # needs to reach someone.
    try:
        ttft_alert = run_ttft_gate(
            log_file=TTFT_LOG_FILE,
            session_type=TTFT_SESSION_TYPE,
            last=TTFT_LAST_N,
            threshold=TTFT_THRESHOLD_SECONDS,
        )
        if ttft_alert:
            log(f"TTFT regression: {ttft_alert}")
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
