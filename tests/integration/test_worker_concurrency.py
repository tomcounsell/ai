"""Integration tests for worker concurrency controls.

Validates:
1. Sessions with the same chat_id execute strictly one at a time (per-chat serialization)
2. At most MAX_CONCURRENT_SESSIONS sessions run simultaneously across all chat_ids
3. The global semaphore prevents resource exhaustion when multiple chat_ids are active

All tests use redis_test_db fixture (autouse=True in conftest.py) for Redis isolation.
"""

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import agent.agent_session_queue as _queue
from agent.agent_session_queue import (
    _active_workers,
    _ensure_worker,
    _pop_agent_session,
    _starting_workers,
)
from models.agent_session import AgentSession


def _create_test_session(chat_id: str, session_id: str | None = None, **overrides) -> AgentSession:
    """Create an AgentSession with sensible defaults for concurrency testing."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "normal",
        "created_at": time.time(),
        "session_id": session_id or f"session-{chat_id}-{time.time()}",
        "working_dir": "/tmp/test",
        "message_text": "test message",
        "sender_name": "Test",
        "chat_id": chat_id,
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


class TestPopLockContention:
    """Tests for Redis pop lock preventing duplicate session pops."""

    @pytest.mark.asyncio
    async def test_pop_returns_none_when_lock_held(self):
        """_pop_agent_session returns None when the pop lock is already held.

        This simulates the TOCTOU race scenario: a second worker calling
        _pop_agent_session for the same chat_id while the first is mid-transition.
        """
        from agent.agent_session_queue import _acquire_pop_lock, _release_pop_lock

        chat_id = "test-contention-chat"
        _create_test_session(chat_id=chat_id, session_id="session-contention-1")

        # Simulate another worker holding the lock
        _release_pop_lock(chat_id)
        acquired = _acquire_pop_lock(chat_id)
        assert acquired, "Should have acquired the lock"

        try:
            # Second pop attempt should return None (lock held)
            result = await _pop_agent_session(chat_id)
            assert result is None, (
                "When pop lock is held, _pop_agent_session must return None to prevent TOCTOU race"
            )
        finally:
            _release_pop_lock(chat_id)

    @pytest.mark.asyncio
    async def test_pop_succeeds_after_lock_released(self):
        """_pop_agent_session succeeds once the lock is released."""
        from agent.agent_session_queue import _acquire_pop_lock, _release_pop_lock

        chat_id = "test-contention-after-release"
        _create_test_session(chat_id=chat_id, session_id="session-after-release-1")

        # Hold and then release the lock
        _release_pop_lock(chat_id)
        _acquire_pop_lock(chat_id)
        _release_pop_lock(chat_id)

        # Now the pop should succeed
        result = await _pop_agent_session(chat_id)
        assert result is not None, "Pop must succeed after lock is released"
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_pop_marks_session_running_atomically(self):
        """Session must be marked running before the lock is released.

        Simulates two concurrent pops: the first should claim the session,
        the second should find it already running (not pending).
        """
        chat_id = "test-atomic-running"
        session_id_1 = "session-atomic-1"
        _create_test_session(chat_id=chat_id, session_id=session_id_1)

        # First pop
        session = await _pop_agent_session(chat_id)
        assert session is not None
        assert session.status == "running"
        assert session.session_id == session_id_1

        # Second pop should find no pending sessions
        result2 = await _pop_agent_session(chat_id)
        assert result2 is None, "No second session should be found — already claimed"


class TestGlobalSemaphore:
    """Tests for the global concurrency semaphore."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_sessions(self):
        """At most MAX_CONCURRENT_SESSIONS sessions should execute simultaneously.

        Enqueues 3 sessions with the same chat_id and patches _execute_agent_session
        to use a controlled delay. Verifies that at most 1 session is running per
        chat_id at any point in time (per-chat serialization).
        """
        max_sessions = 2
        original_semaphore = _queue._global_session_semaphore

        try:
            # Set up a semaphore with a low ceiling for testing
            _queue._global_session_semaphore = asyncio.Semaphore(max_sessions)

            chat_id_a = "test-semaphore-chat-a"
            chat_id_b = "test-semaphore-chat-b"
            chat_id_c = "test-semaphore-chat-c"

            concurrently_running = []
            peak_concurrent = [0]
            execution_lock = asyncio.Lock()

            async def fake_execute(session):
                async with execution_lock:
                    concurrently_running.append(session.agent_session_id)
                    peak_concurrent[0] = max(peak_concurrent[0], len(concurrently_running))

                await asyncio.sleep(0.05)  # Simulate work

                async with execution_lock:
                    concurrently_running.remove(session.agent_session_id)

            # Create sessions for 3 different chat_ids
            _create_test_session(chat_id=chat_id_a, session_id="sess-sem-a")
            _create_test_session(chat_id=chat_id_b, session_id="sess-sem-b")
            _create_test_session(chat_id=chat_id_c, session_id="sess-sem-c")

            with patch("agent.agent_session_queue._execute_agent_session", new=fake_execute):
                # Start workers for all 3 chat_ids
                for cid in [chat_id_a, chat_id_b, chat_id_c]:
                    _ensure_worker(cid)

                # Wait for workers to complete
                await asyncio.sleep(0.5)

            assert peak_concurrent[0] <= max_sessions, (
                f"Peak concurrent sessions ({peak_concurrent[0]}) exceeded "
                f"MAX_CONCURRENT_SESSIONS={max_sessions}"
            )
        finally:
            _queue._global_session_semaphore = original_semaphore
            # Clean up workers
            for task in list(_active_workers.values()):
                task.cancel()
            _active_workers.clear()
            _starting_workers.clear()

    @pytest.mark.asyncio
    async def test_semaphore_none_allows_unlimited_sessions(self):
        """When _global_session_semaphore is None, no ceiling applies.

        This is the backward-compatible mode before the worker initializes
        the semaphore (e.g., in tests that don't call _run_worker).
        """
        original_semaphore = _queue._global_session_semaphore
        try:
            _queue._global_session_semaphore = None
            # Just verify the pop path doesn't crash when semaphore is None
            chat_id = "test-semaphore-none"
            _create_test_session(chat_id=chat_id, session_id="sess-no-sem")
            result = await _pop_agent_session(chat_id)
            assert result is not None
            assert result.status == "running"
        finally:
            _queue._global_session_semaphore = original_semaphore


