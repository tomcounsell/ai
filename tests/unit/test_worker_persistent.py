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

import agent.agent_session_queue as asq
from agent.agent_session_queue import (
    _active_events,
    _active_workers,
    _worker_loop,
    request_shutdown,
)


@pytest.fixture(autouse=True)
def reset_shutdown_flag():
    """Reset the shutdown flag before and after each test."""
    asq._shutdown_requested = False
    yield
    asq._shutdown_requested = False


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
            asq._shutdown_requested = True
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

        async def mock_pop(cid):
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
            patch(
                "agent.hooks.session_registry.get_activity",
                return_value={"tool_count": 0},
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
        assert asq._shutdown_requested is False
        request_shutdown()
        assert asq._shutdown_requested is True

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

        async def mock_pop(cid):
            return next(pop_results, None)

        async def fake_execute(session):
            sessions_executed.append(session.message_text)
            # Request shutdown during first session execution
            asq._shutdown_requested = True

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
            patch(
                "agent.hooks.session_registry.get_activity",
                return_value={"tool_count": 0},
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
            asq._shutdown_requested = True
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
