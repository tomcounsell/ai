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

Table scoping (#2836)
----------------------
A ``## Verification`` section is scanned for pipe-blocks: contiguous runs of
``|``-prefixed lines. That is the GitHub Flavored Markdown definition of one
table (GFM spec section 4.10 -- a pipe table is a leaf block whose body
consumes rows until a blank line or a line that cannot be part of the table).
Every pipe-block in the section is classified on its own, independently:

- A block is a **check table** when it has at least three columns and one of
  its first three column names is exactly ``Command`` (case-insensitive).
  Every data row in it is parsed as a check, exactly as before.
- A block that is not a check table -- a red/green summary, a findings recap
  -- becomes a :class:`SkippedTable`: named, reported, and non-failing. A
  second markdown table in the section is legitimate plan authoring; treating
  its header, separator, and data rows as guaranteed-fail checks (the pre-fix
  behavior) is exactly the bug this module exists to not have.
- When the section has pipe-blocks but **none** of them is a check table, the
  section yielded zero executable checks and that is a loud failure: exactly
  one :class:`MalformedRow` per pipe-block (never one per row), and no
  :class:`SkippedTable` is produced in this branch -- a block is either
  skipped or malformed, never both.

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
from dataclasses import dataclass, field

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
class SkippedTable:
    """A pipe-block in ``## Verification`` that is not a check table.

    Named and reported in both runners, but non-failing: a summary table is
    legitimate plan authoring, and failing the gate on it would reproduce
    #2836 with a friendlier message (see the module docstring's "Table
    scoping" section).
    """

    header: str
    row_count: int
    reason: str


@dataclass(frozen=True)
class ParsedTable:
    """Everything a ``## Verification`` table yielded, including its rejects."""

    checks: list[VerificationCheck]
    malformed: list[MalformedRow]
    skipped: list[SkippedTable] = field(default_factory=list)


@dataclass
class CheckResult:
    """Result of running a single verification check."""

    check: VerificationCheck
    passed: bool
    exit_code: int
    output: str
    error: str = ""


_SEPARATOR_ROW_RE = re.compile(r"^\|[\s\-:|]+\|$")


def _iter_pipe_blocks(section: str) -> list[list[str]]:
    """Split a section into contiguous runs of ``|``-prefixed lines.

    This is the GFM definition of one markdown table: a blank line, or any
    line that cannot be part of the table, ends the table block (GFM spec
    section 4.10). A second markdown table therefore never merges into the
    first -- each contiguous run is returned as its own block.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append(stripped)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _is_check_table_header(header_cells: list[str]) -> bool:
    """A block is a check table when it has >=3 columns and one of its first
    three column names is exactly "Command" (case-insensitive)."""
    if len(header_cells) < 3:
        return False
    return any(cell.strip().lower() == "command" for cell in header_cells[:3])


def _block_data_rows(block: list[str]) -> list[str]:
    """A block's rows after its header and (if present) its separator row."""
    rows = block[1:]
    if rows and _SEPARATOR_ROW_RE.match(rows[0]):
        rows = rows[1:]
    return rows


def parse_verification_table(markdown: str) -> ParsedTable:
    """Extract verification checks from a ``## Verification`` markdown table.

    Returns an empty :class:`ParsedTable` when no ``## Verification`` section is
    found or the section has no pipe-blocks at all.

    The section is split into pipe-blocks (see :func:`_iter_pipe_blocks`) and
    each is classified independently. A **check table** (header carries a
    ``Command`` column among its first three) contributes its data rows as
    checks; the expected column count comes from its own header, so a table
    that carries an extra annotation column is read correctly instead of
    having every row rejected. Only the first three columns of a check table
    are used: Check, Command, Expected.

    A non-check table becomes a non-failing :class:`SkippedTable`. When the
    section has pipe-blocks but none of them is a check table, that is a loud
    failure instead: exactly one :class:`MalformedRow` per pipe-block, never
    one per row, and ``skipped`` stays empty in that branch.

    A check-table row that does not yield the header's column count lands in
    ``malformed`` with a reason. It is never executed on a guess (#2570).
    """
    section_match = re.search(
        r"^## Verification\s*$(.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return ParsedTable(checks=[], malformed=[], skipped=[])

    section = section_match.group(1)
    blocks = _iter_pipe_blocks(section)
    if not blocks:
        return ParsedTable(checks=[], malformed=[], skipped=[])

    check_blocks: list[tuple[list[str], list[str]]] = []
    non_check_blocks: list[list[str]] = []
    for block in blocks:
        header_cells = split_row_cells(block[0])
        if _is_check_table_header(header_cells):
            check_blocks.append((block, header_cells))
        else:
            non_check_blocks.append(block)

    if not check_blocks:
        # Rows present but zero executable checks: a loud failure, one
        # MalformedRow per pipe-block, never one per row. `skipped` stays
        # empty -- a block is either skipped or malformed, never both.
        malformed = [
            MalformedRow(
                line=block[0],
                reason=(
                    f"table has {len(block)} row(s) but none of its first three "
                    "column names is Command; the ## Verification section "
                    "yielded zero executable checks"
                ),
            )
            for block in blocks
        ]
        return ParsedTable(checks=[], malformed=malformed, skipped=[])

    checks: list[VerificationCheck] = []
    malformed: list[MalformedRow] = []
    skipped: list[SkippedTable] = [
        SkippedTable(
            header=block[0],
            row_count=len(_block_data_rows(block)),
            reason="not a check table: no column of the first three is named Command",
        )
        for block in non_check_blocks
    ]

    for block, header_cells in check_blocks:
        expected_columns = max(len(header_cells), 3)

        for row in _block_data_rows(block):
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

    return ParsedTable(checks=checks, malformed=malformed, skipped=skipped)


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
    table: ParsedTable,
) -> str:
    """Format check results as a human-readable report.

    ``table`` is required -- both production call sites
    (``docs/sdlc/do-build.md`` and ``docs/sdlc/do-pr-review.md``) construct a
    ``ParsedTable`` and are updated in the same change that added this
    parameter, so an optional parameter would be a back-compat bridge with no
    beneficiary and a silent way for the ``skipped`` diagnostic to stop
    reaching a reader (#2570 added ``malformed`` optionally and a stale doc
    still described the old signature; this parameter does not repeat that).

    Malformed rows are reported in their own section, above the checks and
    named as plan-authoring errors, so a row that could not be read is never
    mistaken for evidence about the code under test (#2570). They also make the
    run fail: a row nobody can execute is not a passing check.

    Skipped (non-check) tables are reported in their own section and do not
    participate in the pass/fail verdict (#2836): a summary table is
    legitimate plan authoring.
    """
    malformed = table.malformed
    skipped = table.skipped
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

    if skipped:
        lines.append(f"### Non-check tables skipped ({len(skipped)})")
        lines.append("")
        lines.append(
            "These pipe-blocks are not check tables (no Command column) and were "
            "not executed. They do not affect the pass/fail verdict."
        )
        lines.append("")
        for s in skipped:
            lines.append(f"- [SKIPPED] {s.header} ({s.row_count} row(s))")
            lines.append(f"  Reason: {s.reason}")
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
