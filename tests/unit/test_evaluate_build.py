"""Unit tests for scripts/evaluate_build.py.

Tests section parsing, verdict formatting, exit code logic, and
CLI behavior without making real API calls.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts to path for direct imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import evaluate_build
from evaluate_build import (
    _make_dry_run_verdicts,
    extract_section,
    get_git_diff,
)

PLAN_WITH_AC = """\
---
status: In Progress
---

# Feature Plan

## Problem
This is a test problem.

## Acceptance Criteria

- [ ] The evaluator script exists at `scripts/evaluate_build.py`
- [ ] Running with --help exits 0 with usage text
- [ ] FAIL verdicts route to /do-patch

## Success Criteria

- [ ] `python scripts/evaluate_build.py --help` exits 0
"""

PLAN_WITHOUT_AC = """\
---
status: In Progress
---

# Feature Plan

## Problem
This is a test problem.

## Success Criteria

- [ ] `python scripts/evaluate_build.py --help` exits 0
"""

PLAN_WITH_EMPTY_AC = """\
---
status: In Progress
---

# Feature Plan

## Acceptance Criteria

## Success Criteria

- [ ] Something
"""


class TestExtractSection:
    """Tests for the extract_section utility function."""

    def test_extract_section_with_ac_section(self):
        """Parses criteria from a plan with ## Acceptance Criteria."""
        result = extract_section(PLAN_WITH_AC, "Acceptance Criteria")
        assert "evaluator script exists" in result
        assert "Running with --help exits 0" in result
        assert "FAIL verdicts route to /do-patch" in result

    def test_extract_section_without_ac_section(self):
        """Returns empty string when section is absent."""
        result = extract_section(PLAN_WITHOUT_AC, "Acceptance Criteria")
        assert result == ""

    def test_extract_section_returns_only_that_section(self):
        """Does not bleed into adjacent sections."""
        result = extract_section(PLAN_WITH_AC, "Acceptance Criteria")
        assert "Success Criteria" not in result
        assert "python scripts/evaluate_build.py --help" not in result

    def test_extract_section_empty_content(self):
        """Returns empty string when section header exists but no content."""
        result = extract_section(PLAN_WITH_EMPTY_AC, "Acceptance Criteria")
        # The section content is just whitespace
        assert result.strip() == ""


class TestDryRunVerdicts:
    """Tests for the --dry-run mock verdict generator."""

    def test_dry_run_returns_pass_for_each_criterion(self):
        """Generates a PASS verdict for each non-empty criterion line."""
        criteria = """\
- [ ] The evaluator script exists
- [ ] Running with --help exits 0
- [ ] FAIL verdicts route to /do-patch
"""
        verdicts = _make_dry_run_verdicts(criteria)
        assert len(verdicts) == 3
        for v in verdicts:
            assert v["verdict"] == "PASS"
            assert "[dry-run]" in v["evidence"]

    def test_dry_run_strips_checkbox_markers(self):
        """Criterion text does not include the `- [ ]` prefix."""
        criteria = "- [ ] The evaluator script exists\n"
        verdicts = _make_dry_run_verdicts(criteria)
        assert len(verdicts) == 1
        assert verdicts[0]["criterion"] == "The evaluator script exists"

    def test_dry_run_skips_empty_lines(self):
        """Empty lines do not produce verdicts."""
        criteria = "\n\n- [ ] Only one criterion\n\n"
        verdicts = _make_dry_run_verdicts(criteria)
        assert len(verdicts) == 1


