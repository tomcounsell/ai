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

        # Return a session from filter
        existing_session = MagicMock()
        # _enqueue_nudge now uses direct mutation + async_save (status is IndexedField)
        existing_session.async_save = AsyncMock()
        mock_agent_session.query.filter.return_value = [existing_session]

        with patch(
            "agent.agent_session_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            with patch("agent.agent_session_queue._ensure_worker"):
                asyncio.run(
                    _enqueue_nudge(
                        session=session,
                        branch_name="test-branch",
                        task_list_id="tl-1",
                        auto_continue_count=1,
                        output_msg="test output",
                        coaching_message="continue",
                    )
                )
                # to_thread should be called for the filter
                assert mock_to_thread.called
