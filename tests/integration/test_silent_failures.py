"""Tests for silent failure logging in job_queue.py (Gap 1).

Verifies that critical exception handlers in job_queue.py emit
logger.warning() calls instead of silently swallowing exceptions
with `except Exception: pass`.

The 7 critical locations are:
- _push_job: lifecycle transition logging
- _pop_job: lifecycle transition logging
- _enqueue_nudge: plan file resolution from session context
- _execute_job: session re-read from Redis
- _load_cooldowns: file read
- _save_cooldowns: file write
- check_revival: branch existence check

Tests use caplog to assert warnings are emitted when exceptions occur.
Assertions check log level and presence of key identifiers (session_id,
file path, etc.) — NOT exact message text, per risk mitigation in the plan.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

# claude_agent_sdk mock is centralized in conftest.py


class TestPushJobLogging:
    """Tests that _push_job logs warnings on lifecycle transition failures."""

    @pytest.mark.asyncio
    async def test_lifecycle_transition_failure_logs_warning(self, caplog, redis_test_db):
        """When log_lifecycle_transition raises, a warning is emitted."""
        import time

        from models.agent_session import AgentSession

        # Create a session that will trigger the lifecycle logging path
        AgentSession.create(
            session_id="test-push-lifecycle",
            project_key="test-project",
            status="pending",
            chat_id="chat_1",
            sender_name="Test",
            created_at=time.time(),
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
            caplog.at_level(logging.WARNING, logger="agent.job_queue"),
        ):
            from agent.job_queue import _push_job

            await _push_job(
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
    """Tests that _pop_job logs warnings on lifecycle transition failures."""

    @pytest.mark.asyncio
    async def test_lifecycle_transition_failure_logs_warning(self, caplog, redis_test_db):
        """When log_lifecycle_transition raises during pop, a warning is emitted."""
        import time

        from models.agent_session import AgentSession

        # Create a pending session for the worker to pop
        AgentSession.create(
            session_id="test-pop-lifecycle",
            project_key="test-pop-project",
            status="pending",
            chat_id="chat_2",
            sender_name="Test",
            created_at=time.time(),
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
            caplog.at_level(logging.WARNING, logger="agent.job_queue"),
        ):
            from agent.job_queue import _pop_job

            job = await _pop_job("chat_2")

        # Job should still be returned (failure is non-fatal)
        assert job is not None
        # Verify warning was logged
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "lifecycle" in r.message.lower() or "test-pop" in r.message for r in warning_records
        ), (
            f"Expected warning about lifecycle transition, got: "
            f"{[r.message for r in warning_records]}"
        )


class TestEnqueueContinuationSessionLookupLogging:
    """Tests that _enqueue_nudge handles missing session gracefully."""

    @pytest.mark.asyncio
    async def test_missing_session_logs_error_and_falls_back(self, caplog, redis_test_db):
        """When no AgentSession exists for the session_id, an error is logged
        and the function falls back to enqueue_job."""

        mock_job = MagicMock()
        mock_job.project_key = "test-project"
        mock_job.session_id = "nonexistent-session-999"
        mock_job.working_dir = "/tmp/test"
        mock_job.message_text = "continue"
        mock_job.sender_name = "Test"
        mock_job.chat_id = "chat_3"
        mock_job.telegram_message_id = 3
        mock_job.work_item_slug = None
        mock_job.task_list_id = None
        mock_job.classification_type = None

        from unittest.mock import AsyncMock as _AsyncMock

        with (
            caplog.at_level(logging.ERROR, logger="agent.job_queue"),
            patch("agent.job_queue.enqueue_job", new_callable=_AsyncMock),
        ):
            from agent.job_queue import _enqueue_nudge

            await _enqueue_nudge(
                job=mock_job,
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
        import agent.job_queue as jq
        from agent.job_queue import _load_cooldowns

        # Save and replace the cooldown file path
        original = jq._COOLDOWN_FILE
        jq._COOLDOWN_FILE = tmp_path / "bad_cooldowns.json"

        # Write invalid JSON
        jq._COOLDOWN_FILE.write_text("{invalid json content")

        try:
            with caplog.at_level(logging.WARNING, logger="agent.job_queue"):
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
        import agent.job_queue as jq
        from agent.job_queue import _save_cooldowns

        original = jq._COOLDOWN_FILE
        # Point to a path that can't be written (permission denied simulation)
        jq._COOLDOWN_FILE = MagicMock()
        jq._COOLDOWN_FILE.parent.mkdir = MagicMock(side_effect=Exception("Permission denied"))

        try:
            with caplog.at_level(logging.WARNING, logger="agent.job_queue"):
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

        from agent.job_queue import check_revival
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
            patch("agent.job_queue._load_cooldowns", return_value={}),
            patch("subprocess.run", side_effect=Exception("git not found")),
            caplog.at_level(logging.WARNING, logger="agent.job_queue"),
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

        from agent.job_queue import (
            _enqueue_nudge,
            _execute_job,
            _load_cooldowns,
            _pop_job,
            _push_job,
            _save_cooldowns,
            check_revival,
        )

        critical_functions = [
            _push_job,
            _pop_job,
            _enqueue_nudge,
            _execute_job,
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
