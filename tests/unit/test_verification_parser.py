"""Unit tests for agent/verification_parser.py -- machine-readable verification checks."""

from agent.verification_parser import (
    VerificationCheck,
    evaluate_expectation,
    parse_verification_table,
)

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
        checks = parse_verification_table(md)
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
        checks = parse_verification_table(md)
        assert len(checks) == 1
        assert checks[0].expected == "output > 0"

    def test_output_contains_expectation(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Module loads | `python -c "import foo; print(foo.__version__)"` | output contains foo |
"""
        checks = parse_verification_table(md)
        assert len(checks) == 1
        assert checks[0].expected == "output contains foo"

    def test_no_verification_section(self):
        md = """\
## Success Criteria

- [ ] Something
"""
        checks = parse_verification_table(md)
        assert checks == []

    def test_empty_table(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
"""
        checks = parse_verification_table(md)
        assert checks == []

    def test_ignores_separator_row(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |
"""
        checks = parse_verification_table(md)
        assert len(checks) == 1

    def test_strips_backticks_from_command(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hello` | exit code 0 |
"""
        checks = parse_verification_table(md)
        assert checks[0].command == "echo hello"

    def test_command_without_backticks(self):
        md = """\
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | echo hello | exit code 0 |
"""
        checks = parse_verification_table(md)
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
        checks = parse_verification_table(md)
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
        checks = parse_verification_table(md)
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
