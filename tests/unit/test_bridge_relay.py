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
    KNOWN_MESSAGE_TYPES,
    MAX_RELAY_RETRIES,
    OUTBOX_KEY_PATTERN,
    RELAY_BATCH_SIZE,
    RELAY_POLL_INTERVAL,
    _dead_letter_message,
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

    def test_max_relay_retries(self):
        assert MAX_RELAY_RETRIES == 3

    def test_known_message_types(self):
        assert KNOWN_MESSAGE_TYPES == {None, "reaction", "custom_emoji_message"}


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
        """Should use client.send_file() when file_paths list is present."""
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
                "file_paths": [tmp_path],
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
                "file_paths": [tmp_path],
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
        """Should fall back to text-only when all files missing but text present."""
        mock_client = MagicMock()
        mock_sent = MagicMock()
        mock_sent.id = 77

        message = {
            "chat_id": "12345",
            "text": "The file was here",
            "file_paths": ["/nonexistent/deleted.png"],
            "session_id": "test-session",
        }

        mock_send = AsyncMock(return_value=mock_sent)
        with patch("bridge.markdown.send_markdown", mock_send):
            result = await _send_queued_message(mock_client, message)

        assert result == 77
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_file_no_text_returns_none(self):
        """Should return None when all files missing and no text to fall back to."""
        mock_client = MagicMock()

        message = {
            "chat_id": "12345",
            "text": "",
            "file_paths": ["/nonexistent/deleted.png"],
            "session_id": "test-session",
        }

        result = await _send_queued_message(mock_client, message)
        assert result is None

    @pytest.mark.asyncio
    async def test_backward_compat_file_path_string(self):
        """Should handle legacy file_path (string) payloads during rolling deployment."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 88
            mock_client.send_file = AsyncMock(return_value=mock_sent)

            # Legacy payload with file_path (string), not file_paths (list)
            message = {
                "chat_id": "12345",
                "text": "Legacy payload",
                "file_path": tmp_path,
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            assert result == 88
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_path,
                caption="Legacy payload",
                reply_to=None,
            )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_multi_file_album_send(self):
        """Should send multiple files as album via send_file with list."""
        tmp_files = []
        try:
            for suffix in [".png", ".jpg", ".gif"]:
                f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                f.write(b"fake data")
                f.close()
                tmp_files.append(f.name)

            mock_client = MagicMock()
            # Telethon returns list of Messages for albums
            mock_msgs = [MagicMock(id=101), MagicMock(id=102), MagicMock(id=103)]
            mock_client.send_file = AsyncMock(return_value=mock_msgs)

            message = {
                "chat_id": "12345",
                "text": "Album caption",
                "file_paths": tmp_files,
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            assert result == 101  # First message ID
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_files,
                caption="Album caption",
                reply_to=None,
            )
        finally:
            for f in tmp_files:
                os.unlink(f)

    @pytest.mark.asyncio
    async def test_partial_missing_files_sends_available(self):
        """Should send available files when some are missing at relay time."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 99
            mock_client.send_file = AsyncMock(return_value=mock_sent)

            message = {
                "chat_id": "12345",
                "text": "Partial album",
                "file_paths": [tmp_path, "/nonexistent/missing.png"],
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            assert result == 99
            # Should only send the available file
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_path,
                caption="Partial album",
                reply_to=None,
            )
        finally:
            os.unlink(tmp_path)


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


