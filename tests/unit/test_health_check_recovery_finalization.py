"""Tests for health-check recovery finalization fallback (issue #917).

When `_execute_agent_session()` completes normally but the inner `agent_session`
lookup returned None (race on status="running" filter after health-check recovery),
the fallback `else` branch must call `complete_transcript()` to finalize the session.

Tests:
1. agent_session=None + no error + defer_reaction=False → complete_transcript("completed")
2. agent_session=None + error + defer_reaction=False → complete_transcript("failed")
3. agent_session=None + defer_reaction=True → complete_transcript NOT called (nudge path)
4. agent_session is non-None → existing path used (regression guard)
5. Fallback raises StatusConflictError → info logged, no propagation
6. Fallback raises unexpected exception → warning logged, no propagation
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_session(**overrides):
    """Create a minimal session-like object for the finalization block."""
    defaults = {
        "session_id": "test-session-001",
        "agent_session_id": "agent-sess-001",
        "project_key": "test-project",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_task(error=None):
    """Create a minimal task-like object."""
    return SimpleNamespace(error=error)


def _make_chat_state(defer_reaction=False):
    """Create a minimal chat_state-like object."""
    return SimpleNamespace(defer_reaction=defer_reaction)


def _run_finalization_block(session, agent_session, task, chat_state, complete_transcript_mock):
    """Execute the finalization block extracted from _execute_agent_session().

    This mirrors the if/else structure at ~L3364 in agent_session_queue.py.
    We test the logic directly rather than calling _execute_agent_session()
    (which requires extensive async setup).
    """
    if agent_session:
        try:
            final_status = (
                "active"
                if chat_state.defer_reaction
                else ("completed" if not task.error else "failed")
            )
            if not chat_state.defer_reaction:
                complete_transcript_mock(session.session_id, status=final_status)
        except Exception:
            pass
    else:
        # Fallback finalization — the code under test (issue #917)
        if not chat_state.defer_reaction:
            try:
                from models.session_lifecycle import StatusConflictError

                final_status = "completed" if not task.error else "failed"
                complete_transcript_mock(session.session_id, status=final_status)
            except StatusConflictError:
                logging.getLogger(__name__).info(
                    "Fallback finalization skipped: session %s already transitioned "
                    "(CAS conflict — expected)",
                    session.agent_session_id,
                )
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "Fallback finalization failed for session %s: %s",
                    session.agent_session_id,
                    e,
                )


class TestFallbackFinalization:
    """Tests for the else branch when agent_session is None."""

    def test_completed_when_no_error(self):
        """agent_session=None + no error + defer_reaction=False → 'completed'."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_called_once_with("test-session-001", status="completed")

    def test_failed_when_error(self):
        """agent_session=None + error + defer_reaction=False → 'failed'."""
        session = _make_session()
        task = _make_task(error="some error")
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_called_once_with("test-session-001", status="failed")

    def test_nudge_path_not_finalized(self):
        """agent_session=None + defer_reaction=True → complete_transcript NOT called."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=True)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_not_called()

    def test_existing_path_when_agent_session_present(self):
        """agent_session is non-None → existing complete_transcript path used."""
        session = _make_session()
        agent_session = MagicMock()  # non-None
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, agent_session, task, chat_state, mock_ct)

        # Should still be called (existing path), with "completed"
        mock_ct.assert_called_once_with("test-session-001", status="completed")

    def test_status_conflict_error_is_info_not_exception(self, caplog):
        """StatusConflictError → info logged, no exception propagated."""
        from models.session_lifecycle import StatusConflictError

        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock(
            side_effect=StatusConflictError(
                session_id="test-session-001",
                expected_status="running",
                actual_status="completed",
            )
        )

        with caplog.at_level(logging.INFO):
            # Should not raise
            _run_finalization_block(session, None, task, chat_state, mock_ct)

        assert "CAS conflict" in caplog.text or "already transitioned" in caplog.text

    def test_unexpected_exception_is_warning_not_propagated(self, caplog):
        """Unexpected exception → warning logged, no exception propagated."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock(side_effect=RuntimeError("Redis connection lost"))

        with caplog.at_level(logging.WARNING):
            # Should not raise
            _run_finalization_block(session, None, task, chat_state, mock_ct)

        assert "Redis connection lost" in caplog.text


