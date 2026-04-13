"""Unit tests for models/task_type_profile.py — TRM TaskTypeProfile model.

Tests cover:
- delegation_recommendation derivation logic (structured/autonomous thresholds)
- get_delegation_recommendation() with missing profile (safe default)
- get_delegation_recommendation() with None inputs (safe default)
- update_task_type_profile() no-op when task_type is None
- update_task_type_profile() no-op when session status != completed
- _derive_recommendation() threshold logic
- Incremental metric aggregation
"""

from unittest.mock import MagicMock, patch

from models.task_type_profile import (
    REWORK_RATE_THRESHOLD,
    SESSION_COUNT_MINIMUM,
    TaskTypeProfile,
    _derive_recommendation,
    get_delegation_recommendation,
    update_task_type_profile,
)

# ===================================================================
# _derive_recommendation threshold logic
# ===================================================================


class TestDeriveRecommendation:
    """Tests for _derive_recommendation() derived field logic."""

    def test_structured_when_session_count_below_minimum(self):
        """Returns 'structured' when session_count < SESSION_COUNT_MINIMUM."""
        result = _derive_recommendation(rework_rate=0.0, session_count=SESSION_COUNT_MINIMUM - 1)
        assert result == "structured"

    def test_structured_when_session_count_is_zero(self):
        """Returns 'structured' for brand-new task type with zero sessions."""
        result = _derive_recommendation(rework_rate=0.0, session_count=0)
        assert result == "structured"

    def test_structured_when_rework_rate_exceeds_threshold(self):
        """Returns 'structured' when rework_rate > REWORK_RATE_THRESHOLD."""
        result = _derive_recommendation(
            rework_rate=REWORK_RATE_THRESHOLD + 0.01,
            session_count=SESSION_COUNT_MINIMUM,
        )
        assert result == "structured"

    def test_autonomous_when_proven_low_rework(self):
        """Returns 'autonomous' when session_count >= minimum and rework_rate is low."""
        result = _derive_recommendation(
            rework_rate=0.0,
            session_count=SESSION_COUNT_MINIMUM,
        )
        assert result == "autonomous"

    def test_autonomous_at_exact_threshold_rework(self):
        """Returns 'autonomous' when rework_rate exactly equals threshold (not exceeded)."""
        result = _derive_recommendation(
            rework_rate=REWORK_RATE_THRESHOLD,
            session_count=SESSION_COUNT_MINIMUM,
        )
        assert result == "autonomous"

    def test_structured_when_count_just_below_minimum(self):
        """Edge case: count = minimum - 1 → structured."""
        result = _derive_recommendation(rework_rate=0.0, session_count=SESSION_COUNT_MINIMUM - 1)
        assert result == "structured"

    def test_autonomous_when_count_exactly_minimum(self):
        """Edge case: count = minimum exactly → autonomous (if rework is low)."""
        result = _derive_recommendation(rework_rate=0.0, session_count=SESSION_COUNT_MINIMUM)
        assert result == "autonomous"


# ===================================================================
# get_delegation_recommendation — safe default behavior
# ===================================================================


class TestGetDelegationRecommendation:
    """Tests for get_delegation_recommendation() safety contract."""

    def test_returns_structured_for_none_project_key(self):
        """Returns 'structured' when project_key is None."""
        result = get_delegation_recommendation(None, "sdlc-build")
        assert result == "structured"

    def test_returns_structured_for_none_task_type(self):
        """Returns 'structured' when task_type is None."""
        result = get_delegation_recommendation("test-project", None)
        assert result == "structured"

    def test_returns_structured_for_both_none(self):
        """Returns 'structured' when both project_key and task_type are None."""
        result = get_delegation_recommendation(None, None)
        assert result == "structured"

    def test_returns_structured_for_empty_strings(self):
        """Returns 'structured' when project_key or task_type is empty string."""
        result = get_delegation_recommendation("", "sdlc-build")
        assert result == "structured"
        result2 = get_delegation_recommendation("test-project", "")
        assert result2 == "structured"

    def test_returns_structured_when_no_profile_exists(self):
        """Returns 'structured' when no profile exists for the project+task_type combo."""
        with patch.object(TaskTypeProfile.query, "filter", return_value=[]):
            result = get_delegation_recommendation("nonexistent-project", "sdlc-build")
        assert result == "structured"

    def test_returns_structured_on_query_exception(self):
        """Returns 'structured' (never raises) when query throws an exception."""
        with patch.object(TaskTypeProfile.query, "filter", side_effect=Exception("Redis down")):
            result = get_delegation_recommendation("test-project", "sdlc-build")
        assert result == "structured"

    def test_returns_recommendation_from_existing_profile(self):
        """Returns the stored delegation_recommendation when a profile exists."""
        mock_profile = MagicMock()
        mock_profile.delegation_recommendation = "autonomous"

        with patch.object(TaskTypeProfile.query, "filter", return_value=[mock_profile]):
            result = get_delegation_recommendation("test-project", "sdlc-build")
        assert result == "autonomous"

    def test_returns_structured_when_profile_has_none_recommendation(self):
        """Falls back to 'structured' when profile.delegation_recommendation is None."""
        mock_profile = MagicMock()
        mock_profile.delegation_recommendation = None

        with patch.object(TaskTypeProfile.query, "filter", return_value=[mock_profile]):
            result = get_delegation_recommendation("test-project", "sdlc-build")
        assert result == "structured"


# ===================================================================
# update_task_type_profile — no-op and skip conditions
# ===================================================================


