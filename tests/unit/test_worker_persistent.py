"""Tests for worker persistent mode and graceful shutdown.

Covers:
- Persistent mode: worker waits indefinitely in standalone mode
- Bridge mode: existing drain-timeout-exit behavior unchanged
- Graceful shutdown: request_shutdown() wakes workers and they exit after current session
- Shutdown flag checked before starting new work
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from popoto.exceptions import ModelException

import agent.agent_session_queue as asq
from agent.agent_session_queue import (
    CORRUPTED_POP_ESCALATE_N,
    _active_events,
    _active_workers,
    _worker_loop,
    request_shutdown,
)


@pytest.fixture(autouse=True)
def reset_shutdown_flag():
    """Reset the shutdown flag before and after each test.

    The runtime reads/writes the canonical binding at
    ``agent.session_state._shutdown_requested``; ``asq._shutdown_requested``
    is a stale import-time copy that the worker loop never consults.
    """
    asq._session_state._shutdown_requested = False
    yield
    asq._session_state._shutdown_requested = False


def _mock_session(text="test", chat_id="test_chat", session_id="s1"):
    """Create a mock AgentSession."""
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


class TestPersistentMode:
    """Worker stays alive in standalone mode when queue is empty."""

    @pytest.mark.asyncio
    async def test_standalone_waits_indefinitely(self):
        """In standalone mode, worker waits for event instead of timing out."""
        chat_id = "persistent_test"
        event = asyncio.Event()

        async def wake_and_shutdown():
            await asyncio.sleep(0.1)
            asq._session_state._shutdown_requested = True
            event.set()  # Wake the worker

        # Mock pop to always return None (empty queue)
        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            wake_task = asyncio.create_task(wake_and_shutdown())
            await _worker_loop(chat_id, event)
            await wake_task

        # Worker exited due to shutdown, not timeout
        assert chat_id not in _active_workers

    @pytest.mark.asyncio
    async def test_status_conflict_during_pop_does_not_crash_loop(self):
        """A StatusConflictError from _pop_agent_session (a session killed in the
        race between pop reading status=pending and transition→running) must be
        caught and skipped — the loop must survive and keep popping, not
        propagate and die, stranding all other pending sessions (issue #1803)."""
        from models.session_lifecycle import StatusConflictError

        chat_id = "conflict_test"
        event = asyncio.Event()
        calls = {"n": 0}

        def pop_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise StatusConflictError(
                    "session_x", "pending", "killed", reason="worker picked up session"
                )
            return None  # empty queue thereafter

        async def wake_and_shutdown():
            await asyncio.sleep(0.15)
            asq._session_state._shutdown_requested = True
            event.set()

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new=AsyncMock(side_effect=pop_side_effect),
            ),
        ):
            wake_task = asyncio.create_task(wake_and_shutdown())
            # Must NOT raise StatusConflictError out of the loop.
            await _worker_loop(chat_id, event)
            await wake_task

        assert calls["n"] >= 2, "loop should have continued popping after the conflict"
        assert chat_id not in _active_workers

    @pytest.mark.asyncio
    async def test_model_exception_during_pop_does_not_crash_loop(self):
        """A Popoto ModelException from _pop_agent_session (a corrupted record —
        all fields None except status=pending — that fails the pending→running
        save) must be caught and skipped: the loop survives, routes the record
        to cleanup_corrupted_agent_sessions best-effort, and keeps popping
        (issue #2088, sibling of the #1803 StatusConflictError handler)."""
        chat_id = "corrupted_test"
        event = asyncio.Event()
        calls = {"n": 0}

        def pop_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                # The confirmed escaping exception (Sentry VALOR-E5). It carries
                # NO session_id — the handler must never dereference one.
                raise ModelException("Model instance parameters invalid. Failed to save.")
            return None  # empty queue thereafter

        async def wake_and_shutdown():
            await asyncio.sleep(0.15)
            asq._session_state._shutdown_requested = True
            event.set()

        reaper = MagicMock(return_value={"corrupted": 1, "orphans": 0})

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new=AsyncMock(side_effect=pop_side_effect),
            ),
            patch(
                "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
                new=reaper,
            ),
            patch("agent.agent_session_queue.CORRUPTED_POP_BACKOFF_SECONDS", 0.001),
        ):
            wake_task = asyncio.create_task(wake_and_shutdown())
            # Must NOT raise ModelException out of the loop.
            await _worker_loop(chat_id, event)
            await wake_task

        assert calls["n"] >= 2, "loop should have continued popping after the corrupted pop"
        assert chat_id not in _active_workers
        assert reaper.call_count >= 1, "corrupted pop should route to the ORM reaper"

    @pytest.mark.asyncio
    async def test_reaper_failure_during_corrupted_pop_does_not_crash_loop(self):
        """If the best-effort reaper itself raises, the failure must be swallowed
        inside the ModelException clause (it is NOT caught by the sibling
        `except BaseException: raise`) — the loop still survives and continues."""
        chat_id = "corrupted_reaper_fail_test"
        event = asyncio.Event()
        calls = {"n": 0}

        def pop_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ModelException("Model instance parameters invalid. Failed to save.")
            return None

        async def wake_and_shutdown():
            await asyncio.sleep(0.15)
            asq._session_state._shutdown_requested = True
            event.set()

        reaper = MagicMock(side_effect=Exception("reaper blew up"))

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new=AsyncMock(side_effect=pop_side_effect),
            ),
            patch(
                "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
                new=reaper,
            ),
            patch("agent.agent_session_queue.CORRUPTED_POP_BACKOFF_SECONDS", 0.001),
        ):
            wake_task = asyncio.create_task(wake_and_shutdown())
            # A reaper failure must NOT re-crash the loop.
            await _worker_loop(chat_id, event)
            await wake_task

        assert calls["n"] >= 2, "loop should survive a reaper failure and keep popping"
        assert chat_id not in _active_workers

    @pytest.mark.asyncio
    async def test_repeated_corrupted_pop_escalates_without_crash(self):
        """With a corrupted record stuck at the queue head (ModelException on
        every pop) and the reaper a no-op, the loop must (a) survive, (b) apply
        the CORRUPTED_POP_BACKOFF_SECONDS backoff between corrupted pops, and
        (c) emit the one-shot escalation logger.error EXACTLY ONCE after
        CORRUPTED_POP_ESCALATE_N consecutive corrupted pops (idempotent via
        _corrupted_pop_escalated). The escalation reads no reaper return value."""
        chat_id = "corrupted_escalate_test"
        event = asyncio.Event()

        # ModelException on EVERY pop — a record stuck at the head.
        pop = AsyncMock(
            side_effect=ModelException("Model instance parameters invalid. Failed to save.")
        )
        reaper = MagicMock(return_value={"corrupted": 0, "orphans": 0})

        # Spy on the backoff sleep and use it to terminate the otherwise-infinite
        # spin: the ModelException path's ONLY asyncio.sleep is the backoff.
        sleep_calls = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay, *a, **k):
            sleep_calls.append(delay)
            if len(sleep_calls) >= CORRUPTED_POP_ESCALATE_N + 2:
                asq._session_state._shutdown_requested = True
                event.set()
            await real_sleep(0)  # yield without real delay

        mock_logger = MagicMock()

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch("agent.agent_session_queue._pop_agent_session", new=pop),
            patch(
                "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
                new=reaper,
            ),
            patch("agent.agent_session_queue.CORRUPTED_POP_BACKOFF_SECONDS", 0.001),
            patch("agent.agent_session_queue.asyncio.sleep", side_effect=fake_sleep),
            patch("agent.agent_session_queue.logger", mock_logger),
        ):
            # Must survive the repeated corruption and terminate on shutdown.
            await _worker_loop(chat_id, event)

        assert chat_id not in _active_workers
        assert len(sleep_calls) >= CORRUPTED_POP_ESCALATE_N, "backoff applied between pops"
        assert all(d == 0.001 for d in sleep_calls), "backoff used the patched constant"
        escalations = [c for c in mock_logger.error.call_args_list if "consecutively" in c.args[0]]
        assert len(escalations) == 1, "escalation logger.error must fire exactly once"

    @pytest.mark.asyncio
    async def test_corrupted_pop_guard_resets_on_successful_pop(self):
        """A successful pop must clear the worker_key-keyed spin guard so a later,
        unrelated corrupted pop does not inherit a stale count and escalate
        prematurely. Sequence: ModelException, healthy pop (resets guard),
        ModelException, empty → shutdown. With CORRUPTED_POP_ESCALATE_N patched
        to 2, escalation would fire on the 2nd corrupted pop only if the reset
        failed; asserting zero escalations proves the reset works."""
        chat_id = "corrupted_reset_test"
        event = asyncio.Event()
        healthy = _mock_session("healthy", chat_id, "s1")
        pop_results = [
            ModelException("Model instance parameters invalid. Failed to save."),
            healthy,
            ModelException("Model instance parameters invalid. Failed to save."),
        ]
        idx = {"i": 0}

        async def mock_pop(cid, is_project_keyed=False):
            if idx["i"] < len(pop_results):
                result = pop_results[idx["i"]]
                idx["i"] += 1
                if isinstance(result, Exception):
                    raise result
                return result
            return None

        async def fake_execute(session):
            # After the healthy session executes, let the loop pop the 2nd
            # corrupted record, then shut down from the backoff spy below.
            pass

        reaper = MagicMock(return_value={"corrupted": 0, "orphans": 0})
        mock_fresh = MagicMock()
        mock_fresh.status = "running"

        # Terminate via the backoff spy after the 2nd corrupted pop's backoff
        # (sleep #1 follows pop1's ModelException; sleep #2 follows pop3's).
        backoffs = {"n": 0}
        real_sleep = asyncio.sleep

        async def fake_sleep(delay, *a, **k):
            backoffs["n"] += 1
            if backoffs["n"] >= 2:
                asq._session_state._shutdown_requested = True
                event.set()
            await real_sleep(0)

        mock_logger = MagicMock()

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch("agent.agent_session_queue._pop_agent_session", side_effect=mock_pop),
            patch(
                "agent.agent_session_queue._execute_agent_session",
                side_effect=fake_execute,
            ),
            patch(
                "agent.agent_session_queue._complete_agent_session",
                new_callable=AsyncMock,
            ),
            patch("agent.agent_session_queue._check_restart_flag", return_value=False),
            patch("agent.agent_session_queue.save_session_snapshot"),
            patch(
                "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
                new=reaper,
            ),
            patch("agent.agent_session_queue.CORRUPTED_POP_ESCALATE_N", 2),
            patch("agent.agent_session_queue.CORRUPTED_POP_BACKOFF_SECONDS", 0.001),
            patch("agent.agent_session_queue.asyncio.sleep", side_effect=fake_sleep),
            patch("agent.agent_session_queue.logger", mock_logger),
            patch.object(asq.AgentSession, "get", return_value=mock_fresh, create=True),
        ):
            await _worker_loop(chat_id, event)

        assert chat_id not in _active_workers
        escalations = [c for c in mock_logger.error.call_args_list if "consecutively" in c.args[0]]
        assert escalations == [], "guard reset on the healthy pop should prevent escalation"

    @pytest.mark.asyncio
    async def test_bridge_mode_exits_on_empty_queue(self):
        """In bridge mode (no env var), worker exits on empty queue after timeout."""
        chat_id = "bridge_drain_test"
        event = asyncio.Event()

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("agent.agent_session_queue.DRAIN_TIMEOUT", 0.05),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "agent.agent_session_queue._pop_agent_session_with_fallback",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            # Remove VALOR_WORKER_MODE if set
            os.environ.pop("VALOR_WORKER_MODE", None)
            await _worker_loop(chat_id, event)

        assert chat_id not in _active_workers

    @pytest.mark.asyncio
    async def test_standalone_processes_nudge_without_exit(self):
        """Standalone worker processes a nudge re-enqueue without exiting between."""
        chat_id = "nudge_test"
        event = asyncio.Event()
        sessions_executed = []

        session1 = _mock_session("session 1", chat_id, "s1")
        session2 = _mock_session("session 2 (nudge)", chat_id, "s2")

        pop_results = [session1, None, session2, None]
        pop_index = 0

        async def mock_pop(cid, is_project_keyed=False):
            nonlocal pop_index
            if pop_index < len(pop_results):
                result = pop_results[pop_index]
                pop_index += 1
                return result
            return None

        async def _delayed_set(delay, ev):
            await asyncio.sleep(delay)
            ev.set()

        async def fake_execute(session):
            sessions_executed.append(session.message_text)
            if len(sessions_executed) == 1:
                # Simulate nudge re-enqueue arriving AFTER finally block clears event
                asyncio.create_task(_delayed_set(0.05, event))
            elif len(sessions_executed) == 2:
                # After second session, trigger shutdown
                request_shutdown()

        # Mock AgentSession.get to return a running session (so nudge guard completes normally)
        mock_fresh = MagicMock()
        mock_fresh.status = "running"

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                side_effect=mock_pop,
            ),
            patch(
                "agent.agent_session_queue._execute_agent_session",
                side_effect=fake_execute,
            ),
            patch(
                "agent.agent_session_queue._complete_agent_session",
                new_callable=AsyncMock,
            ),
            patch(
                "agent.agent_session_queue._check_restart_flag",
                return_value=False,
            ),
            patch("agent.agent_session_queue.save_session_snapshot"),
            patch.object(
                asq.AgentSession,
                "get",
                return_value=mock_fresh,
                create=True,
            ),
        ):
            # Register event so request_shutdown can wake us
            _active_events[chat_id] = event
            try:
                await _worker_loop(chat_id, event)
            finally:
                _active_events.pop(chat_id, None)

        assert len(sessions_executed) == 2
        assert "session 1" in sessions_executed
        assert "session 2 (nudge)" in sessions_executed