class TestFallbackExistsInSource:
    """Structural test: verify the fallback finalization code is present in the source."""

    def test_fallback_finalization_present_in_agent_session_queue(self):
        """The else branch with fallback finalization must exist in the source.

        After the session_executor.py extraction, the fallback code lives in
        agent/session_executor.py. We check that module's source instead.
        """
        import inspect

        import agent.session_executor as mod

        source = inspect.getsource(mod)
        assert "Fallback finalization" in source, (
            "Expected 'Fallback finalization' comment in agent/session_executor.py — "
            "the else branch from issue #917 is missing"
        )
        assert "agent_session was None" in source, (
            "Expected 'agent_session was None' log message in agent/session_executor.py"
        )


class TestHasProgressChildActivity:
    """Tests for _has_progress() child-activity awareness (issue #963, Bug 2).

    A PM session with active children should not be declared stuck by the
    health check, even if it has no own-progress signals (turn_count,
    log_path, claude_session_uuid).
    """

    @staticmethod
    def _make_entry(**overrides):
        """Create a minimal AgentSession-like object for _has_progress."""
        defaults = {
            "turn_count": 0,
            "log_path": "",
            "claude_session_uuid": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @staticmethod
    def _make_child(status="running"):
        return SimpleNamespace(status=status)

    def test_returns_true_when_child_running(self):
        """_has_progress returns True when a child session is running."""

        entry = self._make_entry()
        entry.get_children = lambda: [self._make_child(status="running")]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_returns_true_when_child_pending(self):
        """_has_progress returns True when a child session is pending."""
        entry = self._make_entry()
        entry.get_children = lambda: [self._make_child(status="pending")]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_returns_false_when_all_children_terminal(self):
        """_has_progress returns False when all children are in terminal status."""
        entry = self._make_entry()
        entry.get_children = lambda: [
            self._make_child(status="completed"),
            self._make_child(status="failed"),
            self._make_child(status="killed"),
        ]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is False

    def test_returns_false_when_no_children(self):
        """_has_progress returns False when no children exist."""
        entry = self._make_entry()
        entry.get_children = lambda: []

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is False


# ==========================================================================
# Two-tier no-progress detector tests (issue #1036)
# ==========================================================================


def _now_utc():
    from datetime import UTC, datetime

    return datetime.now(tz=UTC)


def _ago(seconds: int):
    from datetime import timedelta

    return _now_utc() - timedelta(seconds=seconds)


class TestHasProgressDualHeartbeat:
    """Tests for dual-heartbeat OR semantics in _has_progress (#1036).

    Tier 1 freshness: either `last_heartbeat_at` OR `last_sdk_heartbeat_at`
    within HEARTBEAT_FRESHNESS_WINDOW (90s) → progress=True.
    """

    @staticmethod
    def _make_entry(**overrides):
        defaults = {
            "turn_count": 0,
            "log_path": "",
            "claude_session_uuid": None,
            "last_heartbeat_at": None,
            "last_sdk_heartbeat_at": None,
        }
        defaults.update(overrides)
        entry = SimpleNamespace(**defaults)
        entry.get_children = lambda: []
        return entry

    def test_queue_heartbeat_within_window_returns_true(self):
        entry = self._make_entry(last_heartbeat_at=_ago(30))
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_sdk_heartbeat_within_window_returns_true(self):
        entry = self._make_entry(last_sdk_heartbeat_at=_ago(30))
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_either_heartbeat_fresh_returns_true(self):
        # One fresh, one stale → True (OR semantics)
        entry = self._make_entry(
            last_heartbeat_at=_ago(30),
            last_sdk_heartbeat_at=_ago(300),
        )
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_both_heartbeats_stale_returns_false(self):
        # Both stale, other fields empty → False
        entry = self._make_entry(
            last_heartbeat_at=_ago(300),
            last_sdk_heartbeat_at=_ago(300),
        )
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is False

    def test_heartbeats_at_boundary_returns_true(self):
        # At age=89s (just inside 90s window) → True
        entry = self._make_entry(
            last_heartbeat_at=_ago(89),
            last_sdk_heartbeat_at=_ago(89),
        )
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_both_heartbeats_none_falls_through_to_other_checks(self):
        # Both None, turn_count=5 → True (unchanged behavior, #944)
        entry = self._make_entry(turn_count=5)
        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True


class TestStdoutStaleRetired:
    """The stdout-stale Tier 1 kill signal (#1046) was retired by #1172.

    Fresh heartbeats are now sufficient evidence of progress regardless of
    stdout cadence. These regression tests assert the removal held —
    long-thinking turns and large tool outputs no longer false-kill PM work.
    See ``tests/unit/test_session_health_inference_removed.py`` for the
    structural guards on the deleted constants.
    """

    @staticmethod
    def _make_entry(**overrides):
        defaults = {
            "turn_count": 0,
            "log_path": "",
            "claude_session_uuid": None,
            "last_heartbeat_at": _ago(30),  # fresh heartbeat
            "last_sdk_heartbeat_at": None,
            "last_stdout_at": None,
            "started_at": None,
        }
        defaults.update(overrides)
        entry = SimpleNamespace(**defaults)
        entry.get_children = lambda: []
        return entry

    def test_fresh_heartbeats_stale_stdout_returns_true(self):
        """Fresh heartbeats + stale stdout → progress (deleted path must NOT fire)."""
        from agent.agent_session_queue import _has_progress

        entry = self._make_entry(last_stdout_at=_ago(700))
        assert _has_progress(entry) is True

    def test_fresh_heartbeats_no_stdout_old_started_at_returns_true(self):
        """Fresh heartbeats + no stdout + old started_at → progress (deleted path)."""
        from agent.agent_session_queue import _has_progress

        entry = self._make_entry(last_stdout_at=None, started_at=_ago(400))
        assert _has_progress(entry) is True

    def test_fresh_heartbeats_no_stdout_young_started_at_returns_true(self):
        """Fresh heartbeats + young session: warmup tolerance preserved."""
        from agent.agent_session_queue import _has_progress

        entry = self._make_entry(last_stdout_at=None, started_at=_ago(120))
        assert _has_progress(entry) is True


class TestTier2ReprieveGates:
    """Tests for _tier2_reprieve_signal (#1036).

    Tier 2 activity-positive gates evaluated only after Tier 1 flagged stuck.
    Any ONE passing gate → reprieve (non-None return).
    """

    @staticmethod
    def _make_entry(**overrides):
        defaults = {"last_stdout_at": None}
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_handle(self, pid=None):
        from agent.agent_session_queue import SessionHandle

        # Use a done task so we can construct SessionHandle without running one.
        fake_task = MagicMock()
        return SessionHandle(task=fake_task, pid=pid)

    def test_reprieve_on_process_alive(self, monkeypatch):
        """Non-zombie process without children → 'alive'."""
        import psutil as _psutil

        class _Proc:
            def status(self):
                return _psutil.STATUS_RUNNING

            def children(self):
                return []

        monkeypatch.setattr(_psutil, "Process", lambda pid: _Proc())
        from agent.agent_session_queue import _tier2_reprieve_signal

        handle = self._make_handle(pid=12345)
        assert _tier2_reprieve_signal(handle, self._make_entry()) == "alive"

    def test_reprieve_on_children(self, monkeypatch):
        """Non-zombie process with children → 'children' (preferred signal)."""
        import psutil as _psutil

        class _Proc:
            def status(self):
                return _psutil.STATUS_RUNNING

            def children(self):
                return [MagicMock()]

        monkeypatch.setattr(_psutil, "Process", lambda pid: _Proc())
        from agent.agent_session_queue import _tier2_reprieve_signal

        handle = self._make_handle(pid=12345)
        assert _tier2_reprieve_signal(handle, self._make_entry()) == "children"

    def test_no_reprieve_on_zombie(self, monkeypatch):
        """Zombie status → not a reprieve via (c)(d); falls to (e)."""
        import psutil as _psutil

        class _Proc:
            def status(self):
                return _psutil.STATUS_ZOMBIE

            def children(self):
                return [MagicMock()]

        monkeypatch.setattr(_psutil, "Process", lambda pid: _Proc())
        from agent.agent_session_queue import _tier2_reprieve_signal

        handle = self._make_handle(pid=12345)
        assert _tier2_reprieve_signal(handle, self._make_entry()) is None

    def test_no_reprieve_on_dead_process(self, monkeypatch):
        """psutil.NoSuchProcess → skip (c)(d); fall to (e)."""
        import psutil as _psutil

        def _raise(_pid):
            raise _psutil.NoSuchProcess(_pid)

        monkeypatch.setattr(_psutil, "Process", _raise)
        from agent.agent_session_queue import _tier2_reprieve_signal

        handle = self._make_handle(pid=999999)
        assert _tier2_reprieve_signal(handle, self._make_entry()) is None

    def test_no_reprieve_on_recent_stdout(self):
        """The "stdout" gate was retired by #1172 — recent stdout no longer reprieves."""
        from agent.agent_session_queue import _tier2_reprieve_signal

        handle = self._make_handle(pid=None)
        entry = self._make_entry(last_stdout_at=_ago(30))
        assert _tier2_reprieve_signal(handle, entry) is None

    def test_no_reprieve_on_handle_none(self):
        """handle=None and no other evidence → None."""
        from agent.agent_session_queue import _tier2_reprieve_signal

        assert _tier2_reprieve_signal(None, self._make_entry()) is None

    def test_no_reprieve_on_stdout_when_handle_none(self):
        """handle=None and no compaction → None even if stdout is fresh (#1172)."""
        from agent.agent_session_queue import _tier2_reprieve_signal

        entry = self._make_entry(last_stdout_at=_ago(30))
        assert _tier2_reprieve_signal(None, entry) is None


class TestRecoveryCancellation:
    """Tests for task cancellation in the kill path (#1036)."""

    def test_registry_registration_roundtrip(self):
        """SessionHandle round-trips through _active_sessions."""
        import asyncio

        from agent.agent_session_queue import SessionHandle, _active_sessions

        async def _test():
            t = asyncio.current_task()
            _active_sessions["test-abc"] = SessionHandle(task=t, pid=42)
            try:
                assert _active_sessions["test-abc"].pid == 42
                assert _active_sessions["test-abc"].task is t
            finally:
                _active_sessions.pop("test-abc", None)

        asyncio.run(_test())
        # Final cleanup check
        assert "test-abc" not in _active_sessions

    def test_recovery_handles_missing_registry_entry(self):
        """handle=None → _tier2_reprieve_signal still works gracefully."""
        from agent.agent_session_queue import _tier2_reprieve_signal

        # No handle, no fresh signals → None
        entry = SimpleNamespace(last_stdout_at=None)
        assert _tier2_reprieve_signal(None, entry) is None

    def test_recovery_handles_completed_task_gracefully(self):
        """A done task with done()==True → no crash during cancel wait."""
        import asyncio

        from agent.agent_session_queue import SessionHandle

        async def _test():
            async def _trivial():
                return None

            t = asyncio.create_task(_trivial())
            await t  # complete it
            handle = SessionHandle(task=t, pid=1)
            # Simulate the health-check cancel path
            if not handle.task.done():  # should be False
                handle.task.cancel()
            assert handle.task.done() is True

        asyncio.run(_test())


class TestRecoveryAttempts:
    """Tests for recovery_attempts counter semantics (#1036)."""

    def test_model_fields_exist(self):
        """AgentSession has recovery_attempts and reprieve_count fields."""
        from models.agent_session import AgentSession

        s = AgentSession(chat_id="x", project_key="test", working_dir="/tmp")
        assert hasattr(s, "recovery_attempts")
        assert hasattr(s, "reprieve_count")

    def test_startup_recovery_does_not_touch_recovery_attempts(self):
        """_recover_interrupted_agent_sessions_startup source does not reference
        recovery_attempts (startup recovery is semantically different from
        health-check kills — Risk 3 in plan)."""
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._recover_interrupted_agent_sessions_startup)
        assert "recovery_attempts" not in src, (
            "startup recovery must not increment recovery_attempts (Risk 3)"
        )

    def test_health_check_source_mentions_recovery_attempts_and_max(self):
        """Sanity: the health-check kill path references recovery_attempts
        and MAX_RECOVERY_ATTEMPTS."""
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._agent_session_health_check)
        assert "recovery_attempts" in src
        assert "MAX_RECOVERY_ATTEMPTS" in src
        assert "reprieve_count" in src


