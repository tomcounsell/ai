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
# This replicates the exact routing logic from telegram_bridge.py so we
# can test edge cases. The bridge code is:
#
#     if chat_title and chat_title.startswith("Dev:"):
#         _session_type = "dev"
#     else:
#         _session_type = "pm"
# ---------------------------------------------------------------------------


class TestDevGroupRoutingDecision:
    """Unit-level tests for the 'Dev:' prefix routing rule."""

    @staticmethod
    def _route(chat_title: str | None) -> str:
        """Replicate the exact bridge routing logic for testing edge cases."""
        if chat_title and chat_title.startswith("Dev:"):
            return "dev"
        return "pm"

    def test_dev_prefix_routes_to_dev(self):
        """'Dev: ProjectName' → session_type='dev'."""
        assert self._route("Dev: Valor") == "dev"

    def test_dev_prefix_no_space_routes_to_dev(self):
        """'Dev:NoSpace' → session_type='dev' (startswith only checks 'Dev:')."""
        assert self._route("Dev:NoSpace") == "dev"

    def test_dev_prefix_only_routes_to_dev(self):
        """'Dev:' with nothing after → still matches prefix."""
        assert self._route("Dev:") == "dev"

    def test_regular_group_routes_to_pm(self):
        """'PM: Valor' → session_type='pm'."""
        assert self._route("PM: Valor") == "pm"

    def test_none_title_routes_to_pm(self):
        """None (DM) → session_type='pm'."""
        assert self._route(None) == "pm"

    def test_empty_string_routes_to_pm(self):
        """Empty string → session_type='pm' (falsy)."""
        assert self._route("") == "pm"

    def test_lowercase_dev_routes_to_pm(self):
        """'dev: lowercase' → session_type='pm' (case-sensitive startswith)."""
        assert self._route("dev: lowercase") == "pm"

    def test_developer_prefix_routes_to_pm(self):
        """'Developer Chat' starts with 'Dev' but NOT 'Dev:' → pm."""
        assert self._route("Developer Chat") == "pm"

    def test_dev_in_middle_routes_to_pm(self):
        """'Important Dev: Task' has 'Dev:' in middle, not start → pm."""
        assert self._route("Important Dev: Task") == "pm"

    def test_whitespace_only_routes_to_pm(self):
        """Whitespace-only title is truthy but doesn't start with 'Dev:'."""
        assert self._route("   ") == "pm"

    def test_dev_with_trailing_whitespace(self):
        """'Dev: ' with trailing space → dev (prefix matches)."""
        assert self._route("Dev: ") == "dev"

    def test_dev_with_unicode_project_name(self):
        """'Dev: 日本語プロジェクト' → dev (prefix still 'Dev:')."""
        assert self._route("Dev: 日本語プロジェクト") == "dev"


# ---------------------------------------------------------------------------
# Integration: session_type flows through _push_agent_session → AgentSession in Redis
# ---------------------------------------------------------------------------


class TestRoutingToSessionCreation:
    """session_type passed to _push_agent_session creates AgentSession with correct flags."""

    @pytest.mark.asyncio
    async def test_pm_session_type_persists(self):
        """_push_agent_session(session_type='pm') → AgentSession.is_pm=True in Redis."""
        kwargs = _default_push_kwargs(session_type="pm")
        await _push_agent_session(**kwargs)

        # Retrieve session from Redis
        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1, "Session must exist in Redis after _push_agent_session"
        session = sessions[0]
        assert session.session_type == "pm"
        assert session.is_pm is True
        assert session.is_dev is False

    @pytest.mark.asyncio
    async def test_dev_session_type_persists(self):
        """_push_agent_session(session_type='dev') → AgentSession.is_dev=True in Redis."""
        kwargs = _default_push_kwargs(session_type="dev")
        await _push_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_type == "dev"
        assert session.is_dev is True
        assert session.is_pm is False

    @pytest.mark.asyncio
    async def test_default_session_type_is_pm(self):
        """_push_agent_session without explicit session_type defaults to 'pm'."""
        kwargs = _default_push_kwargs()
        # Don't pass session_type — should default to "pm"
        kwargs.pop("session_type", None)
        await _push_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        session = sessions[0]
        assert session.session_type == "pm"
        assert session.is_pm is True


class TestEnqueueJobSessionTypeFlow:
    """enqueue_agent_session (the public API) propagates session_type to Redis."""

    @pytest.mark.asyncio
    async def test_enqueue_pm_creates_pm_session(self):
        """enqueue_agent_session(session_type='pm') → AgentSession with is_pm=True."""
        kwargs = _default_push_kwargs(session_type="pm")
        await enqueue_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        assert sessions[0].session_type == "pm"

    @pytest.mark.asyncio
    async def test_enqueue_dev_creates_dev_session(self):
        """enqueue_agent_session(session_type='dev') → AgentSession with is_dev=True."""
        kwargs = _default_push_kwargs(session_type="dev")
        await enqueue_agent_session(**kwargs)

        sessions = list(AgentSession.query.filter(session_id=kwargs["session_id"]))
        assert len(sessions) >= 1
        assert sessions[0].session_type == "dev"
        assert sessions[0].is_dev is True


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

    def test_get_agent_response_sdk_no_workflow_id(self):
        """get_agent_response_sdk must not accept workflow_id."""
        from agent.sdk_client import get_agent_response_sdk

        sig = inspect.signature(get_agent_response_sdk)
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
            session_type="pm",
        )
        assert not hasattr(test_session, "workflow_id") or test_session.workflow_id is None
