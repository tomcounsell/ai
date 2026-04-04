"""Execute generated Rodney happy path scripts and collect results.

Runs each .sh script in the target directory, collects exit codes
(0=pass, 1=fail, 2=error), captures screenshots as evidence, and
produces a JSON summary report.

Usage:
    python tools/happy_path_runner.py tests/happy-paths/scripts/
    python tools/happy_path_runner.py tests/happy-paths/scripts/ --evidence-dir /tmp/evidence
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Exit code meanings for Rodney/script execution
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


@dataclass
class ScriptResult:
    """Result of executing a single happy path script."""

    script: str
    status: str  # "pass", "fail", "error"
    exit_code: int
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    screenshot: str | None = None


@dataclass
class RunSummary:
    """Summary of all happy path script executions."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    duration_seconds: float = 0.0
    results: list[ScriptResult] = field(default_factory=list)


def check_rodney_installed() -> bool:
    """Check if rodney is available on PATH."""
    return shutil.which("rodney") is not None


def run_script(
    script_path: Path,
    evidence_dir: Path | None = None,
    timeout: int = 120,
) -> ScriptResult:
    """Execute a single happy path shell script.

    Args:
        script_path: Path to the .sh script.
        evidence_dir: Directory to store failure screenshots.
        timeout: Maximum execution time in seconds.

    Returns:
        ScriptResult with execution details.
    """
    script_name = script_path.stem
    start = time.monotonic()

    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=script_path.parent,
        )
        duration = time.monotonic() - start
        exit_code = result.returncode

        if exit_code == EXIT_PASS:
            status = "pass"
        elif exit_code == EXIT_FAIL:
            status = "fail"
        else:
            status = "error"

        script_result = ScriptResult(
            script=script_name,
            status=status,
            exit_code=exit_code,
            duration_seconds=round(duration, 2),
            stdout=result.stdout[-2000:] if result.stdout else "",
            stderr=result.stderr[-2000:] if result.stderr else "",
        )

        # Capture failure screenshot if evidence dir is provided
        if status != "pass" and evidence_dir:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = evidence_dir / f"{script_name}-failure.png"
            try:
                subprocess.run(
                    ["rodney", "screenshot", str(screenshot_path)],
                    capture_output=True,
                    timeout=30,
                )
                if screenshot_path.exists():
                    script_result.screenshot = str(screenshot_path)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        script_result = ScriptResult(
            script=script_name,
            status="error",
            exit_code=EXIT_ERROR,
            duration_seconds=round(duration, 2),
            stderr=f"Script timed out after {timeout}s",
        )
    except OSError as e:
        duration = time.monotonic() - start
        script_result = ScriptResult(
            script=script_name,
            status="error",
            exit_code=EXIT_ERROR,
            duration_seconds=round(duration, 2),
            stderr=str(e),
        )

    return script_result


def run_all(
    scripts_dir: Path,
    evidence_dir: Path | None = None,
    timeout: int = 120,
) -> RunSummary:
    """Execute all happy path scripts in a directory.

    Args:
        scripts_dir: Directory containing .sh scripts.
        evidence_dir: Directory for failure evidence screenshots.
        timeout: Per-script timeout in seconds.

    Returns:
        RunSummary with aggregated results.
    """
    if evidence_dir is None:
        evidence_dir = scripts_dir.parent / "evidence"

    scripts = sorted(scripts_dir.glob("*.sh"))
    if not scripts:
        logger.warning("No happy path scripts found in %s", scripts_dir)
        return RunSummary()

    summary = RunSummary(total=len(scripts))
    start = time.monotonic()

    for script_path in scripts:
        logger.info("Running: %s", script_path.name)
        result = run_script(script_path, evidence_dir, timeout)
        summary.results.append(result)

        if result.status == "pass":
            summary.passed += 1
            logger.info("  PASS (%0.1fs)", result.duration_seconds)
        elif result.status == "fail":
            summary.failed += 1
            logger.warning("  FAIL (%0.1fs): %s", result.duration_seconds, result.stderr[:200])
        else:
            summary.errored += 1
            logger.error("  ERROR (%0.1fs): %s", result.duration_seconds, result.stderr[:200])

    summary.duration_seconds = round(time.monotonic() - start, 2)
    return summary


def format_summary_table(summary: RunSummary) -> str:
    """Format run summary as a markdown table.

    Args:
        summary: RunSummary from run_all().

    Returns:
        Markdown-formatted results table.
    """
    if summary.total == 0:
        return "No happy path scripts found."

    lines = [
        "## Happy Path Test Results",
        "",
        "| Script | Status | Duration | Details |",
        "|--------|--------|----------|---------|",
    ]

    for r in summary.results:
        status_icon = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}[r.status]
        detail = ""
        if r.status != "pass" and r.stderr:
            # First line of stderr as detail
            detail = r.stderr.strip().split("\n")[0][:80]
        lines.append(f"| {r.script} | {status_icon} | {r.duration_seconds}s | {detail} |")

    lines.extend(
        [
            "",
            f"**Total:** {summary.total} | "
            f"**Passed:** {summary.passed} | "
            f"**Failed:** {summary.failed} | "
            f"**Errors:** {summary.errored} | "
            f"**Duration:** {summary.duration_seconds}s",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    """CLI entry point: run happy path scripts and report results."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python tools/happy_path_runner.py <scripts_dir> [--evidence-dir <dir>]")
        return 2

    scripts_dir = Path(sys.argv[1])
    evidence_dir = None

    if "--evidence-dir" in sys.argv:
        idx = sys.argv.index("--evidence-dir")
        if idx + 1 < len(sys.argv):
            evidence_dir = Path(sys.argv[idx + 1])

    if not scripts_dir.is_dir():
        logger.error("Scripts directory does not exist: %s", scripts_dir)
        return 2

    if not check_rodney_installed():
        logger.error(
            "Rodney is not installed. Install from: https://github.com/nicois/rodney/releases"
        )
        return 2

    summary = run_all(scripts_dir, evidence_dir)

    # Output summary table to stdout
    print(format_summary_table(summary))
    print()

    # Output JSON summary for programmatic consumption
    json_summary = {
        "total": summary.total,
        "passed": summary.passed,
        "failed": summary.failed,
        "errored": summary.errored,
        "duration_seconds": summary.duration_seconds,
        "results": [asdict(r) for r in summary.results],
    }
    print("<!-- HAPPY_PATH_RESULTS")
    print(json.dumps(json_summary, indent=2))
    print("-->")

    # Return appropriate exit code
    if summary.failed > 0 or summary.errored > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
