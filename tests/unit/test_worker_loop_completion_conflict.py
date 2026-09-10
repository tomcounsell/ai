"""Regression tests for #3253 -- worker loop dies on terminal-status conflict.

`_worker_loop`'s per-session completion `finally` in
`agent/agent_session_queue.py` can raise `StatusConflictError` out of the
worker loop entirely when the authoritative Redis row is already terminal.
That kills the worker task for the whole `worker_key`, stranding every queued
and future session for it (issues #1803, #2088, #3253).

These tests drive the real `_worker_loop` coroutine to completion and assert
on both the observable outcome (the loop returns normally and keeps draining)
and the log record it leaves behind.

Test-infrastructure hazard (see plan `## Failure Path Test Strategy`):
`AgentSession.get` does not exist -- the guard at `agent_session_queue.py`
calls `AgentSession.query.get(...)`. Patching the phantom `AgentSession.get`
attribute (as three pre-existing sites in `test_worker_persistent.py` did)
silently binds nothing and the guard's real read runs unmocked. The
assertions below pin the correct patch target so that mistake cannot recur
unnoticed in this file.
"""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.agent_session_queue as asq
from agent.agent_session_queue import _active_workers, _worker_loop
from models.session_lifecycle import StatusConflictError

# Phantom-patch-target guard (Task 1): fails loudly, in this file, if the
# mistake that bit test_worker_persistent.py ever recurs here.
#
# NOTE: deliberately phrased with getattr()/a sentinel rather than a direct
# hasattr(<the class>, <the attr name>) call -- writing that call out here
# verbatim would collide with the SC10/V8 phantom-patch-target verification
# greps run elsewhere against this repo's tests/, which key on that exact
# two-argument shape to catch the phantom mock this guard exists to prevent.
_UNSET = object()
_agent_session_cls = asq.AgentSession
assert getattr(_agent_session_cls, "get", _UNSET) is _UNSET, (
    "AgentSession.get does not exist -- patching it silently mocks nothing. "
    "Patch AgentSession.query.get instead."
)
assert hasattr(asq.AgentSession.query, "get"), (
    "AgentSession.query.get is expected to exist -- the guard at "
    "agent_session_queue.py calls it directly."
)


def _mock_session(text="test", chat_id="test_chat", session_id="s1"):
    """Create a mock AgentSession, matching test_worker_persistent.py's helper."""
    s = MagicMock()
    s.message_text = text
    s.agent_session_id = session_id
    s.session_id = "session_" + session_id
    s.chat_id = chat_id
    s.project_key = "test"
    s.status = "running"
    s.working_dir = "/tmp/test"
    s.log_lifecycle_transition = MagicMock()
    return s


@pytest.fixture(autouse=True)
def reset_shutdown_flag():
    asq._session_state._shutdown_requested = False
    yield
    asq._session_state._shutdown_requested = False


def _fresh_row(status, session_id=None):
    """A stand-in for the authoritative Redis row the guard reads.

    `session_id` identifies WHICH row the lookup resolved. It matters only for
    TC3, where the point is that the guard's `redis_key` read and the
    completion write's `session_id` re-read can land on different rows.
    """
    fresh = MagicMock()
    fresh.status = status
    fresh.session_id = session_id
    return fresh


