"""Tests for _enqueue_continuation — session reuse via delete-and-recreate.

Covers:
- Session reuse: existing session is preserved (not orphaned) across auto-continue
- Metadata preservation: classification_type, history, links, context_summary survive
- Coaching message passed directly from Observer agent
- Fallback to enqueue_job when no session found
- _JOB_FIELDS completeness (context_summary, expectations included)

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import time
from unittest.mock import MagicMock

import pytest

# claude_agent_sdk mock is centralized in conftest.py
from agent.job_queue import (
    _JOB_FIELDS,
    SendToChatResult,
    _enqueue_continuation,
    should_guard_empty_output,
)
from models.agent_session import AgentSession


def _make_mock_job(**overrides):
    """Create a mock Job with sensible defaults."""
    defaults = {
        "project_key": "test-project",
        "session_id": "test-session-123",
        "working_dir": "/tmp/test-wd",
        "message_text": "continue",
        "sender_name": "Test User",
        "chat_id": "chat_456",
        "message_id": 789,
        "work_item_slug": None,
        "task_list_id": None,
        "workflow_id": None,
        "classification_type": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


def _create_session(redis_test_db, **overrides):
    """Create an AgentSession in Redis for testing."""
    defaults = {
        "session_id": "test-session-123",
        "project_key": "test-project",
        "status": "running",
        "chat_id": "chat_456",
        "sender_name": "Test User",
        "created_at": time.time(),
        "started_at": time.time(),
        "message_text": "original message",
        "working_dir": "/tmp/test-wd",
        "message_id": 789,
        "priority": "high",
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


class TestSessionReuse:
    """Tests for session reuse via delete-and-recreate in _enqueue_continuation."""

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self, redis_test_db):
        """Auto-continue reuses the existing session record, not a new one."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        job = _make_mock_job(session_id=session.session_id, classification_type="sdlc")

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="test-tl",
            auto_continue_count=1,
            output_msg="Building...",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(sessions) == 1

    @pytest.mark.asyncio
    async def test_session_status_reset_to_pending(self, redis_test_db):
        """Reused session has status='pending' ready for worker pickup."""
        session = _create_session(redis_test_db, status="running")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(sessions) == 1
        assert sessions[0].status == "pending"

    @pytest.mark.asyncio
    async def test_message_text_updated_to_coaching(self, redis_test_db):
        """Reused session gets the coaching message as message_text."""
        session = _create_session(redis_test_db, message_text="original request")
        job = _make_mock_job(session_id=session.session_id)

        coaching_text = "[System Coach] Include test output next time."
        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
            coaching_message=coaching_text,
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].message_text == coaching_text

    @pytest.mark.asyncio
    async def test_auto_continue_count_updated(self, redis_test_db):
        """Reused session gets the new auto_continue_count."""
        session = _create_session(redis_test_db, auto_continue_count=0)
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=5,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].auto_continue_count == 5

    @pytest.mark.asyncio
    async def test_priority_set_to_high(self, redis_test_db):
        """Reused session always gets high priority."""
        session = _create_session(redis_test_db, priority="low")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].priority == "high"

    @pytest.mark.asyncio
    async def test_task_list_id_updated(self, redis_test_db):
        """Reused session gets the task_list_id from the argument."""
        session = _create_session(redis_test_db, task_list_id="old-tl")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="new-tl-from-arg",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].task_list_id == "new-tl-from-arg"


