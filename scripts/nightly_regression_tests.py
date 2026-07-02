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

from config.models import PINNED_CLAUDE_VERSION

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "nightly_tests_last_run.json"
LAST_RUN_INTEGRATION_FILE = DATA_DIR / "nightly_tests_integration_last_run.json"
LAST_RUN_OLLAMA_FILE = DATA_DIR / "nightly_tests_ollama_last_run.json"
LOG_FILE = PROJECT_DIR / "logs" / "nightly_tests.log"
TELEGRAM_CHAT = "Eng: Valor"
TELEGRAM_BIN = PROJECT_DIR / ".venv" / "bin" / "valor-telegram"
PYTEST_JSON_TMP = "/tmp/nightly_pytest_report.json"
PYTEST_JSON_INTEGRATION_TMP = "/tmp/nightly_granite_realloop_report.json"
PYTEST_JSON_OLLAMA_TMP = "/tmp/nightly_granite_ollama_report.json"

PYTEST_TIMEOUT_SECONDS = 1800  # 30 minutes max
PYTEST_INTEGRATION_TIMEOUT_SECONDS = 600  # 10 minutes for the granite loop test
# The ollama-backed Substrate B session reasons on a local model (minutes per
# turn) plus a reachability probe — give it a generous ceiling.
PYTEST_OLLAMA_TIMEOUT_SECONDS = 900  # 15 minutes for the ollama-backed E2E

# Version-pinned canary for the ollama-backed granite suite (plan Task 6 /
# Success Criterion 4). Substrate B launches the REAL ``claude`` binary — a new
# release is the exact thing that silently breaks production granite, so the
# nightly run pins the version it validated against and alerts on drift. The pin
# (imported at module top) is the SINGLE SOURCE OF TRUTH in config/models.py,
# shared with the D1a update-time drift check (scripts/update/verify.py); bump
# it there. Canary provisioning against a not-yet-fleet version: issue #1854.

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


def ollama_reachable_for_nightly() -> bool:
    """Reachability probe for the ollama-backed suite (monkeypatchable seam).

    Delegates to the shared ``ollama_substrate_reachable`` (mirrors the
    ``_model_reachable`` pattern). Defined as a module-level function so the
    self-skip path can be unit-tested by monkeypatching this name to ``False``
    (plan Task 6 / verification row: "monkeypatches the reachability probe").
    All exceptions are swallowed to ``False`` — an import or probe failure must
    self-skip, never crash the nightly run.
    """
    try:
        from tests.granite_faults.ollama_env import ollama_substrate_reachable

        return ollama_substrate_reachable()
    except Exception as exc:  # noqa: BLE001
        log(f"Ollama reachability probe errored (treating as unreachable): {exc}")
        return False


