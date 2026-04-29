"""
Integration tests for the summarizer pipeline.

Tests real API calls and the response->summarizer wiring chain.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.message_drafter import ClassificationResult, OutputType, classify_output


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set -- skipping live classification test",
)
@pytest.mark.asyncio
async def test_classify_output_real_api():
    """Call classify_output() with real Anthropic API and validate the result."""
    text = (
        "I've finished implementing the feature. The tests pass and the PR is ready "
        "for review at https://github.com/example/repo/pull/42. Let me know if you "
        "want any changes."
    )

    result = await classify_output(text)

    # Must return a valid ClassificationResult
    assert isinstance(result, ClassificationResult)
    assert isinstance(result.output_type, OutputType)
    assert result.confidence > 0
    assert isinstance(result.reason, str)
    assert len(result.reason) > 0


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

        mock_draft.return_value = MessageDraft(text="Summarized output", was_drafted=True)
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
        return MessageDraft(text=text, was_drafted=False)

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
