"""Tests for zombie session resurrection fix (#1006).

Verifies that killed/terminal sessions found in the running index are
skipped (not recovered) by both startup recovery and health check.

The core bug: Popoto IndexedField status indexes can retain stale entries
for sessions that have already been finalized (killed, completed, etc.).
When health check or startup recovery queries filter(status="running"),
these zombie entries appear alongside legitimate running sessions. Without
a terminal-status guard, the zombie gets re-promoted to pending.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from models.session_lifecycle import TERMINAL_STATUSES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    agent_session_id="test-id-1",
    session_id="tg_proj_chat_123",
    status="running",
    started_at=None,
    session_type="pm",
    project_key="test-proj",
    worker_key=None,
    **kwargs,
):
    """Create a mock AgentSession with the given attributes."""
    session = MagicMock()
    session.agent_session_id = agent_session_id
    session.id = agent_session_id
    session.session_id = session_id
    session.status = status
    session.started_at = started_at or (time.time() - 600)  # 10 min ago by default
    session.created_at = time.time() - 700
    session.session_type = session_type
    session.project_key = project_key
    session.worker_key = worker_key or project_key
    session.is_project_keyed = True
    session.message_text = "test"
    session.turn_count = 0
    session.log_path = None
    session.claude_session_uuid = None
    session.response_delivered_at = None
    session.get_children = MagicMock(return_value=[])
    for k, v in kwargs.items():
        setattr(session, k, v)
    return session


# ---------------------------------------------------------------------------
# Startup recovery: terminal sessions must be skipped
# ---------------------------------------------------------------------------


class TestStartupRecoverySkipsTerminalSessions:
    """Startup recovery must skip sessions whose hash status is terminal."""

    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    @patch("agent.agent_session_queue.AgentSession")
    def test_terminal_session_not_recovered(self, mock_cls, terminal_status):
        """Sessions with any terminal hash status should not be recovered."""
        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        zombie = _make_session(
            status=terminal_status,
            session_id=f"tg_proj_chat_{terminal_status}",
        )
        mock_cls.query.filter.return_value = [zombie]

        count = _recover_interrupted_agent_sessions_startup()
        assert count == 0, f"Terminal session ({terminal_status}) was incorrectly recovered"

    @patch("models.session_lifecycle.update_session")
    @patch("agent.session_health.AgentSession")
    def test_legitimate_running_session_still_recovered(self, mock_cls, mock_update):
        """A truly running session (non-terminal) should still be recovered."""
        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        legit = _make_session(status="running", session_id="tg_proj_chat_legit")
        mock_cls.query.filter.return_value = [legit]

        count = _recover_interrupted_agent_sessions_startup()
        assert count == 1
        mock_update.assert_called_once()

    @patch("models.session_lifecycle.update_session")
    @patch("agent.session_health.AgentSession")
    def test_mixed_terminal_and_running_only_recovers_running(self, mock_cls, mock_update):
        """When both zombie and legitimate sessions exist, only the running one is recovered."""
        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        zombie = _make_session(
            agent_session_id="zombie-1",
            status="killed",
            session_id="tg_proj_chat_zombie",
        )
        legit = _make_session(
            agent_session_id="legit-1",
            status="running",
            session_id="tg_proj_chat_legit",
        )
        mock_cls.query.filter.return_value = [zombie, legit]

        count = _recover_interrupted_agent_sessions_startup()
        assert count == 1
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# Health check: terminal sessions must be skipped
# ---------------------------------------------------------------------------


class TestHealthCheckSkipsTerminalSessions:
    """Health check must skip sessions whose hash status is terminal."""

    @pytest.mark.parametrize("terminal_status", ["killed", "completed", "abandoned"])
    @pytest.mark.asyncio
    @patch("agent.agent_session_queue._active_workers", {})
    @patch("agent.agent_session_queue._active_events", {})
    @patch("agent.agent_session_queue.AgentSession")
    async def test_terminal_session_not_recovered_by_health_check(self, mock_cls, terminal_status):
        """Health check should skip a terminal session found in running index."""
        from agent.agent_session_queue import _agent_session_health_check

        zombie = _make_session(
            status=terminal_status,
            session_id=f"tg_proj_chat_{terminal_status}",
        )

        def mock_filter(**kwargs):
            if kwargs.get("status") == "running":
                return [zombie]
            return []

        mock_cls.query.filter.side_effect = mock_filter

        # Should complete without attempting recovery on the zombie
        await _agent_session_health_check()


# ---------------------------------------------------------------------------
# Terminal status constants consistency
# ---------------------------------------------------------------------------


class TestTerminalStatusConstants:
    """Verify the terminal status set includes all expected values."""

    @pytest.mark.parametrize("status", ["completed", "failed", "killed", "abandoned", "cancelled"])
    def test_terminal_status_present(self, status):
        assert status in TERMINAL_STATUSES
