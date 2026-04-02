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
        "get_parent_chat_session": lambda: None,
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