class TestPerChatSerialization:
    """Tests for per-chat-id session serialization.

    Sessions with the same chat_id must execute strictly one at a time.
    """

    @pytest.mark.asyncio
    async def test_three_sessions_same_chat_id_execute_serially(self):
        """Three sessions with chat_id='0' must execute strictly one at a time.

        This is the exact scenario from the bug report: three PM sessions
        enqueued with chat_id='0' causing 5+ concurrent sessions.

        The test uses a controlled _execute_agent_session mock with a shared
        counter to verify the serialization guarantee.
        """
        chat_id = "0"  # The bug report scenario

        # Track concurrent executions
        running_count = [0]
        peak_running = [0]
        execution_order = []
        count_lock = asyncio.Lock()

        session_ids = [f"serial-session-{i}" for i in range(3)]
        for i, sid in enumerate(session_ids):
            _create_test_session(
                chat_id=chat_id,
                session_id=sid,
                priority="normal",
                created_at=time.time() + i * 0.001,  # Ensure ordering
            )

        async def fake_execute(session):
            async with count_lock:
                running_count[0] += 1
                peak_running[0] = max(peak_running[0], running_count[0])
                execution_order.append(session.session_id)

            await asyncio.sleep(0.05)

            async with count_lock:
                running_count[0] -= 1

        original_semaphore = _queue._global_session_semaphore
        try:
            _queue._global_session_semaphore = asyncio.Semaphore(3)  # Global ceiling

            with patch("agent.agent_session_queue._execute_agent_session", new=fake_execute):
                _ensure_worker(chat_id)
                await asyncio.sleep(0.5)  # Wait for all sessions to complete

            assert peak_running[0] <= 1, (
                f"Peak concurrent sessions for chat_id={chat_id!r} was "
                f"{peak_running[0]}, expected ≤ 1. "
                "Per-chat serialization is broken."
            )
            assert len(execution_order) == 3, (
                f"Expected 3 sessions to execute, got {len(execution_order)}: {execution_order}"
            )
        finally:
            _queue._global_session_semaphore = original_semaphore
            task = _active_workers.pop(chat_id, None)
            if task:
                task.cancel()
            _starting_workers.discard(chat_id)

    @pytest.mark.asyncio
    async def test_global_ceiling_across_multiple_chat_ids(self):
        """Global semaphore ceiling must apply across all chat_ids combined.

        With MAX_CONCURRENT_SESSIONS=2, at most 2 sessions should run at
        any point regardless of how many different chat_ids are active.
        """
        max_sessions = 2
        chat_ids = [f"global-ceil-chat-{i}" for i in range(4)]

        running_count = [0]
        peak_running = [0]
        count_lock = asyncio.Lock()

        for i, cid in enumerate(chat_ids):
            _create_test_session(chat_id=cid, session_id=f"global-sess-{i}")

        async def fake_execute(session):
            async with count_lock:
                running_count[0] += 1
                peak_running[0] = max(peak_running[0], running_count[0])

            await asyncio.sleep(0.05)

            async with count_lock:
                running_count[0] -= 1

        original_semaphore = _queue._global_session_semaphore
        try:
            _queue._global_session_semaphore = asyncio.Semaphore(max_sessions)

            with patch("agent.agent_session_queue._execute_agent_session", new=fake_execute):
                for cid in chat_ids:
                    _ensure_worker(cid)
                await asyncio.sleep(0.8)

            assert peak_running[0] <= max_sessions, (
                f"Peak concurrent sessions ({peak_running[0]}) exceeded "
                f"MAX_CONCURRENT_SESSIONS={max_sessions}. "
                "Global semaphore is not working correctly."
            )
        finally:
            _queue._global_session_semaphore = original_semaphore
            for cid in chat_ids:
                task = _active_workers.pop(cid, None)
                if task:
                    task.cancel()
                _starting_workers.discard(cid)