def get_claude_version() -> str | None:
    """Return the ``claude`` binary version string, or None if unavailable."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"claude --version errored: {exc}")
        return None
    if result.returncode != 0:
        return None
    # Output shape: "2.1.197 (Claude Code)" — take the leading token.
    return (result.stdout or "").strip().split()[0] if result.stdout.strip() else None


def claude_canary_alert() -> str | None:
    """Return a drift alert when the live ``claude`` version != the pinned one.

    A version bump under the ollama-backed canary means the exact binary that
    breaks production granite has shipped — surface it as a regression alert so
    the harness gets re-validated against the new release. Returns None when the
    version matches or cannot be determined (a missing binary is handled by the
    reachability skip, not here).
    """
    live = get_claude_version()
    if live is None or live == PINNED_CLAUDE_VERSION:
        return None
    return (
        f"claude version drift (granite ollama canary): pinned "
        f"{PINNED_CLAUDE_VERSION}, live {live}. A new claude release can break "
        f"the granite PTY idle heuristic — re-validate the harness and bump "
        f"PINNED_CLAUDE_VERSION in config/models.py."
    )


def run_ollama_suite(dry_run: bool = False) -> None:
    """Run the ollama-backed granite Substrate B suite (self-skipping canary).

    Self-skips with a LOGGED reason (no subprocess spawned) when ollama/the
    model is unreachable — Substrate B must never hard-fail the nightly run on
    a machine without a served model. When reachable, it fires the
    version-pinned claude canary and runs the E2E as an isolated subprocess
    (``GRANITE_OLLAMA_SMOKE=1``), then surfaces failures as Telegram alerts.
    """
    # Per-suite expected-machine gate (issue #1841, mirrors #1740's
    # NIGHTLY_MODEL_EXPECTED pattern). This is intentionally a SEPARATE var from
    # NIGHTLY_MODEL_EXPECTED: PR #1840 pinned the ollama backend to qwen-only
    # tags with no fallback, so ollama reachability is now decoupled from
    # anthropic-model reachability — a bridge machine can have
    # NIGHTLY_MODEL_EXPECTED=1 (anthropic model reachable) while having no qwen
    # tag at all. Reusing NIGHTLY_MODEL_EXPECTED here would alert-storm every
    # bridge machine every night. Set NIGHTLY_OLLAMA_EXPECTED=1 ONLY on the one
    # machine designated to actually run the ollama canary (do NOT add it to
    # the shared com.valor.nightly-tests.plist).
    ollama_expected = bool(os.environ.get("NIGHTLY_OLLAMA_EXPECTED", "").strip())

    if not ollama_reachable_for_nightly():
        log(
            "Ollama-backed granite suite skipped: ollama/model unreachable "
            "(self-skip, no subprocess spawned)."
        )
        if ollama_expected:
            send_telegram(
                "Nightly granite ollama suite: did not run — ollama/model unreachable "
                "on the expected canary machine (NIGHTLY_OLLAMA_EXPECTED=1). Check "
                "ollama status and the pinned qwen tag.",
                dry_run=dry_run,
            )
        return

    # Version-pinned canary: alert on drift, but still run the suite.
    drift = claude_canary_alert()
    if drift:
        log(f"Canary: {drift}")
        send_telegram(drift, dry_run=dry_run)

    log("Starting ollama-backed granite Substrate B suite (isolated invocation) ...")
    env = {**os.environ, "GRANITE_OLLAMA_SMOKE": "1"}
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_granite_ollama_e2e.py",
                "-v",
                "--tb=short",
                "--json-report",
                f"--json-report-file={PYTEST_JSON_OLLAMA_TMP}",
            ],
            cwd=PROJECT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=PYTEST_OLLAMA_TIMEOUT_SECONDS,
        )
        log(f"ollama suite pytest exit code: {result.returncode}")
    except subprocess.TimeoutExpired:
        log(f"ERROR: ollama suite timed out after {PYTEST_OLLAMA_TIMEOUT_SECONDS}s")
        send_telegram(
            "Nightly granite ollama suite: timed out. Check logs/nightly_tests.log.",
            dry_run=dry_run,
        )
        return
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: ollama suite subprocess failed: {exc}")
        if ollama_expected:
            send_telegram(
                f"Nightly granite ollama suite: subprocess failed on the expected "
                f"canary machine (NIGHTLY_OLLAMA_EXPECTED=1): {exc}",
                dry_run=dry_run,
            )
        return

    try:
        report = json.loads(Path(PYTEST_JSON_OLLAMA_TMP).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"ERROR: Failed to parse ollama suite JSON report: {exc}")
        if ollama_expected:
            send_telegram(
                f"Nightly granite ollama suite: JSON report unparseable on the "
                f"expected canary machine (NIGHTLY_OLLAMA_EXPECTED=1): {exc}",
                dry_run=dry_run,
            )
        return

    summary = report.get("summary", {})
    current = {
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "error": summary.get("error", 0),
        "skipped": summary.get("skipped", 0),
        "total": summary.get("total", 0),
        "run_at": datetime.now(UTC).isoformat(),
    }
    log(
        f"Ollama suite results: passed={current['passed']}, failed={current['failed']}, "
        f"error={current['error']}, skipped={current['skipped']}, total={current['total']}"
    )

    prev = load_last_run(LAST_RUN_OLLAMA_FILE)
    if not prev:
        send_telegram(
            f"Nightly granite ollama baseline established: {current['total']} tests, "
            f"{current['failed']} failures, {current['skipped']} skipped.",
            dry_run=dry_run,
        )
    elif current["failed"] > 0 or current["error"] > 0:
        send_telegram(
            f"Nightly granite ollama regression: {current['failed']} failed, "
            f"{current['error']} errors. Run: "
            f"GRANITE_OLLAMA_SMOKE=1 pytest tests/integration/test_granite_ollama_e2e.py -v",
            dry_run=dry_run,
        )
    else:
        log("Granite ollama suite: clean run — no alert sent")

    save_last_run(current, LAST_RUN_OLLAMA_FILE)
    log(f"Ollama suite state saved to {LAST_RUN_OLLAMA_FILE}")


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

    # Run the ollama-backed granite Substrate B suite (self-skipping canary).
    # Unreachable ollama self-skips with a logged reason; never crashes the run.
    log("--- Granite ollama-backed Substrate B run ---")
    try:
        run_ollama_suite(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        log(f"Ollama suite hook error (non-fatal): {exc}")

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
