"""Shadow Room-inbox append on the RECOVERY re-enqueue paths (issue #2494).

The phase-2 gate found that recovery re-enqueues bypassed the shadow append:
a message re-dispatched by a scanner had no Room-inbox entry. All three
scanners (``bridge/catchup.py``, ``bridge/reconciler.py``,
``bridge/agent_catchup.py``) now call ``shadow_append_inbox`` alongside their
untouched enqueue, mirroring live intake's append-precedes-dispatch order.

Two invariants per scanner:
1. A re-enqueued message produces a matching Room-inbox entry (same
   ``chat_id``/``message_id`` shape as live intake).
2. An inbox failure (``Room.resolve`` raising) NEVER prevents the re-enqueue —
   shadow mode has no durability responsibility; dispatch is untouched.

Logging containment: importing ``bridge.telegram_bridge`` anywhere in the
pytest process attaches a root RotatingFileHandler on ``logs/bridge.log``, so
the deliberate ERROR logs these failure tests provoke would leak into the real
bridge log (the fixture-leakage defect the gate report flagged for the intake
test). The autouse ``_contain_bridge_log`` fixture detaches any such handler
for the duration of each test; assertions use ``caplog`` instead.
"""

import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.agent_catchup import UNANSWERED_NEEDS_REPLY, OwnedChat, sweep_chat
from bridge.catchup import scan_for_missed_messages
from bridge.reconciler import reconcile_once
from models.room import Room, telegram_addressee


@pytest.fixture(autouse=True)
def _contain_bridge_log():
    """Detach any root file handler on logs/bridge.log for this test's duration.

    Keeps the deliberate ERROR logs from the failure-path tests out of the
    real bridge log; ``caplog`` still captures them via its own handler.
    """
    root = logging.getLogger()
    detached = [
        h
        for h in root.handlers
        if isinstance(h, logging.FileHandler)
        and str(getattr(h, "baseFilename", "")).endswith("bridge.log")
    ]
    for h in detached:
        root.removeHandler(h)
    yield
    for h in detached:
        root.addHandler(h)


@pytest.fixture
def scratch_project():
    key = f"test-inbox-{uuid.uuid4().hex[:8]}"
    project = {"_key": key, "working_directory": "/tmp/x"}
    yield project
    for room in Room.query.filter(project_key=key):
        room.clear_inbox()
        room.delete()


@pytest.fixture
def broken_room_resolve(monkeypatch):
    """Make Room.resolve raise, simulating an inbox outage."""

    def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(Room, "resolve", classmethod(lambda cls, *a, **k: _boom()))


def _inbox_entries(project, chat_id):
    room = Room.resolve(project["_key"], telegram_addressee(chat_id))
    return room.peek_inbox()


# ---------------------------------------------------------------------------
# Mechanical-scanner fakes (mirrors test_catchup_claim.py / test_reconciler.py)
# ---------------------------------------------------------------------------


def _make_message(msg_id, text="hello", out=False, minutes_ago=1):
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = out
    msg.date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    msg.reply_to_msg_id = None

    sender = MagicMock()
    sender.first_name = "TestUser"
    sender.username = "testuser"
    sender.id = 12345
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dialog(chat_title, entity_id=100):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = -(1000000000000 + entity_id)
    return dialog


def _client_for(dialog, message):
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])
    client.get_messages = AsyncMock(return_value=[message])
    return client