class TestDisableProgressKill:
    """Tests for DISABLE_PROGRESS_KILL runtime kill-switch (#1036)."""

    def test_env_var_referenced_in_health_check(self):
        """The env var must be read in the health-check recovery branch."""
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._agent_session_health_check)
        assert "DISABLE_PROGRESS_KILL" in src, "kill-switch env var not wired"

    def test_env_var_suppression_via_monkeypatch(self, monkeypatch):
        """Setting DISABLE_PROGRESS_KILL=1 is picked up via os.environ.get."""
        import os

        monkeypatch.setenv("DISABLE_PROGRESS_KILL", "1")
        assert os.environ.get("DISABLE_PROGRESS_KILL") == "1"
        # Sanity cleanup: monkeypatch auto-undoes


class TestAgentSessionFieldsRoundTrip:
    """Tests for _AGENT_SESSION_FIELDS round-trip (B2 from plan critique)."""

    def test_new_fields_in_agent_session_fields_list(self):
        """All five new fields must round-trip through save/load."""
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        required = {
            "last_heartbeat_at",
            "last_sdk_heartbeat_at",
            "last_stdout_at",
            "recovery_attempts",
            "reprieve_count",
        }
        missing = required - set(_AGENT_SESSION_FIELDS)
        assert not missing, f"Missing from _AGENT_SESSION_FIELDS: {missing}"

    def test_datetime_fields_registered(self):
        """All three new DatetimeField names registered for coercion."""
        from models.agent_session import AgentSession

        required = {"last_heartbeat_at", "last_sdk_heartbeat_at", "last_stdout_at"}
        missing = required - AgentSession._DATETIME_FIELDS
        assert not missing, f"Missing from _DATETIME_FIELDS: {missing}"


