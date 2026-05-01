"""Integration acceptance test for chat_message_log end-to-end flow (issue #1192).

Simulates a turn where the agent posts a message via Path B (valor-telegram send)
mid-session, then verifies:
  1. The outbound entry is recorded in the session's chat_message_log.
  2. When the drafter is invoked, its prompt context contains the prior Path B message.

Uses real Redis (via redis_test_db fixture) and real AgentSession persistence.
Does NOT use real Telegram — the relay send is mocked at the Telethon layer.

Marker: sdlc (tagged for the sdlc test suite)
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from bridge.message_drafter import _build_draft_prompt
from bridge.telegram_relay import _append_outbound_chat_log
from models.agent_session import AgentSession


@pytest.fixture
def session(redis_test_db):
    """A live AgentSession in Redis for the test."""
    sess = AgentSession.create(
        session_id="e2e-chat-log-test-1",
        project_key="test",
        status="active",
        chat_id="12345",
        sender_name="Tom",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        message_text="What is the deployment status?",
    )
    yield sess
    # Cleanup
    try:
        sess.delete()
    except Exception:
        pass


class TestChatMessageLogE2E:
    """End-to-end acceptance test: Path B send → drafter sees the prior message."""

    def test_path_b_outbound_entry_is_recorded_in_chat_log(self, session):
        """After a simulated Path B relay send, the session's chat_message_log contains the entry."""
        # Simulate: relay successfully sends a Path B message for this session.
        # The payload includes owner_agent_session_id pointing to our session.
        payload = {
            "text": "Working on the deployment — checking the pods now.",
            "chat_id": "12345",
            "session_id": "cli-9999",  # synthetic Path B session_id
            "owner_agent_session_id": session.session_id,
        }
        _append_outbound_chat_log(payload, msg_id=777)

        # Re-fetch from Redis to verify the write was durable
        rows = list(AgentSession.query.filter(session_id=session.session_id))
        assert rows, f"Session {session.session_id!r} not found after append"
        fresh = rows[0]
        log = fresh.chat_message_log or []
        assert len(log) == 1
        entry = log[0]
        assert entry["direction"] == "out"
        assert entry["sender"] == "valor"
        assert "deployment" in entry["content"]
        assert entry["message_id"] == 777

    def test_drafter_prompt_contains_prior_path_b_message(self, session):
        """After a Path B outbound entry is in the log, the drafter prompt includes it.

        This is the core acceptance criterion from the plan:
        'simulated turn where the agent posts a message via Path B mid-session,
         then trigger the drafter and assert its prompt contains the prior Path B message.'
        """
        path_b_text = "Working on the deployment — checking the pods now."

        # Simulate Path B send recorded in chat log
        payload = {
            "text": path_b_text,
            "chat_id": "12345",
            "session_id": "cli-8888",
            "owner_agent_session_id": session.session_id,
        }
        _append_outbound_chat_log(payload, msg_id=888)

        # Re-fetch so the drafter reads from the durable Redis state
        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0] if rows else session

        # Build the drafter prompt as _build_draft_prompt would
        agent_output = "Deployment is stable. All pods are running."
        prompt = _build_draft_prompt(agent_output, {}, session=fresh)

        # The prior Path B message must appear in the prompt
        assert path_b_text in prompt, (
            f"Expected Path B text to appear in drafter prompt, but it did not.\n"
            f"Path B text: {path_b_text!r}\n"
            f"Prompt excerpt: {prompt[:500]!r}"
        )
        # The 'you have already said' instruction must be present
        assert "already said the 'out' lines" in prompt

    def test_inbound_entry_also_appears_in_drafter_prompt(self, session):
        """Inbound entries (direction='in') from the user also appear in the drafter prompt."""
        # Directly append an inbound entry
        session.append_chat_log(
            direction="in",
            sender="Tom",
            content="What is the deployment status?",
            message_id=100,
        )

        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0] if rows else session
        prompt = _build_draft_prompt("Deployment is fine.", {}, session=fresh)

        assert "What is the deployment status?" in prompt
        assert "[in] Tom:" in prompt

    def test_both_in_and_out_entries_appear_in_drafter_prompt(self, session):
        """A mix of in and out entries all appear in the drafter prompt."""
        session.append_chat_log(direction="in", sender="Tom", content="Status?", message_id=10)
        session.append_chat_log(direction="out", sender="valor", content="Checking.", message_id=11)
        session.append_chat_log(direction="in", sender="Tom", content="Any blockers?", message_id=12)

        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0] if rows else session
        prompt = _build_draft_prompt("No blockers found.", {}, session=fresh)

        assert "Status?" in prompt
        assert "Checking." in prompt
        assert "Any blockers?" in prompt

    def test_chat_log_is_empty_for_fresh_session(self, session):
        """A freshly created session has an empty chat log — drafter produces valid prompt."""
        prompt = _build_draft_prompt("Agent did work.", {}, session=session)
        assert "Recent chat in this thread" not in prompt
        assert isinstance(prompt, str)
        assert "Agent did work." in prompt
