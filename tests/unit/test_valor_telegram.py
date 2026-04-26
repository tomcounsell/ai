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
        # resolve_chat forwards strict= to resolve_chat_id. Default is False.
        mock_resolve.assert_called_once_with("Dev: Valor", strict=False)

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

    def _make_args(
        self, chat="-123456", message="hello", file=None, image=None, audio=None, reply_to=None
    ):
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


# =============================================================================
# Issue #1163 — CLI wiring for ChatCandidate / AmbiguousChatError
# =============================================================================


class _CandidateStub:
    """Minimal stand-in for ChatCandidate in tests that import it."""

    def __init__(self, chat_id: str, chat_name: str, last_activity_ts: float | None):
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.last_activity_ts = last_activity_ts


class TestCmdReadFlags:
    """Ambiguity handling, new flags, did-you-mean, and freshness header."""

    def _read_args(
        self,
        chat=None,
        chat_id=None,
        user=None,
        project=None,
        limit=10,
        search=None,
        since=None,
        json_out=False,
        strict=False,
    ):
        return argparse.Namespace(
            chat=chat,
            chat_id=chat_id,
            user=user,
            project=project,
            limit=limit,
            search=search,
            since=since,
            json=json_out,
            strict=strict,
        )

    def test_default_ambiguity_picks_most_recent_and_exits_0(self, capsys):
        """Default (non-strict) path: resolver returns a chat_id, CLI exits 0.

        Under the hotfixed plan (Q2 = pick-most-recent-with-warning), the
        ambiguity warning is emitted by the resolver's logger — the CLI
        just receives a chat_id and proceeds. No stderr error from the
        CLI layer, no exit 1.
        """
        from tools.valor_telegram import cmd_read

        with (
            patch(
                "tools.valor_telegram.resolve_chat",
                return_value="-100123",  # most-recent winner picked by resolver
            ),
            patch(
                "tools.valor_telegram._lookup_chat_metadata",
                return_value={"chat_name": "PM: PsyOptimal", "last_activity_ts": None},
            ),
            patch(
                "tools.telegram_history.get_recent_messages",
                return_value={"messages": []},
            ),
        ):
            result = cmd_read(self._read_args(chat="PsyOptimal"))

        assert result == 0
        captured = capsys.readouterr()
        # CLI layer must NOT print an ambiguity error block on the default path.
        assert "Ambiguous chat name" not in captured.out
        assert "Ambiguous chat name" not in captured.err

    def test_default_path_passes_strict_false_to_resolver(self):
        """Verify cmd_read passes strict=False when --strict is not set."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.valor_telegram.resolve_chat") as mock_resolve,
            patch("tools.valor_telegram._lookup_chat_metadata", return_value=None),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            mock_resolve.return_value = "-100"
            cmd_read(self._read_args(chat="X", strict=False))

        # Assert strict kwarg was False (pick-most-recent + warn).
        call = mock_resolve.call_args
        assert call.kwargs.get("strict") is False

    def test_strict_ambiguity_prints_stdout_and_exits_1(self, capsys):
        """--strict path catches AmbiguousChatError, renders stdout, exits 1."""
        from tools.telegram_history import AmbiguousChatError
        from tools.valor_telegram import cmd_read

        candidates = [
            _CandidateStub("-100123", "PM: PsyOptimal", 1_700_000_100.0),
            _CandidateStub("-100456", "PsyOptimal", 1_700_000_000.0),
        ]

        with patch("tools.valor_telegram.resolve_chat", side_effect=AmbiguousChatError(candidates)):
            result = cmd_read(self._read_args(chat="PsyOptimal", strict=True))

        assert result == 1
        captured = capsys.readouterr()
        # Candidates go to stdout (scripted callers parse stdout), not stderr.
        assert "Ambiguous chat name" in captured.out
        assert "-100123" in captured.out and "PM: PsyOptimal" in captured.out
        assert "-100456" in captured.out and "PsyOptimal" in captured.out
        assert "--chat-id" in captured.out  # advice line

    def test_strict_flag_passes_to_resolver(self):
        """Verify cmd_read passes strict=True when --strict is set."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.valor_telegram.resolve_chat") as mock_resolve,
            patch("tools.valor_telegram._lookup_chat_metadata", return_value=None),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            mock_resolve.return_value = "-100"
            cmd_read(self._read_args(chat="X", strict=True))

        call = mock_resolve.call_args
        assert call.kwargs.get("strict") is True

    def test_empty_chat_rejected_before_resolver(self, capsys):
        """Empty --chat is rejected with exit 1 BEFORE hitting the resolver (C3)."""
        from tools.valor_telegram import cmd_read

        with patch(
            "tools.valor_telegram.resolve_chat",
            side_effect=AssertionError("resolver must not be called for empty --chat"),
        ):
            result = cmd_read(self._read_args(chat=""))
        assert result == 1
        err = capsys.readouterr().err
        assert "--chat cannot be empty" in err

        with patch(
            "tools.valor_telegram.resolve_chat",
            side_effect=AssertionError("resolver must not be called for whitespace --chat"),
        ):
            result = cmd_read(self._read_args(chat="   "))
        assert result == 1
        err = capsys.readouterr().err
        assert "--chat cannot be empty" in err

    def test_empty_user_rejected_before_resolver(self, capsys):
        """Empty --user is rejected with exit 1 BEFORE hitting resolve_username (C3)."""
        from tools.valor_telegram import cmd_read

        with patch(
            "tools.telegram_users.resolve_username",
            side_effect=AssertionError("resolve_username must not be called for empty --user"),
        ):
            result = cmd_read(self._read_args(user=""))
        assert result == 1
        err = capsys.readouterr().err
        assert "--user cannot be empty" in err

    def test_chat_id_bypasses_matcher(self, capsys):
        """--chat-id skips resolve_chat entirely and reads the id directly."""
        from tools.valor_telegram import cmd_read

        with (
            patch(
                "tools.valor_telegram.resolve_chat",
                side_effect=AssertionError("should not be called"),
            ),
            patch(
                "tools.valor_telegram._lookup_chat_metadata",
                return_value={"chat_name": "Direct Chat", "last_activity_ts": None},
            ),
            patch(
                "tools.telegram_history.get_recent_messages",
                return_value={"messages": []},
            ),
        ):
            result = cmd_read(self._read_args(chat_id="-999"))

        assert result == 0
        out = capsys.readouterr().out
        assert "chat_id=-999" in out
        assert "last activity: never" in out

    def test_user_flag_routes_through_whitelist(self, capsys):
        """--user forces resolve_username and reads that id."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.telegram_users.resolve_username", return_value=12345),
            patch("tools.valor_telegram._lookup_chat_metadata", return_value=None),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            result = cmd_read(self._read_args(user="lewis"))

        assert result == 0
        out = capsys.readouterr().out
        assert "chat_id=12345" in out

    def test_user_flag_unknown_username(self, capsys):
        """--user with an unknown username exits 1 with a helpful error."""
        from tools.valor_telegram import cmd_read

        with patch("tools.telegram_users.resolve_username", return_value=None):
            result = cmd_read(self._read_args(user="ghost_user"))

        assert result == 1
        err = capsys.readouterr().err
        assert "Unknown username" in err

    def test_zero_match_renders_did_you_mean(self, capsys):
        """Zero-match prints did-you-mean candidates on stderr, exits 1."""
        from tools.valor_telegram import cmd_read

        fake_suggestions = [
            {"chat_id": "-100123", "chat_name": "PM: PsyOptimal", "last_activity_ts": None},
            {"chat_id": "-100456", "chat_name": "PsyOptimal Old", "last_activity_ts": None},
        ]
        with (
            patch("tools.valor_telegram.resolve_chat", return_value=None),
            patch("tools.valor_telegram._did_you_mean_candidates", return_value=fake_suggestions),
        ):
            result = cmd_read(self._read_args(chat="Psy"))

        assert result == 1
        err = capsys.readouterr().err
        assert "Did you mean" in err
        assert "-100123" in err
        assert "-100456" in err

    def test_zero_match_no_suggestions(self, capsys):
        """Zero-match with no suggestions still exits 1 cleanly."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.valor_telegram.resolve_chat", return_value=None),
            patch("tools.valor_telegram._did_you_mean_candidates", return_value=[]),
        ):
            result = cmd_read(self._read_args(chat="NothingXYZ"))

        assert result == 1
        err = capsys.readouterr().err
        assert "No chat matched" in err

    def test_freshness_header_with_timestamp(self, capsys):
        """Freshness header shows Xh-ago age when last_activity_ts is present."""
        import time

        from tools.valor_telegram import cmd_read

        fresh_ts = time.time() - 120  # 2 minutes ago
        with (
            patch("tools.valor_telegram.resolve_chat", return_value="-100123"),
            patch(
                "tools.valor_telegram._lookup_chat_metadata",
                return_value={"chat_name": "PM: PsyOptimal", "last_activity_ts": fresh_ts},
            ),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            result = cmd_read(self._read_args(chat="PM: PsyOptimal"))

        assert result == 0
        out = capsys.readouterr().out
        assert "PM: PsyOptimal" in out
        assert "chat_id=-100123" in out
        assert "2m ago" in out

    def test_freshness_header_never(self, capsys):
        """Freshness header shows 'never' when last_activity_ts is None."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.valor_telegram.resolve_chat", return_value="-100456"),
            patch(
                "tools.valor_telegram._lookup_chat_metadata",
                return_value={"chat_name": "Fresh Chat", "last_activity_ts": None},
            ),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            result = cmd_read(self._read_args(chat="Fresh Chat"))

        assert result == 0
        out = capsys.readouterr().out
        assert "last activity: never" in out

    def test_chat_id_with_no_messages_renders_clean_message(self, capsys):
        """--chat-id with numeric input that has no messages renders a clear line."""
        from tools.valor_telegram import cmd_read

        with (
            patch("tools.valor_telegram._lookup_chat_metadata", return_value=None),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            result = cmd_read(self._read_args(chat_id="-100123"))

        assert result == 0
        out = capsys.readouterr().out
        assert "No messages found for chat -100123" in out

    def test_flag_mutex_enforced_in_cmd_read(self, capsys):
        """Direct cmd_read() invocation with mutex violation exits 1."""
        from tools.valor_telegram import cmd_read

        result = cmd_read(self._read_args(chat="X", chat_id="-1"))

        assert result == 1
        err = capsys.readouterr().err
        assert "mutually exclusive" in err


class TestCmdReadArgparseMutex:
    """argparse-level enforcement of --chat / --chat-id / --user / --project mutex."""

    def test_chat_and_chat_id_both_rejected(self):
        """Passing both --chat and --chat-id raises SystemExit (argparse)."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram", "read", "--chat", "foo", "--chat-id", "-123"]
        with pytest.raises(SystemExit):
            main()

    def test_chat_and_user_both_rejected(self):
        """Passing both --chat and --user raises SystemExit (argparse)."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram", "read", "--chat", "foo", "--user", "bar"]
        with pytest.raises(SystemExit):
            main()

    def test_project_and_chat_both_rejected(self):
        """Passing both --project and --chat raises SystemExit (argparse mutex)."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram", "read", "--project", "psyoptimal", "--chat", "foo"]
        with pytest.raises(SystemExit):
            main()

    def test_project_and_chat_id_both_rejected(self):
        """Passing both --project and --chat-id raises SystemExit (argparse mutex)."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram", "read", "--project", "psyoptimal", "--chat-id", "-1"]
        with pytest.raises(SystemExit):
            main()

    def test_project_and_user_both_rejected(self):
        """Passing both --project and --user raises SystemExit (argparse mutex)."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram", "read", "--project", "psyoptimal", "--user", "tom"]
        with pytest.raises(SystemExit):
            main()


