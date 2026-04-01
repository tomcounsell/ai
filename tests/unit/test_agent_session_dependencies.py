"""Tests for session dependency tracking, branch mapping, checkpoint/restore, PM controls."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_agent_session():
    """Patch AgentSession for unit testing."""
    with patch("agent.agent_session_queue.AgentSession") as mock_cls:
        yield mock_cls


class TestDependenciesMet:
    """Tests for _dependencies_met helper."""

    def test_no_dependencies_returns_true(self, mock_agent_session):
        """Session with no depends_on should be considered met."""
        from agent.agent_session_queue import _dependencies_met

        session = MagicMock()
        session.depends_on = None
        assert _dependencies_met(session) is True

    def test_empty_dependencies_returns_true(self, mock_agent_session):
        """Session with empty depends_on list should be considered met."""
        from agent.agent_session_queue import _dependencies_met

        session = MagicMock()
        session.depends_on = []
        assert _dependencies_met(session) is True

    def test_completed_dependency_returns_true(self, mock_agent_session):
        """Session with all completed dependencies should be met."""
        from agent.agent_session_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "completed"
        mock_agent_session.query.filter.return_value = [dep]

        session = MagicMock()
        session.depends_on = ["dep-stable-id-1"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is True

    def test_failed_dependency_returns_false(self, mock_agent_session):
        """Session with failed dependency should NOT be met."""
        from agent.agent_session_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "failed"
        mock_agent_session.query.filter.return_value = [dep]

        session = MagicMock()
        session.depends_on = ["dep-stable-id-1"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is False

    def test_cancelled_dependency_returns_false(self, mock_agent_session):
        """Session with cancelled dependency should NOT be met."""
        from agent.agent_session_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "cancelled"
        mock_agent_session.query.filter.return_value = [dep]

        session = MagicMock()
        session.depends_on = ["dep-stable-id-1"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is False

    def test_running_dependency_returns_false(self, mock_agent_session):
        """Session with still-running dependency should NOT be met."""
        from agent.agent_session_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "running"
        mock_agent_session.query.filter.return_value = [dep]

        session = MagicMock()
        session.depends_on = ["dep-stable-id-1"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is False

    def test_missing_dependency_returns_false(self, mock_agent_session):
        """Session with missing dependency should NOT be met (conservative)."""
        from agent.agent_session_queue import _dependencies_met

        mock_agent_session.query.filter.return_value = []

        session = MagicMock()
        session.depends_on = ["nonexistent-stable-id"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is False

    def test_multiple_deps_all_completed(self, mock_agent_session):
        """Session with multiple completed dependencies should be met."""
        from agent.agent_session_queue import _dependencies_met

        dep1 = MagicMock()
        dep1.status = "completed"
        dep2 = MagicMock()
        dep2.status = "completed"

        # Return different deps for different filter calls
        mock_agent_session.query.filter.side_effect = [[dep1], [dep2]]

        session = MagicMock()
        session.depends_on = ["dep-1", "dep-2"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is True

    def test_multiple_deps_one_failed(self, mock_agent_session):
        """Session with one failed dependency should NOT be met."""
        from agent.agent_session_queue import _dependencies_met

        dep1 = MagicMock()
        dep1.status = "completed"
        dep2 = MagicMock()
        dep2.status = "failed"

        mock_agent_session.query.filter.side_effect = [[dep1], [dep2]]

        session = MagicMock()
        session.depends_on = ["dep-1", "dep-2"]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is False

    def test_none_entry_in_depends_on_skipped(self, mock_agent_session):
        """None entries in depends_on list should be skipped."""
        from agent.agent_session_queue import _dependencies_met

        session = MagicMock()
        session.depends_on = [None, ""]
        session.agent_session_id = "test-session"
        assert _dependencies_met(session) is True


class TestDependencyStatus:
    """Tests for dependency_status helper."""

    def test_no_dependencies(self, mock_agent_session):
        """Session with no depends_on should return empty dict."""
        from agent.agent_session_queue import dependency_status

        session = MagicMock()
        session.depends_on = None
        assert dependency_status(session) == {}

    def test_reports_statuses(self, mock_agent_session):
        """Should report status of each dependency."""
        from agent.agent_session_queue import dependency_status

        dep = MagicMock()
        dep.status = "completed"
        mock_agent_session.query.filter.return_value = [dep]

        session = MagicMock()
        session.depends_on = ["dep-1"]
        result = dependency_status(session)
        assert result["dep-1"] == "completed"

    def test_reports_missing(self, mock_agent_session):
        """Should report 'missing' for non-existent dependencies."""
        from agent.agent_session_queue import dependency_status

        mock_agent_session.query.filter.return_value = []

        session = MagicMock()
        session.depends_on = ["missing-dep"]
        result = dependency_status(session)
        assert result["missing-dep"] == "missing"


class TestResolveBranchForStage:
    """Tests for resolve_branch_for_stage."""

    def test_no_slug_returns_main(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage(None, "BUILD")
        assert branch == "main"
        assert needs_wt is False

    def test_no_stage_returns_main(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", None)
        assert branch == "main"
        assert needs_wt is False

    def test_plan_stage_uses_main(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "PLAN")
        assert branch == "main"
        assert needs_wt is False

    def test_issue_stage_uses_main(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "ISSUE")
        assert branch == "main"
        assert needs_wt is False

    def test_build_stage_uses_session_branch(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "BUILD")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_test_stage_uses_session_branch(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "TEST")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_review_stage_uses_session_branch(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "REVIEW")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_merge_stage_no_worktree(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "MERGE")
        assert branch == "session/my-feature"
        assert needs_wt is False

    def test_case_insensitive(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "build")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_unknown_stage_falls_back(self):
        from agent.agent_session_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "UNKNOWN")
        assert branch == "main"
        assert needs_wt is False


class TestCheckpointBranchState:
    """Tests for checkpoint_branch_state."""

    def test_no_working_dir_skips(self):
        from agent.agent_session_queue import checkpoint_branch_state

        session = MagicMock()
        session.working_dir = None
        checkpoint_branch_state(session)
        session.save.assert_not_called()

    @patch("agent.agent_session_queue.subprocess.run")
    def test_saves_branch_and_commit(self, mock_run):
        from agent.agent_session_queue import checkpoint_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="session/my-feature\n"),
            MagicMock(returncode=0, stdout="abc123def456\n"),
        ]

        session = MagicMock()
        session.working_dir = "/tmp/test"
        session.session_id = "test-session"
        checkpoint_branch_state(session)

        assert session.branch_name == "session/my-feature"
        assert session.commit_sha == "abc123def456"
        session.save.assert_called_once()

    @patch("agent.agent_session_queue.subprocess.run")
    def test_handles_git_failure(self, mock_run):
        from agent.agent_session_queue import checkpoint_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="not a git repo"),
            MagicMock(returncode=1, stderr="not a git repo"),
        ]

        session = MagicMock()
        session.working_dir = "/tmp/test"
        session.session_id = "test-session"
        checkpoint_branch_state(session)
        session.save.assert_not_called()


class TestRestoreBranchState:
    """Tests for restore_branch_state."""

    def test_no_checkpoint_data_returns_true(self):
        from agent.agent_session_queue import restore_branch_state

        session = MagicMock()
        session.working_dir = "/tmp/test"
        session.branch_name = None
        session.commit_sha = None
        session.session_id = "test-session"
        assert restore_branch_state(session) is True

    def test_no_working_dir_returns_true(self):
        from agent.agent_session_queue import restore_branch_state

        session = MagicMock()
        session.working_dir = None
        session.branch_name = "main"
        session.commit_sha = "abc123"
        session.session_id = "test-session"
        assert restore_branch_state(session) is True

    @patch("agent.agent_session_queue.subprocess.run")
    def test_matching_branch_and_ancestor(self, mock_run):
        from agent.agent_session_queue import restore_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="session/my-feature\n"),  # current branch
            MagicMock(returncode=0),  # ancestor check
        ]

        session = MagicMock()
        session.working_dir = "/tmp/test"
        session.branch_name = "session/my-feature"
        session.commit_sha = "abc123"
        session.session_id = "test-session"
        assert restore_branch_state(session) is True


class TestReorderSession:
    """Tests for reorder_agent_session PM control."""

    def test_reorder_changes_priority(self, mock_agent_session):
        from agent.agent_session_queue import reorder_agent_session

        session = MagicMock()
        session.status = "pending"
        session.agent_session_id = "old-id"
        mock_agent_session.query.get.return_value = session

        result = reorder_agent_session("old-id", "urgent")
        assert result is True
        # priority is a regular Field — reorder now mutates in place (no delete-and-recreate)
        assert session.priority == "urgent"
        session.save.assert_called_once()

    def test_reorder_invalid_priority(self, mock_agent_session):
        from agent.agent_session_queue import reorder_agent_session

        result = reorder_agent_session("some-id", "invalid")
        assert result is False

    def test_reorder_non_pending_fails(self, mock_agent_session):
        from agent.agent_session_queue import reorder_agent_session

        session = MagicMock()
        session.status = "running"
        mock_agent_session.query.get.return_value = session

        result = reorder_agent_session("some-id", "high")
        assert result is False


class TestCancelSession:
    """Tests for cancel_agent_session PM control."""

    def test_cancel_pending_session(self, mock_agent_session):
        from agent.agent_session_queue import cancel_agent_session

        session = MagicMock()
        session.status = "pending"
        session.agent_session_id = "old-id"
        session.stable_agent_session_id = "stable-1"
        mock_agent_session.query.get.return_value = session

        result = cancel_agent_session("old-id")
        assert result is True
        # status is an IndexedField — cancel now mutates in place (no delete-and-recreate)
        assert session.status == "cancelled"
        session.save.assert_called_once()

    def test_cancel_running_fails(self, mock_agent_session):
        from agent.agent_session_queue import cancel_agent_session

        session = MagicMock()
        session.status = "running"
        mock_agent_session.query.get.return_value = session

        result = cancel_agent_session("some-id")
        assert result is False


class TestRetrySession:
    """Tests for retry_agent_session PM control."""

    def test_retry_failed_session(self, mock_agent_session):
        from agent.agent_session_queue import retry_agent_session

        session = MagicMock()
        session.status = "failed"
        session.agent_session_id = "old-id"
        mock_agent_session.query.filter.return_value = [session]

        new_session = MagicMock()
        new_session.agent_session_id = "new-id"
        new_session.stable_agent_session_id = "new-stable"
        mock_agent_session.create.return_value = new_session

        result = retry_agent_session("old-stable-id")
        assert result is not None

    def test_retry_cancelled_session(self, mock_agent_session):
        from agent.agent_session_queue import retry_agent_session

        session = MagicMock()
        session.status = "cancelled"
        session.agent_session_id = "old-id"
        mock_agent_session.query.filter.return_value = [session]

        new_session = MagicMock()
        mock_agent_session.create.return_value = new_session

        result = retry_agent_session("old-stable-id")
        assert result is not None

    def test_retry_running_fails(self, mock_agent_session):
        from agent.agent_session_queue import retry_agent_session

        session = MagicMock()
        session.status = "running"
        mock_agent_session.query.filter.return_value = [session]

        result = retry_agent_session("some-stable-id")
        assert result is None

    def test_retry_not_found(self, mock_agent_session):
        from agent.agent_session_queue import retry_agent_session

        mock_agent_session.query.filter.return_value = []
        result = retry_agent_session("nonexistent")
        assert result is None

    def test_retry_updates_dependents(self, mock_agent_session):
        """When a session is retried, pending sessions that depend on its old
        stable_agent_session_id should be updated to reference the new stable_agent_session_id."""
        from agent.agent_session_queue import retry_agent_session

        old_stable = "old-stable-id"

        # The failed session being retried
        session = MagicMock()
        session.status = "failed"
        session.agent_session_id = "old-id"
        session.chat_id = "chat-1"

        # A pending session that depends on the old stable id
        dependent = MagicMock()
        dependent.depends_on = [old_stable, "other-dep"]
        dependent.stable_agent_session_id = "dep-stable"

        # The new session created by retry
        new_session = MagicMock()
        new_session.agent_session_id = "new-id"
        new_session.stable_agent_session_id = "new-stable-id"

        mock_agent_session.create.return_value = new_session

        # First call: filter(stable_agent_session_id=old_stable) -> [session]
        # Second call: filter(chat_id="chat-1", status="pending") -> [dependent]
        mock_agent_session.query.filter.side_effect = [
            [session],  # lookup the session to retry
            [dependent],  # pending sessions in the same chat
        ]

        result = retry_agent_session(old_stable)
        assert result is not None

        # Verify the dependent was deleted and recreated with updated deps
        dependent.delete.assert_called_once()
        # The second create call should have the updated depends_on
        assert mock_agent_session.create.call_count == 2
        second_create_kwargs = mock_agent_session.create.call_args_list[1][1]
        assert second_create_kwargs["depends_on"] == ["new-stable-id", "other-dep"]


class TestGetQueueStatus:
    """Tests for get_queue_status PM control."""

    def test_empty_queue(self, mock_agent_session):
        from agent.agent_session_queue import get_queue_status

        mock_agent_session.query.filter.return_value = []
        result = get_queue_status("chat-1")
        assert result["pending"] == []
        assert result["running"] == []
        assert result["completed"] == []
        assert result["failed"] == []
        assert result["cancelled"] == []

    def test_categorizes_sessions(self, mock_agent_session):
        from agent.agent_session_queue import get_queue_status

        pending_session = MagicMock()
        pending_session.status = "pending"
        pending_session.agent_session_id = "j1"
        pending_session.stable_agent_session_id = "s1"
        pending_session.session_id = "sess-1"
        pending_session.message_text = "test msg"
        pending_session.priority = "normal"
        pending_session.depends_on = None
        pending_session.created_at = 1000.0
        pending_session.started_at = None

        running_session = MagicMock()
        running_session.status = "running"
        running_session.agent_session_id = "j2"
        running_session.stable_agent_session_id = "s2"
        running_session.session_id = "sess-2"
        running_session.message_text = "running msg"
        running_session.priority = "high"
        running_session.depends_on = ["s1"]
        running_session.created_at = 2000.0
        running_session.started_at = 2001.0

        mock_agent_session.query.filter.return_value = [pending_session, running_session]
        result = get_queue_status("chat-1")
        assert len(result["pending"]) == 1
        assert len(result["running"]) == 1
        assert result["pending"][0]["agent_session_id"] == "j1"
        assert result["running"][0]["agent_session_id"] == "j2"


class TestTerminalStatuses:
    """Verify cancelled is included in terminal statuses."""

    def test_cancelled_in_terminal_statuses(self):
        from agent.agent_session_queue import _TERMINAL_STATUSES

        assert "cancelled" in _TERMINAL_STATUSES
        assert "completed" in _TERMINAL_STATUSES
        assert "failed" in _TERMINAL_STATUSES


class TestPopSessionDependencyFiltering:
    """Tests for _pop_agent_session dependency filtering integration."""

    def test_pop_skips_blocked_session(self, mock_agent_session):
        """Sessions with unmet dependencies should be skipped by _pop_agent_session."""
        from agent.agent_session_queue import _pop_agent_session

        # Create a session with an unmet dependency
        blocked_session = MagicMock()
        blocked_session.scheduled_at = None
        blocked_session.depends_on = ["unmet-dep"]
        blocked_session.agent_session_id = "blocked"
        blocked_session.priority = "normal"
        blocked_session.created_at = 1000.0

        mock_agent_session.query.async_filter = AsyncMock(return_value=[blocked_session])

        # Dependency lookup returns nothing (missing dep)
        mock_agent_session.query.filter.return_value = []

        result = asyncio.run(_pop_agent_session("chat-1"))
        # Should return None since the only session is blocked
        assert result is None

    def test_pop_picks_unblocked_session(self, mock_agent_session):
        """Sessions without dependencies should be picked normally."""
        from agent.agent_session_queue import _pop_agent_session

        unblocked_session = MagicMock()
        unblocked_session.scheduled_at = None
        unblocked_session.depends_on = None
        unblocked_session.agent_session_id = "unblocked"
        unblocked_session.priority = "normal"
        unblocked_session.created_at = 1000.0
        unblocked_session.session_id = "sess-1"
        # _pop_agent_session now uses direct mutation + async_save (status is IndexedField)
        unblocked_session.async_save = AsyncMock()

        mock_agent_session.query.async_filter = AsyncMock(return_value=[unblocked_session])

        result = asyncio.run(_pop_agent_session("chat-1"))
        assert result is not None
        assert unblocked_session.status == "running"
        unblocked_session.async_save.assert_called_once()
