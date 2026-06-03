"""Unit tests for the recovery subprocess-kill escalation (issue #1537).

When the liveness check recovers a no-progress ``running`` session, it must
confirm the underlying ``claude -p`` subprocess actually exited before requeuing
the DB record to ``pending``. If the subprocess ignores ``task.cancel()`` (a true
hang), the recovery path escalates SIGTERM -> SIGKILL against the recorded
``claude_pid``; a subprocess that cannot be confirmed dead escalates the session
to ``failed`` (terminal) so the orphan reaper owns cleanup, rather than parking an
invisible orphan at ``pending`` that wedges the worker slot.

Covers ``_confirm_subprocess_dead`` (the signal-escalation helper) and the
``_apply_recovery_transition`` requeue/finalize branching.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import session_health

# ==========================================================================
# _confirm_subprocess_dead — signal escalation helper
# ==========================================================================


class TestConfirmSubprocessDead:
    """Direct tests of the SIGTERM->SIGKILL escalation helper.

    The helper returns a :class:`session_health.SubprocessKillResult`
    ``(confirmed_dead, signal_sent)``. ``signal_sent`` distinguishes the
    already-dead path (cancel sufficed, no signal) from a genuine SIGTERM/SIGKILL
    escalation so the caller does not over-count the escalated counter.
    """

    def test_none_pid_returns_confirmed_no_signal(self):
        """No PID recorded → nothing to kill → confirmed dead, no signal sent."""
        with patch.object(session_health.os, "kill") as mock_kill:
            result = session_health._confirm_subprocess_dead(None, timeout=3.0)
        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_kill.assert_not_called()

    def test_nonpositive_pid_returns_confirmed_no_signal(self):
        """pid <= 0 is not a real process → confirmed dead, no signals."""
        with patch.object(session_health.os, "kill") as mock_kill:
            assert session_health._confirm_subprocess_dead(0, timeout=3.0) == (True, False)
            assert session_health._confirm_subprocess_dead(-1, timeout=3.0) == (True, False)
        mock_kill.assert_not_called()

    def test_already_dead_returns_confirmed_without_signals(self):
        """First liveness probe (signal 0) raises ProcessLookupError → already gone.

        cancel() sufficed: confirmed_dead=True but signal_sent=False, so the caller
        must NOT count this as a kill escalation.
        """
        with patch.object(session_health.os, "kill", side_effect=ProcessLookupError) as mock_kill:
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        assert result.confirmed_dead is True
        assert result.signal_sent is False
        # Only the initial signal-0 probe; no SIGTERM/SIGKILL.
        assert mock_kill.call_count == 1
        assert mock_kill.call_args_list[0].args == (1234, 0)

    def test_sigterm_suffices_reports_signal_sent(self):
        """PID alive at probe, dies after SIGTERM → SIGKILL never sent, signal_sent True."""
        # Sequence of os.kill behaviors:
        #   probe(0) -> alive (returns)
        #   SIGTERM  -> returns (signal delivered)
        #   poll probe(0) -> ProcessLookupError (now dead)
        calls = []

        def fake_kill(pid, sig):
            calls.append(sig)
            if sig == 0 and len(calls) == 1:
                return  # initial probe: alive
            if sig == session_health.signal.SIGTERM:
                return  # SIGTERM delivered
            # Any subsequent signal-0 poll: process has exited.
            raise ProcessLookupError

        with patch.object(session_health.os, "kill", side_effect=fake_kill):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)

        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        assert session_health.signal.SIGTERM in calls
        assert session_health.signal.SIGKILL not in calls

    def test_sigkill_sent_only_when_sigterm_insufficient(self):
        """PID survives SIGTERM grace → SIGKILL is escalated, then dies; signal_sent True."""
        sent_signals = []

        def fake_kill(pid, sig):
            sent_signals.append(sig)
            if sig == session_health.signal.SIGKILL:
                return  # SIGKILL delivered; subsequent probe will report dead
            if sig in (0, session_health.signal.SIGTERM):
                # Initial probe + SIGTERM + all SIGTERM-grace polls: still alive,
                # until SIGKILL has been issued.
                if session_health.signal.SIGKILL in sent_signals and sig == 0:
                    raise ProcessLookupError
                return
            raise ProcessLookupError

        # Force the SIGTERM grace poll to expire immediately so the test does not
        # actually sleep for SUBPROCESS_KILL_TIMEOUT seconds.
        with (
            patch.object(session_health.os, "kill", side_effect=fake_kill),
            patch.object(session_health.time, "sleep"),
            patch.object(
                session_health.time,
                "monotonic",
                side_effect=[0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            ),
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)

        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        assert session_health.signal.SIGTERM in sent_signals
        assert session_health.signal.SIGKILL in sent_signals

    def test_survives_sigterm_and_sigkill_reports_not_confirmed_signal_sent(self):
        """PID stays alive through SIGTERM and SIGKILL → not confirmed, but signal_sent True."""

        def fake_kill(pid, sig):
            # Process never dies: signal 0 always returns (alive), signals deliver.
            return

        with (
            patch.object(session_health.os, "kill", side_effect=fake_kill),
            patch.object(session_health.time, "sleep"),
            patch.object(
                session_health.time,
                "monotonic",
                side_effect=[0.0] + [100.0] * 20,  # deadline immediately in the past after start
            ),
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)

        # A real SIGTERM/SIGKILL was delivered even though it didn't take.
        assert result == session_health.SubprocessKillResult(confirmed_dead=False, signal_sent=True)

    def test_permission_error_on_probe_returns_not_confirmed_no_signal(self):
        """PermissionError on the initial liveness probe → not confirmed, no signal."""
        with patch.object(session_health.os, "kill", side_effect=PermissionError):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        assert result == session_health.SubprocessKillResult(
            confirmed_dead=False, signal_sent=False
        )

    def test_permission_error_on_sigterm_returns_not_confirmed_no_signal(self):
        """Probe says alive, SIGTERM raises PermissionError → not confirmed, no signal landed."""

        def fake_kill(pid, sig):
            if sig == 0:
                return  # alive
            raise PermissionError

        with patch.object(session_health.os, "kill", side_effect=fake_kill):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        # SIGTERM was rejected, so no signal actually landed.
        assert result == session_health.SubprocessKillResult(
            confirmed_dead=False, signal_sent=False
        )


# ==========================================================================
# _increment_subprocess_kill_counter — best-effort Redis counters
# ==========================================================================


class TestSubprocessKillCounter:
    """The counters are best-effort and never propagate a backend failure."""

    def _session(self):
        return SimpleNamespace(project_key="test-proj")

    def test_escalated_increments_escalated_key(self):
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            session_health._increment_subprocess_kill_counter(self._session(), escalated=True)
        mock_redis.incr.assert_called_once_with(
            "test-proj:session-health:subprocess_kill_escalated"
        )

    def test_failed_increments_failed_key(self):
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            session_health._increment_subprocess_kill_counter(self._session(), escalated=False)
        mock_redis.incr.assert_called_once_with("test-proj:session-health:subprocess_kill_failed")

    def test_counter_backend_failure_never_propagates(self):
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.incr.side_effect = RuntimeError("redis down")
            # Must not raise.
            session_health._increment_subprocess_kill_counter(self._session(), escalated=False)


# ==========================================================================
# _apply_recovery_transition — requeue vs failed branching
# ==========================================================================


def _make_entry(*, claude_pid=4321, recovery_attempts=0):
    """Minimal AgentSession-like stub for the recovery else branch.

    ``recovery_attempts=0`` keeps us below MAX_RECOVERY_ATTEMPTS so the
    ``else`` requeue/failed branch (not the attempts-exhausted branch) is taken.
    """
    return SimpleNamespace(
        agent_session_id="sess-1537",
        session_id="sid-1537",
        project_key="test-proj",
        chat_id="chat-1",
        claude_pid=claude_pid,
        recovery_attempts=recovery_attempts,
        reprieve_count=0,
        priority="normal",
        started_at="2026-06-03T00:00:00Z",
        response_delivered_at=None,
        exit_returncode=0,
        is_project_keyed=False,
        save=lambda **kw: None,
    )


@pytest.fixture
def recovery_patches():
    """Patch the lifecycle helpers and worker-ensure side effects.

    Yields a dict of the mocks for assertions. ``_tier2_reprieve_signal`` returns
    ``None`` so the recovery is not reprieved; ``_ensure_worker`` is a no-op.
    """
    with (
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
        patch("models.session_lifecycle.transition_status") as mock_transition,
        patch.object(session_health, "_tier2_reprieve_signal", return_value=None),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("popoto.redis_db.POPOTO_REDIS_DB"),
    ):
        yield {"finalize": mock_finalize, "transition": mock_transition}


def _run_recovery(entry):
    return asyncio.run(
        session_health._apply_recovery_transition(
            entry,
            reason="no progress",
            reason_kind="no_progress",
            handle=None,
            worker_key="worker-1",
        )
    )


class TestRecoveryBranching:
    """The requeue ``else`` branch finalizes to failed when the subprocess survives."""

    def test_subprocess_survives_escalates_to_failed(self, recovery_patches):
        """Subprocess not confirmed dead → finalize_session('failed'), no requeue."""
        entry = _make_entry()
        survived = session_health.SubprocessKillResult(confirmed_dead=False, signal_sent=True)
        with patch.object(session_health, "_confirm_subprocess_dead", return_value=survived):
            assert _run_recovery(entry) is True

        # Finalized to failed; never requeued to pending.
        recovery_patches["finalize"].assert_called_once()
        assert recovery_patches["finalize"].call_args.args[1] == "failed"
        recovery_patches["transition"].assert_not_called()
        # started_at must NOT be nulled into a pending record.
        assert entry.started_at is not None

    def test_subprocess_confirmed_dead_requeues_to_pending(self, recovery_patches):
        """Subprocess confirmed dead via SIGTERM/SIGKILL → existing requeue path runs."""
        entry = _make_entry()
        killed = session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        with patch.object(session_health, "_confirm_subprocess_dead", return_value=killed):
            assert _run_recovery(entry) is True

        recovery_patches["transition"].assert_called_once()
        assert recovery_patches["transition"].call_args.args[1] == "pending"
        # Healthy recovery path nulls started_at and bumps priority.
        assert entry.started_at is None
        assert entry.priority == "high"
        # Not finalized as failed.
        for call in recovery_patches["finalize"].call_args_list:
            assert call.args[1] != "failed"

    def test_no_pid_recorded_requeues_normally(self, recovery_patches):
        """entry.claude_pid is None → _confirm_subprocess_dead True → requeue."""
        entry = _make_entry(claude_pid=None)
        # Do not mock _confirm_subprocess_dead: exercise the real None short-circuit.
        with patch.object(session_health.os, "kill") as mock_kill:
            assert _run_recovery(entry) is True
        mock_kill.assert_not_called()
        recovery_patches["transition"].assert_called_once()
        assert recovery_patches["transition"].call_args.args[1] == "pending"

    def test_escalated_counter_increments_when_signal_was_sent(self, recovery_patches):
        """Confirmed-dead because a SIGTERM/SIGKILL landed → escalated counter increments."""
        entry = _make_entry(claude_pid=4321)
        killed = session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        with (
            patch.object(session_health, "_confirm_subprocess_dead", return_value=killed),
            patch.object(session_health, "_increment_subprocess_kill_counter") as mock_counter,
        ):
            _run_recovery(entry)
        mock_counter.assert_called_once()
        assert mock_counter.call_args.kwargs["escalated"] is True

    def test_escalated_counter_skipped_when_cancel_sufficed(self, recovery_patches):
        """Confirmed-dead WITHOUT a signal (task.cancel sufficed) → no counter increment.

        This is the over-counting fix: an already-dead subprocess must NOT inflate
        the ``subprocess_kill_escalated`` metric.
        """
        entry = _make_entry(claude_pid=4321)
        already_dead = session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        with (
            patch.object(session_health, "_confirm_subprocess_dead", return_value=already_dead),
            patch.object(session_health, "_increment_subprocess_kill_counter") as mock_counter,
        ):
            _run_recovery(entry)
        mock_counter.assert_not_called()

    def test_failed_counter_increments_on_survival(self, recovery_patches):
        """Not-confirmed-dead → failed counter increments."""
        entry = _make_entry()
        survived = session_health.SubprocessKillResult(confirmed_dead=False, signal_sent=True)
        with (
            patch.object(session_health, "_confirm_subprocess_dead", return_value=survived),
            patch.object(session_health, "_increment_subprocess_kill_counter") as mock_counter,
        ):
            _run_recovery(entry)
        mock_counter.assert_called_once()
        assert mock_counter.call_args.kwargs["escalated"] is False