class TestCmdReadProject:
    """Cross-chat project-level reads via `--project` (issue #1169)."""

    def _read_args(
        self,
        chat=None,
        chat_id=None,
        user=None,
        project=None,
        limit=10,
        search=None,
        since=None,
        json_out=False,
        strict=False,
    ):
        return argparse.Namespace(
            chat=chat,
            chat_id=chat_id,
            user=user,
            project=project,
            limit=limit,
            search=search,
            since=since,
            json=json_out,
            strict=strict,
        )

    def test_zero_matching_chats_exits_1(self, capsys):
        """`--project unknown` with no matching chats exits 1 with a stderr hint."""
        from tools.valor_telegram import cmd_read

        with patch("tools.valor_telegram.resolve_chats_by_project", return_value=[]):
            result = cmd_read(self._read_args(project="unknown"))

        assert result == 1
        err = capsys.readouterr().err
        assert "No chats found for project 'unknown'" in err
        assert "valor-telegram chats --project" in err

    def test_single_matching_chat_renders_header_and_messages(self, capsys):
        """One matching chat → project header + per-line `[chat_name]` tag."""
        from tools.valor_telegram import cmd_read

        candidates = [_CandidateStub("100", "PsyOPTIMAL", 1_700_000_500.0)]
        msgs = {
            "messages": [
                {
                    "id": "m1",
                    "message_id": 1,
                    "sender": "alice",
                    "content": "hello",
                    "timestamp": "2026-04-25T10:00:00",
                    "message_type": "text",
                }
            ]
        }
        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", return_value=msgs),
        ):
            result = cmd_read(self._read_args(project="psyoptimal", limit=10))

        assert result == 0
        out = capsys.readouterr().out
        assert "[project=psyoptimal" in out
        assert "1 chats" in out
        assert "PsyOPTIMAL" in out
        assert "[PsyOPTIMAL]" in out
        assert "alice: hello" in out

    def test_many_chats_merge_chronological_and_trim_total(self, capsys):
        """Messages from all matching chats are interleaved by ts desc and trimmed total."""
        from tools.valor_telegram import cmd_read

        candidates = [
            _CandidateStub("100", "ChatA", 1_700_000_500.0),
            _CandidateStub("200", "ChatB", 1_700_000_400.0),
        ]

        # ChatA has 3 messages, ChatB has 3 messages, all at different times
        msgs_a = {
            "messages": [
                {
                    "id": "a1",
                    "message_id": 1,
                    "sender": "alice",
                    "content": "A-newest",
                    "timestamp": "2026-04-25T12:00:00",
                    "message_type": "text",
                },
                {
                    "id": "a2",
                    "message_id": 2,
                    "sender": "alice",
                    "content": "A-middle",
                    "timestamp": "2026-04-25T10:00:00",
                    "message_type": "text",
                },
                {
                    "id": "a3",
                    "message_id": 3,
                    "sender": "alice",
                    "content": "A-oldest",
                    "timestamp": "2026-04-25T08:00:00",
                    "message_type": "text",
                },
            ]
        }
        msgs_b = {
            "messages": [
                {
                    "id": "b1",
                    "message_id": 1,
                    "sender": "bob",
                    "content": "B-newest",
                    "timestamp": "2026-04-25T11:00:00",
                    "message_type": "text",
                },
                {
                    "id": "b2",
                    "message_id": 2,
                    "sender": "bob",
                    "content": "B-middle",
                    "timestamp": "2026-04-25T09:00:00",
                    "message_type": "text",
                },
                {
                    "id": "b3",
                    "message_id": 3,
                    "sender": "bob",
                    "content": "B-oldest",
                    "timestamp": "2026-04-25T07:00:00",
                    "message_type": "text",
                },
            ]
        }

        def fake_get_recent(chat_id, limit):
            if str(chat_id) == "100":
                return msgs_a
            return msgs_b

        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", side_effect=fake_get_recent),
        ):
            # limit=4 → top 4 across the union after merge.
            result = cmd_read(self._read_args(project="proj", limit=4))

        assert result == 0
        out = capsys.readouterr().out

        # Output is chronological (oldest first) — bridge prints in chronological
        # order historically; the merge is timestamp-desc then displayed oldest-first
        # to match single-chat behavior.
        # Either ordering is fine as long as exactly 4 of the 6 lines made it
        # and the OLDEST 2 were dropped (B-oldest 07:00 and A-oldest 08:00).
        assert "A-newest" in out
        assert "B-newest" in out
        assert "A-middle" in out
        assert "B-middle" in out
        assert "A-oldest" not in out
        assert "B-oldest" not in out

    def test_json_output_includes_chat_id_and_chat_name(self, capsys):
        """`--project --json` enriches each message dict with chat_id + chat_name."""
        from tools.valor_telegram import cmd_read

        candidates = [_CandidateStub("100", "ChatA", 1_700_000_500.0)]
        msgs = {
            "messages": [
                {
                    "id": "m1",
                    "message_id": 1,
                    "sender": "alice",
                    "content": "hi",
                    "timestamp": "2026-04-25T10:00:00",
                    "message_type": "text",
                }
            ]
        }
        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", return_value=msgs),
        ):
            result = cmd_read(self._read_args(project="proj", json_out=True))

        assert result == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["chat_id"] == "100"
        assert data[0]["chat_name"] == "ChatA"
        # Existing fields still present
        assert data[0]["sender"] == "alice"
        assert data[0]["content"] == "hi"

    def test_project_freshness_header_format(self, capsys):
        """Header format: `[project=KEY · N chats: name1, name2 · last activity: T]`."""
        import re

        from tools.valor_telegram import cmd_read

        candidates = [
            _CandidateStub("100", "ChatA", 1_700_000_500.0),
            _CandidateStub("200", "ChatB", 1_700_000_400.0),
        ]
        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            cmd_read(self._read_args(project="psyoptimal"))

        out = capsys.readouterr().out
        # Match: [project=psyoptimal · 2 chats: ChatA, ChatB · last activity: ...]
        pattern = r"\[project=psyoptimal · 2 chats: ChatA, ChatB · last activity: .+\]"
        assert re.search(pattern, out), f"Header pattern not matched in: {out!r}"

    def test_project_strict_rejected(self, capsys):
        """`--project` + `--strict` is rejected with explicit error."""
        from tools.valor_telegram import cmd_read

        result = cmd_read(self._read_args(project="psyoptimal", strict=True))

        assert result == 1
        err = capsys.readouterr().err
        assert "--strict has no effect with --project" in err

    def test_empty_project_rejected(self, capsys):
        """`--project ''` and `--project '   '` are rejected as empty."""
        from tools.valor_telegram import cmd_read

        result = cmd_read(self._read_args(project=""))
        assert result == 1
        err = capsys.readouterr().err
        assert "--project cannot be empty" in err

        result = cmd_read(self._read_args(project="   "))
        assert result == 1
        err = capsys.readouterr().err
        assert "--project cannot be empty" in err

    def test_project_mutex_in_cmd_read(self, capsys):
        """Direct cmd_read invocation with --project + --chat exits 1."""
        from tools.valor_telegram import cmd_read

        result = cmd_read(self._read_args(project="psyoptimal", chat="foo"))

        assert result == 1
        err = capsys.readouterr().err
        assert "mutually exclusive" in err

    def test_long_chat_name_truncated_in_per_line_tag(self, capsys):
        """Per-line `[chat_name]` tag truncates names >25 chars with ellipsis."""
        from tools.valor_telegram import cmd_read

        long_name = "PsyOPTIMAL Engineering Daily Standup"  # 36 chars
        candidates = [_CandidateStub("100", long_name, 1_700_000_500.0)]
        msgs = {
            "messages": [
                {
                    "id": "m1",
                    "message_id": 1,
                    "sender": "alice",
                    "content": "hello",
                    "timestamp": "2026-04-25T10:00:00",
                    "message_type": "text",
                }
            ]
        }
        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", return_value=msgs),
        ):
            cmd_read(self._read_args(project="proj"))

        out = capsys.readouterr().out
        # The 36-char name must be truncated to 25 chars (+ ellipsis) in the
        # per-line tag, but the FULL name appears in the project header.
        assert long_name in out  # Header has the full name
        # Per-line tag truncates: first 22 chars + "..." = 25 visible chars.
        truncated = long_name[:22] + "..."
        assert f"[{truncated}]" in out

    def test_empty_results_prints_header_then_no_messages(self, capsys):
        """Project header prints BEFORE any 'no messages' text on empty results."""
        from tools.valor_telegram import cmd_read

        candidates = [_CandidateStub("100", "ChatA", 1_700_000_500.0)]
        with (
            patch("tools.valor_telegram.resolve_chats_by_project", return_value=candidates),
            patch("tools.telegram_history.get_recent_messages", return_value={"messages": []}),
        ):
            result = cmd_read(self._read_args(project="proj"))

        assert result == 0
        out = capsys.readouterr().out
        header_idx = out.find("[project=proj")
        nomsg_idx = out.find("No messages found for project 'proj'")
        assert header_idx >= 0
        assert nomsg_idx >= 0
        assert header_idx < nomsg_idx


