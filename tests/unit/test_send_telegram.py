"""Tests for tools/send_telegram.py -- PM self-messaging tool.

Tests the CLI tool that ChatSession uses to send Telegram messages
via the Redis outbox queue, bypassing the summarizer.
"""

import json
import os
import tempfile
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
        """Should exit with error when message text is empty and no file."""
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
        assert "file_paths" not in payload

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


class TestSendTelegramFileSupport:
    """Test file attachment support."""

    def test_queues_file_payload(self):
        """Should include file_paths in Redis payload when --file is provided."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image data")

        try:
            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
                patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
            ):
                from tools.send_telegram import send_message

                send_message("Check this screenshot", file_paths=[tmp_path])

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert payload["text"] == "Check this screenshot"
            assert payload["file_paths"] == [tmp_path]
            assert payload["session_id"] == "test-session"
        finally:
            os.unlink(tmp_path)

    def test_file_only_send(self):
        """Should allow file-only sends with no caption text."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake pdf data")

        try:
            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            ):
                from tools.send_telegram import send_message

                send_message("", file_paths=[tmp_path])

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert payload["text"] == ""
            assert payload["file_paths"] == [tmp_path]
        finally:
            os.unlink(tmp_path)

    def test_file_not_found_exits(self):
        """Should exit with error when file does not exist."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("caption", file_paths=["/nonexistent/file.png"])
            assert exc_info.value.code == 1

    def test_empty_file_path_exits(self):
        """Should exit with error when --file path is empty string."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_message

            with pytest.raises(SystemExit) as exc_info:
                send_message("caption", file_paths=[""])
            assert exc_info.value.code == 1

    def test_file_path_normalized_to_absolute(self):
        """Should normalize file paths to absolute paths."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            tmp_path = f.name
            f.write(b"test data")

        try:
            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
                patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
            ):
                from tools.send_telegram import send_message

                send_message("text", file_paths=[tmp_path])

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert all(os.path.isabs(p) for p in payload["file_paths"])
        finally:
            os.unlink(tmp_path)

    def test_multi_file_queues_album(self):
        """Should queue multiple files as a file_paths list."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        tmp_files = []

        try:
            for suffix in [".png", ".jpg", ".gif"]:
                f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                f.write(b"fake data")
                f.close()
                tmp_files.append(f.name)

            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
                patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
            ):
                from tools.send_telegram import send_message

                send_message("Album caption", file_paths=tmp_files)

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert payload["text"] == "Album caption"
            assert len(payload["file_paths"]) == 3
        finally:
            for f in tmp_files:
                os.unlink(f)

    def test_too_many_files_exits(self):
        """Should exit with error when more than 10 files specified."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        tmp_files = []
        try:
            for i in range(11):
                f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                f.write(b"fake data")
                f.close()
                tmp_files.append(f.name)

            with patch.dict(os.environ, env, clear=True):
                from tools.send_telegram import send_message

                with pytest.raises(SystemExit) as exc_info:
                    send_message("caption", file_paths=tmp_files)
                assert exc_info.value.code == 1
        finally:
            for f in tmp_files:
                os.unlink(f)

    def test_partial_missing_files_exits(self):
        """Should exit with error listing missing files when some don't exist."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake data")

        try:
            with patch.dict(os.environ, env, clear=True):
                from tools.send_telegram import send_message

                with pytest.raises(SystemExit) as exc_info:
                    send_message("caption", file_paths=[tmp_path, "/nonexistent/file.png"])
                assert exc_info.value.code == 1
        finally:
            os.unlink(tmp_path)


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
            assert exc_info.value.code == 2  # argparse exits with code 2

    def test_main_with_file_flag(self):
        """Should parse --file flag and pass to send_message as list."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
            f.write(b"fake image")

        try:
            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
                patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
                patch("sys.argv", ["send_telegram.py", "Caption text", "--file", tmp_path]),
            ):
                from tools.send_telegram import main

                main()

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert payload["text"] == "Caption text"
            assert payload["file_paths"] == [tmp_path]
        finally:
            os.unlink(tmp_path)

    def test_main_with_multiple_file_flags(self):
        """Should parse multiple --file flags into a list."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        tmp_files = []

        try:
            for suffix in [".png", ".jpg"]:
                f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                f.write(b"fake data")
                f.close()
                tmp_files.append(f.name)

            argv = ["send_telegram.py", "Album", "--file", tmp_files[0], "--file", tmp_files[1]]
            with (
                patch.dict(os.environ, env, clear=True),
                patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
                patch("tools.send_telegram._linkify_text", side_effect=lambda t: t),
                patch("sys.argv", argv),
            ):
                from tools.send_telegram import main

                main()

            payload = json.loads(mock_redis.rpush.call_args[0][1])
            assert payload["text"] == "Album"
            assert len(payload["file_paths"]) == 2
        finally:
            for f in tmp_files:
                os.unlink(f)

    def test_main_rejects_unknown_flags(self):
        """Should reject unknown flags like --photo or --project."""
        with patch("sys.argv", ["send_telegram.py", "--photo", "foo", "test"]):
            from tools.send_telegram import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2  # argparse exits with code 2


