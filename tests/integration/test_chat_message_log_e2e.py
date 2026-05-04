"""Integration acceptance test for chat_message_log end-to-end flow (issue #1192).

Simulates a turn where the agent posts a message via Path B (valor-telegram send)
mid-session, then verifies:
  1. The outbound entry is recorded in the session's chat_message_log.
  2. When the drafter is invoked, its prompt context contains the prior Path B message.

Uses real Redis (via redis_test_db fixture) and real AgentSession persistence.
Does NOT use real Telegram — the relay send is mocked at the Telethon layer.

Marker: sdlc (tagged for the sdlc test suite)
"""

from datetime import UTC, datetime

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
        """After a simulated Path B relay send, the session's chat_message_log contains it."""
        # Simulate: relay successfully sends a Path B message for this session.
        # The payload includes owner_agent_session_id pointing to our session.
        # NOTE: owner_agent_session_id must be the Popoto AutoKey (agent_session_id),
        # not the bridge session_id field. _append_outbound_chat_log calls get_by_id().
        payload = {
            "text": "Working on the deployment — checking the pods now.",
            "chat_id": "12345",
            "session_id": "cli-9999",  # synthetic Path B session_id
            "owner_agent_session_id": session.agent_session_id,
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
            "owner_agent_session_id": session.agent_session_id,
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
        session.append_chat_log(
            direction="in", sender="Tom", content="Any blockers?", message_id=12
        )

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


class TestCompletionRunnerSuppressionE2E:
    """End-to-end (issue #1262): mid-session Path B send → completion runner
    fires → suppression check reads chat_message_log and dedupes.

    Asserts on chat_message_log (Path B's only writer), NOT recent_sent_drafts
    (Path A only). Mocks the harness, send_cb, and Redis layers; uses real
    Popoto/AgentSession persistence for the chat_log read-back.
    """

    def test_completion_runner_reads_chat_message_log_for_suppression_baseline(self, session):
        """Path B `valor-telegram send` populates chat_message_log; the
        completion runner's _build_completion_baseline returns the entry
        in the should_suppress shape ({ts, text, artifacts}).
        """
        from agent.session_completion import _build_completion_baseline

        path_b_text = "Deployment complete — all 5 pods green and serving."
        payload = {
            "text": path_b_text,
            "chat_id": "12345",
            "session_id": "cli-7777",
            "owner_agent_session_id": session.agent_session_id,
        }
        _append_outbound_chat_log(payload, msg_id=999)

        # Re-fetch from Redis (simulates the runner's pre-suppression refetch)
        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0]
        baseline = _build_completion_baseline(fresh)

        # The baseline must be the should_suppress shape, not the raw chat_log
        # entry shape. This is the load-bearing adapter contract.
        assert len(baseline) == 1
        entry = baseline[0]
        assert entry["text"] == path_b_text
        assert "ts" in entry
        assert "artifacts" in entry
        # Adapter must NOT leak the chat_log-only fields
        assert "direction" not in entry
        assert "message_id" not in entry
        assert "sender" not in entry

    def test_completion_runner_baseline_excludes_inbound_entries_e2e(self, session):
        """The user's own inbound message must never become the suppression
        baseline (we never suppress against the user's own message).
        """
        from agent.session_completion import _build_completion_baseline

        session.append_chat_log(
            direction="in",
            sender="Tom",
            content="What is the deploy status?",
            message_id=50,
        )
        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0]
        baseline = _build_completion_baseline(fresh)
        assert baseline == []

    def test_completion_runner_baseline_uses_chat_log_not_recent_sent_drafts(self, session):
        """Critical regression guard: the completion runner's baseline source
        is chat_message_log, NOT recent_sent_drafts. recent_sent_drafts is
        Path-A-only (TelegramRelayOutputHandler.send), so it would be empty
        for the duplicate-via-Path-B scenario this fix targets.
        """
        from agent.session_completion import _build_completion_baseline

        # Set recent_sent_drafts to a value that should NOT influence the
        # suppression baseline (it's the wrong field for Path B duplicates).
        path_a_text = "this is a Path A entry that we DO NOT want to use"
        session.recent_sent_drafts = [
            {"ts": __import__("time").time(), "text": path_a_text, "artifacts": {}}
        ]
        session.save()

        # Add a Path B outbound entry that IS the right baseline source.
        path_b_text = "this is the Path B entry that we DO want to dedupe against"
        session.append_chat_log(
            direction="out",
            sender="valor",
            content=path_b_text,
            message_id=60,
        )

        rows = list(AgentSession.query.filter(session_id=session.session_id))
        fresh = rows[0]
        baseline = _build_completion_baseline(fresh)

        # Baseline must contain the Path B text only.
        baseline_texts = [entry["text"] for entry in baseline]
        assert path_b_text in baseline_texts
        assert path_a_text not in baseline_texts
