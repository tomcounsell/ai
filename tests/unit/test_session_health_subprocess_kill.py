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

Fenced pre-cancel snapshot (#2518): the snapshot no longer skips the fence when
the row records no ``create_time``. That condition used to fail OPEN — the raw
pid went straight into a real SIGTERM→SIGKILL — which is exactly what the
canonical rule in ``agent/pid_fence.py`` forbids: unknown never authorizes a
kill. Tests that need the pid to reach ``_confirm_subprocess_dead`` therefore
record a ``create_time`` and run inside ``_fenced()``.
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import session_health

# ==========================================================================
# _confirm_subprocess_dead — signal escalation helper
# ==========================================================================


class TestConfirmSubprocessDead:
    """Direct tests of the SIGTERM->SIGKILL escalation helper.

    Process-GROUP aware (issue #1938): the helper derives the group from the pid
    via ``os.getpgid`` and signals the GROUP with ``os.killpg`` (``pgid == pid``
    under ``start_new_session``) so a detached group with grandchildren (MCP
    servers) is fully reaped, not just the group leader. The tests patch
    ``os.getpgid`` (returns the pid) and ``os.killpg``.

    The helper returns a :class:`session_health.SubprocessKillResult`
    ``(confirmed_dead, signal_sent)``. ``signal_sent`` distinguishes the
    already-dead path (cancel sufficed, no signal) from a genuine SIGTERM/SIGKILL
    escalation so the caller does not over-count the escalated counter.
    """

    def test_none_pid_returns_confirmed_no_signal(self):
        """No PID recorded → nothing to kill → confirmed dead, no signal sent."""
        with (
            patch.object(session_health.os, "killpg") as mock_killpg,
            patch.object(session_health.os, "getpgid") as mock_getpgid,
        ):
            result = session_health._confirm_subprocess_dead(None, timeout=3.0)
        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_killpg.assert_not_called()
        mock_getpgid.assert_not_called()

    def test_nonpositive_pid_returns_confirmed_no_signal(self):
        """pid <= 0 is not a real process → confirmed dead, no signals."""
        with (
            patch.object(session_health.os, "killpg") as mock_killpg,
            patch.object(session_health.os, "getpgid") as mock_getpgid,
        ):
            assert session_health._confirm_subprocess_dead(0, timeout=3.0) == (True, False)
            assert session_health._confirm_subprocess_dead(-1, timeout=3.0) == (True, False)
        mock_killpg.assert_not_called()
        mock_getpgid.assert_not_called()

    def test_string_pid_is_cast_to_int(self):
        """``claude_pid`` off Popoto's IndexedField arrives as a string; must not
        raise ``TypeError`` comparing ``str <= int`` (regression, issue found
        2026-07-08 via a crashed recovery: ``pid <= 0`` on a str pid)."""
        with (
            patch.object(session_health.os, "getpgid", side_effect=ProcessLookupError),
            patch.object(session_health.os, "killpg") as mock_killpg,
        ):
            result = session_health._confirm_subprocess_dead("1234", timeout=3.0)
        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_killpg.assert_not_called()

    def test_non_numeric_string_pid_treated_as_none(self):
        """A garbage/non-numeric pid string must not raise — treated as no PID."""
        with (
            patch.object(session_health.os, "killpg") as mock_killpg,
            patch.object(session_health.os, "getpgid") as mock_getpgid,
        ):
            result = session_health._confirm_subprocess_dead("not-a-pid", timeout=3.0)
        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_killpg.assert_not_called()
        mock_getpgid.assert_not_called()

    def test_group_leader_already_gone_returns_confirmed_no_signal(self):
        """``os.getpgid`` raises ProcessLookupError → leader gone → group gone."""
        with (
            patch.object(session_health.os, "getpgid", side_effect=ProcessLookupError),
            patch.object(session_health.os, "killpg") as mock_killpg,
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_killpg.assert_not_called()

    def test_already_dead_group_probe_returns_confirmed_without_signals(self):
        """First liveness probe (killpg signal 0) raises ProcessLookupError → gone.

        cancel() sufficed: confirmed_dead=True but signal_sent=False, so the caller
        must NOT count this as a kill escalation.
        """
        with (
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(
                session_health.os, "killpg", side_effect=ProcessLookupError
            ) as mock_killpg,
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        assert result.confirmed_dead is True
        assert result.signal_sent is False
        # Only the initial signal-0 group probe; no SIGTERM/SIGKILL.
        assert mock_killpg.call_count == 1
        assert mock_killpg.call_args_list[0].args == (1234, 0)

    def test_sigterm_suffices_reports_signal_sent(self):
        """Group alive at probe, dies after SIGTERM → SIGKILL never sent, signal_sent True."""
        # Sequence of os.killpg behaviors:
        #   probe(0) -> alive (returns)
        #   SIGTERM  -> returns (signal delivered)
        #   poll probe(0) -> ProcessLookupError (now dead)
        calls = []

        def fake_killpg(pgid, sig):
            calls.append(sig)
            if sig == 0 and len(calls) == 1:
                return  # initial probe: alive
            if sig == session_health.signal.SIGTERM:
                return  # SIGTERM delivered
            # Any subsequent signal-0 poll: group has exited.
            raise ProcessLookupError

        with (
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=fake_killpg),
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)

        assert result == session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        assert session_health.signal.SIGTERM in calls
        assert session_health.signal.SIGKILL not in calls

    def test_sigkill_sent_only_when_sigterm_insufficient(self):
        """Group survives SIGTERM grace → SIGKILL escalated, then dies; signal_sent True."""
        sent_signals = []

        def fake_killpg(pgid, sig):
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
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=fake_killpg),
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
        """Group stays alive through SIGTERM and SIGKILL → not confirmed, signal_sent True."""

        def fake_killpg(pgid, sig):
            # Group never dies: signal 0 always returns (alive), signals deliver.
            return

        with (
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=fake_killpg),
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
        """PermissionError on the initial group liveness probe → not confirmed, no signal."""
        with (
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=PermissionError),
        ):
            result = session_health._confirm_subprocess_dead(1234, timeout=3.0)
        assert result == session_health.SubprocessKillResult(
            confirmed_dead=False, signal_sent=False
        )

    def test_permission_error_on_sigterm_returns_not_confirmed_no_signal(self):
        """Probe says alive, SIGTERM raises PermissionError → not confirmed, no signal landed."""

        def fake_killpg(pgid, sig):
            if sig == 0:
                return  # alive
            raise PermissionError

        with (
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=fake_killpg),
        ):
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


