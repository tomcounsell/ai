#!/usr/bin/env python3
"""Nightly regression test runner.

Runs pytest tests/unit/ -n auto with JSON report, compares against prior run,
and sends a Telegram alert only when new failures appear. Clean runs are silent.

Also runs the granite real-loop integration test as a second isolated invocation
so the nightly harness catches both unit regressions and granite loop failures.

Usage:
    python scripts/nightly_regression_tests.py             # Run tests, send Telegram on regression
    python scripts/nightly_regression_tests.py --dry-run   # Preview without sending Telegram
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LAST_RUN_INTEGRATION_FILE = DATA_DIR / "nightly_tests_integration_last_run.json"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Eng: Valor"
TELEGRAM_BIN = PROJECT_DIR / ".venv" / "bin" / "valor-telegram"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
PYTEST_JSON_INTEGRATION_TMP = "/tmp/nightly_granite_realloop_report.json"

PYTEST_TIMEOUT_SECONDS = 1800  # 30 minutes max
PYTEST_INTEGRATION_TIMEOUT_SECONDS = 600  # 10 minutes for the granite loop test

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


def run_tests() -> dict:
    """Run pytest tests/unit/ with --json-report. Returns summary dict."""
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
        "run_at": datetime.now(UTC).isoformat(),
    }


def run_integration_tests() -> dict | None:
    """Run the granite real-loop integration test with --json-report.

    Returns a summary dict on success, or None if the subprocess crashed or
    the report could not be parsed. Failures/errors are surfaced as Telegram
    alerts by the caller. This is a SEPARATE invocation from the unit suite —
    it never clobbers PYTEST_JSON_TMP.
    """
    log("Starting granite real-loop integration test (isolated invocation) ...")
    integration_target = "tests/integration/test_granite_container_loop.py"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                integration_target,
                "-v",
                "--tb=short",
                "--json-report",
                f"--json-report-file={PYTEST_JSON_INTEGRATION_TMP}",
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=PYTEST_INTEGRATION_TIMEOUT_SECONDS,
        )
        log(f"integration pytest exit code: {result.returncode}")
    except subprocess.TimeoutExpired:
        log(
            f"ERROR: integration pytest timed out after {PYTEST_INTEGRATION_TIMEOUT_SECONDS}s "
            f"— granite real-loop test not completed"
        )
        return None
    except Exception as exc:
        log(f"ERROR: integration pytest subprocess failed: {exc}")
        return None

    # Parse JSON report — distinct from unit suite report
    try:
        report = json.loads(Path(PYTEST_JSON_INTEGRATION_TMP).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"ERROR: Failed to parse integration JSON report: {exc}")
        return None

    summary = report.get("summary", {})
    return {
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "error": summary.get("error", 0),
        "skipped": summary.get("skipped", 0),
        "total": summary.get("total", 0),
        "run_at": datetime.now(UTC).isoformat(),
    }


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


def _process_integration_results(
    integration: dict | None,
    dry_run: bool,
) -> None:
    """Process granite real-loop integration results and send alerts.

    Mirrors the unit suite delta/alert flow but with a separate state key.
    Skip-visibility: when NIGHTLY_MODEL_EXPECTED env var is truthy and the
    report parsed successfully with skipped > 0, an alert is raised (the
    granite real-loop test should not skip on a model-reachable machine).
    """
    if integration is None:
        # subprocess crashed or timed out — surface as a hard alert
        send_telegram(
            "Nightly granite real-loop test: subprocess crashed or timed out. "
            "Check logs/nightly_tests.log for details.",
            dry_run=dry_run,
        )
        return

    log(
        f"Integration results: passed={integration['passed']}, "
        f"failed={integration['failed']}, error={integration['error']}, "
        f"skipped={integration['skipped']}, total={integration['total']}"
    )

    # Load prior state for the integration suite (separate key from unit suite).
    prev_integration = load_last_run(LAST_RUN_INTEGRATION_FILE)
    is_first_integration_run = not prev_integration

    if is_first_integration_run:
        msg = (
            f"Nightly granite real-loop baseline established: "
            f"{integration['total']} tests, {integration['failed']} failures, "
            f"{integration['skipped']} skipped."
        )
        send_telegram(msg, dry_run=dry_run)
    elif integration["failed"] > 0 or integration["error"] > 0:
        msg = (
            f"Nightly granite real-loop regression: "
            f"{integration['failed']} failed, {integration['error']} errors. "
            f"Run: pytest tests/integration/test_granite_container_loop.py -v"
        )
        send_telegram(msg, dry_run=dry_run)
    else:
        log("Granite real-loop: clean run — no alert sent")

    # Skip-visibility gate (issue #1740): when NIGHTLY_MODEL_EXPECTED is set,
    # the nightly harness runs on a machine where the model IS reachable. Skipped
    # tests in that context mean the skip condition fired erroneously — alert.
    # Only evaluate when the report was actually parsed (integration is not None).
    nightly_model_expected = bool(os.environ.get("NIGHTLY_MODEL_EXPECTED", "").strip())
    if nightly_model_expected and integration["skipped"] > 0:
        send_telegram(
            f"Nightly granite real-loop: {integration['skipped']} test(s) skipped "
            f"on a model-reachable machine (NIGHTLY_MODEL_EXPECTED=1). "
            f"The skip condition may be firing incorrectly — investigate.",
            dry_run=dry_run,
        )

    # Save integration state (separate from unit suite).
    save_last_run(integration, LAST_RUN_INTEGRATION_FILE)
    log(f"Integration state saved to {LAST_RUN_INTEGRATION_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly regression test runner")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending Telegram")
    args = parser.parse_args()

    log("=== Nightly regression test run starting ===")

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

    log(
        f"Results: passed={current['passed']}, failed={current['failed']}, "
        f"error={current['error']}, total={current['total']}"
    )

    # Compute delta
    delta = current["failed"] - prev.get("failed", 0)
    new_errors = current.get("error", 0)
    log(f"Delta: {delta:+d} new failures, {new_errors} collection errors")

    # Alert logic
    if is_first_run:
        msg = (
            f"Nightly regression baseline established: "
            f"{current['total']} tests, {current['failed']} failures."
        )
        send_telegram(msg, dry_run=args.dry_run)
    elif delta > 0:
        msg = (
            f"Nightly regression: +{delta} new failures "
            f"({current['failed']} total). Run: pytest tests/unit/ -n auto"
        )
        send_telegram(msg, dry_run=args.dry_run)
    elif new_errors > 0:
        msg = (
            f"Nightly tests: collection error ({new_errors} errors). "
            f"Run: pytest tests/unit/ -n auto"
        )
        send_telegram(msg, dry_run=args.dry_run)
    else:
        log("Clean run — no Telegram alert sent")

    # Save state
    save_last_run(current)
    log(f"State saved to {LAST_RUN_FILE}")

    # Run granite real-loop integration test (second, isolated invocation).
    # Failures and skips (on model-reachable machines) are surfaced as Telegram alerts.
    log("--- Granite real-loop integration run ---")
    try:
        integration = run_integration_tests()
        _process_integration_results(integration, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        log(f"Integration run hook error (non-fatal): {exc}")

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
