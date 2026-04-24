"""Integration test for tools.sdlc_session_ensure bridge short-circuit.

Drives the headline dashboard claim for #1147: a bridge-initiated PM session
with no issue_url (only message_text) must NOT produce a duplicate
``sdlc-local-{N}`` record when /sdlc Step 1.5 runs ``ensure_session``.

Uses real Popoto Redis writes (no mocks) to validate the end-to-end flow:
1. Create a PM AgentSession mimicking bridge creation (session_type=pm,
   message_text="SDLC issue 9999", issue_url=None).
2. Set VALOR_SESSION_ID=<bridge_session_id>.
3. Invoke ensure_session(9999).
4. Assert: result reuses the bridge session id, created=False.
5. Assert: no ``sdlc-local-9999`` exists in Redis.

Cleanup happens in teardown via ``instance.delete()`` per CLAUDE.md's manual
testing hygiene rule — every test session is created with a recognizable
``project_key`` prefix and deleted through the Popoto ORM.
"""

from __future__ import annotations

import pytest

from models.agent_session import AgentSession

# Recognizable project_key prefix so teardown can scope cleanup narrowly and any
# leaked records are easy to spot on the dashboard.
TEST_PROJECT_KEY = "test-sdlc-ensure-int"


@pytest.fixture
def cleanup_test_sessions():
    """Delete every AgentSession created under TEST_PROJECT_KEY before and after."""

    def _cleanup():
        try:
            stale = [
                s
                for s in AgentSession.query.all()
                if getattr(s, "project_key", None) == TEST_PROJECT_KEY
            ]
        except Exception:
            return
        for s in stale:
            try:
                s.delete()
            except Exception:
                pass

    _cleanup()
    yield
    _cleanup()


def test_bridge_short_circuit_produces_no_duplicate(monkeypatch, cleanup_test_sessions):
    """End-to-end: bridge PM session + VALOR_SESSION_ID => no sdlc-local-N duplicate."""
    from tools.sdlc_session_ensure import ensure_session

    bridge_session_id = "tg_valor_test_9999"

    # Create a bridge-style PM session the way the Telegram bridge would.
    bridge_session = AgentSession.create_pm(
        session_id=bridge_session_id,
        project_key=TEST_PROJECT_KEY,
        working_dir="/tmp",
        chat_id="test_chat_9999",
        telegram_message_id=1,
        message_text="SDLC issue 9999",
        sender_name="IntegrationTest",
    )

    # Transition to running so it looks like a live worker turn.
    try:
        from models.session_lifecycle import transition_status

        transition_status(bridge_session, "running", "integration test setup")
    except Exception:
        # Not critical for this test — the short-circuit still activates as long
        # as status is non-terminal, and "pending" is non-terminal.
        pass

    # Simulate what agent/sdk_client.py does for bridge-initiated sessions.
    monkeypatch.setenv("VALOR_SESSION_ID", bridge_session_id)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    result = ensure_session(issue_number=9999)

    # The short-circuit must return the bridge session id and NOT create a new
    # sdlc-local-9999 record.
    assert result == {"session_id": bridge_session_id, "created": False}

    # Confirm via direct Popoto query: the duplicate zombie must not exist.
    zombie = list(AgentSession.query.filter(session_id="sdlc-local-9999"))
    assert zombie == [], (
        "ensure_session must NOT create sdlc-local-9999 when "
        "VALOR_SESSION_ID points at a live PM session"
    )

    # And there should be exactly one PM session in our test project_key.
    pm_sessions = [
        s
        for s in AgentSession.query.all()
        if getattr(s, "project_key", None) == TEST_PROJECT_KEY
        and getattr(s, "session_type", None) == "pm"
    ]
    assert len(pm_sessions) == 1
    assert pm_sessions[0].session_id == bridge_session_id
