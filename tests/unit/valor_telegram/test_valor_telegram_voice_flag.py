"""Unit tests for the --voice-note + --cleanup-after-send flags on
``valor-telegram send``.

Asserts:
- Without --voice-note, the payload pushed to the Redis outbox does NOT
  carry voice_note / duration / cleanup_file fields (backward compat).
- With --voice-note, the payload carries voice_note=True and a numeric
  duration field.
- With --cleanup-after-send, the payload carries cleanup_file=True.
- Without --cleanup-after-send, cleanup_file is absent (manual CLI use).
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch


def _fake_redis():
    r = MagicMock()
    r.rpush = MagicMock(return_value=1)
    r.expire = MagicMock(return_value=1)
    return r


def _make_args(file_path: str, voice_note: bool = False, cleanup: bool = False):
    """Build the argparse Namespace cmd_send expects."""
    import argparse

    return argparse.Namespace(
        chat="12345",
        message="",
        file=None,
        image=None,
        audio=file_path,
        reply_to=None,
        voice_note=voice_note,
        cleanup_after_send=cleanup,
    )


def _capture_payload(redis_mock):
    """Pull the JSON payload out of the rpush call args."""
    assert redis_mock.rpush.called, "rpush should have been invoked"
    _, raw = redis_mock.rpush.call_args.args
    return json.loads(raw)


def test_payload_default_omits_voice_fields():
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        from tools.valor_telegram import cmd_send

        r = _fake_redis()
        with (
            patch("tools.valor_telegram.resolve_chat", return_value="12345"),
            patch("tools.valor_telegram._get_redis_connection", return_value=r),
        ):
            rc = cmd_send(_make_args(path))
        assert rc == 0
        payload = _capture_payload(r)
        assert "voice_note" not in payload
        assert "duration" not in payload
        assert "cleanup_file" not in payload
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_voice_note_flag_sets_payload_fields():
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        from tools.valor_telegram import cmd_send

        r = _fake_redis()
        with (
            patch("tools.valor_telegram.resolve_chat", return_value="12345"),
            patch("tools.valor_telegram._get_redis_connection", return_value=r),
            patch("tools.tts._compute_duration_opus", return_value=4.2),
        ):
            rc = cmd_send(_make_args(path, voice_note=True))
        assert rc == 0
        payload = _capture_payload(r)
        assert payload.get("voice_note") is True
        assert payload.get("duration") == 4.2
        # Without --cleanup-after-send the relay should NOT delete
        assert "cleanup_file" not in payload
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_cleanup_after_send_flag_sets_cleanup_file():
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        from tools.valor_telegram import cmd_send

        r = _fake_redis()
        with (
            patch("tools.valor_telegram.resolve_chat", return_value="12345"),
            patch("tools.valor_telegram._get_redis_connection", return_value=r),
            patch("tools.tts._compute_duration_opus", return_value=2.0),
        ):
            rc = cmd_send(_make_args(path, voice_note=True, cleanup=True))
        assert rc == 0
        payload = _capture_payload(r)
        assert payload.get("cleanup_file") is True
    finally:
        if os.path.exists(path):
            os.unlink(path)
