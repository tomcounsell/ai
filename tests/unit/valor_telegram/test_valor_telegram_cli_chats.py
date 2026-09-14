"""Tests for tools.valor_telegram.cmd_chats: --search and --project filters.

Split out of the former ``tests/unit/test_valor_telegram.py`` monolith (#2879).
"""

import argparse
import json
from unittest.mock import patch


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