@contextlib.contextmanager
def _dedup_patches():
    """Patch the shared dedup gate so the scanners treat the message as missed."""
    patches = (
        patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
        patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
        patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
        patch("bridge.dedup.release_message_claim", new_callable=AsyncMock),
        # bridge/reconciler.py imports these at module top — patch its names too.
        patch("bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
        patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        patch("bridge.reconciler.release_message_claim", new_callable=AsyncMock),
    )
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


async def _run_catchup(client, project, enqueue_fn):
    return await scan_for_missed_messages(
        client=client,
        monitored_groups=["test group"],
        projects_config={},
        should_respond_fn=AsyncMock(return_value=(True, False)),
        enqueue_agent_session_fn=enqueue_fn,
        find_project_fn=MagicMock(return_value=project),
    )


async def _run_reconciler(client, project, enqueue_fn):
    return await reconcile_once(
        client=client,
        monitored_groups=["test group"],
        should_respond_fn=AsyncMock(return_value=(True, False)),
        enqueue_agent_session_fn=enqueue_fn,
        find_project_fn=MagicMock(return_value=project),
    )


class TestCatchupShadowAppend:
    @pytest.mark.asyncio
    async def test_re_enqueue_appends_matching_inbox_entry(self, scratch_project):
        dialog = _make_dialog("Test Group", entity_id=500)
        msg = _make_message(801, text="missed message")
        enqueue_fn = AsyncMock()

        with _dedup_patches():
            queued = await _run_catchup(_client_for(dialog, msg), scratch_project, enqueue_fn)

        assert queued == 1
        enqueue_fn.assert_called_once()
        entries = _inbox_entries(scratch_project, dialog.id)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["chat_id"] == str(dialog.id)
        assert entry["message_id"] == 801
        assert entry["sender_name"] == "TestUser"
        assert entry["text"] == "missed message"

    @pytest.mark.asyncio
    async def test_inbox_failure_does_not_prevent_re_enqueue(
        self, scratch_project, broken_room_resolve, caplog
    ):
        dialog = _make_dialog("Test Group", entity_id=501)
        msg = _make_message(802, text="missed message")
        enqueue_fn = AsyncMock()

        with caplog.at_level(logging.ERROR, logger="bridge.room_inbox"), _dedup_patches():
            queued = await _run_catchup(_client_for(dialog, msg), scratch_project, enqueue_fn)

        assert queued == 1, "inbox outage must not block the recovery re-enqueue"
        enqueue_fn.assert_called_once()
        assert any("shadow append failed" in rec.message for rec in caplog.records)


class TestReconcilerShadowAppend:
    @pytest.mark.asyncio
    async def test_re_enqueue_appends_matching_inbox_entry(self, scratch_project):
        dialog = _make_dialog("Test Group", entity_id=600)
        msg = _make_message(901, text="recovered message")
        enqueue_fn = AsyncMock()

        with _dedup_patches():
            recovered = await _run_reconciler(_client_for(dialog, msg), scratch_project, enqueue_fn)

        assert recovered == 1
        enqueue_fn.assert_called_once()
        entries = _inbox_entries(scratch_project, dialog.id)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["chat_id"] == str(dialog.id)
        assert entry["message_id"] == 901
        assert entry["text"] == "recovered message"

    @pytest.mark.asyncio
    async def test_inbox_failure_does_not_prevent_re_enqueue(
        self, scratch_project, broken_room_resolve, caplog
    ):
        dialog = _make_dialog("Test Group", entity_id=601)
        msg = _make_message(902, text="recovered message")
        enqueue_fn = AsyncMock()

        with caplog.at_level(logging.ERROR, logger="bridge.room_inbox"), _dedup_patches():
            recovered = await _run_reconciler(_client_for(dialog, msg), scratch_project, enqueue_fn)

        assert recovered == 1, "inbox outage must not block the recovery re-enqueue"
        enqueue_fn.assert_called_once()
        assert any("shadow append failed" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Agent-catchup (LLM-judge sweep) — mirrors test_agent_catchup.py fakes
# ---------------------------------------------------------------------------


def _make_thread_msg(msg_id: int, text: str, *, out: bool = False, minutes_ago: int = 5):
    date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sender = SimpleNamespace(first_name="TestUser", id=12345)

    async def _get_sender():
        return sender

    return SimpleNamespace(
        id=msg_id,
        text=text,
        out=out,
        date=date,
        get_sender=_get_sender,
        reactions=None,
    )


class _FakeClient:
    def __init__(self, messages_by_entity: dict):
        self._messages = messages_by_entity

    async def get_messages(self, entity, limit=None):
        msgs = self._messages.get(entity, [])
        return msgs[:limit] if limit else msgs


async def _noop_record(*args, **kwargs):
    return None


async def _unanswered_judge(transcript, inbound_text, inbound_id):
    return UNANSWERED_NEEDS_REPLY


def _owned_chat(project, chat_id=555):
    return OwnedChat(chat_id=chat_id, chat_title="Test Group", project=project, entity=object())


class TestAgentCatchupShadowAppend:
    @pytest.mark.asyncio
    async def test_recovery_enqueue_appends_matching_inbox_entry(self, scratch_project):
        chat = _owned_chat(scratch_project, chat_id=555)
        messages = [_make_thread_msg(42, "unanswered question?")]
        client = _FakeClient({chat.entity: messages})
        enqueue_calls: list[dict] = []

        async def enqueue_fn(**kwargs):
            enqueue_calls.append(kwargs)

        result = await sweep_chat(
            client,
            chat,
            enqueue_fn=enqueue_fn,
            judge_fn=_unanswered_judge,
            record_processed_fn=_noop_record,
            record_last_fn=_noop_record,
        )

        assert result.enqueued == 1
        assert len(enqueue_calls) == 1
        entries = _inbox_entries(scratch_project, chat.chat_id)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["chat_id"] == str(chat.chat_id)
        assert entry["message_id"] == 42
        assert entry["sender_name"] == "TestUser"
        assert entry["text"] == "unanswered question?"

    @pytest.mark.asyncio
    async def test_inbox_failure_does_not_prevent_recovery_enqueue(
        self, scratch_project, broken_room_resolve, caplog
    ):
        chat = _owned_chat(scratch_project, chat_id=556)
        messages = [_make_thread_msg(43, "unanswered question?")]
        client = _FakeClient({chat.entity: messages})
        enqueue_calls: list[dict] = []

        async def enqueue_fn(**kwargs):
            enqueue_calls.append(kwargs)

        with caplog.at_level(logging.ERROR, logger="bridge.room_inbox"):
            result = await sweep_chat(
                client,
                chat,
                enqueue_fn=enqueue_fn,
                judge_fn=_unanswered_judge,
                record_processed_fn=_noop_record,
                record_last_fn=_noop_record,
            )

        assert result.enqueued == 1, "inbox outage must not block the recovery enqueue"
        assert len(enqueue_calls) == 1
        assert any("shadow append failed" in rec.message for rec in caplog.records)
