"""Integration test: stalled-session detection -> outbox handoff to bridge relay.

Issue #1313. Drives one watchdog tick against a stale-pending fixture session
and asserts the reaction payload lands in ``telegram:outbox:{session_id}`` with
the right schema, then asserts ``bridge.telegram_relay._send_queued_reaction``
recognizes the payload shape (called as a unit with a stub Telethon client).

Uses an in-memory Redis stub so this test does not depend on a live Redis
instance and never touches production keys.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _FakeRedis:
    """Minimal in-memory Redis stub matching the `set NX EX`, `rpush`,
    `expire`, `delete` surface used by the watchdog."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

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


def _make_stale_pending_session(session_id="tg_user_-100_42"):
    """Build a SimpleNamespace mimicking AgentSession for a stale-pending fixture."""
    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id="as-001",
        status="pending",
        # Stale: created well past the 300s pending threshold.
        started_at=None,
        created_at=now - 600,
        updated_at=now - 600,
        project_key="testproj",
        chat_id="-100",
        # initial_telegram_message provides telegram_message_id via property,
        # but SimpleNamespace can expose it directly.
        telegram_message_id=42,
    )


def _query_filter_for(sessions_by_status):
    def filter_fn(**kwargs):
        return sessions_by_status.get(kwargs.get("status", ""), [])

    return SimpleNamespace(filter=filter_fn)


def test_watchdog_tick_writes_reaction_to_outbox(monkeypatch):
    """A stalled pending session triggers a reaction payload in the outbox."""
    from monitoring import session_watchdog

    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)
    monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)

    session = _make_stale_pending_session()

    # The session needs `_get_history_list` (read by check_stalled_sessions).
    session._get_history_list = lambda: []

    sessions_by_status = {"pending": [session], "running": [], "active": []}
    with patch(
        "monitoring.session_watchdog.AgentSession.query",
        _query_filter_for(sessions_by_status),
    ):
        stalled = session_watchdog.check_stalled_sessions()

    # Watchdog must have detected the stall.
    assert any(s["session_id"] == "tg_user_-100_42" for s in stalled)

    # And queued exactly one reaction payload to the session's outbox.
    queue_key = "telegram:outbox:tg_user_-100_42"
    assert queue_key in fake.lists
    assert len(fake.lists[queue_key]) == 1

    payload = json.loads(fake.lists[queue_key][0])
    assert payload["type"] == "reaction"
    assert payload["chat_id"] == "-100"
    assert payload["reply_to"] == 42
    assert payload["emoji"] == "⏳"
    assert payload["session_id"] == "tg_user_-100_42"
    assert "timestamp" in payload

    # Outbox TTL applied so the relay-down case eventually clears.
    assert fake.expires[queue_key] == 3600

    # Dedup key claimed.
    assert "watchdog:stall_reaction_applied:tg_user_-100_42" in fake.store


def test_relay_send_queued_reaction_accepts_watchdog_payload(monkeypatch):
    """The bridge's _send_queued_reaction must accept the watchdog's payload shape.

    Patches `bridge.response.set_reaction` to avoid a real Telethon call; the
    test only verifies the payload validation/parse path returns True.
    """
    from bridge.telegram_relay import _send_queued_reaction
    from monitoring import session_watchdog

    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)
    monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)

    session = _make_stale_pending_session()
    assert session_watchdog._apply_stall_reaction(session) is True

    queue_key = "telegram:outbox:tg_user_-100_42"
    payload = json.loads(fake.lists[queue_key][0])

    fake_set_reaction = AsyncMock(return_value=True)
    with patch("bridge.response.set_reaction", fake_set_reaction):
        result = asyncio.run(_send_queued_reaction(telegram_client=object(), message=payload))

    assert result is True
    fake_set_reaction.assert_awaited_once()
    # Verify the relay parsed chat_id, reply_to, emoji correctly.
    args, kwargs = fake_set_reaction.call_args
    # set_reaction(client, chat_id, msg_id, emoji)
    assert args[1] == -100
    assert args[2] == 42
    assert args[3] == "⏳"


def test_second_tick_does_not_double_queue(monkeypatch):
    """Two ticks observing the same stalled session queue the reaction once."""
    from monitoring import session_watchdog

    fake = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fake)
    monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)

    session = _make_stale_pending_session()
    session._get_history_list = lambda: []

    sessions_by_status = {"pending": [session], "running": [], "active": []}
    with patch(
        "monitoring.session_watchdog.AgentSession.query",
        _query_filter_for(sessions_by_status),
    ):
        session_watchdog.check_stalled_sessions()
        session_watchdog.check_stalled_sessions()

    queue_key = "telegram:outbox:tg_user_-100_42"
    assert len(fake.lists[queue_key]) == 1
