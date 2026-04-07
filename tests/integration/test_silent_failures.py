"""Tests for silent failure logging in agent_session_queue.py (Gap 1).

Verifies that critical exception handlers in agent_session_queue.py emit
logger.warning() calls instead of silently swallowing exceptions
with `except Exception: pass`.

The 7 critical locations are:
- _push_agent_session: lifecycle transition logging
- _pop_agent_session: lifecycle transition logging
- _enqueue_nudge: plan file resolution from session context
- _execute_agent_session: session re-read from Redis
- _load_cooldowns: file read
- _save_cooldowns: file write
- check_revival: branch existence check

Tests use caplog to assert warnings are emitted when exceptions occur.
Assertions check log level and presence of key identifiers (session_id,
file path, etc.) — NOT exact message text, per risk mitigation in the plan.
"""

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# claude_agent_sdk mock is centralized in conftest.py


class TestPushJobLogging:
    """Tests that _push_agent_session logs warnings on lifecycle transition failures."""

    @pytest.mark.asyncio
    async def test_lifecycle_transition_failure_logs_warning(self, caplog, redis_test_db):
        """When log_lifecycle_transition raises, a warning is emitted."""

        from models.agent_session import AgentSession

        # Create a session that will trigger the lifecycle logging path
        AgentSession.create(
            session_id="test-push-lifecycle",
            project_key="test-project",
            status="pending",
            chat_id="chat_1",
            sender_name="Test",
            created_at=datetime.now(tz=UTC),
            message_text="test message",
            working_dir="/tmp/test",
            telegram_message_id=1,
            priority="normal",
        )

        # Patch the lifecycle method to raise
        with (
            patch.object(
                AgentSession,
                "log_lifecycle_transition",
                side_effect=Exception("Redis connection lost"),
            ),
            caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"),
        ):
            from agent.agent_session_queue import _push_agent_session

            await _push_agent_session(
                project_key="test-project",
                session_id="test-push-lifecycle",
                working_dir="/tmp/test",
                message_text="test",
                sender_name="Test",
                chat_id="chat_1",
                telegram_message_id=1,
            )

        # Verify warning was logged with session_id
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("test-push-lifecycle" in r.message for r in warning_records), (
            f"Expected warning with session_id, got: {[r.message for r in warning_records]}"
        )


class TestPopJobLogging:
    """Tests that _pop_agent_session logs warnings on lifecycle transition failures."""

    @pytest.mark.asyncio
    async def test_lifecycle_transition_failure_is_non_fatal(self, caplog, redis_test_db):
        """When log_lifecycle_transition raises during pop, the session is
        still returned and the failure is logged non-fatally.

        transition_status() catches log_lifecycle_transition exceptions and
        logs them at DEBUG level as non-fatal, so _pop_agent_session still
        completes the pending->running transition and returns the session.
        """

        from models.agent_session import AgentSession

        # Create a pending session for the worker to pop
        AgentSession.create(
            session_id="test-pop-lifecycle",
            project_key="test-pop-project",
            status="pending",
            chat_id="chat_2",
            sender_name="Test",
            created_at=datetime.now(tz=UTC),
            message_text="test message",
            working_dir="/tmp/test",
            telegram_message_id=2,
            priority="normal",
        )

        # Patch lifecycle method to raise after pop
        def failing_log(self, *args, **kwargs):
            raise Exception("Redis timeout on lifecycle log")

        with (
            patch.object(AgentSession, "log_lifecycle_transition", failing_log),
            caplog.at_level(logging.DEBUG, logger="models.session_lifecycle"),
        ):
            from agent.agent_session_queue import _pop_agent_session

            session = await _pop_agent_session("chat_2")

        # Session should still be returned (failure is non-fatal)
        assert session is not None
        assert session.status == "running"
        # Verify the lifecycle failure was noted (DEBUG log from transition_status)
        debug_records = [r for r in caplog.records if "[lifecycle]" in r.message]
        assert any("Lifecycle log failed" in r.message for r in debug_records), (
            f"Expected non-fatal lifecycle log message, got: {[r.message for r in debug_records]}"
        )