class TestGracefulShutdown:
    """request_shutdown() coordinates clean worker exit."""

    def test_request_shutdown_sets_flag(self):
        """request_shutdown() sets the module-level flag."""
        assert asq._session_state._shutdown_requested is False
        request_shutdown()
        assert asq._session_state._shutdown_requested is True

    def test_request_shutdown_wakes_events(self):
        """request_shutdown() sets all active events to wake waiting workers."""
        event1 = asyncio.Event()
        event2 = asyncio.Event()
        _active_events["chat1"] = event1
        _active_events["chat2"] = event2

        try:
            assert not event1.is_set()
            assert not event2.is_set()

            request_shutdown()

            assert event1.is_set()
            assert event2.is_set()
        finally:
            _active_events.pop("chat1", None)
            _active_events.pop("chat2", None)

    @pytest.mark.asyncio
    async def test_shutdown_exits_after_current_session(self):
        """Worker finishes current session then exits on shutdown."""
        chat_id = "shutdown_mid_session"
        event = asyncio.Event()
        sessions_executed = []

        session1 = _mock_session("current session", chat_id, "s1")
        session2 = _mock_session("next session", chat_id, "s2")

        # Pop returns session1 first, then session2
        pop_results = iter([session1, session2])

        async def mock_pop(cid, is_project_keyed=False):
            return next(pop_results, None)

        async def fake_execute(session):
            sessions_executed.append(session.message_text)
            # Request shutdown during first session execution
            asq._session_state._shutdown_requested = True

        mock_fresh = MagicMock()
        mock_fresh.status = "running"

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                side_effect=mock_pop,
            ),
            patch(
                "agent.agent_session_queue._execute_agent_session",
                side_effect=fake_execute,
            ),
            patch(
                "agent.agent_session_queue._complete_agent_session",
                new_callable=AsyncMock,
            ),
            patch(
                "agent.agent_session_queue._check_restart_flag",
                return_value=False,
            ),
            patch("agent.agent_session_queue.save_session_snapshot"),
            patch.object(
                asq.AgentSession,
                "get",
                return_value=mock_fresh,
                create=True,
            ),
        ):
            await _worker_loop(chat_id, event)

        # First session was executed, second was NOT (shutdown between sessions)
        assert len(sessions_executed) == 1
        assert sessions_executed[0] == "current session"

    @pytest.mark.asyncio
    async def test_shutdown_with_no_active_session_exits_immediately(self):
        """If shutdown is requested while waiting, worker exits without processing."""
        chat_id = "shutdown_idle"
        event = asyncio.Event()

        async def trigger_shutdown():
            await asyncio.sleep(0.05)
            asq._session_state._shutdown_requested = True
            event.set()

        with (
            patch.dict(os.environ, {"VALOR_WORKER_MODE": "standalone"}),
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            task = asyncio.create_task(trigger_shutdown())
            await _worker_loop(chat_id, event)
            await task

        assert chat_id not in _active_workers


class TestBackwardCompatibility:
    """Existing bridge behavior is unchanged."""

    def test_drain_timeout_constant_unchanged(self):
        """DRAIN_TIMEOUT should still be 1.5 seconds."""
        from agent.agent_session_queue import DRAIN_TIMEOUT

        assert DRAIN_TIMEOUT == 1.5

    def test_request_shutdown_importable(self):
        """request_shutdown should be importable from agent_session_queue."""
        from agent.agent_session_queue import request_shutdown

        assert callable(request_shutdown)
