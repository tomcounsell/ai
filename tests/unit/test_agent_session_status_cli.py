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
        session.worker_key = "chat-123"
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
            s.worker_key = chat_id
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

    @patch("agent.agent_session_queue.AgentSession")
    def test_slug_keyed_session_groups_by_slug(self, mock_session_cls):
        """Slugged sessions sharing chat_id group under slug (worker_key), not chat_id.

        Regression for PR #1087 tech-debt finding: _cli_show_status must use
        session.worker_key (the canonical routing key) for grouping and active-worker
        lookup, not a fresh chat_id/project_key recomputation — otherwise slug-keyed
        workers are misreported as DEAD/missing.
        """
        session_a = MagicMock()
        session_a.chat_id = "0"
        session_a.project_key = "valor"
        session_a.worker_key = "feat-A"
        session_a.status = "running"
        session_a.agent_session_id = "session-A"
        session_a.created_at = 1000000.0
        session_a.started_at = 1000000.0
        session_a.message_text = "slug A"
        session_a.session_id = "sid-A"
        session_a.correlation_id = "cid-A"

        session_b = MagicMock()
        session_b.chat_id = "0"
        session_b.project_key = "valor"
        session_b.worker_key = "feat-B"
        session_b.status = "running"
        session_b.agent_session_id = "session-B"
        session_b.created_at = 1000000.0
        session_b.started_at = 1000000.0
        session_b.message_text = "slug B"
        session_b.session_id = "sid-B"
        session_b.correlation_id = "cid-B"

        mock_session_cls.query.all.return_value = [session_a, session_b]

        # Simulate one alive slug-keyed worker, one missing — verifies lookup uses worker_key
        live_worker = MagicMock()
        live_worker.done.return_value = False
        with patch("agent.agent_session_queue._active_workers", {"feat-A": live_worker}):
            from agent.agent_session_queue import _cli_show_status

            captured = StringIO()
            sys.stdout = captured
            try:
                _cli_show_status()
            finally:
                sys.stdout = sys.__stdout__
            output = captured.getvalue()

        # Both slugs appear as worker keys in the grouping header
        assert "worker: feat-A" in output
        assert "worker: feat-B" in output
        # feat-A should be reported alive, feat-B as DEAD/missing
        lines = output.splitlines()
        feat_a_idx = next(i for i, line in enumerate(lines) if "worker: feat-A" in line)
        feat_b_idx = next(i for i, line in enumerate(lines) if "worker: feat-B" in line)
        assert "alive" in lines[feat_a_idx + 1]
        assert "DEAD/missing" in lines[feat_b_idx + 1]


class TestSessionFlushStuckCli(unittest.TestCase):
    """Test _cli_flush_stuck worker-key lookup."""

    @patch("agent.agent_session_queue._cli_recover_single_agent_session")
    @patch("agent.agent_session_queue.AgentSession")
    def test_slug_keyed_running_session_with_live_worker_is_skipped(
        self, mock_session_cls, mock_recover
    ):
        """A slug-keyed worker that is alive must NOT be treated as orphaned.

        Regression for PR #1087 tech-debt finding: _cli_flush_stuck previously used
        `session.chat_id or session.project_key` to look up _active_workers, which
        missed slug-keyed workers entirely — a healthy slug-keyed worker was classified
        as DEAD and _cli_recover_single_agent_session was called on the running session.
        """
        session = MagicMock()
        session.chat_id = "0"
        session.project_key = "valor"
        session.worker_key = "feat-A"
        session.status = "running"
        session.agent_session_id = "session-A"
        mock_session_cls.query.filter.return_value = [session]

        live_worker = MagicMock()
        live_worker.done.return_value = False
        with patch("agent.agent_session_queue._active_workers", {"feat-A": live_worker}):
            from agent.agent_session_queue import _cli_flush_stuck

            captured = StringIO()
            sys.stdout = captured
            try:
                _cli_flush_stuck()
            finally:
                sys.stdout = sys.__stdout__

        # Healthy worker must NOT trigger recovery
        mock_recover.assert_not_called()
        assert "worker still alive" in captured.getvalue()

    @patch("agent.agent_session_queue._cli_recover_single_agent_session")
    @patch("agent.agent_session_queue.AgentSession")
    def test_slug_keyed_running_session_with_dead_worker_is_recovered(
        self, mock_session_cls, mock_recover
    ):
        """A slug-keyed worker that is missing from _active_workers must be recovered."""
        session = MagicMock()
        session.chat_id = "0"
        session.project_key = "valor"
        session.worker_key = "feat-A"
        session.status = "running"
        session.agent_session_id = "session-A"
        mock_session_cls.query.filter.return_value = [session]

        with patch("agent.agent_session_queue._active_workers", {}):
            from agent.agent_session_queue import _cli_flush_stuck

            captured = StringIO()
            sys.stdout = captured
            try:
                _cli_flush_stuck()
            finally:
                sys.stdout = sys.__stdout__

        mock_recover.assert_called_once_with(session)
        output = captured.getvalue()
        assert "Recovering orphaned session session-A" in output
        assert "worker_key=feat-A" in output
