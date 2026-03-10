"""Parse machine-readable verification tables from plan documents.

Plan documents contain a ``## Verification`` section with a markdown table:

    ## Verification

    | Check | Command | Expected |
    |-------|---------|----------|
    | Tests pass | `pytest tests/ -x -q` | exit code 0 |
    | Lint clean | `python -m ruff check .` | exit code 0 |

This module extracts those rows into ``VerificationCheck`` objects and provides
``evaluate_expectation`` to decide pass/fail based on command results.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationCheck:
    """A single machine-readable verification check from a plan document."""

    name: str
    command: str
    expected: str


@dataclass
class CheckResult:
    """Result of running a single verification check."""

    check: VerificationCheck
    passed: bool
    exit_code: int
    output: str
    error: str = ""


def parse_verification_table(markdown: str) -> list[VerificationCheck]:
    """Extract verification checks from a ``## Verification`` markdown table.

    Returns an empty list when no ``## Verification`` section is found or the
    table has no data rows.
    """
    # Find the ## Verification section
    section_match = re.search(
        r"^## Verification\s*$(.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return []

    section = section_match.group(1)

    # Find table rows (lines starting with |)
    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]

    if len(rows) < 2:
        # Need at least header + separator (no data rows)
        return []

    checks: list[VerificationCheck] = []
    for row in rows[2:]:  # Skip header and separator
        cells = [c.strip() for c in row.split("|")]
        # Split produces empty strings at boundaries: ['', 'Check', 'Command', 'Expected', '']
        cells = [c for c in cells if c]
        if len(cells) < 3:
            continue

        name = cells[0].strip()
        command = cells[1].strip().strip("`")
        expected = cells[2].strip()

        if not name or not command or not expected:
            continue

        checks.append(VerificationCheck(name=name, command=command, expected=expected))

    return checks


def evaluate_expectation(expected: str, *, exit_code: int, output: str) -> bool:
    """Evaluate whether a command result meets the expected outcome.

    Supported expectation formats:
    - ``exit code N`` -- checks that exit_code == N
    - ``output > N`` -- checks that output (stripped) is numeric and > N
    - ``output contains X`` -- checks that X appears in output
    """
    expected = expected.strip()

    # exit code N
    m = re.match(r"exit code (\d+)", expected)
    if m:
        return exit_code == int(m.group(1))

    # output > N
    m = re.match(r"output\s*>\s*(\d+)", expected)
    if m:
        threshold = int(m.group(1))
        try:
            value = int(output.strip())
        except (ValueError, TypeError):
            return False
        return value > threshold

    # output contains X
    m = re.match(r"output contains (.+)", expected)
    if m:
        substring = m.group(1).strip()
        return substring in output

    return False


def run_checks(
    checks: list[VerificationCheck],
    *,
    cwd: str | None = None,
    timeout: int = 120,
) -> list[CheckResult]:
    """Run a list of verification checks and return results.

    Each check is executed as a shell command. The result is evaluated against
    the check's expected outcome.
    """
    results: list[CheckResult] = []
    for check in checks:
        try:
            proc = subprocess.run(
                check.command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
            passed = evaluate_expectation(
                check.expected,
                exit_code=proc.returncode,
                output=proc.stdout,
            )
            results.append(
                CheckResult(
                    check=check,
                    passed=passed,
                    exit_code=proc.returncode,
                    output=proc.stdout.strip(),
                    error=proc.stderr.strip(),
                )
            )
        except subprocess.TimeoutExpired:
            results.append(
                CheckResult(
                    check=check,
                    passed=False,
                    exit_code=-1,
                    output="",
                    error=f"Command timed out after {timeout}s",
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    check=check,
                    passed=False,
                    exit_code=-1,
                    output="",
                    error=f"Failed to execute: {e}",
                )
            )

    return results


def format_results(results: list[CheckResult]) -> str:
    """Format check results as a human-readable report."""
    lines: list[str] = ["## Verification Results", ""]
    all_passed = all(r.passed for r in results)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"- [{status}] {r.check.name}")
        if not r.passed:
            lines.append(f"  Command: `{r.check.command}`")
            lines.append(f"  Expected: {r.check.expected}")
            lines.append(f"  Got: exit code {r.exit_code}")
            if r.output:
                lines.append(f"  Output: {r.output[:200]}")
            if r.error:
                lines.append(f"  Error: {r.error[:200]}")

    lines.append("")
    summary = "All checks passed." if all_passed else "Some checks failed."
    lines.append(f"**{summary}**")

    return "\n".join(lines)
