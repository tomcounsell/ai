"""Parse machine-readable verification tables from plan documents.

Plan documents contain a ``## Verification`` section with a markdown table:

    ## Verification

    | Check | Command | Expected |
    |-------|---------|----------|
    | Tests pass | `pytest tests/ -x -q` | exit code 0 |
    | Lint clean | `python -m ruff check .` | exit code 0 |

This module extracts those rows into ``VerificationCheck`` objects and provides
``evaluate_expectation`` to decide pass/fail based on command results.

Pipes in commands (#2570)
-------------------------
Rows are split on **unescaped** ``|`` only, and ``\\|`` is unescaped to a
literal ``|`` afterwards. That is standard GitHub-flavored Markdown, and it
makes the Markdown-safe way to write a pipe also the correct way:

    | Anti-criterion | `grep -rc 'a\\|b' src/ \\| wc -l` | match count == 0 |

runs ``grep -rc 'a|b' src/ | wc -l``. Before this, every row was split on every
``|``, so a pipe-bearing command was truncated at the pipe, the ``Expected``
cell received the fragment *after* it, and the real expectation was discarded.
The check then ran a command its author never wrote, failed for an unrelated
reason (usually a shell syntax error), and reported that failure against the
code under test. Escaping did not help: both ``\\|`` and a bare ``|``
truncated, so there was no working form and regex alternation, shell
pipelines, and ``re.S|re.M`` were all unwritable.

A row that still does not yield the header's column count -- an author wrote a
bare ``|`` and meant it as part of the command -- is now reported as a
:class:`MalformedRow` rather than executed as something else. Silently running
a different command than the one on the page is the failure this module exists
not to have.

The escape composes, which matters for basic-regex ``grep``: in a BRE,
alternation is spelled ``\\|``, and to get that through the table you double
the backslash. What lands in the shell is one level of unescaping::

    table cell        reaches the shell as     grep -E / grep -c meaning
    ---------------   ----------------------   -------------------------
    a\\|b              a|b                      -E: alternation. -c: literal "a|b"
    a\\\\|b             a\\|b                     -c (BRE): alternation
    a|b               (rejected: malformed)    --
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Split on a `|` that is not backslash-escaped. A row's cells are the pieces
# between these; `\|` inside a cell survives the split and is unescaped after.
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def split_row_cells(row: str) -> list[str]:
    """Split one markdown table row into its cells, honoring ``\\|`` escapes.

    Leading/trailing empties from the row's border pipes are dropped; interior
    empties are preserved, because an empty cell is a real (if unusable) cell
    and collapsing it would shift every column after it.
    """
    parts = _UNESCAPED_PIPE_RE.split(row)
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.replace("\\|", "|").strip() for p in parts]


@dataclass(frozen=True)
class VerificationCheck:
    """A single machine-readable verification check from a plan document."""

    name: str
    command: str
    expected: str


@dataclass(frozen=True)
class MalformedRow:
    """A table row that could not be read as the author wrote it.

    Reported separately from a failed check: this is a plan-authoring error,
    not evidence about the code under test.
    """

    line: str
    reason: str


@dataclass(frozen=True)
class ParsedTable:
    """Everything a ``## Verification`` table yielded, including its rejects."""

    checks: list[VerificationCheck]
    malformed: list[MalformedRow]


@dataclass
class CheckResult:
    """Result of running a single verification check."""

    check: VerificationCheck
    passed: bool
    exit_code: int
    output: str
    error: str = ""


def parse_verification_table(markdown: str) -> ParsedTable:
    """Extract verification checks from a ``## Verification`` markdown table.

    Returns an empty :class:`ParsedTable` when no ``## Verification`` section is
    found or the table has no data rows.

    The expected column count comes from the header rather than being hardcoded
    to 3, so a table that carries an extra annotation column is read correctly
    instead of having every row rejected. Only the first three columns are used:
    Check, Command, Expected.

    A row that does not yield that many cells lands in ``malformed`` with a
    reason. It is never executed on a guess (#2570).
    """
    section_match = re.search(
        r"^## Verification\s*$(.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return ParsedTable(checks=[], malformed=[])

    section = section_match.group(1)

    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        # Need at least header + separator (no data rows)
        return ParsedTable(checks=[], malformed=[])

    expected_columns = max(len(split_row_cells(rows[0])), 3)

    checks: list[VerificationCheck] = []
    malformed: list[MalformedRow] = []

    for row in rows[2:]:  # Skip header and separator
        cells = split_row_cells(row)

        if len(cells) != expected_columns:
            malformed.append(
                MalformedRow(
                    line=row,
                    reason=(
                        f"expected {expected_columns} columns, got {len(cells)}. "
                        "An unescaped `|` inside a cell splits the row. Write it "
                        "as `\\|` (Markdown escape) and it reaches the shell as a "
                        "literal pipe."
                    ),
                )
            )
            continue

        name = cells[0]
        command = cells[1].strip("`")
        expected = cells[2]

        if not name or not command or not expected:
            malformed.append(
                MalformedRow(
                    line=row,
                    reason="Check, Command, and Expected must all be non-empty.",
                )
            )
            continue

        checks.append(VerificationCheck(name=name, command=command, expected=expected))

    return ParsedTable(checks=checks, malformed=malformed)


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
      Canonical idioms (write the pipe as ``\\|`` in the plan's table -- see the
      module docstring; it reaches the shell as a literal ``|``):
        - ``grep -c PATTERN file``      → emits literal "0", exit 1 → passes
        - ``grep -rc PATTERN dir``      → emits "path:0" per file, exit 1 → passes
        - ``grep -r PATTERN dir \\| wc -l`` → emits whitespace "       0", exit 0 → passes
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


def format_results(
    results: list[CheckResult],
    malformed: list[MalformedRow] | None = None,
) -> str:
    """Format check results as a human-readable report.

    Malformed rows are reported in their own section, above the checks and
    named as plan-authoring errors, so a row that could not be read is never
    mistaken for evidence about the code under test (#2570). They also make the
    run fail: a row nobody can execute is not a passing check.
    """
    malformed = malformed or []
    lines: list[str] = ["## Verification Results", ""]
    all_passed = all(r.passed for r in results) and not malformed

    if malformed:
        lines.append(f"### Plan authoring errors ({len(malformed)})")
        lines.append("")
        lines.append("These rows were NOT executed. Fix the plan, not the code.")
        lines.append("")
        for m in malformed:
            lines.append(f"- [MALFORMED] {m.line}")
            lines.append(f"  Reason: {m.reason}")
        lines.append("")

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
    if malformed and all(r.passed for r in results):
        summary = f"{len(malformed)} row(s) could not be parsed and were not run."
    else:
        summary = "All checks passed." if all_passed else "Some checks failed."
    lines.append(f"**{summary}**")

    return "\n".join(lines)