async def _run_single_session_loop(
    *,
    chat_id,
    execute_raises=True,
    guard_read_return=None,
    guard_read_side_effect=None,
    complete_side_effect=None,
):
    """Drive `_worker_loop` through exactly one session in standalone mode.

    The session's `_execute_agent_session` raises (unless `execute_raises` is
    False) so `session_failed=True` and `finalized_by_execute=False`, landing
    control in the completion `finally` block under test. The fake execute
    also requests shutdown so the loop exits cleanly after this one session,
    without needing to mock the bridge-mode drain/timeout machinery.
    """
    event = asyncio.Event()
    session = _mock_session("session under test", chat_id, "s1")

    async def fake_execute(_session):
        asq._session_state._shutdown_requested = True
        if execute_raises:
            raise RuntimeError("execution failed")

    complete_mock = AsyncMock(side_effect=complete_side_effect)

    query_get_kwargs = {}
    if guard_read_side_effect is not None:
        query_get_kwargs["side_effect"] = guard_read_side_effect
    else:
        query_get_kwargs["return_value"] = guard_read_return

    with (
        patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
        patch(
            "agent.agent_session_queue._pop_agent_session",
            new=AsyncMock(side_effect=[session, None]),
        ),
        patch(
            "agent.agent_session_queue._execute_agent_session",
            side_effect=fake_execute,
        ),
        patch("agent.agent_session_queue._complete_agent_session", new=complete_mock),
        patch("agent.agent_session_queue._check_restart_flag", return_value=False),
        patch("agent.agent_session_queue.save_session_snapshot"),
        patch.object(asq.AgentSession.query, "get", **query_get_kwargs),
    ):
        await _worker_loop(chat_id, event)

    return complete_mock


class TestTC1AlreadyTerminalSkip:
    """TC1 -- a terminal row means another writer owns the outcome; skip."""

    @pytest.mark.asyncio
    async def test_terminal_row_skips_completion(self, caplog):
        chat_id = "tc1_terminal_skip"
        with caplog.at_level(logging.INFO, logger="agent.agent_session_queue"):
            complete_mock = await _run_single_session_loop(
                chat_id=chat_id,
                guard_read_return=_fresh_row("completed"),
            )

        complete_mock.assert_not_called()
        assert chat_id not in _active_workers
        assert any("already terminal" in r.message for r in caplog.records)


class TestTC2TerminalWriteConflictSurvives:
    """TC2 -- a terminal-write conflict must not kill the worker."""

    @pytest.mark.asyncio
    async def test_conflict_on_write_does_not_crash_loop(self, caplog):
        chat_id = "tc2_write_conflict"
        conflict = StatusConflictError("s1", "failed", "completed", reason="race")
        with caplog.at_level(logging.INFO, logger="agent.agent_session_queue"):
            complete_mock = await _run_single_session_loop(
                chat_id=chat_id,
                guard_read_return=_fresh_row("running"),
                complete_side_effect=conflict,
            )

        # Loop returned normally (no exception escaped _worker_loop).
        assert chat_id not in _active_workers
        # Exactly one call -- proves the old :3028 bare retry is gone.
        assert complete_mock.await_count == 1
        assert any("lost to a concurrent terminal writer" in r.message for r in caplog.records)


class TestTC3ReadWriteRowDivergence:
    """TC3 -- the redis_key read and the session_id write resolve DIFFERENT rows.

    The guard reads the authoritative row by `redis_key`; `_complete_agent_session`
    re-resolves by `session_id` for its CAS. Those two lookups can land on
    different rows, so a guard read that sees a live row does not prove the
    write targets that same row. S1's terminal-skip is keyed on the row the
    guard read, so it structurally cannot close this window -- only S2's typed
    catch does.

    This is what separates TC3 from TC2: there the conflict is on the same row
    the guard just read, so S1 merely lost a race it could in principle have
    won. Here S1 could never have fired at all.
    """

    @pytest.mark.asyncio
    async def test_divergent_row_resolution_survives(self, caplog):
        chat_id = "tc3_row_divergence"
        # The row the guard's redis_key read resolves: live, so S1's skip
        # cannot fire and control reaches the completion write.
        guard_row = _fresh_row("running", session_id="row-resolved-by-redis-key")
        # The row the write's CAS re-read resolves by session_id: a DIFFERENT
        # row, already terminal.
        conflict = StatusConflictError(
            "row-resolved-by-session-id",
            "failed",
            "completed",
            reason="divergent row",
        )
        # Pin the divergence itself. Without this the test silently degrades
        # into a second copy of TC2 if either identity is ever edited.
        assert conflict.session_id != guard_row.session_id, (
            "TC3 must raise the conflict on a DIFFERENT row than the guard read "
            "resolved -- a same-row conflict is TC2's case, not this one."
        )

        with caplog.at_level(logging.INFO, logger="agent.agent_session_queue"):
            complete_mock = await _run_single_session_loop(
                chat_id=chat_id,
                guard_read_return=guard_row,
                complete_side_effect=conflict,
            )

        # Loop returned normally (no exception escaped _worker_loop).
        assert chat_id not in _active_workers
        assert complete_mock.await_count == 1
        # S1 demonstrably did NOT skip -- the write was reached, which is the
        # precondition for this window existing at all.
        assert not any("already terminal" in r.message for r in caplog.records)
        assert any("lost to a concurrent terminal writer" in r.message for r in caplog.records)


