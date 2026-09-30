"""Unit tests for the belt-and-suspenders length guard in bridge.telegram_relay.

The guard intercepts any text message >4096 chars that reaches the relay and
converts it to a .txt file attachment. This is a secondary defense; the primary
fix lives in the message drafter (bridge/message_drafter.py). NEVER split
messages -- see docs/plans/message-drafter.md (No-Gos section).

Tests assert:
- A 4096-char text passes through unchanged (no .txt conversion).
- A 4097-char text triggers .txt conversion with a short caption.
- The ERROR log contains session_id, chat_id, and len.
- The full raw text is written to the temp .txt file.
- Conversion failure falls back to the normal text send path.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.telegram_relay import _send_queued_message


def _fake_sent(msg_id: int = 42):
    """Build a minimal object mimicking a Telethon Message with an .id attribute."""
    obj = MagicMock()
    obj.id = msg_id
    return obj


@pytest.mark.asyncio
async def test_length_guard_passes_through_at_4096_chars(caplog):
    """Text of exactly 4096 chars should go through the normal text path, no conversion."""
    client = MagicMock()
    client.send_file = AsyncMock()

    # Patch send_markdown to return a fake Message
    with patch(
        "bridge.markdown.send_markdown",
        new_callable=AsyncMock,
        return_value=_fake_sent(msg_id=100),
    ) as mock_send_markdown:
        msg = {
            "chat_id": "123456",
            "reply_to": None,
            "text": "x" * 4096,
            "session_id": "test-session-4096",
        }
        result = await _send_queued_message(client, msg)

    assert result == 100
    mock_send_markdown.assert_awaited_once()
    # send_file must NOT have been called (no overflow path triggered)
    client.send_file.assert_not_called()


@pytest.mark.asyncio
async def test_length_guard_converts_4097_to_txt_attachment(caplog):
    """Text >4096 chars triggers .txt conversion; send_file called, send_markdown not."""
    import logging

    caplog.set_level(logging.ERROR, logger="bridge.telegram_relay")

    client = MagicMock()
    client.send_file = AsyncMock(return_value=_fake_sent(msg_id=7))

    oversize = "A" * 4097
    msg = {
        "chat_id": "123456",
        "reply_to": 55,
        "text": oversize,
        "session_id": "tg_test_overflow_42",
    }

    with patch("bridge.markdown.send_markdown", new_callable=AsyncMock) as mock_send_markdown:
        result = await _send_queued_message(client, msg)

    assert result == 7
    # send_file was called with a .txt path
    client.send_file.assert_awaited_once()
    args, kwargs = client.send_file.call_args
    # first positional is chat_id (int), second is the file path
    assert args[0] == 123456
    file_path = args[1]
    assert isinstance(file_path, str)
    assert file_path.endswith(".txt")
    assert os.path.isfile(file_path), f"Expected the overflow file at {file_path}"
    # Caption is short and mentions the auto-attach reason
    caption = kwargs.get("caption")
    assert caption is not None
    assert "4096" in caption or "exceeded" in caption.lower()
    # reply_to is preserved
    assert kwargs.get("reply_to") == 55
    # Normal text send path must NOT have been used
    mock_send_markdown.assert_not_called()

    # ERROR log contains session_id, chat_id, and length
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "Expected an ERROR log when length guard trips"
    combined = " ".join(r.getMessage() for r in error_records)
    assert "tg_test_overflow_42" in combined
    assert "123456" in combined
    assert "4097" in combined

    # Full raw text preserved in the .txt file
    with open(file_path) as fh:
        written = fh.read()
    assert written == oversize

    # Cleanup
    try:
        os.remove(file_path)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_length_guard_never_splits():
    """Guard must never produce multiple send calls for a single oversized message."""
    client = MagicMock()
    client.send_file = AsyncMock(return_value=_fake_sent(msg_id=1))

    msg = {
        "chat_id": "777",
        "reply_to": None,
        "text": "Z" * 8000,  # well over limit
        "session_id": "s1",
    }

    with patch("bridge.markdown.send_markdown", new_callable=AsyncMock) as mock_send_markdown:
        await _send_queued_message(client, msg)

    # Exactly one send call; no splitting.
    assert client.send_file.await_count == 1
    assert mock_send_markdown.await_count == 0


@pytest.mark.asyncio
async def test_length_guard_falls_back_on_conversion_failure(caplog, monkeypatch):
    """If .txt conversion itself fails, fall through to the normal text send path."""
    import logging

    caplog.set_level(logging.ERROR, logger="bridge.telegram_relay")

    # Force tempfile.mkstemp inside _send_queued_message to raise.
    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(tempfile, "mkstemp", _boom)

    client = MagicMock()
    client.send_file = AsyncMock()

    with patch(
        "bridge.markdown.send_markdown",
        new_callable=AsyncMock,
        return_value=_fake_sent(msg_id=99),
    ) as mock_send_markdown:
        msg = {
            "chat_id": "500",
            "reply_to": None,
            "text": "q" * 5000,
            "session_id": "fallback",
        }
        result = await _send_queued_message(client, msg)

    # Fell back to the normal text send path; send_markdown was invoked.
    assert result == 99
    mock_send_markdown.assert_awaited_once()
    # send_file NOT called (conversion errored before send).
    client.send_file.assert_not_called()
    # The first ERROR announces the oversize; a second ERROR announces the failed conversion.
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("oversized" in m.lower() or "4096" in m for m in errors)