class TestExitCodes:
    """Tests for CLI exit code behavior."""

    def _run_script(self, args, cwd=None):
        """Helper to run evaluate_build.py as a subprocess."""
        result = subprocess.run(
            [sys.executable, "scripts/evaluate_build.py"] + args,
            capture_output=True,
            text=True,
            cwd=cwd or str(Path(__file__).parent.parent.parent),
        )
        return result

    def test_exit_code_0_on_help(self):
        """--help exits 0."""
        result = self._run_script(["--help"])
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_exit_code_3_on_missing_ac_section(self, tmp_path):
        """Runs evaluate_build.py on a temp plan file without AC section, exits 3."""
        plan_file = tmp_path / "no_ac_plan.md"
        plan_file.write_text(PLAN_WITHOUT_AC)

        result = subprocess.run(
            [sys.executable, "scripts/evaluate_build.py", str(plan_file)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 3, f"Expected exit 3, got {result.returncode}. stderr: {result.stderr}"
        assert "no Acceptance Criteria section" in result.stderr or "empty diff" in result.stderr

    def test_exit_code_3_on_empty_ac_section(self, tmp_path):
        """Runs evaluate_build.py on a plan with empty AC section, exits 3."""
        plan_file = tmp_path / "empty_ac_plan.md"
        plan_file.write_text(PLAN_WITH_EMPTY_AC)

        result = subprocess.run(
            [sys.executable, "scripts/evaluate_build.py", str(plan_file)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 3, f"Expected exit 3, got {result.returncode}. stderr: {result.stderr}"

    def test_exit_code_1_on_missing_plan(self):
        """Runs evaluate_build.py on a nonexistent file path, exits 1."""
        result = self._run_script(["/nonexistent/path/to/plan.md"])
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}. stderr: {result.stderr}"

    def test_exit_code_0_on_dry_run_with_ac(self, tmp_path):
        """Uses --dry-run flag on a plan with AC, exits 0 (all PASS)."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        # We need to run in the worktree or repo root so git diff runs
        # In dry-run mode with AC section, it still needs a non-empty diff
        # Mock get_git_diff to return non-empty diff
        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py b/foo.py\n+some change"):
            with patch.object(sys, "argv", ["evaluate_build.py", "--dry-run", str(plan_file)]):
                exit_code = evaluate_build.main()
        assert exit_code == 0


class TestVerdictFormatting:
    """Tests for verdict output formatting."""

    def test_verdict_output_is_valid_json(self, tmp_path):
        """evaluate_build.py outputs valid JSON to stdout."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        mock_verdicts = [
            {"criterion": "criterion 1", "verdict": "PASS", "evidence": "Found in diff."},
            {"criterion": "criterion 2", "verdict": "PARTIAL", "evidence": "Partially found."},
        ]

        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py b/foo.py\n+change"):
            with patch.object(evaluate_build, "evaluate_criteria", return_value=mock_verdicts):
                with patch.object(sys, "argv", ["evaluate_build.py", str(plan_file)]):
                    import io
                    from contextlib import redirect_stdout
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        exit_code = evaluate_build.main()

        output = captured.getvalue()
        data = json.loads(output)
        assert "verdicts" in data
        assert len(data["verdicts"]) == 2

    def test_partial_verdict_logs_warning(self, tmp_path, capsys):
        """PARTIAL verdicts emit WARNING to stderr."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        mock_verdicts = [
            {"criterion": "criterion 1", "verdict": "PARTIAL", "evidence": "Partially implemented."},
        ]

        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py\n+change"):
            with patch.object(evaluate_build, "evaluate_criteria", return_value=mock_verdicts):
                with patch.object(sys, "argv", ["evaluate_build.py", str(plan_file)]):
                    import io
                    from contextlib import redirect_stdout
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        exit_code = evaluate_build.main()

        assert exit_code == 0
        captured_err = capsys.readouterr()
        assert "PARTIAL" in captured_err.err

    def test_fail_verdict_returns_exit_2(self, tmp_path):
        """FAIL verdicts cause exit code 2."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        mock_verdicts = [
            {"criterion": "criterion 1", "verdict": "FAIL", "evidence": "Not found in diff."},
        ]

        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py\n+change"):
            with patch.object(evaluate_build, "evaluate_criteria", return_value=mock_verdicts):
                with patch.object(sys, "argv", ["evaluate_build.py", str(plan_file)]):
                    import io
                    from contextlib import redirect_stdout
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        exit_code = evaluate_build.main()

        assert exit_code == 2

    def test_api_error_returns_exit_1(self, tmp_path):
        """API errors are caught and return exit code 1 (non-blocking)."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py\n+change"):
            with patch.object(evaluate_build, "evaluate_criteria", side_effect=Exception("API timeout")):
                with patch.object(sys, "argv", ["evaluate_build.py", str(plan_file)]):
                    import io
                    from contextlib import redirect_stdout
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        exit_code = evaluate_build.main()

        assert exit_code == 1

    def test_json_decode_error_returns_exit_1(self, tmp_path):
        """JSON parse errors are caught and return exit code 1 (non-blocking)."""
        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text(PLAN_WITH_AC)

        with patch.object(evaluate_build, "get_git_diff", return_value="diff --git a/foo.py\n+change"):
            with patch.object(evaluate_build, "evaluate_criteria", side_effect=json.JSONDecodeError("bad", "", 0)):
                with patch.object(sys, "argv", ["evaluate_build.py", str(plan_file)]):
                    import io
                    from contextlib import redirect_stdout
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        exit_code = evaluate_build.main()

        assert exit_code == 1