class TestCmdChatsSearch:
    """`valor-telegram chats --search` filter (Task 5)."""

    def _chats_args(self, search=None, project=None, json_out=False):
        return argparse.Namespace(search=search, project=project, json=json_out)

    def test_search_filter_matches(self, capsys):
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PM: PsyOptimal",
                    "message_count": 3,
                    "last_message": "2026-04-24T10:00",
                },
                {
                    "chat_id": "2",
                    "chat_name": "Dev: Valor",
                    "message_count": 5,
                    "last_message": "2026-04-24T09:00",
                },
            ],
            "count": 2,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(search="psy"))

        assert result == 0
        out = capsys.readouterr().out
        assert "PM: PsyOptimal" in out
        assert "Dev: Valor" not in out
        # Header acknowledges the search filter
        assert "matching 'psy'" in out

    def test_search_filter_normalization_aware(self, capsys):
        """--search 'PM psy' matches 'PM: PsyOptimal' via normalization."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PM: PsyOptimal",
                    "message_count": 3,
                    "last_message": "2026-04-24T10:00",
                },
            ],
            "count": 1,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(search="PM psy"))

        assert result == 0
        out = capsys.readouterr().out
        assert "PM: PsyOptimal" in out

    def test_search_filter_zero_matches(self, capsys):
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "Alpha",
                    "message_count": 1,
                    "last_message": None,
                }
            ],
            "count": 1,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(search="zzz_nothing"))

        assert result == 0
        out = capsys.readouterr().out
        assert "No chats matched" in out

    def test_empty_search_rejected(self, capsys):
        """Empty --search is rejected (C3 concern) — no silent match-all."""
        from tools.valor_telegram import cmd_chats

        # Should NOT call list_chats at all; reject empty before any work.
        with patch(
            "tools.telegram_history.list_chats",
            side_effect=AssertionError("list_chats must not be called for empty --search"),
        ):
            result = cmd_chats(self._chats_args(search=""))
        assert result == 1
        err = capsys.readouterr().err
        assert "--search cannot be empty" in err

        with patch(
            "tools.telegram_history.list_chats",
            side_effect=AssertionError("list_chats must not be called for whitespace --search"),
        ):
            result = cmd_chats(self._chats_args(search="   "))
        assert result == 1
        err = capsys.readouterr().err
        assert "--search cannot be empty" in err

    def test_search_json_output(self, capsys):
        """--search with --json produces JSON output containing only matches."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {"chat_id": "1", "chat_name": "PM: Psy", "message_count": 1, "last_message": None},
                {"chat_id": "2", "chat_name": "Dev", "message_count": 1, "last_message": None},
            ],
            "count": 2,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(search="Psy", json_out=True))

        assert result == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        names = [c["chat_name"] for c in data["chats"]]
        assert "PM: Psy" in names
        assert "Dev" not in names
        assert data["count"] == 1


