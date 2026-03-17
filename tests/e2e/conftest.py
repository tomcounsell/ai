"""Shared fixtures for e2e tests.

Provides mock boundaries for Telegram API and Claude CLI subprocess
while allowing real Redis and real internal wiring.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_telegram_client():
    """AsyncMock Telethon client with common methods stubbed."""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=9999))
    client.send_file = AsyncMock(return_value=MagicMock(id=9998))
    client.get_messages = AsyncMock(return_value=[])
    client.get_dialogs = AsyncMock(return_value=[])
    # SendReactionRequest via __call__
    client.__call__ = AsyncMock(return_value=None)
    return client


def _make_telegram_event(
    chat_id=-1001234567890,
    message_id=42,
    text="Hello Valor",
    sender_id=111222333,
    sender_first_name="TestUser",
    sender_username="testuser",
    is_private=False,
    is_group=True,
    reply_to_msg_id=None,
    out=False,
    chat_title="Dev: Valor",
):
    """Factory for fake Telegram events with configurable fields."""
    sender = MagicMock()
    sender.id = sender_id
    sender.first_name = sender_first_name
    sender.username = sender_username

    message = MagicMock()
    message.id = message_id
    message.text = text
    message.out = out
    message.reply_to_msg_id = reply_to_msg_id
    message.date = None
    message.media = None
    message.get_sender = AsyncMock(return_value=sender)

    chat = MagicMock()
    chat.id = chat_id
    chat.title = chat_title

    event = MagicMock()
    event.message = message
    event.chat_id = chat_id
    event.is_private = is_private
    event.is_group = is_group
    event.chat = chat
    event.sender_id = sender_id

    return event


@pytest.fixture
def make_telegram_event():
    """Factory fixture: call with kwargs to create fake Telegram events."""
    return _make_telegram_event


@pytest.fixture
def mock_agent_response():
    """Patch get_agent_response_sdk to return a canned response."""
    with patch("agent.sdk_client.get_agent_response_sdk") as mock_fn:
        mock_fn.return_value = "I received your message and here is my response."
        yield mock_fn


@pytest.fixture
def e2e_config(sample_config):
    """Real config loading with test overrides.

    Uses sample_config from the root conftest and sets up routing module globals.
    """
    return sample_config
