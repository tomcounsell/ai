"""Unit tests for scripts/validate_build.py."""

# Import the module under test
import importlib.util
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "validate_build", Path(__file__).parents[2] / "scripts" / "validate_build.py"
)
validate_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_build)


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
    def test_parses_standard_table(self):
        plan = textwrap.dedent("""\
            ## Verification

            | Check | Command | Expected |
            |-------|---------|----------|
            | Tests pass | `pytest tests/ -x -q` | exit code 0 |
            | Lint clean | `python -m ruff check .` | exit code 0 |
        """)
        checks = validate_build.parse_verification_table(plan)
        assert len(checks) == 2
        assert checks[0]["name"] == "Tests pass"
        assert checks[0]["command"] == "pytest tests/ -x -q"
        assert checks[0]["expected"] == "exit code 0"

    def test_no_verification_section(self):
        plan = "## Solution\nSome text.\n"
        checks = validate_build.parse_verification_table(plan)
        assert len(checks) == 0

    def test_verification_without_table(self):
        plan = textwrap.dedent("""\
            ## Verification

            Just some text, no table here.
        """)
        checks = validate_build.parse_verification_table(plan)
        assert len(checks) == 0


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
    def test_passing_command(self):
        checks = [{"name": "echo test", "command": "echo hello", "expected": "exit code 0"}]
        results = validate_build.check_verification_table(checks)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_failing_command(self):
        checks = [{"name": "false cmd", "command": "false", "expected": "exit code 0"}]
        results = validate_build.check_verification_table(checks)
        assert len(results) == 1
        assert results[0]["status"] == "FAIL"

    def test_output_check(self):
        checks = [
            {
                "name": "output check",
                "command": "echo 0",
                "expected": "output 0",
            }
        ]
        results = validate_build.check_verification_table(checks)
        assert len(results) == 1
        assert results[0]["status"] == "PASS"

    def test_timeout_skips(self):
        checks = [
            {
                "name": "slow cmd",
                "command": "sleep 60",
                "expected": "exit code 0",
            }
        ]
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("sleep", 30),
        ):
            results = validate_build.check_verification_table(checks)
        assert len(results) == 1
        assert results[0]["status"] == "SKIP"


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

    def test_malformed_verification_table(self, tmp_path):
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
            # Malformed table -> nothing parseable -> exit 0
            assert validate_build.main() == 0
