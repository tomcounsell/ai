"""Tests for agent/steering.py — self-draft attempt budget helpers.

Covers:
- bump_self_draft_attempts: atomic increment, TTL wiring, distinct post-increment values
- reset_self_draft_attempts: deletes the key
"""

from unittest.mock import patch


class TestSelfDraftAttempts:
    """Tests for bump_self_draft_attempts and reset_self_draft_attempts."""

    def _make_fake_redis(self):
        """Return a fake Redis that mimics INCR / EXPIRE / DELETE for the counter."""
        store = {}

        class FakeRedis:
            def incr(self, key):
                store[key] = store.get(key, 0) + 1
                return store[key]

            def expire(self, key, ttl):
                # Record that expire was called (not needed for logic, but allows assertion).
                self._expire_calls = getattr(self, "_expire_calls", [])
                self._expire_calls.append((key, ttl))

            def delete(self, key):
                store.pop(key, None)

            def get(self, key):
                v = store.get(key)
                return str(v).encode() if v is not None else None

        return FakeRedis(), store

    def test_bump_returns_post_increment_value(self):
        """First bump returns 1, second returns 2, third returns 3."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            assert bump_self_draft_attempts("sess-1") == 1
            assert bump_self_draft_attempts("sess-1") == 2
            assert bump_self_draft_attempts("sess-1") == 3

    def test_bump_ttl_set_only_on_first_bump(self):
        """TTL is set on the first bump (count==1) and not on subsequent bumps."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import _SELF_DRAFT_ATTEMPTS_TTL, bump_self_draft_attempts

            bump_self_draft_attempts("sess-ttl")
            assert len(fake_r._expire_calls) == 1
            assert fake_r._expire_calls[0][1] == _SELF_DRAFT_ATTEMPTS_TTL

            bump_self_draft_attempts("sess-ttl")
            # expire should NOT have been called again
            assert len(fake_r._expire_calls) == 1

    def test_bump_distinct_values_under_sequential_calls(self):
        """Sequential bumps produce distinct, monotonically increasing values.

        Goal: self_draft_attempts_atomic_increment requirement from the plan.
        The fake-Redis test validates the functional contract; the production
        Redis INCR is genuinely atomic (documented by Redis).
        """
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            results = [bump_self_draft_attempts("sess-atomic") for _ in range(5)]

        assert results == [1, 2, 3, 4, 5], "Each bump must return a unique, increasing value"

    def test_reset_deletes_key(self):
        """reset_self_draft_attempts deletes the key so the next bump starts at 1."""
        fake_r, store = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts, reset_self_draft_attempts

            bump_self_draft_attempts("sess-reset")
            bump_self_draft_attempts("sess-reset")
            # Key is present at 2
            key = "steering:attempts:sess-reset"
            assert store[key] == 2

            reset_self_draft_attempts("sess-reset")
            # Key is gone
            assert key not in store

            # After reset, next bump starts fresh at 1
            result = bump_self_draft_attempts("sess-reset")
            assert result == 1

    def test_counters_are_per_session_independent(self):
        """Bumps for different sessions do not interfere."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            assert bump_self_draft_attempts("sess-A") == 1
            assert bump_self_draft_attempts("sess-B") == 1
            assert bump_self_draft_attempts("sess-A") == 2
            assert bump_self_draft_attempts("sess-B") == 2

    def test_self_draft_max_attempts_constant(self):
        """SELF_DRAFT_MAX_ATTEMPTS is 2 (matches the plan spec)."""
        from agent.steering import SELF_DRAFT_MAX_ATTEMPTS

        assert SELF_DRAFT_MAX_ATTEMPTS == 2
