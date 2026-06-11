"""Tests for the PTYPool hardenings from the PR #1612 deep-dive audit.

Covers four fixes:

1. Respawned slots reuse the cwd captured by `initialize(cwd=...)` —
   previously respawns silently fell back to the worker process's cwd.
2. `acquire_pair` checks pair liveness and recycles a dead pair instead
   of handing it to a session (guaranteed hang before the fix).
3. `_release_pair` prunes completed respawn tasks so the task list does
   not grow by one Task per session for the worker's lifetime.
4. `initialize` loads persisted pids BEFORE the first spawn persists,
   so a prior process's orphan pids are not clobbered.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.granite_container.pty_pool import PTYPool


class _FakeDriver:
    """Stands in for PTYDriver: records ctor args, controllable liveness."""

    instances: list[_FakeDriver] = []

    class _FakeChild:
        pid = None  # pool skips pid tracking for None pids

    def __init__(self, role: str = "pm", cwd: str | None = None, model: str | None = None):
        self.role = role
        self.cwd = cwd
        self._alive = True
        # A real (fake) child: liveness checks only apply to drivers
        # that have one — `_child is None` is treated as test-stub-live.
        self._child = self._FakeChild()
        self.closed = False
        _FakeDriver.instances.append(self)

    def spawn(self) -> None:
        pass

    def isalive(self) -> bool:
        return self._alive

    def close(self, force: bool = True) -> None:
        self.closed = True
        self._alive = False


class _PoolTestBase(unittest.TestCase):
    def setUp(self) -> None:
        _FakeDriver.instances = []
        self._tmp = tempfile.TemporaryDirectory()
        self.registry = str(Path(self._tmp.name) / "pids.json")
        self._patcher = patch("agent.granite_container.pty_pool.PTYDriver", _FakeDriver)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmp.cleanup()


class TestRespawnKeepsCwd(_PoolTestBase):
    def test_respawned_pair_uses_initialize_cwd(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/configured/dir")
            async with pool.acquire_pair() as (pm, dev):
                self.assertEqual(pm.cwd, "/configured/dir")
            await pool.drain_respawns()

        asyncio.run(_run())
        # 2 prewarm + 2 respawn drivers; ALL must carry the configured cwd.
        self.assertEqual(len(_FakeDriver.instances), 4)
        for driver in _FakeDriver.instances:
            self.assertEqual(
                driver.cwd,
                "/configured/dir",
                f"{driver.role} driver lost the pool cwd: {driver.cwd!r}",
            )


class TestAcquireLivenessCheck(_PoolTestBase):
    def test_dead_pair_recycled_at_acquire(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            # Kill the parked pair behind the pool's back.
            slot = pool._slots[0]
            pm, dev = slot.pty_pair
            pm._alive = False

            async with pool.acquire_pair() as (new_pm, new_dev):
                # The acquired pair must be live — the dead one recycled.
                self.assertTrue(new_pm.isalive())
                self.assertTrue(new_dev.isalive())
                self.assertIsNot(new_pm, pm)
            await pool.drain_respawns()

        asyncio.run(_run())

    def test_pair_none_recycled_at_acquire(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            slot = pool._slots[0]
            slot.pty_pair = None  # simulate a half-released slot

            async with pool.acquire_pair() as (pm, dev):
                self.assertIsNotNone(pm)
                self.assertTrue(pm.isalive())
            await pool.drain_respawns()

        asyncio.run(_run())


class TestRespawnTaskPruning(_PoolTestBase):
    def test_respawn_tasks_do_not_accumulate(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            for _ in range(10):
                async with pool.acquire_pair():
                    pass
                # Let the background respawn complete before the next
                # cycle so its task is prunable.
                await pool.drain_respawns()
                # drain_respawns clears the list itself; re-acquire to
                # exercise the prune in _release_pair too.
            return len(pool._respawn_tasks)

        remaining = asyncio.run(_run())
        self.assertLessEqual(remaining, 1)

    def test_prune_in_release_pair(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            for _ in range(5):
                async with pool.acquire_pair():
                    pass
                # Give the respawn task a chance to finish WITHOUT
                # draining (drain clears the list; we want to observe
                # the prune in _release_pair).
                for _ in range(20):
                    await asyncio.sleep(0)
            # After 5 cycles with completed respawns, the list must not
            # hold 5 completed tasks.
            return len(pool._respawn_tasks)

        remaining = asyncio.run(_run())
        self.assertLessEqual(remaining, 2)


class TestReleaseClearsEvent(_PoolTestBase):
    def test_event_cleared_synchronously_on_release(self) -> None:
        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            slot = pool._slots[0]
            self.assertTrue(slot.event.is_set())
            await pool._release_pair(slot)
            # Cleared immediately — not deferred to the respawn task —
            # so a waiter cannot busy-spin on a latched event while the
            # slot is respawning.
            self.assertFalse(slot.event.is_set())
            self.assertEqual(slot.state, "respawning")
            await pool.drain_respawns()

        asyncio.run(_run())


class TestPidRegistryLoadOrder(_PoolTestBase):
    def test_prior_pids_survive_initialize(self) -> None:
        # A prior worker process left an orphan pid in the registry.
        Path(self.registry).write_text(json.dumps({"pids": [987654]}))

        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry)
            await pool.initialize(cwd="/x")
            return pool.get_spawned_pids()

        pids = asyncio.run(_run())
        self.assertIn(987654, pids, "prior orphan pid clobbered by pre-warm persist")
        on_disk = json.loads(Path(self.registry).read_text())["pids"]
        self.assertIn(987654, on_disk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
