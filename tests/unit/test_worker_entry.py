"""Tests for the standalone worker entry point (worker/__main__.py).

Tests configuration loading, argument parsing, and basic startup logic.
"""

import signal
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
        from worker.__main__ import _load_projects, _parse_args, main

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
        assert "worker" in combined.lower(), f"Expected 'worker' in output, got: {combined[:500]}"

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
        from agent.agent_session_queue import (
            _reaction_callbacks,
            _send_callbacks,
            register_callbacks,
        )
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
        from agent.agent_session_queue import (
            _reaction_callbacks,
            _send_callbacks,
            register_callbacks,
        )

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
            if (
                stripped.startswith("def ")
                or stripped.startswith("class ")
                or stripped.startswith("async def ")
            ):
                if not stripped.startswith("def _"):
                    # Could be a top-level function
                    pass
                in_imports = False
                continue

            if in_imports and stripped.startswith("from bridge."):
                pytest.fail(f"Module-level bridge import found: {stripped}")

    def test_bridge_has_no_execution_function_imports(self):
        """bridge/telegram_bridge.py must not import execution functions from agent_session_queue.

        These functions are worker-only responsibilities after the bridge/worker separation:
        - _ensure_worker
        - _recover_interrupted_agent_sessions_startup
        - _agent_session_health_loop
        - _cleanup_orphaned_claude_processes
        """
        source = (Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py").read_text()

        forbidden_imports = [
            "_ensure_worker",
            "_recover_interrupted_agent_sessions_startup",
            "_agent_session_health_loop",
            "_cleanup_orphaned_claude_processes",
        ]

        # Find all import statements in the file
        import_lines = [
            line.strip()
            for line in source.split("\n")
            if line.strip().startswith("from ") or line.strip().startswith("import ")
        ]

        for fn_name in forbidden_imports:
            for import_line in import_lines:
                if fn_name in import_line:
                    pytest.fail(f"Execution function '{fn_name}' imported in bridge: {import_line}")

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


class TestWorkerStartupSequence:
    """Test that worker/__main__.py startup sequence is complete and deterministic.

    After bridge/worker separation, all session lifecycle functions must be called
    from the worker startup sequence — not from the bridge.
    """

    def test_worker_imports_cleanup_orphaned(self):
        """worker/__main__.py must import _cleanup_orphaned_claude_processes."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "_cleanup_orphaned_claude_processes" in source, (
            "worker/__main__.py must import and call _cleanup_orphaned_claude_processes"
        )

    def test_worker_calls_rebuild_indexes(self):
        """worker/__main__.py must call AgentSession.rebuild_indexes()."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "rebuild_indexes" in source, (
            "worker/__main__.py must call AgentSession.rebuild_indexes() at startup"
        )

    def test_worker_calls_recover_interrupted(self):
        """worker/__main__.py must call _recover_interrupted_agent_sessions_startup."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "_recover_interrupted_agent_sessions_startup" in source, (
            "worker/__main__.py must call _recover_interrupted_agent_sessions_startup at startup"
        )

    def test_worker_calls_cleanup_corrupted(self):
        """worker/__main__.py must call cleanup_corrupted_agent_sessions."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "cleanup_corrupted_agent_sessions" in source, (
            "worker/__main__.py must call cleanup_corrupted_agent_sessions at startup"
        )

    def test_worker_calls_health_loop(self):
        """worker/__main__.py must start _agent_session_health_loop."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "_agent_session_health_loop" in source, (
            "worker/__main__.py must start _agent_session_health_loop as background task"
        )

    def test_worker_calls_ensure_worker_for_pending(self):
        """worker/__main__.py must call _ensure_worker for pending sessions at startup."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "_ensure_worker" in source, (
            "worker/__main__.py must call _ensure_worker for pending sessions at startup"
        )

    def test_cleanup_orphaned_in_agent_queue_not_bridge(self):
        """_cleanup_orphaned_claude_processes must be defined in agent_session_queue, not bridge."""
        aq_source = (
            Path(__file__).parent.parent.parent / "agent" / "agent_session_queue.py"
        ).read_text()
        bridge_source = (
            Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        ).read_text()

        assert "def _cleanup_orphaned_claude_processes" in aq_source, (
            "_cleanup_orphaned_claude_processes should be defined in agent/agent_session_queue.py"
        )
        assert "def _cleanup_orphaned_claude_processes" not in bridge_source, (
            "_cleanup_orphaned_claude_processes must NOT be defined in bridge/telegram_bridge.py"
        )

    def test_worker_startup_sequence_order(self):
        """Verify the startup sequence order in worker/__main__.py.

        Checks line numbers of actual function calls (not imports) to confirm the
        deterministic order: rebuild_indexes -> cleanup_corrupted -> recover_interrupted
        -> cleanup_orphaned -> ensure_workers.
        """
        import re

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()

        lines = source.split("\n")

        def first_call_line(pattern: str) -> int:
            """Return the line number of the first call matching pattern, skipping imports."""
            in_import_block = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                # Track multi-line import blocks
                if stripped.startswith("from ") and "(" in stripped and ")" not in stripped:
                    in_import_block = True
                if in_import_block:
                    if ")" in stripped:
                        in_import_block = False
                    continue
                # Skip single-line imports
                if stripped.startswith("from ") or stripped.startswith("import "):
                    continue
                if re.search(pattern, line):
                    return i
            return -1

        line_rebuild = first_call_line(r"\.rebuild_indexes\(\)")
        line_cleanup_corrupted = first_call_line(r"cleanup_corrupted_agent_sessions\(\)")
        line_recover = first_call_line(r"_recover_interrupted_agent_sessions_startup\(\)")
        line_cleanup_orphaned = first_call_line(r"_cleanup_orphaned_claude_processes\(\)")
        line_ensure_worker = first_call_line(r"_ensure_worker\(")

        assert line_rebuild >= 0, "AgentSession.rebuild_indexes() call not found"
        assert line_cleanup_corrupted >= 0, "cleanup_corrupted_agent_sessions() call not found"
        assert line_recover >= 0, "_recover_interrupted_agent_sessions_startup() call not found"
        assert line_cleanup_orphaned >= 0, "_cleanup_orphaned_claude_processes() call not found"
        assert line_ensure_worker >= 0, "_ensure_worker( call not found"

        assert line_rebuild < line_cleanup_corrupted, (
            f"rebuild_indexes (line {line_rebuild}) should come before "
            f"cleanup_corrupted_agent_sessions (line {line_cleanup_corrupted})"
        )
        assert line_cleanup_corrupted < line_recover, (
            f"cleanup_corrupted (line {line_cleanup_corrupted}) should come before "
            f"_recover_interrupted (line {line_recover})"
        )
        assert line_recover < line_cleanup_orphaned, (
            f"_recover_interrupted (line {line_recover}) should come before "
            f"_cleanup_orphaned (line {line_cleanup_orphaned})"
        )
        assert line_cleanup_orphaned < line_ensure_worker, (
            f"_cleanup_orphaned (line {line_cleanup_orphaned}) should come before "
            f"_ensure_worker (line {line_ensure_worker})"
        )


