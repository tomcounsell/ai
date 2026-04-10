"""Tests for asyncio.to_thread wrapping in agent_session_queue.py.

Covers get_active_session_for_chat and related async helpers.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401

import pytest

from agent.agent_session_queue import get_active_session_for_chat


@pytest.fixture
def mock_agent_session():
    """Patch AgentSession.query.filter for unit testing."""
    with patch("agent.agent_session_queue.AgentSession") as mock_cls:
        yield mock_cls


class TestGetActiveSessionForChat:
    """Tests for the get_active_session_for_chat helper."""

    def test_returns_none_when_no_sessions(self, mock_agent_session):
        """Should return None if no running sessions exist for chat_id."""
        mock_agent_session.query.filter.return_value = []
        result = asyncio.run(get_active_session_for_chat("12345"))
        assert result is None
        mock_agent_session.query.filter.assert_called_once_with(chat_id="12345", status="running")

    def test_returns_most_recent_session(self, mock_agent_session):
        """Should return the most recent running session by created_at."""
        older = MagicMock()
        older.created_at = 1000.0
        newer = MagicMock()
        newer.created_at = 2000.0

        mock_agent_session.query.filter.return_value = [older, newer]
        result = asyncio.run(get_active_session_for_chat("12345"))
        assert result is newer

    def test_returns_single_session(self, mock_agent_session):
        """Should return the only session when exactly one exists."""
        session = MagicMock()
        session.created_at = 1500.0

        mock_agent_session.query.filter.return_value = [session]
        result = asyncio.run(get_active_session_for_chat("67890"))
        assert result is session

    def test_handles_none_created_at(self, mock_agent_session):
        """Should handle sessions with None created_at (sorted as 0)."""
        no_ts = MagicMock()
        no_ts.created_at = None
        with_ts = MagicMock()
        with_ts.created_at = 500.0

        mock_agent_session.query.filter.return_value = [no_ts, with_ts]
        result = asyncio.run(get_active_session_for_chat("12345"))
        # with_ts has higher created_at, should be returned
        assert result is with_ts

    def test_uses_to_thread(self, mock_agent_session):
        """Verify the sync filter call is wrapped in asyncio.to_thread."""
        mock_agent_session.query.filter.return_value = []
        with patch(
            "agent.agent_session_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            asyncio.run(get_active_session_for_chat("12345"))
            # to_thread should have been called (at least once for this function)
            assert mock_to_thread.called


class TestPushJobAsyncWrapping:
    """Verify _push_agent_session wraps sync Popoto calls in asyncio.to_thread."""

    def test_push_agent_session_superseding_uses_to_thread(self, mock_agent_session):
        """Superseding logic in _push_agent_session uses to_thread for sync filter+save."""
        from agent.agent_session_queue import _push_agent_session

        # Set up mocks
        old_session = MagicMock()
        old_session.status = "completed"
        old_session.agent_session_id = "old-session"
        mock_agent_session.query.filter.return_value = [old_session]
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch(
            "agent.agent_session_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-1",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="123",
                    telegram_message_id=1,
                )
            )
            # to_thread should be called for superseding and lifecycle logging
            assert mock_to_thread.call_count >= 1


class TestPushAgentSessionPublish:
    """Verify _push_agent_session publishes to valor:sessions:new after enqueue."""

    def test_publish_called_after_enqueue(self, mock_agent_session):
        """Publish to valor:sessions:new should be called after session is written."""
        from agent.agent_session_queue import _push_agent_session

        mock_agent_session.query.filter.return_value = []
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.publish = MagicMock(return_value=1)
            asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-pub",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="chat-1",
                    telegram_message_id=1,
                )
            )
            mock_redis.publish.assert_called_once()
            args = mock_redis.publish.call_args
            assert args[0][0] == "valor:sessions:new"
            import json

            payload = json.loads(args[0][1])
            assert payload["chat_id"] == "chat-1"
            assert payload["session_id"] == "sess-pub"

    def test_session_written_even_if_publish_fails(self, mock_agent_session):
        """Publish failure must not prevent session creation."""
        from agent.agent_session_queue import _push_agent_session

        mock_agent_session.query.filter.return_value = []
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.publish = MagicMock(side_effect=Exception("Redis down"))
            # Should not raise
            result = asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-fail",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="chat-2",
                    telegram_message_id=2,
                )
            )
            # Session was still created
            mock_agent_session.async_create.assert_called_once()
            # Result is a count (not an exception)
            assert isinstance(result, int)


class TestEnqueueContinuationAsyncWrapping:
    """Verify _enqueue_nudge wraps sync filter in asyncio.to_thread."""

    def test_continuation_filter_uses_to_thread(self, mock_agent_session):
        """The session lookup in _enqueue_nudge should use to_thread."""
        from agent.agent_session_queue import _enqueue_nudge

        # Create a mock agent session
        mock_rj = MagicMock()
        mock_rj.session_id = "sess-1"
        mock_rj.project_key = "test"
        session = mock_rj

        # Return a session from filter -- used by get_authoritative_session
        existing_session = MagicMock()
        existing_session.status = "running"
        existing_session.session_id = "sess-1"
        existing_session.project_key = "test"
        existing_session.save = MagicMock()
        existing_session.log_lifecycle_transition = MagicMock()

        # Patch get_authoritative_session and update_session at the source module
        # (_enqueue_nudge does a local import from models.session_lifecycle)
        with patch(
            "models.session_lifecycle.get_authoritative_session",
            return_value=existing_session,
        ):
            with patch(
                "models.session_lifecycle.update_session",
            ) as mock_update:
                with patch(
                    "agent.agent_session_queue.asyncio.to_thread",
                    wraps=asyncio.to_thread,
                ) as mock_to_thread:
                    with patch("agent.agent_session_queue._ensure_worker"):
                        asyncio.run(
                            _enqueue_nudge(
                                session=session,
                                branch_name="test-branch",
                                task_list_id="tl-1",
                                auto_continue_count=1,
                                output_msg="test output",
                                nudge_feedback="continue",
                            )
                        )
                        # to_thread should be called for the re-read
                        assert mock_to_thread.called
                        # update_session should be called for the transition
                        mock_update.assert_called_once()