class TestEnqueueContinuationSessionLookupLogging:
    """Tests that _enqueue_nudge handles missing session gracefully."""

    @pytest.mark.asyncio
    async def test_missing_session_logs_error_and_falls_back(self, caplog, redis_test_db):
        """When no AgentSession exists for the session_id, an error is logged
        and the function falls back to enqueue_agent_session."""

        # Use a real AgentSession instance (not persisted) so the fallback
        # _extract_agent_session_fields call sees real field values instead
        # of MagicMock placeholders that crash AgentSession construction.
        from models.agent_session import AgentSession as _AS

        mock_session_entry = _AS(
            project_key="test-project",
            session_id="nonexistent-session-999",
            working_dir="/tmp/test",
            message_text="continue",
            sender_name="Test",
            chat_id="chat_3",
            telegram_message_id=3,
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
        )

        from unittest.mock import AsyncMock as _AsyncMock

        with (
            caplog.at_level(logging.ERROR, logger="agent.agent_session_queue"),
            patch("agent.agent_session_queue.enqueue_agent_session", new_callable=_AsyncMock),
        ):
            from agent.agent_session_queue import _enqueue_nudge

            await _enqueue_nudge(
                session=mock_session_entry,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        # Verify error was logged about missing session
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("nonexistent-session-999" in r.message for r in error_records), (
            f"Expected error with session_id, got: {[r.message for r in error_records]}"
        )


class TestLoadCooldownsLogging:
    """Tests that _load_cooldowns logs warnings on file read failures."""

    def test_file_read_failure_logs_warning(self, caplog, tmp_path):
        """When cooldown file read fails, a warning is emitted."""
        import agent.agent_session_queue as jq
        from agent.agent_session_queue import _load_cooldowns

        # Save and replace the cooldown file path
        original = jq._COOLDOWN_FILE
        jq._COOLDOWN_FILE = tmp_path / "bad_cooldowns.json"

        # Write invalid JSON
        jq._COOLDOWN_FILE.write_text("{invalid json content")

        try:
            with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
                result = _load_cooldowns()

            # Should return empty dict on failure
            assert result == {}
            # Verify warning was logged
            warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert any("cooldown" in r.message.lower() for r in warning_records), (
                f"Expected warning about cooldowns, got: {[r.message for r in warning_records]}"
            )
        finally:
            jq._COOLDOWN_FILE = original


class TestSaveCooldownsLogging:
    """Tests that _save_cooldowns logs warnings on file write failures."""

    def test_file_write_failure_logs_warning(self, caplog):
        """When cooldown file write fails, a warning is emitted."""
        import agent.agent_session_queue as jq
        from agent.agent_session_queue import _save_cooldowns

        original = jq._COOLDOWN_FILE
        # Point to a path that can't be written (permission denied simulation)
        jq._COOLDOWN_FILE = MagicMock()
        jq._COOLDOWN_FILE.parent.mkdir = MagicMock(side_effect=Exception("Permission denied"))

        try:
            with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
                _save_cooldowns({"test-project": 1234567890.0})

            warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert any("cooldown" in r.message.lower() for r in warning_records), (
                f"Expected warning about cooldowns, got: {[r.message for r in warning_records]}"
            )
        finally:
            jq._COOLDOWN_FILE = original


class TestCheckRevivalBranchLogging:
    """Tests that check_revival logs warnings on branch existence check failures."""

    def test_branch_check_failure_logs_warning(self, caplog, redis_test_db):
        """When subprocess fails checking branch existence, a warning is emitted."""
        import time as _time

        from agent.agent_session_queue import check_revival
        from models.agent_session import AgentSession

        # Create a session in Redis that belongs to this chat so the branch
        # list is populated (check_revival queries Redis first, then verifies
        # branches with git subprocess)
        AgentSession.create(
            session_id="revival-test-session",
            project_key="test-revival-project",
            status="running",
            chat_id="chat_revival",
            sender_name="Test",
            created_at=_time.time(),
            message_text="test",
            working_dir="/tmp/nonexistent",
            telegram_message_id=99,
            priority="normal",
        )

        with (
            patch("agent.agent_session_queue._load_cooldowns", return_value={}),
            patch("subprocess.run", side_effect=Exception("git not found")),
            caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"),
        ):
            result = check_revival(
                project_key="test-revival-project",
                working_dir="/tmp/nonexistent",
                chat_id="chat_revival",
            )

        # check_revival should return None (branch check failed, no valid branches)
        assert result is None
        # The key assertion is that the warning was logged
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("branch" in r.message.lower() for r in warning_records), (
            f"Expected warning about branch check, got: {[r.message for r in warning_records]}"
        )


class TestNoSilentPassRemaining:
    """Meta-test verifying no silent 'except Exception: pass' remains in critical paths."""

    def test_no_bare_pass_in_critical_functions(self):
        """Critical functions should not have bare 'except Exception: pass'."""
        import inspect

        from agent.agent_session_queue import (
            _enqueue_nudge,
            _execute_agent_session,
            _load_cooldowns,
            _pop_agent_session,
            _push_agent_session,
            _save_cooldowns,
            check_revival,
        )

        critical_functions = [
            _push_agent_session,
            _pop_agent_session,
            _enqueue_nudge,
            _execute_agent_session,
            _load_cooldowns,
            _save_cooldowns,
            check_revival,
        ]

        for func in critical_functions:
            source = inspect.getsource(func)
            # Find all except blocks with bare pass
            # This is a simple pattern check, not a full AST analysis
            lines = source.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped == "pass" and i > 0:
                    prev_line = lines[i - 1].strip()
                    if prev_line.startswith("except Exception"):
                        # Check if it's truly bare (no logger call nearby)
                        # Look at next few lines for logger
                        context = "\n".join(lines[max(0, i - 2) : i + 3])
                        if "logger" not in context:
                            pytest.fail(
                                f"Found 'except Exception: pass' without logger "
                                f"in {func.__name__} at line {i}: {context}"
                            )
