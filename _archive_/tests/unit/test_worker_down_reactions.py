"""Unit tests for the worker-down (⚠) reaction record→clear lifecycle (issue #2178).

The bridge records which messages got a worker-down warning; when the worker
recovers and drains the session it replaces the ⚠ with the normal processing
reaction via the ``telegram:outbox:{session_id}`` relay reach-back.
"""

import json

import pytest

import agent.worker_down_reactions as wdr
from agent.worker_down_reactions import (
    WORKER_DOWN_REACTIONS_KEY_PREFIX,
    clear_worker_down_reactions,
    record_worker_down_reaction,
)


class _FakeRedis:
    """Minimal in-memory Redis stand-in for list ops used by the module."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    def delete(self, key):
        existed = key in self.lists
        self.lists.pop(key, None)
        self.expires.pop(key, None)
        return 1 if existed else 0


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(wdr, "_redis", lambda: fake)
    # Pin the recovery emoji so the test does not depend on bridge.response import.
    monkeypatch.setattr(wdr, "_recovered_emoji", lambda: "✍")
    return fake


def test_record_then_clear_round_trip(fake_redis):
    session_id = "tg_test_-100_42"
    tracking_key = f"{WORKER_DOWN_REACTIONS_KEY_PREFIX}{session_id}"

    assert record_worker_down_reaction(session_id, chat_id=-100, message_id=11) is True
    assert record_worker_down_reaction(session_id, chat_id=-100, message_id=22) is True

    # Tracking list holds both warned messages with a TTL.
    assert len(fake_redis.lists[tracking_key]) == 2
    assert tracking_key in fake_redis.expires

    queued = clear_worker_down_reactions(session_id)
    assert queued == 2

    # Tracking key is gone; two replacement reactions landed on the outbox.
    assert tracking_key not in fake_redis.lists
    outbox_key = f"telegram:outbox:{session_id}"
    payloads = [json.loads(p) for p in fake_redis.lists[outbox_key]]
    assert len(payloads) == 2
    for payload, msg_id in zip(payloads, (11, 22), strict=True):
        assert payload["type"] == "reaction"
        assert payload["chat_id"] == "-100"
        assert payload["reply_to"] == msg_id
        assert payload["emoji"] == "✍"
        assert payload["session_id"] == session_id


def test_clear_unknown_session_is_noop(fake_redis):
    assert clear_worker_down_reactions("tg_never_recorded") == 0
    assert fake_redis.lists == {}


def test_clear_empty_session_id_is_noop(fake_redis):
    assert clear_worker_down_reactions("") == 0


def test_record_rejects_bad_inputs(fake_redis):
    assert record_worker_down_reaction("", chat_id=1, message_id=1) is False
    assert record_worker_down_reaction("sid", chat_id=None, message_id=1) is False
    assert record_worker_down_reaction("sid", chat_id=1, message_id=None) is False
    assert fake_redis.lists == {}


def test_replacement_emoji_is_non_falsy_for_relay(fake_redis):
    """The relay drops reaction payloads with a falsy emoji; the replacement
    must always be a non-empty glyph so the ⚠ actually gets overwritten."""
    session_id = "tg_relay_guard_1"
    record_worker_down_reaction(session_id, chat_id=5, message_id=7)
    clear_worker_down_reactions(session_id)
    payload = json.loads(fake_redis.lists[f"telegram:outbox:{session_id}"][0])
    assert payload["emoji"]  # non-empty — passes the relay's `not emoji` guard