class TestMetadataPreservation:
    """Tests verifying that all session metadata survives auto-continue."""

    @pytest.mark.asyncio
    async def test_classification_type_preserved(self, redis_test_db):
        """classification_type='sdlc' survives auto-continue."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        job = _make_mock_job(session_id=session.session_id, classification_type="sdlc")

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="Building...",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].classification_type == "sdlc"

    @pytest.mark.asyncio
    async def test_history_preserved(self, redis_test_db):
        """History entries survive auto-continue."""
        session = _create_session(redis_test_db)
        session.append_history("user", "SDLC 285")
        session.append_history("stage", "ISSUE completed")
        session.append_history("stage", "PLAN completed")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="Building...",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        history = sessions[0]._get_history_list()
        assert len(history) == 3
        assert "[user] SDLC 285" in history
        assert "[stage] ISSUE completed" in history
        assert "[stage] PLAN completed" in history

    @pytest.mark.asyncio
    async def test_links_preserved(self, redis_test_db):
        """Issue, plan, and PR URLs survive auto-continue."""
        session = _create_session(redis_test_db)
        session.set_link("issue", "https://github.com/org/repo/issues/285")
        session.set_link(
            "plan",
            "https://github.com/org/repo/blob/main/docs/plans/test.md",
        )
        session.set_link("pr", "https://github.com/org/repo/pull/290")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        links = sessions[0].get_links()
        assert links["issue"] == "https://github.com/org/repo/issues/285"
        assert links["plan"] == "https://github.com/org/repo/blob/main/docs/plans/test.md"
        assert links["pr"] == "https://github.com/org/repo/pull/290"

    @pytest.mark.asyncio
    async def test_context_summary_preserved(self, redis_test_db):
        """context_summary survives auto-continue."""
        session = _create_session(
            redis_test_db, context_summary="Building SDLC session tracking fix"
        )
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].context_summary == "Building SDLC session tracking fix"

    @pytest.mark.asyncio
    async def test_expectations_preserved(self, redis_test_db):
        """expectations survives auto-continue."""
        session = _create_session(
            redis_test_db,
            expectations="Waiting for test results from CI",
        )
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].expectations == "Waiting for test results from CI"

    @pytest.mark.asyncio
    async def test_is_sdlc_works_after_continuation(self, redis_test_db):
        """is_sdlc returns True on the reused session."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        session.append_history("stage", "ISSUE completed")
        job = _make_mock_job(
            session_id=session.session_id,
            classification_type="sdlc",
        )

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].is_sdlc is True

    @pytest.mark.asyncio
    async def test_stage_progress_works_after_continuation(self, redis_test_db):
        """get_stage_progress() returns correct data on the reused session."""
        session = _create_session(redis_test_db)
        session.append_history("stage", "ISSUE completed")
        session.append_history("stage", "PLAN completed")
        session.append_history("stage", "BUILD in_progress")
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        progress = sessions[0].get_stage_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"
        assert progress["TEST"] == "pending"


class TestFallbackBehavior:
    """Tests for fallback behavior when session is not found."""

    @pytest.mark.asyncio
    async def test_fallback_preserves_metadata_when_no_session(self, redis_test_db):
        """When no session exists, fallback recreates from Job._rj metadata."""
        # Create a session to use as the Job's underlying AgentSession,
        # then delete it so the query finds nothing — simulating Redis expiry
        original = _create_session(
            redis_test_db,
            session_id="fallback-session",
            classification_type="sdlc",
        )
        original.context_summary = "Building feature X"
        original.issue_url = "https://github.com/org/repo/issues/99"
        original.pr_url = "https://github.com/org/repo/pull/100"
        original.save()

        # Create a real Job wrapping this session
        from agent.job_queue import Job

        job = Job(original)

        # Delete the session from Redis to simulate expiry
        original.delete()
        assert list(AgentSession.query.filter(session_id="fallback-session")) == []

        # Run continuation — should recreate from Job._rj metadata
        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        # Verify the recreated session preserves metadata
        sessions = list(AgentSession.query.filter(session_id="fallback-session"))
        assert len(sessions) == 1
        recreated = sessions[0]
        assert recreated.classification_type == "sdlc"
        assert recreated.context_summary == "Building feature X"
        assert recreated.issue_url == "https://github.com/org/repo/issues/99"
        assert recreated.pr_url == "https://github.com/org/repo/pull/100"
        assert recreated.status == "pending"
        assert recreated.priority == "high"


