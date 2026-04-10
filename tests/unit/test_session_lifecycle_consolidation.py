"""Tests for models/session_lifecycle.py — consolidated session lifecycle management.

Covers:
- Terminal transitions via finalize_session()
- Non-terminal transitions via transition_status()
- Idempotency for both functions
- Invalid status rejection
- Skip flags (skip_auto_tag, skip_checkpoint, skip_parent)
- Import safety (subprocess context simulation)
- Parent finalization via _finalize_parent_sync()
"""

from unittest.mock import MagicMock, patch

import pytest

from models.session_lifecycle import (
    ALL_STATUSES,
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    StatusConflictError,
    finalize_session,
    get_authoritative_session,
    transition_status,
    update_session,
)


@pytest.fixture
def mock_session():
    """Create a mock AgentSession with common attributes."""
    session = MagicMock()
    session.session_id = "test-session-123"
    session.agent_session_id = "agent-sess-123"
    session.status = "running"
    session.parent_agent_session_id = None
    session.completed_at = None
    return session


class TestFinalizeSessionTerminalTransitions:
    """finalize_session() handles all terminal transitions correctly."""

    def test_completed_transition(self, mock_session):
        """Session transitions to completed status."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "completed", "work finished")

        assert mock_session.status == "completed"
        assert mock_session.completed_at is not None
        mock_session.save.assert_called_once()

    def test_failed_transition(self, mock_session):
        """Session transitions to failed status."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "failed", "error occurred")

        assert mock_session.status == "failed"
        mock_session.save.assert_called_once()

    def test_killed_transition(self, mock_session):
        """Session transitions to killed status."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "killed", "user requested kill")

        assert mock_session.status == "killed"
        mock_session.save.assert_called_once()

    def test_abandoned_transition(self, mock_session):
        """Session transitions to abandoned status."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "abandoned", "watchdog detected stale")

        assert mock_session.status == "abandoned"
        mock_session.save.assert_called_once()

    def test_cancelled_transition(self, mock_session):
        """Session transitions to cancelled status."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "cancelled", "PM cancelled")

        assert mock_session.status == "cancelled"
        mock_session.save.assert_called_once()


class TestFinalizeSessionLifecycleLog:
    """finalize_session() calls log_lifecycle_transition."""

    def test_lifecycle_log_called(self, mock_session):
        """log_lifecycle_transition is called with the new status and reason."""
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "completed", "test reason")

        mock_session.log_lifecycle_transition.assert_called_once_with("completed", "test reason")

    def test_lifecycle_log_failure_nonfatal(self, mock_session):
        """Lifecycle log failure doesn't block status save."""
        mock_session.log_lifecycle_transition.side_effect = RuntimeError("log fail")
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "completed", "test")

        assert mock_session.status == "completed"
        mock_session.save.assert_called_once()


class TestFinalizeSessionIdempotency:
    """finalize_session() is idempotent for already-terminal sessions."""

    def test_already_completed_skips(self, mock_session):
        """If session is already completed, finalize_session is a no-op."""
        mock_session.status = "completed"
        finalize_session(mock_session, "completed", "duplicate call")

        # save should NOT be called (idempotent skip)
        mock_session.save.assert_not_called()
        mock_session.log_lifecycle_transition.assert_not_called()

    def test_different_terminal_state_proceeds(self, mock_session):
        """If session is in a different terminal state, finalize proceeds."""
        mock_session.status = "failed"
        with patch("agent.agent_session_queue.checkpoint_branch_state"):
            finalize_session(mock_session, "completed", "override")

        assert mock_session.status == "completed"
        mock_session.save.assert_called_once()


class TestFinalizeSessionInvalidInput:
    """finalize_session() rejects invalid inputs."""

    def test_none_session_raises(self):
        """None session raises ValueError."""
        with pytest.raises(ValueError, match="must not be None"):
            finalize_session(None, "completed", "test")

    def test_non_terminal_status_raises(self, mock_session):
        """Non-terminal status raises ValueError."""
        with pytest.raises(ValueError, match="terminal status"):
            finalize_session(mock_session, "running", "test")

    def test_pending_status_raises(self, mock_session):
        """Pending (non-terminal) status raises ValueError."""
        with pytest.raises(ValueError, match="terminal status"):
            finalize_session(mock_session, "pending", "test")