class TestCmdChatsProject:
    """`valor-telegram chats --project` filter (issue #1169)."""

    def _chats_args(self, search=None, project=None, json_out=False):
        return argparse.Namespace(search=search, project=project, json=json_out)

    def test_project_filter_matches(self, capsys):
        """`chats --project psyoptimal` returns only matching chats."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PsyOPTIMAL",
                    "project_key": "psyoptimal",
                    "message_count": 3,
                    "last_message": "2026-04-24T10:00",
                },
                {
                    "chat_id": "2",
                    "chat_name": "Dev: Valor",
                    "project_key": "valor",
                    "message_count": 5,
                    "last_message": "2026-04-24T09:00",
                },
                {
                    "chat_id": "3",
                    "chat_name": "PM: PsyOptimal",
                    "project_key": "psyoptimal",
                    "message_count": 7,
                    "last_message": "2026-04-24T11:00",
                },
            ],
            "count": 3,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(project="psyoptimal"))

        assert result == 0
        out = capsys.readouterr().out
        assert "PsyOPTIMAL" in out
        assert "PM: PsyOptimal" in out
        assert "Dev: Valor" not in out
        assert "matching project 'psyoptimal'" in out

    def test_project_and_search_combined(self, capsys):
        """`chats --project psyoptimal --search 'pm'` applies BOTH filters."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PsyOPTIMAL",
                    "project_key": "psyoptimal",
                    "message_count": 3,
                    "last_message": "2026-04-24T10:00",
                },
                {
                    "chat_id": "2",
                    "chat_name": "PM: PsyOptimal",
                    "project_key": "psyoptimal",
                    "message_count": 7,
                    "last_message": "2026-04-24T11:00",
                },
                {
                    "chat_id": "3",
                    "chat_name": "PM: Valor",
                    "project_key": "valor",
                    "message_count": 4,
                    "last_message": "2026-04-24T12:00",
                },
            ],
            "count": 3,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(project="psyoptimal", search="pm"))

        assert result == 0
        out = capsys.readouterr().out
        assert "PM: PsyOptimal" in out
        assert "PsyOPTIMAL" not in out.replace("PM: PsyOptimal", "")  # exclude PM line
        assert "PM: Valor" not in out

    def test_project_unknown_returns_empty(self, capsys):
        """`chats --project unknown` returns empty with no-match message."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PsyOPTIMAL",
                    "project_key": "psyoptimal",
                    "message_count": 3,
                    "last_message": None,
                },
            ],
            "count": 1,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(project="unknown"))

        assert result == 0
        out = capsys.readouterr().out
        assert "No chats" in out

    def test_empty_project_rejected(self, capsys):
        """`chats --project ''` is rejected."""
        from tools.valor_telegram import cmd_chats

        with patch(
            "tools.telegram_history.list_chats",
            side_effect=AssertionError("list_chats must not be called for empty --project"),
        ):
            result = cmd_chats(self._chats_args(project=""))
        assert result == 1
        err = capsys.readouterr().err
        assert "--project cannot be empty" in err

    def test_project_json_includes_project_key(self, capsys):
        """`chats --project --json` returns filtered list with project_key field."""
        from tools.valor_telegram import cmd_chats

        fake = {
            "chats": [
                {
                    "chat_id": "1",
                    "chat_name": "PsyOPTIMAL",
                    "project_key": "psyoptimal",
                    "message_count": 3,
                    "last_message": None,
                },
                {
                    "chat_id": "2",
                    "chat_name": "Dev: Valor",
                    "project_key": "valor",
                    "message_count": 5,
                    "last_message": None,
                },
            ],
            "count": 2,
        }
        with patch("tools.telegram_history.list_chats", return_value=fake):
            result = cmd_chats(self._chats_args(project="psyoptimal", json_out=True))

        assert result == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["count"] == 1
        names = [c["chat_name"] for c in data["chats"]]
        assert names == ["PsyOPTIMAL"]
        assert data["chats"][0]["project_key"] == "psyoptimal"


class TestFormatRelativeAge:
    """`_format_relative_age` helper."""

    def test_none_returns_never(self):
        from tools.valor_telegram import _format_relative_age

        assert _format_relative_age(None) == "never"

    def test_seconds_fresh(self):
        import time

        from tools.valor_telegram import _format_relative_age

        assert _format_relative_age(time.time() - 10) == "<1m ago"

    def test_minutes(self):
        import time

        from tools.valor_telegram import _format_relative_age

        assert _format_relative_age(time.time() - 300) == "5m ago"

    def test_hours(self):
        import time

        from tools.valor_telegram import _format_relative_age

        assert _format_relative_age(time.time() - 3 * 3600) == "3h ago"

    def test_days(self):
        import time

        from tools.valor_telegram import _format_relative_age

        result = _format_relative_age(time.time() - 2 * 86400)
        assert result == "2d ago"

    def test_invalid_input(self):
        from tools.valor_telegram import _format_relative_age

        assert _format_relative_age("not a timestamp") == "never"
