"""Tests for tools/send_telegram.py -- PM self-messaging tool.

Tests the CLI tool that ChatSession uses to send Telegram messages
via the Redis outbox queue, bypassing the summarizer.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


class TestSendTelegramValidation:
    """Test input validation and environment variable checks."""

    def test_missing_chat_id_exits(self):
        """Should exit with error when TELEGRAM_CHAT_ID is not set."""
        env = {"VALOR_SESSION_ID": "test-session"}
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("Hello")
            assert exc_info.value.code == 1

    def test_missing_session_id_exits(self):
        """Should exit with error when VALOR_SESSION_ID is not set."""
        env = {"TELEGRAM_CHAT_ID": "12345"}
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("Hello")
            assert exc_info.value.code == 1

    def test_empty_message_exits(self):
        """Should exit with error when message text is empty."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("")
            assert exc_info.value.code == 1

    def test_whitespace_only_message_exits(self):
        """Should exit with error when message is only whitespace."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("   ")
            assert exc_info.value.code == 1


class TestSendTelegramQueueing:
    """Test Redis queue operations."""

    def test_queues_message_to_redis(self):
        """Should push a JSON message to the Redis outbox queue."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session-123",
        }

        mock_redis = MagicMock()

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
        ):
            from tools.send_telegram import send_message

            send_message("Hello, stakeholder!")

        # Verify RPUSH was called with correct key
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == "telegram:outbox:test-session-123"

        # Verify message payload
        payload = json.loads(call_args[0][1])
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 67890
        assert payload["text"] == "Hello, stakeholder!"
        assert payload["session_id"] == "test-session-123"
        assert "timestamp" in payload

        # Verify TTL was set
        mock_redis.expire.assert_called_once_with("telegram:outbox:test-session-123", 3600)

    def test_truncates_long_messages(self):
        """Should truncate messages exceeding Telegram's limit."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        long_message = "A" * 5000

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
        ):
            from tools.send_telegram import send_message

            send_message(long_message)

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert len(payload["text"]) <= 4096
        assert payload["text"].endswith("...")

    def test_redis_failure_exits(self):
        """Should exit with error when Redis connection fails."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_redis.rpush.side_effect = Exception("Connection refused")

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
        ):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("Hello")
            assert exc_info.value.code == 1

    def test_reply_to_none_when_not_set(self):
        """Should handle missing TELEGRAM_REPLY_TO gracefully."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
        ):
            from tools.send_telegram import send_message

            send_message("Hello without reply")

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] is None


class TestSendTelegramCli:
    """Test CLI entry point."""

    def test_main_with_args(self):
        """Should join argv into message text."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
            patch("sys.argv", ["send_telegram.py", "Hello", "World"]),
        ):
            from tools.send_telegram import main

            main()

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["text"] == "Hello World"

    def test_main_no_args_exits(self):
        """Should exit with error when no arguments provided."""
        with patch("sys.argv", ["send_telegram.py"]):
            from tools.send_telegram import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
