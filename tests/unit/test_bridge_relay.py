"""Tests for bridge/telegram_relay.py -- PM outbox relay.

Tests the async relay task that processes PM-authored messages
from Redis outbox queues and sends them via Telethon.
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.telegram_relay import (
    OUTBOX_KEY_PATTERN,
    RELAY_BATCH_SIZE,
    RELAY_POLL_INTERVAL,
    _record_sent_message,
    _send_queued_message,
    get_outbox_length,
    process_outbox,
)


class TestRelayConstants:
    """Test relay configuration constants."""

    def test_poll_interval_is_100ms(self):
        assert RELAY_POLL_INTERVAL == 0.1

    def test_batch_size_is_10(self):
        assert RELAY_BATCH_SIZE == 10

    def test_outbox_key_pattern(self):
        assert OUTBOX_KEY_PATTERN == "telegram:outbox:*"


class TestSendQueuedMessage:
    """Test the single message send function."""

    @pytest.mark.asyncio
    async def test_sends_via_send_markdown(self):
        """Should send message via bridge.markdown.send_markdown."""
        mock_client = MagicMock()
        mock_sent = MagicMock()
        mock_sent.id = 42

        message = {
            "chat_id": "12345",
            "reply_to": 67890,
            "text": "Hello from PM",
            "session_id": "test-session",
        }

        mock_send = AsyncMock(return_value=mock_sent)
        with patch("bridge.markdown.send_markdown", mock_send):
            result = await _send_queued_message(mock_client, message)

        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_none_on_missing_chat_id(self):
        """Should return None for messages without chat_id."""
        result = await _send_queued_message(MagicMock(), {"text": "hello"})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_missing_text_and_file(self):
        """Should return None for messages without text or file_path."""
        result = await _send_queued_message(MagicMock(), {"chat_id": "123"})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_send_failure(self):
        """Should return None when Telethon send fails."""
        message = {"chat_id": "123", "text": "hello", "session_id": "s"}

        mock_send = AsyncMock(side_effect=Exception("Network error"))
        with patch("bridge.markdown.send_markdown", mock_send):
            result = await _send_queued_message(MagicMock(), message)

        assert result is None

    @pytest.mark.asyncio
    async def test_sends_file_via_send_file(self):
        """Should use client.send_file() when file_path is present."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 55
            mock_client.send_file = AsyncMock(return_value=mock_sent)

            message = {
                "chat_id": "12345",
                "reply_to": 67890,
                "text": "Check this",
                "file_path": tmp_path,
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            assert result == 55
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_path,
                caption="Check this",
                reply_to=67890,
            )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_file_only_send_no_caption(self):
        """Should send file with caption=None when text is empty."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake pdf")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 66
            mock_client.send_file = AsyncMock(return_value=mock_sent)

            message = {
                "chat_id": "12345",
                "text": "",
                "file_path": tmp_path,
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            assert result == 66
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_path,
                caption=None,
                reply_to=None,
            )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_missing_file_falls_back_to_text(self):
        """Should fall back to text-only when file_path is present but file missing."""
        mock_client = MagicMock()
        mock_sent = MagicMock()
        mock_sent.id = 77

        message = {
            "chat_id": "12345",
            "text": "The file was here",
            "file_path": "/nonexistent/deleted.png",
            "session_id": "test-session",
        }

        mock_send = AsyncMock(return_value=mock_sent)
        with patch("bridge.markdown.send_markdown", mock_send):
            result = await _send_queued_message(mock_client, message)

        assert result == 77
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_file_no_text_returns_none(self):
        """Should return None when file is missing and no text to fall back to."""
        mock_client = MagicMock()

        message = {
            "chat_id": "12345",
            "text": "",
            "file_path": "/nonexistent/deleted.png",
            "session_id": "test-session",
        }

        result = await _send_queued_message(mock_client, message)
        assert result is None


class TestRecordSentMessage:
    """Test recording sent message IDs on AgentSession."""

    def test_records_message_on_session(self):
        """Should call record_pm_message on the newest session."""
        mock_session = MagicMock()
        mock_session.created_at = 100.0
        mock_session.record_pm_message = MagicMock()

        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [mock_session]
            _record_sent_message("test-session", 42)

        mock_session.record_pm_message.assert_called_once_with(42)

    def test_handles_missing_session(self):
        """Should not crash when session is not found."""
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []
            # Should not raise
            _record_sent_message("nonexistent-session", 42)

    def test_handles_query_exception(self):
        """Should not crash on Redis errors."""
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.side_effect = Exception("Redis down")
            # Should not raise
            _record_sent_message("test-session", 42)


class TestGetOutboxLength:
    """Test outbox queue length checking."""

    def test_returns_queue_length(self):
        """Should return the number of pending messages."""
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 3

        with patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis):
            length = get_outbox_length("test-session")

        assert length == 3
        mock_redis.llen.assert_called_once_with("telegram:outbox:test-session")

    def test_returns_zero_on_error(self):
        """Should return 0 when Redis is unavailable."""
        with patch("bridge.telegram_relay._get_redis_connection", side_effect=Exception("down")):
            length = get_outbox_length("test-session")

        assert length == 0


class TestProcessOutbox:
    """Test the outbox processing cycle."""

    @pytest.mark.asyncio
    async def test_processes_queued_messages(self):
        """Should pop messages from Redis, send via Telethon, and record IDs."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "reply_to": 67890,
                "text": "PM message",
                "session_id": "test-session",
            }
        )
        # First lpop returns message, second returns None (queue empty)
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        mock_sent = MagicMock()
        mock_sent.id = 99

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message", new_callable=AsyncMock
            ) as mock_send,
            patch("bridge.telegram_relay._record_sent_message") as mock_record,
        ):
            mock_send.return_value = 99
            sent = await process_outbox(MagicMock())

        assert sent == 1
        mock_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_requeues_on_send_failure(self):
        """Should re-push message to queue tail on send failure."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "fail message",
                "session_id": "test-session",
            }
        )
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message", new_callable=AsyncMock
            ) as mock_send,
        ):
            mock_send.return_value = None  # Send failed
            sent = await process_outbox(MagicMock())

        assert sent == 0
        # Verify re-push
        mock_redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_malformed_json(self):
        """Should skip queue entries with invalid JSON."""
        mock_redis = MagicMock()
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = ["not valid json", None]

        with patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis):
            sent = await process_outbox(MagicMock())

        assert sent == 0

    @pytest.mark.asyncio
    async def test_handles_redis_error(self):
        """Should handle Redis connection errors without crashing."""
        with patch(
            "bridge.telegram_relay._get_redis_connection",
            side_effect=Exception("Connection refused"),
        ):
            sent = await process_outbox(MagicMock())

        assert sent == 0