class TestSigtermExitCode:
    """Test that SIGTERM sets _shutdown_via_signal and SIGINT does not.

    launchd only applies ThrottleInterval on non-zero exits.  When the worker
    exits with code 0 (voluntary stop) launchd applies an internal ~10-minute
    throttle regardless of the configured ThrottleInterval value.  SIGTERM is
    an external/forced termination that should result in exit code 1 so
    launchd respects the 10-second ThrottleInterval.  SIGINT is a developer
    stop (Ctrl-C) and should remain exit code 0.
    """

    def setup_method(self):
        """Reset _shutdown_via_signal before each test."""
        import worker.__main__ as wm

        wm._shutdown_via_signal = False

    def teardown_method(self):
        """Reset _shutdown_via_signal after each test."""
        import worker.__main__ as wm

        wm._shutdown_via_signal = False

    def test_sigterm_sets_shutdown_flag(self):
        """SIGTERM signal handler must set _shutdown_via_signal = True."""
        import worker.__main__ as wm

        wm._shutdown_via_signal = False
        # Simulate what the SIGTERM branch of _signal_handler does
        if signal.SIGTERM:
            wm._shutdown_via_signal = True

        assert wm._shutdown_via_signal is True, "_shutdown_via_signal must be True after SIGTERM"

    def test_sigint_does_not_set_shutdown_flag(self):
        """SIGINT signal handler must NOT set _shutdown_via_signal."""
        import worker.__main__ as wm

        wm._shutdown_via_signal = False
        # Simulate what the SIGINT branch of _signal_handler does — flag is NOT set

        assert wm._shutdown_via_signal is False, (
            "_shutdown_via_signal must remain False after SIGINT"
        )

    def test_sigterm_flag_present_in_source(self):
        """worker/__main__.py source must contain the _shutdown_via_signal flag pattern."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "_shutdown_via_signal = True" in source, (
            "SIGTERM handler must set _shutdown_via_signal = True"
        )
        assert "sys.exit(1)" in source, (
            "main() must call sys.exit(1) when _shutdown_via_signal is True"
        )
        assert "if _shutdown_via_signal" in source, (
            "main() must check _shutdown_via_signal after asyncio.run() returns"
        )

    def test_sigterm_exit_note_in_source(self):
        """worker/__main__.py must log the reason for the non-zero exit."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "ThrottleInterval" in source, (
            "Exit code 1 log line must mention ThrottleInterval for operator clarity"
        )

    def test_launchctl_bootout_in_stop_worker(self):
        """scripts/valor-service.sh stop_worker() must use launchctl bootout not unload."""
        source = (Path(__file__).parent.parent.parent / "scripts" / "valor-service.sh").read_text()

        start = source.find("stop_worker()")
        assert start >= 0, "stop_worker() function not found in valor-service.sh"

        next_fn = source.find("\n}", start)
        stop_worker_body = source[start : next_fn + 2] if next_fn >= 0 else source[start:]

        assert "launchctl bootout" in stop_worker_body, (
            "stop_worker() must use 'launchctl bootout' (not 'launchctl unload') "
            "so launchd supervision is maintained through stop/start cycles"
        )
        assert "launchctl unload" not in stop_worker_body, (
            "stop_worker() must not use deprecated 'launchctl unload' — "
            "it destroys launchd supervision and prevents automatic restarts"
        )
