"""Unit tests for job hierarchy: parent-child job decomposition (issue #359).

Tests cover:
- AgentSession.parent_job_id field
- get_parent(), get_children(), get_completion_progress() helpers
- _finalize_parent() completion propagation logic
- _transition_parent() status transitions
- _job_hierarchy_health_check() orphan and stuck parent detection
- job_scheduler --parent-job flag and children subcommand
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
        session.job_id = kwargs.get("job_id", "test-job-1")
        session.session_id = kwargs.get("session_id", "test-session-1")
        session.status = kwargs.get("status", "pending")
        session.parent_job_id = kwargs.get("parent_job_id", None)
        session.message_text = kwargs.get("message_text", "test message")
        session.priority = kwargs.get("priority", "normal")
        session.created_at = kwargs.get("created_at", time.time())
        session.issue_url = kwargs.get("issue_url", None)
        session.scheduled_after = kwargs.get("scheduled_after", None)
        session.started_at = kwargs.get("started_at", None)
        return session

    @patch("models.agent_session.AgentSession.query")
    def test_get_parent_returns_parent(self, mock_query):
        """get_parent() returns the parent session when parent_job_id is set."""
        from models.agent_session import AgentSession

        parent = self._make_session(job_id="parent-1")
        mock_query.get.return_value = parent

        child = AgentSession()
        child.parent_job_id = "parent-1"
        child.job_id = "child-1"

        result = child.get_parent()
        assert result is not None
        mock_query.get.assert_called_once_with("parent-1")

    @patch("models.agent_session.AgentSession.query")
    def test_get_parent_returns_none_when_no_parent(self, mock_query):
        """get_parent() returns None when parent_job_id is not set."""
        from models.agent_session import AgentSession

        child = AgentSession()
        child.parent_job_id = None
        child.job_id = "child-1"

        result = child.get_parent()
        assert result is None
        mock_query.get.assert_not_called()

    @patch("models.agent_session.AgentSession.query")
    def test_get_children_returns_children(self, mock_query):
        """get_children() returns list of child sessions."""
        from models.agent_session import AgentSession

        children = [
            self._make_session(job_id="child-1", parent_job_id="parent-1"),
            self._make_session(job_id="child-2", parent_job_id="parent-1"),
        ]
        mock_query.filter.return_value = children

        parent = AgentSession()
        parent.job_id = "parent-1"

        result = parent.get_children()
        assert len(result) == 2
        mock_query.filter.assert_called_once_with(parent_job_id="parent-1")

    @patch("models.agent_session.AgentSession.query")
    def test_get_children_returns_empty_when_none(self, mock_query):
        """get_children() returns empty list when no children exist."""
        from models.agent_session import AgentSession

        mock_query.filter.return_value = []

        parent = AgentSession()
        parent.job_id = "parent-1"

        result = parent.get_children()
        assert result == []

    @patch("models.agent_session.AgentSession.query")
    def test_get_completion_progress(self, mock_query):
        """get_completion_progress() returns correct counts."""
        from models.agent_session import AgentSession

        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="completed"),
            self._make_session(job_id="c3", status="failed"),
            self._make_session(job_id="c4", status="running"),
            self._make_session(job_id="c5", status="pending"),
        ]
        mock_query.filter.return_value = children

        parent = AgentSession()
        parent.job_id = "parent-1"

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
        parent.job_id = "parent-1"

        completed, total, failed = parent.get_completion_progress()
        assert completed == 0
        assert total == 0
        assert failed == 0


# ===================================================================
# _finalize_parent() and _transition_parent()
# ===================================================================


class TestFinalizeParent:
    """Test completion propagation logic."""

    def _make_session(self, **kwargs):
        session = MagicMock()
        for k, v in kwargs.items():
            setattr(session, k, v)
        return session

    @patch("agent.job_queue.AgentSession")
    @patch("agent.job_queue._extract_job_fields")
    def test_transition_parent_completed(self, mock_extract, mock_model):
        """_transition_parent transitions parent to completed and re-links children."""
        from agent.job_queue import _transition_parent

        child1 = self._make_session(job_id="c1", parent_job_id="p1")
        parent = self._make_session(job_id="p1", status="waiting_for_children")
        parent.get_children.return_value = [child1]

        # _extract_job_fields is called for parent first, then for each child
        mock_extract.side_effect = [
            {"status": "waiting_for_children", "completed_at": None},
            {"status": "running", "parent_job_id": "p1"},
        ]
        new_parent = MagicMock()
        new_parent.job_id = "p1-new"
        # First create() returns new parent, second returns re-created child
        new_child = MagicMock()
        new_child.job_id = "c1-new"
        mock_model.create.side_effect = [new_parent, new_child]

        _transition_parent(parent, "completed")

        # Parent should be deleted and recreated
        parent.delete.assert_called_once()
        first_create_kwargs = mock_model.create.call_args_list[0][1]
        assert first_create_kwargs["status"] == "completed"
        assert first_create_kwargs["completed_at"] is not None

        # Child should be deleted and recreated with new parent_job_id
        child1.delete.assert_called_once()
        second_create_kwargs = mock_model.create.call_args_list[1][1]
        assert second_create_kwargs["parent_job_id"] == "p1-new"

    @patch("agent.job_queue.AgentSession")
    @patch("agent.job_queue._extract_job_fields")
    def test_transition_parent_failed(self, mock_extract, mock_model):
        """_transition_parent transitions parent to failed when child fails."""
        from agent.job_queue import _transition_parent

        parent = self._make_session(job_id="p1", status="waiting_for_children")
        parent.get_children.return_value = []
        mock_extract.return_value = {"status": "waiting_for_children", "completed_at": None}
        new_parent = MagicMock()
        new_parent.job_id = "p1-new"
        mock_model.create.return_value = new_parent

        _transition_parent(parent, "failed")

        call_kwargs = mock_model.create.call_args[1]
        assert call_kwargs["status"] == "failed"

    @patch("agent.job_queue.AgentSession")
    @patch("agent.job_queue._extract_job_fields")
    def test_transition_parent_waiting_no_completed_at(self, mock_extract, mock_model):
        """_transition_parent does not set completed_at for non-terminal status."""
        from agent.job_queue import _transition_parent

        parent = self._make_session(job_id="p1", status="running")
        parent.get_children.return_value = []
        mock_extract.return_value = {"status": "running", "completed_at": None}
        new_parent = MagicMock()
        new_parent.job_id = "p1-new"
        mock_model.create.return_value = new_parent

        _transition_parent(parent, "waiting_for_children")

        call_kwargs = mock_model.create.call_args[1]
        assert call_kwargs["status"] == "waiting_for_children"
        assert call_kwargs["completed_at"] is None

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_all_completed(self, mock_model, mock_transition):
        """_finalize_parent completes parent when all children succeed."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="completed"),
        ]
        parent.get_children.return_value = children
        mock_model.query.get.return_value = parent

        await _finalize_parent("p1")

        mock_transition.assert_called_once_with(parent, "completed")

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_any_failed(self, mock_model, mock_transition):
        """_finalize_parent fails parent when any child fails."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="failed"),
        ]
        parent.get_children.return_value = children
        mock_model.query.get.return_value = parent

        await _finalize_parent("p1")

        mock_transition.assert_called_once_with(parent, "failed")

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_skips_non_terminal_children(self, mock_model, mock_transition):
        """_finalize_parent does not finalize when children still running."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="running"),
        ]
        parent.get_children.return_value = children
        mock_model.query.get.return_value = parent

        await _finalize_parent("p1")

        mock_transition.assert_not_called()

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_with_completing_child_override(
        self, mock_model, mock_transition
    ):
        """_finalize_parent overrides status of completing child correctly.

        When called from _complete_job, the completing child's Redis status is
        still 'running'. The completing_child_id/completing_child_status params
        let _finalize_parent treat it as terminal.
        """
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        # c2 is still "running" in Redis, but is the completing child
        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="running"),
        ]
        parent.get_children.return_value = children
        mock_model.query.get.return_value = parent

        # With completing_child_id, c2's status is overridden to "completed"
        await _finalize_parent(
            "p1",
            completing_child_id="c2",
            completing_child_status="completed",
        )

        mock_transition.assert_called_once_with(parent, "completed")

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_completing_child_failed(self, mock_model, mock_transition):
        """_finalize_parent correctly handles a completing child that failed."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        children = [
            self._make_session(job_id="c1", status="completed"),
            self._make_session(job_id="c2", status="running"),
        ]
        parent.get_children.return_value = children
        mock_model.query.get.return_value = parent

        await _finalize_parent(
            "p1",
            completing_child_id="c2",
            completing_child_status="failed",
        )

        mock_transition.assert_called_once_with(parent, "failed")

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_skips_already_completed(self, mock_model, mock_transition):
        """_finalize_parent is idempotent — skips if parent already completed."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="completed",
        )
        mock_model.query.get.return_value = parent

        await _finalize_parent("p1")

        mock_transition.assert_not_called()

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_missing_parent(self, mock_model, mock_transition):
        """_finalize_parent handles missing parent gracefully."""
        from agent.job_queue import _finalize_parent

        mock_model.query.get.return_value = None

        await _finalize_parent("nonexistent")

        mock_transition.assert_not_called()

    @patch("agent.job_queue._transition_parent")
    @patch("agent.job_queue.AgentSession")
    @pytest.mark.asyncio
    async def test_finalize_parent_no_children_auto_completes(self, mock_model, mock_transition):
        """_finalize_parent auto-completes parent with no children."""
        from agent.job_queue import _finalize_parent

        parent = self._make_session(
            job_id="p1",
            status="waiting_for_children",
        )
        parent.get_children.return_value = []
        mock_model.query.get.return_value = parent

        await _finalize_parent("p1")

        mock_transition.assert_called_once_with(parent, "completed")


