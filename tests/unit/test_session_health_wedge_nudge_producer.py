"""Unit tests for the wedge-nudge health-loop producer (issue #1879, Task 3).

Covers ``_wedge_nudge_eligible`` (the PTY-transport + frozen-frame gate
predicate) and ``_maybe_push_wedge_nudge`` (the orchestration function wired
into the running-scan's ``in_scope_handle is not None`` sibling branch,
alongside the pre-existing ``in_scope_handle is None`` #944 orphan elif).

This is the producer half of the mid-run steering drain / continue-nudge
recovery rung documented in
``docs/plans/granite-mid-run-steering-drain-continue-nudge.md``. Task 1
(``agent/steering.py``) already added ``push_wedge_nudge`` /
``set_wedge_nudge_latch`` / ``has_wedge_nudge_latch``, exercised directly by
``tests/unit/test_steering.py::TestWedgeNudgeChannel``. This file is
scoped to the *producer* gate + orchestration logic in
``agent/session_health.py`` only.

Critical invariant under test throughout: the wedge-nudge branch takes NO
recovery action. It must never set ``should_recover``, never call
``_apply_recovery_transition``, and never touch ``recovery_attempts`` — see
the #1820 ownership-boundary reconciliation in the plan (BLOCKER r1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.session_health import (
    NUDGE_WEDGE_THRESHOLD_S,
    WEDGE_NUDGE_LATCH_TTL_S,
    _maybe_push_wedge_nudge,
    _wedge_nudge_eligible,
)

# ---------------------------------------------------------------------------
# _wedge_nudge_eligible — pure predicate
# ---------------------------------------------------------------------------


def _entry(*, last_pty_read_loop_at, last_pty_activity_at, project_key="test-wedge-nudge"):
    return SimpleNamespace(
        agent_session_id="sess-eligible",
        project_key=project_key,
        last_pty_read_loop_at=last_pty_read_loop_at,
        last_pty_activity_at=last_pty_activity_at,
    )


def test_eligible_when_pty_transport_and_frame_frozen_past_threshold():
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=now - timedelta(seconds=5),
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S + 1),
    )
    assert _wedge_nudge_eligible(entry, now) is True


def test_not_eligible_when_frame_still_fresh():
    """A genuinely active turn keeps last_pty_activity_at fresh — never a nudge target."""
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=now - timedelta(seconds=5),
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S - 1),
    )
    assert _wedge_nudge_eligible(entry, now) is False


def test_not_eligible_for_non_pty_session():
    """last_pty_read_loop_at is None (SDK/headless) — must never be nudge-eligible."""
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=None,
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S + 100),
    )
    assert _wedge_nudge_eligible(entry, now) is False


def test_not_eligible_when_activity_field_missing():
    now = datetime.now(tz=UTC)
    entry = _entry(last_pty_read_loop_at=now - timedelta(seconds=5), last_pty_activity_at=None)
    assert _wedge_nudge_eligible(entry, now) is False


def test_eligible_exactly_at_threshold_boundary_is_false():
    """Strictly greater-than semantics: exactly at the threshold is not yet eligible."""
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=now - timedelta(seconds=5),
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S),
    )
    assert _wedge_nudge_eligible(entry, now) is False


# ---------------------------------------------------------------------------
# _maybe_push_wedge_nudge — orchestration (mocked steering + redis counter)
# ---------------------------------------------------------------------------


class _FakeRedisCounter:
    """Minimal fake supporting .incr(key) with a call log."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def _wedged_entry(project_key="test-wedge-nudge"):
    now = datetime.now(tz=UTC)
    return _entry(
        last_pty_read_loop_at=now - timedelta(seconds=5),
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S + 1),
        project_key=project_key,
    )


def test_maybe_push_wedge_nudge_pushes_and_increments_telemetry_on_match():
    entry = _wedged_entry()
    now = datetime.now(tz=UTC)
    fake_redis = _FakeRedisCounter()

    with (
        patch("agent.steering.push_wedge_nudge") as mock_push,
        patch("agent.steering.set_wedge_nudge_latch", return_value=True) as mock_latch,
        patch("popoto.redis_db.POPOTO_REDIS_DB", fake_redis),
    ):
        result = _maybe_push_wedge_nudge(entry, now)

    assert result is True
    mock_push.assert_called_once_with("sess-eligible")
    mock_latch.assert_called_once_with("sess-eligible", ttl_seconds=WEDGE_NUDGE_LATCH_TTL_S)
    assert fake_redis.counts == {"test-wedge-nudge:session-health:wedge_nudge_sent": 1}


def test_maybe_push_wedge_nudge_no_push_when_not_eligible():
    """Fresh frame (not frozen) — the gate must reject before ever touching the latch."""
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=now - timedelta(seconds=5),
        last_pty_activity_at=now - timedelta(seconds=1),
    )

    with (
        patch("agent.steering.push_wedge_nudge") as mock_push,
        patch("agent.steering.set_wedge_nudge_latch") as mock_latch,
    ):
        result = _maybe_push_wedge_nudge(entry, now)

    assert result is False
    mock_push.assert_not_called()
    mock_latch.assert_not_called()


def test_maybe_push_wedge_nudge_no_push_for_non_pty_session():
    """Non-PTY (last_pty_read_loop_at=None) — must never push regardless of staleness."""
    now = datetime.now(tz=UTC)
    entry = _entry(
        last_pty_read_loop_at=None,
        last_pty_activity_at=now - timedelta(seconds=NUDGE_WEDGE_THRESHOLD_S + 500),
    )

    with (
        patch("agent.steering.push_wedge_nudge") as mock_push,
        patch("agent.steering.set_wedge_nudge_latch") as mock_latch,
    ):
        result = _maybe_push_wedge_nudge(entry, now)

    assert result is False
    mock_push.assert_not_called()
    mock_latch.assert_not_called()


