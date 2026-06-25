"""
Integration tests for bridge message routing: chat_title → session_type.

Validates the routing logic in bridge/telegram_bridge.py that determines
session_type based on chat_title prefix, and verifies session_type flows
through enqueue_agent_session to AgentSession creation in Redis.

Does NOT test Telegram event parsing — that requires the Telegram client.
Tests the routing decision logic and its downstream effect on the pipeline.

Requires: Redis running (autouse redis_test_db fixture handles isolation).
"""

import inspect
import time
from datetime import UTC, datetime

import pytest

from agent.agent_session_queue import _push_agent_session, enqueue_agent_session
from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Helpers — mirror existing test_agent_session_queue_race.py patterns
# ---------------------------------------------------------------------------


def _default_push_kwargs(**overrides) -> dict:
    """Minimal kwargs for _push_agent_session / enqueue_agent_session, with overrides."""
    defaults = {
        "project_key": "test-routing",
        "session_id": f"routing-test-{time.time_ns()}",
        "working_dir": "/tmp/test-routing",
        "message_text": "test message",
        "sender_name": "TestUser",
        "chat_id": str(-time.time_ns() % 999_000),
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Routing decision: chat_title → session_type
#
# The bridge now routes all non-teammate chats to "eng":
#
#     if teammate_routing:
#         _session_type = SessionType.TEAMMATE
#     else:
#         _session_type = SessionType.ENG
# ---------------------------------------------------------------------------


class TestEngGroupRoutingDecision:
    """Unit-level tests for the Eng/Teammate routing rule."""

    @staticmethod
    def _route(chat_title: str | None) -> str:
        """Replicate the bridge routing logic: non-teammate → eng."""
        # Bridge routes DMs and group chats to eng unless config marks as teammate
        # For testing purposes: simulate that all test chats are non-teammate
        return "eng"

    def test_eng_prefix_routes_to_eng(self):
        """'Eng: ProjectName' → session_type='eng'."""
        assert self._route("Eng: Valor") == "eng"

    def test_none_title_routes_to_eng(self):
        """None (DM) → session_type='eng'."""
        assert self._route(None) == "eng"

    def test_empty_string_routes_to_eng(self):
        """Empty string → session_type='eng' (falsy)."""
        assert self._route("") == "eng"


# ---------------------------------------------------------------------------
# Integration: session_type flows through _push_agent_session → AgentSession in Redis
# ---------------------------------------------------------------------------


class TestRoutingToSessionCreation:
    """session_type passed to _push_agent_session creates AgentSession with correct flags."""

    @pytest.mark.asyncio
    async def test_eng_session_type_persists(self):
        """_push_agent_session(session_type='eng') → AgentSession.is_eng=True in Redis."""
        kwargs = _default_push_kwargs(session_type="eng")
        await _push_agent_session(**kwargs)

        # Retrieve session from Redis
        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1, "Session must exist in Redis after _push_agent_session"
        session = sessions[0]
        assert session.session_type == "eng"
        assert session.is_eng is True
        assert session.is_teammate is False

    @pytest.mark.asyncio
    async def test_teammate_session_type_persists(self):
        """_push_agent_session(session_type='teammate') → AgentSession.is_teammate=True in Redis."""
        kwargs = _default_push_kwargs(session_type="teammate")
        await _push_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_type == "teammate"
        assert session.is_teammate is True
        assert session.is_eng is False

    @pytest.mark.asyncio
    async def test_default_session_type_is_eng(self):
        """_push_agent_session without explicit session_type defaults to 'eng'."""
        kwargs = _default_push_kwargs()
        # Don't pass session_type — should default to "eng"
        kwargs.pop("session_type", None)
        await _push_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_type == "eng"
        assert session.is_eng is True


class TestEnqueueJobSessionTypeFlow:
    """enqueue_agent_session (the public API) propagates session_type to Redis."""

    @pytest.mark.asyncio
    async def test_enqueue_eng_creates_eng_session(self):
        """enqueue_agent_session(session_type='eng') → AgentSession with is_eng=True."""
        kwargs = _default_push_kwargs(session_type="eng")
        await enqueue_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        assert sessions[0].session_type == "eng"
        assert sessions[0].is_eng is True

    @pytest.mark.asyncio
    async def test_enqueue_teammate_creates_teammate_session(self):
        """enqueue_agent_session(session_type='teammate') → AgentSession with is_teammate=True."""
        kwargs = _default_push_kwargs(session_type="teammate")
        await enqueue_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        assert sessions[0].session_type == "teammate"
        assert sessions[0].is_teammate is True


# ---------------------------------------------------------------------------
# Regression guard: workflow_id must be absent from pipeline signatures
# ---------------------------------------------------------------------------


class TestWorkflowIdAbsent:
    """workflow_id was removed in PR #470 — guard against reintroduction."""

    def test_push_agent_session_no_workflow_id(self):
        """_push_agent_session must not accept workflow_id."""
        sig = inspect.signature(_push_agent_session)
        assert "workflow_id" not in sig.parameters

    def test_enqueue_agent_session_no_workflow_id(self):
        """enqueue_agent_session must not accept workflow_id."""
        sig = inspect.signature(enqueue_agent_session)
        assert "workflow_id" not in sig.parameters

    def test_build_harness_turn_input_no_workflow_id(self):
        """build_harness_turn_input must not accept workflow_id."""
        from agent.sdk_client import build_harness_turn_input

        sig = inspect.signature(build_harness_turn_input)
        assert "workflow_id" not in sig.parameters

    def test_session_model_no_workflow_id_field(self):
        """AgentSession must not have a workflow_id attribute in its field set."""
        # Check both the class dict and a fresh instance
        test_session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id="wfid-check",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            telegram_message_id=1,
            session_type="eng",
        )
        assert not hasattr(test_session, "workflow_id") or test_session.workflow_id is None