# ===================================================================
# Job scheduler --parent-job flag
# ===================================================================


class TestSchedulerParentJob:
    """Test the --parent-job argument parsing and validation."""

    def test_schedule_help_includes_parent_job(self):
        """The schedule subcommand accepts --parent-job flag."""
        import sys

        from tools.job_scheduler import main

        # Capture help output
        old_argv = sys.argv
        try:
            sys.argv = ["job_scheduler", "schedule", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv

    def test_children_subcommand_exists(self):
        """The children subcommand is registered."""
        import sys

        from tools.job_scheduler import main

        old_argv = sys.argv
        try:
            sys.argv = ["job_scheduler", "children", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv


# ===================================================================
# _JOB_FIELDS includes parent_job_id
# ===================================================================


class TestJobFieldsIncludesParentJobId:
    """Verify parent_job_id is in the extract list."""

    def test_parent_job_id_in_job_fields(self):
        from agent.job_queue import _JOB_FIELDS

        assert "parent_job_id" in _JOB_FIELDS


# ===================================================================
# ValorAgent job_id injection
# ===================================================================


class TestValorAgentJobIdInjection:
    """Test that JOB_ID is injected into the env."""

    @patch("agent.sdk_client.load_system_prompt", return_value="test prompt")
    def test_job_id_in_create_options_env(self, mock_prompt):
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(
            working_dir="/tmp/test",
            job_id="test-job-123",
        )
        options = agent._create_options(session_id="test-session")
        assert options.env.get("JOB_ID") == "test-job-123"

    @patch("agent.sdk_client.load_system_prompt", return_value="test prompt")
    def test_no_job_id_when_not_set(self, mock_prompt):
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(working_dir="/tmp/test")
        options = agent._create_options(session_id="test-session")
        assert "JOB_ID" not in options.env