def test_maybe_push_wedge_nudge_no_push_when_latch_already_held():
    """set_wedge_nudge_latch returning False means a latch is already held this window."""
    entry = _wedged_entry()
    now = datetime.now(tz=UTC)
    fake_redis = _FakeRedisCounter()

    with (
        patch("agent.steering.push_wedge_nudge") as mock_push,
        patch("agent.steering.set_wedge_nudge_latch", return_value=False) as mock_latch,
        patch("popoto.redis_db.POPOTO_REDIS_DB", fake_redis),
    ):
        result = _maybe_push_wedge_nudge(entry, now)

    assert result is False
    mock_latch.assert_called_once()
    mock_push.assert_not_called()
    assert fake_redis.counts == {}


def test_maybe_push_wedge_nudge_is_fail_silent_on_raising_push():
    """A raising push_wedge_nudge must not propagate — the tick must survive."""
    entry = _wedged_entry()
    now = datetime.now(tz=UTC)

    with (
        patch("agent.steering.push_wedge_nudge", side_effect=RuntimeError("boom")),
        patch("agent.steering.set_wedge_nudge_latch", return_value=True),
    ):
        result = _maybe_push_wedge_nudge(entry, now)  # must not raise

    assert result is False


def test_maybe_push_wedge_nudge_counter_failure_does_not_block_or_raise():
    """A raising Redis counter increment must not prevent the function from returning True."""
    entry = _wedged_entry()
    now = datetime.now(tz=UTC)

    class _RaisingRedis:
        def incr(self, key):
            raise RuntimeError("redis down")

    with (
        patch("agent.steering.push_wedge_nudge") as mock_push,
        patch("agent.steering.set_wedge_nudge_latch", return_value=True),
        patch("popoto.redis_db.POPOTO_REDIS_DB", _RaisingRedis()),
    ):
        result = _maybe_push_wedge_nudge(entry, now)

    assert result is True
    mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# One-nudge-per-window — real steering latch (fake Redis), no mocking of
# push/latch internals, proving the durable TTL latch (not a mock) enforces
# "at most one nudge per turn-wait window" (Risk 3 / Race 2 in the plan).
# ---------------------------------------------------------------------------


class _FakeSteeringRedis:
    """Fake Redis supporting list ops (nudge channel) and SET NX EX / EXISTS (latch),
    plus .incr() for the telemetry counter — mirrors
    tests/unit/test_steering.py::TestWedgeNudgeChannel._make_fake_redis, extended
    with .incr() so the same fake backs both the steering channel/latch and the
    session-health telemetry counter in one patch target.
    """

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        bucket = self.lists.get(key)
        if not bucket:
            return None
        return bucket.pop(0)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def exists(self, key):
        return 1 if key in self.kv else 0

    def delete(self, key):
        self.kv.pop(key, None)
        self.lists.pop(key, None)

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def test_one_nudge_per_window_second_tick_within_ttl_pushes_nothing():
    """Two producer ticks within the latch TTL window on the same session:
    only the first pushes a nudge; the second finds the latch already held
    and does nothing further (real push_wedge_nudge/set_wedge_nudge_latch,
    fake Redis backing both)."""
    entry = _wedged_entry(project_key="test-wedge-nudge-window")
    now = datetime.now(tz=UTC)
    fake_r = _FakeSteeringRedis()

    with (
        patch("agent.steering._get_redis", return_value=fake_r),
        patch("popoto.redis_db.POPOTO_REDIS_DB", fake_r),
    ):
        from agent.steering import pop_wedge_nudges

        first_result = _maybe_push_wedge_nudge(entry, now)
        second_result = _maybe_push_wedge_nudge(entry, now)

        assert first_result is True
        assert second_result is False

        # Exactly one nudge landed on the signal channel.
        nudges = pop_wedge_nudges(entry.agent_session_id)
        assert len(nudges) == 1
        assert nudges[0]["text"] == "continue"

        # Telemetry counter incremented exactly once, not twice.
        assert fake_r.counts == {"test-wedge-nudge-window:session-health:wedge_nudge_sent": 1}


# ---------------------------------------------------------------------------
# Running-scan wiring: the elif branch never touches should_recover /
# recovery_attempts / _apply_recovery_transition (#1820 reconciliation).
# ---------------------------------------------------------------------------


def test_running_scan_branch_source_has_no_recovery_action():
    """Static guard: the new sibling branch's call site must not appear inside
    any code path that also sets should_recover=True or increments
    recovery_attempts. This pins the #1820 BLOCKER-level invariant at the
    source-inspection level, complementing the behavioral tests above."""
    import inspect

    import agent.session_health as session_health

    src = inspect.getsource(session_health._agent_session_health_check)
    assert "elif in_scope_handle is not None:" in src
    assert "_maybe_push_wedge_nudge(entry, now)" in src

    # Extract just the new branch's block (from the elif to the next
    # unindented `if should_recover:` guard) and assert it never sets
    # should_recover or touches recovery_attempts.
    marker = "elif in_scope_handle is not None:"
    start = src.index(marker)
    end = src.index("if should_recover:", start)
    branch_src = src[start:end]
    assert "should_recover = True" not in branch_src
    assert "recovery_attempts" not in branch_src
    assert "_apply_recovery_transition" not in branch_src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
