"""Unit tests for the voice-note + cleanup_file relay branch.

Asserts:
- A payload with `voice_note: True` calls Telethon `send_file` with
  `voice_note=True` and `attributes=[DocumentAttributeAudio(voice=True)]`.
- A payload with `cleanup_file: True` triggers an unlink after a successful
  send (voice or document).
- A payload with `cleanup_file: True` triggers an unlink when the message
  is moved to the dead-letter queue (retry exhaustion).
- Voice-note send failure falls back to the standard document-send path
  and logs a warning -- the relay never raises.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.telegram_relay import _dead_letter_message, _send_queued_message


def _fake_sent(msg_id: int = 99):
    obj = MagicMock()
    obj.id = msg_id
    return obj


def _fresh_voice_file() -> str:
    """Create an empty .ogg temp file and return its path. Caller cleans up."""
    fd, path = tempfile.mkstemp(suffix=".ogg", prefix="voice_test_")
    os.close(fd)
    return path


@pytest.mark.asyncio
async def test_voice_note_branch_uses_voice_kwarg_and_attribute():
    voice_file = _fresh_voice_file()
    try:
        client = MagicMock()
        client.send_file = AsyncMock(return_value=_fake_sent(42))

        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "",
            "file_paths": [voice_file],
            "voice_note": True,
            "duration": 7.4,
        }
        result = await _send_queued_message(client, msg)
        assert result == 42

        kwargs = client.send_file.call_args.kwargs
        assert kwargs.get("voice_note") is True
        attrs = kwargs.get("attributes")
        assert attrs and len(attrs) == 1
        # Confirm DocumentAttributeAudio with voice=True
        attr = attrs[0]
        assert getattr(attr, "voice", False) is True
        assert getattr(attr, "duration", None) == 7  # int(7.4)
    finally:
        if os.path.exists(voice_file):
            os.unlink(voice_file)


@pytest.mark.asyncio
async def test_cleanup_file_unlinks_after_voice_note_send():
    voice_file = _fresh_voice_file()
    try:
        client = MagicMock()
        client.send_file = AsyncMock(return_value=_fake_sent(101))

        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "",
            "file_paths": [voice_file],
            "voice_note": True,
            "duration": 1.0,
            "cleanup_file": True,
        }
        result = await _send_queued_message(client, msg)
        assert result == 101
        assert not os.path.exists(voice_file), "relay should have deleted the temp file"
    finally:
        if os.path.exists(voice_file):
            os.unlink(voice_file)


@pytest.mark.asyncio
async def test_cleanup_file_unlinks_after_document_send():
    """cleanup_file is honored for non-voice attachments too."""
    doc_file = _fresh_voice_file()
    try:
        client = MagicMock()
        client.send_file = AsyncMock(return_value=_fake_sent(202))

        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "",
            "file_paths": [doc_file],
            "cleanup_file": True,
        }
        result = await _send_queued_message(client, msg)
        assert result == 202
        assert not os.path.exists(doc_file)
    finally:
        if os.path.exists(doc_file):
            os.unlink(doc_file)


@pytest.mark.asyncio
async def test_cleanup_file_skipped_when_flag_absent():
    """The relay must NOT delete files unless the producer opted in."""
    doc_file = _fresh_voice_file()
    try:
        client = MagicMock()
        client.send_file = AsyncMock(return_value=_fake_sent(303))

        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "",
            "file_paths": [doc_file],
            # cleanup_file deliberately absent
        }
        await _send_queued_message(client, msg)
        assert os.path.exists(doc_file), "file must survive when cleanup_file is not set"
    finally:
        if os.path.exists(doc_file):
            os.unlink(doc_file)


@pytest.mark.asyncio
async def test_voice_note_failure_falls_back_to_document_send(caplog):
    """If voice-note send raises, the relay must fall through to document send."""
    voice_file = _fresh_voice_file()
    try:
        client = MagicMock()
        # First call (voice_note) raises, second call (document send) succeeds.
        client.send_file = AsyncMock(
            side_effect=[RuntimeError("telethon: voice rejected"), _fake_sent(404)]
        )

        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "",
            "file_paths": [voice_file],
            "voice_note": True,
            "duration": 2.0,
        }
        result = await _send_queued_message(client, msg)
        assert result == 404
        assert client.send_file.call_count == 2
        # Confirm fallback was logged
        assert any("voice-note send failed" in r.message for r in caplog.records)
    finally:
        if os.path.exists(voice_file):
            os.unlink(voice_file)


@pytest.mark.asyncio
async def test_cleanup_file_unlinks_on_dead_letter_placement():
    """Retry exhaustion is the OTHER terminal completion point that must clean up."""
    voice_file = _fresh_voice_file()
    try:
        msg = {
            "chat_id": "12345",
            "session_id": "cli-test",
            "text": "stub text so dead_letters has something to persist",
            "file_paths": [voice_file],
            "voice_note": True,
            "duration": 1.0,
            "cleanup_file": True,
        }
        # persist_failed_delivery is best-effort; mock to a no-op so we can
        # focus on the unlink contract.
        with patch("bridge.dead_letters.persist_failed_delivery", new=AsyncMock()):
            await _dead_letter_message(msg, reason="max retries exceeded")
        assert not os.path.exists(voice_file), "DLQ placement must honor cleanup_file"
    finally:
        if os.path.exists(voice_file):
            os.unlink(voice_file)
