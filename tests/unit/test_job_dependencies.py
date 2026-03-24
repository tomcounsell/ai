"""Tests for job dependency tracking, branch mapping, checkpoint/restore, and PM controls."""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_agent_session():
    """Patch AgentSession for unit testing."""
    with patch("agent.job_queue.AgentSession") as mock_cls:
        yield mock_cls


class TestDependenciesMet:
    """Tests for _dependencies_met helper."""

    def test_no_dependencies_returns_true(self, mock_agent_session):
        """Job with no depends_on should be considered met."""
        from agent.job_queue import _dependencies_met

        job = MagicMock()
        job.depends_on = None
        assert _dependencies_met(job) is True

    def test_empty_dependencies_returns_true(self, mock_agent_session):
        """Job with empty depends_on list should be considered met."""
        from agent.job_queue import _dependencies_met

        job = MagicMock()
        job.depends_on = []
        assert _dependencies_met(job) is True

    def test_completed_dependency_returns_true(self, mock_agent_session):
        """Job with all completed dependencies should be met."""
        from agent.job_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "completed"
        mock_agent_session.query.filter.return_value = [dep]

        job = MagicMock()
        job.depends_on = ["dep-stable-id-1"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is True

    def test_failed_dependency_returns_false(self, mock_agent_session):
        """Job with failed dependency should NOT be met."""
        from agent.job_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "failed"
        mock_agent_session.query.filter.return_value = [dep]

        job = MagicMock()
        job.depends_on = ["dep-stable-id-1"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is False

    def test_cancelled_dependency_returns_false(self, mock_agent_session):
        """Job with cancelled dependency should NOT be met."""
        from agent.job_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "cancelled"
        mock_agent_session.query.filter.return_value = [dep]

        job = MagicMock()
        job.depends_on = ["dep-stable-id-1"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is False

    def test_running_dependency_returns_false(self, mock_agent_session):
        """Job with still-running dependency should NOT be met."""
        from agent.job_queue import _dependencies_met

        dep = MagicMock()
        dep.status = "running"
        mock_agent_session.query.filter.return_value = [dep]

        job = MagicMock()
        job.depends_on = ["dep-stable-id-1"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is False

    def test_missing_dependency_returns_false(self, mock_agent_session):
        """Job with missing dependency should NOT be met (conservative)."""
        from agent.job_queue import _dependencies_met

        mock_agent_session.query.filter.return_value = []

        job = MagicMock()
        job.depends_on = ["nonexistent-stable-id"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is False

    def test_multiple_deps_all_completed(self, mock_agent_session):
        """Job with multiple completed dependencies should be met."""
        from agent.job_queue import _dependencies_met

        dep1 = MagicMock()
        dep1.status = "completed"
        dep2 = MagicMock()
        dep2.status = "completed"

        # Return different deps for different filter calls
        mock_agent_session.query.filter.side_effect = [[dep1], [dep2]]

        job = MagicMock()
        job.depends_on = ["dep-1", "dep-2"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is True

    def test_multiple_deps_one_failed(self, mock_agent_session):
        """Job with one failed dependency should NOT be met."""
        from agent.job_queue import _dependencies_met

        dep1 = MagicMock()
        dep1.status = "completed"
        dep2 = MagicMock()
        dep2.status = "failed"

        mock_agent_session.query.filter.side_effect = [[dep1], [dep2]]

        job = MagicMock()
        job.depends_on = ["dep-1", "dep-2"]
        job.job_id = "test-job"
        assert _dependencies_met(job) is False

    def test_none_entry_in_depends_on_skipped(self, mock_agent_session):
        """None entries in depends_on list should be skipped."""
        from agent.job_queue import _dependencies_met

        job = MagicMock()
        job.depends_on = [None, ""]
        job.job_id = "test-job"
        assert _dependencies_met(job) is True


class TestDependencyStatus:
    """Tests for dependency_status helper."""

    def test_no_dependencies(self, mock_agent_session):
        """Job with no depends_on should return empty dict."""
        from agent.job_queue import dependency_status

        job = MagicMock()
        job.depends_on = None
        assert dependency_status(job) == {}

    def test_reports_statuses(self, mock_agent_session):
        """Should report status of each dependency."""
        from agent.job_queue import dependency_status

        dep = MagicMock()
        dep.status = "completed"
        mock_agent_session.query.filter.return_value = [dep]

        job = MagicMock()
        job.depends_on = ["dep-1"]
        result = dependency_status(job)
        assert result["dep-1"] == "completed"

    def test_reports_missing(self, mock_agent_session):
        """Should report 'missing' for non-existent dependencies."""
        from agent.job_queue import dependency_status

        mock_agent_session.query.filter.return_value = []

        job = MagicMock()
        job.depends_on = ["missing-dep"]
        result = dependency_status(job)
        assert result["missing-dep"] == "missing"


class TestResolveBranchForStage:
    """Tests for resolve_branch_for_stage."""

    def test_no_slug_returns_main(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage(None, "BUILD")
        assert branch == "main"
        assert needs_wt is False

    def test_no_stage_returns_main(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", None)
        assert branch == "main"
        assert needs_wt is False

    def test_plan_stage_uses_main(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "PLAN")
        assert branch == "main"
        assert needs_wt is False

    def test_issue_stage_uses_main(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "ISSUE")
        assert branch == "main"
        assert needs_wt is False

    def test_build_stage_uses_session_branch(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "BUILD")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_test_stage_uses_session_branch(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "TEST")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_review_stage_uses_session_branch(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "REVIEW")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_merge_stage_no_worktree(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "MERGE")
        assert branch == "session/my-feature"
        assert needs_wt is False

    def test_case_insensitive(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "build")
        assert branch == "session/my-feature"
        assert needs_wt is True

    def test_unknown_stage_falls_back(self):
        from agent.job_queue import resolve_branch_for_stage

        branch, needs_wt = resolve_branch_for_stage("my-feature", "UNKNOWN")
        assert branch == "main"
        assert needs_wt is False


class TestCheckpointBranchState:
    """Tests for checkpoint_branch_state."""

    def test_no_working_dir_skips(self):
        from agent.job_queue import checkpoint_branch_state

        job = MagicMock()
        job.working_dir = None
        checkpoint_branch_state(job)
        job.save.assert_not_called()

    @patch("agent.job_queue.subprocess.run")
    def test_saves_branch_and_commit(self, mock_run):
        from agent.job_queue import checkpoint_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="session/my-feature\n"),
            MagicMock(returncode=0, stdout="abc123def456\n"),
        ]

        job = MagicMock()
        job.working_dir = "/tmp/test"
        job.session_id = "test-session"
        checkpoint_branch_state(job)

        assert job.branch_name == "session/my-feature"
        assert job.commit_sha == "abc123def456"
        job.save.assert_called_once()

    @patch("agent.job_queue.subprocess.run")
    def test_handles_git_failure(self, mock_run):
        from agent.job_queue import checkpoint_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="not a git repo"),
            MagicMock(returncode=1, stderr="not a git repo"),
        ]

        job = MagicMock()
        job.working_dir = "/tmp/test"
        job.session_id = "test-session"
        checkpoint_branch_state(job)
        job.save.assert_not_called()


class TestRestoreBranchState:
    """Tests for restore_branch_state."""

    def test_no_checkpoint_data_returns_true(self):
        from agent.job_queue import restore_branch_state

        job = MagicMock()
        job.working_dir = "/tmp/test"
        job.branch_name = None
        job.commit_sha = None
        job.session_id = "test-session"
        assert restore_branch_state(job) is True

    def test_no_working_dir_returns_true(self):
        from agent.job_queue import restore_branch_state

        job = MagicMock()
        job.working_dir = None
        job.branch_name = "main"
        job.commit_sha = "abc123"
        job.session_id = "test-session"
        assert restore_branch_state(job) is True

    @patch("agent.job_queue.subprocess.run")
    def test_matching_branch_and_ancestor(self, mock_run):
        from agent.job_queue import restore_branch_state

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="session/my-feature\n"),  # current branch
            MagicMock(returncode=0),  # ancestor check
        ]

        job = MagicMock()
        job.working_dir = "/tmp/test"
        job.branch_name = "session/my-feature"
        job.commit_sha = "abc123"
        job.session_id = "test-session"
        assert restore_branch_state(job) is True


class TestReorderJob:
    """Tests for reorder_job PM control."""

    def test_reorder_changes_priority(self, mock_agent_session):
        from agent.job_queue import reorder_job

        job = MagicMock()
        job.status = "pending"
        job.job_id = "old-id"
        mock_agent_session.query.get.return_value = job

        new_job = MagicMock()
        new_job.job_id = "new-id"
        mock_agent_session.create.return_value = new_job

        result = reorder_job("old-id", "urgent")
        assert result is True
        job.delete.assert_called_once()

    def test_reorder_invalid_priority(self, mock_agent_session):
        from agent.job_queue import reorder_job

        result = reorder_job("some-id", "invalid")
        assert result is False

    def test_reorder_non_pending_fails(self, mock_agent_session):
        from agent.job_queue import reorder_job

        job = MagicMock()
        job.status = "running"
        mock_agent_session.query.get.return_value = job

        result = reorder_job("some-id", "high")
        assert result is False


class TestCancelJob:
    """Tests for cancel_job PM control."""

    def test_cancel_pending_job(self, mock_agent_session):
        from agent.job_queue import cancel_job

        job = MagicMock()
        job.status = "pending"
        job.job_id = "old-id"
        job.stable_job_id = "stable-1"
        mock_agent_session.query.get.return_value = job

        new_job = MagicMock()
        new_job.job_id = "new-id"
        new_job.stable_job_id = "stable-1"
        mock_agent_session.create.return_value = new_job

        result = cancel_job("old-id")
        assert result is True
        job.delete.assert_called_once()

    def test_cancel_running_fails(self, mock_agent_session):
        from agent.job_queue import cancel_job

        job = MagicMock()
        job.status = "running"
        mock_agent_session.query.get.return_value = job

        result = cancel_job("some-id")
        assert result is False


class TestRetryJob:
    """Tests for retry_job PM control."""

    def test_retry_failed_job(self, mock_agent_session):
        from agent.job_queue import retry_job

        job = MagicMock()
        job.status = "failed"
        job.job_id = "old-id"
        mock_agent_session.query.filter.return_value = [job]

        new_job = MagicMock()
        new_job.job_id = "new-id"
        new_job.stable_job_id = "new-stable"
        mock_agent_session.create.return_value = new_job

        result = retry_job("old-stable-id")
        assert result is not None

    def test_retry_cancelled_job(self, mock_agent_session):
        from agent.job_queue import retry_job

        job = MagicMock()
        job.status = "cancelled"
        job.job_id = "old-id"
        mock_agent_session.query.filter.return_value = [job]

        new_job = MagicMock()
        mock_agent_session.create.return_value = new_job

        result = retry_job("old-stable-id")
        assert result is not None

    def test_retry_running_fails(self, mock_agent_session):
        from agent.job_queue import retry_job

        job = MagicMock()
        job.status = "running"
        mock_agent_session.query.filter.return_value = [job]

        result = retry_job("some-stable-id")
        assert result is None

    def test_retry_not_found(self, mock_agent_session):
        from agent.job_queue import retry_job

        mock_agent_session.query.filter.return_value = []
        result = retry_job("nonexistent")
        assert result is None


class TestGetQueueStatus:
    """Tests for get_queue_status PM control."""

    def test_empty_queue(self, mock_agent_session):
        from agent.job_queue import get_queue_status

        mock_agent_session.query.filter.return_value = []
        result = get_queue_status("chat-1")
        assert result["pending"] == []
        assert result["running"] == []
        assert result["completed"] == []
        assert result["failed"] == []
        assert result["cancelled"] == []

    def test_categorizes_jobs(self, mock_agent_session):
        from agent.job_queue import get_queue_status

        pending_job = MagicMock()
        pending_job.status = "pending"
        pending_job.job_id = "j1"
        pending_job.stable_job_id = "s1"
        pending_job.session_id = "sess-1"
        pending_job.message_text = "test msg"
        pending_job.priority = "normal"
        pending_job.depends_on = None
        pending_job.created_at = 1000.0
        pending_job.started_at = None

        running_job = MagicMock()
        running_job.status = "running"
        running_job.job_id = "j2"
        running_job.stable_job_id = "s2"
        running_job.session_id = "sess-2"
        running_job.message_text = "running msg"
        running_job.priority = "high"
        running_job.depends_on = ["s1"]
        running_job.created_at = 2000.0
        running_job.started_at = 2001.0

        mock_agent_session.query.filter.return_value = [pending_job, running_job]
        result = get_queue_status("chat-1")
        assert len(result["pending"]) == 1
        assert len(result["running"]) == 1
        assert result["pending"][0]["job_id"] == "j1"
        assert result["running"][0]["job_id"] == "j2"


class TestTerminalStatuses:
    """Verify cancelled is included in terminal statuses."""

    def test_cancelled_in_terminal_statuses(self):
        from agent.job_queue import _TERMINAL_STATUSES

        assert "cancelled" in _TERMINAL_STATUSES
        assert "completed" in _TERMINAL_STATUSES
        assert "failed" in _TERMINAL_STATUSES


class TestPopJobDependencyFiltering:
    """Tests for _pop_job dependency filtering integration."""

    def test_pop_skips_blocked_jobs(self, mock_agent_session):
        """Jobs with unmet dependencies should be skipped by _pop_job."""
        from agent.job_queue import _pop_job

        # Create a job with an unmet dependency
        blocked_job = MagicMock()
        blocked_job.scheduled_after = None
        blocked_job.depends_on = ["unmet-dep"]
        blocked_job.job_id = "blocked"
        blocked_job.priority = "normal"
        blocked_job.created_at = 1000.0

        mock_agent_session.query.async_filter = AsyncMock(return_value=[blocked_job])

        # Dependency lookup returns nothing (missing dep)
        mock_agent_session.query.filter.return_value = []

        result = asyncio.run(_pop_job("chat-1"))
        # Should return None since the only job is blocked
        assert result is None

    def test_pop_picks_unblocked_job(self, mock_agent_session):
        """Jobs without dependencies should be picked normally."""
        from agent.job_queue import Job, _pop_job

        unblocked_job = MagicMock()
        unblocked_job.scheduled_after = None
        unblocked_job.depends_on = None
        unblocked_job.job_id = "unblocked"
        unblocked_job.priority = "normal"
        unblocked_job.created_at = 1000.0
        unblocked_job.session_id = "sess-1"
        unblocked_job.async_delete = AsyncMock()

        new_job = MagicMock()
        new_job.job_id = "new-unblocked"
        new_job.session_id = "sess-1"

        mock_agent_session.query.async_filter = AsyncMock(return_value=[unblocked_job])
        mock_agent_session.async_create = AsyncMock(return_value=new_job)

        result = asyncio.run(_pop_job("chat-1"))
        assert result is not None
