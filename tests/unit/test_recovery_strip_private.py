"""Recovery scanners strip ``<private>`` spans at intake (#3040).

Live intake (``bridge/telegram_bridge.py``) computes ``strip_private(text)``
once and every durable boundary downstream (bridge.log, Memory,
TelegramMessage, AgentSession.message_text) sees the stripped variant. The
three recovery scanners re-enqueue messages the live handler missed, so they
own the same boundaries: the recovery log line and the ``message_text`` they
hand to ``enqueue_agent_session``. The ruling recorded in
``docs/features/durability-model.md`` ("Private-tag stripping happens at
intake") is that every path strips where it first reads the text, so nothing
downstream can log or persist a wrapped span.

Each test drives the real scanner with a payload carrying a private-tagged
span and asserts the span reaches neither boundary. The tests fail against a
scanner that logs or enqueues the raw text.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.agent_catchup import UNANSWERED_NEEDS_REPLY, OwnedChat, sweep_chat
from bridge.catchup import scan_for_missed_messages
from bridge.reconciler import reconcile_once

pytestmark = pytest.mark.unit

SECRET = "sk-SECRET-42-never-durable"
RAW_TEXT = f"rotate the key <private>{SECRET}</private> before Monday"
STRIPPED_TEXT = "rotate the key before Monday"


def _make_message(msg_id: int, text: str):
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = False
    msg.date = datetime.now(UTC) - timedelta(minutes=1)
    msg.reply_to_msg_id = None
    sender = MagicMock()
    sender.first_name = "TestUser"
    sender.username = "testuser"
    sender.id = 12345
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dialog(chat_title: str, entity_id: int):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = -(1000000000000 + entity_id)
    return dialog


def _project():
    return {"_key": "testproj", "working_directory": "/tmp/test"}


def _assert_stripped_everywhere(enqueue_fn: AsyncMock, caplog) -> None:
    enqueue_fn.assert_called_once()
    message_text = enqueue_fn.call_args.kwargs["message_text"]
    assert message_text == STRIPPED_TEXT, "AgentSession.message_text must carry stripped text"
    assert SECRET not in message_text
    assert SECRET not in caplog.text, "the recovery log line is durable; it must not carry the span"
    assert "<private>" not in caplog.text
    assert "rotate the key" in caplog.text, "the log line still carries the stripped preview"


@pytest.mark.asyncio
async def test_reconciler_strips_private_span_at_intake(caplog):
    caplog.set_level(logging.DEBUG, logger="bridge.reconciler")
    dialog = _make_dialog("Test Group", entity_id=200)
    msg = _make_message(555, RAW_TEXT)
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])
    client.get_messages = AsyncMock(return_value=[msg])
    enqueue_fn = AsyncMock()

    with (
        patch("bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
        patch("bridge.reconciler.record_message_processed", AsyncMock()),
        patch("bridge.reconciler.record_last_processed", AsyncMock()),
    ):
        recovered = await reconcile_once(
            client=client,
            monitored_groups=["test group"],
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(return_value=_project()),
        )

    assert recovered == 1
    _assert_stripped_everywhere(enqueue_fn, caplog)


@pytest.mark.asyncio
async def test_catchup_strips_private_span_at_intake(caplog):
    caplog.set_level(logging.DEBUG, logger="bridge.catchup")
    dialog = _make_dialog("Test Group", entity_id=401)
    msg = _make_message(701, RAW_TEXT)
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])

    async def _get_messages(entity, limit=None, min_id=None, offset_id=0):
        if min_id is not None or offset_id != 0:
            return []
        return [msg]

    client.get_messages = AsyncMock(side_effect=_get_messages)
    enqueue_fn = AsyncMock()

    with (
        patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
        patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
        patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
        patch("bridge.dedup.release_message_claim", new_callable=AsyncMock),
    ):
        queued = await scan_for_missed_messages(
            client=client,
            monitored_groups=["test group"],
            projects_config={},
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(return_value=_project()),
        )

    assert queued == 1
    _assert_stripped_everywhere(enqueue_fn, caplog)


class _FakeClient:
    def __init__(self, messages):
        self._messages = messages

    async def get_messages(self, entity, limit=None):
        return self._messages[:limit] if limit else self._messages


@pytest.mark.asyncio
async def test_agent_catchup_strips_private_span_at_intake(caplog):
    caplog.set_level(logging.DEBUG, logger="bridge.agent_catchup")
    sender = SimpleNamespace(first_name="TestUser", id=12345)

    async def _get_sender():
        return sender

    inbound = SimpleNamespace(
        id=42,
        text=RAW_TEXT,
        out=False,
        date=datetime.now(UTC) - timedelta(minutes=5),
        get_sender=_get_sender,
        reactions=None,
    )
    chat = OwnedChat(chat_id=555, chat_title="Dev: Popoto", project=_project(), entity=object())
    enqueue_fn = AsyncMock()
    judge_calls: list[tuple] = []

    async def _judge(transcript: str, inbound_text: str, inbound_id: int) -> str:
        judge_calls.append((transcript, inbound_text, inbound_id))
        return UNANSWERED_NEEDS_REPLY

    async def _noop(*args, **kwargs):
        return None

    result = await sweep_chat(
        _FakeClient([inbound]),
        chat,
        enqueue_fn=enqueue_fn,
        judge_fn=_judge,
        record_processed_fn=_noop,
        record_last_fn=_noop,
    )

    assert result.enqueued == 1
    _assert_stripped_everywhere(enqueue_fn, caplog)
    # The judge transcript is built from the same intake read, so the span is
    # gone there too: intake is the single strip point for this scanner.
    transcript, inbound_text, _ = judge_calls[0]
    assert SECRET not in transcript and SECRET not in inbound_text