class TestDeadLetterMessage:
    """Test the dead letter routing helper."""

    @pytest.mark.asyncio
    async def test_persists_text_message_to_dead_letter(self):
        """Should call persist_failed_delivery for text messages."""
        message = {
            "chat_id": "12345",
            "reply_to": 67890,
            "text": "Failed message",
            "session_id": "test-session",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="max retries exceeded")

        mock_persist.assert_called_once_with(
            chat_id=12345,
            reply_to=67890,
            text="Failed message",
        )

    @pytest.mark.asyncio
    async def test_discards_reaction_without_persisting(self):
        """Should log and discard reactions without calling persist_failed_delivery."""
        message = {
            "type": "reaction",
            "chat_id": "12345",
            "reply_to": 67890,
            "emoji": "thumbsup",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="max retries exceeded")

        mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_discards_custom_emoji_without_persisting(self):
        """Should log and discard custom emoji messages without persisting."""
        message = {
            "type": "custom_emoji_message",
            "chat_id": "12345",
            "emoji": "star",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="max retries exceeded")

        mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_persist_failure_gracefully(self):
        """Should not raise when persist_failed_delivery fails."""
        message = {
            "chat_id": "12345",
            "text": "Failed message",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery",
            new_callable=AsyncMock,
            side_effect=Exception("Redis down"),
        ):
            # Should not raise
            await _dead_letter_message(message, reason="test")

    @pytest.mark.asyncio
    async def test_discards_message_without_text_or_chat_id(self):
        """Should discard messages that have no text or chat_id."""
        message = {"chat_id": "12345"}  # No text

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="test")

        mock_persist.assert_not_called()


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
        """Should re-push message with _relay_attempts to queue tail on send failure."""
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
        # Verify re-push with _relay_attempts
        mock_redis.rpush.assert_called_once()
        requeued_payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert requeued_payload["_relay_attempts"] == 1

    @pytest.mark.asyncio
    async def test_dead_letters_after_max_retries(self):
        """Should route to dead letter after MAX_RELAY_RETRIES attempts."""
        mock_redis = MagicMock()
        # Message already at MAX_RELAY_RETRIES - 1 attempts
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "persistent failure",
                "session_id": "test-session",
                "_relay_attempts": MAX_RELAY_RETRIES - 1,
            }
        )
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message", new_callable=AsyncMock
            ) as mock_send,
            patch(
                "bridge.telegram_relay._dead_letter_message", new_callable=AsyncMock
            ) as mock_dead_letter,
        ):
            mock_send.return_value = None  # Send failed
            sent = await process_outbox(MagicMock())

        assert sent == 0
        # Should NOT re-queue
        mock_redis.rpush.assert_not_called()
        # Should dead-letter
        mock_dead_letter.assert_called_once()
        dead_msg = mock_dead_letter.call_args[0][0]
        assert dead_msg["_relay_attempts"] == MAX_RELAY_RETRIES

    @pytest.mark.asyncio
    async def test_unknown_message_type_discarded(self):
        """Should discard messages with unknown type without re-queue."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "type": "bogus_type",
                "chat_id": "12345",
                "text": "unknown",
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
            sent = await process_outbox(MagicMock())

        assert sent == 0
        # Should NOT call any handler
        mock_send.assert_not_called()
        # Should NOT re-queue
        mock_redis.rpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_exception_feeds_retry_path(self):
        """Should catch handler exceptions and feed into bounded retry."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "crash message",
                "session_id": "test-session",
            }
        )
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Unexpected crash"),
            ),
        ):
            sent = await process_outbox(MagicMock())

        assert sent == 0
        # Should re-queue with _relay_attempts
        mock_redis.rpush.assert_called_once()
        requeued_payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert requeued_payload["_relay_attempts"] == 1

    @pytest.mark.asyncio
    async def test_reaction_failure_uses_bounded_retry(self):
        """Should use bounded retry for reaction failures instead of silent discard."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "type": "reaction",
                "chat_id": "12345",
                "reply_to": 67890,
                "emoji": "thumbsup",
            }
        )
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_reaction", new_callable=AsyncMock
            ) as mock_reaction,
        ):
            mock_reaction.return_value = False  # Reaction failed
            sent = await process_outbox(MagicMock())

        assert sent == 0
        # Should re-queue with retry counter
        mock_redis.rpush.assert_called_once()
        requeued_payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert requeued_payload["_relay_attempts"] == 1

    @pytest.mark.asyncio
    async def test_custom_emoji_failure_uses_bounded_retry(self):
        """Should use bounded retry for custom emoji failures."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "type": "custom_emoji_message",
                "chat_id": "12345",
                "emoji": "star",
            }
        )
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [message, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_custom_emoji_message", new_callable=AsyncMock
            ) as mock_emoji,
        ):
            mock_emoji.return_value = None  # Send failed
            sent = await process_outbox(MagicMock())

        assert sent == 0
        mock_redis.rpush.assert_called_once()
        requeued_payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert requeued_payload["_relay_attempts"] == 1

    @pytest.mark.asyncio
    async def test_successful_messages_unaffected(self):
        """Should not add _relay_attempts or change behavior for successful sends."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "success message",
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
            patch("bridge.telegram_relay._record_sent_message"),
        ):
            mock_send.return_value = 42
            sent = await process_outbox(MagicMock())

        assert sent == 1
        # Should NOT re-queue
        mock_redis.rpush.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_retry_counter_increments_across_cycles(self):
        """Should increment _relay_attempts correctly when message already has attempts."""
        mock_redis = MagicMock()
        # Message with 1 prior attempt
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "retrying",
                "session_id": "test-session",
                "_relay_attempts": 1,
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
            mock_send.return_value = None
            await process_outbox(MagicMock())

        # Should re-queue with attempts=2
        mock_redis.rpush.assert_called_once()
        requeued_payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert requeued_payload["_relay_attempts"] == 2

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure_batch(self):
        """Should handle a batch with both successful and failed messages correctly."""
        mock_redis = MagicMock()
        success_msg = json.dumps({"chat_id": "12345", "text": "good", "session_id": "s1"})
        fail_msg = json.dumps({"chat_id": "12345", "text": "bad", "session_id": "s2"})
        success_msg2 = json.dumps({"chat_id": "12345", "text": "also good", "session_id": "s3"})
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [success_msg, fail_msg, success_msg2, None]

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message", new_callable=AsyncMock
            ) as mock_send,
            patch("bridge.telegram_relay._record_sent_message"),
        ):
            # First succeeds, second fails, third succeeds
            mock_send.side_effect = [42, None, 43]
            sent = await process_outbox(MagicMock())

        assert sent == 2
        # Only the failed message should be re-queued
        assert mock_redis.rpush.call_count == 1