#: The ``create_time`` a FENCED test entry records. ``_fenced`` below patches
#: ``proc_create_time`` to return it, so the real fence reads "still ours".
_FENCE_CT = 777.0


@contextlib.contextmanager
def _fenced(live_ct=_FENCE_CT):
    """Make the real fence read ``live_ct`` for every pid this block signals.

    ``agent.pid_fence.proc_create_time`` is the late-bound seam (#2518 spike-4);
    the fabricated pids below have no real process, so without this the fence
    reads "dead" and the pre-cancel snapshot is nulled.
    """
    with patch("agent.pid_fence.proc_create_time", return_value=live_ct):
        yield


def _make_entry(*, claude_pid=4321, recovery_attempts=0, create_time=_FENCE_CT):
    """Minimal AgentSession-like stub for the recovery else branch.

    ``recovery_attempts=0`` keeps us below MAX_RECOVERY_ATTEMPTS so the
    ``else`` requeue/failed branch (not the attempts-exhausted branch) is taken.

    Durability plan #2494: recovery snapshots the pid from the fenced execution
    record (``live_fence``), not ``claude_pid``. #2518: the snapshot is now
    FENCED unconditionally — ``create_time=None`` produces a legacy row, which
    nulls the snapshot rather than failing open into a real SIGTERM→SIGKILL.
    Tests that want the pid to reach ``_confirm_subprocess_dead`` must record a
    ``create_time`` AND run inside ``_fenced()``.
    """
    return SimpleNamespace(
        agent_session_id="sess-1537",
        session_id="sid-1537",
        project_key="test-proj",
        chat_id="chat-1",
        live_fence=(
            {"pid": claude_pid, "create_time": create_time} if claude_pid is not None else None
        ),
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
    ``_deliver_deferred_self_draft_fallback`` and
    ``_deliver_terminal_interrupt_notice`` are stubbed so tests that don't care
    about delivery mechanics (transport resolution, FileOutputHandler fallback)
    never hit real I/O -- the escalation-branch delivery tests below patch
    these explicitly where they matter.
    """
    with (
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
        patch("models.session_lifecycle.transition_status") as mock_transition,
        patch.object(session_health, "_tier2_reprieve_signal", return_value=None),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("popoto.redis_db.POPOTO_REDIS_DB"),
        patch.object(
            session_health, "_deliver_deferred_self_draft_fallback", new_callable=AsyncMock
        ),
        patch.object(
            session_health, "_deliver_terminal_interrupt_notice", new_callable=AsyncMock
        ) as mock_terminal_notice,
    ):
        yield {
            "finalize": mock_finalize,
            "transition": mock_transition,
            "terminal_notice": mock_terminal_notice,
        }


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
        # Otherwise-silent escalation (no deferred, non-tool_timeout kind) now
        # delivers the last-resort terminal notice exactly once.
        recovery_patches["terminal_notice"].assert_awaited_once_with(entry)

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
        """Genuinely-absent pid (claude_pid is None) → real None short-circuit → requeue.

        Re-scoped for #1938: runner sessions now SET ``claude_pid`` on spawn, so
        the "no pid" case is a session with no live subprocess at recovery time
        (between turns / never spawned). No signal of any kind is delivered.
        """
        entry = _make_entry(claude_pid=None)
        # Do not mock _confirm_subprocess_dead: exercise the real None short-circuit.
        with (
            patch.object(session_health.os, "killpg") as mock_killpg,
            patch.object(session_health.os, "getpgid") as mock_getpgid,
        ):
            assert _run_recovery(entry) is True
        mock_killpg.assert_not_called()
        mock_getpgid.assert_not_called()
        recovery_patches["transition"].assert_called_once()
        assert recovery_patches["transition"].call_args.args[1] == "pending"

    def test_runner_session_group_survives_escalates_to_failed(self, recovery_patches):
        """A runner session whose process GROUP will not die → failed, not requeue (#1938).

        Exercises the real ``_confirm_subprocess_dead`` group path end-to-end: a
        live pid whose group never exits under SIGTERM/SIGKILL must NOT be parked
        at ``pending`` as an invisible orphan.
        """
        entry = _make_entry(claude_pid=8080)

        def fake_killpg(pgid, sig):
            return  # group never dies (signal 0 always reports alive)

        # timeout=0 makes the confirm poll return immediately without sleeping
        # or patching ``time`` (patching ``session_health.time.monotonic`` would
        # leak into the event loop's own clock — StopIteration).
        with (
            _fenced(),
            patch.object(session_health.os, "getpgid", lambda pid: pid),
            patch.object(session_health.os, "killpg", side_effect=fake_killpg),
            patch.object(session_health, "SUBPROCESS_KILL_TIMEOUT", 0.0),
        ):
            assert _run_recovery(entry) is True

        recovery_patches["finalize"].assert_called_once()
        assert recovery_patches["finalize"].call_args.args[1] == "failed"
        recovery_patches["transition"].assert_not_called()

    def test_recovery_snapshots_pid_before_teardown_clears_it(self, recovery_patches):
        """Pre-cancel snapshot keeps the confirm targeting the real pid (#1938).

        Durability plan #2494: the runner no longer clears the execution fence on
        teardown, but even if a concurrent unwind nulled it, the recovery path
        snapshots the fenced pid BEFORE ``handle.task.cancel()``. Assert the
        pre-cancel snapshot is what reaches ``_confirm_subprocess_dead``.
        """
        entry = _make_entry(claude_pid=9090)
        seen = {}

        async def _drive():
            async def _hang():
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    # Simulate a concurrent unwind nulling the fence after cancel.
                    entry.live_fence = None
                    raise

            task = asyncio.ensure_future(_hang())
            await asyncio.sleep(0)  # let the task start
            handle = SimpleNamespace(task=task)

            def _capture(pid, *, timeout):
                seen["pid"] = pid
                return session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)

            with (
                _fenced(),
                patch.object(session_health, "_confirm_subprocess_dead", side_effect=_capture),
                patch.object(session_health, "_should_kill_no_progress", return_value=True),
            ):
                await session_health._apply_recovery_transition(
                    entry,
                    reason="no progress",
                    reason_kind="no_progress",
                    handle=handle,
                    worker_key="worker-1",
                )

        asyncio.run(_drive())
        # The confirm saw the pre-cancel snapshot, not the cleared None.
        assert seen["pid"] == 9090


class TestPreCancelSnapshotIsFenced:
    """The pre-cancel snapshot must never hand an unverified pid to a real kill.

    ``_apply_recovery_transition`` feeds ``pid_snapshot`` straight into
    ``_confirm_subprocess_dead``, which escalates SIGTERM → SIGKILL against the
    process GROUP. Before #2518 the guard read
    ``if _snap_pid is not None and _snap_ct is not None and not fence_is_live(...)``
    — so a row with a pid and no recorded ``create_time`` skipped the fence
    entirely and failed OPEN. That is the single condition the canonical rule in
    ``agent/pid_fence.py`` forbids.
    """

    def _capture_snapshot(self, entry, *, live_ct):
        seen = {}

        def _capture(pid, *, timeout):
            seen["pid"] = pid
            return session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=False)

        with (
            _fenced(live_ct),
            patch.object(session_health, "_confirm_subprocess_dead", side_effect=_capture),
        ):
            _run_recovery(entry)
        return seen["pid"]

    def test_matching_fence_passes_the_pid_through(self, recovery_patches):
        entry = _make_entry(claude_pid=4321, create_time=_FENCE_CT)
        assert self._capture_snapshot(entry, live_ct=_FENCE_CT) == 4321

    def test_legacy_row_with_no_recorded_create_time_yields_none(self, recovery_patches):
        """The D8 fail-open fix: unknown identity → no pid to signal.

        The pid probes ALIVE here (``proc_create_time`` returns a real value),
        so the only thing withholding the SIGTERM→SIGKILL is the fence. Before
        #2518 this row's raw pid reached ``_confirm_subprocess_dead`` and a
        process nobody could prove was ours got signalled.
        """
        entry = _make_entry(claude_pid=4321, create_time=None)
        assert self._capture_snapshot(entry, live_ct=_FENCE_CT) is None

    def test_recycled_fence_yields_none(self, recovery_patches):
        entry = _make_entry(claude_pid=4321, create_time=_FENCE_CT)
        assert self._capture_snapshot(entry, live_ct=_FENCE_CT + 5000.0) is None

    def test_dead_pid_yields_none(self, recovery_patches):
        entry = _make_entry(claude_pid=4321, create_time=_FENCE_CT)
        assert self._capture_snapshot(entry, live_ct=None) is None

    def test_no_fence_at_all_yields_none(self, recovery_patches):
        entry = _make_entry(claude_pid=None)
        assert self._capture_snapshot(entry, live_ct=_FENCE_CT) is None

    def test_legacy_row_delivers_no_signal_end_to_end(self, recovery_patches):
        """Run the REAL ``_confirm_subprocess_dead``: no signal of any kind lands."""
        entry = _make_entry(claude_pid=4321, create_time=None)

        with (
            _fenced(),
            patch.object(session_health.os, "killpg") as mock_killpg,
            patch.object(session_health.os, "getpgid") as mock_getpgid,
        ):
            assert _run_recovery(entry) is True

        mock_killpg.assert_not_called()
        mock_getpgid.assert_not_called()

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


# ==========================================================================
# _deliver_terminal_interrupt_notice — escalation-branch last-resort voice
# (silent-resume inversion: the pre-cancel prediction wrote nothing for a
# non-terminal outcome, so the send sites stayed silent; when the subprocess
# then survives, this helper is the only remaining voice.)
# ==========================================================================


def _terminal_entry(
    *,
    session_id: str = "sess-terminal",
    project_key: str = "test-proj",
    extra_context: dict | None = None,
    chat_id: str = "chat-1",
    telegram_message_id: int = 42,
) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        project_key=project_key,
        extra_context=extra_context or {},
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
    )


class TestDeliverTerminalInterruptNotice:
    """Direct tests of `_deliver_terminal_interrupt_notice`."""

    def test_subprocess_survived_escalation_delivers_no_resume_when_no_earlier_send(self):
        """Dedup key free (no earlier send-site delivery) → INTERRUPT_NO_RESUME sent,
        using the exact shared `interrupted-sent` key/TTL the two send sites use."""
        from agent.notification_copy import INTERRUPT_NO_RESUME

        send_cb = AsyncMock()
        entry = _terminal_entry(session_id="sess-fresh")
        redis_db = MagicMock()
        redis_db.set = MagicMock(return_value=True)  # dedup key acquired

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)),
        ):
            asyncio.run(session_health._deliver_terminal_interrupt_notice(entry))

        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1] == INTERRUPT_NO_RESUME
        redis_db.set.assert_called_once_with(
            f"interrupted-sent:{entry.session_id}", "1", nx=True, ex=120
        )

    def test_escalation_send_deduped_when_interrupted_sent_already_held(self):
        """The shared `interrupted-sent` key is already held (a send site fired
        earlier) → the escalation helper sends nothing (no double message)."""
        send_cb = AsyncMock()
        entry = _terminal_entry(session_id="sess-held")
        redis_db = MagicMock()
        redis_db.set = MagicMock(return_value=False)  # key already held elsewhere

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)),
        ):
            asyncio.run(session_health._deliver_terminal_interrupt_notice(entry))

        send_cb.assert_not_awaited()

    def test_terminal_interrupt_notice_sends_when_dedup_redis_errors(self, caplog):
        """Fail-open (Concern 1): a Redis exception during dedup acquisition must
        NOT silence the send — only a legitimate `acquired is False` suppresses."""
        from agent.notification_copy import INTERRUPT_NO_RESUME

        send_cb = AsyncMock()
        entry = _terminal_entry(session_id="sess-err")
        redis_db = MagicMock()
        redis_db.set = MagicMock(side_effect=RuntimeError("redis down"))

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)),
            caplog.at_level("WARNING"),
        ):
            asyncio.run(session_health._deliver_terminal_interrupt_notice(entry))

        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1] == INTERRUPT_NO_RESUME
        assert any(
            "lock failed" in record.message or "lock failed" in record.getMessage()
            for record in caplog.records
        )

        # A legitimate acquired=False (key already held) instead suppresses the send.
        send_cb.reset_mock()
        redis_db_held = MagicMock()
        redis_db_held.set = MagicMock(return_value=False)
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db_held),
            patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)),
        ):
            asyncio.run(session_health._deliver_terminal_interrupt_notice(entry))
        send_cb.assert_not_awaited()


# ==========================================================================
# _apply_recovery_transition — subprocess-survived escalation branch must
# never double-message across its three sibling deliveries (critique BLOCKER)
# ==========================================================================


def _escalation_entry(
    *,
    claude_pid=4321,
    recovery_attempts=0,
    extra_context: dict | None = None,
    current_tool_name: str | None = "mcp__svc",
) -> SimpleNamespace:
    return SimpleNamespace(
        agent_session_id="sess-esc",
        session_id="sid-esc",
        project_key="test-proj",
        chat_id="chat-1",
        telegram_message_id=42,
        live_fence=({"pid": claude_pid, "create_time": None} if claude_pid is not None else None),
        recovery_attempts=recovery_attempts,
        reprieve_count=0,
        priority="normal",
        started_at="2026-06-03T00:00:00Z",
        response_delivered_at=None,
        exit_returncode=0,
        is_project_keyed=False,
        current_tool_name=current_tool_name,
        extra_context=extra_context or {},
        save=lambda **kw: None,
    )


def _run_escalation(entry, *, reason_kind):
    """Drive `_apply_recovery_transition` down the subprocess-survived escalation
    branch (confirmed_dead=False) with the three sibling deliveries spied on."""
    survived = session_health.SubprocessKillResult(confirmed_dead=False, signal_sent=True)
    with (
        patch("models.session_lifecycle.finalize_session"),
        patch("models.session_lifecycle.transition_status"),
        patch.object(session_health, "_tier2_reprieve_signal", return_value=None),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("popoto.redis_db.POPOTO_REDIS_DB"),
        patch.object(session_health, "_confirm_subprocess_dead", return_value=survived),
        patch.object(
            session_health, "_deliver_deferred_self_draft_fallback", new_callable=AsyncMock
        ) as mock_deferred,
        patch.object(
            session_health, "_deliver_tool_timeout_degraded_notice", new_callable=AsyncMock
        ) as mock_degraded,
        patch.object(
            session_health, "_deliver_terminal_interrupt_notice", new_callable=AsyncMock
        ) as mock_terminal,
    ):
        asyncio.run(
            session_health._apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind=reason_kind,
                handle=None,
                worker_key="worker-1",
            )
        )
    return mock_deferred, mock_degraded, mock_terminal


def test_escalation_branch_attempts_at_most_one_user_facing_send():
    """Critique BLOCKER guard: across all three branch shapes, the
    subprocess-survived escalation branch attempts at most one user-facing
    send. This verifies call-intent mutual exclusion, not delivery success —
    the mocked sibling helpers here always resolve truthy regardless of the
    real send outcome, so it cannot observe whether a message actually
    reached the user. `_deliver_tool_timeout_degraded_notice` now returns a
    bool reflecting real delivery, and the escalation branch gates
    `_degraded_sent` on that return value (not on having merely called it) —
    see its docstring for the residual edge case that return value doesn't
    cover.

    `_deliver_deferred_self_draft_fallback` is called unconditionally by the
    branch in every case (its own internal `deferred_self_draft_pending` gate
    is covered separately in test_mcp_hang_graceful_degradation.py); what this
    guard verifies is that the degraded notice and the new terminal notice
    are mutually exclusive with it and with each other.
    """
    # (i) Deferred self-draft pending -> real answer sent, degraded and
    # terminal notices both gated off by `not _has_deferred`.
    entry_i = _escalation_entry(extra_context={"deferred_self_draft_pending": True})
    _deferred_i, degraded_i, terminal_i = _run_escalation(entry_i, reason_kind="tool_timeout")
    degraded_i.assert_not_awaited()
    terminal_i.assert_not_awaited()

    # (ii) reason_kind == "tool_timeout", no deferred -> degraded notice sent,
    # terminal notice gated off by `not _degraded_sent`.
    entry_ii = _escalation_entry(extra_context={})
    _deferred_ii, degraded_ii, terminal_ii = _run_escalation(entry_ii, reason_kind="tool_timeout")
    degraded_ii.assert_awaited_once()
    terminal_ii.assert_not_awaited()

    # (iii) no deferred, non-tool_timeout kind -> terminal notice is the only
    # send (last-resort voice for an otherwise-silent branch).
    entry_iii = _escalation_entry(extra_context={})
    _deferred_iii, degraded_iii, terminal_iii = _run_escalation(
        entry_iii, reason_kind="no_progress"
    )
    degraded_iii.assert_not_awaited()
    terminal_iii.assert_awaited_once()


def test_escalation_branch_speaks_when_degraded_notice_silently_fails():
    """Regression guard for the call-intent-vs-delivery-success gap flagged in
    plan critique concern #2: if `_deliver_tool_timeout_degraded_notice`
    resolves to False (its send callback raised and it swallowed the
    exception per its own contract), the branch must NOT mistake the mere
    *attempt* for a delivered message — the terminal notice must still fire,
    or the escalation branch would produce a fully silent terminal failure,
    the exact regression class this issue exists to prevent.
    """
    entry = _escalation_entry(extra_context={})
    survived = session_health.SubprocessKillResult(confirmed_dead=False, signal_sent=True)
    with (
        patch("models.session_lifecycle.finalize_session"),
        patch("models.session_lifecycle.transition_status"),
        patch.object(session_health, "_tier2_reprieve_signal", return_value=None),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("popoto.redis_db.POPOTO_REDIS_DB"),
        patch.object(session_health, "_confirm_subprocess_dead", return_value=survived),
        patch.object(
            session_health, "_deliver_deferred_self_draft_fallback", new_callable=AsyncMock
        ),
        patch.object(
            session_health,
            "_deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_degraded,
        patch.object(
            session_health, "_deliver_terminal_interrupt_notice", new_callable=AsyncMock
        ) as mock_terminal,
    ):
        asyncio.run(
            session_health._apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind="tool_timeout",
                handle=None,
                worker_key="worker-1",
            )
        )
    mock_degraded.assert_awaited_once()
    mock_terminal.assert_awaited_once()
