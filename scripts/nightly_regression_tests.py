#!/usr/bin/env python3
"""Nightly regression test runner.

Runs pytest tests/unit/ -n auto with JSON report, compares against prior run,
and sends a Telegram alert only when new failures appear. Clean runs are silent.

Usage:
    python scripts/nightly_regression_tests.py             # Run tests, send Telegram on regression
    python scripts/nightly_regression_tests.py --dry-run   # Preview without sending Telegram
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Dev: Valor"
TELEGRAM_BIN = PROJECT_DIR / ".venv" / "bin" / "valor-telegram"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"

PYTEST_TIMEOUT_SECONDS = 1800  # 30 minutes max


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


def load_last_run() -> dict:
    """Load previous run state. Returns empty dict on missing/corrupt file (signals first run)."""
    if LAST_RUN_FILE.exists():
        try:
            return json.loads(LAST_RUN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}  # Empty dict = first run


def save_last_run(state: dict) -> None:
    """Atomically persist current run state to LAST_RUN_FILE."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps(state, indent=2) + "\n")


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

    # Run tests
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
    log("=== Nightly regression test run complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
