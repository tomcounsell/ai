"""Tests for reflections/utilities.py's Telegram-paging entry points (#3072).

Covers ``send_eng_telegram`` (project-dict callers) and
``resolve_host_eng_chat`` / ``send_host_eng_telegram`` (fleet-wide-digest
callers), plus mutation anti-test 1 (send by id, not name).

Anti-test 2 (the ``PROJECT_ROOT``-narrowed fallback) lives in Task 2's build
verification against ``tests/unit/test_docs_auditor_substrate.py`` — it only
bites once the ``docs_auditor`` delegation exists, and it must not edit that
test file. See the plan's Failure Path Test Strategy for both.

No local run can resolve a real chat_id (spike-4: every project in this
machine's projects.json carries an empty ``telegram`` block), so every case
here is a unit test against a synthetic project dict.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from reflections.utilities import (
    resolve_host_eng_chat,
    send_eng_telegram,
    send_host_eng_telegram,
)

pytestmark = [pytest.mark.unit]

VALOR_PROJECT = {
    "slug": "valor",
    "telegram": {"groups": {"Eng: Valor": {"chat_id": -1003449100931}}},
}


# ---------------------------------------------------------------------------
# send_eng_telegram — mirrors TestTelegramChatRouting's shape
# ---------------------------------------------------------------------------


class TestSendEngTelegram:
    def test_resolves_sends_by_id_not_name(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("reflections.utilities.subprocess.run", side_effect=fake_run):
            sent = send_eng_telegram(VALOR_PROJECT, "hi", logger_prefix="test")

        assert sent is True
        argv = calls[0]
        assert "-1003449100931" in argv
        assert "Eng: Valor" not in argv

    def test_no_telegram_key_suppresses_send(self, caplog):
        project = {"slug": "royop"}
        with (
            patch("reflections.utilities.subprocess.run") as run,
            caplog.at_level("WARNING"),
        ):
            sent = send_eng_telegram(project, "hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()
        assert "royop" in caplog.text

    def test_no_eng_group_suppresses_send(self):
        project = {"telegram": {"groups": {"Ops: X": {"chat_id": 1}}}}
        with patch("reflections.utilities.subprocess.run") as run:
            sent = send_eng_telegram(project, "hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()

    def test_malformed_chat_id_string_suppresses_send(self):
        project = {"telegram": {"groups": {"Eng: X": {"chat_id": "-100123"}}}}
        with patch("reflections.utilities.subprocess.run") as run:
            sent = send_eng_telegram(project, "hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()

    def test_bool_chat_id_suppresses_send(self):
        """resolve_eng_group rejects bool chat_id explicitly; pinned here too."""
        project = {"telegram": {"groups": {"Eng: X": {"chat_id": True}}}}
        with patch("reflections.utilities.subprocess.run") as run:
            sent = send_eng_telegram(project, "hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()

    def test_valor_telegram_absent_still_returns_true(self):
        with patch("reflections.utilities.subprocess.run", side_effect=FileNotFoundError):
            sent = send_eng_telegram(VALOR_PROJECT, "hi", logger_prefix="test")

        assert sent is True

    def test_timeout_still_returns_true(self):
        import subprocess as _subprocess

        with patch(
            "reflections.utilities.subprocess.run",
            side_effect=_subprocess.TimeoutExpired(cmd="valor-telegram", timeout=10),
        ):
            sent = send_eng_telegram(VALOR_PROJECT, "hi", logger_prefix="test")

        assert sent is True

    def test_nonzero_exit_logs_warning_and_still_returns_true(self, caplog):
        with (
            patch(
                "reflections.utilities.subprocess.run",
                return_value=MagicMock(returncode=1, stdout="", stderr="boom"),
            ),
            caplog.at_level("WARNING"),
        ):
            sent = send_eng_telegram(VALOR_PROJECT, "hi", logger_prefix="test")

        assert sent is True
        assert "valor-telegram exited 1" in caplog.text

    def test_resolve_eng_group_raising_suppresses_send_no_subprocess(self):
        """The row Solution §1's two-scope split exists to protect: a raising
        resolver must return False with no subprocess call, never True."""
        with (
            patch(
                "reflections.utilities.resolve_eng_group",
                side_effect=RuntimeError("boom"),
            ),
            patch("reflections.utilities.subprocess.run") as run,
        ):
            sent = send_eng_telegram(VALOR_PROJECT, "hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_host_eng_chat / send_host_eng_telegram
# ---------------------------------------------------------------------------


class TestResolveHostEngChat:
    def test_project_root_match_with_eng_group_returns_id_not_name(self, tmp_path):
        project = {**VALOR_PROJECT, "working_directory": str(tmp_path)}
        chat = resolve_host_eng_chat(
            tmp_path, load_projects=lambda: [project], project_root=tmp_path
        )
        assert chat == "-1003449100931"

    def test_project_root_match_without_eng_group_returns_fallback(self, tmp_path):
        project = {"slug": "royop", "working_directory": str(tmp_path), "telegram": {}}
        chat = resolve_host_eng_chat(
            tmp_path, load_projects=lambda: [project], project_root=tmp_path
        )
        assert chat == "Eng: Valor"  # FALLBACK_ENG_CHAT, this checkout only

    def test_foreign_registered_repo_with_no_group_returns_none(self, tmp_path):
        foreign_root = tmp_path / "foreign"
        foreign_root.mkdir()
        project = {"slug": "foo", "working_directory": str(foreign_root), "telegram": {}}
        chat = resolve_host_eng_chat(
            foreign_root, load_projects=lambda: [project], project_root=tmp_path
        )
        assert chat is None

    def test_unregistered_root_not_project_root_returns_none(self, tmp_path):
        foreign_root = tmp_path / "unregistered"
        foreign_root.mkdir()
        chat = resolve_host_eng_chat(foreign_root, load_projects=lambda: [], project_root=tmp_path)
        assert chat is None

    def test_load_projects_raising_falls_through_to_rung_3(self, tmp_path):
        def raising_loader():
            raise RuntimeError("boom")

        chat = resolve_host_eng_chat(tmp_path, load_projects=raising_loader, project_root=tmp_path)
        assert chat == "Eng: Valor"


class TestSendHostEngTelegram:
    def test_resolves_sends_by_id(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("reflections.utilities.subprocess.run", side_effect=fake_run),
            patch("reflections.utilities.resolve_host_eng_chat", return_value="-100999"),
        ):
            sent = send_host_eng_telegram("hi", logger_prefix="test")

        assert sent is True
        assert "-100999" in calls[0]

    def test_no_destination_suppresses_send_no_subprocess(self, caplog):
        with (
            patch("reflections.utilities.subprocess.run") as run,
            patch("reflections.utilities.resolve_host_eng_chat", return_value=None),
            caplog.at_level("WARNING"),
        ):
            sent = send_host_eng_telegram("hi", logger_prefix="test")

        assert sent is False
        run.assert_not_called()
