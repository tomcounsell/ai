"""Tests for tools.valor_telegram.cmd_read: ambiguity handling, flags,
argparse mutex enforcement, and cross-chat project reads.

Split out of the former ``tests/unit/test_valor_telegram.py`` monolith (#2879).
"""

import argparse
import json
import sys
from unittest.mock import patch

import pytest

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
