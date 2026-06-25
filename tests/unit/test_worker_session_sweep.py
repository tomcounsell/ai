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


def _make_running_session(
    *,
    agent_session_id: str = "sweep-test-1",
    session_id: str = "sweep-test-1",
    claude_pid: int | None = _DEAD_PID,
    started_at: float | None = None,
    status: str = "running",
) -> MagicMock:
    """Create a minimal MagicMock representing a running AgentSession."""
    s = MagicMock()
    s.agent_session_id = agent_session_id
    s.session_id = session_id
    s.claude_pid = claude_pid
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
