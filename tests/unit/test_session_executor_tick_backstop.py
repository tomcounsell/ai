"""Tests for the SDK-tick compaction backstop in agent/session_executor.py.

Issue #1127 / C1. The backstop detects compaction by observing a drop in
``ResultMessage.num_turns`` across consecutive ticks. On detection it arms
``last_compaction_ts`` and bumps ``compaction_skipped_count`` via partial
save. Never crashes the executor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.sdk_client import (
    _session_turn_counts,
    clear_turn_count,
    record_turn_count,
)
from agent.session_executor import _tick_backstop_check_compaction


@pytest.fixture(autouse=True)
def _clear_turn_counts():
    """Drop the shared turn-count registry between tests."""
    _session_turn_counts.clear()
    yield
    _session_turn_counts.clear()


def _make_session(session_id: str = "sess-1") -> MagicMock:
    """Build a minimal session stand-in with the attributes the backstop reads."""
    s = MagicMock()
    s.session_id = session_id
    # _last_observed_message_count starts unset; getattr returns None
    del s._last_observed_message_count  # ensure attribute not auto-created
    s.project_key = "test-compaction"
    return s


def _make_agent_session() -> MagicMock:
    """Build a minimal AgentSession stand-in with the fields the backstop writes."""
    a = MagicMock()
    a.last_compaction_ts = None
    a.compaction_skipped_count = 0
    return a


class TestNoBackstopOnSteadyOrIncreasingCount:
    def test_no_prior_count_just_records(self):
        """First tick has no prior count — only records the observation."""
        session = _make_session()
        agent_session = _make_agent_session()
        record_turn_count("sess-1", 5)

        _tick_backstop_check_compaction(session, agent_session)

        # Tracker updated
        assert session._last_observed_message_count == 5
        # No save on agent_session
        agent_session.save.assert_not_called()
        assert agent_session.last_compaction_ts is None

    def test_steady_count_no_backstop(self):
        """Same count tick-over-tick is not a drop."""
        session = _make_session()
        session._last_observed_message_count = 10
        agent_session = _make_agent_session()
        record_turn_count("sess-1", 10)

        _tick_backstop_check_compaction(session, agent_session)

        assert session._last_observed_message_count == 10
        agent_session.save.assert_not_called()

    def test_increasing_count_no_backstop(self):
        """Normal turn progression (count goes up) is not a backstop trigger."""
        session = _make_session()
        session._last_observed_message_count = 10
        agent_session = _make_agent_session()
        record_turn_count("sess-1", 15)

        _tick_backstop_check_compaction(session, agent_session)

        assert session._last_observed_message_count == 15
        agent_session.save.assert_not_called()


class TestBackstopFiresOnCountDrop:
    def test_count_drop_arms_guard(self):
        """Count drop triggers the backstop — writes last_compaction_ts and bumps skip count."""
        session = _make_session()
        session._last_observed_message_count = 20
        agent_session = _make_agent_session()
        record_turn_count("sess-1", 5)  # dropped from 20 → 5 (compaction)

        _tick_backstop_check_compaction(session, agent_session)

        assert session._last_observed_message_count == 5
        # last_compaction_ts was set to a float timestamp
        assert isinstance(agent_session.last_compaction_ts, float)
        assert agent_session.last_compaction_ts > 0
        # compaction_skipped_count bumped
        assert agent_session.compaction_skipped_count == 1
        # Partial save invoked with the two named fields
        agent_session.save.assert_called_once()
        kwargs = agent_session.save.call_args.kwargs
        assert "update_fields" in kwargs
        assert set(kwargs["update_fields"]) == {
            "last_compaction_ts",
            "compaction_skipped_count",
        }

    def test_existing_skipped_count_increments(self):
        session = _make_session()
        session._last_observed_message_count = 20
        agent_session = _make_agent_session()
        agent_session.compaction_skipped_count = 7
        record_turn_count("sess-1", 3)

        _tick_backstop_check_compaction(session, agent_session)

        assert agent_session.compaction_skipped_count == 8


class TestBackstopExceptionSafety:
    def test_save_exception_swallowed(self):
        """Exception from save() does not propagate."""
        session = _make_session()
        session._last_observed_message_count = 20
        agent_session = _make_agent_session()
        agent_session.save.side_effect = RuntimeError("redis exploded")
        record_turn_count("sess-1", 5)

        # Must not raise
        _tick_backstop_check_compaction(session, agent_session)

    def test_missing_agent_session_does_not_crash(self):
        """None agent_session is tolerated — backstop logs warning but does not raise."""
        session = _make_session()
        session._last_observed_message_count = 20
        record_turn_count("sess-1", 5)

        # Must not raise
        _tick_backstop_check_compaction(session, None)
        # The session tracker is still updated for next tick
        assert session._last_observed_message_count == 5

    def test_missing_session_id_does_not_crash(self):
        """Session without session_id returns cleanly without raising."""
        session = MagicMock()
        session.session_id = None

        # Must not raise
        _tick_backstop_check_compaction(session, None)


class TestTurnCountTracker:
    def test_record_and_read(self):
        record_turn_count("sess-A", 42)
        from agent.sdk_client import get_turn_count

        assert get_turn_count("sess-A") == 42

    def test_clear_removes_entry(self):
        record_turn_count("sess-A", 42)
        clear_turn_count("sess-A")
        from agent.sdk_client import get_turn_count

        assert get_turn_count("sess-A") is None

    def test_record_rejects_non_numeric(self):
        record_turn_count("sess-A", "not-a-number")  # type: ignore[arg-type]
        from agent.sdk_client import get_turn_count

        assert get_turn_count("sess-A") is None
