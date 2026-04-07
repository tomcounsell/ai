"""Unit tests for session hierarchy: parent-child session decomposition (issue #359).

Tests cover:
- AgentSession.parent_agent_session_id field
- get_parent(), get_children(), get_completion_progress() helpers
- _finalize_parent_sync() completion propagation logic (models.session_lifecycle)
- _transition_parent() status transitions (agent.agent_session_queue wrapper)
- _agent_session_hierarchy_health_check() orphan and stuck parent detection
- agent_session_scheduler --parent-session flag and children subcommand
"""

import time
from unittest.mock import MagicMock, patch

import pytest

# ===================================================================
# AgentSession hierarchy helpers (unit, no Redis)
# ===================================================================


class TestAgentSessionHierarchyHelpers:
    """Test hierarchy helper methods with mocked Redis queries."""

    def _make_session(self, **kwargs):
        """Create a mock AgentSession with given attributes."""
        session = MagicMock()
        session.agent_session_id = kwargs.get("agent_session_id", "test-session-1")
        session.session_id = kwargs.get("session_id", "test-session-1")
        session.status = kwargs.get("status", "pending")
        session.parent_agent_session_id = kwargs.get("parent_agent_session_id", None)
        session.message_text = kwargs.get("message_text", "test message")
        session.priority = kwargs.get("priority", "normal")
        session.created_at = kwargs.get("created_at", time.time())
        session.issue_url = kwargs.get("issue_url", None)
        session.scheduled_at = kwargs.get("scheduled_at", None)
        session.started_at = kwargs.get("started_at", None)
        return session

    @patch("models.agent_session.AgentSession.query")
    def test_get_parent_returns_parent(self, mock_query):
        """get_parent() returns the parent session when parent_agent_session_id is set."""
        from models.agent_session import AgentSession

        parent = self._make_session(agent_session_id="parent-1")
        mock_query.get.return_value = parent

        child = AgentSession()
        child.parent_agent_session_id = "parent-1"
        child.agent_session_id = "child-1"

        result = child.get_parent()
        assert result is not None
        mock_query.get.assert_called_once_with("parent-1")

    @patch("models.agent_session.AgentSession.query")
    def test_get_parent_returns_none_when_no_parent(self, mock_query):
        """get_parent() returns None when parent_agent_session_id is not set."""
        from models.agent_session import AgentSession

        child = AgentSession()
        child.parent_agent_session_id = None
        child.agent_session_id = "child-1"

        result = child.get_parent()
        assert result is None
        mock_query.get.assert_not_called()

    @patch("models.agent_session.AgentSession.query")
    def test_get_children_returns_children(self, mock_query):
        """get_children() returns list of child sessions."""
        from models.agent_session import AgentSession

        children = [
            self._make_session(agent_session_id="child-1", parent_agent_session_id="parent-1"),
            self._make_session(agent_session_id="child-2", parent_agent_session_id="parent-1"),
        ]
        mock_query.filter.return_value = children

        parent = AgentSession()
        parent.agent_session_id = "parent-1"

        result = parent.get_children()
        assert len(result) == 2
        mock_query.filter.assert_called_once_with(parent_agent_session_id="parent-1")

    @patch("models.agent_session.AgentSession.query")
    def test_get_children_returns_empty_when_none(self, mock_query):
        """get_children() returns empty list when no children exist."""
        from models.agent_session import AgentSession

        mock_query.filter.return_value = []

        parent = AgentSession()
        parent.agent_session_id = "parent-1"

        result = parent.get_children()
        assert result == []

    @patch("models.agent_session.AgentSession.query")
    def test_get_completion_progress(self, mock_query):
        """get_completion_progress() returns correct counts."""
        from models.agent_session import AgentSession

        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="completed"),
            self._make_session(agent_session_id="c3", status="failed"),
            self._make_session(agent_session_id="c4", status="running"),
            self._make_session(agent_session_id="c5", status="pending"),
        ]
        mock_query.filter.return_value = children

        parent = AgentSession()
        parent.agent_session_id = "parent-1"

        completed, total, failed = parent.get_completion_progress()
        assert completed == 2
        assert total == 5
        assert failed == 1

    @patch("models.agent_session.AgentSession.query")
    def test_get_completion_progress_no_children(self, mock_query):
        """get_completion_progress() returns zeros when no children."""
        from models.agent_session import AgentSession

        mock_query.filter.return_value = []

        parent = AgentSession()
        parent.agent_session_id = "parent-1"

        completed, total, failed = parent.get_completion_progress()
        assert completed == 0
        assert total == 0
        assert failed == 0


# ===================================================================
# _finalize_parent_sync() and _transition_parent()
# ===================================================================