class TestSendTelegramReaction:
    """Test --react flag for emoji reactions."""

    def test_react_queues_reaction_payload(self):
        """Should queue a reaction payload with type='reaction' and resolved emoji."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(emoji="\U0001f525")

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
        ):
            from tools.send_telegram import send_reaction

            send_reaction("excited")

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == "\U0001f525"
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 67890
        assert payload["session_id"] == "test-session"
        assert "custom_emoji_document_id" not in payload

    def test_react_queues_custom_emoji_reaction(self):
        """Should include custom_emoji_document_id when result is custom."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(
            emoji="\U0001f525",
            document_id=99999,
            is_custom=True,
        )

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
        ):
            from tools.send_telegram import send_reaction

            send_reaction("excited")

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["custom_emoji_document_id"] == 99999

    def test_react_requires_reply_to(self):
        """Should exit with error when TELEGRAM_REPLY_TO is not set."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_reaction

            with pytest.raises(SystemExit) as exc_info:
                send_reaction("happy")
            assert exc_info.value.code == 1

    def test_react_empty_feeling_exits(self):
        """Should exit with error when feeling is empty."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_reaction

            with pytest.raises(SystemExit) as exc_info:
                send_reaction("")
            assert exc_info.value.code == 1

    def test_react_cli_flag(self):
        """Should parse --react flag from CLI and call send_reaction."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(emoji="\U0001f44d")

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
            patch("sys.argv", ["send_telegram.py", "--react", "happy"]),
        ):
            from tools.send_telegram import main

            main()

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == "\U0001f44d"


class TestSendTelegramEmoji:
    """Test --emoji flag for standalone custom emoji messages."""

    def test_emoji_queues_custom_emoji_message(self):
        """Should queue a custom_emoji_message payload."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "TELEGRAM_REPLY_TO": "67890",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(
            emoji="\U0001f389",
            document_id=42,
            is_custom=True,
        )

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
        ):
            from tools.send_telegram import send_emoji

            send_emoji("celebration")

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f389"
        assert payload["custom_emoji_document_id"] == 42
        assert payload["chat_id"] == "12345"
        assert payload["session_id"] == "test-session"

    def test_emoji_standard_fallback(self):
        """Should queue standard emoji when no custom match."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(emoji="\U0001f525")

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
        ):
            from tools.send_telegram import send_emoji

            send_emoji("fire")

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f525"
        assert "custom_emoji_document_id" not in payload

    def test_emoji_empty_feeling_exits(self):
        """Should exit with error when feeling is empty."""
        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_emoji

            with pytest.raises(SystemExit) as exc_info:
                send_emoji("")
            assert exc_info.value.code == 1

    def test_emoji_missing_chat_id_exits(self):
        """Should exit with error when TELEGRAM_CHAT_ID not set."""
        env = {"VALOR_SESSION_ID": "test-session"}
        with patch.dict(os.environ, env, clear=True):
            from tools.send_telegram import send_emoji

            with pytest.raises(SystemExit) as exc_info:
                send_emoji("happy")
            assert exc_info.value.code == 1

    def test_emoji_cli_flag(self):
        """Should parse --emoji flag from CLI."""
        from tools.emoji_embedding import EmojiResult

        env = {
            "TELEGRAM_CHAT_ID": "12345",
            "VALOR_SESSION_ID": "test-session",
        }

        mock_redis = MagicMock()
        mock_result = EmojiResult(emoji="\U0001f525")

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.send_telegram._get_redis_connection", return_value=mock_redis),
            patch("tools.emoji_embedding.find_best_emoji", return_value=mock_result),
            patch("sys.argv", ["send_telegram.py", "--emoji", "excited"]),
        ):
            from tools.send_telegram import main

            main()

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f525"