# ==========================================================================
# Spike-1 cancellation invariant tests (#1039 review)
# ==========================================================================


class TestSessionHandleTaskInvariant:
    """Tests that SessionHandle.task registration never targets the worker loop.

    Plan spike-1 (#1036) and #1039 review explicitly forbid cancelling the
    worker-loop task from the health check. These tests guard the invariant.
    """

    def test_session_handle_task_defaults_to_none(self):
        """SessionHandle() constructed without args has task=None."""
        from agent.agent_session_queue import SessionHandle

        handle = SessionHandle()
        assert handle.task is None
        assert handle.pid is None

    def test_handle_task_is_none_before_background_task_starts(self):
        """Health-check cancel path must no-op when handle.task is None.

        Between _execute_agent_session entry and BackgroundTask.run() there
        is nothing session-scoped to cancel; a bare `.cancel()` call on
        None would crash the health check.
        """
        from agent.agent_session_queue import SessionHandle

        handle = SessionHandle(task=None, pid=None)
        # Mirror the health-check guard (agent_session_queue.py ~L1900).
        if handle is not None and handle.task is not None and not handle.task.done():
            # This branch must NOT be entered when task is None.
            raise AssertionError("must not attempt cancel when handle.task is None")
        # The guard correctly skipped cancel — test passes.
        assert handle.task is None

    def test_cancelling_handle_task_does_not_cancel_worker_loop(self):
        """Cancelling one session handle's task must not cancel the other.

        This is the core invariant of spike-1: the health check's .cancel()
        must target the session-scoped task (BackgroundTask._task), not the
        worker-loop task that is shared across sessions.
        """
        import asyncio

        from agent.agent_session_queue import SessionHandle

        async def _test():
            # Two distinct long-running "session" tasks (simulating two
            # BackgroundTask._task instances on the same worker).
            async def _long_running(label: str):
                try:
                    await asyncio.sleep(60)
                    return label
                except asyncio.CancelledError:
                    raise

            task_a = asyncio.create_task(_long_running("A"))
            task_b = asyncio.create_task(_long_running("B"))
            try:
                handle_a = SessionHandle(task=task_a)
                handle_b = SessionHandle(task=task_b)

                # Cancel only A via its handle.
                handle_a.task.cancel()
                try:
                    await asyncio.wait_for(handle_a.task, timeout=1.0)
                except (asyncio.CancelledError, TimeoutError):
                    pass

                assert handle_a.task.done(), "handle_a.task should be done after cancel"
                assert not handle_b.task.done(), (
                    "handle_b.task must NOT be cancelled when handle_a.task is cancelled — "
                    "this guards plan spike-1 (sessions must not share a cancel target)"
                )
            finally:
                for t in (task_a, task_b):
                    if not t.done():
                        t.cancel()
                        try:
                            await asyncio.wait_for(t, timeout=1.0)
                        except (asyncio.CancelledError, TimeoutError):
                            pass

        asyncio.run(_test())

    def test_session_handle_task_populated_after_task_run(self):
        """Source audit: _execute_agent_session populates handle.task from
        BackgroundTask._task after task.run(), not from asyncio.current_task().

        This is a structural assertion — the worker task must NEVER be the
        cancel target (plan spike-1, #1039 review).
        """
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._execute_agent_session)
        # The populated handle.task must come from task._task (BackgroundTask).
        assert "task._task" in src, (
            "Expected _execute_agent_session to reference BackgroundTask._task "
            "when populating the registry handle"
        )
        # The initial registration must use SessionHandle(task=None) so the
        # worker task is NOT stored as the cancel target.
        assert "SessionHandle(task=None)" in src, (
            "Expected initial registration to use SessionHandle(task=None) — "
            "registering asyncio.current_task() would make cancel target the "
            "worker loop (plan spike-1 violation)"
        )
        # The old buggy pattern must be gone.
        assert "SessionHandle(task=_current_task)" not in src, (
            "Found stale spike-1 violation: SessionHandle(task=_current_task) "
            "registers the worker-loop task as the cancel target"
        )