class TestFinalizeParent:
    """Test completion propagation logic via lifecycle module."""

    def _make_session(self, **kwargs):
        session = MagicMock()
        for k, v in kwargs.items():
            setattr(session, k, v)
        # Ensure log_lifecycle_transition exists for lifecycle module calls
        if not hasattr(session, "log_lifecycle_transition"):
            session.log_lifecycle_transition = MagicMock()
        return session

    def test_transition_parent_completed(self):
        """_transition_parent transitions parent to completed via lifecycle module."""
        from agent.agent_session_queue import _transition_parent

        parent = self._make_session(agent_session_id="p1", status="waiting_for_children")

        _transition_parent(parent, "completed")

        # status is an IndexedField — direct mutation, no delete-and-recreate
        assert parent.status == "completed"
        assert parent.completed_at is not None
        parent.save.assert_called_once()

    def test_transition_parent_failed(self):
        """_transition_parent transitions parent to failed via lifecycle module."""
        from agent.agent_session_queue import _transition_parent

        parent = self._make_session(agent_session_id="p1", status="waiting_for_children")

        _transition_parent(parent, "failed")

        assert parent.status == "failed"
        assert parent.completed_at is not None
        parent.save.assert_called_once()

    def test_transition_parent_waiting_no_completed_at(self):
        """_transition_parent does not set completed_at for non-terminal status."""
        from agent.agent_session_queue import _transition_parent

        parent = self._make_session(agent_session_id="p1", status="running")
        parent.completed_at = None

        _transition_parent(parent, "waiting_for_children")

        # Non-terminal status should not set completed_at
        assert parent.status == "waiting_for_children"
        assert parent.completed_at is None
        parent.save.assert_called_once()

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_all_completed(self, mock_model, mock_transition):
        """_finalize_parent_sync completes parent when all children succeed."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="completed"),
        ]
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = children

        _finalize_parent_sync("p1")

        mock_transition.assert_called_once_with(parent, "completed")

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_any_failed(self, mock_model, mock_transition):
        """_finalize_parent_sync fails parent when any child fails."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="failed"),
        ]
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = children

        _finalize_parent_sync("p1")

        mock_transition.assert_called_once_with(parent, "failed")

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_skips_non_terminal_children(self, mock_model, mock_transition):
        """_finalize_parent_sync does not finalize when children still running."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="running"),
        ]
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = children

        _finalize_parent_sync("p1")

        mock_transition.assert_not_called()

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_with_completing_child_override(self, mock_model, mock_transition):
        """_finalize_parent_sync overrides status of completing child correctly.

        When called from finalize_session, the completing child's Redis status is
        still 'running'. The completing_child_id/completing_child_status params
        let _finalize_parent_sync treat it as terminal.
        """
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        # c2 is still "running" in Redis, but is the completing child
        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="running"),
        ]
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = children

        # With completing_child_id, c2's status is overridden to "completed"
        _finalize_parent_sync(
            "p1",
            completing_child_id="c2",
            completing_child_status="completed",
        )

        mock_transition.assert_called_once_with(parent, "completed")

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_completing_child_failed(self, mock_model, mock_transition):
        """_finalize_parent_sync correctly handles a completing child that failed."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(agent_session_id="c1", status="completed"),
            self._make_session(agent_session_id="c2", status="running"),
        ]
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = children

        _finalize_parent_sync(
            "p1",
            completing_child_id="c2",
            completing_child_status="failed",
        )

        mock_transition.assert_called_once_with(parent, "failed")

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_skips_already_completed(self, mock_model, mock_transition):
        """_finalize_parent_sync is idempotent — skips if parent already terminal."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="completed",
        )
        mock_model.query.get.return_value = parent

        _finalize_parent_sync("p1")

        mock_transition.assert_not_called()

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_missing_parent(self, mock_model, mock_transition):
        """_finalize_parent_sync handles missing parent gracefully."""
        from models.session_lifecycle import _finalize_parent_sync

        mock_model.query.get.return_value = None

        _finalize_parent_sync("nonexistent")

        mock_transition.assert_not_called()

    @patch("models.session_lifecycle._transition_parent")
    @patch("models.agent_session.AgentSession")
    def test_finalize_parent_sync_no_children(self, mock_model, mock_transition):
        """_finalize_parent_sync does nothing when parent has no children."""
        from models.session_lifecycle import _finalize_parent_sync

        parent = self._make_session(
            agent_session_id="p1",
            status="waiting_for_children",
        )
        mock_model.query.get.return_value = parent
        mock_model.query.filter.return_value = []

        _finalize_parent_sync("p1")

        mock_transition.assert_not_called()


# ===================================================================
# Session scheduler --parent-session flag
# ===================================================================


class TestSchedulerParentSession:
    """Test the --parent-session argument parsing and validation."""

    def test_schedule_help_includes_parent_session(self):
        """The schedule subcommand accepts --parent-session flag."""
        import sys

        from tools.agent_session_scheduler import main

        # Capture help output
        old_argv = sys.argv
        try:
            sys.argv = ["agent_session_scheduler", "schedule", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv

    def test_children_subcommand_exists(self):
        """The children subcommand is registered."""
        import sys

        from tools.agent_session_scheduler import main

        old_argv = sys.argv
        try:
            sys.argv = ["agent_session_scheduler", "children", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv


# ===================================================================
# _AGENT_SESSION_FIELDS includes parent_agent_session_id
# ===================================================================


class TestSessionFieldsIncludesParentSessionId:
    """Verify parent_agent_session_id is in the extract list."""

    def test_parent_agent_session_id_in_session_fields(self):
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        assert "parent_agent_session_id" in _AGENT_SESSION_FIELDS


# ===================================================================
# ValorAgent agent_session_id injection
# ===================================================================


class TestValorAgentSessionIdInjection:
    """Test that AGENT_SESSION_ID is injected into the env."""

    @patch("agent.sdk_client.load_system_prompt", return_value="test prompt")
    def test_agent_session_id_in_create_options_env(self, mock_prompt):
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(
            working_dir="/tmp/test",
            agent_session_id="test-session-123",
        )
        options = agent._create_options(session_id="test-session")
        assert options.env.get("AGENT_SESSION_ID") == "test-session-123"

    @patch("agent.sdk_client.load_system_prompt", return_value="test prompt")
    def test_no_agent_session_id_when_not_set(self, mock_prompt):
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(working_dir="/tmp/test")
        options = agent._create_options(session_id="test-session")
        assert "AGENT_SESSION_ID" not in options.env
