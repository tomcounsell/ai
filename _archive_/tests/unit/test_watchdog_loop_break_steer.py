"""Unit tests for monitoring/session_watchdog.py::_inject_watchdog_steer (issue #1128).

Validates the detection→steering actuator introduced by this plan:

- Repetition / error_cascade / token_alert call sites push exactly ONE
  steer per cooldown window via an atomic Redis `SET NX EX`.
- A second call within the cooldown window is suppressed.
- Each reason has its own cooldown key so parallel detections don't
  squelch each other.
- `sender="watchdog"` is persisted on the pushed message envelope.
- `WATCHDOG_AUTO_STEER_ENABLED=false` suppresses without crashing.
- Redis / steering failures are fail-quiet (watchdog loop keeps running).
"""

from __future__ import annotations

import time

import pytest

from agent.steering import clear_steering_queue, pop_all_steering_messages
from monitoring.session_watchdog import (
    STEER_COOLDOWN,
    TOKEN_ALERT_COOLDOWN,
    _inject_watchdog_steer,
)


def _db():
    """Return the live test-patched Redis client via module lookup.

    conftest's `redis_test_db` fixture rebinds
    `popoto.redis_db.POPOTO_REDIS_DB` AFTER module import. A module-level
    `from popoto.redis_db import POPOTO_REDIS_DB` would capture the pre-
    patch production client — always look up the module attribute.
    """
    import popoto.redis_db as _rdb

    return _rdb.POPOTO_REDIS_DB


@pytest.fixture(autouse=True)
def _clear_cooldown_keys():
    """Purge any lingering cooldown keys between tests."""
    db = _db()
    for key in db.scan_iter("watchdog:steer_cooldown:*"):
        db.delete(key)
    yield
    db = _db()
    for key in db.scan_iter("watchdog:steer_cooldown:*"):
        db.delete(key)


@pytest.fixture()
def test_session_id():
    sid = f"watchdog-steer-{int(time.time() * 1000)}"
    clear_steering_queue(sid)
    yield sid
    clear_steering_queue(sid)


class TestCooldownAtomicity:
    def test_first_call_pushes_steer(self, test_session_id):
        assert (
            _inject_watchdog_steer(
                test_session_id,
                "repetition",
                "please stop looping",
                cooldown_seconds=STEER_COOLDOWN,
            )
            is True
        )
        msgs = pop_all_steering_messages(test_session_id)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "please stop looping"
        assert msgs[0]["sender"] == "watchdog"

    def test_second_call_suppressed_within_cooldown(self, test_session_id):
        assert _inject_watchdog_steer(test_session_id, "repetition", "first") is True
        assert _inject_watchdog_steer(test_session_id, "repetition", "second") is False
        # Only one message on the queue
        msgs = pop_all_steering_messages(test_session_id)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "first"

    def test_cooldown_key_is_reason_scoped(self, test_session_id):
        """A repetition cooldown does NOT suppress an error_cascade steer."""
        assert _inject_watchdog_steer(test_session_id, "repetition", "rep-1") is True
        # Different reason → independent cooldown slot
        assert _inject_watchdog_steer(test_session_id, "error_cascade", "cascade-1") is True
        # Different reason → independent cooldown slot
        assert (
            _inject_watchdog_steer(
                test_session_id, "token_alert", "budget-1", cooldown_seconds=TOKEN_ALERT_COOLDOWN
            )
            is True
        )
        msgs = pop_all_steering_messages(test_session_id)
        assert len(msgs) == 3
        assert {m["text"] for m in msgs} == {"rep-1", "cascade-1", "budget-1"}

    def test_cooldown_key_uses_set_nx_ex(self, test_session_id):
        """Cooldown key is set with a TTL (atomic contract).

        We verify by calling _inject_watchdog_steer and then immediately
        reading the TTL on the same connection — same test, same tx, no
        inter-test Redis flush.
        """
        # First call sets the cooldown key.
        assert _inject_watchdog_steer(test_session_id, "repetition", "hi") is True
        key = f"watchdog:steer_cooldown:repetition:{test_session_id}"
        db = _db()
        # Key must exist immediately after set.
        assert db.exists(key) == 1
        ttl = db.ttl(key)
        # Redis ttl() returns -2 if key doesn't exist, -1 if no TTL, >=0 otherwise.
        # A positive TTL confirms that SET applied `ex=cooldown_seconds`.
        assert ttl > 0


class TestSenderAttribution:
    def test_sender_is_watchdog(self, test_session_id):
        _inject_watchdog_steer(test_session_id, "repetition", "loop!")
        msgs = pop_all_steering_messages(test_session_id)
        assert msgs[0]["sender"] == "watchdog"

    def test_token_alert_message_carries_sender(self, test_session_id):
        _inject_watchdog_steer(
            test_session_id,
            "token_alert",
            "budget",
            cooldown_seconds=TOKEN_ALERT_COOLDOWN,
        )
        msgs = pop_all_steering_messages(test_session_id)
        assert msgs[0]["sender"] == "watchdog"


class TestFeatureGate:
    def test_disabled_env_suppresses(self, test_session_id, monkeypatch):
        monkeypatch.setenv("WATCHDOG_AUTO_STEER_ENABLED", "false")
        assert _inject_watchdog_steer(test_session_id, "repetition", "nope") is False
        msgs = pop_all_steering_messages(test_session_id)
        assert msgs == []

    def test_enabled_when_unset(self, test_session_id, monkeypatch):
        monkeypatch.delenv("WATCHDOG_AUTO_STEER_ENABLED", raising=False)
        assert _inject_watchdog_steer(test_session_id, "repetition", "yes") is True


class TestFailQuiet:
    def test_redis_error_is_logged_not_raised(self, test_session_id, monkeypatch):
        """Redis failures bubble up as a quiet False, not an exception."""
        from monitoring import session_watchdog

        class FakeDB:
            def set(self, *a, **kw):
                raise RuntimeError("redis down")

        import popoto.redis_db as popoto_db_mod

        monkeypatch.setattr(popoto_db_mod, "POPOTO_REDIS_DB", FakeDB())
        # The import inside _inject_watchdog_steer is scoped — monkeypatch
        # just the attribute so the fresh import picks up our fake.
        result = session_watchdog._inject_watchdog_steer(test_session_id, "repetition", "boom")
        assert result is False

    def test_steering_push_error_is_logged_not_raised(self, test_session_id, monkeypatch):
        """If push_steering_message raises, helper returns False quietly."""
        # Force cooldown slot open (no prior key), then make push_steering_message fail.
        import agent.steering as steering_mod

        def boom(*a, **kw):
            raise RuntimeError("steering broken")

        monkeypatch.setattr(steering_mod, "push_steering_message", boom)
        assert _inject_watchdog_steer(test_session_id, "repetition", "fail") is False
