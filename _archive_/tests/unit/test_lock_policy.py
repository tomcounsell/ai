"""Each coordination lock states its policy and counts its degradation (#3183).

Three locks gate the pipeline and all three used to fail open silently. Two
of them are right to; the third is not, because two ``claude -p`` processes on
one worktree corrupt git state. These tests pin the declared policy of each,
the branch that counts, and — the one behaviour change — that the run claim
now fails closed with no override.
"""

from unittest.mock import patch

import pytest

from agent import lock_policy


class FakeRedis:
    def __init__(self):
        self.hash: dict[str, dict[str, int]] = {}

    def hincrby(self, key, field, amount):
        self.hash.setdefault(key, {})
        self.hash[key][field] = self.hash[key].get(field, 0) + amount
        return self.hash[key][field]

    def hgetall(self, key):
        return {k: str(v) for k, v in self.hash.get(key, {}).items()}


@pytest.fixture
def counter(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("utils.redis_client.text_redis", lambda: fake)
    return fake


class TestDeclaredPolicies:
    def test_every_lock_declares_one(self):
        assert lock_policy.LOCK_POLICIES == {
            "pop_lock": "open",
            "claim_message": "open",
            "claim_pending_run": "closed",
        }

    def test_the_counter_field_carries_the_policy(self, counter):
        """The tile renders the count beside the policy it was taken under."""
        lock_policy.record_lock_degradation("pop_lock", "open", project_key="valor")
        assert counter.hash["valor:locks:degraded"] == {"pop_lock:open": 1}

    def test_the_counter_never_raises(self, monkeypatch):
        """A counter write must not be able to change a lock's answer."""

        class _Down:
            def hincrby(self, *args, **kwargs):
                raise ConnectionError("redis down")

        monkeypatch.setattr("utils.redis_client.text_redis", lambda: _Down())
        assert lock_policy.record_lock_degradation("pop_lock", "open") is None

    def test_reading_counts_never_raises(self, monkeypatch):
        class _Down:
            def hgetall(self, *args, **kwargs):
                raise ConnectionError("redis down")

        monkeypatch.setattr("utils.redis_client.text_redis", lambda: _Down())
        assert lock_policy.degradation_counts() == {}


class TestPopLockFailsOpen:
    def test_redis_error_returns_true_and_counts(self, counter):
        """Duplicate work beats a stalled queue."""
        from agent.session_pickup import _acquire_pop_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as redis:
            redis.set.side_effect = ConnectionError("redis down")
            assert _acquire_pop_lock("worker-1") is True
        assert counter.hash[lock_policy.degraded_key(None)] == {"pop_lock:open": 1}


class TestClaimMessageFailsOpen:
    @pytest.mark.asyncio
    async def test_redis_error_returns_true_and_counts(self, counter):
        """A Redis hiccup must not silently drop a message."""
        from bridge.dedup import claim_message

        with patch("bridge.dedup._get_redis") as get_redis:
            get_redis.side_effect = ConnectionError("redis down")
            assert await claim_message("chat-1", 5) is True
        assert counter.hash[lock_policy.degraded_key(None)] == {"claim_message:open": 1}


class TestRunClaimFailsClosed:
    def test_redis_error_returns_false_and_counts(self, counter):
        """The behaviour change: a Redis error must NOT hand out the claim.

        Failing open here degrades to CAS-only protection, which does not stop
        two actors from starting a `claude -p` on the same worktree.
        """
        from models.session_lifecycle import claim_pending_run

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as redis:
            redis.set.side_effect = ConnectionError("redis down")
            assert claim_pending_run("sess-1", "worker-1") is False
        assert counter.hash[lock_policy.degraded_key(None)] == {"claim_pending_run:closed": 1}

    def test_a_won_claim_still_returns_true(self, counter):
        from models.session_lifecycle import claim_pending_run

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as redis:
            redis.set.return_value = True
            assert claim_pending_run("sess-1", "worker-1") is True
        assert counter.hash == {}, "a healthy acquisition is not a degradation"

    def test_a_lost_claim_returns_false_without_counting(self, counter):
        from models.session_lifecycle import claim_pending_run

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as redis:
            redis.set.return_value = None
            assert claim_pending_run("sess-1", "worker-1") is False
        assert counter.hash == {}, "losing a race is the lock working, not degrading"

    def test_no_break_glass_exists(self):
        """Owner decision: fail-closed is the only behaviour.

        An override would reintroduce exactly the risk the flip removes, so
        the absence is the criterion.
        """
        import inspect

        from models import session_lifecycle

        source = inspect.getsource(session_lifecycle.claim_pending_run)
        assert "run_claim_fail_open" not in source
        assert "return bool(acquired)" in source, "the happy path must still grant the claim"


class TestDashboardRows:
    def test_every_lock_renders_even_at_zero(self, counter):
        from ui.data.locks import get_lock_policies

        rows = get_lock_policies()
        assert {row["name"] for row in rows} == set(lock_policy.LOCK_POLICIES)
        assert all(row["count"] == 0 for row in rows)

    def test_fail_closed_sorts_first(self, counter):
        from ui.data.locks import get_lock_policies

        assert get_lock_policies()[0]["name"] == "claim_pending_run"

    def test_counts_are_joined_to_their_lock(self, counter):
        from ui.data.locks import get_lock_policies

        lock_policy.record_lock_degradation("claim_message", "open")
        rows = {row["name"]: row["count"] for row in get_lock_policies()}
        assert rows["claim_message"] == 1
        assert rows["pop_lock"] == 0
