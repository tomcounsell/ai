"""Integration tests for session finalization and TaskTypeProfile updates.

Tests cover the full pipeline:
1. Complete a dev session
2. Verify task_type is set via auto_tag_session Rule 7
3. Verify TaskTypeProfile is updated with correct metrics
4. Verify profile update failure does not prevent finalization

These tests use the real Redis test database (db=1) via the redis_test_db
autouse fixture in tests/conftest.py.
"""

from datetime import UTC, datetime

from models.agent_session import AgentSession
from models.task_type_profile import (
    TaskTypeProfile,
    get_delegation_recommendation,
    update_task_type_profile,
)

# ===================================================================
# Integration: session completion → TaskTypeProfile update
# ===================================================================


class TestSessionFinalizeProfileUpdate:
    """End-to-end: complete a session, verify TaskTypeProfile is updated."""

    def test_complete_session_creates_profile(self, redis_test_db):
        """Completing a session with task_type set creates a TaskTypeProfile entry."""
        session = AgentSession.create(
            session_id="integ-finalize-1",
            project_key="test-project",
            status="running",
            created_at=datetime.now(tz=UTC),
            turn_count=8,
        )
        session.task_type = "sdlc-build"
        session.save()

        # Complete the session
        update_task_type_profile("integ-finalize-1")

        # Manually set status to completed (update_task_type_profile reads status)
        session.status = "completed"
        session.save()
        update_task_type_profile("integ-finalize-1")

        # Verify profile was created
        profiles = list(
            TaskTypeProfile.query.filter(project_key="test-project", task_type="sdlc-build")
        )
        assert len(profiles) == 1
        profile = profiles[0]
        assert profile.session_count == 1
        assert abs(profile.avg_turns - 8.0) < 0.001
        assert profile.rework_rate == 0.0
        # session_count < SESSION_COUNT_MINIMUM → structured
        assert profile.delegation_recommendation == "structured"

    def test_profile_delegation_becomes_autonomous_after_enough_sessions(self, redis_test_db):
        """After enough completed sessions with low rework, delegation becomes 'autonomous'."""
        project_key = "test-proj-auto"
        task_type = "sdlc-build"

        # Simulate SESSION_COUNT_MINIMUM completed sessions with 0 rework
        from models.task_type_profile import SESSION_COUNT_MINIMUM

        for i in range(SESSION_COUNT_MINIMUM):
            session = AgentSession.create(
                session_id=f"integ-auto-{i}",
                project_key=project_key,
                status="completed",
                created_at=datetime.now(tz=UTC),
                turn_count=5,
            )
            session.task_type = task_type
            session.save()
            update_task_type_profile(f"integ-auto-{i}")

        recommendation = get_delegation_recommendation(project_key, task_type)
        assert recommendation == "autonomous"

    def test_profile_stays_structured_with_high_rework(self, redis_test_db):
        """Profile stays 'structured' when rework_rate exceeds threshold."""
        project_key = "test-proj-rework"
        task_type = "sdlc-patch"

        from models.task_type_profile import REWORK_RATE_THRESHOLD, SESSION_COUNT_MINIMUM

        # Simulate enough sessions but with high rework (more than REWORK_RATE_THRESHOLD)
        total = SESSION_COUNT_MINIMUM + 2
        rework_sessions = int(total * (REWORK_RATE_THRESHOLD + 0.1)) + 1  # just above threshold

        for i in range(total):
            session = AgentSession.create(
                session_id=f"integ-rework-{i}",
                project_key=project_key,
                status="completed",
                created_at=datetime.now(tz=UTC),
                turn_count=4,
            )
            session.task_type = task_type
            if i < rework_sessions:
                session.rework_triggered = "true"
            session.save()
            update_task_type_profile(f"integ-rework-{i}")

        recommendation = get_delegation_recommendation(project_key, task_type)
        assert recommendation == "structured"

    def test_profile_skipped_for_failed_session(self, redis_test_db):
        """TaskTypeProfile is NOT updated for failed sessions."""
        session = AgentSession.create(
            session_id="integ-failed-1",
            project_key="test-project-fail",
            status="failed",
            created_at=datetime.now(tz=UTC),
            turn_count=3,
        )
        session.task_type = "sdlc-build"
        session.save()

        update_task_type_profile("integ-failed-1")

        profiles = list(
            TaskTypeProfile.query.filter(project_key="test-project-fail", task_type="sdlc-build")
        )
        assert len(profiles) == 0

    def test_profile_skipped_for_missing_task_type(self, redis_test_db):
        """TaskTypeProfile is NOT updated when task_type is None."""
        AgentSession.create(
            session_id="integ-notype-1",
            project_key="test-project-notype",
            status="completed",
            created_at=datetime.now(tz=UTC),
            turn_count=5,
        )
        # task_type intentionally not set

        update_task_type_profile("integ-notype-1")

        profiles = list(TaskTypeProfile.query.all())
        # Filter to our project only (avoids cross-test contamination)
        our_profiles = [p for p in profiles if p.project_key == "test-project-notype"]
        assert len(our_profiles) == 0


# ===================================================================
# Integration: finalize_session → profile update via lifecycle hook
# ===================================================================


class TestFinalizeSessionProfileIntegration:
    """End-to-end via finalize_session() — verifies the lifecycle hook fires."""

    def test_finalize_session_updates_profile(self, redis_test_db):
        """finalize_session() with status=completed triggers profile update."""
        from models.session_lifecycle import finalize_session

        session = AgentSession.create(
            session_id="integ-lifecycle-1",
            project_key="lifecycle-project",
            status="running",
            created_at=datetime.now(tz=UTC),
            turn_count=10,
        )
        session.task_type = "sdlc-build"
        session.save()

        finalize_session(session, "completed")

        # Verify profile was created via the lifecycle hook
        profiles = list(
            TaskTypeProfile.query.filter(project_key="lifecycle-project", task_type="sdlc-build")
        )
        assert len(profiles) == 1
        assert profiles[0].session_count == 1
        assert abs(profiles[0].avg_turns - 10.0) < 0.001

    def test_finalize_session_profile_error_does_not_block(self, redis_test_db):
        """Deliberate exception in profile update does not prevent session finalization."""
        from unittest.mock import patch

        from models.session_lifecycle import finalize_session

        session = AgentSession.create(
            session_id="integ-error-1",
            project_key="error-project",
            status="running",
            created_at=datetime.now(tz=UTC),
            turn_count=5,
        )
        session.task_type = "sdlc-build"
        session.save()

        # Patch update_task_type_profile to always throw
        with patch(
            "models.task_type_profile.update_task_type_profile",
            side_effect=Exception("forced failure"),
        ):
            # Must not raise
            finalize_session(session, "completed")

        # Status must be completed
        sessions = list(AgentSession.query.filter(session_id="integ-error-1"))
        assert sessions[0].status == "completed"
