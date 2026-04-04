"""
Integration tests for the summarizer pipeline.

Tests real API calls and the response->summarizer wiring chain.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.summarizer import ClassificationResult, OutputType, classify_output


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
    """Verify that send_response_with_files invokes summarize_response for long text."""
    from bridge.response import send_response_with_files

    # Build a response long enough to trigger summarization (>= 200 chars)
    long_text = "Here is a detailed status update. " * 20  # ~680 chars

    # Mock Telegram client that captures sent messages
    mock_client = AsyncMock()
    sent_message = MagicMock()
    sent_message.id = 999
    mock_client.send_message = AsyncMock(return_value=sent_message)

    # Mock event with chat_id
    mock_event = MagicMock()
    mock_event.chat_id = 12345
    mock_event.message.id = 1

    # Create a minimal session that is not SDLC (to test the len>=200 path)
    # Must explicitly set has_pm_messages to return False to avoid pm_bypass,
    # and is_sdlc to False so we test the len>=200 path.
    mock_session = MagicMock()
    mock_session.session_id = "test-session-123"
    mock_session.session_type = "teammate"
    mock_session.sdlc_stage = None
    mock_session.github_issue_url = None
    mock_session.github_pr_url = None
    mock_session.delivery_action = None
    mock_session.has_pm_messages = MagicMock(return_value=False)
    mock_session.get_parent_session = MagicMock(return_value=None)
    mock_session.is_sdlc = False

    # Patch AgentSession.query to return our mock session, and the summarizer
    with (
        patch("bridge.response.filter_tool_logs", return_value=long_text),
        patch("bridge.response.extract_files_from_response", return_value=(long_text, [])),
        patch("bridge.summarizer.summarize_response") as mock_summarize,
        patch("models.agent_session.AgentSession") as mock_agent_session_cls,
    ):
        from bridge.summarizer import SummarizedResponse

        mock_summarize.return_value = SummarizedResponse(
            text="Summarized output", was_summarized=True
        )

        # Make the session query return our mock
        mock_agent_session_cls.query.filter.return_value = [mock_session]

        await send_response_with_files(
            client=mock_client,
            event=mock_event,
            response=long_text,
            session=mock_session,
        )

        # Verify summarize_response was called with the long text and session
        mock_summarize.assert_called_once()
        call_args = mock_summarize.call_args
        assert call_args[0][0] == long_text  # first positional arg is text
        assert call_args[1]["session"] == mock_session  # session kwarg
