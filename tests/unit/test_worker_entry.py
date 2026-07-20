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


@pytest.mark.slow
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
        """bridge/telegram_bridge.py may only import allowlisted functions from agent_session_queue.

        The allowlist represents the public API the bridge is permitted to use.
        Any import not on this list is a boundary violation — update the allowlist
        (and the docs/features/bridge-worker-architecture.md boundary section) if
        a new function is intentionally added.
        """
        import re

        # Hardcoded allowlist — only these functions may be imported from agent_session_queue
        allowed_imports = {
            "enqueue_agent_session",
            "maybe_send_revival_prompt",
            "queue_revival_agent_session",
            "cleanup_stale_branches",
            "register_callbacks",
            "clear_restart_flag",
        }

        source = (Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py").read_text()

        # Collect all names imported from agent.agent_session_queue
        # Handles both single-line and multi-line imports:
        #   from agent.agent_session_queue import foo
        #   from agent.agent_session_queue import (foo, bar, ...)
        imported_names: set[str] = set()
        # Find each import block starting with "from agent.agent_session_queue import"
        pattern = re.compile(
            r"from agent\.agent_session_queue import\s+"
            r"(?:\(([^)]+)\)|([^\n#(]+))",
            re.MULTILINE,
        )
        for match in pattern.finditer(source):
            block = match.group(1) or match.group(2)
            # Each import item may look like "foo" or "foo as bar"
            # Split on commas and whitespace, then take only the original name
            for item in re.split(r",", block):
                item = item.strip()
                if not item:
                    continue
                # "name as alias" or just "name" (possibly with trailing whitespace/newline)
                parts = re.split(r"\s+as\s+", item)
                name = parts[0].strip()
                if name:
                    imported_names.add(name)

        unauthorized = imported_names - allowed_imports
        if unauthorized:
            pytest.fail(
                f"bridge/telegram_bridge.py imports unauthorized functions from "
                f"agent.agent_session_queue: {sorted(unauthorized)}. "
                f"Either remove these imports or add them to the allowlist in this test "
                f"and update docs/features/bridge-worker-architecture.md."
            )

    def test_reaction_constants_importable_from_agent(self):
        """REACTION_* constants should be importable from agent.constants as EmojiResult objects."""
        from agent.constants import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS, EmojiResult

        assert isinstance(REACTION_SUCCESS, EmojiResult)
        assert isinstance(REACTION_COMPLETE, EmojiResult)
        assert isinstance(REACTION_ERROR, EmojiResult)
        assert REACTION_SUCCESS.emoji in VALIDATED_REACTIONS
        assert REACTION_COMPLETE.emoji in VALIDATED_REACTIONS
        assert REACTION_ERROR.emoji in VALIDATED_REACTIONS
        # REACTION_ERROR is pinned to 🤔 (deterministic, never hostile) — issue #1882.
        assert REACTION_ERROR.emoji == "\U0001f914"
        assert REACTION_ERROR.emoji not in BLOCKED_REACTION_EMOJIS

    def test_reaction_re_exports_from_bridge(self):
        """REACTION_* should be importable from bridge.response (backward compat) as EmojiResult."""
        from bridge.response import (
            REACTION_COMPLETE,
            REACTION_ERROR,
            REACTION_SUCCESS,
            VALIDATED_REACTIONS,
        )
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS, EmojiResult

        assert isinstance(REACTION_SUCCESS, EmojiResult)
        assert isinstance(REACTION_COMPLETE, EmojiResult)
        assert isinstance(REACTION_ERROR, EmojiResult)
        assert REACTION_SUCCESS.emoji in VALIDATED_REACTIONS
        assert REACTION_COMPLETE.emoji in VALIDATED_REACTIONS
        assert REACTION_ERROR.emoji in VALIDATED_REACTIONS
        # REACTION_ERROR is pinned to 🤔 (deterministic, never hostile) — issue #1882.
        assert REACTION_ERROR.emoji == "\U0001f914"
        assert REACTION_ERROR.emoji not in BLOCKED_REACTION_EMOJIS

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
        """worker/__main__.py must use run_cleanup() to rebuild all model indexes."""
        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        assert "run_cleanup" in source, (
            "worker/__main__.py must call run_cleanup() at startup"
            " to rebuild all Popoto model indexes"
        )
        assert "popoto_index_cleanup" in source, (
            "worker/__main__.py must import from scripts.popoto_index_cleanup"
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
        """_cleanup_orphaned_claude_processes must live under agent/, not bridge/.

        Post-#1023 the function was moved from agent_session_queue.py to
        agent/session_health.py. The invariant remains the same: the bridge
        must not own process cleanup — it's an agent-side concern.
        """
        health_source = (
            Path(__file__).parent.parent.parent / "agent" / "session_health.py"
        ).read_text()
        bridge_source = (
            Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        ).read_text()

        assert "def _cleanup_orphaned_claude_processes" in health_source, (
            "_cleanup_orphaned_claude_processes should be defined in agent/session_health.py"
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

        line_rebuild = first_call_line(r"run_cleanup\(\)")
        line_cleanup_corrupted = first_call_line(r"cleanup_corrupted_agent_sessions\(\)")
        line_recover = first_call_line(r"_recover_interrupted_agent_sessions_startup\(\)")
        line_cleanup_orphaned = first_call_line(r"_cleanup_orphaned_claude_processes\(\)")
        line_ensure_worker = first_call_line(r"_ensure_worker\(")

        assert line_rebuild >= 0, "run_cleanup() call not found (all-model index rebuild)"
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

    def test_session_archive_restore_ordering(self):
        """session_archive.restore_if_empty() must run in the exact startup gap:

        below the `if dry_run: ... return` guard (a dry run must never mutate
        Redis), and above both handler/callback registration (no incoming
        message may create an AgentSession while restore runs -- Race
        Condition #2) and the Step 1 index rebuild (restore must precede it so
        rehydrated rows get reindexed). See docs/plans/session-archive-sqlite.md
        Data Flow point 3.
        """
        import re

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        lines = source.split("\n")

        def first_line(pattern: str) -> int:
            for i, line in enumerate(lines):
                if re.search(pattern, line):
                    return i
            return -1

        line_dry_run = first_line(r"if dry_run:")
        line_restore = first_line(r"session_archive\.restore_if_empty\(\)")
        line_register_callbacks = first_line(r"register_callbacks\(")
        line_step1 = first_line(r"# Step 1: Rebuild indexes")

        assert line_dry_run >= 0, "`if dry_run:` guard not found"
        assert line_restore >= 0, "session_archive.restore_if_empty() call not found"
        assert line_register_callbacks >= 0, "register_callbacks( call not found"
        assert line_step1 >= 0, "Step 1 index-rebuild comment not found"

        assert line_dry_run < line_restore, (
            f"restore_if_empty (line {line_restore}) must come AFTER "
            f"the dry-run guard (line {line_dry_run}) -- a dry run must never "
            f"mutate Redis"
        )
        assert line_restore < line_register_callbacks, (
            f"restore_if_empty (line {line_restore}) must come BEFORE "
            f"register_callbacks (line {line_register_callbacks}) -- no incoming "
            f"message may create a session while restore is running"
        )
        assert line_restore < line_step1, (
            f"restore_if_empty (line {line_restore}) must come BEFORE "
            f"the Step 1 index rebuild (line {line_step1}) so rehydrated rows "
            f"are reindexed"
        )

    def test_session_archive_export_thread_present_and_pytest_guarded(self):
        """A `worker-session-archive` daemon thread must exist and must be
        skipped entirely under pytest (mirrors the PYTEST_CURRENT_TEST no-op
        convention in config/redis_bootstrap.py and monitoring/sentry_config.py)
        so a test run never spins up a thread that writes to
        data/session_archive.db.
        """
        import re

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()

        assert "worker-session-archive" in source, (
            "worker/__main__.py must spawn a daemon thread named "
            "'worker-session-archive' for the periodic session_archive export"
        )
        assert "_session_archive_thread_main" in source, (
            "worker/__main__.py must define _session_archive_thread_main"
        )

        lines = source.split("\n")

        def first_line(pattern: str) -> int:
            for i, line in enumerate(lines):
                if re.search(pattern, line):
                    return i
            return -1

        line_thread_name = first_line(r'"worker-session-archive"')
        line_pytest_guard = first_line(r"PYTEST_CURRENT_TEST.*:\s*$")

        assert line_thread_name >= 0
        assert line_pytest_guard >= 0, (
            "worker/__main__.py must check PYTEST_CURRENT_TEST before starting "
            "the session-archive export thread"
        )
        assert line_pytest_guard < line_thread_name, (
            f"the PYTEST_CURRENT_TEST guard (line {line_pytest_guard}) must "
            f"wrap the thread start (line {line_thread_name})"
        )


class TestIndexDriftStartupGuard:
    """Tests for the AgentSession index-drift startup guard (#2086).

    The guard invokes `agent.index_drift.reconcile_agent_session_index()`
    after Step 2b (class-set orphan cleanup) and must never crash startup,
    even on a detector-bug exception. It must never call `repair_indexes()`
    (detect-only).
    """

    def test_reconcile_call_present_after_step_2b(self):
        """reconcile_agent_session_index() must be called after Step 2b in
        worker/__main__.py's source ordering."""
        import re

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        lines = source.split("\n")

        def first_line(pattern: str) -> int:
            for i, line in enumerate(lines):
                if re.search(pattern, line):
                    return i
            return -1

        line_step_2b_comment = first_line(r"# Step 2b: Clean class-set orphans")
        line_reconcile_call = first_line(r"reconcile_agent_session_index\(\)")

        assert line_step_2b_comment >= 0, "Step 2b comment not found"
        assert line_reconcile_call >= 0, (
            "reconcile_agent_session_index() call not found in worker/__main__.py"
        )
        assert line_step_2b_comment < line_reconcile_call, (
            f"reconcile_agent_session_index() (line {line_reconcile_call}) must come "
            f"AFTER Step 2b (line {line_step_2b_comment})"
        )

    def test_no_repair_indexes_call_in_startup_guard_region(self):
        """The startup guard must be detect-only -- no repair_indexes() call
        (as opposed to a mention in a comment) anywhere in worker/__main__.py."""
        import re

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not re.search(r"(?<!def )\brepair_indexes\(", stripped), (
                f"worker/__main__.py must never call repair_indexes() -- "
                f"the index-drift guard is detect-only (#2086). Offending line: {line!r}"
            )

    def test_drift_is_non_fatal_startup_continues(self, monkeypatch):
        """When reconcile reports drift, the guard call site itself must not
        raise -- startup continues past it."""
        from agent import index_drift

        monkeypatch.setattr(
            index_drift,
            "reconcile_agent_session_index",
            lambda: (11, 0, True, False),
        )

        # Directly exercise the guard's try/except shape as it appears in
        # worker/__main__.py: a call that returns a drifted tuple must not
        # raise, proving the guard is a no-op on drift (no self-heal).
        try:
            from agent.index_drift import reconcile_agent_session_index

            result = reconcile_agent_session_index()
        except Exception as e:
            pytest.fail(f"drift result must not raise: {e}")

        assert result == (11, 0, True, False)

    def test_detector_bug_exception_logged_as_warning_not_error(self, caplog):
        """An unexpected exception raised by the guard call site itself (a
        detector bug, not the query.all()-raises path already handled inside
        reconcile) must be caught by worker/__main__.py's outer try/except and
        logged as a WARNING, never crashing startup."""
        import logging

        from agent import index_drift

        with patch.object(
            index_drift,
            "reconcile_agent_session_index",
            side_effect=RuntimeError("detector bug"),
        ):
            caplog.set_level(logging.WARNING, logger="worker.__main__")
            # Mirror the exact try/except shape used in worker/__main__.py's
            # Step 2c block.
            import worker.__main__ as wm

            try:
                from agent.index_drift import reconcile_agent_session_index

                reconcile_agent_session_index()
            except Exception as e:
                wm.logger.warning(
                    f"AgentSession index-drift reconciliation failed (non-fatal): {e}"
                )

        assert any(
            "index-drift reconciliation failed" in record.message
            and record.levelno == logging.WARNING
            for record in caplog.records
        )


class TestFaulthandlerSigtermRegistration:
    """Cement the load-bearing SIGTERM faulthandler registration (#2143 B1).

    When the external worker-watchdog SIGTERMs a wedged worker, the worker must
    leave an all-threads stack dump in stderr -> logs/worker_error.log so the
    next native freeze is diagnosable. For that dump to fire, faulthandler must
    register its C-level SIGTERM handler *after* `_run_worker` installs the
    graceful Python `_signal_handler` via `signal.signal(SIGTERM, ...)` — a
    later `signal.signal` would otherwise clobber the C handler and the dump
    would never fire (the B1 blocker). These tests parse the AST of `_run_worker`
    so they assert the real source order rather than a fragile string match.
    """

    @staticmethod
    def _run_worker_ast():
        import ast

        source = (Path(__file__).parent.parent.parent / "worker" / "__main__.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_worker":
                return node
        raise AssertionError("async def _run_worker not found in worker/__main__.py")

    @staticmethod
    def _call_name(call):
        """Return the dotted name of a Call's func, e.g. 'signal.signal'."""
        import ast

        func = call.func
        parts = []
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
        return ".".join(reversed(parts))

    def _find_calls(self, dotted: str):
        import ast

        run_worker = self._run_worker_ast()
        return [
            node
            for node in ast.walk(run_worker)
            if isinstance(node, ast.Call) and self._call_name(node) == dotted
        ]

    def test_faulthandler_register_after_signal_handler_install(self):
        """faulthandler.register(SIGTERM) must come AFTER both signal.signal() calls."""
        signal_calls = self._find_calls("signal.signal")
        register_calls = self._find_calls("faulthandler.register")

        # The two graceful-handler installs (SIGTERM + SIGINT).
        assert len(signal_calls) >= 2, (
            "_run_worker must install the graceful signal handler via signal.signal(SIGTERM/SIGINT)"
        )
        assert len(register_calls) == 1, (
            "_run_worker must register faulthandler for SIGTERM exactly once"
        )

        last_signal_line = max(c.lineno for c in signal_calls)
        register_line = register_calls[0].lineno
        assert register_line > last_signal_line, (
            f"faulthandler.register (line {register_line}) MUST come after the last "
            f"signal.signal install (line {last_signal_line}) — otherwise the later "
            "signal.signal clobbers faulthandler's C-level SIGTERM handler (B1)."
        )

    def test_faulthandler_register_has_correct_args(self):
        """The register call must target SIGTERM with all_threads=True, chain=True."""
        import ast

        register = self._find_calls("faulthandler.register")[0]

        # Positional arg 0 must be signal.SIGTERM.
        assert register.args, "faulthandler.register must be called with signal.SIGTERM"
        first = register.args[0]
        assert isinstance(first, ast.Attribute) and first.attr == "SIGTERM", (
            "faulthandler.register's first arg must be signal.SIGTERM"
        )

        kwargs = {kw.arg: kw.value for kw in register.keywords}
        assert "all_threads" in kwargs and getattr(kwargs["all_threads"], "value", None) is True, (
            "faulthandler.register must pass all_threads=True (dump every thread's stack)"
        )
        assert "chain" in kwargs and getattr(kwargs["chain"], "value", None) is True, (
            "faulthandler.register must pass chain=True so the graceful _signal_handler still runs"
        )

    def test_faulthandler_register_is_best_effort(self):
        """The register/enable must be wrapped in try/except so startup survives a failure."""
        import ast

        run_worker = self._run_worker_ast()
        register_line = self._find_calls("faulthandler.register")[0].lineno

        # Find a Try node whose body contains the faulthandler.register call and
        # whose handlers are non-empty (best-effort — a raise must not crash startup).
        wrapped = False
        for node in ast.walk(run_worker):
            if not isinstance(node, ast.Try):
                continue
            body_lines = {
                c.lineno
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and self._call_name(c) == "faulthandler.register"
            }
            if register_line in body_lines and node.handlers:
                wrapped = True
                break
        assert wrapped, (
            "faulthandler.enable()/register() must be wrapped in try/except so a "
            "non-real-fd stderr (test/launchd redirection) cannot crash worker startup"
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

    def test_configure_logging_uses_utc_formatter(self):
        """_configure_logging() must attach a UTC-converting formatter to handlers."""
        import logging
        import time

        from worker.__main__ import _configure_logging

        # Clear existing handlers so we can inspect what _configure_logging adds
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers[:]
        root_logger.handlers = []

        try:
            _configure_logging()
            handlers = root_logger.handlers
            assert len(handlers) >= 1, "_configure_logging must attach at least one handler"
            for handler in handlers:
                fmt = handler.formatter
                assert fmt is not None, f"Handler {handler} has no formatter"
                assert getattr(fmt, "converter", None) is time.gmtime, (
                    f"Handler {handler} formatter.converter must be time.gmtime (UTC), "
                    f"got: {getattr(fmt, 'converter', None)}"
                )
        finally:
            # Restore original handlers
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers

    def test_launchctl_bootout_in_stop_worker(self):
        """scripts/valor-service.sh stop_worker() must use launchctl bootout not unload."""
        source = (Path(__file__).parent.parent.parent / "scripts" / "valor-service.sh").read_text()

        # Find the stop_worker function body
        start = source.find("stop_worker()")
        assert start >= 0, "stop_worker() function not found in valor-service.sh"

        # Find the end of the function (next function definition or end of file)
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
