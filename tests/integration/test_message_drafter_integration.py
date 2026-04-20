"""
Integration tests for the summarizer pipeline.

Tests real API calls and the response->summarizer wiring chain.
"""

import os
from unittest.mock import MagicMock, patch

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
    drafter entry point is TelegramRelayOutputHandler.send, which unconditionally
    runs the drafter when MESSAGE_DRAFTER_IN_HANDLER is enabled (default True).
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
