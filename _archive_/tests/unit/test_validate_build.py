"""Unit tests for scripts/validate_build.py."""

# Import the module under test
import importlib.util
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.verification_parser import (
    MalformedRow,
    ParsedTable,
    SkippedTable,
    VerificationCheck,
    parse_verification_table,
    run_checks,
)

spec = importlib.util.spec_from_file_location(
    "validate_build", Path(__file__).parents[2] / "scripts" / "validate_build.py"
)
validate_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_build)

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "verification"


class TestExtractSection:
    def test_extracts_verification_section(self):
        plan = textwrap.dedent("""\
            ## Solution
            Some solution text.

            ## Verification
            | Check | Command | Expected |
            |-------|---------|----------|
            | test  | `echo hi` | exit code 0 |

            ## Success Criteria
            - [ ] Something works
        """)
        section = validate_build.extract_section(plan, "Verification")
        assert "| Check |" in section
        assert "echo hi" in section

    def test_returns_empty_for_missing_section(self):
        plan = "## Solution\nSome text.\n"
        assert validate_build.extract_section(plan, "Verification") == ""

    def test_extracts_success_criteria(self):
        plan = textwrap.dedent("""\
            ## Success Criteria
            - [ ] `pytest tests/ -x -q` passes
            - [ ] Feature works

            ## Rabbit Holes
            Don't go here.
        """)
        section = validate_build.extract_section(plan, "Success Criteria")
        assert "pytest" in section
        assert "Rabbit Holes" not in section


class TestParseFileAssertions:
    def test_create_assertion(self):
        plan = "- [ ] Create `scripts/validate_build.py` for validation\n"
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 1
        assert assertions[0]["action"] == "exists"
        assert assertions[0]["path"] == "scripts/validate_build.py"

    def test_delete_assertion(self):
        plan = "- [ ] Delete `config/old_config.json` after migration\n"
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 1
        assert assertions[0]["action"] == "not_exists"
        assert assertions[0]["path"] == "config/old_config.json"

    def test_update_assertion(self):
        plan = "- [x] Update `docs/features/README.md` index table\n"
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 1
        assert assertions[0]["action"] == "modified"

    def test_no_assertions_in_plain_text(self):
        plan = "- [ ] Make sure the tests pass\n- [ ] Review the code\n"
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 0

    def test_add_assertion(self):
        plan = "- [ ] Add `docs/features/my-feature.md` describing the feature\n"
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 1
        assert assertions[0]["action"] == "exists"

    def test_multiple_assertions(self):
        plan = textwrap.dedent("""\
            - [ ] Create `scripts/new.py` for new feature
            - [ ] Delete `scripts/old.py` no longer needed
            - [x] Update `docs/README.md` with new info
        """)
        assertions = validate_build.parse_file_assertions(plan)
        assert len(assertions) == 3


class TestParseVerificationTable:
    """`validate_build.py` carries no table parser of its own (#2836/#2843);
    `agent.verification_parser.parse_verification_table` is the sole
    definition, imported directly here. Returns a `ParsedTable`, not a
    `list[dict]`."""

    def test_parses_standard_table(self):
        plan = textwrap.dedent("""\
            ## Verification

            | Check | Command | Expected |
            |-------|---------|----------|
            | Tests pass | `pytest tests/ -x -q` | exit code 0 |
            | Lint clean | `python -m ruff check .` | exit code 0 |
        """)
        table = parse_verification_table(plan)
        assert len(table.checks) == 2
        assert table.checks[0].name == "Tests pass"
        assert table.checks[0].command == "pytest tests/ -x -q"
        assert table.checks[0].expected == "exit code 0"

    def test_no_verification_section(self):
        plan = "## Solution\nSome text.\n"
        table = parse_verification_table(plan)
        assert table.checks == []
        assert table.malformed == []
        assert table.skipped == []

    def test_verification_without_table(self):
        plan = textwrap.dedent("""\
            ## Verification

            Just some text, no table here.
        """)
        table = parse_verification_table(plan)
        assert table.checks == []
        assert table.malformed == []
        assert table.skipped == []


