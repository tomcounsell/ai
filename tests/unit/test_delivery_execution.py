"""Tests for delivery instruction execution in bridge/response.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session(**kwargs):
    """Create a mock session with delivery fields."""
    defaults = {
        "session_id": "test-session",
        "delivery_action": None,
        "delivery_text": None,
        "delivery_emoji": None,
        "is_sdlc": False,
        "has_pm_messages": lambda: False,
        "get_parent_session": lambda: None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
class TestDeliveryExecution:
    async def test_send_action_delivers_text(self):
        """delivery_action='send' sends delivery_text via send_markdown."""
        from bridge.response import send_response_with_files

        session = _make_session(
            delivery_action="send",
            delivery_text="Agent-approved message.",
        )
        mock_client = AsyncMock()
        mock_sent = MagicMock()

        with patch("bridge.markdown.send_markdown", new_callable=AsyncMock, return_value=mock_sent):
            result = await send_response_with_files(
                mock_client,
                None,
                "raw agent output that should be ignored",
                chat_id=123,
                reply_to=456,
                session=session,
            )

        assert result == mock_sent

    async def test_send_action_falls_back_to_raw_text(self):
        """delivery_action='send' without delivery_text uses filtered response."""
        from bridge.response import send_response_with_files

        session = _make_session(delivery_action="send", delivery_text=None)
        mock_client = AsyncMock()
        mock_sent = MagicMock()

        with patch("bridge.markdown.send_markdown", new_callable=AsyncMock, return_value=mock_sent):
            result = await send_response_with_files(
                mock_client,
                None,
                "fallback response text",
                chat_id=123,
                reply_to=456,
                session=session,
            )

        # Should have sent something (the filtered response text)
        assert result == mock_sent

    async def test_react_action_sets_emoji(self):
        """delivery_action='react' calls set_reaction with the emoji."""
        from bridge.response import send_response_with_files

        session = _make_session(delivery_action="react", delivery_emoji="😁")
        mock_client = AsyncMock()

        with patch(
            "bridge.response.set_reaction", new_callable=AsyncMock, return_value=True
        ) as mock_react:
            result = await send_response_with_files(
                mock_client,
                None,
                "some output",
                chat_id=123,
                reply_to=456,
                session=session,
            )

        mock_react.assert_called_once_with(mock_client, 123, 456, "😁")
        assert result is None  # react-only returns None

    async def test_silent_action_sends_nothing(self):
        """delivery_action='silent' sends nothing at all."""
        from bridge.response import send_response_with_files

        session = _make_session(delivery_action="silent")
        mock_client = AsyncMock()

        with patch("bridge.response.set_reaction", new_callable=AsyncMock) as mock_react:
            result = await send_response_with_files(
                mock_client,
                None,
                "some output",
                chat_id=123,
                reply_to=456,
                session=session,
            )

        mock_react.assert_not_called()
        assert result is None

    async def test_no_delivery_action_falls_through(self):
        """No delivery_action falls through to normal summarizer path."""
        from bridge.response import send_response_with_files

        session = _make_session(delivery_action=None)
        mock_client = AsyncMock()
        mock_sent = MagicMock()

        with (
            patch("bridge.markdown.send_markdown", new_callable=AsyncMock, return_value=mock_sent),
            patch("bridge.summarizer.summarize_response", new_callable=AsyncMock) as mock_sum,
        ):
            # Ensure summarizer path is attempted for long responses
            mock_sum.return_value = SimpleNamespace(
                text="summarized",
                full_output_file=None,
                was_summarized=True,
                context_summary=None,
                expectations=None,
            )
            await send_response_with_files(
                mock_client,
                None,
                "A" * 300,  # Long enough to trigger summarizer
                chat_id=123,
                reply_to=456,
                session=session,
            )

        # Summarizer should have been called (no delivery instruction to bypass it)
        mock_sum.assert_called_once()


def _make_summarized_response(**kwargs):
    """Create a SummarizedResponse-like object."""
    defaults = {
        "text": "",
        "full_output_file": None,
        "was_summarized": False,
        "needs_self_summary": False,
        "artifacts": {},
        "context_summary": None,
        "expectations": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
class TestSelfSummaryFallback:
    async def test_steering_injected_when_session_available(self):
        """When needs_self_summary=True and session available, push steering and return sentinel."""
        from bridge.response import send_response_with_files
        from bridge.summarizer import STEERING_DEFERRED

        session = _make_session(session_id="test-123")
        mock_client = AsyncMock()

        summarized = _make_summarized_response(needs_self_summary=True)
        mock_push = MagicMock()
        mock_peek = MagicMock(return_value=None)  # No prior steering message

        with (
            patch(
                "bridge.summarizer.summarize_response",
                new_callable=AsyncMock,
                return_value=summarized,
            ),
            patch("agent.steering.push_steering_message", mock_push),
            patch("agent.steering.peek_steering_sender", mock_peek),
        ):
            result = await send_response_with_files(
                mock_client,
                None,
                "Let me investigate the issue. " * 20,  # long enough to trigger summarizer
                chat_id=123,
                reply_to=456,
                session=session,
            )

        assert result == STEERING_DEFERRED
        mock_push.assert_called_once()
        call_args = mock_push.call_args
        assert call_args[0][0] == "test-123"  # session_id
        assert call_args[1]["sender"] == "summarizer-fallback"

    async def test_no_session_falls_through_to_narration_gate(self):
        """When needs_self_summary=True but no session, apply narration gate."""
        from bridge.response import send_response_with_files

        # No session_id on the session
        session = _make_session(session_id=None)
        mock_client = AsyncMock()

        summarized = _make_summarized_response(needs_self_summary=True)
        # Must be >= 200 chars to trigger summarizer path
        long_narration = "Let me investigate the configuration. " * 10

        with (
            patch(
                "bridge.summarizer.summarize_response",
                new_callable=AsyncMock,
                return_value=summarized,
            ),
            patch("bridge.markdown.send_markdown", new_callable=AsyncMock) as mock_send,
        ):
            await send_response_with_files(
                mock_client,
                None,
                long_narration,
                chat_id=123,
                reply_to=456,
                session=session,
            )

        # Should have sent something (either narration fallback or truncated original)
        assert mock_send.called, "send_markdown should have been called"
        sent_text = mock_send.call_args[0][2]
        assert len(sent_text) > 0

    async def test_narration_gate_replaces_narration_text(self):
        """is_narration_only gate replaces narration with NARRATION_FALLBACK_MESSAGE."""
        from bridge.message_quality import NARRATION_FALLBACK_MESSAGE
        from bridge.response import send_response_with_files

        session = _make_session(session_id=None)
        mock_client = AsyncMock()

        summarized = _make_summarized_response(needs_self_summary=True)
        # Must be >= 200 chars to trigger summarizer path
        narration_text = "Let me check the configuration. " * 10

        with (
            patch(
                "bridge.summarizer.summarize_response",
                new_callable=AsyncMock,
                return_value=summarized,
            ),
            patch("bridge.markdown.send_markdown", new_callable=AsyncMock) as mock_send,
            patch("bridge.message_quality.is_narration_only", return_value=True),
        ):
            await send_response_with_files(
                mock_client,
                None,
                narration_text,
                chat_id=123,
                reply_to=456,
                session=session,
            )

        assert mock_send.called, "send_markdown should have been called"
        sent_text = mock_send.call_args[0][2]
        assert sent_text == NARRATION_FALLBACK_MESSAGE

    async def test_loop_prevention_skips_steering_when_already_pending(self):
        """If summarizer-fallback steering already pending, skip and fall through."""
        from bridge.response import send_response_with_files

        session = _make_session(session_id="test-loop")
        mock_client = AsyncMock()

        summarized = _make_summarized_response(needs_self_summary=True)
        # peek_steering_sender returns "summarizer-fallback" indicating already pending
        mock_peek = MagicMock(return_value="summarizer-fallback")
        mock_push = MagicMock()

        with (
            patch(
                "bridge.summarizer.summarize_response",
                new_callable=AsyncMock,
                return_value=summarized,
            ),
            patch("agent.steering.push_steering_message", mock_push),
            patch("agent.steering.peek_steering_sender", mock_peek),
            patch("bridge.markdown.send_markdown", new_callable=AsyncMock),
        ):
            result = await send_response_with_files(
                mock_client,
                None,
                "x" * 300,
                chat_id=123,
                reply_to=456,
                session=session,
            )

        # Should NOT have pushed another steering message
        mock_push.assert_not_called()
        # Should NOT have returned STEERING_DEFERRED
        from bridge.summarizer import STEERING_DEFERRED

        assert result != STEERING_DEFERRED
