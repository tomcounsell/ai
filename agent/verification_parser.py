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

- A block is a **check table** when its columns match the check contract: an
  ``Expected`` column immediately after a ``Command`` column (case-insensitive),
  with at least one column ahead of them naming the check. The pair is located,
  not pinned to fixed offsets, so a leading index column is fine. Every data row
  in it is parsed as a check.
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

Three-valued outcomes (#2791/#2901/#3022)
-----------------------------------------
A check result is :class:`CheckOutcome`.``PASS``, ``FAIL``, or
``UNEVALUATED`` -- never a boolean. ``UNEVALUATED`` means *the grader could
not answer the question*, and it is produced by a timeout, by any runner
exception, by an expectation form the grammar does not recognise (including
an empty cell), and by a command cell carrying no backticked span. Each
carries a ``reason``.

``UNEVALUATED`` is **blocking** -- it does not pass -- but it is reported as
its own token and never as ``[FAIL]``. The distinction is the whole point: a
gate that says "your code is wrong" when it means "my grader is wrong" costs
a human the time to discover the difference, and the 2026-09 supervisor batch
hand-verified every such "failure" as actually passing.

Table classification is by column **contract** -- an ``Expected`` column
directly after a ``Command`` column, with something ahead of them to name the
check -- not by the word ``Command`` appearing anywhere in the first three
positions. A table shaped ``| Command | Observed stdout | Observed exit |`` used
to be classified as a check table and have its *second* column executed as a
shell command (#3022); it has no name column and no following ``Expected``, so
the contract rejects it.

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

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)

# Split on a `|` that is not backslash-escaped. A row's cells are the pieces
# between these; `\|` inside a cell survives the split and is unescaped after.
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")

# One bound, shared by every runner of this repo's verification tables. The
# second runner (`scripts/validate_build.py`) carried its own 30s ceiling and
# its own SKIP-on-timeout disposition, so the two graded the same event two
# different ways (#2901). Provisional and tunable.
#
# The bound needs a lever because a timeout is now a durable merge refusal, not
# the non-blocking SKIP it used to be: the slowest row in this repo's plans sits
# around a quarter of the bound on a quiet machine, so heavy contention alone
# can push a legitimate suite over and hold a PR. Raise it via
# VERIFICATION_TIMEOUT_S (or `--timeout`) when a real suite brushes the ceiling
# -- but contention is a load problem, not a bound problem, so prefer rerunning
# on a quiet machine over permanently inflating this.
# A non-positive bound is rejected rather than honored: it would time out every
# row, and since the recorder writes regardless of exit code, a stray
# VERIFICATION_TIMEOUT_S=0 in a launchd environment would persist an
# all-UNEVALUATED aggregate that holds the lane with no self-evident cause.
_FALLBACK_TIMEOUT_S = 120
try:
    _configured_timeout = int(os.environ.get("VERIFICATION_TIMEOUT_S", "") or _FALLBACK_TIMEOUT_S)
except ValueError:
    _configured_timeout = _FALLBACK_TIMEOUT_S
if _configured_timeout <= 0:
    logger.warning(
        "VERIFICATION_TIMEOUT_S=%r is not positive; using %ss",
        os.environ.get("VERIFICATION_TIMEOUT_S"),
        _FALLBACK_TIMEOUT_S,
    )
    _configured_timeout = _FALLBACK_TIMEOUT_S
DEFAULT_TIMEOUT_S = _configured_timeout

# Underscore-prefixed metadata key inside the ledger's `stage_states_json`
# blob, mirroring `_verdicts` / `_sdlc_dispatches` / `_run_identities`. A new
# key in an already-flexible JSON blob: no schema field, no migration.
VERIFICATION_OUTCOMES_KEY = "_verification_outcomes"


class CheckOutcome(StrEnum):
    """The three things a verification check can say.

    ``UNEVALUATED`` is not a softer ``FAIL``: it is blocking, but it reports
    that the *grader* could not answer, not that the code is wrong.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    UNEVALUATED = "UNEVALUATED"


# The outcomes that hold a PR (owner ruling on #3080, `ba092a06d`): FAIL says
# the code is wrong, UNEVALUATED says the grader could not answer, and neither
# is evidence that the lane is shippable.
#
# Defined here rather than on either consumer because it has exactly two, and
# they sit on opposite sides of an architectural boundary: `tools/merge_predicate`
# refuses the merge, and `agent/sdlc_router`'s row 8g re-routes rather than
# dispatching one the predicate would refuse. The router may not import `tools/`
# (see `guard_g8_artifact_verification`), so a set spelled out on the predicate
# could not be shared with it -- and a set spelled out twice is one edit away
# from the two sides silently disagreeing about which outcomes block.
BLOCKING_OUTCOMES = frozenset({CheckOutcome.FAIL.value, CheckOutcome.UNEVALUATED.value})


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
    """A single machine-readable verification check from a plan document.

    ``unevaluated_reason`` is non-empty when the row was read but cannot be
    executed as written -- today, a command cell carrying no backticked span.
    Such a check is never run; it grades ``UNEVALUATED`` with that reason.

    ``extraction_note`` records a non-obvious reading of the command cell (a
    cell with two backticked spans, where the first is taken), so the report
    says what ran rather than leaving the author to guess.
    """

    name: str
    command: str
    expected: str
    unevaluated_reason: str = ""
    extraction_note: str = ""


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
    skipped: list[SkippedTable]


@dataclass
class CheckResult:
    """Result of running a single verification check.

    ``outcome`` is three-valued (:class:`CheckOutcome`). There is deliberately
    no ``passed`` boolean: keeping one alongside would let a caller keep asking
    the ambiguous two-valued question, which is the defect this type exists to
    remove.

    ``reason`` is populated exactly when ``outcome`` is ``UNEVALUATED`` and says
    why the grader could not answer.
    """

    check: VerificationCheck
    outcome: CheckOutcome
    exit_code: int
    output: str
    error: str = ""
    reason: str = ""


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


def check_column_indices(header_cells: list[str]) -> tuple[int, int] | None:
    """Locate a check table's ``(command, expected)`` column indices.

    The contract is a **shape**, not a fixed pair of offsets: an ``Expected``
    column immediately following a ``Command`` column (case-insensitive), with
    at least one column ahead of ``Command`` to name the check. Returns the two
    indices, or ``None`` when the block is not a check table.

    The predicate this replaced asked whether *any* of the first three column
    names was ``Command``, which is a question about vocabulary rather than
    about shape. A table shaped ``| Command | Observed stdout | Observed exit |``
    -- a results recap, not a check list -- satisfied it, and its "Observed
    stdout" column was then executed as a shell command with no diagnostic
    emitted (#3022).

    Pinning the pair to indices 1 and 2 fixed that but over-corrected: a
    leading index column (``| # | Check | Command | Expected |``) is an
    established shape in this repo's live plans, and pinning silently turned
    one such plan's 30 executable checks into 0 checks and 2 malformed rows.
    Searching for the adjacent pair keeps both false positives rejected --
    ``| Command | Observed stdout | Observed exit |`` has no column ahead of
    ``Command`` and no following ``Expected``, and ``| # | Criterion | Check |``
    has no ``Command`` at all -- while accepting every genuine shape.
    """
    for i in range(1, len(header_cells) - 1):
        if (
            header_cells[i].strip().lower() == "command"
            and header_cells[i + 1].strip().lower() == "expected"
        ):
            return i, i + 1
    return None


def _is_check_table_header(header_cells: list[str]) -> bool:
    """Whether a block's header matches the check contract."""
    return check_column_indices(header_cells) is not None


# The first backticked span in a command cell. Anything outside it -- a
# trailing em-dash gloss, a parenthetical -- is prose about the command, not
# part of it.
_BACKTICKED_SPAN_RE = re.compile(r"`([^`]+)`")


def _extract_command(cell: str) -> tuple[str, str, str]:
    """Read a command cell into ``(command, unevaluated_reason, note)``.

    The command is the cell's **first backticked span**. The prior reading was
    ``cell.strip("`")``, which stripped the outer backticks and kept everything
    between them -- so ``` `echo hi` -- this checks greeting ``` was executed
    verbatim under ``shell=True`` as ``echo hi` -- this checks greeting``.

    A cell with no backticked span yields an ``unevaluated_reason``: there is
    nothing unambiguous to run, and guessing is how the trailing-prose defect
    happened. A cell with two or more spans takes the first and says so.
    """
    spans = _BACKTICKED_SPAN_RE.findall(cell)
    if not spans:
        return (
            cell,
            (
                "command cell carries no backticked span, so there is no "
                "unambiguous command to run. Write the command as `cmd`."
            ),
            "",
        )
    note = ""
    if len(spans) > 1:
        note = f"command cell carried {len(spans)} backticked spans; ran the first"
    return spans[0], "", note


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
    each is classified independently. A **check table** (an ``Expected`` column
    immediately after a ``Command`` column, with at least one column ahead of
    them -- see :func:`check_column_indices`) contributes its data rows as
    checks; the expected column count comes from its own header, so a table
    that carries an extra annotation column is read correctly instead of
    having every row rejected. Three columns of a check table are read: the one
    ahead of ``Command`` for the name, then ``Command`` and ``Expected``
    wherever the header puts them.

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
                    f"table has {len(block)} row(s) but its columns are not "
                    "(<name>, Command, Expected); the ## Verification section "
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
            reason=("not a check table: its columns are not (<name>, Command, Expected)"),
        )
        for block in non_check_blocks
    ]

    for block, header_cells in check_blocks:
        expected_columns = max(len(header_cells), 3)
        indices = check_column_indices(header_cells)
        if indices is None:
            # Unreachable in practice: check_blocks was filtered through
            # _is_check_table_header, which is this same call returning
            # non-None. Guarded explicitly anyway so the invariant is
            # enforced in code rather than assumed at this type seam.
            malformed.append(
                MalformedRow(
                    line=block[0],
                    reason="table header no longer matches the check contract",
                )
            )
            continue
        command_idx, expected_idx = indices
        # The check's name is the column immediately ahead of Command, so a
        # leading index column yields the descriptive name rather than "1".
        name_idx = command_idx - 1

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

            name = cells[name_idx]
            raw_command = cells[command_idx]
            expected = cells[expected_idx]

            if not name or not raw_command.strip() or not expected:
                malformed.append(
                    MalformedRow(
                        line=row,
                        reason="Check, Command, and Expected must all be non-empty.",
                    )
                )
                continue

            command, unevaluated_reason, note = _extract_command(raw_command)
            checks.append(
                VerificationCheck(
                    name=name,
                    command=command,
                    expected=expected,
                    unevaluated_reason=unevaluated_reason,
                    extraction_note=note,
                )
            )

    return ParsedTable(checks=checks, malformed=malformed, skipped=skipped)


def timeout_reason(timeout: int) -> str:
    """The one timeout disposition, shared by both runners of these tables."""
    return (
        f"command timed out after {timeout}s, so it never produced a result to grade "
        "(this is not evidence that the check failed)"
    )


def unevaluated_reason(expected: str | None) -> str:
    """Say why ``expected`` could not be graded, for an ``UNEVALUATED`` row."""
    if expected is None or not expected.strip():
        return "expectation cell is empty, so there is nothing to grade."
    return (
        f"unrecognized expectation form: {expected.strip()!r}. "
        "The grammar reads: exit code N, exit N, exit code != N, output contains X, "
        "output does not contain X, match count == 0, output > N, > N, >= N, == N, "
        "output is N, prints `N`, output `N`, empty output, no output."
    )


_TRAILING_GLOSS_DELIMITERS = (",", "(", "`")


def _match_bare_with_delimited_gloss(pattern: str, expected: str) -> re.Match | None:
    """Match a bare anchored form, tolerating a trailing gloss set off by a delimiter.

    **Delimited-gloss rule.** A trailing gloss is only recognised as a gloss --
    and therefore ignored -- when it is set off by a delimiter: a comma, or a
    parenthesis/backtick span (``exit 0, JSON `"compatible": true``` reads as
    ``exit 0`` plus a comma-delimited gloss). Bare trailing words with no
    delimiter are ambiguous (``exit 0 maybe`` could be a typo for a different
    number) and the form stays unmatched, i.e. UNEVALUATED.
    """
    m = re.match(pattern, expected)
    if not m:
        return None
    rest = expected[m.end() :].lstrip()
    if not rest or rest[0] in _TRAILING_GLOSS_DELIMITERS:
        return m
    return None


def evaluate_expectation(expected: str | None, *, exit_code: int, output: str) -> CheckOutcome:
    """Grade a command result against its expected outcome, three-valued.

    Returns ``PASS``, ``FAIL``, or ``UNEVALUATED``. ``UNEVALUATED`` is returned
    for an expectation the grammar does not recognise and for an empty,
    whitespace-only, or ``None`` cell -- never ``FAIL``, because "I did not
    understand the question" is not evidence about the code under test. Call
    :func:`unevaluated_reason` for the accompanying reason text.

    Supported expectation formats (positive):
    - ``exit code N`` / ``exit N`` -- passes when exit_code == N. ``exit N`` also
      tolerates a delimited trailing gloss (see the delimited-gloss rule below),
      e.g. ``exit 0, JSON `"compatible": true```.
    - ``output > N`` / ``> N`` -- passes when output (stripped) is numeric and > N
    - ``>= N`` -- passes when output (stripped) is numeric and >= N
    - ``== N`` / ``output == N`` -- passes when output (stripped) is numeric and == N
    - ``output is N`` -- equality, same semantics as ``output == N``
    - ``prints `N``` / ```output `N``` -- passes when stripped output equals N
    - ``empty output`` / ``no output`` -- passes when stdout is empty or whitespace-only
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

    **Anchoring rule.** The pre-existing forms (``exit code N``, ``output > N``,
    ``output contains X``) stay prefix-matched, because a trailing gloss on
    them -- ``exit code 0 (verified 2026-09-02 ...)`` -- is an established
    authoring idiom in this repo's live plans and anchoring would turn eleven
    working rows into blocking ``UNEVALUATED`` for a change nobody asked for.
    The forms added here (``exit N``, ``prints `N```, ``>= N``, ``> N``,
    ``== N``, ``empty output``) are **anchored**: a bare comparator followed by
    prose is easy to write by accident, and grading a sentence as if it were a
    number is exactly the guess this module exists to stop making. An anchored
    form that does not match reports ``UNEVALUATED`` naming the cell, which
    tells the author what to fix.
    """
    if expected is None or not expected.strip():
        # An empty cell is not a failed check; it is an ungraded one.
        return CheckOutcome.UNEVALUATED
    expected = expected.strip()

    def verdict(ok: bool) -> CheckOutcome:
        return CheckOutcome.PASS if ok else CheckOutcome.FAIL

    def numeric_verdict(op) -> CheckOutcome:
        """Grade a numeric comparison against stripped stdout.

        Non-numeric stdout is a genuine FAIL, not UNEVALUATED: the expectation
        was understood, and the command answered something that is not a
        number.
        """
        try:
            return verdict(op(int(output.strip())))
        except (ValueError, TypeError):
            return CheckOutcome.FAIL

    # --- inverse forms (must be checked before positive forms) ---

    # exit code != N  (inverse: passes when exit_code differs from N)
    m = re.match(r"exit code\s*!=\s*(\d+)", expected)
    if m:
        return verdict(exit_code != int(m.group(1)))

    # output does not contain X  (inverse: passes when X absent AND stdout non-empty)
    m = re.match(r"output does not contain (.+)", expected)
    if m:
        substring = m.group(1).strip()
        if not output.strip():
            # empty-stdout gate: errored / stderr-only command must not false-pass
            return CheckOutcome.FAIL
        return verdict(substring not in output)

    # match count == 0  (inverse: passes when grep -c / -rc output shows zero matches)
    if expected == "match count == 0":
        if not output.strip():
            # empty-stdout gate: truly-empty stdout means the command errored or
            # wrote only to stderr; all(...) over an empty list would be vacuously
            # True without this guard.
            return CheckOutcome.FAIL
        lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
        return verdict(all(ln == "0" or ln.endswith(":0") for ln in lines))

    # --- positive forms ---

    # exit code N  (positive exact-match: passes when exit_code == N). Left
    # prefix-matched: `exit code 0 (verified 2026-09-02 to return exactly one
    # EnvCall today)` is an established authoring idiom in live plans, and
    # anchoring it here would turn eleven working rows across docs/plans/ into
    # blocking UNEVALUATED for a change nobody asked for.
    m = re.match(r"exit code (\d+)", expected)
    if m:
        return verdict(exit_code == int(m.group(1)))

    # exit N  (anchored, but tolerates a delimited trailing gloss -- see the
    # delimited-gloss rule on `_match_bare_with_delimited_gloss` -- e.g.
    # `exit 0, JSON `"compatible": true``. A bare trailing word with no
    # delimiter, e.g. `exit 0 maybe`, stays unmatched and UNEVALUATED.)
    m = _match_bare_with_delimited_gloss(r"exit\s+(\d+)", expected)
    if m:
        return verdict(exit_code == int(m.group(1)))

    # empty output / no output  (passes when stdout is empty or whitespace-only).
    # `no output` is an exact synonym a live plan already writes; accepting it is
    # an alias, not a widening -- there is no reading of it that differs.
    if expected in ("empty output", "no output"):
        return verdict(not output.strip())

    # prints `N`  (passes when stripped stdout equals N; backticks optional)
    m = re.match(r"prints\s+`?([^`]+?)`?\s*$", expected)
    if m:
        return verdict(output.strip() == m.group(1).strip())

    # output `N`  (backtick-wrapped integer, equality against stripped stdout;
    # same semantics as `output == N` below, and prefix-matched for the same
    # reason -- it is the `output`-prefixed idiom's backtick spelling).
    m = re.match(r"output\s*`(\d+)`", expected)
    if m:
        target = int(m.group(1))
        return numeric_verdict(lambda value: value == target)

    # Trailing-gloss rule (applies uniformly to >, >=, ==): the `output`-prefixed
    # spellings (`output > N`, `output >= N`, `output == N`) are the established
    # authoring idiom in live plans -- e.g. `output > 0 (a bare file-wide grep
    # returns 3 today)` or `output == 2 (the two read sites)` -- so they are
    # prefix-matched and tolerate a trailing gloss. The bare spellings (`> N`,
    # `>= N`, `== N`) have no such idiom behind them and stay anchored, so a
    # trailing gloss on a bare form is UNEVALUATED. Each `output`-prefixed
    # branch is tried before its bare counterpart so the prefix match wins.

    # output >= N -- prefix-matched (see the trailing-gloss rule above).
    m = re.match(r"output\s*>=\s*(\d+)", expected)
    if m:
        threshold = int(m.group(1))
        return numeric_verdict(lambda value: value >= threshold)

    # >= N  (anchored, see the trailing-gloss rule above)
    m = re.match(r">=\s*(\d+)\s*$", expected)
    if m:
        threshold = int(m.group(1))
        return numeric_verdict(lambda value: value >= threshold)

    # output > N -- prefix-matched (see the trailing-gloss rule above).
    m = re.match(r"output\s*>\s*(\d+)", expected)
    if m:
        threshold = int(m.group(1))
        return numeric_verdict(lambda value: value > threshold)

    # > N  (anchored, see the trailing-gloss rule above)
    m = re.match(r">\s*(\d+)\s*$", expected)
    if m:
        threshold = int(m.group(1))
        return numeric_verdict(lambda value: value > threshold)

    # output == N -- prefix-matched (see the trailing-gloss rule above).
    m = re.match(r"output\s*==\s*(\d+)", expected)
    if m:
        target = int(m.group(1))
        return numeric_verdict(lambda value: value == target)

    # output is N -- `is` used as the equality verb; same semantics and
    # prefix-matching as `output == N` above.
    m = re.match(r"output\s+is\s+(\d+)", expected)
    if m:
        target = int(m.group(1))
        return numeric_verdict(lambda value: value == target)

    # == N  (anchored, see the trailing-gloss rule above)
    m = re.match(r"==\s*(\d+)\s*$", expected)
    if m:
        target = int(m.group(1))
        return numeric_verdict(lambda value: value == target)

    # output contains X
    m = re.match(r"output contains (.+)", expected)
    if m:
        substring = m.group(1).strip()
        return verdict(substring in output)

    return CheckOutcome.UNEVALUATED


def run_checks(
    checks: list[VerificationCheck],
    *,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> list[CheckResult]:
    """Run a list of verification checks and return results.

    Each check is executed as a shell command and graded three-valued. A check
    the parser already marked unrunnable is not executed at all; a timeout and
    any runner exception both grade ``UNEVALUATED`` with the reason attached,
    because neither is evidence about the code under test.
    """
    results: list[CheckResult] = []
    for check in checks:
        if check.unevaluated_reason:
            results.append(
                CheckResult(
                    check=check,
                    outcome=CheckOutcome.UNEVALUATED,
                    exit_code=-1,
                    output="",
                    reason=check.unevaluated_reason,
                )
            )
            continue
        try:
            proc = subprocess.run(
                check.command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
            outcome = evaluate_expectation(
                check.expected,
                exit_code=proc.returncode,
                output=proc.stdout,
            )
            results.append(
                CheckResult(
                    check=check,
                    outcome=outcome,
                    exit_code=proc.returncode,
                    output=proc.stdout.strip(),
                    error=proc.stderr.strip(),
                    reason=(
                        unevaluated_reason(check.expected)
                        if outcome is CheckOutcome.UNEVALUATED
                        else ""
                    ),
                )
            )
        except subprocess.TimeoutExpired:
            reason = timeout_reason(timeout)
            results.append(
                CheckResult(
                    check=check,
                    outcome=CheckOutcome.UNEVALUATED,
                    exit_code=-1,
                    output="",
                    error=reason,
                    reason=reason,
                )
            )
        except Exception as e:
            reason = f"runner error, the check never ran: {type(e).__name__}: {e}"
            results.append(
                CheckResult(
                    check=check,
                    outcome=CheckOutcome.UNEVALUATED,
                    exit_code=-1,
                    output="",
                    error=reason,
                    reason=reason,
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

    ``UNEVALUATED`` rows render as their own token with their reason and are
    never printed as ``[FAIL]``. They are blocking -- the run does not report
    "All checks passed." -- but they say plainly that the grader, not the code,
    is what could not answer.
    """
    malformed = table.malformed
    skipped = table.skipped
    lines: list[str] = ["## Verification Results", ""]
    unevaluated = [r for r in results if r.outcome is CheckOutcome.UNEVALUATED]
    failed = [r for r in results if r.outcome is CheckOutcome.FAIL]
    all_passed = not failed and not unevaluated and not malformed

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
        lines.append(f"- [{r.outcome.value}] {r.check.name}")
        if r.outcome is CheckOutcome.UNEVALUATED:
            lines.append(f"  Command: `{r.check.command}`")
            lines.append(f"  Expected: {r.check.expected}")
            lines.append(f"  Reason: {r.reason}")
            continue
        if r.outcome is CheckOutcome.FAIL:
            lines.append(f"  Command: `{r.check.command}`")
            lines.append(f"  Expected: {r.check.expected}")
            lines.append(f"  Got: exit code {r.exit_code}")
            if r.output:
                lines.append(f"  Output: {r.output[:200]}")
            if r.error:
                lines.append(f"  Error: {r.error[:200]}")
        if r.check.extraction_note:
            lines.append(f"  Note: {r.check.extraction_note}")

    lines.append("")
    if all_passed:
        summary = "All checks passed."
    elif not failed and malformed and not unevaluated:
        summary = f"{len(malformed)} row(s) could not be parsed and were not run."
    elif not failed:
        summary = (
            f"{len(unevaluated)} check(s) could not be evaluated"
            + (f" and {len(malformed)} row(s) could not be parsed" if malformed else "")
            + "."
        )
    else:
        summary = "Some checks failed."
    lines.append(f"**{summary}**")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persisting the graded aggregate (#3065, Cluster B -> Cluster C)
# ---------------------------------------------------------------------------
#
# A verification run is graded at TEST/DOCS time and consumed by the merge
# predicate later, so the outcome has to outlive the process that produced it.
# It is stored as `_verification_outcomes` inside the issue-keyed
# PipelineLedger's `stage_states_json` blob -- an underscore-prefixed metadata
# key exactly like `_verdicts`, `_sdlc_dispatches`, and `_run_identities`, so
# it needs no schema field and no migration.
#
# The aggregate carries the PR head SHA it was graded against. Without that
# anchor a lane that passes verification and then takes a new commit merges on
# a cached PASS -- "a fact readable earlier, not now", which is the exact defect
# this mechanism exists to close. The SHA is resolved through
# `tools.pr_head_resolver.resolve_pr_head_sha` (git-first) and never a bare
# `gh` read: a stale `gh` head SHA is what flipped the verdict-staleness gate
# fail-open in #2895.


def aggregate_outcomes(results: list[CheckResult], table: ParsedTable | None = None) -> dict:
    """Reduce a graded run to the record the merge predicate reads.

    Overall outcome is the worst thing present: any ``FAIL`` (or any malformed
    row, which is an unrunnable check) makes the run ``FAIL``; otherwise any
    ``UNEVALUATED`` makes it ``UNEVALUATED``; a run with no checks at all is
    ``UNEVALUATED``, never a vacuous ``PASS``.
    """
    malformed_count = len(table.malformed) if table else 0
    counts = {
        CheckOutcome.PASS.value: 0,
        CheckOutcome.FAIL.value: 0,
        CheckOutcome.UNEVALUATED.value: 0,
    }
    for r in results:
        counts[r.outcome.value] += 1

    if counts[CheckOutcome.FAIL.value] or malformed_count:
        overall = CheckOutcome.FAIL
    elif counts[CheckOutcome.UNEVALUATED.value] or not results:
        overall = CheckOutcome.UNEVALUATED
    else:
        overall = CheckOutcome.PASS

    return {
        "outcome": overall.value,
        "counts": counts,
        "malformed": malformed_count,
        "recorded_at": datetime.now(UTC).isoformat(),
        "rows": [
            {
                "name": r.check.name,
                "outcome": r.outcome.value,
                "reason": r.reason,
            }
            for r in results
        ],
    }


def record_verification_outcomes(
    target_repo: str | None,
    issue_number: int | None,
    results: list[CheckResult],
    *,
    table: ParsedTable | None = None,
    pr_number: int | None = None,
    repo_root: str | None = None,
) -> bool:
    """Persist this run's graded aggregate to the lane's ledger.

    Stamps ``head_sha`` with the PR head the run was graded against, resolved
    through ``tools.pr_head_resolver.resolve_pr_head_sha``. A lane with no PR
    at write time (or one whose head cannot be resolved) records the aggregate
    with **no** ``head_sha`` key rather than a fabricated one, and does not
    crash; the reader decides what an unanchored aggregate is worth.

    Fails OPEN: returns ``False`` on any failure and never raises. A grading
    run that cannot write its record must still report its result to the human
    in front of it.
    """
    if not target_repo or not issue_number:
        return False

    try:
        aggregate = aggregate_outcomes(results, table)

        if pr_number:
            try:
                from tools.pr_head_resolver import resolve_pr_head_sha

                head_sha = resolve_pr_head_sha(
                    int(pr_number), repo=target_repo, repo_root=repo_root
                )
                if head_sha:
                    aggregate["head_sha"] = head_sha
                else:
                    # Resolver returned no exception but no usable SHA either --
                    # stamp this distinctly from "never anchored" so a merge
                    # refusal can say *why* there is no head_sha.
                    aggregate["head_sha_anchor_failed"] = True
            except Exception as exc:
                logger.debug(
                    "record_verification_outcomes: head-SHA resolve failed for "
                    "%s#%s PR %s (%s: %s) -- recording without an anchor",
                    target_repo,
                    issue_number,
                    pr_number,
                    type(exc).__name__,
                    exc,
                )
                # A transient resolver failure at record time must not read
                # the same as a lane that was never anchored: stamp the
                # failed-anchor case distinctly so the refusal message and
                # troubleshooting can tell the two apart.
                aggregate["head_sha_anchor_failed"] = True

        from agent.pipeline_ledger import PipelineLedger
        from tools.stage_states_helpers import update_stage_states

        ledger = PipelineLedger.get_or_create(target_repo, issue_number)

        def write_outcomes(states: dict) -> dict:
            states[VERIFICATION_OUTCOMES_KEY] = aggregate
            return states

        return bool(update_stage_states(ledger, write_outcomes, field="stage_states_json"))
    except Exception as exc:
        logger.debug(
            "record_verification_outcomes: write failed for %s#%s (%s: %s)",
            target_repo,
            issue_number,
            type(exc).__name__,
            exc,
        )
        return False


class VerificationOutcomesUnavailableError(Exception):
    """The recorded aggregate could not be read, as distinct from absent.

    A merge gate must tell these two apart. "No aggregate was ever recorded"
    is a lane the gate deliberately does not block; "the aggregate exists but
    the read failed" is a lane about which nothing is known, and treating the
    second as the first converts a recorded ``FAIL`` into an unenforced pass on
    a Redis blip. Every neighbouring group in ``tools/merge_predicate.py``
    fails closed on its own read error; this makes that possible here.
    """


def read_verification_outcomes(target_repo: str | None, issue_number: int | None) -> dict | None:
    """Return the recorded aggregate for a lane, or ``None`` if there is none.

    Non-mutating: uses :meth:`PipelineLedger.get`, so a read never litters an
    empty ledger.

    Fails **closed**. ``None`` means genuine absence -- no ledger, no
    ``stage_states`` blob, or no ``_verification_outcomes`` key in it. Anything
    that prevents an answer (an unreachable store, an unparseable blob, a
    record of the wrong shape) raises :class:`VerificationOutcomesUnavailableError`
    rather than reporting absence, so the caller can refuse instead of
    silently passing a lane it could not check.
    """
    if not target_repo or not issue_number:
        return None
    try:
        from agent.pipeline_ledger import PipelineLedger

        ledger = PipelineLedger.get(target_repo, issue_number)
        if ledger is None:
            return None
        raw = ledger.stage_states_json
        if raw is None or raw == "":
            return None
        blob = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.debug(
            "read_verification_outcomes: read failed for %s#%s (%s: %s)",
            target_repo,
            issue_number,
            type(exc).__name__,
            exc,
        )
        raise VerificationOutcomesUnavailableError(
            f"could not read verification outcomes for {target_repo}#{issue_number}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(blob, dict):
        raise VerificationOutcomesUnavailableError(
            f"stage_states for {target_repo}#{issue_number} is {type(blob).__name__}, not an object"
        )
    if VERIFICATION_OUTCOMES_KEY not in blob:
        return None
    record = blob[VERIFICATION_OUTCOMES_KEY]
    if not isinstance(record, dict):
        raise VerificationOutcomesUnavailableError(
            f"recorded verification outcomes for {target_repo}#{issue_number} are "
            f"{type(record).__name__}, not an object"
        )
    return record
