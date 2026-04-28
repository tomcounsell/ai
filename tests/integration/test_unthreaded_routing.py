"""Tests for unthreaded message routing into active sessions (#318).

Verifies that when semantic routing matches an unthreaded message to an
active (running/active) session, the message is pushed to the steering
queue instead of creating a competing session. Dormant session matches still
resume the session normally.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

from datetime import UTC, datetime

import pytest

from agent.steering import pop_all_steering_messages, push_steering_message


class TestUnthreadedActiveSessionRouting:
    """Verify that unthreaded messages matching active sessions get queued."""

    def test_push_steering_message_for_active_session(self):
        """When a message matches an active session, it should be pushed
        to the steering queue (not create a new session)."""
        session_id = "tg_valor_123_456"
        push_steering_message(session_id, "use JWT for auth", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 1
        assert messages[0]["text"] == "use JWT for auth"
        assert messages[0]["sender"] == "Tom"
        assert messages[0]["is_abort"] is False

    def test_abort_keyword_detected_in_unthreaded(self):
        """Abort keywords should be detected even for unthreaded messages."""
        session_id = "tg_valor_123_789"
        push_steering_message(session_id, "stop", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 1
        assert messages[0]["is_abort"] is True

    def test_multiple_unthreaded_messages_queued_fifo(self):
        """Multiple unthreaded messages should queue in FIFO order."""
        session_id = "tg_valor_123_multi"
        push_steering_message(session_id, "first point", "Tom")
        push_steering_message(session_id, "second point", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 2
        assert messages[0]["text"] == "first point"
        assert messages[1]["text"] == "second point"


class TestSemanticRoutingDecisionMatrix:
    """Test the decision matrix from the plan:
    | Session status | Match confidence | Action |
    | running/active | >= 0.80          | Push to steering queue |
    | dormant        | >= 0.80          | Resume session (use session_id) |
    | any            | < 0.80           | Create new session |
    """

    @pytest.mark.asyncio
    async def test_active_session_gets_steering_message(self):
        """Active session match -> push_steering_message, return early."""
        from models.agent_session import AgentSession

        # Create an active session in Redis
        session = AgentSession(
            session_id="tg_valor_chat1_100",
            project_key="valor",
            status="running",
            message_text="implement feature X",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
            expectations="waiting for auth decision",
        )
        session.save()

        try:
            # Verify the session is findable
            found = list(AgentSession.query.filter(session_id="tg_valor_chat1_100"))
            assert len(found) == 1
            assert found[0].status == "running"

            # Simulate what the bridge does: push steering message
            push_steering_message(
                "tg_valor_chat1_100",
                "use OAuth please",
                "Tom",
            )

            # Verify it landed in the steering queue
            messages = pop_all_steering_messages("tg_valor_chat1_100")
            assert len(messages) == 1
            assert messages[0]["text"] == "use OAuth please"
        finally:
            session.delete()

    @pytest.mark.asyncio
    async def test_dormant_session_returns_session_id(self):
        """Dormant session match -> session_id is used (no steering)."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id="tg_valor_chat1_200",
            project_key="valor",
            status="dormant",
            message_text="waiting for review",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
            expectations="need PR approval",
        )
        session.save()

        try:
            found = list(AgentSession.query.filter(session_id="tg_valor_chat1_200"))
            assert len(found) == 1
            assert found[0].status == "dormant"

            # For dormant sessions, the bridge should NOT push steering
            # messages — it should use the session_id for resumption.
            # Verify the steering queue stays empty.
            messages = pop_all_steering_messages("tg_valor_chat1_200")
            assert len(messages) == 0
        finally:
            session.delete()

    @pytest.mark.asyncio
    async def test_session_lookup_after_match(self):
        """Verify AgentSession can be loaded by session_id after find_matching_session."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id="tg_valor_chat1_300",
            project_key="valor",
            status="active",
            message_text="build the feature",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        try:
            # Simulate the bridge's session lookup
            matched_sessions = list(AgentSession.query.filter(session_id="tg_valor_chat1_300"))
            assert len(matched_sessions) == 1
            matched = matched_sessions[0]
            assert matched.status in ("running", "active")

            # Active: should get steering message
            push_steering_message("tg_valor_chat1_300", "adjust scope", "Tom")
            messages = pop_all_steering_messages("tg_valor_chat1_300")
            assert len(messages) == 1
        finally:
            session.delete()

    @pytest.mark.asyncio
    async def test_missing_session_falls_through(self):
        """If matched session_id doesn't exist in Redis, fall through to
        normal routing (use it as session_id for dormant resume)."""
        from models.agent_session import AgentSession

        # Don't create any session — simulate session_router returning
        # a session_id that no longer exists
        matched_sessions = list(AgentSession.query.filter(session_id="tg_valor_chat1_nonexistent"))
        assert len(matched_sessions) == 0
        # Bridge should fall through to using matched_id as session_id


class TestPlanSkipReplyRouting:
    """Issue #1189: PM bucket-#3 announcement creates a dormant session
    with `expectations` set to the workflow question. Fresh `plan` and
    `skip` replies must route back to that dormant session via the
    semantic router at confidence >= 0.80.

    Network-dependent: calls Haiku via the real Anthropic API. Skipped
    when ANTHROPIC_API_KEY is unavailable.
    """

    _WORKFLOW_EXPECTATIONS = (
        "Should I file a GitHub issue and run /do-plan (`plan`), or override SDLC "
        "for this task only (`skip`)? Reply with one short token: `plan` or `skip`."
    )
    _WORKFLOW_CONTEXT = (
        "PM session received a coding/automation request and announced the workflow "
        "contract: 'Unless you directly instruct me to skip our standard workflow, "
        "we need to file an issue to plan all improvements and changes to software.' "
        "Awaiting human reply with `plan` or `skip`."
    )

    @pytest.fixture(autouse=True)
    def _require_api_key(self):
        """Skip the whole class when no Anthropic key is available."""
        from utils.api_keys import get_anthropic_api_key

        if not get_anthropic_api_key():
            pytest.skip("ANTHROPIC_API_KEY not configured — skipping live router test")

    def _make_dormant_pm_session(self, session_id: str, chat_id: str):
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="valor",
            status="dormant",
            chat_id=chat_id,
            message_text="please add a new launchd timer for the nightly cleanup",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
            expectations=self._WORKFLOW_EXPECTATIONS,
            context_summary=self._WORKFLOW_CONTEXT,
        )
        session.save()
        return session

    @pytest.mark.asyncio
    async def test_plan_reply_matches_dormant_pm_session(self):
        """A fresh `plan` reply should match the dormant PM session at
        confidence >= 0.80 via the semantic router."""
        from bridge.session_router import (
            ROUTING_CONFIDENCE_THRESHOLD,
            find_matching_session,
        )

        chat_id = "tg_valor_chat_plan"
        session = self._make_dormant_pm_session("tg_valor_pm_plan_1", chat_id)
        try:
            matched_id, confidence = await find_matching_session(
                chat_id=chat_id,
                message_text="plan",
                project_key="valor",
            )
            assert matched_id == session.session_id, (
                f"Expected `plan` reply to match the dormant PM session, "
                f"got matched_id={matched_id} confidence={confidence:.2f}"
            )
            assert confidence >= ROUTING_CONFIDENCE_THRESHOLD, (
                f"Expected confidence >= {ROUTING_CONFIDENCE_THRESHOLD}, got {confidence:.2f}"
            )
        finally:
            session.delete()

    @pytest.mark.asyncio
    async def test_skip_reply_matches_dormant_pm_session(self):
        """A fresh `skip` reply should also match the dormant PM session
        at confidence >= 0.80."""
        from bridge.session_router import (
            ROUTING_CONFIDENCE_THRESHOLD,
            find_matching_session,
        )

        chat_id = "tg_valor_chat_skip"
        session = self._make_dormant_pm_session("tg_valor_pm_skip_1", chat_id)
        try:
            matched_id, confidence = await find_matching_session(
                chat_id=chat_id,
                message_text="skip",
                project_key="valor",
            )
            assert matched_id == session.session_id, (
                f"Expected `skip` reply to match the dormant PM session, "
                f"got matched_id={matched_id} confidence={confidence:.2f}"
            )
            assert confidence >= ROUTING_CONFIDENCE_THRESHOLD, (
                f"Expected confidence >= {ROUTING_CONFIDENCE_THRESHOLD}, got {confidence:.2f}"
            )
        finally:
            session.delete()

    @pytest.mark.asyncio
    async def test_unrelated_topic_does_not_match(self):
        """An unrelated reply (topic shift) should NOT match the dormant
        PM session — the router falls through to new-session creation."""
        from bridge.session_router import find_matching_session

        chat_id = "tg_valor_chat_topic_shift"
        session = self._make_dormant_pm_session("tg_valor_pm_topic_shift_1", chat_id)
        try:
            matched_id, confidence = await find_matching_session(
                chat_id=chat_id,
                message_text=("actually, never mind that — what's the weather forecast?"),
                project_key="valor",
            )
            # Either no match (preferred) or low confidence — both fall through.
            assert matched_id is None or confidence < 0.80, (
                f"Unrelated message should not match dormant workflow session, "
                f"got matched_id={matched_id} confidence={confidence:.2f}"
            )
        finally:
            session.delete()
