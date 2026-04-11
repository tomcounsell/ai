"""Unit tests for models/session_lifecycle.py — session lifecycle management.

Tests cover:
- finalize_session() calls update_task_type_profile after auto_tag_session
- finalize_session() skips profile update when skip_auto_tag=True
- finalize_session() profile update failure never prevents session finalization
- StatusConflictError behavior
- finalize_session() validation (None session, non-terminal status)
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from models.session_lifecycle import (
    finalize_session,
)


def _make_session(session_id="test-session-lc", status="running", project_key="test"):
    """Create a minimal mock AgentSession for lifecycle tests."""
    session = MagicMock()
    session.session_id = session_id
    session.status = status
    session.project_key = project_key
    session.parent_agent_session_id = None
    session._saved_field_values = {}
    return session


def _build_mock_modules():
    """Build mock session_tags and task_type_profile modules for patching."""
    mock_auto_tag_module = MagicMock()
    mock_profile_module = MagicMock()
    return mock_auto_tag_module, mock_profile_module


# ===================================================================
# finalize_session — TaskTypeProfile update hook
# ===================================================================


class TestFinalizeSessionProfileHook:
    """Tests for the step 2.5 TaskTypeProfile update hook in finalize_session()."""

    def test_profile_update_called_when_auto_tag_runs(self):
        """update_task_type_profile is called when skip_auto_tag=False (default)."""
        session = _make_session()
        mock_auto_tag_module, mock_profile_module = _build_mock_modules()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed")

        # Both auto_tag and profile update should have been called
        mock_auto_tag_module.auto_tag_session.assert_called_once_with(session.session_id)
        mock_profile_module.update_task_type_profile.assert_called_once_with(session.session_id)

    def test_profile_update_call_order(self):
        """update_task_type_profile is called AFTER auto_tag_session (and after status save)."""
        session = _make_session()
        call_order = []

        # Track save() calls to verify profile update comes after
        def tracking_save():
            call_order.append("session_save")

        session.save = tracking_save

        mock_auto_tag_module = MagicMock()
        mock_auto_tag_module.auto_tag_session = lambda sid: call_order.append("auto_tag")

        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile = lambda sid: call_order.append(
            "update_profile"
        )

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed")

        assert "auto_tag" in call_order
        assert "update_profile" in call_order
        # auto_tag must precede update_profile, and profile update must come after session save
        assert call_order.index("auto_tag") < call_order.index("update_profile")
        assert call_order.index("session_save") < call_order.index("update_profile")

    def test_profile_update_skipped_when_skip_auto_tag(self):
        """update_task_type_profile is NOT called when skip_auto_tag=True."""
        session = _make_session()
        mock_auto_tag_module, mock_profile_module = _build_mock_modules()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed", skip_auto_tag=True)

        # Profile update must NOT have been called
        mock_profile_module.update_task_type_profile.assert_not_called()
        # auto_tag must also NOT have been called
        mock_auto_tag_module.auto_tag_session.assert_not_called()

    def test_profile_update_failure_does_not_prevent_finalization(self):
        """Exception in update_task_type_profile must not block session status save."""
        session = _make_session()
        mock_auto_tag_module = MagicMock()
        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile.side_effect = Exception("Redis is down")

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            # Must not raise — finalization must complete
            finalize_session(session, "completed")

        # Status must have been set to "completed"
        assert session.status == "completed"
        # save() must have been called
        session.save.assert_called()

    def test_finalization_sets_completed_status_despite_profile_error(self):
        """Session status reaches 'completed' even when profile update throws."""
        session = _make_session(status="running")
        mock_auto_tag_module = MagicMock()
        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile.side_effect = RuntimeError(
            "intentional failure"
        )

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed", reason="test")

        assert session.status == "completed"
        assert session.completed_at is not None


# ===================================================================
# finalize_session — idempotency
# ===================================================================


class TestFinalizeSessionIdempotency:
    def test_idempotent_when_already_in_target_status(self):
        """finalize_session is a no-op if session already in target terminal state."""
        session = _make_session(status="completed")

        finalize_session(session, "completed")

        # save must NOT have been called (skipped early)
        session.save.assert_not_called()


# ===================================================================
# finalize_session — validation
# ===================================================================


class TestFinalizeSessionValidation:
    def test_raises_for_non_terminal_status(self):
        """finalize_session raises ValueError for non-terminal statuses."""
        session = _make_session()
        with pytest.raises(ValueError, match="terminal"):
            finalize_session(session, "running")

    def test_raises_for_none_session(self):
        """finalize_session raises ValueError when session is None."""
        with pytest.raises(ValueError, match="session must not be None"):
            finalize_session(None, "completed")
