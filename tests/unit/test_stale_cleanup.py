"""Unit tests for _cleanup_stale_sessions in scripts/update/run.py.

Covers:
- Sessions with a live _active_workers entry are skipped (Bug 1 fix)
- Sessions without a live worker and past age threshold are killed via
  finalize_session, not raw delete-and-recreate (Bug 3 fix)
- finalize_session exception inside the loop does not abort remaining cleanup
- Sessions younger than the threshold are not killed
- Sessions with recent updated_at are skipped (heartbeat-based liveness check)
- Sessions with stale updated_at are killed
- Sessions with no updated_at fall back to created_at age check

The FENCE gate (#2518) that now runs ahead of the recency check lives in
``tests/unit/test_update_stale_session_fence.py``. Every session built here
carries no ``live_fence``, so it takes the legacy path these tests describe --
which is exactly the coverage split intended: this file pins the fallback,
that one pins the authoritative check.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_session(
    session_id="session-1",
    agent_session_id="agent-1",
    chat_id="chat_1",
    status="running",
    age_seconds=7200,  # 2 hours old by default (past 120 min threshold)
    updated_at_seconds_ago=None,  # None means updated_at is None
    is_ledger=False,
    last_heartbeat_at=None,
):
    """Create a minimal session-like object for testing."""
    created = datetime.fromtimestamp(time.time() - age_seconds, tz=UTC)
    updated = (
        datetime.fromtimestamp(time.time() - updated_at_seconds_ago, tz=UTC)
        if updated_at_seconds_ago is not None
        else None
    )
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        chat_id=chat_id,
        status=status,
        created_at=created,
        updated_at=updated,
        is_ledger=is_ledger,
        last_heartbeat_at=last_heartbeat_at,
        delete=MagicMock(),
        save=MagicMock(),
        log_lifecycle_transition=MagicMock(),
    )


def _run_cleanup_full(sessions_by_status, active_workers=None, age_minutes=120):
    """Like ``_run_cleanup`` but returns the full 5-tuple, for tests that
    deliberately exercise the ledger or heartbeat rungs (where the
    fence-live/ledger/heartbeat-are-all-zero assertion in ``_run_cleanup``
    would be wrong to apply).
    """
    from scripts.update.run import _cleanup_stale_sessions

    active_workers = active_workers or {}

    def mock_filter(status=None):
        return sessions_by_status.get(status, [])

    with (
        patch("models.agent_session.AgentSession") as mock_as_class,
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
    ):
        mock_as_class.query.filter.side_effect = mock_filter

        import agent.session_state as queue_module

        original = getattr(queue_module, "_active_workers", {})
        try:
            queue_module._active_workers = active_workers
            result = _cleanup_stale_sessions(Path("/tmp"), age_minutes=age_minutes)
        finally:
            queue_module._active_workers = original

    return (*result, mock_finalize)


def _run_cleanup(sessions_by_status, active_workers=None, age_minutes=120):
    """Run _cleanup_stale_sessions with injected mocks.

    Returns (killed_count, skipped_recent, finalize_mock) so tests can assert on
    all. The full return value is now a 5-tuple (#2660):
    ``(killed, skipped_recent, skipped_fence_live, skipped_ledger,
    skipped_heartbeat)``. ``skipped_fence_live``, ``skipped_ledger``, and
    ``skipped_heartbeat`` are all asserted to be 0 here: none of these
    ``SimpleNamespace`` fixtures carries a fence, an ``is_ledger`` flag, or a
    ``last_heartbeat_at`` value, so a non-zero count on any of them would mean
    a rung fired on a row that has nothing for it to read.
    """
    from scripts.update.run import _cleanup_stale_sessions

    active_workers = active_workers or {}

    def mock_filter(status=None):
        return sessions_by_status.get(status, [])

    with (
        patch("models.agent_session.AgentSession") as mock_as_class,
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
    ):
        mock_as_class.query.filter.side_effect = mock_filter

        import agent.session_state as queue_module

        original = getattr(queue_module, "_active_workers", {})
        try:
            queue_module._active_workers = active_workers
            result = _cleanup_stale_sessions(Path("/tmp"), age_minutes=age_minutes)
        finally:
            queue_module._active_workers = original

    killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat = result
    assert skipped_fence_live == 0, (
        "no session in this file records a fence, so the fence gate must not "
        "have fired -- a non-zero count means it read something it should not"
    )
    assert skipped_ledger == 0, (
        "no session in this file sets is_ledger, so the ledger rung must not fire"
    )
    assert skipped_heartbeat == 0, (
        "no session in this file sets last_heartbeat_at, so the heartbeat rung must not fire"
    )
    return killed, skipped_recent, mock_finalize


class TestCleanupRecentUpdatedAt:
    """Sessions with recent updated_at are skipped (heartbeat-based liveness)."""

    def test_recent_updated_at_skips_session(self):
        """Session with updated_at 10 min ago is skipped even if created_at is 3 hours ago."""
        # 10 minutes ago — within 30-minute recency window
        session = _make_session(age_seconds=10800, updated_at_seconds_ago=600)
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 0, "Should not kill session with recent updated_at"
        assert skipped == 1, "Should count session as skipped-live"
        mock_finalize.assert_not_called()

    def test_stale_updated_at_kills_session(self):
        """Session with updated_at 60 min ago and created_at 3 hours ago is killed."""
        # updated_at 60 min ago — outside 30-min recency window
        session = _make_session(age_seconds=10800, updated_at_seconds_ago=3600)
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 1, "Should kill session with stale updated_at"
        assert skipped == 0
        mock_finalize.assert_called_once_with(
            session,
            "killed",
            reason="stale cleanup (stale heartbeat, liveness unverified)",
            skip_checkpoint=True,
        )

    def test_none_updated_at_falls_back_to_created_at_young(self):
        """Session with updated_at=None and young created_at is not killed."""
        # No updated_at, created_at 10 min ago — below 120 min fallback threshold
        session = _make_session(age_seconds=600, updated_at_seconds_ago=None)
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 0, "Young session with no updated_at should not be killed"
        assert skipped == 0
        mock_finalize.assert_not_called()

    def test_none_updated_at_falls_back_to_created_at_stale(self):
        """Session with updated_at=None and old created_at (>120 min) is killed."""
        # No updated_at, created_at 3 hours ago — above 120 min fallback threshold
        session = _make_session(age_seconds=10800, updated_at_seconds_ago=None)
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 1, "Old session with no updated_at should be killed"
        assert skipped == 0
        mock_finalize.assert_called_once()

    def test_updated_at_exactly_at_boundary_is_skipped(self):
        """Session with updated_at just under 30 min ago is still skipped."""
        # 29 minutes ago — just inside the 30-minute window
        session = _make_session(age_seconds=10800, updated_at_seconds_ago=29 * 60)
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 0
        assert skipped == 1
        mock_finalize.assert_not_called()


class TestCleanupSkipsLiveWorkers:
    """Sessions with live _active_workers entries are not killed."""

    def test_live_worker_skips_session(self):
        """Session whose chat_id has a live (not-done) asyncio Task is skipped."""
        # Session with stale updated_at but live worker should still be skipped
        stale_session = _make_session(
            chat_id="chat_active", age_seconds=7200, updated_at_seconds_ago=3600
        )
        sessions_by_status = {"running": [stale_session], "pending": []}

        live_task = MagicMock(spec=asyncio.Task)
        live_task.done.return_value = False
        active_workers = {"chat_active": live_task}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status, active_workers)

        assert killed == 0, "Should not kill session with live worker"
        mock_finalize.assert_not_called()

    def test_done_worker_does_not_protect_session(self):
        """Session whose chat_id worker is done() is treated as orphaned."""
        # stale updated_at so it falls through to kill
        stale_session = _make_session(
            chat_id="chat_done", age_seconds=7200, updated_at_seconds_ago=3600
        )
        sessions_by_status = {"running": [stale_session], "pending": []}

        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        active_workers = {"chat_done": done_task}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status, active_workers)

        assert killed == 1, "Should kill session with done worker"
        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert args[0] is stale_session
        assert args[1] == "killed"
        assert kwargs.get("skip_checkpoint") is True

    def test_no_chat_id_falls_through_to_recency_check(self):
        """Session with no chat_id is not protected by registry — uses recency check."""
        # stale updated_at so it gets killed
        stale_session = _make_session(chat_id=None, age_seconds=7200, updated_at_seconds_ago=3600)
        sessions_by_status = {"running": [stale_session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 1
        mock_finalize.assert_called_once()


class TestCleanupUsesLifecycleLayer:
    """_cleanup_stale_sessions routes terminal transitions through finalize_session."""

    def test_finalize_session_called_not_raw_delete(self):
        """No raw s.delete() / AgentSession.create() — finalize_session is used."""
        stale_session = _make_session(age_seconds=7200, updated_at_seconds_ago=3600)
        sessions_by_status = {"running": [stale_session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        mock_finalize.assert_called_once_with(
            stale_session,
            "killed",
            reason="stale cleanup (stale heartbeat, liveness unverified)",
            skip_checkpoint=True,
        )
        assert "no live process" not in mock_finalize.call_args.kwargs["reason"], (
            "the recency path observed nothing about the process, so the reason "
            "must not claim it did (#2518)"
        )
        stale_session.delete.assert_not_called()

    def test_finalize_exception_does_not_abort_loop(self):
        """A finalize_session failure for one session does not stop cleanup of others."""
        from scripts.update.run import _cleanup_stale_sessions

        s1 = _make_session(
            session_id="s1",
            agent_session_id="a1",
            age_seconds=7200,
            updated_at_seconds_ago=3600,
        )
        s2 = _make_session(
            session_id="s2",
            agent_session_id="a2",
            age_seconds=7200,
            updated_at_seconds_ago=3600,
        )
        sessions_by_status = {"running": [s1, s2], "pending": []}

        call_count = [0]

        def finalize_side_effect(session, status, **kwargs):
            call_count[0] += 1
            if session is s1:
                raise RuntimeError("Redis exploded")

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch(
                "models.session_lifecycle.finalize_session",
                side_effect=finalize_side_effect,
            ),
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.session_state as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                result = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert call_count[0] == 2, "finalize_session must be called for both sessions"
        killed, _skipped_recent, _skipped_fence_live, _skipped_ledger, _skipped_heartbeat = result
        assert killed == 1


class TestCleanupPendingExclusion:
    """pending sessions are never iterated — they have no process to clean up."""

    def test_pending_sessions_never_killed(self):
        """A stale pending session is not killed — _cleanup_stale_sessions skips it."""
        stale_pending = _make_session(
            session_id="p1",
            agent_session_id="ap1",
            status="pending",
            age_seconds=10800,  # 3 hours old
            updated_at_seconds_ago=3600,  # 1 hour since last update
        )
        # Only pending sessions in the mock — no running sessions
        sessions_by_status = {"running": [], "pending": [stale_pending]}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 0, "pending sessions must never be killed"
        assert skipped == 0
        mock_finalize.assert_not_called()

    def test_pending_sessions_excluded_from_loop(self):
        """AgentSession.query.filter is never called with status='pending'."""
        stale_pending = _make_session(status="pending", age_seconds=10800)
        sessions_by_status = {"running": [], "pending": [stale_pending]}

        from unittest.mock import call

        from scripts.update.run import _cleanup_stale_sessions

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session"),
        ):

            def mock_filter(status=None):
                return sessions_by_status.get(status, [])

            mock_as_class.query.filter.side_effect = mock_filter

            import agent.session_state as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                _cleanup_stale_sessions(Path("/tmp"))
            finally:
                queue_module._active_workers = original

        # filter must only be called with status="running"
        pending_calls = [
            c for c in mock_as_class.query.filter.call_args_list if c == call(status="pending")
        ]
        assert len(pending_calls) == 0, "filter(status='pending') must never be called"


class TestCleanupAgeThreshold:
    """Sessions younger than the threshold are not killed (via created_at fallback)."""

    def test_young_session_not_killed(self):
        """Session younger than age_minutes threshold and no updated_at is left alone."""
        # Only 10 minutes old, no updated_at
        young_session = _make_session(age_seconds=600, updated_at_seconds_ago=None)
        sessions_by_status = {"running": [young_session], "pending": []}

        killed, skipped, mock_finalize = _run_cleanup(sessions_by_status)

        assert killed == 0
        assert skipped == 0
        mock_finalize.assert_not_called()


class TestLedgerAndHeartbeatRungs:
    """New reaper rungs added by #2660: ``is_ledger`` (rung 2) and
    ``last_heartbeat_at`` (rung 4). Fenced coverage for these same rungs lives
    in ``tests/unit/test_update_stale_session_fence.py``; this file's fixtures
    carry no fence, so these pin the legacy/no-fence path through both rungs.
    """

    def test_ledger_row_is_skipped_and_counted_separately_from_recent(self):
        """A non-executable CLI anchor row is skipped at rung 2, not rung 5,
        even though its ``updated_at`` is stale enough to otherwise be killed.
        """
        ledger_session = _make_session(
            age_seconds=10800, updated_at_seconds_ago=None, is_ledger=True
        )
        sessions_by_status = {"running": [ledger_session], "pending": []}

        killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat, finalize = (
            _run_cleanup_full(sessions_by_status)
        )

        assert killed == 0
        assert skipped_ledger == 1
        assert skipped_recent == 0
        finalize.assert_not_called()

    def test_fresh_heartbeat_spares_a_session_with_stale_updated_at(self):
        """A session mid-execution (60s heartbeat tick, stale ``updated_at``
        because the heartbeat's partial save deliberately doesn't move it) is
        spared at rung 4, counted separately from ``skipped_recent``.
        """
        session = _make_session(
            age_seconds=10800,
            updated_at_seconds_ago=3600,  # stale by recency-window standards
            last_heartbeat_at=datetime.now(UTC),
        )
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat, finalize = (
            _run_cleanup_full(sessions_by_status)
        )

        assert killed == 0
        assert skipped_heartbeat == 1
        assert skipped_recent == 0
        finalize.assert_not_called()

    def test_stale_heartbeat_falls_through_to_updated_at_rung(self):
        """A ``last_heartbeat_at`` outside the recency window must not spare the
        row at rung 4; it falls through and is decided by rung 5 as before.
        """
        session = _make_session(
            age_seconds=10800,
            updated_at_seconds_ago=3600,
            last_heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
        )
        sessions_by_status = {"running": [session], "pending": []}

        killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat, finalize = (
            _run_cleanup_full(sessions_by_status)
        )

        assert skipped_heartbeat == 0
        assert killed == 1, (
            "a stale heartbeat must not protect a session from the recency/age gates"
        )
