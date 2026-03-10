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