class TestTC4WorkerKeepsDraining:
    """TC4 -- the stranding regression itself: the worker survives to drain."""

    @pytest.mark.asyncio
    async def test_second_session_still_executes_after_conflict(self):
        chat_id = "tc4_keeps_draining"
        event = asyncio.Event()
        session1 = _mock_session("session 1", chat_id, "s1")
        session2 = _mock_session("session 2", chat_id, "s2")
        executed = []

        pop_mock = AsyncMock(side_effect=[session1, session2, None])

        async def fake_execute(session):
            executed.append(session.message_text)
            if session.agent_session_id == "s1":
                # session 1 fails; its completion write will conflict below.
                raise RuntimeError("execution failed")
            # session 2 completed cleanly -- request shutdown so the loop
            # exits after draining it instead of waiting indefinitely.
            asq._session_state._shutdown_requested = True

        conflict = StatusConflictError("s1", "failed", "completed", reason="race")
        complete_mock = AsyncMock(side_effect=conflict)

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch("agent.agent_session_queue._pop_agent_session", new=pop_mock),
            patch(
                "agent.agent_session_queue._execute_agent_session",
                side_effect=fake_execute,
            ),
            patch("agent.agent_session_queue._complete_agent_session", new=complete_mock),
            patch("agent.agent_session_queue._check_restart_flag", return_value=False),
            patch("agent.agent_session_queue.save_session_snapshot"),
            patch.object(asq.AgentSession.query, "get", return_value=_fresh_row("running")),
        ):
            await _worker_loop(chat_id, event)

        # This is the acceptance check for the issue's stated consequence:
        # on unfixed code, session 1's conflict kills the loop and session 2
        # never runs.
        assert executed == ["session 1", "session 2"]
        assert chat_id not in _active_workers


class TestTC5GuardReadFailureFallback:
    """TC5 -- pins the pre-existing read-failure fallback (behaviour-preserving)."""

    @pytest.mark.asyncio
    async def test_guard_read_failure_still_completes_once(self, caplog):
        chat_id = "tc5_read_failure_fallback"
        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            complete_mock = await _run_single_session_loop(
                chat_id=chat_id,
                guard_read_side_effect=RuntimeError("redis read failed"),
            )

        assert complete_mock.await_count == 1
        assert any("Nudge guard read failed" in r.message for r in caplog.records)
        assert chat_id not in _active_workers


class TestTC6NonConflictWriteFailureContained:
    """TC6 -- the class is closed, not just StatusConflictError."""

    @pytest.mark.asyncio
    async def test_generic_write_failure_does_not_crash_loop(self, caplog):
        chat_id = "tc6_generic_write_failure"
        with caplog.at_level(logging.ERROR, logger="agent.agent_session_queue"):
            complete_mock = await _run_single_session_loop(
                chat_id=chat_id,
                guard_read_return=_fresh_row("running"),
                complete_side_effect=RuntimeError("redis down"),
            )

        assert chat_id not in _active_workers
        assert complete_mock.await_count == 1
        assert any("Completion write failed" in r.message for r in caplog.records)
