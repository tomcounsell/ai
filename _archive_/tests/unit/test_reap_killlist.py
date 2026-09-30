"""Unit tests for the durable wedge-reap kill-list (issue #2146).

Covers ``agent.reap_killlist``:
  - ``add`` persists survivors to the Redis hash (best-effort, fail-silent).
  - ``drain_and_kill`` SIGKILLs a still-live PID whose ``create_time`` matches
    the stored value, and REMOVES the entry (one-shot drain).
  - ``drain_and_kill`` SKIPS (never kills) a PID whose ``create_time`` differs
    (PID recycle guard) yet still removes the stale entry.
  - All paths are fail-silent when Redis is unavailable.

No real processes and no real Redis: the module's ``_redis`` seam is patched to
a hash-backed fake, and the ``kill_fn`` / ``proc_ctime_fn`` drain seams are fakes.
"""

from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

from agent import reap_killlist


class FakeRedis:
    """Minimal hash-backed Redis stub for the kill-list (str fields/values)."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[str(field)] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, field):
        self.hashes.get(key, {}).pop(str(field), None)

    def expire(self, key, ttl):
        self.expires[key] = ttl


@pytest.fixture
def fake_redis():
    r = FakeRedis()
    with patch("agent.reap_killlist._redis", return_value=r):
        yield r


def test_add_persists_entries_and_sets_ttl(fake_redis):
    written = reap_killlist.add([(501, 111.0, 4242, "sess-abc")])
    assert written == 1
    stored = fake_redis.hgetall("valor:reap:killlist")
    assert "501" in stored
    import json

    entry = json.loads(stored["501"])
    assert entry["pid"] == 501
    assert entry["create_time"] == 111.0
    assert entry["pgid"] == 4242
    assert entry["session_ref"] == "sess-abc"
    # TTL applied so a never-rebooting machine does not accumulate entries.
    assert fake_redis.expires.get("valor:reap:killlist")


def test_add_empty_is_noop(fake_redis):
    assert reap_killlist.add([]) == 0
    assert fake_redis.hgetall("valor:reap:killlist") == {}


def test_drain_kills_matching_create_time_and_removes_entry(fake_redis):
    reap_killlist.add([(501, 111.0, 4242, "sess-abc")])

    kills: list = []
    killed = reap_killlist.drain_and_kill(
        kill_fn=lambda pid, sig: kills.append((pid, sig)),
        proc_ctime_fn=lambda pid: 111.0,  # live create_time matches stored
    )
    assert killed == 1
    assert kills == [(501, signal.SIGKILL)]
    # One-shot drain: entry removed.
    assert fake_redis.hgetall("valor:reap:killlist") == {}


def test_drain_skips_recycled_pid_but_still_removes_entry(fake_redis):
    """create_time mismatch = the PID was recycled to an unrelated process →
    NEVER kill it, but discard the stale entry (recycle safety, the Downstream
    'never target outside the recorded subtree' constraint)."""
    reap_killlist.add([(501, 111.0, 4242, "sess-abc")])

    kills: list = []
    killed = reap_killlist.drain_and_kill(
        kill_fn=lambda pid, sig: kills.append((pid, sig)),
        proc_ctime_fn=lambda pid: 999.0,  # different create_time → recycled
    )
    assert killed == 0
    assert kills == []  # no collateral kill
    assert fake_redis.hgetall("valor:reap:killlist") == {}  # stale entry discarded


def test_drain_skips_dead_pid_and_removes_entry(fake_redis):
    reap_killlist.add([(501, 111.0, 4242, "sess-abc")])

    kills: list = []
    killed = reap_killlist.drain_and_kill(
        kill_fn=lambda pid, sig: kills.append((pid, sig)),
        proc_ctime_fn=lambda pid: None,  # process gone
    )
    assert killed == 0
    assert kills == []
    assert fake_redis.hgetall("valor:reap:killlist") == {}


def test_drain_empty_list_is_noop(fake_redis):
    assert reap_killlist.drain_and_kill(kill_fn=lambda p, s: None) == 0


def test_add_fail_silent_when_redis_unavailable():
    with patch("agent.reap_killlist._redis", side_effect=RuntimeError("no redis")):
        # Must not raise — a reap teardown cannot crash on persistence.
        assert reap_killlist.add([(501, 111.0, 4242, "sess-abc")]) == 0


def test_drain_fail_silent_when_redis_unavailable():
    with patch("agent.reap_killlist._redis", side_effect=RuntimeError("no redis")):
        assert reap_killlist.drain_and_kill(kill_fn=lambda p, s: None) == 0
