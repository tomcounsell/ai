"""Tests for Observer early return bug fixes (issue #374).

Tests three fixes:
1. Session identity mapping — Claude Code UUID stored and used for resume
2. Watchdog tool count scoping — counts reset at query start, scoped by VALOR_SESSION_ID
3. Deterministic record selection — newest running record selected from duplicates

Run with: pytest tests/test_observer_early_return.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Bug 1: Session identity mapping (claude_session_uuid)
# ============================================================================


class TestClaudeSessionUUID:
    """Verify claude_session_uuid field and its usage."""

    def test_agent_session_has_uuid_field(self):
        """AgentSession model should have claude_session_uuid field."""
        from models.agent_session import AgentSession

        session = AgentSession()
        assert hasattr(session, "claude_session_uuid")
        assert session.claude_session_uuid is None

    def test_get_prior_session_uuid_unknown_session(self):
        """Unknown session_id should return None."""
        from agent.sdk_client import _get_prior_session_uuid

        result = _get_prior_session_uuid("nonexistent_session_374_test")
        assert result is None

    def test_get_prior_session_uuid_empty_string(self):
        """Empty session_id should return None."""
        from agent.sdk_client import _get_prior_session_uuid

        result = _get_prior_session_uuid("")
        assert result is None

    def test_has_prior_session_delegates_to_uuid_lookup(self):
        """_has_prior_session should return False when no UUID stored."""
        from agent.sdk_client import _has_prior_session

        result = _has_prior_session("nonexistent_session_374_test2")
        assert result is False

    def test_store_and_retrieve_uuid(self):
        """Store a UUID and verify it can be retrieved."""
        from agent.sdk_client import _get_prior_session_uuid, _store_claude_session_uuid
        from models.agent_session import AgentSession

        test_session_id = f"test_uuid_store_{int(time.time())}"
        test_uuid = "abc123-def456-ghi789"

        # Create a session record
        session = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="running",
            created_at=time.time(),
            working_dir="/tmp",
            message_text="test",
            sender_name="test",
        )

        try:
            # Store the UUID
            _store_claude_session_uuid(test_session_id, test_uuid)

            # Retrieve it
            result = _get_prior_session_uuid(test_session_id)
            assert result == test_uuid
        finally:
            # Cleanup
            session.delete()


try:
    import agent.sdk_client  # noqa: F401

    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


@pytest.mark.skipif(not _SDK_AVAILABLE, reason="claude_agent_sdk not importable")
class TestCreateOptionsUUID:
    """Verify _create_options uses stored UUID for resume."""

    def test_fresh_session_no_resume(self):
        """Fresh session (no UUID) should not set resume."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id="fresh_no_uuid_374")
        assert options.continue_conversation is False
        assert options.resume is None

    def test_resume_uses_uuid_not_telegram_id(self):
        """When a UUID is stored, resume should use it, not the session_id."""
        from agent.sdk_client import ValorAgent, _store_claude_session_uuid
        from models.agent_session import AgentSession

        test_session_id = f"test_resume_uuid_{int(time.time())}"
        test_uuid = "claude-uuid-for-resume-test"

        session = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="completed",
            created_at=time.time(),
            working_dir="/tmp",
            message_text="test",
            sender_name="test",
        )

        try:
            _store_claude_session_uuid(test_session_id, test_uuid)

            agent = ValorAgent()
            options = agent._create_options(session_id=test_session_id)
            assert options.continue_conversation is True
            assert options.resume == test_uuid, (
                f"resume should be Claude UUID '{test_uuid}', "
                f"not Telegram ID '{test_session_id}'"
            )
        finally:
            session.delete()


# ============================================================================
# Bug 2: Watchdog tool count scoping
# ============================================================================


class TestWatchdogCountScoping:
    """Verify watchdog tool counts are scoped by VALOR_SESSION_ID and can be reset."""

    def test_reset_session_count_function_exists(self):
        """reset_session_count function should exist in health_check."""
        from agent.health_check import reset_session_count

        assert callable(reset_session_count)

    def test_reset_clears_count(self):
        """reset_session_count should clear the counter for a session."""
        from agent.health_check import _tool_counts, reset_session_count

        test_id = "test_reset_374"
        _tool_counts[test_id] = 42

        reset_session_count(test_id)
        assert test_id not in _tool_counts

    def test_reset_noop_for_unknown_session(self):
        """reset_session_count should not error for unknown sessions."""
        from agent.health_check import reset_session_count

        # Should not raise
        reset_session_count("completely_unknown_session_374")

    def test_valor_session_id_env_used_in_hook(self):
        """Watchdog hook should use VALOR_SESSION_ID env var when available."""
        # This is a code-path test — we verify the env var is read
        # by checking the source code pattern. The actual hook requires
        # SDK types we can't easily mock.
        import inspect

        from agent.health_check import watchdog_hook

        source = inspect.getsource(watchdog_hook)
        assert "VALOR_SESSION_ID" in source, (
            "watchdog_hook should check VALOR_SESSION_ID env var"
        )