class TestParseSuccessCriteriaCommands:
    def test_extracts_runnable_commands(self):
        plan = textwrap.dedent("""\
            ## Success Criteria
            - [ ] `pytest tests/ -x -q` passes
            - [ ] Feature is documented
            - [ ] `python -m ruff check .` is clean
        """)
        criteria = validate_build.parse_success_criteria_commands(plan)
        assert len(criteria) == 2
        assert criteria[0]["command"] == "pytest tests/ -x -q"
        assert criteria[1]["command"] == "python -m ruff check ."

    def test_ignores_non_command_backticks(self):
        plan = textwrap.dedent("""\
            ## Success Criteria
            - [ ] The `status` field is set correctly
            - [ ] Use `MyClass` for implementation
        """)
        criteria = validate_build.parse_success_criteria_commands(plan)
        assert len(criteria) == 0

    def test_no_success_criteria(self):
        plan = "## Solution\nSome text.\n"
        criteria = validate_build.parse_success_criteria_commands(plan)
        assert len(criteria) == 0


class TestCheckFileAssertions:
    def test_existing_file_passes(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("content")
        assertions = [{"action": "exists", "path": str(f), "source": "Create test.py"}]
        results = validate_build.check_file_assertions(assertions)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_missing_file_fails(self):
        assertions = [
            {
                "action": "exists",
                "path": "/nonexistent/file.py",
                "source": "Create file.py",
            }
        ]
        results = validate_build.check_file_assertions(assertions)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"

    def test_deleted_file_passes(self):
        assertions = [
            {
                "action": "not_exists",
                "path": "/nonexistent/file.py",
                "source": "Delete file.py",
            }
        ]
        results = validate_build.check_file_assertions(assertions)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_not_deleted_file_fails(self, tmp_path):
        f = tmp_path / "still_here.py"
        f.write_text("content")
        assertions = [
            {
                "action": "not_exists",
                "path": str(f),
                "source": "Delete still_here.py",
            }
        ]
        results = validate_build.check_file_assertions(assertions)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"


class TestCheckVerificationTable:
    """`check_verification_table` now takes a `ParsedTable` (#2836/#2843)."""

    def test_passing_command(self):
        check = VerificationCheck(name="echo test", command="echo hello", expected="exit code 0")
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_failing_command(self):
        check = VerificationCheck(name="false cmd", command="false", expected="exit code 0")
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"

    def test_output_check(self):
        """The bare `output <exact>` form is deleted (#2843/spike-5, measured
        zero usage in active plans). `output > N` is what the row shape was
        standing in for."""
        check = VerificationCheck(name="output check", command="echo 1", expected="output > 0")
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_timeout_skips(self):
        check = VerificationCheck(name="slow cmd", command="sleep 60", expected="exit code 0")
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("sleep", 30),
        ):
            results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "SKIP"

    def test_malformed_row_fails(self):
        table = ParsedTable(
            checks=[], malformed=[MalformedRow(line="| bad |", reason="r")], skipped=[]
        )
        results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"
        assert "fix the plan, not the code" in results[0]["message"]

    def test_skipped_table_is_info_not_counted(self):
        """A SkippedTable is reported but never PASS/FAIL/SKIP, so it cannot
        change the exit code (#2836)."""
        skip = SkippedTable(header="| a | b | c |", row_count=1, reason="no Command column")
        table = ParsedTable(checks=[], malformed=[], skipped=[skip])
        results = validate_build.check_verification_table(table)
        assert len(results) == 1
        assert results[0]["status"] == "INFO"


class TestCheckSuccessCriteria:
    def test_passing_criterion(self):
        criteria = [{"command": "true", "source": "should pass"}]
        results = validate_build.check_success_criteria(criteria)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_failing_criterion(self):
        criteria = [{"command": "false", "source": "should fail"}]
        results = validate_build.check_success_criteria(criteria)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"


