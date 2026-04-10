"""Tests for the session status CLI (python -m agent.agent_session_queue --status)."""

import sys
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch


class TestSessionStatusCli(unittest.TestCase):
    """Test _cli_show_status output."""

    @patch("agent.agent_session_queue.AgentSession")
    @patch("agent.agent_session_queue._active_workers", {})
    def test_empty_queue(self, mock_session_cls):
        """Status with no sessions prints 'Queue is empty.'"""
        mock_session_cls.query.all.return_value = []
        from agent.agent_session_queue import _cli_show_status

        captured = StringIO()
        sys.stdout = captured
        try:
            _cli_show_status()
        finally:
            sys.stdout = sys.__stdout__
        assert "Queue is empty" in captured.getvalue()

    @patch("agent.agent_session_queue.AgentSession")
    @patch("agent.agent_session_queue._active_workers", {})
    def test_shows_sessions(self, mock_session_cls):
        """Status with sessions shows session info."""
        session = MagicMock()
        session.chat_id = "chat-123"
        session.project_key = "valor"
        session.status = "running"
        session.agent_session_id = "session-001"
        session.created_at = 1000000.0
        session.started_at = 1000000.0
        session.message_text = "Hello world"
        session.session_id = "sess-abc"
        session.correlation_id = "corr-xyz"
        mock_session_cls.query.all.return_value = [session]

        from agent.agent_session_queue import _cli_show_status

        captured = StringIO()
        sys.stdout = captured
        try:
            _cli_show_status()
        finally:
            sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "valor" in output
        assert "chat-123" in output
        assert "running" in output
        assert "session-001" in output
        assert "Total:" in output

    @patch("agent.agent_session_queue.AgentSession")
    @patch("agent.agent_session_queue._active_workers", {})
    def test_summary_counts_mixed_status(self, mock_session_cls):
        """Summary line counts each status correctly across multiple chats."""

        def _make_session(chat_id, status, idx):
            s = MagicMock()
            s.chat_id = chat_id
            s.project_key = "proj"
            s.status = status
            s.agent_session_id = f"session-{idx:03d}"
            s.created_at = 1000000.0 + idx
            s.started_at = 1000000.0 + idx
            s.message_text = f"msg {idx}"
            s.session_id = f"sid-{idx}"
            s.correlation_id = f"corr-{idx}"
            return s

        sessions = [
            _make_session("chat-A", "pending", 1),
            _make_session("chat-A", "running", 2),
            _make_session("chat-A", "completed", 3),
            _make_session("chat-B", "pending", 4),
            _make_session("chat-B", "completed", 5),
            _make_session("chat-B", "completed", 6),
        ]
        mock_session_cls.query.all.return_value = sessions

        from agent.agent_session_queue import _cli_show_status

        captured = StringIO()
        sys.stdout = captured
        try:
            _cli_show_status()
        finally:
            sys.stdout = sys.__stdout__
        output = captured.getvalue()

        assert "2 pending" in output
        assert "1 running" in output
        assert "3 completed" in output
        assert "Total: 6 sessions" in output
