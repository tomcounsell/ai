"""Tests for the SDK-tick compaction backstop in agent/session_executor.py.

Issue #1127 / C1. The backstop detects compaction by observing a drop in
``ResultMessage.num_turns`` across consecutive ticks. On detection it arms
``last_compaction_ts`` and bumps ``compaction_skipped_count`` via partial
save. Never crashes the executor.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.sdk_client import (
    _session_turn_counts,
    clear_turn_count,
    record_turn_count,
)
from agent.session_executor import _tick_backstop_check_compaction, _tick_issue_lock_renewal


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


class TestIssueLockRenewal:
    """Tests for _tick_issue_lock_renewal — the tier-1 (60s) heartbeat's
    issue #1954/#2003 renewal side effect. Must fire for a live `eng`
    session with a resolved issue_number AND an established run identity
    (a LIVE ``active_run_id``, re-fetched from Redis each tick — cycle-3
    BLOCKER 2), and must NOT fire for a non-eng session, a session with no
    issue_number, a session with no live active_run_id, or a missing
    agent_session -- see _tick_issue_lock_renewal's docstring for why this
    lives in the tier-1 (60s) block rather than the 25-minute calendar
    block.
    """

    def test_lock_renewal_sources_live_active_run_id(self):
        """#2003 cycle-2 BLOCKER: renewal identity is the record's
        active_run_id (own-identity read-back), never session_id or a
        process token. Cycle-3: sourced via the per-tick fresh fetch."""
        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-1"

        from models.session_lifecycle import IssueLockResult

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="sess-1", owner_run_id="run-1954"
            )
        )

        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.session_executor._fetch_live_active_run_id", return_value="run-1954"),
        ):
            _tick_issue_lock_renewal(session, agent_session)

        mock_touch.assert_called_once()
        args, kwargs = mock_touch.call_args
        assert args[0] == 1954
        assert args[1] == "run-1954"
        assert kwargs.get("session_id") == "sess-1"
        assert kwargs.get("ttl") is not None

    def test_lock_renewal_past_ttl_by_same_session_object(self):
        """Regression (#2003): a lock acquired with run_id X is still
        renewable PAST its original TTL by the same session object. Real
        Redis: acquire with a 1s TTL, then let the tick renew with the
        default 300s TTL -- the key's TTL extends beyond the original
        expiry, proving the renewal path presents the OWNING identity."""
        import popoto.redis_db as rdb

        from models.session_lifecycle import touch_issue_lock

        issue_number = 21954
        acquired = touch_issue_lock(issue_number, "run-x", session_id="sess-1", ttl=1)
        assert acquired.acquired is True

        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = issue_number
        agent_session.session_id = "sess-1"

        with patch("agent.session_executor._fetch_live_active_run_id", return_value="run-x"):
            _tick_issue_lock_renewal(session, agent_session)

        # Renewed by the same owner: TTL now far beyond the original 1s.
        pttl = rdb.POPOTO_REDIS_DB.pttl(f"session:issuelock:{issue_number}")
        assert pttl > 1_000, f"lock TTL not extended past original expiry (pttl={pttl})"
        peek = touch_issue_lock(issue_number, "run-x", session_id="sess-1", peek=True)
        assert peek.acquired is True

    def test_lock_renewal_warns_on_not_owner(self, caplog):
        """#2003: a not-owner renewal result logs a WARNING (no longer
        fire-and-forget) so an out-from-under takeover is visible."""
        import logging

        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-1"

        from models.session_lifecycle import IssueLockResult

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )

        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.session_executor._fetch_live_active_run_id", return_value="run-mine"),
            caplog.at_level(logging.WARNING),
        ):
            _tick_issue_lock_renewal(session, agent_session)

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("not-owner" in m and "foreign-run" in m for m in warnings), warnings

    def test_no_active_run_id_skips_renewal(self):
        """A session with no established run identity must never extend (or
        mint) the lock -- renewal is skipped."""
        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-1"

        with (
            patch("models.session_lifecycle.touch_issue_lock") as mock_touch,
            patch("agent.session_executor._fetch_live_active_run_id", return_value=None),
        ):
            _tick_issue_lock_renewal(session, agent_session)

        mock_touch.assert_not_called()

    def test_non_eng_session_does_not_renew_lock(self):
        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "teammate"
        agent_session.issue_number = 1954

        with patch("models.session_lifecycle.touch_issue_lock") as mock_touch:
            _tick_issue_lock_renewal(session, agent_session)

        mock_touch.assert_not_called()

    def test_eng_session_without_issue_number_does_not_renew_lock(self):
        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = None

        with patch("models.session_lifecycle.touch_issue_lock") as mock_touch:
            _tick_issue_lock_renewal(session, agent_session)

        mock_touch.assert_not_called()

    def test_missing_agent_session_does_not_renew_lock(self):
        session = _make_session()

        with patch("models.session_lifecycle.touch_issue_lock") as mock_touch:
            # Must not raise
            _tick_issue_lock_renewal(session, None)

        mock_touch.assert_not_called()

    def test_touch_issue_lock_exception_is_swallowed(self):
        """A Redis hiccup during renewal must never crash the heartbeat loop."""
        session = _make_session()
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-1"

        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                side_effect=RuntimeError("redis exploded"),
            ),
            patch("agent.session_executor._fetch_live_active_run_id", return_value="run-1954"),
        ):
            # Must not raise
            _tick_issue_lock_renewal(session, agent_session)


class TestIssueLockRenewalFreshFetch:
    """#2003 cycle-3 BLOCKER 2: the renewal tick must re-fetch
    ``active_run_id`` from Redis each tick instead of reading the executor's
    in-memory snapshot. The snapshot is fetched once at session start,
    BEFORE the session-ensure subprocess writes active_run_id -- so it is
    permanently stale: None on fresh runs (renewal skips forever, the lock
    lapses mid-stage, #1915 takeover window reopens) or the PREVIOUS run's
    id on resumed sessions (a lapsed lock is SET-NX re-acquired under a
    dead identity and renewed forever).
    """

    @staticmethod
    def _cleanup(session_id: str) -> None:
        from models.agent_session import AgentSession

        for s in AgentSession.query.filter(session_id=session_id):
            s.delete()

    def test_run_id_written_after_snapshot_is_renewed(self):
        """The judge-mandated regression: active_run_id is written via a
        SEPARATE fetch/save AFTER the executor's snapshot object was created
        (simulating the session-ensure subprocess write). The tick must renew
        under the freshly written id, not skip on the snapshot's None."""
        import uuid as _uuid

        from models.agent_session import AgentSession

        sid = f"tick-fetch-{_uuid.uuid4().hex[:8]}"
        try:
            snapshot = AgentSession.create_local(
                session_id=sid,
                project_key="test-tickfetch",
                working_dir="/tmp",
                session_type="eng",
                issue_number=31954,
            )
            assert getattr(snapshot, "active_run_id", None) in (None, "")

            # Subprocess-write simulation: a SEPARATE fetch mutates the record.
            fresh = list(AgentSession.query.filter(session_id=sid))[0]
            fresh.active_run_id = "run-subprocess"
            fresh.save()
            # The executor's snapshot object still carries the stale value.
            assert getattr(snapshot, "active_run_id", None) in (None, "")

            mock_touch = MagicMock()
            with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
                _tick_issue_lock_renewal(_make_session(), snapshot)

            mock_touch.assert_called_once()
            args, _kwargs = mock_touch.call_args
            assert args[0] == 31954
            assert args[1] == "run-subprocess"
        finally:
            self._cleanup(sid)

    def test_stale_previous_run_id_is_never_renewed(self):
        """Resumed-run hazard: the snapshot carries the PREVIOUS run's id.
        After the new run's ensure rebinds the record, the tick must present
        the NEW identity -- never SET-NX/renew under the dead one."""
        import uuid as _uuid

        from models.agent_session import AgentSession

        sid = f"tick-fetch-{_uuid.uuid4().hex[:8]}"
        try:
            snapshot = AgentSession.create_local(
                session_id=sid,
                project_key="test-tickfetch",
                working_dir="/tmp",
                session_type="eng",
                issue_number=31955,
                active_run_id="run-old",
            )
            # New run's ensure rebinds via a separate fetch/save.
            fresh = list(AgentSession.query.filter(session_id=sid))[0]
            fresh.active_run_id = "run-new"
            fresh.save()
            assert snapshot.active_run_id == "run-old"  # snapshot is stale

            mock_touch = MagicMock()
            with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
                _tick_issue_lock_renewal(_make_session(), snapshot)

            mock_touch.assert_called_once()
            args, _kwargs = mock_touch.call_args
            assert args[1] == "run-new"
            assert "run-old" not in [c.args[1] for c in mock_touch.call_args_list]
        finally:
            self._cleanup(sid)

    def test_fetch_failure_skips_tick_without_raising(self):
        """A fetch error skips this tick's renewal (the next tick retries)
        and never crashes the heartbeat loop."""
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-err"

        with (
            patch(
                "agent.session_executor.AgentSession.query.filter",
                side_effect=RuntimeError("redis exploded"),
            ),
            patch("models.session_lifecycle.touch_issue_lock") as mock_touch,
        ):
            _tick_issue_lock_renewal(_make_session(), agent_session)

        mock_touch.assert_not_called()

    def test_record_gone_skips_tick(self):
        """A deleted record (no rows) yields no identity -- renewal skips."""
        agent_session = _make_agent_session()
        agent_session.session_type = "eng"
        agent_session.issue_number = 1954
        agent_session.session_id = "sess-gone-nonexistent-xyz"

        with patch("models.session_lifecycle.touch_issue_lock") as mock_touch:
            _tick_issue_lock_renewal(_make_session(), agent_session)

        mock_touch.assert_not_called()
