"""Integration tests for reflections/daily_report.py (#1263).

Exercises the end-to-end pipeline against a temp vault dir, with the audio
brief LLM and TTS calls patched out. Verifies the file lands and the audio
outbox payload (when synthesized) matches the pm_audio_briefing/delivery
shape.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import reflections.daily_report as dr


@pytest.fixture
def yesterday_utc() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


@pytest.fixture
def temp_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault" / "AI Valor Engels System" / "daily-logs"

    def fake_resolve():
        return vault

    monkeypatch.setattr(dr, "_resolve_vault_path", fake_resolve)
    return vault


def test_run_writes_vault_file_when_no_signals(yesterday_utc, temp_vault):
    """Empty day still produces a file with the no-activity marker."""

    async def fake_collect(target_date):
        return dr.DayActivity(date_iso=target_date.strftime("%Y-%m-%d"))

    with patch.object(dr, "_collect_day_activity", side_effect=fake_collect):
        result = asyncio.run(dr.run())

    assert result["status"] == "ok"
    target_str = yesterday_utc.strftime("%Y-%m-%d")
    written = temp_vault / f"{target_str}.md"
    assert written.exists(), f"vault file not written at {written}"
    body = written.read_text()
    assert "# Daily Log:" in body
    assert "No system activity recorded" in body
    # Audio brief skipped
    assert any("Audio brief skipped" in f for f in result["findings"])


def test_run_writes_substantive_log_with_full_named_entities(yesterday_utc, temp_vault):
    """When the aggregator returns rich data, the file contains full entities."""

    async def fake_collect(target_date):
        return dr.DayActivity(
            date_iso=target_date.strftime("%Y-%m-%d"),
            commits=[
                {
                    "project": "ai",
                    "sha": "deadbeef",
                    "author": "v",
                    "subject": "feat: vault writer",
                    "is_merge": False,
                },
            ],
            prs=[
                {
                    "project": "ai",
                    "number": 1263,
                    "title": "Daily Log Overhaul",
                    "state": "MERGED",
                    "url": "https://example.test/pr/1263",
                },
            ],
        )

    # Force audio brief to return empty so we don't make LLM calls
    with (
        patch.object(dr, "_collect_day_activity", side_effect=fake_collect),
        patch.object(dr, "_build_audio_brief", return_value=("", "")),
    ):
        result = asyncio.run(dr.run())

    assert result["status"] == "ok"
    target_str = yesterday_utc.strftime("%Y-%m-%d")
    body = (temp_vault / f"{target_str}.md").read_text()
    # Full named entity (subject + URL) appears
    assert "feat: vault writer" in body
    assert "Daily Log Overhaul" in body
    assert "https://example.test/pr/1263" in body


def test_run_handles_aggregator_errors_gracefully(yesterday_utc, temp_vault):
    """Per-source errors surface as `[ERROR: ...]` lines but file still lands."""

    async def fake_collect(target_date):
        a = dr.DayActivity(date_iso=target_date.strftime("%Y-%m-%d"))
        a.errors["git:fake"] = "timeout after 30s"
        return a

    with patch.object(dr, "_collect_day_activity", side_effect=fake_collect):
        result = asyncio.run(dr.run())

    assert result["status"] == "ok"
    body = (temp_vault / f"{yesterday_utc.strftime('%Y-%m-%d')}.md").read_text()
    assert "## Aggregator Notes" in body
    assert "[ERROR: git:fake]" in body


def test_run_handles_tts_failure_without_crashing(yesterday_utc, temp_vault):
    """TTS failure must not crash the reflection — file still lands."""

    async def fake_collect(target_date):
        return dr.DayActivity(
            date_iso=target_date.strftime("%Y-%m-%d"),
            prs=[{"project": "ai", "number": 1, "title": "X", "state": "MERGED", "url": "u"}],
        )

    def fake_tts(text, output_path, **kwargs):
        return {"error": "kokoro unavailable", "path": output_path, "duration": 0.0}

    with (
        patch.object(dr, "_collect_day_activity", side_effect=fake_collect),
        patch.object(dr, "_build_audio_brief", return_value=("Brief transcript", "")),
        patch("tools.tts.synthesize", side_effect=fake_tts),
    ):
        result = asyncio.run(dr.run())

    assert result["status"] == "ok"
    assert any("TTS failed" in f for f in result["findings"])


def test_voice_note_payload_shape_matches_delivery_module(yesterday_utc):
    """The enqueued payload must match `pm_audio_briefing.delivery._voice_note_payload`."""
    import redis

    fake_payloads = []

    class FakeRedis:
        def rpush(self, key, value):
            fake_payloads.append((key, json.loads(value)))

        def expire(self, key, seconds):
            pass

    with patch.object(redis, "from_url", return_value=FakeRedis()):
        dr._enqueue_voice_note(
            chat_id=-12345,
            audio_path="/tmp/brief.ogg",
            duration=12.5,
            session_id="daily-report-and-notify-2026-05-02",
        )

    assert len(fake_payloads) == 1
    queue_key, payload = fake_payloads[0]
    assert queue_key == "telegram:outbox:daily-report-and-notify-2026-05-02"
    # Required fields per pm_audio_briefing/delivery.py:84-94
    assert payload["chat_id"] == -12345
    assert payload["voice_note"] is True
    assert payload["text"] == ""
    assert payload["file_paths"] == ["/tmp/brief.ogg"]
    assert payload["duration"] == 12.5
    assert payload["cleanup_file"] is True
    assert payload["session_id"] == "daily-report-and-notify-2026-05-02"


def test_run_enqueues_voice_note_when_transcript_built(yesterday_utc, temp_vault):
    """Full happy path: aggregator → render → TTS → outbox."""
    enqueued = []

    def fake_select_chat(project):
        return -98765 if project.get("slug") == "ai" else None

    def fake_enqueue(chat_id, audio_path, duration, session_id):
        enqueued.append((chat_id, session_id, duration))

    async def fake_collect(target_date):
        return dr.DayActivity(
            date_iso=target_date.strftime("%Y-%m-%d"),
            prs=[{"project": "ai", "number": 1, "title": "X", "state": "MERGED", "url": "u"}],
        )

    def fake_tts(text, output_path, **kwargs):
        return {
            "path": output_path,
            "duration": 9.0,
            "backend": "kokoro",
            "voice": "af_bella",
            "format": "opus",
            "error": None,
        }

    with (
        patch.object(dr, "_collect_day_activity", side_effect=fake_collect),
        patch.object(dr, "_build_audio_brief", return_value=("Brief transcript", "")),
        patch("tools.tts.synthesize", side_effect=fake_tts),
        patch.object(dr, "_select_target_chat_id", side_effect=fake_select_chat),
        patch.object(dr, "_enqueue_voice_note", side_effect=fake_enqueue),
        patch.object(
            dr,
            "load_local_projects",
            return_value=[{"slug": "ai", "telegram": {"groups": {"PM: ai": {"chat_id": -98765}}}}],
        ),
    ):
        result = asyncio.run(dr.run())

    assert result["status"] == "ok"
    assert len(enqueued) == 1
    assert enqueued[0][0] == -98765
    # Session id includes the prefix and the date
    assert enqueued[0][1].startswith("daily-report-and-notify-")
    assert enqueued[0][2] == 9.0