class TestMainEdgeCases:
    def test_help_flag(self):
        with patch("sys.argv", ["validate_build.py", "--help"]):
            assert validate_build.main() == 0

    def test_missing_plan_file(self, tmp_path):
        nonexistent = str(tmp_path / "missing.md")
        with patch("sys.argv", ["validate_build.py", nonexistent]):
            assert validate_build.main() == 0

    def test_empty_plan_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        with patch("sys.argv", ["validate_build.py", str(f)]):
            assert validate_build.main() == 0

    def test_plan_with_only_checked_items(self, tmp_path):
        f = tmp_path / "done.md"
        f.write_text(
            textwrap.dedent("""\
            ## Success Criteria
            - [x] Everything is done
            - [x] All tests pass
        """)
        )
        with patch("sys.argv", ["validate_build.py", str(f)]):
            # No runnable commands in checked items -> nothing to validate -> exit 0
            assert validate_build.main() == 0

    def test_plan_with_no_checkboxes(self, tmp_path):
        f = tmp_path / "no_checkboxes.md"
        f.write_text(
            textwrap.dedent("""\
            ## Solution
            Just some prose about the solution.

            ## Verification
            No table here, just text.
        """)
        )
        with patch("sys.argv", ["validate_build.py", str(f)]):
            assert validate_build.main() == 0

    def test_malformed_verification_table(self, tmp_path, capsys):
        """A row that cannot be read must FAIL, not be silently skipped (#2570).

        This previously asserted exit 0 -- "nothing parseable, so nothing to
        say". That silence is the defect: an unrunnable row and a passing check
        were indistinguishable in the exit code.
        """
        f = tmp_path / "malformed.md"
        f.write_text(
            textwrap.dedent("""\
            ## Verification

            | Check | Command |
            |-------|---------|
            | incomplete row |
        """)
        )
        with patch("sys.argv", ["validate_build.py", str(f)]):
            assert validate_build.main() == 1
        out = capsys.readouterr().out
        assert "MALFORMED VERIFICATION ROW" in out
        assert "fix the plan, not the code" in out

    def test_pipe_bearing_command_runs_intact(self, tmp_path):
        """The command class this parser could not express at all (#2570):
        a shell pipeline. `\\|` in the table reaches the shell as a real pipe,
        so `printf 'a\\nb\\n' | wc -l` emits 2 rather than truncating. This
        guarantee must survive delegation to the shared parser (#2836/#2843)."""
        f = tmp_path / "piped.md"
        f.write_text(
            textwrap.dedent("""\
            ## Verification

            | Check | Command | Expected |
            |-------|---------|----------|
            | Pipeline runs | `printf 'a\\nb\\n' \\| wc -l` | output contains 2 |
        """)
        )
        table = parse_verification_table(f.read_text())
        assert len(table.checks) == 1
        assert table.checks[0].command == "printf 'a\\nb\\n' | wc -l"
        assert table.checks[0].expected == "output contains 2"
        with patch("sys.argv", ["validate_build.py", str(f)]):
            assert validate_build.main() == 0

    def test_output_gt_passes(self):
        """`output > N` reaches the `startswith("output ")` branch under the
        deleted evaluator and string-compares stdout against '> 0' -- never
        true. Fails today (#2843)."""
        check = VerificationCheck(name="gt check", command="echo 1", expected="output > 0")
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert results[0]["status"] == "PASS"

    def test_match_count_zero_clean_passes(self):
        """A satisfied anti-criterion (`grep -c` prints '0', exits 1) hits the
        deleted evaluator's flexible fallback and reports FAIL on a clean
        tree. Fails today (#2843)."""
        check = VerificationCheck(
            name="clean grep", command="grep -c zzz /dev/null", expected="match count == 0"
        )
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert results[0]["status"] == "PASS"

    def test_violated_anti_criterion_fails(self):
        """A violated anti-criterion (`grep -c` prints '24', exits 0) hits the
        deleted evaluator's `actual_exit == 0` fallback and reports PASS.
        This is #2783 Severity-1. Fails today (reports PASS)."""
        check = VerificationCheck(
            name="violated grep",
            command="printf 'a\\na\\n' | grep -c a",
            expected="match count == 0",
        )
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        results = validate_build.check_verification_table(table)
        assert results[0]["status"] == "FAIL"

    def test_trailing_newline_parity_with_run_checks(self):
        """A command whose stdout is '0\\n' must evaluate identically through
        `validate_build`'s loop and `run_checks` -- both must pass the same
        unstripped stdout to `evaluate_expectation`, or the stripped copy
        re-creates the exact divergence this lane closes."""
        check = VerificationCheck(
            name="trailing newline", command="printf '0\\n'", expected="match count == 0"
        )
        table = ParsedTable(checks=[check], malformed=[], skipped=[])
        vb_results = validate_build.check_verification_table(table)
        rc_results = run_checks([check])
        assert vb_results[0]["status"] == "PASS"
        assert rc_results[0].passed is True


