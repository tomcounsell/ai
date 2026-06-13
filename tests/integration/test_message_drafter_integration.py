"""
Integration tests for the drafter pipeline.

Tests the response->drafter wiring chain. The classify_output cluster was
deleted in the drafter passthrough refactor (issue #1680) — routing decisions
now live in bridge/promise_gate.py and the nudge loop.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_response_summarizer_wiring():
    """Verify that TelegramRelayOutputHandler.send invokes draft_message for all text.

    Post-#1074 consolidation: send_response_with_files is gone. The canonical
    drafter entry point is TelegramRelayOutputHandler.send, which always
    routes text through draft_message before writing to the outbox.
    """
    from agent.output_handler import TelegramRelayOutputHandler

    # Build a response long enough to trigger summarization (>= 200 chars)
    long_text = "Here is a detailed status update. " * 20  # ~680 chars

    mock_session = MagicMock()
    mock_session.session_id = "test-session-123"
    mock_session.session_type = "teammate"
    mock_session.sdlc_stage = None
    mock_session.has_pm_messages = MagicMock(return_value=False)
    mock_session.get_parent_session = MagicMock(return_value=None)
    mock_session.is_sdlc = False

    handler = TelegramRelayOutputHandler()
    with (
        patch("bridge.message_drafter.draft_message") as mock_draft,
        patch.object(handler, "_get_redis") as mock_redis,
    ):
        from bridge.message_drafter import MessageDraft

        mock_draft.return_value = MessageDraft(text="Summarized output")
        mock_redis.return_value = MagicMock()

        await handler.send(
            chat_id="12345",
            text=long_text,
            reply_to_msg_id=1,
            session=mock_session,
        )

        mock_draft.assert_called_once()
        call_args = mock_draft.call_args
        assert call_args[0][0] == long_text  # first positional arg is text
        assert call_args[1]["session"] == mock_session  # session kwarg


# === Read-the-Room (issue #1193) integration tests ============================


@pytest.fixture
def rtr_handler_setup(monkeypatch):
    """Common fixture for RTR-on integration tests.

    Yields ``(handler, mock_redis, mock_session)``. The drafter is patched
    to bypass cleanly so ``delivery_text == text``. The RTR env-var flag is
    set to true. Snapshots are short-circuited via a non-empty stub so the
    Haiku call is reached -- we then patch ``read_the_room`` itself per-test
    to inject the verdict we want.
    """
    monkeypatch.setenv("READ_THE_ROOM_ENABLED", "true")
    from agent.output_handler import TelegramRelayOutputHandler
    from bridge.message_drafter import MessageDraft

    handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock()
    mock_redis.expire = MagicMock()
    handler._redis = mock_redis

    mock_session = MagicMock()
    # session_id != chat_id so we can verify queue alignment.
    mock_session.session_id = "abc"
    mock_session.session_type = "teammate"
    mock_session.sdlc_stage = None
    mock_session.sdlc_slug = None
    mock_session.has_pm_messages = MagicMock(return_value=False)
    mock_session.get_parent_session = MagicMock(return_value=None)
    mock_session.is_sdlc = False
    mock_session.session_events = None

    def bypass_drafter(text, *, session=None, medium="telegram"):
        return MessageDraft(text=text)

    monkeypatch.setattr(
        "bridge.message_drafter.draft_message",
        AsyncMock(side_effect=bypass_drafter),
    )
    return handler, mock_redis, mock_session


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rtr_suppress_writes_reaction_to_session_queue(rtr_handler_setup):
    """suppress + reply_to anchor: 👀 reaction lands at telegram:outbox:{session_id}
    NOT telegram:outbox:{chat_id} (Implementation Note F7)."""
    from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

    handler, mock_redis, mock_session = rtr_handler_setup
    chat_id = "-100123"
    long_text = "Logged 4 entries to the project knowledge base. " * 6

    with patch(
        "bridge.read_the_room.read_the_room",
        AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
    ):
        await handler.send(
            chat_id=chat_id,
            text=long_text,
            reply_to_msg_id=42,
            session=mock_session,
        )

    mock_redis.rpush.assert_called_once()
    queue_key, raw_payload = mock_redis.rpush.call_args[0]
    assert queue_key == "telegram:outbox:abc"
    assert queue_key != f"telegram:outbox:{chat_id}"
    payload = json.loads(raw_payload)
    assert payload["type"] == "reaction"
    assert payload["emoji"] == RTR_SUPPRESS_EMOJI
    assert payload["reply_to"] == 42

    # Session event recorded.
    types_ = [e["type"] for e in (mock_session.session_events or [])]
    assert "rtr.suppressed" in types_


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rtr_trim_long_substitutes_revised_text(rtr_handler_setup):
    """trim with len >= 20 swaps delivery_text and emits rtr.trimmed."""
    from bridge.read_the_room import RoomVerdict

    handler, mock_redis, mock_session = rtr_handler_setup
    long_text = "Logged 4 entries to the project knowledge base. " * 6
    revised = "Quick note: see dashboard for details."

    with patch(
        "bridge.read_the_room.read_the_room",
        AsyncMock(return_value=RoomVerdict(action="trim", revised_text=revised, reason="partial")),
    ):
        await handler.send(
            chat_id="-100123",
            text=long_text,
            reply_to_msg_id=42,
            session=mock_session,
        )

    mock_redis.rpush.assert_called_once()
    payload = json.loads(mock_redis.rpush.call_args[0][1])
    assert payload["text"] == revised
    types_ = [e["type"] for e in (mock_session.session_events or [])]
    assert "rtr.trimmed" in types_


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rtr_trim_short_coerced_to_reaction(rtr_handler_setup):
    """trim with len < TRIM_TOO_SHORT_THRESHOLD coerces to suppress + 👀."""
    from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

    handler, mock_redis, mock_session = rtr_handler_setup
    long_text = "Logged 4 entries to the project knowledge base. " * 6

    with patch(
        "bridge.read_the_room.read_the_room",
        AsyncMock(return_value=RoomVerdict(action="trim", revised_text="ok", reason="too_short")),
    ):
        await handler.send(
            chat_id="-100123",
            text=long_text,
            reply_to_msg_id=42,
            session=mock_session,
        )

    # Exactly one rpush -- the reaction.
    assert mock_redis.rpush.call_count == 1
    payload = json.loads(mock_redis.rpush.call_args[0][1])
    assert payload["type"] == "reaction"
    assert payload["emoji"] == RTR_SUPPRESS_EMOJI
    types_ = [e["type"] for e in (mock_session.session_events or [])]
    assert "rtr.suppressed" in types_
    suppress_event = next(e for e in mock_session.session_events if e["type"] == "rtr.suppressed")
    assert suppress_event["reason"] == "trim_too_short"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rtr_failure_writes_original_text(rtr_handler_setup):
    """When the RTR call returns send (e.g., on Haiku failure), the original
    delivery_text lands on the outbox as a normal text payload."""
    from bridge.read_the_room import RoomVerdict

    handler, mock_redis, mock_session = rtr_handler_setup
    long_text = "Logged 4 entries to the project knowledge base. " * 6

    with patch(
        "bridge.read_the_room.read_the_room",
        AsyncMock(return_value=RoomVerdict(action="send", reason="rtr_error")),
    ):
        await handler.send(
            chat_id="-100123",
            text=long_text,
            reply_to_msg_id=42,
            session=mock_session,
        )

    mock_redis.rpush.assert_called_once()
    payload = json.loads(mock_redis.rpush.call_args[0][1])
    assert payload["text"] == long_text
    assert payload.get("type") != "reaction"


# === Drafter redundancy suppression (issue #1205) integration tests ===========


@pytest.fixture
def redundancy_handler_setup(monkeypatch):
    """Fixture for redundancy-filter integration tests.

    Yields ``(handler, mock_redis, sdlc_session)``. The drafter is patched
    to bypass cleanly (delivery_text == text). The SDLC session has is_sdlc=True
    and recent_sent_drafts starts empty.
    """
    monkeypatch.setenv("DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED", "true")
    from agent.output_handler import TelegramRelayOutputHandler
    from bridge.message_drafter import MessageDraft

    handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.expire = MagicMock()
    handler._redis = mock_redis

    sdlc_session = MagicMock()
    sdlc_session.session_id = "sdlc-int-abc"
    sdlc_session.is_sdlc = True
    sdlc_session.status = "active"
    sdlc_session.recent_sent_drafts = []
    sdlc_session.session_events = None
    sdlc_session.extra_context = {}
    sdlc_session.record_recent_sent_draft = MagicMock()

    def bypass_drafter(text, *, session=None, medium="telegram"):
        return MessageDraft(text=text, artifacts={})

    monkeypatch.setattr(
        "bridge.message_drafter.draft_message",
        AsyncMock(side_effect=bypass_drafter),
    )
    return handler, mock_redis, sdlc_session


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redundancy_three_identical_drafts_produce_one_text_two_reactions(
    redundancy_handler_setup,
):
    """SDLC session: three near-identical drafts within the window produce
    exactly one text message and two 👀 reactions.

    This is the regression test for the issue #1205 scenario: PM session in
    waiting_for_children drafts the same status message N times. Only the first
    send should produce a text; subsequent near-duplicates within the window
    should each queue a 👀 reaction.
    """
    import time

    from bridge.redundancy_filter import RTR_SUPPRESS_EMOJI, SuppressionVerdict

    handler, mock_redis, sdlc_session = redundancy_handler_setup

    # Repeated PM status text (realistic near-verbatim repeat).
    status_text = (
        "Still waiting for child sessions to complete. "
        "I will confirm merge-readiness once all children report back. "
        "No action required from you at this time."
    ) * 5  # repeat to build up enough bigrams

    # First send: no prior → send_verdict (no_baseline).
    # Simulate this by having should_suppress return send for the first call.
    call_count = 0

    def mock_suppress(
        draft_text, draft_artifacts, recent_sent_drafts, expectations, session_status
    ):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return SuppressionVerdict(action="send", reason="no_baseline")
        # Subsequent calls: simulate finding a match.
        return SuppressionVerdict(
            action="suppress",
            reason="jaccard=0.95>=threshold=0.65",
            jaccard=0.95,
            matched_index=0,
        )

    with patch("bridge.redundancy_filter.should_suppress", side_effect=mock_suppress):
        # First send: delivers text.
        await handler.send(
            chat_id="-100123",
            text=status_text,
            reply_to_msg_id=42,
            session=sdlc_session,
        )
        # Second send: suppressed → 👀 reaction.
        sdlc_session.recent_sent_drafts = [
            {"ts": time.time(), "text": status_text[:500], "artifacts": {}}
        ]
        await handler.send(
            chat_id="-100123",
            text=status_text,
            reply_to_msg_id=42,
            session=sdlc_session,
        )
        # Third send: suppressed → 👀 reaction.
        await handler.send(
            chat_id="-100123",
            text=status_text,
            reply_to_msg_id=42,
            session=sdlc_session,
        )

    calls = mock_redis.rpush.call_args_list
    text_payloads = []
    reaction_payloads = []
    for c in calls:
        p = json.loads(c[0][1])
        if p.get("type") == "reaction":
            reaction_payloads.append(p)
        else:
            text_payloads.append(p)

    assert len(text_payloads) == 1, (
        f"Expected 1 text message, got {len(text_payloads)}: {text_payloads}"
    )
    assert len(reaction_payloads) == 2, (
        f"Expected 2 👀 reactions, got {len(reaction_payloads)}: {reaction_payloads}"
    )
    for rp in reaction_payloads:
        assert rp["emoji"] == RTR_SUPPRESS_EMOJI


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redundancy_new_artifact_prevents_suppression(redundancy_handler_setup):
    """When the second draft adds a new PR URL artifact, suppression is bypassed
    and the text is delivered (new_artifact termination fires)."""
    import time

    handler, mock_redis, sdlc_session = redundancy_handler_setup

    base_text = (
        "Review complete. Checking the final merge state. Children sessions have reported back. "
    ) * 10

    # First send (no prior → sends).
    sdlc_session.recent_sent_drafts = []
    await handler.send("-100123", base_text, 42, session=sdlc_session)

    # Simulate a recorded prior draft.
    sdlc_session.recent_sent_drafts = [
        {"ts": time.time(), "text": base_text[:500], "artifacts": {}}
    ]

    # Second send with a new PR URL in the text — extract_artifacts should pick it up.
    new_text = base_text + " PR: https://github.com/tomcounsell/ai/pull/9999"

    await handler.send("-100123", new_text, 42, session=sdlc_session)

    # Both should be text messages (not reactions).
    calls = mock_redis.rpush.call_args_list
    text_payloads = [
        json.loads(c[0][1]) for c in calls if json.loads(c[0][1]).get("type") != "reaction"
    ]
    assert len(text_payloads) == 2, (
        f"Expected 2 text messages (both sends), got {len(text_payloads)}"
    )
