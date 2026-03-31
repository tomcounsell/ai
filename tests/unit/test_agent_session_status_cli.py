"""Tests for the session status CLI (python -m agent.agent_session_queue --status)."""

import sys
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch


class TestJobStatusCli(unittest.TestCase):
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
    def test_shows_jobs(self, mock_session_cls):
        """Status with jobs shows job info."""
        job = MagicMock()
        job.chat_id = "chat-123"
        job.project_key = "valor"
        job.status = "running"
        job.agent_session_id = "job-001"
        job.created_at = 1000000.0
        job.started_at = 1000000.0
        job.message_text = "Hello world"
        job.session_id = "sess-abc"
        job.correlation_id = "corr-xyz"
        mock_session_cls.query.all.return_value = [job]

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
        assert "job-001" in output
        assert "Total:" in output
