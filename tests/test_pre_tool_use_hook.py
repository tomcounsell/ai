"""Tests for pre_tool_use.py capture_git_baseline_once warning on exception.

The pre_tool_use hook is a standalone script with non-standard imports.
We test the baseline capture logic directly rather than importing the hook module.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def session_dir(tmp_path):
    """Create a temp session directory structure."""
    d = tmp_path / "data" / "sessions" / "test-session-123"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def hook_input():
    """Minimal hook input with a session_id."""
    return {"session_id": "test-session-123"}


class TestCaptureGitBaselineWarning:
    """Test that capture_git_baseline_once logs warnings instead of silently swallowing."""

    def test_warning_on_subprocess_failure(self, tmp_path, hook_input, capsys):
        """When git subprocess fails, a HOOK WARNING is printed to stderr."""
        # Import the hook by adding its directory to sys.path
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        # Patch get_project_dir to return our tmp_path so baseline_path
        # won't already exist
        with patch("pre_tool_use.get_project_dir", return_value=tmp_path):
            # Patch subprocess.run to raise an exception
            with patch(
                "pre_tool_use.subprocess.run",
                side_effect=OSError("git not found"),
            ):
                capture_git_baseline_once(hook_input)

        captured = capsys.readouterr()
        assert "HOOK WARNING" in captured.err
        assert "test-session-123" in captured.err
        assert "git not found" in captured.err

    def test_warning_on_json_write_failure(self, tmp_path, hook_input, capsys):
        """When JSON write fails, a HOOK WARNING is printed to stderr."""
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        with patch("pre_tool_use.get_project_dir", return_value=tmp_path):
            # Make the baseline_dir path a file so mkdir fails
            baseline_dir = tmp_path / "data" / "sessions" / "test-session-123"
            baseline_dir.parent.mkdir(parents=True, exist_ok=True)
            # Create a file where the directory should be, so mkdir fails
            baseline_dir.write_text("block")

            capture_git_baseline_once(hook_input)

        captured = capsys.readouterr()
        assert "HOOK WARNING" in captured.err
        assert "test-session-123" in captured.err

    def test_no_crash_on_exception(self, tmp_path, hook_input):
        """Function does not raise even when an exception occurs (fire-and-forget)."""
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        with patch("pre_tool_use.get_project_dir", return_value=tmp_path):
            with patch(
                "pre_tool_use.subprocess.run",
                side_effect=RuntimeError("unexpected"),
            ):
                # Should not raise
                capture_git_baseline_once(hook_input)

    def test_no_warning_on_success(self, tmp_path, hook_input, capsys):
        """When baseline capture succeeds, no warning is printed."""
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        mock_result = type("Result", (), {"stdout": "file.py\n", "returncode": 0})()

        with patch("pre_tool_use.get_project_dir", return_value=tmp_path):
            with patch("pre_tool_use.subprocess.run", return_value=mock_result):
                capture_git_baseline_once(hook_input)

        captured = capsys.readouterr()
        assert "HOOK WARNING" not in captured.err

        # Verify baseline was actually written
        baseline_path = tmp_path / "data" / "sessions" / "test-session-123" / "git_baseline.json"
        assert baseline_path.exists()
        data = json.loads(baseline_path.read_text())
        assert "file.py" in data

    def test_skips_when_no_session_id(self, tmp_path, capsys):
        """When session_id is empty, function returns early without warning."""
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        capture_git_baseline_once({"session_id": ""})

        captured = capsys.readouterr()
        assert "HOOK WARNING" not in captured.err

    def test_skips_when_baseline_already_exists(self, tmp_path, hook_input, capsys):
        """When baseline already exists, function returns early."""
        hooks_dir = str(Path(__file__).parent.parent / ".claude" / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)

        from pre_tool_use import capture_git_baseline_once

        with patch("pre_tool_use.get_project_dir", return_value=tmp_path):
            # Create the baseline file beforehand
            baseline_dir = tmp_path / "data" / "sessions" / "test-session-123"
            baseline_dir.mkdir(parents=True, exist_ok=True)
            baseline_path = baseline_dir / "git_baseline.json"
            baseline_path.write_text("[]")

            capture_git_baseline_once(hook_input)

        captured = capsys.readouterr()
        assert "HOOK WARNING" not in captured.err
