"""Unit tests for agent/verification_parser.py -- machine-readable verification checks."""

from pathlib import Path

from agent.verification_parser import (
    CheckResult,
    MalformedRow,
    ParsedTable,
    SkippedTable,
    VerificationCheck,
    evaluate_expectation,
    format_results,
    parse_verification_table,
    split_row_cells,
)

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "verification"

# ---------------------------------------------------------------------------
# parse_verification_table
# ---------------------------------------------------------------------------


class TestParseVerificationTable:
    """Tests for extracting checks from a markdown verification table."""

    def test_basic_table(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 2
        assert checks[0] == VerificationCheck(
            name="Tests pass",
            command="pytest tests/ -x -q",
            expected="exit code 0",
        )
        assert checks[1] == VerificationCheck(
            name="Lint clean",
            command="python -m ruff check .",
            expected="exit code 0",
        )

    def test_output_gt_expectation(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| PR opened | `gh pr list --head session/slug --json number --jq length` | output > 0 |
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 1
        assert checks[0].expected == "output > 0"

    def test_output_contains_expectation(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Module loads | `python -c "import foo; print(foo.__version__)"` | output contains foo |
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 1
        assert checks[0].expected == "output contains foo"

    def test_no_verification_section(self):
        md = """\
## Success Criteria

- [ ] Something
"""
        checks = parse_verification_table(md).checks
        assert checks == []

    def test_empty_table(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
"""
        checks = parse_verification_table(md).checks
        assert checks == []

    def test_ignores_separator_row(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 1

    def test_strips_backticks_from_command(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hello` | exit code 0 |
"""
        checks = parse_verification_table(md).checks
        assert checks[0].command == "echo hello"

    def test_command_without_backticks(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | echo hello | exit code 0 |
"""
        checks = parse_verification_table(md).checks
        assert checks[0].command == "echo hello"

    def test_table_after_other_content(self):
        """Verification table can appear after other sections."""
        md = """\
# My Plan

## Problem

Something is broken.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Fix works | `python -c "print('ok')"` | exit code 0 |

## Open Questions

None.
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 1
        assert checks[0].name == "Fix works"

    def test_multiple_rows(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Module importable | `python -c "from agent.foo import Bar"` | exit code 0 |
| Feature doc exists | `test -f docs/features/foo.md` | exit code 0 |
| PR opened | `gh pr list --head session/foo --json number --jq length` | output > 0 |
"""
        checks = parse_verification_table(md).checks
        assert len(checks) == 6


# ---------------------------------------------------------------------------
# evaluate_expectation
# ---------------------------------------------------------------------------


class TestEvaluateExpectation:
    """Tests for checking if a command result meets the expectation."""

    def test_exit_code_0_pass(self):
        assert evaluate_expectation("exit code 0", exit_code=0, output="") is True

    def test_exit_code_0_fail(self):
        assert evaluate_expectation("exit code 0", exit_code=1, output="") is False

    def test_exit_code_nonzero(self):
        assert evaluate_expectation("exit code 1", exit_code=1, output="") is True
        assert evaluate_expectation("exit code 1", exit_code=0, output="") is False

    def test_output_gt_pass(self):
        assert evaluate_expectation("output > 0", exit_code=0, output="3") is True

    def test_output_gt_fail(self):
        assert evaluate_expectation("output > 0", exit_code=0, output="0") is False

    def test_output_gt_non_numeric(self):
        assert evaluate_expectation("output > 0", exit_code=0, output="abc") is False

    def test_output_contains_pass(self):
        assert (
            evaluate_expectation("output contains hello", exit_code=0, output="say hello world")
            is True
        )

    def test_output_contains_fail(self):
        assert (
            evaluate_expectation("output contains hello", exit_code=0, output="say goodbye")
            is False
        )

    def test_output_contains_case_sensitive(self):
        assert (
            evaluate_expectation("output contains Hello", exit_code=0, output="hello world")
            is False
        )

    def test_unknown_expectation_returns_false(self):
        assert evaluate_expectation("something weird", exit_code=0, output="ok") is False


# ---------------------------------------------------------------------------
# Inverse / anti-criteria forms
# ---------------------------------------------------------------------------


class TestEvaluateExpectationInverse:
    """Tests for the three inverse (anti-criteria) expectation forms."""

    # --- exit code != N ---

    def test_exit_code_ne_pass(self):
        """Passes when exit code differs from N."""
        assert evaluate_expectation("exit code != 0", exit_code=1, output="") is True
        assert evaluate_expectation("exit code != 0", exit_code=2, output="") is True

    def test_exit_code_ne_fail(self):
        """Fails when exit code equals N (command should have failed but succeeded)."""
        assert evaluate_expectation("exit code != 0", exit_code=0, output="") is False

    def test_exit_code_ne_nonzero_n(self):
        """Works for N != 0 too."""
        assert evaluate_expectation("exit code != 2", exit_code=0, output="") is True
        assert evaluate_expectation("exit code != 2", exit_code=2, output="") is False

    def test_exit_code_ne_grammar_collision_regression(self):
        """Regression: 'exit code != 0' must be evaluated by the inverse branch,
        NOT silently fall through to the positive 'exit code N' branch.

        The positive regex r'exit code (\\d+)' cannot match 'exit code != 0' because
        '!' is not a digit, so without the inverse branch this row would hit the
        safety default (False). This test confirms the inverse branch is reached
        and evaluates correctly.
        """
        # exit_code=0 should FAIL (code matches the forbidden value)
        assert evaluate_expectation("exit code != 0", exit_code=0, output="") is False
        # exit_code=1 should PASS (code differs from forbidden value)
        assert evaluate_expectation("exit code != 0", exit_code=1, output="") is True

    # --- output does not contain X ---

    def test_output_does_not_contain_pass(self):
        """Passes when substring is absent and stdout is non-empty."""
        assert (
            evaluate_expectation(
                "output does not contain DROP TABLE",
                exit_code=0,
                output="SELECT * FROM users",
            )
            is True
        )

    def test_output_does_not_contain_fail_present(self):
        """Fails when the forbidden substring is present in output."""
        assert (
            evaluate_expectation(
                "output does not contain DROP TABLE",
                exit_code=0,
                output="ALTER TABLE; DROP TABLE users;",
            )
            is False
        )

    def test_output_does_not_contain_empty_stdout_gate(self):
        """Empty stdout must NOT false-pass (empty-stdout gate).

        An errored command or one that wrote only to stderr produces empty stdout.
        Without the gate, 'not in ""' is trivially True and would silently pass.
        """
        assert (
            evaluate_expectation(
                "output does not contain FORBIDDEN",
                exit_code=1,
                output="",
            )
            is False
        )
        # Whitespace-only stdout also triggers the gate
        assert (
            evaluate_expectation(
                "output does not contain FORBIDDEN",
                exit_code=0,
                output="   \n  ",
            )
            is False
        )

    def test_output_does_not_contain_ordering_regression(self):
        """Regression: 'output does not contain X' must NOT be captured by the positive
        'output contains (.+)' branch.

        The phrase 'output does not contain FOO' contains the literal substring
        'contains FOO'. A loosely-anchored positive matcher could greedily capture it
        and evaluate the wrong assertion. This test pins the ordering: the inverse
        form is reached when the forbidden substring is absent (True) and present (False).
        """
        # FOO absent → inverse branch → True
        assert (
            evaluate_expectation(
                "output does not contain FOO",
                exit_code=0,
                output="all clean, no matches",
            )
            is True
        )
        # FOO present → inverse branch → False (not positive branch which would be True)
        assert (
            evaluate_expectation(
                "output does not contain FOO",
                exit_code=0,
                output="found FOO in file",
            )
            is False
        )

    # --- match count == 0 ---

    def test_match_count_zero_bare_zero(self):
        """grep -c PATTERN file → emits literal '0', exit 1 → passes."""
        assert evaluate_expectation("match count == 0", exit_code=1, output="0") is True

    def test_match_count_zero_whitespace_zero(self):
        """grep -r PATTERN dir | wc -l → emits '       0' (leading whitespace) → passes."""
        assert evaluate_expectation("match count == 0", exit_code=0, output="       0") is True

    def test_match_count_zero_single_path_colon_zero(self):
        """grep -rc PATTERN file → emits 'path/to/file:0' → passes."""
        assert (
            evaluate_expectation("match count == 0", exit_code=1, output="path/to/file:0") is True
        )

    def test_match_count_zero_multiline_path_colon_zero(self):
        """grep -rc PATTERN dir → emits multiple 'path:0' lines → passes."""
        output = "a.txt:0\nb.txt:0\nc.py:0"
        assert evaluate_expectation("match count == 0", exit_code=1, output=output) is True

    def test_match_count_zero_nonzero_count_fails(self):
        """Any non-zero count fails."""
        assert evaluate_expectation("match count == 0", exit_code=0, output="3") is False
        assert evaluate_expectation("match count == 0", exit_code=0, output="path:3") is False

    def test_match_count_zero_mixed_lines_fails(self):
        """Mixed zero and non-zero lines — one non-zero line must fail the whole check."""
        output = "a.txt:0\nb.txt:2"
        assert evaluate_expectation("match count == 0", exit_code=1, output=output) is False

    def test_match_count_zero_empty_stdout_gate(self):
        """Empty/whitespace-only stdout must NOT vacuously pass (empty-stdout gate).

        all(...) over an empty list is True in Python; without the gate, a command
        that errored or wrote only to stderr would produce empty stdout and pass.
        """
        assert evaluate_expectation("match count == 0", exit_code=1, output="") is False
        assert evaluate_expectation("match count == 0", exit_code=0, output="   \n") is False

    def test_match_count_zero_literal_zero_passes_not_gated(self):
        """Literal '0' (non-empty stdout) must NOT be blocked by the empty-stdout gate.

        This confirms the gate fires only on truly-empty output, not on a legitimately-
        clean grep -c result.
        """
        assert evaluate_expectation("match count == 0", exit_code=1, output="0") is True

    # --- positive forms unchanged (regression) ---

    def test_positive_exit_code_still_works(self):
        assert evaluate_expectation("exit code 0", exit_code=0, output="") is True
        assert evaluate_expectation("exit code 0", exit_code=1, output="") is False
        assert evaluate_expectation("exit code 1", exit_code=1, output="") is True

    def test_positive_output_contains_still_works(self):
        assert evaluate_expectation("output contains ok", exit_code=0, output="all ok") is True
        assert evaluate_expectation("output contains ok", exit_code=0, output="bad") is False

    def test_positive_output_gt_still_works(self):
        assert evaluate_expectation("output > 0", exit_code=0, output="3") is True
        assert evaluate_expectation("output > 0", exit_code=0, output="0") is False


# ---------------------------------------------------------------------------
# Pipe handling in commands (#2570)
# ---------------------------------------------------------------------------


class TestPipesInCommands:
    """Rows split on unescaped `|` only; `\\|` is a literal pipe.

    Before this, every row split on every `|`: a pipe-bearing command was
    truncated at the pipe, the Expected cell received the fragment after it,
    and the real expectation was discarded. The check then ran a command its
    author never wrote and failed for an unrelated reason -- attributing a
    parse error to the code under test. Both `\\|` and a bare `|` truncated, so
    there was no working form at all.

    A scan of docs/plans/ found 544 rows across 251 plans with the truncation
    signature. Escape-aware splitting recovers 502 of them; the remaining 42
    carry a genuinely bare `|` and are now rejected loudly instead of executed
    as something else.
    """

    def _table(self, row: str) -> str:
        return f"## Verification\n\n| Check | Command | Expected |\n|--|--|--|\n{row}\n"

    def test_shell_pipeline_is_expressible(self):
        table = self._table("| Count | `grep -r X dir \\| wc -l` | match count == 0 |")
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert parsed.checks[0].command == "grep -r X dir | wc -l"
        assert parsed.checks[0].expected == "match count == 0"

    def test_regex_alternation_is_expressible(self):
        table = self._table("| Anti | `grep -cE 'a\\|b' src.py` | match count == 0 |")
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert parsed.checks[0].command == "grep -cE 'a|b' src.py"

    def test_python_bitwise_or_is_expressible(self):
        table = self._table('| Flags | `python -c "import re; print(re.S\\|re.M)"` | exit code 0 |')
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert "re.S|re.M" in parsed.checks[0].command

    def test_the_issue_2570_worked_example(self):
        """The exact row from the issue, which parsed to a broken command and a
        garbage expectation, then failed with a shell syntax error."""
        table = self._table(
            "| Anti-criterion: sdlc not hardcoded | "
            '`! grep -nE \'"sdlc/"\\|/ "sdlc"\' scripts/update/hardlinks.py` | exit code 0 |'
        )
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert (
            parsed.checks[0].command
            == '! grep -nE \'"sdlc/"|/ "sdlc"\' scripts/update/hardlinks.py'
        )
        assert parsed.checks[0].expected == "exit code 0"

    def test_bare_pipe_is_rejected_not_truncated(self):
        """The unescaped form has no unambiguous reading, so it must be
        reported as an authoring error rather than executed as a guess."""
        table = self._table("| Anti | `grep -cE 'a|b' src.py` | match count == 0 |")
        parsed = parse_verification_table(table)
        assert parsed.checks == []
        assert len(parsed.malformed) == 1
        assert "unescaped `|`" in parsed.malformed[0].reason
        assert "\\|" in parsed.malformed[0].reason  # names the remedy

    def test_pipe_free_control_still_parses(self):
        """The currently-passing control from the issue's table."""
        table = self._table("| Tests pass | `pytest -q` | exit code 0 |")
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert parsed.checks[0].command == "pytest -q"

    def test_empty_cell_is_rejected_not_silently_dropped(self):
        table = self._table("| Named check | | exit code 0 |")
        parsed = parse_verification_table(table)
        assert parsed.checks == []
        assert len(parsed.malformed) == 1

    def test_column_count_comes_from_the_header(self):
        """One plan in docs/plans/ carries a 4-column Verification table.
        Hardcoding 3 would reject every one of its rows."""
        table = (
            "## Verification\n\n"
            "| Check | Command | Expected | Notes |\n|--|--|--|--|\n"
            "| Tests pass | `pytest -q` | exit code 0 | nightly |\n"
        )
        parsed = parse_verification_table(table)
        assert parsed.malformed == []
        assert parsed.checks[0].name == "Tests pass"
        assert parsed.checks[0].expected == "exit code 0"

    def test_split_row_cells_unescapes_and_preserves_interior_empties(self):
        assert split_row_cells("| a | b\\|c | d |") == ["a", "b|c", "d"]
        assert split_row_cells("| a |  | c |") == ["a", "", "c"]


class TestMalformedRowReporting:
    def test_format_results_names_authoring_errors_separately(self):
        rows = [MalformedRow(line="| bad | row |", reason="expected 3 columns, got 2.")]
        table = ParsedTable(checks=[], malformed=rows, skipped=[])
        report = format_results([], table)
        assert "Plan authoring errors (1)" in report
        assert "fix the plan, not the code" in report.lower()
        assert "| bad | row |" in report

    def test_a_malformed_row_fails_the_run(self):
        """A row nobody can execute is not a passing check."""
        check = VerificationCheck(name="ok", command="true", expected="exit code 0")
        results = [CheckResult(check=check, passed=True, exit_code=0, output="")]
        clean_table = ParsedTable(checks=[check], malformed=[], skipped=[])
        assert "All checks passed." in format_results(results, clean_table)
        malformed_table = ParsedTable(
            checks=[check], malformed=[MalformedRow(line="| x |", reason="r")], skipped=[]
        )
        report = format_results(results, malformed_table)
        assert "All checks passed." not in report
        assert "could not be parsed" in report


# ---------------------------------------------------------------------------
# Per-block table scoping (#2836)
# ---------------------------------------------------------------------------


class TestPerBlockTableScoping:
    """Every pipe-block in `## Verification` is classified independently: a
    check table (Command column among its first three) contributes checks, a
    non-check table is skipped and non-failing, and a section whose tables
    carry no Command column fails loudly with one MalformedRow per block."""

    def test_summary_table_does_not_become_checks(self):
        """The #2741 fixture: a real check table plus a `Row | Pre-change |
        Meaning` summary table. Before per-block scoping this parsed to 27
        checks (16 real + 11 summary-table junk rows, each a guaranteed FAIL).
        """
        text = (FIXTURES_DIR / "2741_pre_fix_verification.md").read_text()
        table = parse_verification_table(text)
        assert len(table.checks) == 16
        assert len(table.malformed) == 0
        assert len(table.skipped) == 1
        assert "Pre-change" in table.skipped[0].header

    def test_second_anti_criterion_table_is_parsed(self):
        """spike-1: a genuine second check table (`Anti-criterion | Command |
        Expected`) must still contribute its rows -- first-table-only scoping
        would drop them, which is strictly worse than the bug being fixed."""
        text = (FIXTURES_DIR / "two_check_tables.md").read_text()
        table = parse_verification_table(text)
        assert len(table.checks) == 4
        assert len(table.malformed) == 0
        assert len(table.skipped) == 0

    def test_pipe_rows_with_no_command_column_are_malformed(self):
        """A section whose only pipe-block has no Command column yields
        exactly one MalformedRow whose `line` is that block's header, and
        `format_results` reports the run as failed."""
        text = (FIXTURES_DIR / "no_command_column.md").read_text()
        table = parse_verification_table(text)
        assert table.checks == []
        assert len(table.malformed) == 1
        assert table.malformed[0].line == "| # | Criterion | Check |"
        assert table.skipped == []
        report = format_results([], table)
        assert "All checks passed." not in report

    def test_no_command_column_yields_one_malformed_per_block(self):
        """Two non-check pipe-blocks in one section yield exactly two
        MalformedRows, regardless of how many data rows each block holds."""
        md = (
            "## Verification\n\n"
            "| # | Criterion | Check |\n|--|--|--|\n"
            "| 1 | one | manual |\n| 2 | two | manual |\n| 3 | three | manual |\n"
            "\n"
            "| Row | Pre-change | Meaning |\n|--|--|--|\n"
            "| a | 1 | red |\n"
        )
        table = parse_verification_table(md)
        assert table.checks == []
        assert len(table.malformed) == 2
        assert table.skipped == []

    def test_skipped_table_does_not_fail_the_run(self):
        """Checks all pass + one skipped block -> format_results reports all
        passed. The skipped block's own status is never PASS/FAIL/SKIP."""
        text = (FIXTURES_DIR / "check_plus_summary.md").read_text()
        table = parse_verification_table(text)
        assert len(table.checks) == 2
        assert len(table.skipped) == 1
        results = [
            CheckResult(check=c, passed=True, exit_code=0, output="ok") for c in table.checks
        ]
        report = format_results(results, table)
        assert "All checks passed." in report

    def test_every_fixture_declares_the_verification_heading(self):
        """Without a literal `## Verification` heading the section regex
        matches nothing and every fixture-backed assertion above would pass
        against an empty parse -- this test fails loudly instead."""
        fixture_files = sorted(FIXTURES_DIR.glob("*.md"))
        assert fixture_files, "no fixtures found under tests/fixtures/verification/"
        for f in fixture_files:
            assert f.read_text().lstrip().startswith("## Verification"), (
                f"{f} does not start with a literal '## Verification' heading"
            )

    def test_skipped_table_is_importable(self):
        assert SkippedTable(header="| a | b |", row_count=1, reason="r").header == "| a | b |"

    def test_parsed_table_carries_skipped_field(self):
        assert "skipped" in ParsedTable.__dataclass_fields__
