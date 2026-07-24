#!/usr/bin/env python3
"""Nightly regression test runner.

Runs pytest tests/unit/ -n auto with JSON report, compares against prior run,
and sends a Telegram alert only when new failures appear. Clean runs are silent.

Serial re-confirmation gate (issue #2180)
-----------------------------------------
`-n auto` is pytest-xdist parallel execution. The classic xdist failure mode —
tests that pass serially but collide under parallel workers on shared state
(Redis keys, temp files, fixture ordering) — produces a *shifting set* of
failures a count-based detector cannot distinguish from a real regression.

To disambiguate, after the parallel run we re-run **only the failing node IDs**
serially (`-n0`). Tests that fail in parallel but pass serially are classified as
xdist-parallelism *artifacts*; tests that fail in both are *confirmed*
regressions. The state file persists the confirmed failing **set** (not a scalar
count), so a regression alert fires only for *newly-confirmed* serial failures.
Artifacts are logged but never alerted, killing the parallel-execution alert
noise. The serial re-run targets only the already-failing node IDs, so it stays
fast and never re-runs the whole suite.

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
import subprocess
import sys
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
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
PYTEST_SERIAL_JSON_TMP = "/tmp/nightly_pytest_serial_report.json"

PYTEST_TIMEOUT_SECONDS = 1800  # 30 minutes max
# Serial re-confirmation only re-runs the already-failing node IDs, so it is far
# cheaper than the full parallel run. Grain-of-salt: provisional, env-tunable if
# the confirmed failing set ever grows large enough to matter.
PYTEST_RECONFIRM_TIMEOUT_SECONDS = 900  # 15 minutes max

# TTFT regression gate (issue #1227).
# Plan target: production 90s, nightly CI 120s (allowing slack for run-to-run noise).
TTFT_LOG_FILE = PROJECT_DIR / "logs" / "cold_start_metrics.jsonl"
TTFT_SESSION_TYPE = "pm"
TTFT_LAST_N = 10
TTFT_THRESHOLD_SECONDS = 120.0


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

    Mirrors the sidecar-lock-file idiom in ``scripts/pr_shape_cache.py``:
    open/create the lock file, then ``flock(LOCK_EX | LOCK_NB)``.

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


def run_tests() -> dict:
    """Run pytest tests/unit/ -n auto with --json-report.

    Returns a summary dict including ``failing_parallel`` — the list of node IDs
    that failed under parallel execution — which the caller re-confirms serially.
    """
    log("Starting pytest tests/unit/ -n auto --json-report ...")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/",
                "-n",
                "auto",
                "--tb=no",
                "-q",
                "--json-report",
                f"--json-report-file={PYTEST_JSON_TMP}",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_SECONDS,
        )
        log(f"pytest exit code: {result.returncode}")
    except subprocess.TimeoutExpired:
        log(f"ERROR: pytest timed out after {PYTEST_TIMEOUT_SECONDS}s")
        raise

    # Parse JSON report
    try:
        report = json.loads(Path(PYTEST_JSON_TMP).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"ERROR: Failed to parse JSON report: {exc}")
        raise

    summary = report.get("summary", {})
    return {
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "error": summary.get("error", 0),
        "skipped": summary.get("skipped", 0),
        "total": summary.get("total", 0),
        "failing_parallel": extract_failing_node_ids(report),
        "run_at": datetime.now(UTC).isoformat(),
    }


def reconfirm_serial(node_ids: list[str]) -> tuple[list[str], list[str]]:
    """Re-run the given node IDs serially (`-n0`) to disambiguate xdist noise.

    Returns ``(confirmed, artifacts)``:
      - ``confirmed`` — node IDs that failed again serially (real regressions).
      - ``artifacts`` — node IDs that passed serially (xdist-parallelism
        collisions on shared state).

    Fail-safe: if the serial re-run cannot be executed or parsed, every input
    node ID is treated as *confirmed* so a genuine regression is never silently
    hidden behind an infrastructure hiccup.
    """
    if not node_ids:
        return [], []

    ordered = sorted(set(node_ids))
    log(f"Serial re-confirmation of {len(ordered)} failing node ID(s) with -n0 ...")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *ordered,
                "-n0",
                "--tb=no",
                "-q",
                "--json-report",
                f"--json-report-file={PYTEST_SERIAL_JSON_TMP}",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=PYTEST_RECONFIRM_TIMEOUT_SECONDS,
        )
        log(f"serial re-confirmation exit code: {result.returncode}")
        report = json.loads(Path(PYTEST_SERIAL_JSON_TMP).read_text())
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        log(f"WARNING: serial re-confirmation failed ({exc}); treating all as confirmed")
        return ordered, []

    serial_failing = set(extract_failing_node_ids(report))
    confirmed = [n for n in ordered if n in serial_failing]
    artifacts = [n for n in ordered if n not in serial_failing]
    return confirmed, artifacts


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
            timeout=30,
        )
        log(f"Telegram sent: {msg}")
    except Exception as exc:
        log(f"WARNING: Failed to send Telegram: {exc}")


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
        timeout=60,
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


def compute_new_failures(prev: dict, confirmed_failing: list[str]) -> list[str]:
    """Node IDs newly confirmed as failing vs. the prior run's confirmed set.

    Set-based so a *shifting* flaky set (same count, different tests) does not
    read as a regression, and a genuinely new failure does — even when the total
    count is flat.
    """
    prev_failing = set(prev.get("failing_tests", []))
    return sorted(n for n in confirmed_failing if n not in prev_failing)


class FailureSummary(BaseModel):
    summary: str


def _raw_failure_preview(confirmed_failing: list[str]) -> str:
    """Build the raw node-ID preview text (first 5 + '+N more')."""
    preview = ", ".join(confirmed_failing[:5])
    if len(confirmed_failing) > 5:
        preview += f", +{len(confirmed_failing) - 5} more"
    return preview


def summarize_failures(confirmed_failing: list[str], report: dict) -> str:
    """Summarize newly-confirmed failures via a cheap LLM call, best-effort.

    Groups failing node IDs by file and pulls short tracebacks from the
    pytest ``--json-report`` payload when available. On ANY failure (empty
    input short-circuits before the LLM call; network error, schema
    validation failure, timeout, etc. are all caught) falls back to the raw
    node-ID preview format that ``main()`` used to build inline.
    """
    if not confirmed_failing:
        return _raw_failure_preview(confirmed_failing)

    by_file: dict[str, list[str]] = {}
    for nodeid in confirmed_failing:
        file_part = nodeid.split("::", 1)[0]
        by_file.setdefault(file_part, []).append(nodeid)

    tests_by_id = {t.get("nodeid"): t for t in report.get("tests", []) if t.get("nodeid")}

    lines = ["Newly-confirmed nightly test failures, grouped by file:"]
    for file_part, node_ids in sorted(by_file.items()):
        lines.append(f"\n{file_part}:")
        for nodeid in node_ids:
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
        return _raw_failure_preview(confirmed_failing)


def maybe_dispatch_triage_session(
    confirmed_failing: list[str], prev: dict
) -> tuple[str | None, str | None]:
    """Dispatch a triage Eng session for newly-confirmed failures, deduped by hash.

    Returns ``(session_id, current_hash)``:
      - ``session_id`` — the dispatched session ID, or ``None`` if no dispatch
        happened (either because there were no new failures, the failing set is
        unchanged since the last dispatch, or the dispatch subprocess itself
        failed).
      - ``current_hash`` — the sha256 of the sorted, deduped confirmed-failing
        node-ID set, computed once here and returned so callers never need to
        recompute it. ``None`` when ``confirmed_failing`` is empty (no hash to
        compute).

    The dedup key is ``prev["dispatched_hash"]``: the sha256 of the sorted,
    deduped confirmed-failing node-ID set as of the last dispatch. The
    caller is responsible for persisting the new hash (and session ID) into
    the state dict it saves via ``save_last_run`` so the *next* run's
    ``prev`` observes it — and for leaving ``dispatched_hash`` untouched on
    clean runs (no new_failures) or on a failed dispatch, so dedup state
    isn't lost and a retry is possible on the next run.
    """
    if not confirmed_failing:
        return None, None

    current_hash = hashlib.sha256(",".join(sorted(set(confirmed_failing))).encode()).hexdigest()
    if current_hash == prev.get("dispatched_hash"):
        log("Triage dispatch skipped — confirmed-failing set unchanged since last dispatch")
        return None, current_hash

    slug = f"nightly-triage-{current_hash[:8]}"
    prompt = (
        "Nightly regression detector found newly-confirmed test failures. "
        "Investigate the root cause of the following failing tests and file "
        "a /do-issue-quality GitHub issue describing the failure, its likely "
        "cause, and suggested next steps. Do NOT attempt an auto-hotfix — "
        "this is an investigation-and-file-an-issue task only.\n\n"
        "Newly-confirmed failing node IDs:\n" + "\n".join(f"- {n}" for n in confirmed_failing)
    )

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
                prompt,
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001  # covers TimeoutExpired, FileNotFoundError, etc.
        log(f"WARNING: triage session dispatch failed ({exc})")
        return None, current_hash

    try:
        session_id = json.loads(result.stdout)["session_id"]
    except Exception:  # noqa: BLE001
        log(f"WARNING: could not parse session_id from dispatch stdout: {result.stdout!r}")
        session_id = None

    log(f"Triage session dispatched: slug={slug} session_id={session_id}")
    return session_id, current_hash


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly regression test runner")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending Telegram")
    args = parser.parse_args()

    log("=== Nightly regression test run starting ===")

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

    # Run unit tests
    try:
        current = run_tests()
    except subprocess.TimeoutExpired:
        log("FATAL: pytest timeout — not saving state, exiting 1")
        return 1
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"FATAL: Could not parse test results: {exc}")
        return 1

    parallel_failing = current.get("failing_parallel", [])
    log(
        f"Results (parallel -n auto): passed={current['passed']}, "
        f"failed={current['failed']}, error={current['error']}, total={current['total']}"
    )

    # Serial re-confirmation gate (issue #2180): re-run only the failing node IDs
    # serially to separate real regressions from xdist-parallelism artifacts.
    confirmed_failing, artifacts = reconfirm_serial(parallel_failing)
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

    # Carry forward the triage-dispatch dedup state by default; only the
    # newly-confirmed-failures branch below overwrites it (a dispatch was
    # actually attempted). Clean/baseline/collection-error runs must not
    # lose the previous dispatch's dedup hash.
    current["dispatched_hash"] = prev.get("dispatched_hash")
    current["dispatched_session_id"] = prev.get("dispatched_session_id")

    new_failures = compute_new_failures(prev, confirmed_failing)
    new_errors = current.get("error", 0)
    log(
        f"Newly-confirmed failures: {len(new_failures)}; "
        f"confirmed total: {current['failed']}; collection errors: {new_errors}"
    )

    # Alert logic — regression fires only on newly-confirmed serial failures.
    if is_first_run:
        msg = (
            f"Nightly regression baseline established: "
            f"{current['total']} tests, {current['failed']} confirmed failures."
        )
        send_telegram(msg, dry_run=args.dry_run)
    elif new_failures:
        try:
            serial_report = json.loads(Path(PYTEST_SERIAL_JSON_TMP).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            serial_report = {}
        summary_text = summarize_failures(new_failures, serial_report)
        triage_session_id, dispatch_hash = maybe_dispatch_triage_session(confirmed_failing, prev)
        if triage_session_id is not None:
            # Only record the new dedup hash (and session ID) on a successful
            # dispatch. If dispatch failed, `dispatched_hash` stays whatever
            # was carried forward from `prev` above, so the next nightly run
            # gets a retry instead of the failure being silently swallowed.
            current["dispatched_hash"] = dispatch_hash
            current["dispatched_session_id"] = triage_session_id
        msg = (
            f"Nightly regression: {len(new_failures)} newly-confirmed failure(s) "
            f"({current['failed']} confirmed total): {summary_text}. "
            f"Run: pytest tests/unit/ -n0"
        )
        if triage_session_id:
            msg += f" [triage session: {triage_session_id}]"
        send_telegram(msg, dry_run=args.dry_run)
    elif new_errors > 0:
        msg = (
            f"Nightly tests: collection error ({new_errors} errors). "
            f"Run: pytest tests/unit/ -n auto"
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


if __name__ == "__main__":
    sys.exit(main())
