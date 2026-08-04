"""Tests for _sweep_dead_worker_sessions() in agent/session_health.py (issue #1767).

When a hung worker is killed by the watchdog, sessions can remain
status='running' with a stale claude_pid. This sweep function detects those
orphaned sessions at worker startup and marks them 'killed' so catchup can
re-enqueue the unanswered human messages.

TDD: RED tests written before implementation; tests use mock/patch to avoid
real Redis dependency.

Patching notes:
- ``subprocess.run`` is patched at ``agent.session_health.subprocess.run``
  because ``subprocess`` is a module-level import in session_health.py.
- ``finalize_session`` is imported lazily inside _sweep_dead_worker_sessions
  (like all other callers in the module) so it must be patched at
  ``models.session_lifecycle.finalize_session`` — that is the namespace the
  ``from models.session_lifecycle import finalize_session`` line binds into.

Fence branches (#2518): ``_make_running_session`` defaulted ``create_time`` to
``None``, which routes every session down the legacy ``_pid_is_alive`` fallback
— so all 13 pre-existing tests exercised the fallback and NONE reached the
fence. ``create_time`` is now a parameter, and ``TestSweepFenceBranches`` covers
the three branches explicitly, including the recycled-pid one (alive pid,
mismatched ``create_time``) that no test reached.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from agent.session_health import AGENT_SESSION_HEALTH_MIN_RUNNING, _sweep_dead_worker_sessions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEAD_PID = 99999  # A PID that does not exist on any reasonable system

# Canonical patch targets
_FINALIZE = "models.session_lifecycle.finalize_session"
_SUBPROC = "agent.session_health.subprocess.run"
_PROC_CT = "agent.pid_fence.proc_create_time"


def _make_running_session(
    *,
    agent_session_id: str = "sweep-test-1",
    session_id: str = "sweep-test-1",
    claude_pid: int | None = _DEAD_PID,
    create_time: float | None = None,
    started_at: float | None = None,
    status: str = "running",
) -> MagicMock:
    """Create a minimal MagicMock representing a running AgentSession.

    Durability plan #2494: the sweep reads the fenced execution record
    (``live_fence``), not ``claude_pid``.

    ``create_time`` selects the branch under test:

    * ``None`` (default) — a LEGACY row. The sweep falls back to the bare
      ``os.kill(pid, 0)`` liveness probe, which is what the pre-existing tests
      in this file exercise.
    * a float — a FENCED row. ``fence_is_live`` decides, so a live pid whose
      identity no longer matches is correctly swept as recycled.
    """
    s = MagicMock()
    s.agent_session_id = agent_session_id
    s.session_id = session_id
    s.live_fence = (
        {"pid": claude_pid, "create_time": create_time} if claude_pid is not None else None
    )
    # Default: started AGENT_SESSION_HEALTH_MIN_RUNNING + 60s ago (well past the guard)
    s.started_at = (
        started_at
        if started_at is not None
        else (time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 60)
    )
    s.status = status
    return s


# ---------------------------------------------------------------------------
# Test: dead PID → session swept to killed
# ---------------------------------------------------------------------------


class TestSweepKillsDeadPidSessions:
    """A running session with a dead claude_pid should be swept to 'killed'."""

    def test_sweep_kills_dead_pid_session(self):
        session = _make_running_session(claude_pid=_DEAD_PID)

        finalized_status = {}

        def fake_finalize(entry, status, reason="", **kwargs):
            finalized_status["status"] = status
            finalized_status["reason"] = reason
            entry.status = status

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE, side_effect=fake_finalize),
            patch(_SUBPROC),
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 1
        assert finalized_status["status"] == "killed"
        assert "dead-worker-sweep" in finalized_status["reason"]
        assert str(_DEAD_PID) in finalized_status["reason"]

    def test_sweep_returns_count_of_swept_sessions(self):
        sessions = [
            _make_running_session(agent_session_id=f"sweep-{i}", claude_pid=_DEAD_PID + i)
            for i in range(3)
        ]

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=sessions),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE) as mock_finalize,
            patch(_SUBPROC),
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 3
        assert mock_finalize.call_count == 3


# ---------------------------------------------------------------------------
# Test: live PID → session NOT swept
# ---------------------------------------------------------------------------


class TestSweepSkipsLivePidSessions:
    """A running session with a live claude_pid must not be swept."""

    def test_sweep_skips_live_pid(self):
        live_pid = os.getpid()  # Current process — definitely alive
        session = _make_running_session(claude_pid=live_pid)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch(_FINALIZE) as mock_finalize,
        ):
            # os.kill(pid, 0) succeeds for our own PID — no OSError
            result = _sweep_dead_worker_sessions()

        assert result == 0
        mock_finalize.assert_not_called()

    def test_sweep_skips_no_pid_session(self):
        """Sessions with claude_pid=None (not yet assigned) must not be swept."""
        session = _make_running_session(claude_pid=None)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch(_FINALIZE) as mock_finalize,
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 0
        mock_finalize.assert_not_called()

    def test_sweep_skips_zero_pid_session(self):
        """Sessions with claude_pid=0 must not be swept (unassigned sentinel)."""
        session = _make_running_session(claude_pid=0)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch(_FINALIZE) as mock_finalize,
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 0
        mock_finalize.assert_not_called()


# ---------------------------------------------------------------------------
# Test: recency guard — recently-started sessions must not be swept
# ---------------------------------------------------------------------------


class TestSweepSkipsRecentSessions:
    """Sessions started within AGENT_SESSION_HEALTH_MIN_RUNNING seconds are skipped."""

    def test_sweep_skips_session_within_recency_guard(self):
        # Started only 10 seconds ago — well within the 300s guard
        recent_start = time.time() - 10
        session = _make_running_session(claude_pid=_DEAD_PID, started_at=recent_start)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE) as mock_finalize,
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 0
        mock_finalize.assert_not_called()

    def test_sweep_kills_session_past_recency_guard(self):
        # Started AGENT_SESSION_HEALTH_MIN_RUNNING + 1s ago — just past the guard
        old_start = time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 1
        session = _make_running_session(claude_pid=_DEAD_PID, started_at=old_start)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE) as mock_finalize,
            patch(_SUBPROC),
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 1
        mock_finalize.assert_called_once()


# ---------------------------------------------------------------------------
# Test: catchup trigger after sweeping
# ---------------------------------------------------------------------------


class TestSweepTriggersCatchup:
    """When sessions are swept, bridge.agent_catchup is invoked via subprocess."""

    def test_catchup_triggered_after_sweep(self):
        session = _make_running_session(claude_pid=_DEAD_PID)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE),
            patch(_SUBPROC) as mock_run,
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 1
        mock_run.assert_called_once()
        # The call must target bridge.agent_catchup
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert "bridge.agent_catchup" in cmd

    def test_no_catchup_when_nothing_swept(self):
        """Catchup must NOT be triggered when no sessions are swept."""
        live_pid = os.getpid()
        session = _make_running_session(claude_pid=live_pid)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch(_SUBPROC) as mock_run,
        ):
            result = _sweep_dead_worker_sessions()

        assert result == 0
        mock_run.assert_not_called()

    def test_catchup_failure_does_not_abort_sweep(self):
        """subprocess.run failure must not raise — sweep result is still returned."""
        session = _make_running_session(claude_pid=_DEAD_PID)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(_FINALIZE),
            patch(_SUBPROC, side_effect=Exception("catchup failed")),
        ):
            result = _sweep_dead_worker_sessions()

        # Despite catchup failure, the sweep count is correct
        assert result == 1


# ---------------------------------------------------------------------------
# Test: CAS conflict handling (StatusConflictError)
# ---------------------------------------------------------------------------


class TestSweepHandlesConcurrentModification:
    """StatusConflictError from finalize_session must be handled gracefully."""

    def test_status_conflict_skips_session(self):
        from models.session_lifecycle import StatusConflictError

        session = _make_running_session(claude_pid=_DEAD_PID)

        with (
            patch("agent.session_health._filter_hydrated_sessions", return_value=[session]),
            patch("os.kill", side_effect=OSError("No such process")),
            patch(
                _FINALIZE,
                side_effect=StatusConflictError(
                    session_id="sweep-test-1",
                    expected_status="running",
                    actual_status="killed",
                    reason="concurrent kill",
                ),
            ),
            patch(_SUBPROC),
        ):
            # Must not raise
            result = _sweep_dead_worker_sessions()

        # Session was not counted as swept (CAS prevented it)
        assert result == 0


# ---------------------------------------------------------------------------
# Test: no running sessions → fast return
# ---------------------------------------------------------------------------


class TestSweepNoRunningSessions:
    def test_empty_running_list_returns_zero(self):
        with patch("agent.session_health._filter_hydrated_sessions", return_value=[]):
            result = _sweep_dead_worker_sessions()

        assert result == 0


# ---------------------------------------------------------------------------
# Fence branches (#2518)
# ---------------------------------------------------------------------------


class TestSweepFenceBranches:
    """The three ``live_fence`` outcomes, driven through the REAL predicate.

    ``agent.pid_fence.proc_create_time`` is the seam (spike-4): it is late-bound
    inside ``fence_is_live``, so patching it drives the production compare
    rather than replacing it. Real pid recycling cannot be forced on demand and
    is parallel-hostile under ``-n auto --dist=loadfile``, so the live
    ``create_time`` is faked instead.
    """

    _RECORDED_CT = 1000.0

    def _run(self, session, *, live_ct):
        """Sweep once with ``proc_create_time`` reporting ``live_ct``.

        ``_filter_hydrated_sessions`` runs FOR REAL here (the session
        stand-ins carry string ``agent_session_id``/``session_id``), so this
        also pins that the sweep queries the status index rather than scanning
        every row.
        """
        finalized: list[tuple[str, str]] = []

        def fake_finalize(entry, status, reason="", **kwargs):
            finalized.append((status, reason))
            entry.status = status

        fake_query = MagicMock()
        # The row leaves the ``running`` index the moment it is finalized, so a
        # second pass finds nothing — this is what makes "exactly once" testable.
        fake_query.filter.side_effect = lambda **kw: (
            [session] if kw.get("status") == getattr(session, "status", None) else []
        )

        with (
            patch("agent.session_health.AgentSession.query", fake_query),
            patch(_PROC_CT, return_value=live_ct),
            patch(_FINALIZE, side_effect=fake_finalize),
            patch(_SUBPROC),
        ):
            result = _sweep_dead_worker_sessions()

        return result, finalized, fake_query

    def test_dead_fence_pid_is_swept(self):
        """The recorded pid is gone: ``proc_create_time`` reads None → not ours."""
        session = _make_running_session(claude_pid=_DEAD_PID, create_time=self._RECORDED_CT)

        result, finalized, _ = self._run(session, live_ct=None)

        assert result == 1
        assert finalized[0][0] == "killed"
        assert "dead-worker-sweep" in finalized[0][1]

    def test_recycled_fence_pid_is_swept(self):
        """The branch no pre-#2518 test reached: pid ALIVE, identity mismatched.

        ``os.kill(pid, 0)`` would report this pid alive and the legacy fallback
        would skip the row forever, orphaning the session and silently dropping
        the human's message. The fence catches it: the original harness is gone,
        so the row is swept.
        """
        session = _make_running_session(
            claude_pid=os.getpid(),  # genuinely alive — the legacy probe would say "skip"
            create_time=self._RECORDED_CT,
        )

        result, finalized, _ = self._run(session, live_ct=self._RECORDED_CT + 5000.0)

        assert result == 1, (
            "A live pid whose create_time no longer matches has been recycled — "
            "the ORIGINAL harness is gone and the session is orphaned"
        )
        assert finalized[0][0] == "killed"

    def test_matching_fence_is_skipped(self):
        session = _make_running_session(claude_pid=os.getpid(), create_time=self._RECORDED_CT)

        result, finalized, _ = self._run(session, live_ct=self._RECORDED_CT)

        assert result == 0
        assert finalized == []

    def test_sub_tolerance_create_time_skew_still_matches(self):
        """A sub-millisecond re-read skew must not sweep a live session."""
        from agent.pid_fence import CREATE_TIME_TOLERANCE_S

        session = _make_running_session(claude_pid=os.getpid(), create_time=self._RECORDED_CT)

        result, _, _ = self._run(session, live_ct=self._RECORDED_CT + CREATE_TIME_TOLERANCE_S / 2)

        assert result == 0

    def test_scan_is_scoped_to_the_running_status_index(self):
        """The sweep must not scan every row — it queries the status index once."""
        session = _make_running_session(claude_pid=_DEAD_PID, create_time=self._RECORDED_CT)

        _, _, fake_query = self._run(session, live_ct=None)

        fake_query.filter.assert_called_once_with(status="running")
        fake_query.all.assert_not_called()

    def test_a_row_is_swept_exactly_once_across_two_passes(self):
        """The second pass finds the row terminal, so it cannot be finalized twice.

        Double-finalizing would emit a second `killed` transition and trigger a
        second catchup, re-enqueuing the human's message twice.
        """
        session = _make_running_session(claude_pid=_DEAD_PID, create_time=self._RECORDED_CT)
        finalized: list[str] = []

        def fake_finalize(entry, status, reason="", **kwargs):
            finalized.append(status)
            entry.status = status

        fake_query = MagicMock()
        fake_query.filter.side_effect = lambda **kw: (
            [session] if kw.get("status") == session.status else []
        )

        with (
            patch("agent.session_health.AgentSession.query", fake_query),
            patch(_PROC_CT, return_value=None),
            patch(_FINALIZE, side_effect=fake_finalize),
            patch(_SUBPROC) as mock_run,
        ):
            first = _sweep_dead_worker_sessions()
            second = _sweep_dead_worker_sessions()

        assert first == 1
        assert second == 0, "the finalized row has left the running index"
        assert finalized == ["killed"]
        assert mock_run.call_count == 1, "catchup fires once, not once per pass"

    def test_legacy_row_with_a_live_pid_is_never_swept_by_the_fence(self):
        """Legacy rows keep the gentler liveness fallback — unknown is not "dead".

        ``fence_is_live`` returns False for an absent recorded ``create_time``,
        so a naive `not fence_is_live(...)` here would sweep every live legacy
        session at every worker boot.
        """
        session = _make_running_session(claude_pid=os.getpid(), create_time=None)

        # ``proc_create_time`` reads a real value, but the row recorded nothing,
        # so the fence cannot decide — the plain os.kill(pid, 0) probe does.
        result, finalized, _ = self._run(session, live_ct=self._RECORDED_CT)

        assert result == 0
        assert finalized == []
