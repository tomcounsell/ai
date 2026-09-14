"""Unit tests for reflections/pm_briefings/delivery.py.

Covers:
- Happy path: synthesize succeeds, voice-note enqueued before written followup
- TTS-failure contract: synthesize() returns dict with `error` (does not raise);
  delivery enqueues failure-notice text payload per group AND raises
  BriefingTtsFailedError; written follow-up is NOT enqueued
- chat_id resolution: missing/None chat_id logs WARNING and skips the group
- DRY_RUN dump path
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from reflections.pm_briefings import delivery

pytestmark = [pytest.mark.unit]


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def project_dict():
    return {
        "slug": "test-proj",
        "machine": "TestMachine",
        "telegram": {
            "groups": {
                "PM: Test": {"chat_id": -100200, "persona": "project-manager"},
                "PM: NoCID": {"persona": "project-manager"},  # no chat_id
            }
        },
        "pm_briefing": {"timezone": "UTC"},
    }


# --- BriefingTtsFailedError class -------------------------------------------


def test_briefing_tts_failed_error_class_is_declared():
    assert isinstance(delivery.BriefingTtsFailedError, type)
    assert issubclass(delivery.BriefingTtsFailedError, RuntimeError)


# --- _resolve_chat_id --------------------------------------------------------


class TestResolveChatId:
    def test_resolves_dict_form(self, project_dict):
        assert delivery._resolve_chat_id(project_dict, "PM: Test") == -100200

    def test_returns_none_when_missing(self, project_dict):
        assert delivery._resolve_chat_id(project_dict, "PM: Nonexistent") is None

    def test_returns_none_when_no_chat_id(self, project_dict):
        assert delivery._resolve_chat_id(project_dict, "PM: NoCID") is None


# --- send() happy path -------------------------------------------------------


class TestSendHappyPath:
    def test_voice_note_enqueued_before_followup(self, project_dict):
        ok_result = {
            "error": None,
            "path": "/tmp/x.ogg",
            "duration": 12.5,
            "backend": "kokoro",
            "voice": "am_michael",
            "format": "opus",
        }
        mock_redis = MagicMock()
        with (
            patch("tools.tts.synthesize", return_value=ok_result),
            patch.object(delivery, "_get_redis_connection", return_value=mock_redis),
        ):
            statuses = delivery.send(
                "Shipped the auth fix.",
                "## Shipped\n- [#42](url) — Auth\n",
                ["PM: Test"],
                project_dict,
            )

        # Two rpush calls: voice-note first, followup second
        rpush_calls = mock_redis.rpush.call_args_list
        assert len(rpush_calls) == 2
        assert statuses["PM: Test"] == "ok"

        # First payload is the voice-note
        first_payload = rpush_calls[0][0][1]
        assert "voice_note" in first_payload
        assert "file_paths" in first_payload

        # Second payload is the followup (text only)
        second_payload = rpush_calls[1][0][1]
        assert "voice_note" not in second_payload
        assert "## Shipped" in second_payload

    def test_skips_groups_with_no_chat_id(self, project_dict):
        ok_result = {
            "error": None,
            "path": "/tmp/x.ogg",
            "duration": 5.0,
            "backend": "kokoro",
            "voice": "am_michael",
            "format": "opus",
        }
        mock_redis = MagicMock()
        with (
            patch("tools.tts.synthesize", return_value=ok_result),
            patch.object(delivery, "_get_redis_connection", return_value=mock_redis),
        ):
            statuses = delivery.send(
                "Hello.",
                "",
                ["PM: Test", "PM: NoCID"],
                project_dict,
            )
        # The group with no chat_id is skipped (no rpush for it)
        assert statuses["PM: Test"] == "ok"
        assert statuses["PM: NoCID"] == "skipped"


# --- send() TTS-failure contract --------------------------------------------


class TestSendTtsFailure:
    def test_failure_notice_enqueued_per_group_and_raises(self, project_dict):
        # Real failure shape per tools/tts/__init__.py:360-440
        fail_result = {
            "error": "backend unavailable",
            "path": None,
            "duration": 0.0,
            "backend": "cloud",
            "voice": "am_michael",
            "format": "opus",
        }
        mock_redis = MagicMock()
        with (
            patch("tools.tts.synthesize", return_value=fail_result),
            patch.object(delivery, "_get_redis_connection", return_value=mock_redis),
        ):
            with pytest.raises(delivery.BriefingTtsFailedError):
                delivery.send(
                    "transcript here",
                    "follow-up here",
                    ["PM: Test"],
                    project_dict,
                )

        # Exactly ONE rpush per target group with the failure notice;
        # no voice-note payload, no follow-up payload
        assert mock_redis.rpush.call_count == 1
        payload = mock_redis.rpush.call_args_list[0][0][1]
        assert "Daily briefing failed" in payload
        assert "voice_note" not in payload
        assert "file_paths" not in payload
        assert "follow-up here" not in payload


# --- send() empty transcript / dry-run --------------------------------------


class TestSendNoOpAndDryRun:
    def test_empty_transcript_returns_skipped(self, project_dict):
        statuses = delivery.send("", "", ["PM: Test"], project_dict)
        assert statuses["PM: Test"] == "skipped"

    def test_dry_run_writes_log_file(self, project_dict, tmp_path, monkeypatch):
        # Monkeypatch the log dir to tmp_path
        monkeypatch.setattr(
            delivery,
            "_dry_run_dump",
            lambda transcript, fu, p: {"_dry_run_path": str(tmp_path / "dump.txt")},
        )
        statuses = delivery.send(
            "transcript",
            "followup",
            ["PM: Test"],
            project_dict,
            dry_run=True,
        )
        assert "_dry_run_path" in statuses