# ---------------------------------------------------------------------------
# Cross-runner agreement (T13). Split by role, never execute the parse-only
# fixtures: `runner_agreement.md` is the sole execution fixture (instantaneous,
# hermetic commands); the other four are real plan-text excerpts compared
# through the parser alone. One of them nests a real invocation of the
# repo's xdist-aware test-runner wrapper, whose margin under this module's
# 30s SKIP ceiling would go red under load for a reason unrelated to the
# defect this guard exists for -- so its commands are never run here.
# ---------------------------------------------------------------------------

PARSE_ONLY_FIXTURES = [
    "2741_pre_fix_verification.md",
    "two_check_tables.md",
    "no_command_column.md",
    "check_plus_summary.md",
]


EXPECTED_PARSE_ONLY_SHAPES = {
    "2741_pre_fix_verification.md": {"checks": 16, "malformed": 0, "skipped": 1},
    "two_check_tables.md": {"checks": 4, "malformed": 0, "skipped": 0},
    "no_command_column.md": {"checks": 0, "malformed": 1, "skipped": 0},
    "check_plus_summary.md": {"checks": 2, "malformed": 0, "skipped": 1},
}


class TestCrossRunnerAgreement:
    def test_both_runners_agree_on_execution_fixture(self):
        p = FIXTURES_DIR / "runner_agreement.md"
        assert p.read_text().lstrip().startswith("## Verification")
        table = parse_verification_table(p.read_text())
        assert table.malformed == []
        assert len(table.checks) == 6

        vb_results = validate_build.check_verification_table(table)
        rc_results = run_checks(table.checks)

        assert len(vb_results) == len(rc_results) == len(table.checks)
        for vb, rc, check in zip(vb_results, rc_results, table.checks, strict=True):
            vb_passed = vb["status"] == "PASS"
            assert vb_passed == rc.passed, (
                f"{check.name}: validate_build={vb['status']!r} run_checks.passed={rc.passed!r}"
            )

    @pytest.mark.parametrize("fixture_name", PARSE_ONLY_FIXTURES)
    def test_parse_only_fixtures_parse_identically(self, fixture_name):
        """Parsed through `parse_verification_table` alone -- commands in
        these fixtures are never executed by this test. Each fixture's shape
        is pinned to the exact figures spike-1/spike-6 measured, so a future
        change to table scoping that shifts any of these fixtures is caught
        here, independently of the parser's own unit tests."""
        p = FIXTURES_DIR / fixture_name
        assert p.read_text().lstrip().startswith("## Verification")
        table = parse_verification_table(p.read_text())
        expected = EXPECTED_PARSE_ONLY_SHAPES[fixture_name]
        assert len(table.checks) == expected["checks"]
        assert len(table.malformed) == expected["malformed"]
        assert len(table.skipped) == expected["skipped"]
