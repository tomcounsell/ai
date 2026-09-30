"""Tests for child priority boost in _pop_agent_session sort key (issue #1004).

Verifies that sessions whose parent is in waiting_for_children status sort
before parentless sessions at the same priority tier, ensuring the child dev
session gets a worker slot instead of being starved by the PM's nudge cycle.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def _make_session(
    priority="normal",
    parent_agent_session_id=None,
    created_at=None,
):
    """Create a mock session object for sort key testing."""
    return SimpleNamespace(
        priority=priority,
        parent_agent_session_id=parent_agent_session_id,
        created_at=created_at or datetime.now(UTC),
    )


class TestChildPriorityBoostSortKey:
    """Test the sort key logic that boosts children of waiting parents."""

    def _sort_key(self, j, parent_waiting_set):
        """Replicate the sort_key function from _pop_agent_session."""
        from agent.agent_session_queue import PRIORITY_RANK

        prio = PRIORITY_RANK.get(j.priority, 2)
        _pid = getattr(j, "parent_agent_session_id", None)
        child_boost = 0 if _pid and _pid in parent_waiting_set else 1
        dt = j.created_at
        if dt is None:
            dt = datetime.min.replace(tzinfo=UTC)
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (prio, child_boost, dt)

    def test_child_of_waiting_parent_sorts_before_parentless(self):
        """Child of waiting_for_children parent sorts before parentless at same priority."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        child = _make_session(priority="normal", parent_agent_session_id="pm-1", created_at=t)
        orphan = _make_session(priority="normal", parent_agent_session_id=None, created_at=t)

        parent_waiting = {"pm-1"}
        assert self._sort_key(child, parent_waiting) < self._sort_key(orphan, parent_waiting)

    def test_child_of_running_parent_no_boost(self):
        """Child of a running (not waiting) parent gets no boost."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        child = _make_session(priority="normal", parent_agent_session_id="pm-2", created_at=t)
        orphan = _make_session(priority="normal", parent_agent_session_id=None, created_at=t)

        parent_waiting = set()  # pm-2 is NOT waiting
        assert self._sort_key(child, parent_waiting) == self._sort_key(orphan, parent_waiting)

    def test_priority_still_dominates_over_boost(self):
        """Higher priority session beats a boosted child at lower priority."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        high_prio = _make_session(priority="high", parent_agent_session_id=None, created_at=t)
        boosted_child = _make_session(
            priority="normal", parent_agent_session_id="pm-1", created_at=t
        )

        parent_waiting = {"pm-1"}
        assert self._sort_key(high_prio, parent_waiting) < self._sort_key(
            boosted_child, parent_waiting
        )

    def test_fifo_preserved_within_boosted_tier(self):
        """Among boosted children at same priority, older session comes first."""
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 2, tzinfo=UTC)
        older = _make_session(priority="normal", parent_agent_session_id="pm-1", created_at=t1)
        newer = _make_session(priority="normal", parent_agent_session_id="pm-1", created_at=t2)

        parent_waiting = {"pm-1"}
        assert self._sort_key(older, parent_waiting) < self._sort_key(newer, parent_waiting)

    def test_fifo_preserved_within_non_boosted_tier(self):
        """Among non-boosted sessions at same priority, older comes first."""
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 1, 2, tzinfo=UTC)
        older = _make_session(priority="normal", created_at=t1)
        newer = _make_session(priority="normal", created_at=t2)

        parent_waiting = set()
        assert self._sort_key(older, parent_waiting) < self._sort_key(newer, parent_waiting)

    @pytest.mark.parametrize("priority", ["critical", "high", "normal", "low"])
    def test_boost_works_at_all_priority_tiers(self, priority):
        """Boost applies within each priority tier, not just normal."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        child = _make_session(priority=priority, parent_agent_session_id="pm-1", created_at=t)
        orphan = _make_session(priority=priority, parent_agent_session_id=None, created_at=t)

        parent_waiting = {"pm-1"}
        assert self._sort_key(child, parent_waiting) < self._sort_key(orphan, parent_waiting)

    def test_full_sort_order_example(self):
        """Full sort of mixed sessions produces expected order."""
        t = datetime(2026, 1, 1, tzinfo=UTC)
        sessions = [
            _make_session(priority="normal", parent_agent_session_id=None, created_at=t),
            _make_session(priority="normal", parent_agent_session_id="pm-1", created_at=t),
            _make_session(priority="high", parent_agent_session_id=None, created_at=t),
        ]
        parent_waiting = {"pm-1"}

        sorted_sessions = sorted(sessions, key=lambda j: self._sort_key(j, parent_waiting))
        # high-prio first, then boosted child, then normal orphan
        assert sorted_sessions[0].priority == "high"
        assert sorted_sessions[1].parent_agent_session_id == "pm-1"
        assert sorted_sessions[2].parent_agent_session_id is None
