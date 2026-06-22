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

    Supported expectation formats (positive):
    - ``exit code N`` -- passes when exit_code == N (positive exact-match)
    - ``output > N`` -- passes when output (stripped) is numeric and > N
    - ``output contains X`` -- passes when substring X appears in stdout

    Supported expectation formats (inverse / anti-criteria):
    - ``exit code != N`` -- passes when exit_code != N (command must NOT exit with N)
    - ``output does not contain X`` -- passes when X is absent from stdout AND stdout
      is non-empty (empty-stdout gate: an errored/stderr-only command cannot false-pass
      by trivially "not containing" the substring). Canonical idiom: pipe output
      through ``grep -v`` or use a command that emits a non-empty clean signal.
    - ``match count == 0`` -- passes when every non-blank line of stdout is "0" or
      ends with ":0" (the ``grep -c``/``grep -rc`` shapes) AND stdout is non-empty
      (empty-stdout gate: a command that errored or wrote only to stderr yields empty
      stdout; without the gate ``all(...)`` over an empty list is vacuously True).
      Canonical idioms:
        - ``grep -c PATTERN file``      → emits literal "0", exit 1 → passes
        - ``grep -rc PATTERN dir``      → emits "path:0" per file, exit 1 → passes
        - ``grep -r PATTERN dir | wc -l`` → emits whitespace "       0", exit 0 → passes
        - truly-empty stdout (errored)  → rejected by empty-stdout gate → fails

    The inverse ``exit code != N`` branch is checked BEFORE the positive ``exit code N``
    branch, and ``output does not contain X`` is checked BEFORE ``output contains X``,
    so the inverse forms are always matched first and never captured by positive matchers.
    """
    expected = expected.strip()

    # --- inverse forms (must be checked before positive forms) ---

    # exit code != N  (inverse: passes when exit_code differs from N)
    m = re.match(r"exit code\s*!=\s*(\d+)", expected)
    if m:
        return exit_code != int(m.group(1))

    # output does not contain X  (inverse: passes when X absent AND stdout non-empty)
    m = re.match(r"output does not contain (.+)", expected)
    if m:
        substring = m.group(1).strip()
        if not output.strip():
            # empty-stdout gate: errored / stderr-only command must not false-pass
            return False
        return substring not in output

    # match count == 0  (inverse: passes when grep -c / -rc output shows zero matches)
    if expected.strip() == "match count == 0":
        if not output.strip():
            # empty-stdout gate: truly-empty stdout means the command errored or
            # wrote only to stderr; all(...) over an empty list would be vacuously
            # True without this guard.
            return False
        lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
        return all(ln == "0" or ln.endswith(":0") for ln in lines)

    # --- positive forms ---

    # exit code N  (positive exact-match: passes when exit_code == N)
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
