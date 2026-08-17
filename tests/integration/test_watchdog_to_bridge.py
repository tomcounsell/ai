"""Integration test: liveness tick publisher -> outbox -> bridge relay (#2716).

Drives one watchdog tick publish against a session past its first tick
interval, asserts the reaction payload lands in ``telegram:outbox:{session_id}``
with the counter's schema (including the ``priority`` field the drain guard
reads), then drives the same payload through the relay's reaction path with a
stub Telethon client.

Uses an in-memory Redis stub so this test never touches a live instance.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.reaction_priority import PRIORITY_HEARTBEAT, PRIORITY_TERMINAL
from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS, heartbeat_reaction

OUTBOX_KEY = "telegram:outbox:tg_user_-100_42"


class _FakeRedis:
    """In-memory stub for the get / set-NX-EX / rpush / expire / delete surface."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    def delete(self, key):
        self.store.pop(key, None)
        self.lists.pop(key, None)
        self.expires.pop(key, None)
        return 1


def _make_running_session(age_seconds):
    """A worker-executed Telegram session — status='running', which
    ``check_all_sessions`` never queries and the tick publisher must."""
    now = time.time()
    return SimpleNamespace(
        session_id="tg_user_-100_42",
        agent_session_id="as-001",
        status="running",
        started_at=now - age_seconds,
        created_at=now - age_seconds,
        updated_at=now - age_seconds,
        project_key="testproj",
        chat_id="-100",
        telegram_message_id=42,
    )


def _query_filter_for(sessions_by_status):
    def filter_fn(**kwargs):
        return sessions_by_status.get(kwargs.get("status", ""), [])

    return SimpleNamespace(filter=filter_fn)


def _publish(session):
    from monitoring import session_watchdog

    with patch(
        "monitoring.session_watchdog.AgentSession.query",
        _query_filter_for({"running": [session], "active": []}),
    ):
        return session_watchdog._publish_liveness_ticks()


def test_watchdog_tick_writes_reaction_to_outbox(monkeypatch):
    """A session past one tick interval queues exactly one counter payload."""
    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)

    assert _publish(_make_running_session(HEARTBEAT_TICK_INTERVAL_SECONDS + 5)) == 1

    assert len(fake.lists[OUTBOX_KEY]) == 1
    payload = json.loads(fake.lists[OUTBOX_KEY][0])
    assert payload["type"] == "reaction"
    assert payload["chat_id"] == "-100"
    assert payload["reply_to"] == 42
    assert payload["emoji"] == heartbeat_reaction(1).emoji
    assert payload["session_id"] == "tg_user_-100_42"
    assert payload["priority"] == PRIORITY_HEARTBEAT
    assert payload["heartbeat_tick"] == 1
    assert "timestamp" in payload

    # Outbox TTL applied so a relay-down window eventually clears.
    assert fake.expires[OUTBOX_KEY] == 3600


def test_relay_send_queued_reaction_accepts_tick_payload(monkeypatch):
    """The relay's reaction path must accept the counter's payload shape."""
    from bridge.telegram_relay import _send_queued_reaction

    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)
    _publish(_make_running_session(HEARTBEAT_TICK_INTERVAL_SECONDS + 5))
    payload = json.loads(fake.lists[OUTBOX_KEY][0])

    fake_set_reaction = AsyncMock(return_value=True)
    with patch("bridge.response.set_reaction", fake_set_reaction):
        result = asyncio.run(_send_queued_reaction(telegram_client=object(), message=payload))

    assert result is True
    args, _ = fake_set_reaction.call_args
    assert args[1] == -100
    assert args[2] == 42
    assert args[3] == heartbeat_reaction(1).emoji


def test_second_publish_in_the_same_interval_does_not_double_queue(monkeypatch):
    """The tick is wall-clock derived, so a rescan recomputes the same value."""
    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)

    session = _make_running_session(HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
    _publish(session)
    _publish(session)

    assert len(fake.lists[OUTBOX_KEY]) == 1


def test_terminal_reaction_is_not_overwritten_by_a_tick(monkeypatch):
    """End to end: a terminal reaction owns the slot, so the tick is dropped at
    the drain and never reaches Telethon."""
    from bridge.liveness_ticks import record_slot_owner
    from bridge.telegram_relay import _reaction_yields_slot, _send_queued_reaction

    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)
    _publish(_make_running_session(HEARTBEAT_TICK_INTERVAL_SECONDS + 5))
    payload = json.loads(fake.lists[OUTBOX_KEY][0])

    record_slot_owner("-100", 42, PRIORITY_TERMINAL)
    assert _reaction_yields_slot(payload) is True

    # Control: absent the guard the same payload would have hit the API.
    fake_set_reaction = AsyncMock(return_value=True)
    with patch("bridge.response.set_reaction", fake_set_reaction):
        asyncio.run(_send_queued_reaction(telegram_client=object(), message=payload))
    fake_set_reaction.assert_awaited_once()
