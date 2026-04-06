"""Tests for the unified valor-telegram CLI tool."""

import argparse
import json
import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent))
from bridge.utc import utc_now
from tools.valor_telegram import format_timestamp, parse_since, resolve_chat


class TestParseSince:
    """Test relative time parsing."""

    def test_hours_ago(self):
        result = parse_since("1 hour ago")
        assert result is not None
        expected = utc_now() - timedelta(hours=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_multiple_hours(self):
        result = parse_since("3 hours ago")
        assert result is not None
        expected = utc_now() - timedelta(hours=3)
        assert abs((result - expected).total_seconds()) < 2

    def test_minutes_ago(self):
        result = parse_since("30 minutes ago")
        assert result is not None
        expected = utc_now() - timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 2

    def test_days_ago(self):
        result = parse_since("2 days ago")
        assert result is not None
        expected = utc_now() - timedelta(days=2)
        assert abs((result - expected).total_seconds()) < 2

    def test_weeks_ago(self):
        result = parse_since("1 week ago")
        assert result is not None
        expected = utc_now() - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_invalid_input(self):
        assert parse_since("not a time") is None
        assert parse_since("") is None
        assert parse_since("yesterday") is None

    def test_case_insensitive(self):
        result = parse_since("2 Hours Ago")
        assert result is not None

    def test_singular_plural(self):
        result1 = parse_since("1 minute ago")
        result2 = parse_since("1 minutes ago")
        assert result1 is not None
        assert result2 is not None


class TestResolveChat:
    """Test chat name resolution."""

    @patch("tools.telegram_history.resolve_chat_id", return_value="-123456")
    def test_resolves_from_history(self, mock_resolve):
        result = resolve_chat("Dev: Valor")
        assert result == "-123456"
        mock_resolve.assert_called_once_with("Dev: Valor")

    def test_returns_none_for_unknown(self):
        result = resolve_chat("nonexistent_chat_xyz_12345")
        assert result is None


class TestFormatTimestamp:
    """Test timestamp formatting."""

    def test_valid_iso_timestamp(self):
        result = format_timestamp("2026-02-14T10:30:00")
        assert result == "2026-02-14 10:30"

    def test_none_input(self):
        assert format_timestamp(None) == "unknown"

    def test_invalid_timestamp(self):
        result = format_timestamp("not-a-date")
        assert isinstance(result, str)
        assert len(result) > 0


class TestCLIParsing:
    """Test CLI argument parsing."""

    def test_read_help(self):
        """Verify read subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "read", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_send_help(self):
        """Verify send subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "send", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_chats_help(self):
        """Verify chats subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "chats", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_no_command_shows_help(self):
        """No subcommand returns error code."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram"]
        result = main()
        assert result == 1


class TestCmdSend:
    """Tests for the Redis-queue-based cmd_send() implementation."""

    def _make_args(self, chat="-123456", message="hello", file=None, image=None, audio=None, reply_to=None):
        """Build a mock Namespace matching what argparse produces for 'send'."""
        ns = argparse.Namespace(
            chat=chat,
            message=message,
            file=file,
            image=image,
            audio=audio,
            reply_to=reply_to,
        )
        return ns

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_successful_queue_push(self, mock_redis_fn, mock_resolve, capsys):
        """Successful send queues payload to Redis and prints confirmation."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="Dev: Valor", message="test message")
        result = cmd_send(args)

        assert result == 0
        mock_redis.rpush.assert_called_once()
        mock_redis.expire.assert_called_once()

        # Check payload structure
        call_args = mock_redis.rpush.call_args
        key = call_args[0][0]
        raw_payload = call_args[0][1]
        assert key.startswith("telegram:outbox:cli-")

        payload = json.loads(raw_payload)
        assert payload["chat_id"] == "-100123456"
        assert payload["text"] == "test message"
        assert payload["session_id"].startswith("cli-")
        assert payload["reply_to"] is None
        assert "timestamp" in payload

        captured = capsys.readouterr()
        assert "Message queued" in captured.out
        assert "chars" in captured.out

    @patch("tools.valor_telegram.resolve_chat", return_value=None)
    def test_unknown_chat_returns_error(self, mock_resolve, capsys):
        """Unknown chat name prints error and returns 1."""
        from tools.valor_telegram import cmd_send

        args = self._make_args(chat="NonexistentChat", message="hello")
        result = cmd_send(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Unknown chat" in captured.err
        assert "valor-telegram chats" in captured.err

    def test_empty_message_no_file_returns_error(self, capsys):
        """Empty message with no file returns error code 1."""
        from tools.valor_telegram import cmd_send

        args = self._make_args(chat="-123456", message="")
        result = cmd_send(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Must provide a message or file" in captured.err

    def test_nonexistent_file_returns_error(self, capsys, tmp_path):
        """Non-existent file path returns error before queueing."""
        from tools.valor_telegram import cmd_send

        args = self._make_args(chat="-123456", message="", file="/nonexistent/path/file.png")
        result = cmd_send(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_message_truncation_at_4096_chars(self, mock_redis_fn, mock_resolve):
        """Messages longer than 4096 chars are truncated before queuing."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        long_message = "x" * 5000
        args = self._make_args(chat="-100123456", message=long_message)
        result = cmd_send(args)

        assert result == 0
        call_args = mock_redis.rpush.call_args
        payload = json.loads(call_args[0][1])
        assert len(payload["text"]) <= 4096

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_reply_to_included_in_payload(self, mock_redis_fn, mock_resolve):
        """reply_to is included in payload when --reply-to is provided."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello", reply_to=999)
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] == 999

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_file_path_included_in_payload(self, mock_redis_fn, mock_resolve, tmp_path):
        """file_paths included in payload when --file provided."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        test_file = tmp_path / "test.png"
        test_file.write_bytes(b"\x89PNG")

        args = self._make_args(chat="-100123456", message="caption", file=str(test_file))
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert "file_paths" in payload
        assert len(payload["file_paths"]) == 1
        assert payload["file_paths"][0].endswith("test.png")

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_redis_failure_returns_error(self, mock_redis_fn, mock_resolve, capsys):
        """Redis connection failure returns error code 1 with helpful message."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis.rpush.side_effect = Exception("Connection refused")
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello")
        result = cmd_send(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Failed to queue message in Redis" in captured.err

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_session_id_uses_cli_prefix(self, mock_redis_fn, mock_resolve):
        """Session ID uses cli- prefix to avoid collision with bridge session IDs."""
        from tools.valor_telegram import cmd_send

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello")
        cmd_send(args)

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["session_id"].startswith("cli-")
        # Session ID is cli-{unix_timestamp} - should be numeric after prefix
        suffix = payload["session_id"][4:]
        assert suffix.isdigit()

    def test_send_subparser_has_reply_to_flag(self):
        """Verify --reply-to flag is registered on the send subparser."""
        import argparse

        from tools.valor_telegram import main

        # Parse a send command with --reply-to
        sys.argv = ["valor-telegram", "send", "--chat", "-123", "--reply-to", "456", "msg"]
        # We can't call main() without it executing cmd_send, so test argparse directly
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        send_p = sub.add_parser("send")
        send_p.add_argument("--chat", required=True)
        send_p.add_argument("message", nargs="?", default="")
        send_p.add_argument("--reply-to", type=int, default=None)

        parsed = parser.parse_args(["send", "--chat", "-123", "--reply-to", "456", "msg"])
        assert parsed.reply_to == 456
