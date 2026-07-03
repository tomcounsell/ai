"""Unit tests for the transient cancel-reason signal (#1877 defect #1).

Contract:
  * unset key → get returns None (send site defaults to resume copy)
  * set/get round-trip returns the written reason
  * the read is NON-destructive (no pop/delete; repeated reads keep returning it)
  * Redis unavailable → both helpers swallow the error; get returns None, never raises
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.cancel_reason import get_cancel_reason, set_cancel_reason


class _FakeRedis:
    """Minimal redis stand-in returning bytes from get (like real redis)."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def set(self, key, value, ex=None, nx=False):
        self.store[key] = value.encode() if isinstance(value, str) else value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        for k in keys:
            self.deleted.append(k)
            self.store.pop(k, None)


def test_unset_reason_returns_none():
    fake = _FakeRedis()
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        assert get_cancel_reason("sess-unset") is None


def test_set_get_round_trip():
    fake = _FakeRedis()
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        set_cancel_reason("sess-1", "no_resume")
        assert get_cancel_reason("sess-1") == "no_resume"
        set_cancel_reason("sess-2", "resume")
        assert get_cancel_reason("sess-2") == "resume"


def test_read_is_non_destructive():
    """Repeated reads keep returning the value — the key is never deleted."""
    fake = _FakeRedis()
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        set_cancel_reason("sess-nd", "no_resume")
        assert get_cancel_reason("sess-nd") == "no_resume"
        assert get_cancel_reason("sess-nd") == "no_resume"
        assert get_cancel_reason("sess-nd") == "no_resume"
    assert fake.deleted == []  # nothing was ever deleted


def test_set_uses_ttl_and_no_nx():
    """set_cancel_reason writes with a TTL and unconditionally (overwrites)."""
    fake = MagicMock()
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        set_cancel_reason("sess-ttl", "no_resume", ttl=180)
    fake.set.assert_called_once()
    args, kwargs = fake.set.call_args
    assert kwargs.get("ex") == 180
    assert not kwargs.get("nx")  # must overwrite, not conditional


def test_get_swallows_redis_error_returns_none():
    """A raising Redis must not propagate into the CancelledError handler."""
    fake = MagicMock()
    fake.get.side_effect = RuntimeError("redis down")
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        assert get_cancel_reason("sess-err") is None


def test_set_swallows_redis_error():
    """set_cancel_reason never raises even if Redis is unavailable."""
    fake = MagicMock()
    fake.set.side_effect = RuntimeError("redis down")
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        set_cancel_reason("sess-err", "no_resume")  # must not raise


def test_empty_session_id_is_noop():
    fake = MagicMock()
    with patch("popoto.redis_db.POPOTO_REDIS_DB", fake):
        set_cancel_reason("", "no_resume")
        assert get_cancel_reason("") is None
    fake.set.assert_not_called()
    fake.get.assert_not_called()
