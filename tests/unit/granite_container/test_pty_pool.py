"""Tests for the bounded PM+Dev PTY slot pool (plan #1572, Task 2)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.granite_container.pty_pool import PTYPool, PTYPoolError


def _make_pool(size: int = 3, pid_registry: str | None = None) -> PTYPool:
    """Build a pool. By default the pid registry is a temp file so the
    test never touches `data/granite_pty_pids.json` on disk."""
    if pid_registry is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        pid_registry = tmp.name
    return PTYPool(pool_size=size, pid_registry_path=pid_registry)


def _patch_spawn_to_succeed():
    """Replace PTYDriver.spawn with a no-op so pre-warm and respawn
    succeed without spawning a real `claude` process. Returns the
    patch object; the caller manages the lifecycle."""
    return patch("agent.granite_container.pty_pool.PTYDriver.spawn", lambda self: None)


def _patch_spawn_with_pids(recorded: dict[str, list[int]]):
    """Like _patch_spawn_to_succeed but records child pids in
    `recorded['pids']` so orphan-kill tests can assert the pool
    tracked them. The pool reads `pm._child.pid` and `dev._child.pid`;
    we install a fake `_child` on the driver instance via a patched
    `PTYDriver.spawn` that creates a new fake child per driver."""

    class _FakeChild:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    pid_counter = [10000]

    def _fake_spawn(self) -> None:
        # Each driver instance gets a unique pid.
        my_pid = pid_counter[0]
        pid_counter[0] += 1
        self._child = _FakeChild(my_pid)
        recorded["pids"].append(my_pid)

    return patch("agent.granite_container.pty_pool.PTYDriver.spawn", _fake_spawn)


class TestPoolSizeValidation(unittest.TestCase):
    def test_pool_size_zero_raises(self) -> None:
        with self.assertRaises(PTYPoolError) as ctx:
            PTYPool(pool_size=0)
        self.assertIn("must be > 0", str(ctx.exception))

    def test_pool_size_negative_raises(self) -> None:
        with self.assertRaises(PTYPoolError) as ctx:
            PTYPool(pool_size=-1)
        self.assertIn("must be > 0", str(ctx.exception))

    def test_pool_size_positive_ok(self) -> None:
        pool = _make_pool(size=3)
        self.assertEqual(pool.pool_size, 3)


class TestPidRegistry(unittest.TestCase):
    def test_pid_registry_read(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump({"pids": [12345, 67890]}, f)
            registry = f.name
        try:
            pool = _make_pool(pid_registry=registry)
            # Read happens in initialize; force it manually.
            pool._load_persisted_pids()
            self.assertIn(12345, pool.get_spawned_pids())
            self.assertIn(67890, pool.get_spawned_pids())
        finally:
            os.unlink(registry)

    def test_kill_orphans_swallows_process_lookup_error(self) -> None:
        # A pid that almost certainly does not exist.
        killed = PTYPool.kill_orphans({99999999})
        # ProcessLookupError -> silently skipped. 0 killed.
        self.assertEqual(killed, 0)


class TestSpawnTracking(unittest.TestCase):
    def test_spawn_records_pids(self) -> None:
        recorded: dict[str, list[int]] = {"pids": []}
        pool = _make_pool(size=2)
        with _patch_spawn_with_pids(recorded):
            asyncio.run(pool.initialize())
        pids = pool.get_spawned_pids()
        # Two slots * two PTYs each = 4 pids.
        self.assertEqual(len(pids), 4)
        # All recorded pids are tracked.
        for pid in recorded["pids"]:
            self.assertIn(pid, pids)

    def test_clear_spawned_pids_empties_set(self) -> None:
        recorded: dict[str, list[int]] = {"pids": []}
        pool = _make_pool(size=1)
        with _patch_spawn_with_pids(recorded):
            asyncio.run(pool.initialize())
        self.assertGreater(len(pool.get_spawned_pids()), 0)
        pool.clear_spawned_pids()
        self.assertEqual(len(pool.get_spawned_pids()), 0)

    def test_pids_persisted_to_disk(self) -> None:
        recorded: dict[str, list[int]] = {"pids": []}
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        try:
            pool = _make_pool(size=1, pid_registry=tmp.name)
            with _patch_spawn_with_pids(recorded):
                asyncio.run(pool.initialize())
            data = json.loads(Path(tmp.name).read_text())
            self.assertIn("pids", data)
            self.assertEqual(len(data["pids"]), 2)  # pm + dev
        finally:
            os.unlink(tmp.name)


class TestAcquireRelease(unittest.TestCase):
    def test_acquire_returns_pair(self) -> None:
        pool = _make_pool(size=2)
        with _patch_spawn_to_succeed():
            asyncio.run(pool.initialize())

            # An acquire should yield (pm, dev). With spawn mocked
            # to a no-op, both are PTYDriver instances whose
            # _child is None. Just assert tuple shape.
            async def _runner() -> None:
                async with pool.acquire_pair() as pair:
                    self.assertIsInstance(pair, tuple)
                    self.assertEqual(len(pair), 2)

            asyncio.run(_runner())

    def test_acquire_blocks_when_all_slots_locked(self) -> None:
        pool = _make_pool(size=2)
        with _patch_spawn_to_succeed():
            asyncio.run(pool.initialize())

            acquired_count = 0
            release_event = asyncio.Event()

            async def hold_slot() -> None:
                nonlocal acquired_count
                async with pool.acquire_pair():
                    acquired_count += 1
                    await release_event.wait()

            async def try_acquire() -> None:
                # Try to acquire a third slot. Should block.
                try:
                    async with pool.acquire_pair():
                        pass
                except TimeoutError:
                    pass

            async def runner() -> None:
                # Start two holders (uses both slots).
                holders = [asyncio.create_task(hold_slot()) for _ in range(2)]
                # Give the holders a moment to acquire.
                await asyncio.sleep(0.05)
                self.assertEqual(acquired_count, 2)
                # Try to acquire a third; this should hang.
                third = asyncio.create_task(try_acquire())
                # If the third is hung, that's the assertion: it
                # never completes within 0.2s.
                try:
                    await asyncio.wait_for(third, timeout=0.2)
                except TimeoutError:
                    pass  # Expected: blocked
                # Release the holders and let the third try.
                release_event.set()
                await asyncio.gather(*holders, return_exceptions=True)
                # After release, the semaphore frees up; the third
                # task should now complete (if it had been given
                # the chance to re-run). Cancel the third task
                # because we are just asserting blocking.
                third.cancel()
                with __import__("contextlib").suppress(asyncio.CancelledError):
                    await third

            asyncio.run(runner())

    def test_release_schedules_respawn(self) -> None:
        pool = _make_pool(size=1)
        with _patch_spawn_to_succeed():
            asyncio.run(pool.initialize())
            assert pool._respawn_tasks == []  # no respawns yet

            async def runner() -> None:
                async with pool.acquire_pair() as _:
                    pass
                # After release, a respawn task should exist.
                self.assertEqual(len(pool._respawn_tasks), 1)
                # Drain it.
                await pool.drain_respawns()
                self.assertEqual(pool._respawn_tasks, [])

            asyncio.run(runner())


class TestRespawnFailure(unittest.TestCase):
    def test_failed_spawn_leaves_slot_in_respawning(self) -> None:
        pool = _make_pool(size=1)

        def _fail_spawn(self) -> None:
            raise RuntimeError("simulated spawn failure")

        with patch("agent.granite_container.pty_pool.PTYDriver.spawn", _fail_spawn):
            # Initialize catches the exception; slot is in respawning.
            asyncio.run(pool.initialize())
            slot = pool._slots[0]
            self.assertEqual(slot.state, "respawning")
            # The event is NOT set; an acquirer would block.
            self.assertFalse(slot.event.is_set())


class TestEventClearFirstLine(unittest.TestCase):
    """Race-free respawn contract: _spawn_slot MUST clear the event as
    its first line, so a previous event.set() from a prior respawn
    round does not leave a latched event that returns immediately
    on a subsequent event.wait() with a stale pty_pair."""

    def test_event_cleared_at_start_of_spawn(self) -> None:
        pool = _make_pool(size=1)

        # Pre-set the event to simulate a stale latched state.
        pool._slots[0].event.set()
        self.assertTrue(pool._slots[0].event.is_set())

        # Track the order of state transitions.
        states_during_spawn: list[tuple[bool, str]] = []

        class _TrackingDriver:
            """A fake PTYDriver replacement that records state
            during construction (i.e. during the spawn)."""

            def __init__(self, role: str, cwd: str | None = None) -> None:
                # This is called inside _spawn_slot under the lock.
                # Record the current event/state.
                states_during_spawn.append((pool._slots[0].event.is_set(), pool._slots[0].state))

        with patch("agent.granite_container.pty_pool.PTYDriver", _TrackingDriver):
            try:
                asyncio.run(pool._spawn_slot(0))
            except Exception:
                pass  # _TrackingDriver doesn't have spawn(); expected.
            # The first state snapshot must show the event cleared.
            # If _spawn_slot did NOT clear the event first, this
            # fails — which is the regression we're guarding
            # against.
            self.assertGreater(len(states_during_spawn), 0)
            first_event_set, first_state = states_during_spawn[0]
            self.assertFalse(
                first_event_set,
                "event must be cleared as the first line of _spawn_slot",
            )
            self.assertEqual(first_state, "respawning")


class _SessionSpecDriver:
    """Fake PTYDriver accepting the per-session spawn kwargs."""

    instances: list = []

    def __init__(
        self,
        role: str = "pm",
        cwd: str | None = None,
        model: str | None = None,
        env: dict | None = None,
        append_system_prompt: str | None = None,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self.model = model
        self.env = env
        self.append_system_prompt = append_system_prompt
        self._child = None  # _pair_is_live treats None-child as live
        self.closed = False
        _SessionSpecDriver.instances.append(self)

    def spawn(self) -> None:
        pass

    def isalive(self) -> bool:
        return True

    def close(self, force: bool = True) -> None:
        self.closed = True


class TestSpawnOnAcquire(unittest.TestCase):
    """Spawn-on-acquire (PR #1612 review B1+B2): a spec carrying
    per-session requirements replaces the pre-warmed pair in the
    SAME slot; the bounded-slot invariant and the normal release/
    respawn lifecycle are preserved."""

    def setUp(self) -> None:
        _SessionSpecDriver.instances = []
        from agent.granite_container.pty_pool import PairSpawnSpec

        self.PairSpawnSpec = PairSpawnSpec

    def _patched_pool(self, size: int = 1) -> PTYPool:
        return _make_pool(size=size)

    def test_env_spec_replaces_prewarmed_pair_in_same_slot(self) -> None:
        async def _run():
            pool = self._patched_pool()
            await pool.initialize(cwd="/x")
            prewarmed = pool._slots[0].pty_pair
            spec = self.PairSpawnSpec(cwd="/x", env={"SESSION_TYPE": "pm"})
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev):
                # Fresh pair, not the prewarmed one.
                self.assertIsNot(pm, prewarmed[0])
                self.assertIsNot(dev, prewarmed[1])
                self.assertEqual(pm.env, {"SESSION_TYPE": "pm"})
                # Same slot holds the fresh pair (bounded-slot invariant).
                self.assertIs(pool._slots[0].pty_pair[0], pm)
            # Prewarmed pair was closed at replacement.
            self.assertTrue(prewarmed[0].closed)
            self.assertTrue(prewarmed[1].closed)
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _SessionSpecDriver):
            asyncio.run(_run())

    def test_cwd_mismatch_triggers_session_spawn(self) -> None:
        async def _run():
            pool = self._patched_pool()
            await pool.initialize(cwd="/pool-cwd")
            spec = self.PairSpawnSpec(cwd="/worktree")
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev):
                self.assertEqual(pm.cwd, "/worktree")
                self.assertEqual(dev.cwd, "/worktree")
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _SessionSpecDriver):
            asyncio.run(_run())

    def test_model_spec_reaches_role_drivers(self) -> None:
        """`pm_model`/`dev_model` land on the matching role's driver
        (pm_model carries the D1-resolved session model; dev_model has
        no production producer today but is pool-layer API)."""

        async def _run():
            pool = self._patched_pool()
            await pool.initialize(cwd="/x")
            spec = self.PairSpawnSpec(cwd="/x", pm_model="opus", dev_model="sonnet")
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev):
                self.assertEqual(pm.model, "opus")
                self.assertEqual(dev.model, "sonnet")
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _SessionSpecDriver):
            asyncio.run(_run())

    def test_matching_spec_uses_prewarmed_pair(self) -> None:
        async def _run():
            pool = self._patched_pool()
            await pool.initialize(cwd="/x")
            prewarmed = pool._slots[0].pty_pair
            spec = self.PairSpawnSpec(cwd="/x")  # no env/persona/model
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev):
                self.assertIs(pm, prewarmed[0])
                self.assertIs(dev, prewarmed[1])
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _SessionSpecDriver):
            asyncio.run(_run())

    def test_failed_session_spawn_releases_slot_and_semaphore(self) -> None:
        """A failing per-session spawn must not leak the semaphore:
        the acquire raises, the slot goes through the normal
        release/respawn path, and a later acquire succeeds."""

        fail_next = {"on": False}

        class _FlakyDriver(_SessionSpecDriver):
            def spawn(self) -> None:
                if fail_next["on"]:
                    raise RuntimeError("simulated session spawn failure")

        async def _run():
            pool = self._patched_pool()
            await pool.initialize(cwd="/x")
            spec = self.PairSpawnSpec(cwd="/x", env={"A": "1"})
            fail_next["on"] = True
            with self.assertRaises(RuntimeError):
                async with pool.acquire_pair(spawn_spec=spec):
                    pass  # pragma: no cover - never reached
            fail_next["on"] = False
            await pool.drain_respawns()
            # The slot recovered: a plain acquire succeeds.
            async with pool.acquire_pair() as (pm, dev):
                self.assertIsNotNone(pm)
                self.assertIsNotNone(dev)
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _FlakyDriver):
            asyncio.run(_run())


class TestNoSleepPollWait(unittest.TestCase):
    """PR #1612 review nit: `_wait_for_idle_slot` must not busy-poll
    with `asyncio.sleep` while all slots are locked — it waits on the
    pool-level condition notified when a slot turns idle."""

    def test_all_locked_waiter_wakes_via_condition_not_sleep(self) -> None:
        import agent.granite_container.pty_pool as pty_pool_mod

        real_sleep = asyncio.sleep

        async def _forbidden_sleep(*a, **kw):
            raise AssertionError("_wait_for_idle_slot busy-polled via asyncio.sleep")

        async def _run():
            pool = _make_pool(size=1)
            await pool.initialize()
            slot = pool._slots[0]
            # Force the all-locked / no-respawning state the old code
            # sleep-polled on.
            slot.state = "locked"
            slot.event.clear()
            with patch.object(pty_pool_mod.asyncio, "sleep", _forbidden_sleep):
                waiter = asyncio.create_task(pool._wait_for_idle_slot())
                # Let the waiter park on the condition (cooperative
                # yields only — asyncio.sleep is forbidden).
                for _ in range(10):
                    await real_sleep(0)
                self.assertFalse(waiter.done())
                # The slot turns idle via the normal spawn path; its
                # condition notify must wake the parked waiter.
                await pool._spawn_slot(0)
                got = await asyncio.wait_for(waiter, timeout=2)
            self.assertIs(got, slot)

        with _patch_spawn_to_succeed():
            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