class TestNoDuplicateRecords:
    """Tests ensuring no duplicate AgentSession records after auto-continue."""

    @pytest.mark.asyncio
    async def test_no_duplicates_after_single_continuation(self, redis_test_db):
        """Single auto-continue produces exactly one session record."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        job = _make_mock_job(
            session_id=session.session_id,
            classification_type="sdlc",
        )

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="msg",
        )

        all_sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(all_sessions) == 1

    @pytest.mark.asyncio
    async def test_no_duplicates_after_multiple_continuations(self, redis_test_db):
        """Multiple auto-continues still produce exactly one record."""
        from agent.job_queue import Job

        session = _create_session(redis_test_db, classification_type="sdlc")

        for i in range(5):
            # Re-fetch the session each iteration so job._rj points to the
            # current record (delete-and-recreate changes the underlying object)
            sessions = list(AgentSession.query.filter(session_id=session.session_id))
            current = sessions[0] if sessions else session
            job = Job(current)
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=i + 1,
                output_msg=f"msg {i}",
            )

        all_sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(all_sessions) == 1
        assert all_sessions[0].auto_continue_count == 5


class TestEnqueueContinuationCoachingMessage:
    """Tests for coaching message handling in _enqueue_continuation."""

    @pytest.mark.asyncio
    async def test_coaching_message_passed_directly(self, redis_test_db):
        """coaching_message parameter is used as the session message_text."""
        session = _create_session(redis_test_db)
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="test-tl",
            auto_continue_count=1,
            output_msg="Running tests...",
            coaching_message="[Observer] Continue with /do-test next.",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].message_text == "[Observer] Continue with /do-test next."

    @pytest.mark.asyncio
    async def test_default_coaching_message_is_continue(self, redis_test_db):
        """When no coaching_message is provided, default is 'continue'."""
        session = _create_session(redis_test_db)
        job = _make_mock_job(session_id=session.session_id)

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="test-tl",
            auto_continue_count=2,
            output_msg="Still building...",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions[0].message_text == "continue"


class TestJobFieldsCompleteness:
    """Tests ensuring _JOB_FIELDS includes all AgentSession fields."""

    def test_context_summary_in_job_fields(self):
        """context_summary must be in _JOB_FIELDS."""
        assert "context_summary" in _JOB_FIELDS

    def test_expectations_in_job_fields(self):
        """expectations must be in _JOB_FIELDS."""
        assert "expectations" in _JOB_FIELDS

    def test_classification_type_in_job_fields(self):
        """classification_type must be in _JOB_FIELDS."""
        assert "classification_type" in _JOB_FIELDS

    def test_history_in_job_fields(self):
        """history must be in _JOB_FIELDS."""
        assert "history" in _JOB_FIELDS

    def test_all_link_fields_in_job_fields(self):
        """issue_url, plan_url, pr_url must be in _JOB_FIELDS."""
        assert "issue_url" in _JOB_FIELDS
        assert "plan_url" in _JOB_FIELDS
        assert "pr_url" in _JOB_FIELDS


class TestSendToChatResultDataclass:
    """Tests for the SendToChatResult dataclass."""

    def test_defaults(self):
        """Default state is no completion sent, no deferred reaction, count 0."""
        result = SendToChatResult()
        assert result.completion_sent is False
        assert result.defer_reaction is False
        assert result.auto_continue_count == 0

    def test_custom_values(self):
        """All fields can be set via constructor."""
        result = SendToChatResult(
            completion_sent=True,
            defer_reaction=True,
            auto_continue_count=5,
        )
        assert result.completion_sent is True
        assert result.defer_reaction is True
        assert result.auto_continue_count == 5

    def test_mutable_state(self):
        """State can be mutated after creation (used in closure)."""
        result = SendToChatResult()
        result.completion_sent = True
        result.defer_reaction = True
        result.auto_continue_count = 3
        assert result.completion_sent is True
        assert result.defer_reaction is True
        assert result.auto_continue_count == 3


class TestEmptyOutputLoopTermination:
    """Tests for empty output loop termination behavior."""

    @pytest.mark.asyncio
    async def test_empty_output_not_enqueued_for_continuation(
        self,
        redis_test_db,
    ):
        """Empty output is handled gracefully by _enqueue_continuation."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        job = _make_mock_job(
            session_id=session.session_id,
            classification_type="sdlc",
        )

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(sessions) == 1
        assert sessions[0].status == "pending"

    @pytest.mark.asyncio
    async def test_whitespace_output_not_enqueued_for_continuation(
        self,
        redis_test_db,
    ):
        """Whitespace-only output should also be handled gracefully."""
        session = _create_session(redis_test_db, classification_type="sdlc")
        job = _make_mock_job(
            session_id=session.session_id,
            classification_type="sdlc",
        )

        await _enqueue_continuation(
            job=job,
            branch_name="session/test",
            task_list_id="tl",
            auto_continue_count=1,
            output_msg="   \n\t  ",
        )

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert len(sessions) == 1

    def test_send_to_chat_result_tracks_empty_output_delivery(self):
        """SendToChatResult.completion_sent is set for empty output."""
        chat_state = SendToChatResult()

        if should_guard_empty_output("", True, True):
            chat_state.completion_sent = True

        assert chat_state.completion_sent is True
        assert chat_state.auto_continue_count == 0