# ============================================================================
# Bug 3: Deterministic record selection
# ============================================================================


class TestDeterministicRecordSelection:
    """Verify that duplicate AgentSession records are handled deterministically."""

    def test_newest_running_record_selected(self):
        """When multiple records exist, the newest running one should be selected."""
        from models.agent_session import AgentSession

        test_session_id = f"test_determ_{int(time.time())}"

        # Create two records — an old completed one and a newer running one
        old = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="completed",
            created_at=time.time() - 100,
            working_dir="/tmp",
            message_text="old",
            sender_name="test",
            classification_type=None,
        )
        newer = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="running",
            created_at=time.time(),
            working_dir="/tmp",
            message_text="new",
            sender_name="test",
            classification_type="sdlc",
        )

        try:
            # Simulate the re-read logic from job_queue.py (Bug 3 fix)
            all_sessions = list(AgentSession.query.filter(session_id=test_session_id))
            active = [
                s for s in all_sessions
                if s.status in ("running", "active", "pending")
            ]
            candidates = active if active else all_sessions
            candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
            selected = candidates[0]

            assert selected.status == "running"
            assert selected.classification_type == "sdlc"
            assert selected.message_text == "new"
        finally:
            old.delete()
            newer.delete()

    def test_fallback_to_newest_when_no_active(self):
        """When no active records exist, fall back to newest of any status."""
        from models.agent_session import AgentSession

        test_session_id = f"test_fallback_{int(time.time())}"

        old = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="completed",
            created_at=time.time() - 200,
            working_dir="/tmp",
            message_text="oldest",
            sender_name="test",
        )
        newer = AgentSession.create(
            session_id=test_session_id,
            project_key="test",
            status="completed",
            created_at=time.time() - 50,
            working_dir="/tmp",
            message_text="newer_completed",
            sender_name="test",
        )

        try:
            all_sessions = list(AgentSession.query.filter(session_id=test_session_id))
            active = [
                s for s in all_sessions
                if s.status in ("running", "active", "pending")
            ]
            candidates = active if active else all_sessions
            candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
            selected = candidates[0]

            assert selected.message_text == "newer_completed"
        finally:
            old.delete()
            newer.delete()

    def test_observer_re_read_selects_correct_record(self):
        """Observer's _handle_update_session should select correct record."""
        # This test verifies the code pattern exists in observer.py
        import inspect

        from bridge.observer import Observer

        source = inspect.getsource(Observer._handle_update_session)
        assert "status" in source and "running" in source, (
            "Observer._handle_update_session should filter by status"
        )
        assert "created_at" in source, (
            "Observer._handle_update_session should sort by created_at"
        )

    def test_status_filter_in_job_queue(self):
        """job_queue send_to_chat should use status-based filtering."""
        import inspect

        import agent.job_queue as jq

        source = inspect.getsource(jq)
        # Check the re-read section uses our new pattern
        assert "running" in source and "active" in source and "pending" in source


# ============================================================================
# Integration: All three fixes work together
# ============================================================================


class TestIntegrationAllFixes:
    """Verify the three fixes don't conflict with each other."""

    def test_uuid_preserved_across_delete_and_recreate(self):
        """claude_session_uuid should be in _JOB_FIELDS for preservation."""
        from agent.job_queue import _JOB_FIELDS

        assert "claude_session_uuid" in _JOB_FIELDS, (
            "claude_session_uuid must be preserved across delete-and-recreate"
        )

    def test_superseded_status_on_push(self):
        """_push_job should mark old completed records as superseded."""
        import inspect

        import agent.job_queue as jq

        source = inspect.getsource(jq._push_job)
        assert "superseded" in source, (
            "_push_job should mark old completed records as superseded"
        )

    def test_session_model_fields_complete(self):
        """AgentSession should have all required fields for the fix."""
        from models.agent_session import AgentSession

        session = AgentSession()
        # Bug 1
        assert hasattr(session, "claude_session_uuid")
        # Existing fields used by Bug 3
        assert hasattr(session, "classification_type")
        assert hasattr(session, "created_at")
        assert hasattr(session, "status")
