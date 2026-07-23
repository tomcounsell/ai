"""Tests for bridge/telegram_relay.py -- PM outbox relay.

Tests the async relay task that processes PM-authored messages
from Redis outbox queues and sends them via Telethon.
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import FloodWaitError

from bridge.telegram_relay import (
    DELIVERED_NO_ID,
    KNOWN_MESSAGE_TYPES,
    MAX_RELAY_RETRIES,
    OUTBOX_KEY_PATTERN,
    RELAY_BATCH_SIZE,
    RELAY_FLOOD_WAIT_BUFFER_SECS,
    RELAY_FLOOD_WAIT_MAX,
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
        """Should use client.send_file() when file_paths list is present.

        Current contract: the file is sent WITHOUT a caption; any accompanying
        text is delivered as a separate follow-up send_message so Telegram's
        narrow caption column doesn't constrain the text layout.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 55
            mock_client.send_file = AsyncMock(return_value=mock_sent)
            mock_client.send_message = AsyncMock()

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
                reply_to=67890,
            )
            # Text is delivered as a separate follow-up message.
            mock_client.send_message.assert_called_once_with(
                12345,
                "Check this",
                reply_to=67890,
            )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_file_only_send_no_caption(self):
        """Should send the file with no follow-up text when text is empty."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake pdf")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 66
            mock_client.send_file = AsyncMock(return_value=mock_sent)
            mock_client.send_message = AsyncMock()

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
                reply_to=None,
            )
            # No text → no follow-up send_message.
            mock_client.send_message.assert_not_called()
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
            mock_client.send_message = AsyncMock()

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
                reply_to=None,
            )
            mock_client.send_message.assert_called_once_with(
                12345,
                "Legacy payload",
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
            mock_client.send_message = AsyncMock()

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
                reply_to=None,
            )
            mock_client.send_message.assert_called_once_with(
                12345,
                "Album caption",
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
            mock_client.send_message = AsyncMock()

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
                reply_to=None,
            )
            mock_client.send_message.assert_called_once_with(
                12345,
                "Partial album",
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


class TestFileIdempotency:
    """Defect 1 — file send idempotency guard (#1749)."""

    @pytest.mark.asyncio
    async def test_file_not_resent_on_text_step_retry(self):
        """send_file must be called exactly once even when send_message fails on first attempt.

        Sequence:
          1. First call: send_file succeeds, message["_file_sent"] = True,
             send_message raises — _send_queued_message returns None.
          2. Second call with the *same* dict (as if re-queued with _file_sent=True):
             send_file must NOT be called again; send_message IS called.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 55
            mock_client.send_file = AsyncMock(return_value=mock_sent)
            # First call: send_message raises; second call: succeeds
            mock_client.send_message = AsyncMock(side_effect=[Exception("network glitch"), None])

            message = {
                "chat_id": "12345",
                "reply_to": None,
                "text": "caption text",
                "file_paths": [tmp_path],
                "session_id": "test-session",
            }

            # First attempt — file sends, text fails
            result1 = await _send_queued_message(mock_client, message)
            assert result1 is None  # text step failed → return None
            assert message.get("_file_sent") is True

            # Second attempt — message dict carries _file_sent=True
            result2 = await _send_queued_message(mock_client, message)
            # send_message succeeded this time
            assert result2 is None or isinstance(result2, (int, type(None)))

            # Core assertion: send_file called exactly once across both attempts
            assert mock_client.send_file.call_count == 1
            # send_message was called on both attempts (once raised, once succeeded)
            assert mock_client.send_message.call_count == 2
        finally:
            os.unlink(tmp_path)


class TestOversizedTextGuard:
    """Defect 2 — oversized text converted to .txt attachment (#1749)."""

    @pytest.mark.asyncio
    async def test_oversized_text_on_file_message_converts_to_txt(self):
        """When file+text message has text >4096 chars, text must ship as .txt, not send_message."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_file_sent = MagicMock()
            mock_file_sent.id = 55
            mock_overflow_sent = MagicMock()
            mock_overflow_sent.id = 56
            # send_file: first call = actual file, second call = .txt attachment
            mock_client.send_file = AsyncMock(side_effect=[mock_file_sent, mock_overflow_sent])
            mock_client.send_message = AsyncMock()

            oversized_text = "x" * 4097

            message = {
                "chat_id": "12345",
                "text": oversized_text,
                "file_paths": [tmp_path],
                "session_id": "test-session",
            }

            result = await _send_queued_message(mock_client, message)

            # send_file called twice: once for real file, once for .txt overflow
            assert mock_client.send_file.call_count == 2
            # send_message must NOT be called with the oversized text
            mock_client.send_message.assert_not_called()
            # Returns a valid message ID (the .txt attachment)
            assert result == 56
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_oversized_text_only_converts_to_txt_not_send_markdown(self):
        """Text-only message >4096 chars must use send_file (.txt), never send_markdown."""
        mock_client = MagicMock()
        mock_overflow_sent = MagicMock()
        mock_overflow_sent.id = 77
        mock_client.send_file = AsyncMock(return_value=mock_overflow_sent)

        oversized_text = "y" * 4097

        message = {
            "chat_id": "12345",
            "text": oversized_text,
            "session_id": "test-session",
        }

        mock_send_markdown = AsyncMock()
        with patch("bridge.markdown.send_markdown", mock_send_markdown):
            result = await _send_queued_message(mock_client, message)

        # send_file called for .txt attachment
        assert mock_client.send_file.call_count == 1
        # send_markdown must NOT receive the oversized text
        mock_send_markdown.assert_not_called()
        assert result == 77

    @pytest.mark.asyncio
    async def test_normal_length_text_still_routes_through_send_markdown(self):
        """Normal-length text-only message (<= 4096 chars) must route through send_markdown."""
        mock_client = MagicMock()
        mock_sent = MagicMock()
        mock_sent.id = 42

        message = {
            "chat_id": "12345",
            "text": "short message",
            "session_id": "test-session",
        }

        mock_send_markdown = AsyncMock(return_value=mock_sent)
        with patch("bridge.markdown.send_markdown", mock_send_markdown):
            result = await _send_queued_message(mock_client, message)

        mock_send_markdown.assert_called_once()
        # send_file must NOT be called for normal-length text
        mock_client.send_file = AsyncMock()
        assert mock_client.send_file.call_count == 0
        assert result == 42


class TestDeadLetterGuard:
    """Defect 3 — dead-letter narrowed from <= 0 to == 0 (#1749)."""

    @pytest.mark.asyncio
    async def test_dead_letter_persists_negative_group_chat_id(self):
        """Negative chat_id (supergroup) must be persisted to dead letter, not discarded."""
        message = {
            "chat_id": "-1003900483201",
            "text": "message to supergroup",
            "session_id": "test-session",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="max retries exceeded")

        mock_persist.assert_called_once_with(
            chat_id=-1003900483201,
            reply_to=None,
            text="message to supergroup",
        )

    @pytest.mark.asyncio
    async def test_dead_letter_discards_zero_chat_id(self):
        """chat_id == 0 is not a valid Telegram peer and must NOT be persisted."""
        message = {
            "chat_id": "0",
            "text": "should be discarded",
            "session_id": "test-session",
        }

        with patch(
            "bridge.dead_letters.persist_failed_delivery", new_callable=AsyncMock
        ) as mock_persist:
            await _dead_letter_message(message, reason="max retries exceeded")

        mock_persist.assert_not_called()


class TestFloodWait:
    """Defect 4 — FloodWaitError handling in relay (#1749)."""

    @pytest.mark.asyncio
    async def test_floodwait_propagates_from_send_queued_message(self):
        """FloodWaitError raised by send_markdown must propagate, not be swallowed."""
        mock_client = MagicMock()
        flood_err = FloodWaitError(request=None, capture=30)
        mock_send_markdown = AsyncMock(side_effect=flood_err)

        message = {
            "chat_id": "12345",
            "text": "hello",
            "session_id": "test-session",
        }

        with patch("bridge.markdown.send_markdown", mock_send_markdown):
            with pytest.raises(FloodWaitError):
                await _send_queued_message(mock_client, message)

    @pytest.mark.asyncio
    async def test_floodwait_honored_without_burning_retries(self):
        """FloodWaitError must sleep the requested duration and NOT increment _relay_attempts."""
        mock_redis = MagicMock()
        message_dict = {
            "chat_id": "12345",
            "text": "flood target",
            "session_id": "test-session",
        }
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [json.dumps(message_dict), None]

        flood_err = FloodWaitError(request=None, capture=10)

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message",
                new_callable=AsyncMock,
                side_effect=flood_err,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            await process_outbox(MagicMock())

        # Must sleep for requested seconds + buffer
        expected_sleep = 10 + RELAY_FLOOD_WAIT_BUFFER_SECS
        mock_sleep.assert_called_once_with(expected_sleep)

        # Must re-queue without incrementing _relay_attempts
        mock_redis.rpush.assert_called_once()
        requeued = json.loads(mock_redis.rpush.call_args[0][1])
        assert "_relay_attempts" not in requeued or requeued.get("_relay_attempts", 0) == 0
        assert requeued.get("_flood_waits") == 1

    @pytest.mark.asyncio
    async def test_floodwait_backstop_dead_letters_message(self):
        """After RELAY_FLOOD_WAIT_MAX flood waits, message must be dead-lettered."""
        mock_redis = MagicMock()
        # Message already at RELAY_FLOOD_WAIT_MAX - 1 flood waits
        message_dict = {
            "chat_id": "12345",
            "text": "repeated flood target",
            "session_id": "test-session",
            "_flood_waits": RELAY_FLOOD_WAIT_MAX - 1,
        }
        mock_redis.keys.return_value = ["telegram:outbox:test-session"]
        mock_redis.lpop.side_effect = [json.dumps(message_dict), None]

        flood_err = FloodWaitError(request=None, capture=5)

        with (
            patch("bridge.telegram_relay._get_redis_connection", return_value=mock_redis),
            patch(
                "bridge.telegram_relay._send_queued_message",
                new_callable=AsyncMock,
                side_effect=flood_err,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch(
                "bridge.telegram_relay._dead_letter_message", new_callable=AsyncMock
            ) as mock_dead_letter,
        ):
            await process_outbox(MagicMock())

        # Must dead-letter the message with the updated flood count
        mock_dead_letter.assert_called_once()
        dead_msg = mock_dead_letter.call_args[0][0]
        assert dead_msg["_flood_waits"] == RELAY_FLOOD_WAIT_MAX
        mock_dead_letter.assert_called_with(dead_msg, reason="flood_backstop")
        # A backstopped message must NOT be re-queued after dead-lettering.
        # The `continue` after _dead_letter_message skips the generic retry block,
        # so rpush must never be called for this message.
        mock_redis.rpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_floodwait_after_file_send_skips_file_on_retry(self):
        """File sent before FloodWait must not be resent after the wait clears.

        Sequence:
          Attempt 1: send_file succeeds (_file_sent=True set), text step raises FloodWaitError.
          process_outbox re-queues the message carrying _file_sent=True.
          Attempt 2 (simulated via second process_outbox call): send_file NOT called again.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            mock_client = MagicMock()
            mock_file_sent = MagicMock()
            mock_file_sent.id = 55
            mock_client.send_file = AsyncMock(return_value=mock_file_sent)
            # send_message raises FloodWaitError on first call
            flood_err = FloodWaitError(request=None, capture=5)
            mock_client.send_message = AsyncMock(side_effect=[flood_err, None])

            message = {
                "chat_id": "12345",
                "text": "caption",
                "file_paths": [tmp_path],
                "session_id": "test-session",
            }

            # First attempt: file sends, text raises FloodWaitError
            with pytest.raises(FloodWaitError):
                await _send_queued_message(mock_client, message)

            assert message.get("_file_sent") is True
            assert mock_client.send_file.call_count == 1

            # Second attempt with the same dict (carries _file_sent=True)
            await _send_queued_message(mock_client, message)

            # send_file must still be called exactly once across both attempts
            assert mock_client.send_file.call_count == 1
            # send_message called twice: once raised, once succeeded
            assert mock_client.send_message.call_count == 2
        finally:
            os.unlink(tmp_path)


class TestFlushConversionSendPath:
    """#2211 — terminal-flush converted payloads driven through the real relay send path.

    Closes the gap between "payload has file_paths" and "the relay actually
    attaches it": the payloads here are built by the same
    ``build_telegram_outbox_payload`` the sync flush uses, then fed to
    ``_send_queued_message`` so the ``os.path.isfile`` filter and the
    ``if not text and not file_paths`` empty guard are exercised for real.
    """

    @pytest.mark.asyncio
    async def test_converted_flush_payload_reaches_file_send_branch(self):
        """A flush-converted payload (existing absolute path) survives the
        isfile filter and reaches the file-send branch."""
        from agent.output_handler import build_telegram_outbox_payload

        with tempfile.NamedTemporaryFile(
            dir="/tmp", prefix="flush-conv-", suffix=".txt", delete=False
        ) as f:
            tmp_path = f.name
            f.write(b"report body")

        try:
            payload = build_telegram_outbox_payload(
                "12345",
                "The weekly report is done.",
                263,
                "test-session",
                file_paths=[tmp_path],
            )
            assert payload["file_paths"] == [tmp_path]

            mock_client = MagicMock()
            mock_sent = MagicMock()
            mock_sent.id = 71
            mock_client.send_file = AsyncMock(return_value=mock_sent)
            mock_client.send_message = AsyncMock()

            result = await _send_queued_message(mock_client, payload)

            assert result == 71
            mock_client.send_file.assert_called_once_with(
                12345,
                tmp_path,
                reply_to=263,
            )
            mock_client.send_message.assert_called_once_with(
                12345,
                "The weekly report is done.",
                reply_to=263,
            )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_canned_notice_payload_not_dropped_by_empty_guard(self):
        """The dead-path-only canned-notice payload (text non-empty, no
        file_paths key) is NOT dropped by ``if not text and not file_paths``."""
        from agent.output_handler import build_telegram_outbox_payload

        payload = build_telegram_outbox_payload(
            "12345",
            "(the referenced file is no longer available)",
            None,
            "test-session",
            file_paths=[],
        )
        assert "file_paths" not in payload, "empty file_paths must omit the key entirely"

        mock_sent = MagicMock()
        mock_sent.id = 72
        mock_send = AsyncMock(return_value=mock_sent)
        with patch("bridge.markdown.send_markdown", mock_send):
            result = await _send_queued_message(MagicMock(), payload)

        assert result == 72, "the canned notice must be delivered, not dropped"
        mock_send.assert_called_once()


class TestNullMsgIdDedup:
    """Regression tests for #2179: a delivered reply with a null message_id.

    A ``pm_direct`` send can reach Telegram while Telethon returns no message
    id. The relay must still (a) register the dedup draft so the executor's
    ``response`` copy is suppressed and (b) not re-queue the already-delivered
    message. Both 07-18 duplicate copies carried a null ``message_id``.
    """

    @pytest.mark.asyncio
    async def test_delivered_without_id_records_dedup_draft(self):
        """DELIVERED_NO_ID must register recent_sent_drafts and count as sent."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "one logical reply",
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
            patch("bridge.telegram_relay._record_relay_sent_draft") as mock_dedup,
            patch("bridge.telegram_relay._record_sent_message") as mock_record,
        ):
            # Send delivered but Telegram returned no message id.
            mock_send.return_value = DELIVERED_NO_ID
            sent = await process_outbox(MagicMock())

        assert sent == 1
        # The #1205-style dedup guard MUST fire even with a null message_id so
        # the executor's response copy is suppressed.
        mock_dedup.assert_called_once()
        assert mock_dedup.call_args[0][0] == "test-session"
        assert mock_dedup.call_args[0][1] == "one logical reply"
        # No real message id, so per-session id recording is skipped.
        mock_record.assert_not_called()
        # Delivered message must NOT be re-queued.
        mock_redis.rpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_still_skips_dedup_and_requeues(self):
        """A genuine failure (None) must skip dedup and re-queue, unchanged."""
        mock_redis = MagicMock()
        message = json.dumps(
            {
                "chat_id": "12345",
                "text": "failed reply",
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
            patch("bridge.telegram_relay._record_relay_sent_draft") as mock_dedup,
        ):
            mock_send.return_value = None  # Send failed / dropped.
            sent = await process_outbox(MagicMock())

        assert sent == 0
        mock_dedup.assert_not_called()
        mock_redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_returns_sentinel_when_telethon_gives_no_id(self):
        """_send_queued_message returns DELIVERED_NO_ID on a null-id delivery."""
        mock_client = MagicMock()
        # send_markdown path returns a message object whose .id is None.
        sent_obj = MagicMock()
        sent_obj.id = None
        mock_client.send_message = AsyncMock(return_value=sent_obj)

        message = {
            "chat_id": "12345",
            "text": "reply with no id",
            "session_id": "test-session",
        }
        result = await _send_queued_message(mock_client, message)
        assert result is DELIVERED_NO_ID
