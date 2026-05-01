"""Smoke test for inbound Telegram attachment steering enrichment (issue #1215).

Drives the centralized helper ``_ack_steering_routed`` end-to-end with a
real temp ``.txt`` file. Verifies two end-to-end contracts:

1. The steering queue receives the file's enriched content -- not the
   ``--file attachment only--`` sentinel. ``push_steering_message`` is
   captured as a spy and inspected.
2. A disambiguated copy of the file lands under the vault subdirectory
   ``~/work-vault/telegram-attachments/`` (monkeypatched to ``tmp_path``
   so the real vault is never polluted). The disambiguated filename
   carries the date prefix and the message id.

The test substitutes ``process_incoming_media`` with a coroutine that
returns the file's text content + the file path -- this isolates the
steering helper from Telethon download mechanics while still exercising
the real ``_ingest_attachments`` copy path.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.messaging]


@pytest.mark.asyncio
async def test_txt_attachment_steering_enriches_and_ingests(tmp_path: Path, monkeypatch):
    """End-to-end: .txt → enriched steering push + vault copy."""
    from bridge import telegram_bridge

    # ----- Arrange -----
    # 1. Real on-disk file the helper will end up copying into the vault.
    src_file = tmp_path / "notes.txt"
    src_file.write_text("hello from the integration test", encoding="utf-8")

    # 2. Redirect the vault subdirectory to tmp_path so the real
    #    ~/work-vault/telegram-attachments/ stays untouched.
    fake_vault = tmp_path / "vault" / "telegram-attachments"
    monkeypatch.setattr(telegram_bridge, "_TELEGRAM_VAULT_SUBDIR", fake_vault)

    # 3. Substitute process_incoming_media with a coroutine that returns
    #    the document content + the on-disk path. This isolates the test
    #    from Telethon download internals while still driving the
    #    _ingest_attachments code path with a real file.
    expected_description = "[Document content]\nhello from the integration test"

    async def _fake_process(client, message):
        return (expected_description, [src_file])

    monkeypatch.setattr(telegram_bridge, "process_incoming_media", _fake_process)

    # 4. Stub Telegram-side I/O (reactions + handled-record) so the
    #    helper does not try to talk to a real Telethon client.
    monkeypatch.setattr(
        telegram_bridge,
        "set_reaction",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        telegram_bridge,
        "record_telegram_message_handled",
        AsyncMock(return_value=None),
    )

    # 5. Capture the Redis steering push as a spy. Returns nothing useful
    #    for our assertions but lets us read what `text` reached the queue.
    pushed: dict = {}

    def _spy_push(session_id, text, sender_name, *, is_abort=False):
        pushed["session_id"] = session_id
        pushed["text"] = text
        pushed["sender_name"] = sender_name
        pushed["is_abort"] = is_abort

    monkeypatch.setattr(telegram_bridge, "push_steering_message", _spy_push)

    # 6. Build a fake Telethon event/message.
    client = MagicMock()
    event = MagicMock()
    event.chat_id = 4242
    message = MagicMock()
    message.id = 9001
    message.media = object()  # truthy → media branch
    message.date = datetime(2026, 4, 30, 13, 49, 35, tzinfo=UTC)

    baseline_bg_len = len(telegram_bridge._background_tasks)

    # ----- Act -----
    await telegram_bridge._ack_steering_routed(
        client,
        event,
        message,
        session_id="sess-integration",
        sender_name="Integration Tester",
        text="--file attachment only--",
        log_context="[smoke]",
    )

    # The helper appends an _ingest_attachments task to _background_tasks.
    # Await it so the vault copy actually lands before we assert.
    assert len(telegram_bridge._background_tasks) == baseline_bg_len + 1
    new_task = telegram_bridge._background_tasks[-1]
    try:
        await asyncio.wait_for(new_task, timeout=5.0)
    finally:
        telegram_bridge._background_tasks[:] = telegram_bridge._background_tasks[:baseline_bg_len]

    # ----- Assert -----
    # Steering queue saw enriched text, not the sentinel.
    assert pushed, "push_steering_message was never called"
    assert pushed["session_id"] == "sess-integration"
    assert pushed["sender_name"] == "Integration Tester"
    assert pushed["text"] == expected_description
    assert pushed["is_abort"] is False
    assert "--file attachment only--" not in pushed["text"]

    # Vault copy landed with disambiguated filename.
    assert fake_vault.is_dir(), "vault subdir was not created"
    landed = list(fake_vault.iterdir())
    assert len(landed) == 1, f"expected exactly one vault copy, got {landed!r}"
    target = landed[0]
    # Filename pattern: {YYYYMMDD_HHMMSS}_{sender}_{message_id}_{basename}
    assert target.name.startswith("20260430_134935_")
    assert "Integration_Tester" in target.name
    assert "_9001_" in target.name
    assert target.name.endswith("_notes.txt")
    # Content fidelity: the bytes copied are exactly the source bytes.
    assert target.read_text(encoding="utf-8") == "hello from the integration test"