class TestUpdateTaskTypeProfile:
    """Tests for update_task_type_profile() skip conditions and safety."""

    def test_no_op_when_session_id_is_empty(self):
        """No-op and no raise when session_id is empty string."""
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter") as mock_filter:
            update_task_type_profile("")
            mock_filter.assert_not_called()

    def test_no_op_when_session_id_is_none(self):
        """No-op and no raise when session_id is None."""
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter") as mock_filter:
            update_task_type_profile(None)  # type: ignore
            mock_filter.assert_not_called()

    def test_no_op_when_session_not_found(self):
        """No-op when session lookup returns empty list."""
        from models.agent_session import AgentSession

        with patch.object(AgentSession.query, "filter", return_value=[]):
            # Should not raise
            update_task_type_profile("nonexistent-session-123")

    def test_no_op_when_task_type_is_none(self):
        """No-op when session has no task_type — no profile should be created."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        mock_session.task_type = None
        mock_session.project_key = "test-project"

        from models.agent_session import AgentSession

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter") as mock_profile_filter,
        ):
            update_task_type_profile("test-session-no-type")
            mock_profile_filter.assert_not_called()

    def test_no_op_when_session_status_is_failed(self):
        """No-op for failed sessions — profiles only track completed work."""
        mock_session = MagicMock()
        mock_session.status = "failed"
        mock_session.task_type = "sdlc-build"
        mock_session.project_key = "test-project"

        from models.agent_session import AgentSession

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter") as mock_profile_filter,
        ):
            update_task_type_profile("test-session-failed")
            mock_profile_filter.assert_not_called()

    def test_no_op_when_session_status_is_killed(self):
        """No-op for killed sessions."""
        mock_session = MagicMock()
        mock_session.status = "killed"
        mock_session.task_type = "sdlc-build"
        mock_session.project_key = "test-project"

        from models.agent_session import AgentSession

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter") as mock_profile_filter,
        ):
            update_task_type_profile("test-session-killed")
            mock_profile_filter.assert_not_called()

    def test_does_not_raise_when_profile_save_fails(self):
        """Exception during profile save must not propagate."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        mock_session.task_type = "sdlc-build"
        mock_session.project_key = "test-project"
        mock_session.turn_count = 10
        mock_session.rework_triggered = None

        mock_profile = MagicMock()
        mock_profile.session_count = 0
        mock_profile.avg_turns = 0.0
        mock_profile.rework_rate = 0.0
        mock_profile.save.side_effect = Exception("Redis write failure")

        from models.agent_session import AgentSession

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter", return_value=[mock_profile]),
        ):
            # Must not raise — exception is caught and logged at DEBUG level
            update_task_type_profile("test-session-save-fail")

    def test_creates_new_profile_for_first_session(self):
        """Creates and saves a new TaskTypeProfile when none exists."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        mock_session.task_type = "sdlc-build"
        mock_session.project_key = "test-project"
        mock_session.turn_count = 8
        mock_session.rework_triggered = None

        saved_profiles = []

        from models.agent_session import AgentSession

        def mock_profile_save(self):
            saved_profiles.append(self)

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter", return_value=[]),
            patch.object(TaskTypeProfile, "save", mock_profile_save),
        ):
            update_task_type_profile("test-session-new-profile")

        assert len(saved_profiles) == 1
        profile = saved_profiles[0]
        assert profile.session_count == 1
        assert profile.avg_turns == 8.0
        assert profile.rework_rate == 0.0
        assert profile.delegation_recommendation == "structured"  # session_count < minimum

    def test_incremental_update_averages_turns_correctly(self):
        """Incremental update correctly rolling-averages turn counts."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        mock_session.task_type = "sdlc-build"
        mock_session.project_key = "test-project"
        mock_session.turn_count = 10
        mock_session.rework_triggered = None

        mock_profile = MagicMock()
        mock_profile.session_count = 4
        mock_profile.avg_turns = 6.0
        mock_profile.rework_rate = 0.0

        saved_profiles = []

        from models.agent_session import AgentSession

        def mock_profile_save(self):
            saved_profiles.append(self)

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter", return_value=[mock_profile]),
        ):
            update_task_type_profile("test-session-update")

        # new_count = 5, new_avg = (6.0 * 4 + 10) / 5 = 34/5 = 6.8
        assert mock_profile.session_count == 5
        assert abs(mock_profile.avg_turns - 6.8) < 0.001
        assert mock_profile.delegation_recommendation == "autonomous"  # count >= 5, rework=0

    def test_rework_rate_computed_correctly(self):
        """Rework rate is correctly computed for a session with rework_triggered=True."""
        mock_session = MagicMock()
        mock_session.status = "completed"
        mock_session.task_type = "sdlc-patch"
        mock_session.project_key = "test-project"
        mock_session.turn_count = 5
        mock_session.rework_triggered = "true"

        mock_profile = MagicMock()
        mock_profile.session_count = 4
        mock_profile.avg_turns = 5.0
        mock_profile.rework_rate = 0.0  # no rework in prior sessions

        from models.agent_session import AgentSession

        with (
            patch.object(AgentSession.query, "filter", return_value=[mock_session]),
            patch.object(TaskTypeProfile.query, "filter", return_value=[mock_profile]),
        ):
            update_task_type_profile("test-session-rework")

        # new_count = 5, new_rework_rate = (0.0 * 4 + 1) / 5 = 0.2
        assert mock_profile.session_count == 5
        assert abs(mock_profile.rework_rate - 0.2) < 0.001
        # rework_rate=0.2 <= 0.3 threshold, count=5 >= minimum → autonomous
        assert mock_profile.delegation_recommendation == "autonomous"