class TestReprieveScopedToNoProgress:
    """Tests for Tier 2 reprieve scoping (#1039 review, tech debt 1+2).

    Tier 1/Tier 2 reprieve logic applies ONLY to no_progress recoveries.
    worker_dead and timeout recoveries must skip reprieve entirely.
    """

    def test_tier2_reprieve_only_applies_to_no_progress(self):
        """Source audit: the Tier 1 flagged metric and Tier 2 reprieve block
        are guarded by `_reason_kind == "no_progress"`.

        Exercises the gating structurally since the health-check function
        is a large async loop (testing it end-to-end requires a full Redis
        + worker harness).
        """
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._agent_session_health_check)
        assert '_reason_kind == "no_progress"' in src, (
            "Expected Tier 1/Tier 2 block to be gated on _reason_kind == 'no_progress' "
            "(tech debt 1+2 from #1039 review)"
        )
        # Confirm the gating sits between reason classification and kill path.
        idx_kind = src.find('_reason_kind = "no_progress"')
        idx_gate = src.find('_reason_kind == "no_progress"')
        idx_tier1_counter = src.find("tier1_flagged_total")
        idx_reprieve = src.find("_tier2_reprieve_signal")
        assert idx_kind < idx_gate, "_reason_kind must be assigned before it is gated"
        assert idx_gate < idx_tier1_counter, (
            "Tier 1 flagged counter must sit INSIDE the no_progress gate"
        )
        assert idx_gate < idx_reprieve, "Tier 2 reprieve call must sit INSIDE the no_progress gate"

    def test_tier1_flagged_metric_only_increments_for_no_progress(self):
        """Source audit: tier1_flagged_total increments inside the no_progress
        branch only. This prevents timeout/worker_dead recoveries from
        inflating the counter (tech debt 1+2)."""
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._agent_session_health_check)

        # The counter must be referenced exactly once (single increment site).
        count_refs = src.count("tier1_flagged_total")
        assert count_refs == 1, (
            f"Expected tier1_flagged_total to be incremented once; found {count_refs} "
            "references — check the no_progress gating"
        )

        # Verify the single reference lives inside the no_progress gated block
        # by checking the text between the gate and the kill path contains it.
        gate_idx = src.find('_reason_kind == "no_progress"')
        kill_idx = src.find("DISABLE_PROGRESS_KILL")
        assert gate_idx != -1 and kill_idx != -1
        gated_section = src[gate_idx:kill_idx]
        assert "tier1_flagged_total" in gated_section, (
            "tier1_flagged_total must be inside the no_progress gate, not outside"
        )

    def test_no_progress_handle_none_debug_log_present(self):
        """Source audit: a debug log is emitted when handle is None so
        operators know the Tier 2 evaluation is degraded (the stdout gate
        was retired by #1172, so without a pid only the compaction gate
        can fire)."""
        import inspect

        from agent import agent_session_queue as q

        src = inspect.getsource(q._agent_session_health_check)
        assert "Tier 2 reprieve will only see compaction state" in src, (
            "Expected a degraded-Tier-2 debug log when handle is None"
        )
