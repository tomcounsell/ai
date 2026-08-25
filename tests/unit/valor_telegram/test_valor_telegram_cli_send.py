"""Tests for tools.valor_telegram.cmd_send: the Redis-queue-based send path.

Split out of the former ``tests/unit/test_valor_telegram.py`` monolith (#2879).
"""

import argparse
import json
import sys
from unittest.mock import MagicMock, patch


class TestCmdSend:
    """Tests for the Redis-queue-based cmd_send() implementation."""

    def _make_args(
        self,
        chat="-123456",
        message="hello",
        file=None,
        image=None,
        audio=None,
        reply_to=None,
        session_id=None,
        ack_sent_id=False,
    ):
        """Build a mock Namespace matching what argparse produces for 'send'."""
        ns = argparse.Namespace(
            chat=chat,
            message=message,
            file=file,
            image=image,
            audio=audio,
            reply_to=reply_to,
            session_id=session_id,
            ack_sent_id=ack_sent_id,
        )
        return ns

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_successful_queue_push(self, mock_redis_fn, mock_resolve, capsys, monkeypatch):
        """Successful send queues payload to Redis and prints confirmation."""
        from tools.valor_telegram import cmd_send

        # Env-isolation hygiene: ensure CI/dev env doesn't leak TELEGRAM_REPLY_TO
        # into this test (issue #1191 added env-var fallback in cmd_send).
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

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

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_session_id_overrides_synthetic_default(self, mock_redis_fn, mock_resolve, monkeypatch):
        """--session-id (issue #2717) overrides the synthetic cli-<epoch> id
        used for both the outbox key and the payload's session_id."""
        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(
            chat="Dev: Valor", message="picking up an issue", session_id="upvote-valor-42-1000"
        )
        result = cmd_send(args)

        assert result == 0
        call_args = mock_redis.rpush.call_args
        key = call_args[0][0]
        payload = json.loads(call_args[0][1])
        assert key == "telegram:outbox:upvote-valor-42-1000"
        assert payload["session_id"] == "upvote-valor-42-1000"
        assert "ack_sent_id" not in payload

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_ack_sent_id_flag_sets_payload_flag(self, mock_redis_fn, mock_resolve, monkeypatch):
        """--ack-sent-id (issue #2717) sets ack_sent_id=True in the payload."""
        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(
            chat="Dev: Valor",
            message="picking up an issue",
            session_id="upvote-valor-42-1000",
            ack_sent_id=True,
        )
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["ack_sent_id"] is True

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
    def test_reply_to_included_in_payload(self, mock_redis_fn, mock_resolve, monkeypatch):
        """reply_to is included in payload when --reply-to is provided."""
        from tools.valor_telegram import cmd_send

        # Env-isolation hygiene: ensure CI/dev env doesn't leak TELEGRAM_REPLY_TO
        # into this test (issue #1191 added env-var fallback in cmd_send).
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello", reply_to=999)
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] == 999

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_env_var_used_as_default_reply_to(self, mock_redis_fn, mock_resolve, monkeypatch):
        """When invoked from inside an AgentSession, cmd_send defaults reply_to
        to the TELEGRAM_REPLY_TO env var (set by agent/sdk_client.py from
        session.telegram_message_id). Issue #1191."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("TELEGRAM_REPLY_TO", "12345")

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello")
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] == 12345

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_explicit_reply_to_overrides_env_var(self, mock_redis_fn, mock_resolve, monkeypatch):
        """Explicit --reply-to wins over TELEGRAM_REPLY_TO env var (issue #1191)."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("TELEGRAM_REPLY_TO", "12345")

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello", reply_to=999)
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] == 999

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_invalid_env_var_falls_back_to_none(self, mock_redis_fn, mock_resolve, monkeypatch):
        """Invalid TELEGRAM_REPLY_TO (non-numeric) silently falls back to None
        rather than crashing (issue #1191)."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("TELEGRAM_REPLY_TO", "not-a-number")

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello")
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] is None

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_empty_env_var_falls_back_to_none(self, mock_redis_fn, mock_resolve, monkeypatch):
        """Empty TELEGRAM_REPLY_TO ('') is treated as not set (issue #1191)."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("TELEGRAM_REPLY_TO", "")

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(chat="-100123456", message="hello")
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["reply_to"] is None

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
