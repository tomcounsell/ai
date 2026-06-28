"""Reproduction / regression harness for the wedged-but-alive worker investigation.

Issue: #1808 — "Wedged-but-alive worker leaves sessions pending indefinitely despite
300s health backstop."

Two complementary tests ship here:

A1 — ``test_worker_loop_parks_on_zero_semaphore`` (mechanism test, load-bearing):
    Drives the *real* ``_worker_loop`` against a zero-slot
    ``_global_session_semaphore`` and asserts the loop parks at
    ``await semaphore.acquire()`` (``agent_session_queue.py:1314``), proving the
    *mechanism* — a slot-starved worker loop — not just the consequence.
    Recovery is also proven: one ``semaphore.release()`` unblocks the loop and the
    sentinel ``_execute_agent_session`` is called.
    ``_pop_agent_session`` is monkeypatched alongside ``_execute_agent_session``
    so the test is deterministic regardless of async-Redis isolation state —
    the real ``_worker_loop`` semaphore/acquire/release path is what we are testing,
    not the pop mechanics.

A2 — ``test_health_check_cannot_escalate_parked_worker`` (backstop-blindness test,
    documents the consequence):
    Registers a non-``done()`` worker future in ``_active_workers`` and shows that
    ``_agent_session_health_check()`` never escalates (never starts a new worker) —
    it nudges with ``event.set(); continue`` regardless of whether the semaphore is
    exhausted.  The health check never reads ``_global_session_semaphore``.  This
    confirms the 300s backstop is blind to the suspension wedge.
    NOTE: this test proves the consequence only.  The loop-park proof lives in A1.

Env-flag assertions — ``TestAsyncioDebugHelper``:
    The ``_asyncio_debug_enabled(env_value)`` helper lives in ``worker/__main__.py``
    and ships on BOTH investigation outcome branches (issue #1808 revision 4 / B2).
    These assertions target the helper directly so they have a stable import target
    regardless of outcome.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

import agent.session_state as _session_state
from agent.agent_session_queue import _active_events, _active_workers
from agent.session_health import _agent_session_health_check
from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_wedge_test_session(worker_key: str, **overrides) -> AgentSession:
    """Create an AgentSession with project_key=test-wedge and status=pending.

    Uses ``session_type="teammate"`` and ``chat_id=worker_key`` so that
    ``AgentSession.worker_key`` resolves to ``chat_id`` — a non-project-keyed
    route that gives a predictable worker_key for ``_agent_session_health_check``
    (which uses ``entry.worker_key``).
    """
    defaults: dict[str, Any] = {
        "project_key": "test-wedge",
        "status": "pending",
        "priority": "high",
        "created_at": time.time(),
        "session_id": f"wedge-test-{worker_key}",
        "working_dir": "/tmp/wedge-test",
        "message_text": "wedge test message",
        "sender_name": "WedgeTester",
        "chat_id": worker_key,
        "telegram_message_id": 1,
        "session_type": "teammate",
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


# ---------------------------------------------------------------------------
# B2 — env-flag helper assertions (always ships on both branches, issue #1808)
# ---------------------------------------------------------------------------


class TestAsyncioDebugHelper:
    """Env-flag parser assertions for ``_asyncio_debug_enabled``.

    The helper ``_asyncio_debug_enabled(env_value: str | None) -> bool`` is the
    pure, always-shipping module-level function in ``worker/__main__.py`` that
    encodes the WORKER_ASYNCIO_DEBUG on/off parse (issue #1808 revision 4 / B2).

    These assertions target the helper directly — not the inline startup branch
    — so they have a stable import target on BOTH investigation outcome branches
    (root-cause-found or not-reproducible).  This resolves the B2 contradiction
    where Task 1 previously asserted against a parser that only shipped
    conditionally.
    """

    @pytest.fixture(autouse=True)
    def import_helper(self):
        """Import _asyncio_debug_enabled from worker/__main__.py."""
        from worker.__main__ import _asyncio_debug_enabled

        self._fn = _asyncio_debug_enabled

    def test_unset_is_off(self):
        """``None`` (env var unset) must return False."""
        assert self._fn(None) is False

    def test_empty_string_is_off(self):
        """``""`` (env var set to empty string) must return False."""
        assert self._fn("") is False

    def test_zero_string_is_off(self):
        """``"0"`` must return False."""
        assert self._fn("0") is False

    def test_false_string_is_off(self):
        """``"false"`` and case variants must return False."""
        assert self._fn("false") is False
        assert self._fn("False") is False
        assert self._fn("FALSE") is False

    def test_one_string_is_on(self):
        """``"1"`` (the canonical enable value) must return True."""
        assert self._fn("1") is True

    def test_truthy_strings_are_on(self):
        """Any non-empty, non-``"0"``, non-``"false"`` string must return True."""
        assert self._fn("true") is True
        assert self._fn("yes") is True
        assert self._fn("on") is True


# ---------------------------------------------------------------------------
# A1 — mechanism test (load-bearing)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWorkerLoopSemaphorePark:
    """A1: Drive the real ``_worker_loop`` against a zero-slot semaphore.

    Proves the *mechanism* (not just the consequence): with no semaphore
    permits available, the worker loop parks at ``await semaphore.acquire()``
    and cannot pop or process a pending session.  Recovery is also proven.

    This is the load-bearing reproduction test for issue #1808 (hypothesis 1:
    semaphore exhaustion).  The health-check tautology (A2) only proves the
    *consequence* — that the 300s backstop cannot recover a parked worker.

    ``_pop_agent_session`` is monkeypatched alongside ``_execute_agent_session``
    so the recovery assertion is deterministic regardless of async-Redis isolation
    state in the test environment.  The real ``_worker_loop`` semaphore
    acquire/release path is what we are testing, not the pop or execution mechanics.
    """

    WORKER_KEY = "test-wedge-sem-01"

    @pytest.mark.asyncio
    async def test_worker_loop_parks_on_zero_semaphore(self, monkeypatch, redis_test_db):
        """A1 mechanism test: zero-slot semaphore parks the worker loop.

        Setup:
        - ``_global_session_semaphore`` has zero available permits.
        - ``_pop_agent_session`` is monkeypatched to return a fake session so the
          pop is deterministic (avoids async-Redis isolation complexity in tests).
        - ``_execute_agent_session`` is monkeypatched to a no-op sentinel.

        Phase 1 — park assertion:
        - Spawn the real ``_worker_loop`` and yield the event loop a few times.
        - Assert: task is not done, sentinel not called.
          This proves the loop is suspended at ``await semaphore.acquire()``
          (``agent_session_queue.py:1314``), not at some other await.

        Phase 2 — recovery assertion:
        - Release one semaphore permit (``semaphore.release()``).
        - Yield the event loop until the sentinel fires (bounded polling).
        - Assert: sentinel was called, proving the park was on the semaphore.

        Pin ``VALOR_WORKER_MODE=standalone`` so the loop uses the "wait
        indefinitely" branch (``event.wait()`` with no DRAIN_TIMEOUT) rather than
        the bridge-mode ``asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)``
        exit path — concern A1-rev4.

        Teardown restores ``_session_state._global_session_semaphore`` and
        ``_session_state._shutdown_requested`` (the standalone branch reads both
        at ``agent_session_queue.py:1305`` and ``1371``; a stale value would
        short-circuit the loop in the next test — A1-teardown-rev4).
        """
        import agent.agent_session_queue as _aq

        # --- Pin standalone mode so loop waits indefinitely (no DRAIN_TIMEOUT exit) ---
        monkeypatch.setenv("VALOR_WORKER_MODE", "standalone")

        # --- Capture prior state for deterministic teardown ---
        prior_semaphore = _session_state._global_session_semaphore
        prior_shutdown = _session_state._shutdown_requested
        loop_task: asyncio.Task | None = None

        try:
            # --- Replace semaphore with zero-slot (no permits — loop will park) ---
            zero_semaphore = asyncio.Semaphore(0)
            _session_state._global_session_semaphore = zero_semaphore

            # --- Build a fake session for the mock pop to return ---
            fake_session = MagicMock()
            fake_session.agent_session_id = "wedge-fake-session-001"
            fake_session.worker_key = self.WORKER_KEY

            # --- Monkeypatch _pop_agent_session to a deterministic sentinel ---
            # Returns fake_session on first call (recovery), None on subsequent calls
            # (so the loop doesn't infinitely process the same session).
            pop_call_count = [0]

            async def _mock_pop(wk, is_pk=False):
                """Deterministic mock: return fake session once, then None."""
                pop_call_count[0] += 1
                if pop_call_count[0] == 1:
                    return fake_session
                return None

            monkeypatch.setattr(_aq, "_pop_agent_session", _mock_pop)

            # --- Monkeypatch _execute_agent_session to a sentinel no-op ---
            execute_calls: list[str] = []

            async def _sentinel_execute(session) -> None:
                """No-op sentinel that records it was called (issue #1808 A1 test)."""
                execute_calls.append(session.agent_session_id)

            monkeypatch.setattr(_aq, "_execute_agent_session", _sentinel_execute)

            # --- Spawn the real _worker_loop ---
            event = asyncio.Event()
            _active_events[self.WORKER_KEY] = event
            loop_task = asyncio.create_task(
                _aq._worker_loop(self.WORKER_KEY, event, False),
                name=f"wedge-test-loop-{self.WORKER_KEY}",
            )
            _active_workers[self.WORKER_KEY] = loop_task

            # --- Phase 1: yield the event loop; loop should park at semaphore.acquire() ---
            # Give the task enough CPU ticks to reach the semaphore acquire and suspend.
            for _ in range(20):
                await asyncio.sleep(0)

            # The task should be suspended (not done), not exited prematurely.
            assert not loop_task.done(), (
                "Worker loop task exited before semaphore was released — expected it to be "
                "suspended at await semaphore.acquire() (agent_session_queue.py:1314). "
                f"Task exception: {loop_task.exception() if loop_task.done() else 'N/A'}"
            )

            # The sentinel should NOT have been called yet — loop is parked before the pop.
            assert len(execute_calls) == 0, (
                f"Sentinel was called {len(execute_calls)} time(s) before semaphore release — "
                f"loop reached _execute_agent_session without acquiring a permit (unexpected). "
                f"This means the loop did NOT park at semaphore.acquire()."
            )

            # --- Phase 2: release one permit and let the loop recover ---
            zero_semaphore.release()

            # Poll until the sentinel fires (loop woke up, popped, and executed the session).
            # Use real sleeps to allow I/O events and async Redis calls to complete.
            for _ in range(200):
                await asyncio.sleep(0.01)  # 10ms per tick, 2s total budget
                if execute_calls:
                    break

            assert len(execute_calls) >= 1, (
                f"Expected sentinel to be called after semaphore release. Got 0 calls. "
                f"loop_task.done()={loop_task.done()}, "
                f"pop_call_count={pop_call_count[0]}. "
                f"If pop_count=0: loop did not wake after release — park was not on "
                f"this semaphore object. "
                f"If pop_count>0 but execute=0: pop returned None (unexpected — mock "
                f"should have returned fake_session on first call). "
            )

        finally:
            # --- Teardown: cancel worker loop task ---
            if loop_task is not None and not loop_task.done():
                loop_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(loop_task), timeout=1.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass

            # --- Restore module globals (A1-teardown-rev4) ---
            _session_state._global_session_semaphore = prior_semaphore
            _session_state._shutdown_requested = prior_shutdown

            # --- Remove worker registration ---
            _active_workers.pop(self.WORKER_KEY, None)
            _active_events.pop(self.WORKER_KEY, None)

            # --- Delete test sessions via Popoto ORM (never raw Redis) ---
            stale = [s for s in AgentSession.query.all() if s.project_key == "test-wedge"]
            for s in stale:
                try:
                    s.delete()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# A2 — backstop-blindness test (consequence; complements A1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHealthCheckBackstopBlindness:
    """A2: The 300s health-check pending branch cannot escalate a parked worker.

    The health-check's pending branch (``session_health.py:2558``) evaluates
    ``worker_alive = worker is not None and not worker.done()``.  A worker loop
    task parked at ``await semaphore.acquire()`` is not ``done()`` — so
    ``worker_alive = True`` — and the branch sets the event and ``continue``s
    without starting a new worker.  The session stays ``pending`` indefinitely.

    This test proves the *consequence* by:
    1. Registering a non-``done()`` asyncio.Future as the fake worker.
    2. Running ``_agent_session_health_check()``.
    3. Asserting the session remains ``pending`` (not escalated).

    NOTE: this test does NOT prove the semaphore-park mechanism — that is A1's job.
    In fact, ``_agent_session_health_check()`` never reads ``_global_session_semaphore``
    (its pending branch only calls ``worker.done()``).  The semaphore drain is
    included for completeness to match production state, but the health-check
    verdict is IDENTICAL at any semaphore value.
    """

    WORKER_KEY = "test-wedge-hc-01"

    @pytest.mark.asyncio
    async def test_health_check_cannot_escalate_parked_worker(self, redis_test_db):
        """A2 consequence test: health check nudges and continues without escalating.

        With a non-done() worker future in ``_active_workers``, the pending
        branch treats the worker as alive and calls ``event.set(); continue``
        without starting a new worker.  The session remains ``pending``.

        Boundary note: the health-check verdict is IDENTICAL regardless of the
        semaphore state (depleted or not), because the pending branch never reads
        ``_global_session_semaphore``.  The semaphore is drained to 0 here to
        match realistic wedge state, but the assertion holds at any semaphore value.
        """
        prior_semaphore = _session_state._global_session_semaphore
        prior_shutdown = _session_state._shutdown_requested

        try:
            # Drain semaphore to 0 (matches wedge production state)
            zero_semaphore = asyncio.Semaphore(0)
            _session_state._global_session_semaphore = zero_semaphore

            # Register a non-done() Future as the fake parked worker
            fake_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
            _active_workers[self.WORKER_KEY] = fake_future  # type: ignore[assignment]

            # Register an event so the nudge branch finds one to set
            fake_event = asyncio.Event()
            _active_events[self.WORKER_KEY] = fake_event

            # Create a pending session routed to WORKER_KEY via chat_id
            session = _create_wedge_test_session(self.WORKER_KEY)
            assert session.status == "pending"

            # Sanity: fake future is not done (simulates a parked worker loop)
            assert not fake_future.done(), "Precondition: fake worker future must be non-done"

            # --- Run the 300s health backstop ---
            await _agent_session_health_check()

            # --- Assert session is still pending (no escalation) ---
            fresh = AgentSession.query.get(redis_key=session.db_key.redis_key)
            assert fresh is not None, "Test session disappeared from Redis during health check"
            assert fresh.status == "pending", (
                f"Expected session to remain 'pending' after health check with a non-done() "
                f"worker (worker_alive=True). Got status='{fresh.status}'. "
                f"If 'running': health check escalated — the backstop behavior changed and "
                f"this test needs updating. "
                f"This test documents that the 300s backstop is blind to the suspension wedge "
                f"(worker loop parked at await semaphore.acquire())."
            )

            # --- Assert the event was nudged (event.set() called) ---
            assert fake_event.is_set(), (
                "Expected the health check pending branch to call event.set() (nudge) "
                "when worker_alive=True. The event was not set — health check behavior changed."
            )

        finally:
            # Teardown: cancel fake future to suppress "Future exception never retrieved"
            if not fake_future.done():
                fake_future.cancel()

            # Restore module globals
            _session_state._global_session_semaphore = prior_semaphore
            _session_state._shutdown_requested = prior_shutdown

            # Remove worker registration
            _active_workers.pop(self.WORKER_KEY, None)
            _active_events.pop(self.WORKER_KEY, None)

            # Delete test sessions via Popoto ORM
            stale = [s for s in AgentSession.query.all() if s.project_key == "test-wedge"]
            for s in stale:
                try:
                    s.delete()
                except Exception:
                    pass

    @pytest.mark.asyncio
    async def test_health_check_escalates_when_no_worker(self, redis_test_db):
        """Control test: health check DOES start a worker when no worker is registered.

        Contrast with A2: without a live worker future, ``worker_alive = False``
        and the health check calls ``_ensure_worker`` (after the age threshold).
        This confirms the escalation path works — it is the path that is
        bypassed when the worker is parked (``not done()`` → ``worker_alive = True``).
        """
        prior_semaphore = _session_state._global_session_semaphore
        prior_shutdown = _session_state._shutdown_requested

        try:
            # Ensure no worker is registered for this key
            _active_workers.pop(self.WORKER_KEY + "-ctrl", None)
            _active_events.pop(self.WORKER_KEY + "-ctrl", None)

            ctrl_key = self.WORKER_KEY + "-ctrl"

            # Create a pending session that is old enough to trigger escalation
            old_created_at = time.time() - 400  # 400s > AGENT_SESSION_HEALTH_MIN_RUNNING (300s)
            session = _create_wedge_test_session(
                ctrl_key, created_at=old_created_at, session_id="wedge-control-01"
            )
            assert session.status == "pending"

            # Run health check — should start a worker (since worker_alive=False)
            await _agent_session_health_check()

            # A new worker task should have been started for ctrl_key
            new_task = _active_workers.get(ctrl_key)

        finally:
            # Cancel any spawned task immediately
            spawned = _active_workers.get(self.WORKER_KEY + "-ctrl")
            if spawned is not None and not spawned.done():
                spawned.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(spawned), timeout=0.5)
                except (TimeoutError, asyncio.CancelledError):
                    pass

            _session_state._global_session_semaphore = prior_semaphore
            _session_state._shutdown_requested = prior_shutdown
            _active_workers.pop(self.WORKER_KEY + "-ctrl", None)
            _active_events.pop(self.WORKER_KEY + "-ctrl", None)

            stale = [s for s in AgentSession.query.all() if s.project_key == "test-wedge"]
            for s in stale:
                try:
                    s.delete()
                except Exception:
                    pass

        assert new_task is not None, (
            "Expected health check to start a worker (via _ensure_worker) for a pending "
            "session whose worker is not alive (worker_alive=False). "
            "No task was registered in _active_workers. "
            "This control test confirms the escalation path works — it is the path "
            "bypassed when the worker is parked (A2)."
        )
