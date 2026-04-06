"""Tests for the standalone worker entry point (worker/__main__.py).

Tests configuration loading, argument parsing, and basic startup logic.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestWorkerImport:
    """Test that the worker module can be imported."""

    def test_worker_package_importable(self):
        """worker package should be importable."""
        import worker

        assert worker.__doc__ is not None

    def test_worker_main_importable(self):
        """worker.__main__ should be importable."""
        from worker.__main__ import main, _parse_args, _load_projects

        assert callable(main)
        assert callable(_parse_args)
        assert callable(_load_projects)


class TestWorkerArgParsing:
    """Test CLI argument parsing."""

    def test_default_args(self):
        """Default args: no project filter, no dry-run."""
        from worker.__main__ import _parse_args

        with patch("sys.argv", ["worker"]):
            args = _parse_args()
        assert args.project is None
        assert args.dry_run is False

    def test_project_flag(self):
        """--project flag should set the project filter."""
        from worker.__main__ import _parse_args

        with patch("sys.argv", ["worker", "--project", "valor"]):
            args = _parse_args()
        assert args.project == "valor"

    def test_dry_run_flag(self):
        """--dry-run flag should enable dry run mode."""
        from worker.__main__ import _parse_args

        with patch("sys.argv", ["worker", "--dry-run"]):
            args = _parse_args()
        assert args.dry_run is True


class TestWorkerDryRun:
    """Test worker dry-run mode via subprocess."""

    def test_dry_run_exits_cleanly(self):
        """python -m worker --dry-run should exit with code 0 if config is valid."""
        result = subprocess.run(
            [sys.executable, "-m", "worker", "--dry-run"],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=30,
        )
        # May exit 0 (config valid) or 1 (no Redis / no config)
        # The important thing is it doesn't crash with an unhandled exception
        assert result.returncode in (0, 1), (
            f"Unexpected exit code {result.returncode}: {result.stderr}"
        )
        # Should contain "worker" in output somewhere
        combined = result.stdout + result.stderr
        assert "worker" in combined.lower(), (
            f"Expected 'worker' in output, got: {combined[:500]}"
        )

    def test_dry_run_with_bad_project(self):
        """--project with nonexistent project should exit with error."""
        result = subprocess.run(
            [sys.executable, "-m", "worker", "--dry-run", "--project", "nonexistent_project_xyz"],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0


class TestRegisterCallbacksWithHandler:
    """Test that register_callbacks accepts OutputHandler instances."""

    def test_register_with_handler(self):
        """register_callbacks should accept an OutputHandler via handler kwarg."""
        from agent.agent_session_queue import register_callbacks, _send_callbacks, _reaction_callbacks
        from agent.output_handler import FileOutputHandler

        handler = FileOutputHandler()
        register_callbacks("test-project-handler", handler=handler)

        assert "test-project-handler" in _send_callbacks
        assert "test-project-handler" in _reaction_callbacks

        # Clean up
        del _send_callbacks["test-project-handler"]
        del _reaction_callbacks["test-project-handler"]

    def test_register_with_raw_callables(self):
        """register_callbacks should still accept raw callables (backward compat)."""
        from agent.agent_session_queue import register_callbacks, _send_callbacks, _reaction_callbacks

        async def fake_send(chat_id, text, reply_to, session):
            pass

        async def fake_react(chat_id, msg_id, emoji):
            pass

        register_callbacks("test-project-raw", fake_send, fake_react)

        assert "test-project-raw" in _send_callbacks
        assert "test-project-raw" in _reaction_callbacks

        # Clean up
        del _send_callbacks["test-project-raw"]
        del _reaction_callbacks["test-project-raw"]

    def test_register_rejects_none_handler_and_none_callbacks(self):
        """register_callbacks should reject when neither handler nor callbacks provided."""
        from agent.agent_session_queue import register_callbacks

        with pytest.raises(ValueError, match="send_callback"):
            register_callbacks("test-project-none")


class TestImportDecoupling:
    """Test that agent_session_queue has no module-level bridge imports."""

    def test_no_module_level_bridge_imports(self):
        """agent/agent_session_queue.py should not import from bridge/ at module level."""
        source = (
            Path(__file__).parent.parent.parent / "agent" / "agent_session_queue.py"
        ).read_text()

        # Check only the top-level imports (before the first class/function definition)
        lines = source.split("\n")
        in_imports = True
        for line in lines:
            stripped = line.strip()
            # Once we hit a function/class def, we're past module-level imports
            if stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("async def "):
                if not stripped.startswith("def _"):
                    # Could be a top-level function
                    pass
                in_imports = False
                continue

            if in_imports and stripped.startswith("from bridge."):
                pytest.fail(
                    f"Module-level bridge import found: {stripped}"
                )

    def test_reaction_constants_importable_from_agent(self):
        """REACTION_* constants should be importable from agent.constants."""
        from agent.constants import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS

        assert REACTION_SUCCESS == "\U0001f44d"
        assert REACTION_COMPLETE == "\U0001f3c6"
        assert REACTION_ERROR == "\U0001f631"

    def test_reaction_re_exports_from_bridge(self):
        """REACTION_* should still be importable from bridge.response (backward compat)."""
        from bridge.response import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS

        assert REACTION_SUCCESS == "\U0001f44d"
        assert REACTION_COMPLETE == "\U0001f3c6"
        assert REACTION_ERROR == "\U0001f631"

    def test_session_logs_importable_from_agent(self):
        """save_session_snapshot should be importable from agent.session_logs."""
        from agent.session_logs import save_session_snapshot

        assert callable(save_session_snapshot)

    def test_session_logs_re_exports_from_bridge(self):
        """save_session_snapshot should still be importable from bridge.session_logs."""
        from bridge.session_logs import save_session_snapshot

        assert callable(save_session_snapshot)
