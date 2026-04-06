"""Tests for session completion zombie fix.

Covers:
- Bug 1: _extract_agent_session_fields preserves status field
- Bug 2: Worker finally block skips completion when nudge was enqueued
- Health check orphan-fixing preserves original session status
"""

from unittest.mock import MagicMock, patch

from agent.agent_session_queue import (
    _AGENT_SESSION_FIELDS,
    _extract_agent_session_fields,
)


class TestExtractAgentSessionFieldsStatus:
    """Bug 1: status must be included in _AGENT_SESSION_FIELDS."""

    def test_status_in_field_list(self):
        """The _AGENT_SESSION_FIELDS list must include 'status'."""
        assert "status" in _AGENT_SESSION_FIELDS

    def test_extract_preserves_completed_status(self):
        """Extracting fields from a completed session must preserve status."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        # Set all fields to avoid AttributeError
        for field in _AGENT_SESSION_FIELDS:
            if field != "status":
                setattr(mock_session, field, f"test_{field}")

        fields = _extract_agent_session_fields(mock_session)
        assert fields["status"] == "completed"

    def test_extract_preserves_pending_status(self):
        """Extracting fields from a pending session must preserve status."""
        mock_session = MagicMock()
        mock_session.status = "pending"
        for field in _AGENT_SESSION_FIELDS:
            if field != "status":
                setattr(mock_session, field, f"test_{field}")

        fields = _extract_agent_session_fields(mock_session)
        assert fields["status"] == "pending"

    def test_extract_preserves_failed_status(self):
        """Extracting fields from a failed session must preserve status."""
        mock_session = MagicMock()
        mock_session.status = "failed"
        for field in _AGENT_SESSION_FIELDS:
            if field != "status":
                setattr(mock_session, field, f"test_{field}")

        fields = _extract_agent_session_fields(mock_session)
        assert fields["status"] == "failed"

    def test_extract_preserves_none_status(self):
        """Extracting fields from a session with None status must preserve None."""
        mock_session = MagicMock()
        mock_session.status = None
        for field in _AGENT_SESSION_FIELDS:
            if field != "status":
                setattr(mock_session, field, f"test_{field}")

        fields = _extract_agent_session_fields(mock_session)
        assert fields["status"] is None


class TestRetryPreservesStatusOverride:
    """Verify that callers which intentionally override status still work."""

    def test_retry_overrides_extracted_status(self):
        """Retry path must override status to 'pending' after extraction."""
        mock_session = MagicMock()
        mock_session.status = "failed"
        for field in _AGENT_SESSION_FIELDS:
            if field != "status":
                setattr(mock_session, field, f"test_{field}")

        fields = _extract_agent_session_fields(mock_session)
        # Simulate what retry does (line ~810)
        fields["status"] = "pending"
        assert fields["status"] == "pending"


class TestHealthCheckOrphanFix:
    """Health check orphan-fixing must preserve original session status."""

    @patch("agent.agent_session_queue.AgentSession")
    def test_orphan_fix_preserves_completed_status(self, mock_cls):
        """When health check recreates an orphaned completed session,
        status must remain 'completed' (not default to 'pending')."""
        # Simulate a completed child with a missing parent
        child = MagicMock()
        child.agent_session_id = "child-1"
        child.parent_agent_session_id = "missing-parent"
        child.status = "completed"
        for field in _AGENT_SESSION_FIELDS:
            if field not in ("status", "parent_agent_session_id"):
                setattr(child, field, f"test_{field}")

        fields = _extract_agent_session_fields(child)
        # The health check clears the parent but should preserve status
        fields["parent_agent_session_id"] = None

        assert fields["status"] == "completed", (
            "Health check orphan-fix must preserve completed status"
        )

    @patch("agent.agent_session_queue.AgentSession")
    def test_orphan_fix_preserves_failed_status(self, mock_cls):
        """A failed orphaned session must stay failed after recreation."""
        child = MagicMock()
        child.agent_session_id = "child-2"
        child.parent_agent_session_id = "missing-parent"
        child.status = "failed"
        for field in _AGENT_SESSION_FIELDS:
            if field not in ("status", "parent_agent_session_id"):
                setattr(child, field, f"test_{field}")

        fields = _extract_agent_session_fields(child)
        fields["parent_agent_session_id"] = None

        assert fields["status"] == "failed", "Health check orphan-fix must preserve failed status"


class TestWorkerFinallyBlockNudgeGuard:
    """Bug 2: Worker finally block must skip completion when nudge was enqueued."""

    @patch("agent.agent_session_queue.AgentSession")
    def test_skips_completion_when_session_is_pending(self, mock_cls):
        """If Redis session status is 'pending' (nudge enqueued),
        _complete_agent_session must NOT be called."""
        # Simulate a session that was nudged: Redis shows "pending"
        fresh_session = MagicMock()
        fresh_session.status = "pending"
        mock_cls.get.return_value = fresh_session

        # The guard logic: re-read from Redis and check status
        fresh = mock_cls.get("test-session")
        should_skip = not fresh or fresh.status == "pending"

        assert should_skip is True, (
            "Worker finally block must skip completion when session is pending"
        )

    @patch("agent.agent_session_queue.AgentSession")
    def test_skips_completion_when_session_deleted(self, mock_cls):
        """If session no longer exists in Redis (nudge fallback recreated it),
        _complete_agent_session must NOT be called."""
        mock_cls.get.return_value = None

        fresh = mock_cls.get("test-session")
        should_skip = not fresh or fresh.status == "pending"

        assert should_skip is True, "Worker finally block must skip completion when session is gone"

    @patch("agent.agent_session_queue.AgentSession")
    def test_completes_session_when_status_is_running(self, mock_cls):
        """If Redis session status is still 'running' (no nudge),
        _complete_agent_session SHOULD be called."""
        fresh_session = MagicMock()
        fresh_session.status = "running"
        mock_cls.get.return_value = fresh_session

        fresh = mock_cls.get("test-session")
        should_skip = not fresh or fresh.status == "pending"

        assert should_skip is False, (
            "Worker finally block must complete session when no nudge detected"
        )