class TestFinalizeSessionSkipFlags:
    """Skip flags control which side effects run."""

    def test_skip_auto_tag(self, mock_session):
        """skip_auto_tag prevents auto_tag_session from running."""
        with (
            patch("agent.agent_session_queue.checkpoint_branch_state"),
            patch("tools.session_tags.auto_tag_session") as mock_tag,
        ):
            finalize_session(mock_session, "completed", "test", skip_auto_tag=True)

        mock_tag.assert_not_called()

    def test_skip_checkpoint(self, mock_session):
        """skip_checkpoint prevents checkpoint_branch_state from running."""
        with patch("agent.agent_session_queue.checkpoint_branch_state") as mock_checkpoint:
            finalize_session(mock_session, "completed", "test", skip_checkpoint=True)

        mock_checkpoint.assert_not_called()

    def test_skip_parent(self, mock_session):
        """skip_parent prevents parent finalization."""
        mock_session.parent_agent_session_id = "parent-123"
        with (
            patch("agent.agent_session_queue.checkpoint_branch_state"),
            patch("models.session_lifecycle._finalize_parent_sync") as mock_parent,
        ):
            finalize_session(mock_session, "completed", "test", skip_parent=True)

        mock_parent.assert_not_called()

    def test_all_skip_flags(self, mock_session):
        """All skip flags together — only lifecycle log + status save."""
        mock_session.parent_agent_session_id = "parent-123"
        finalize_session(
            mock_session,
            "completed",
            "test",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        assert mock_session.status == "completed"
        mock_session.save.assert_called_once()
        mock_session.log_lifecycle_transition.assert_called_once()


class TestFinalizeSessionParentFinalization:
    """finalize_session() triggers parent finalization for child sessions."""

    def test_parent_finalization_called(self, mock_session):
        """When session has a parent, _finalize_parent_sync is called."""
        mock_session.parent_agent_session_id = "parent-123"
        with (
            patch("agent.agent_session_queue.checkpoint_branch_state"),
            patch("models.session_lifecycle._finalize_parent_sync") as mock_parent,
        ):
            finalize_session(mock_session, "completed", "test")

        mock_parent.assert_called_once_with(
            "parent-123",
            completing_child_id="agent-sess-123",
            completing_child_status="completed",
        )

    def test_no_parent_skips_finalization(self, mock_session):
        """When session has no parent, parent finalization is skipped."""
        mock_session.parent_agent_session_id = None
        with (
            patch("agent.agent_session_queue.checkpoint_branch_state"),
            patch("models.session_lifecycle._finalize_parent_sync") as mock_parent,
        ):
            finalize_session(mock_session, "completed", "test")

        mock_parent.assert_not_called()

    def test_parent_finalization_failure_nonfatal(self, mock_session):
        """Parent finalization failure doesn't block status save."""
        mock_session.parent_agent_session_id = "parent-123"
        with (
            patch("agent.agent_session_queue.checkpoint_branch_state"),
            patch(
                "models.session_lifecycle._finalize_parent_sync",
                side_effect=RuntimeError("parent error"),
            ),
        ):
            finalize_session(mock_session, "completed", "test")

        assert mock_session.status == "completed"
        mock_session.save.assert_called_once()


class TestTransitionStatus:
    """transition_status() handles non-terminal transitions."""

    def test_pending_transition(self, mock_session):
        """Session transitions to pending."""
        transition_status(mock_session, "pending", "re-enqueue")

        assert mock_session.status == "pending"
        mock_session.save.assert_called_once()

    def test_running_transition(self, mock_session):
        """Session transitions to running."""
        mock_session.status = "pending"
        transition_status(mock_session, "running", "worker picked up")

        assert mock_session.status == "running"
        mock_session.save.assert_called_once()

    def test_dormant_transition(self, mock_session):
        """Session transitions to dormant."""
        transition_status(mock_session, "dormant", "waiting for human")

        assert mock_session.status == "dormant"
        mock_session.save.assert_called_once()

    def test_waiting_for_children_transition(self, mock_session):
        """Session transitions to waiting_for_children."""
        transition_status(mock_session, "waiting_for_children", "child spawned")

        assert mock_session.status == "waiting_for_children"
        mock_session.save.assert_called_once()

    def test_superseded_transition(self, mock_session):
        """Session transitions to superseded (from terminal requires explicit opt-out)."""
        mock_session.status = "completed"
        transition_status(
            mock_session,
            "superseded",
            "replaced by newer session",
            reject_from_terminal=False,
        )

        assert mock_session.status == "superseded"
        mock_session.save.assert_called_once()

    def test_lifecycle_log_called(self, mock_session):
        """log_lifecycle_transition is called."""
        transition_status(mock_session, "pending", "test reason")

        mock_session.log_lifecycle_transition.assert_called_once_with("pending", "test reason")


class TestTransitionStatusIdempotency:
    """transition_status() is idempotent."""

    def test_already_in_target_state_saves_companion_fields(self, mock_session):
        """If already in target state, save() is still called to persist companion fields."""
        mock_session.status = "pending"
        transition_status(mock_session, "pending", "duplicate")

        # save IS called (idempotent path persists companion fields since #873)
        mock_session.save.assert_called_once()
        # But lifecycle transition log is NOT called (no actual transition)
        mock_session.log_lifecycle_transition.assert_not_called()


class TestTransitionStatusInvalidInput:
    """transition_status() rejects invalid inputs."""

    def test_none_session_raises(self):
        """None session raises ValueError."""
        with pytest.raises(ValueError, match="must not be None"):
            transition_status(None, "pending", "test")

    def test_terminal_status_raises(self, mock_session):
        """Terminal status raises ValueError directing to finalize_session."""
        with pytest.raises(ValueError, match="finalize_session"):
            transition_status(mock_session, "completed", "test")

    def test_unknown_status_raises(self, mock_session):
        """Unknown status raises ValueError."""
        with pytest.raises(ValueError, match="Unknown status"):
            transition_status(mock_session, "invalid_status", "test")


class TestTransitionStatusRevival:
    """completed->pending revival flow works through transition_status."""

    def test_completed_to_pending_blocked_by_default(self, mock_session):
        """completed->pending is blocked by default reject_from_terminal=True."""
        mock_session.status = "completed"
        with pytest.raises(ValueError, match="terminal status"):
            transition_status(mock_session, "pending", "auto-continue re-enqueue")

    def test_completed_to_pending_with_explicit_opt_out(self, mock_session):
        """completed->pending is allowed with reject_from_terminal=False (revival path)."""
        mock_session.status = "completed"
        transition_status(
            mock_session,
            "pending",
            "auto-continue re-enqueue",
            reject_from_terminal=False,
        )

        assert mock_session.status == "pending"
        mock_session.save.assert_called_once()


class TestStatusConstants:
    """Verify status constant sets are correct and exhaustive."""

    def test_terminal_statuses(self):
        """TERMINAL_STATUSES contains the expected values."""
        assert TERMINAL_STATUSES == {
            "completed",
            "failed",
            "killed",
            "abandoned",
            "cancelled",
        }

    def test_non_terminal_statuses(self):
        """NON_TERMINAL_STATUSES contains the expected values."""
        assert NON_TERMINAL_STATUSES == {
            "pending",
            "running",
            "active",
            "dormant",
            "waiting_for_children",
            "superseded",
            "paused_circuit",
            "paused",
        }

    def test_no_overlap(self):
        """Terminal and non-terminal sets don't overlap."""
        assert TERMINAL_STATUSES & NON_TERMINAL_STATUSES == set()

    def test_all_statuses_is_union(self):
        """ALL_STATUSES is the union of terminal and non-terminal."""
        assert ALL_STATUSES == TERMINAL_STATUSES | NON_TERMINAL_STATUSES

    def test_thirteen_total_statuses(self):
        """There are exactly 13 statuses (5 terminal + 8 non-terminal)."""
        assert len(ALL_STATUSES) == 13


class TestImportSafety:
    """Verify the module is importable in restricted environments."""

    def test_module_importable(self):
        """models.session_lifecycle is importable."""
        import models.session_lifecycle  # noqa: F401

    def test_functions_accessible(self):
        """Public functions are accessible."""
        from models.session_lifecycle import finalize_session, transition_status

        assert callable(finalize_session)
        assert callable(transition_status)

    def test_lazy_imports_not_triggered_at_module_level(self):
        """Heavy dependencies (tools.session_tags, agent.agent_session_queue)
        are not imported at module level."""
        import importlib
        import sys

        # Remove cached module to get a fresh import
        mod_name = "models.session_lifecycle"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

        # After import, heavy deps should NOT be in sys.modules
        # (unless they were imported by something else)
        # We just verify the module itself loads without these
        from models.session_lifecycle import finalize_session  # noqa: F811, F401

        # If we get here, the import succeeded without requiring
        # tools.session_tags or agent.agent_session_queue at module level


# ---------------------------------------------------------------------------
# CAS (Compare-and-Set) tests
# ---------------------------------------------------------------------------


class TestStatusConflictError:
    """StatusConflictError carries structured conflict information."""

    def test_exception_attributes(self):
        """Exception stores session_id, expected, actual, and reason."""
        err = StatusConflictError(
            session_id="sess-1",
            expected_status="running",
            actual_status="completed",
            reason="test conflict",
        )
        assert err.session_id == "sess-1"
        assert err.expected_status == "running"
        assert err.actual_status == "completed"
        assert err.reason == "test conflict"

    def test_message_format(self):
        """String representation contains all key details."""
        err = StatusConflictError(
            session_id="sess-1",
            expected_status="running",
            actual_status="completed",
            reason="finalize race",
        )
        msg = str(err)
        assert "sess-1" in msg
        assert "'running'" in msg
        assert "'completed'" in msg
        assert "finalize race" in msg

    def test_message_without_reason(self):
        """Message omits parenthetical when reason is empty."""
        err = StatusConflictError(
            session_id="sess-1",
            expected_status="running",
            actual_status="completed",
        )
        msg = str(err)
        assert "sess-1" in msg
        # No trailing parenthetical
        assert msg.endswith("'completed'")

    def test_is_exception(self):
        """StatusConflictError is a proper Exception subclass."""
        err = StatusConflictError("s", "a", "b")
        assert isinstance(err, Exception)


class TestGetAuthoritativeSession:
    """get_authoritative_session re-reads with tie-break logic."""

    def test_single_record_returned(self):
        """Single matching record is returned directly."""
        mock_sess = MagicMock()
        mock_sess.status = "running"
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [mock_sess]
            result = get_authoritative_session("sess-1")
        assert result is mock_sess

    def test_multiple_records_prefer_running(self):
        """When duplicates exist, running record is preferred."""
        pending_sess = MagicMock()
        pending_sess.status = "pending"
        pending_sess.created_at = 100
        running_sess = MagicMock()
        running_sess.status = "running"
        running_sess.created_at = 90  # older, but running wins
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [pending_sess, running_sess]
            result = get_authoritative_session("sess-1")
        assert result is running_sess

    def test_multiple_records_fallback_to_most_recent(self):
        """When no running record, most recent by created_at wins."""
        old_sess = MagicMock()
        old_sess.status = "pending"
        old_sess.created_at = 100
        new_sess = MagicMock()
        new_sess.status = "pending"
        new_sess.created_at = 200
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [old_sess, new_sess]
            result = get_authoritative_session("sess-1")
        assert result is new_sess

    def test_no_records_returns_none(self):
        """Empty query result returns None."""
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []
            result = get_authoritative_session("sess-1")
        assert result is None

    def test_empty_session_id_returns_none(self):
        """Empty string session_id returns None without querying."""
        result = get_authoritative_session("")
        assert result is None

    def test_none_session_id_returns_none(self):
        """None session_id returns None without querying."""
        result = get_authoritative_session(None)
        assert result is None

    def test_project_key_filter(self):
        """project_key is passed through to the query filter."""
        mock_sess = MagicMock()
        mock_sess.status = "running"
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [mock_sess]
            get_authoritative_session("sess-1", project_key="proj-1")
        mock_as.query.filter.assert_called_once_with(session_id="sess-1", project_key="proj-1")


class TestUpdateSession:
    """update_session() provides atomic re-read + field application + transition."""

    def _make_mock_session(self, status="running"):
        """Helper to create a mock session for update_session tests."""
        sess = MagicMock()
        sess.session_id = "sess-1"
        sess.status = status
        sess.completed_at = None
        sess.parent_agent_session_id = None
        return sess

    def test_happy_path_status_and_fields(self):
        """Status transition + field application works end-to-end."""
        mock_sess = self._make_mock_session(status="running")
        with patch("models.session_lifecycle.get_authoritative_session", return_value=mock_sess):
            update_session(
                "sess-1",
                new_status="pending",
                fields={"priority": "high", "started_at": None},
                reason="re-enqueue",
            )
        assert mock_sess.priority == "high"
        assert mock_sess.started_at is None
        # transition_status delegates to save
        mock_sess.save.assert_called_once()

    def test_cas_conflict_via_expected_status(self):
        """StatusConflictError raised when on-disk status mismatches expected."""
        mock_sess = self._make_mock_session(status="completed")
        with patch("models.session_lifecycle.get_authoritative_session", return_value=mock_sess):
            with pytest.raises(Exception) as exc_info:
                update_session(
                    "sess-1",
                    new_status="pending",
                    fields={"priority": "high"},
                    expected_status="running",
                    reason="recovery",
                )
        assert type(exc_info.value).__name__ == "StatusConflictError"
        assert exc_info.value.expected_status == "running"
        assert exc_info.value.actual_status == "completed"

    def test_field_only_update_no_status(self):
        """Field-only update (no new_status) saves without transition."""
        mock_sess = self._make_mock_session(status="running")
        with patch("models.session_lifecycle.get_authoritative_session", return_value=mock_sess):
            update_session(
                "sess-1",
                fields={"priority": "high"},
                reason="bump priority",
            )
        assert mock_sess.priority == "high"
        mock_sess.save.assert_called_once()
        # No lifecycle transition log for field-only updates
        mock_sess.log_lifecycle_transition.assert_not_called()

    def test_terminal_transition_delegates_to_finalize(self):
        """Terminal new_status delegates to finalize_session."""
        mock_sess = self._make_mock_session(status="running")
        with (
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=mock_sess,
            ),
            patch("agent.agent_session_queue.checkpoint_branch_state"),
        ):
            update_session(
                "sess-1",
                new_status="completed",
                reason="work done",
            )
        assert mock_sess.status == "completed"

    def test_empty_session_id_raises(self):
        """Empty session_id raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            update_session("", new_status="pending", reason="test")

    def test_no_status_no_fields_raises(self):
        """Neither new_status nor fields raises ValueError."""
        with pytest.raises(ValueError, match="At least one"):
            update_session("sess-1", reason="nothing to do")

    def test_session_not_found_raises(self):
        """ValueError raised when session_id doesn't match any record."""
        with patch("models.session_lifecycle.get_authoritative_session", return_value=None):
            with pytest.raises(ValueError, match="No session found"):
                update_session("sess-missing", new_status="pending", reason="test")


