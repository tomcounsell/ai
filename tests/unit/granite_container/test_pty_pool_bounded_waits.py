"""Tests for the bounded-wait paths in PTYPool (fix #4, issue #1815).

Covers three distinct timeout paths introduced by the POOL-1 fix:

1. `sem.acquire()` timeout → raises `PTYPoolError` with a wedge message.
2. `_slot_available.wait()` timeout → `continue` (re-scan, not raise).
3. `slot.event.wait()` timeout → `_force_recycle_slot` reschedules the
   respawn; the parked acquirer proceeds within `PTY_POOL_WAIT_TIMEOUT`.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch

import agent.granite_container.pty_pool as pty_pool_mod
from agent.granite_container.pty_pool import PTYPool, PTYPoolError


def _make_pool(size: int = 1) -> PTYPool:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    return PTYPool(pool_size=size, pid_registry_path=tmp.name)


class _FakeDriver:
    """Minimal PTYDriver stand-in: records calls, controllable liveness."""

    instances: list[_FakeDriver] = []

    def __init__(self, role: str = "pm", cwd: str | None = None, **_kwargs) -> None:
        self.role = role
        self.cwd = cwd
        self._child = None  # None-child is treated as live in _pair_is_live
        self.closed = False
        _FakeDriver.instances.append(self)

    def spawn(self) -> None:
        pass

    def isalive(self) -> bool:
        return True

    def close(self, force: bool = True) -> None:
        self.closed = True


class TestSemAcquireTimeout(unittest.TestCase):
    """Semaphore acquire times out → PTYPoolError (not a hang)."""

    def test_sem_acquire_timeout_raises_pty_pool_error(self) -> None:
        """When all semaphore slots are held and the acquire times out,
        `acquire_pair` raises `PTYPoolError` with a wedge description."""

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/x"))

        async def _run():
            # Use a very short timeout so the test finishes quickly.
            with patch.object(pty_pool_mod, "PTY_POOL_ACQUIRE_TIMEOUT", 0.05):
                with patch.object(pty_pool_mod, "PTY_POOL_WAIT_TIMEOUT", 0.05):
                    hold_entered = asyncio.Event()
                    hold_release = asyncio.Event()

                    async def _holder():
                        async with pool.acquire_pair():
                            hold_entered.set()
                            await hold_release.wait()

                    holder_task = asyncio.create_task(_holder())
                    await hold_entered.wait()

                    # Pool is fully locked — acquire should time out and raise.
                    with self.assertRaises(PTYPoolError) as ctx:
                        async with pool.acquire_pair():
                            pass

                    self.assertIn("timed out", str(ctx.exception))
                    self.assertIn("wedged", str(ctx.exception))

                    hold_release.set()
                    await holder_task
                    with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                        await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            asyncio.run(_run())

    def test_sem_acquire_timeout_does_not_leak_semaphore(self) -> None:
        """After a semaphore-acquire timeout, the semaphore count is
        unchanged — the timed-out caller never decremented it."""

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/x"))

        async def _run():
            with patch.object(pty_pool_mod, "PTY_POOL_ACQUIRE_TIMEOUT", 0.05):
                with patch.object(pty_pool_mod, "PTY_POOL_WAIT_TIMEOUT", 0.05):
                    hold_release = asyncio.Event()
                    hold_entered = asyncio.Event()

                    async def _holder():
                        async with pool.acquire_pair():
                            hold_entered.set()
                            await hold_release.wait()

                    holder_task = asyncio.create_task(_holder())
                    await hold_entered.wait()

                    # The holder holds the one slot. Sem value is 0.
                    sem_before = pool._sem._value  # type: ignore[attr-defined]

                    try:
                        async with pool.acquire_pair():
                            pass
                    except PTYPoolError:
                        pass

                    # Sem value is still 0 (no change from failed acquire).
                    self.assertEqual(pool._sem._value, sem_before)  # type: ignore[attr-defined]

                    hold_release.set()
                    await holder_task
                    with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                        await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            asyncio.run(_run())


class TestSlotAvailableWaitTimeout(unittest.TestCase):
    """_slot_available.wait() timeout → continue (re-scan), not raise."""

    def test_condition_timeout_rescans_and_finds_idle_slot(self) -> None:
        """When the pool-level condition times out (missed notify),
        the waiter re-scans and eventually finds the idle slot."""

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/x"))

        async def _run():
            slot = pool._slots[0]
            # Force the all-locked / no-respawning state.
            slot.state = "locked"
            slot.event.clear()

            with patch.object(pty_pool_mod, "PTY_POOL_WAIT_TIMEOUT", 0.05):
                waiter = asyncio.create_task(pool._wait_for_idle_slot())
                # Let the waiter park on the condition.
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertFalse(waiter.done())

                # Transition to idle WITHOUT notifying the condition —
                # simulates a missed notify. The bounded wait will time
                # out and re-scan, finding the now-idle slot.
                slot.state = "idle"
                slot.event.set()

                got = await asyncio.wait_for(waiter, timeout=2.0)
                self.assertIs(got, slot)

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            asyncio.run(_run())


class TestSlotEventWaitTimeout(unittest.TestCase):
    """slot.event.wait() timeout → force-recycle, not a permanent hang."""

    def test_slot_event_timeout_triggers_force_recycle(self) -> None:
        """When a respawning slot's event never fires within
        PTY_POOL_WAIT_TIMEOUT, `_force_recycle_slot` is called and a
        new respawn task is scheduled, allowing the acquirer to proceed."""

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/x"))

        async def _run():
            slot = pool._slots[0]

            # Jam the slot into a stuck-respawning state: cancel all
            # pending respawn tasks so nothing will set the event.
            slot.state = "respawning"
            slot.event.clear()
            for t in pool._respawn_tasks:
                t.cancel()
            await asyncio.gather(*pool._respawn_tasks, return_exceptions=True)
            pool._respawn_tasks.clear()

            with patch.object(pty_pool_mod, "PTY_POOL_WAIT_TIMEOUT", 0.05):
                with patch.object(pty_pool_mod, "PTY_POOL_ACQUIRE_TIMEOUT", 10):
                    with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                        async with pool.acquire_pair() as (pm, dev, slot_idx):
                            # Acquirer succeeded — force-recycle unblocked it.
                            self.assertIsInstance(slot_idx, int)

            with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            asyncio.run(_run())

    def test_force_recycle_reschedules_respawn_not_sets_event(self) -> None:
        """_force_recycle_slot must NOT manually set slot.event or notify
        _slot_available — only the rescheduled respawn's SUCCESS path does
        that. This test verifies no direct event.set() call is made."""

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/x"))

        async def _run():
            slot = pool._slots[0]
            slot.state = "respawning"
            slot.event.clear()
            for t in pool._respawn_tasks:
                t.cancel()
            await asyncio.gather(*pool._respawn_tasks, return_exceptions=True)
            pool._respawn_tasks.clear()

            # Call force-recycle directly and verify it doesn't set the
            # event synchronously.
            with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                await pool._force_recycle_slot(slot)

            # Immediately after the call, event is still NOT set —
            # the rescheduled respawn task hasn't run yet.
            self.assertFalse(slot.event.is_set())

            # Allow the rescheduled task to run.
            with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
                await pool.drain_respawns()

            # NOW the event should be set (set by the respawn success path).
            self.assertTrue(slot.event.is_set())
            self.assertEqual(slot.state, "idle")

        with patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver):
            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