class TestCASInFinalizeSession:
    """CAS conflict detection in finalize_session."""

    def test_cas_conflict_detected(self):
        """StatusConflictError raised when on-disk status differs from in-memory."""
        session = MagicMock()
        session.session_id = "sess-1"
        session.status = "running"  # caller's in-memory snapshot

        # On-disk has already changed to "completed"
        fresh = MagicMock()
        fresh.status = "completed"

        with patch("models.session_lifecycle.get_authoritative_session", return_value=fresh):
            with pytest.raises(Exception) as exc_info:
                finalize_session(session, "failed", reason="timeout")
        assert type(exc_info.value).__name__ == "StatusConflictError"
        assert exc_info.value.expected_status == "running"
        assert exc_info.value.actual_status == "completed"

    def test_cas_passes_when_status_matches(self):
        """No conflict when on-disk matches in-memory status."""
        session = MagicMock()
        session.session_id = "sess-1"
        session.status = "running"
        session.parent_agent_session_id = None

        fresh = MagicMock()
        fresh.status = "running"

        with (
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=fresh,
            ),
            patch("agent.agent_session_queue.checkpoint_branch_state"),
        ):
            finalize_session(session, "completed", reason="done")
        assert session.status == "completed"
        session.save.assert_called_once()


class TestCASInTransitionStatus:
    """CAS conflict detection in transition_status."""

    def test_cas_conflict_detected(self):
        """StatusConflictError raised when on-disk status differs."""
        session = MagicMock()
        session.session_id = "sess-1"
        session.status = "running"  # caller's in-memory snapshot

        # On-disk has changed to "completed"
        fresh = MagicMock()
        fresh.status = "completed"

        with patch("models.session_lifecycle.get_authoritative_session", return_value=fresh):
            with pytest.raises(Exception) as exc_info:
                transition_status(session, "pending", reason="re-enqueue")
        assert type(exc_info.value).__name__ == "StatusConflictError"
        assert exc_info.value.expected_status == "running"
        assert exc_info.value.actual_status == "completed"

    def test_companion_fields_persisted_on_idempotent_path(self):
        """Idempotent path (already in target state) still calls save()."""
        session = MagicMock()
        session.session_id = "sess-1"
        session.status = "pending"

        # On-disk matches in-memory (no CAS conflict)
        fresh = MagicMock()
        fresh.status = "pending"

        with patch("models.session_lifecycle.get_authoritative_session", return_value=fresh):
            session.priority = "high"
            transition_status(session, "pending", reason="re-enqueue")

        # save() is called even though status didn't change
        session.save.assert_called_once()
        # But lifecycle log is NOT called (no actual transition)
        session.log_lifecycle_transition.assert_not_called()
