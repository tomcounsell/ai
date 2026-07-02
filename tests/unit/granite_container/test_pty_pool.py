"""Tests for the bounded PM+Dev PTY slot pool (plan #1572, Task 2)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent.granite_container.pty_pool as pty_pool_mod
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


class TestRegisterUnregisterPid(unittest.TestCase):
    """Crash-resume PID registration seam (plan #1851): the callback
    entry points `PTYPool.register_pid`/`unregister_pid`, wired to
    `Container(on_pty_spawn=..., on_pty_despawn=...)`."""

    def test_register_pid_adds_and_persists(self) -> None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        try:
            pool = _make_pool(size=1, pid_registry=tmp.name)
            pool.register_pid(54321)
            self.assertIn(54321, pool.get_spawned_pids())
            data = json.loads(Path(tmp.name).read_text())
            self.assertIn(54321, data["pids"])
        finally:
            os.unlink(tmp.name)

    def test_unregister_pid_discards_and_persists(self) -> None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        try:
            pool = _make_pool(size=1, pid_registry=tmp.name)
            pool.register_pid(54321)
            pool.unregister_pid(54321)
            self.assertNotIn(54321, pool.get_spawned_pids())
            data = json.loads(Path(tmp.name).read_text())
            self.assertNotIn(54321, data["pids"])
        finally:
            os.unlink(tmp.name)

    def test_unregister_pid_not_present_is_a_noop(self) -> None:
        pool = _make_pool(size=1)
        # Discarding a pid that was never registered must not raise.
        pool.unregister_pid(99999)
        self.assertNotIn(99999, pool.get_spawned_pids())

    def test_register_pid_returns_without_hanging(self) -> None:
        """Deadlock guard (round-2 BLOCKER): `_persist_pids` re-acquires
        the non-reentrant `_pids_lock`. If `register_pid` ever called
        `_persist_pids()` from inside its own `with self._pids_lock:`
        block, this call would hang forever instead of returning."""
        pool = _make_pool(size=1)
        pool.register_pid(11111)  # must return promptly, not hang
        self.assertIn(11111, pool.get_spawned_pids())

    def test_register_pid_swallows_os_error(self) -> None:
        """`_persist_pids`'s except stays narrow at OSError (plan #1851
        round-2 NIT: reverted the round-1 broadening to Exception — the
        `_pids_lock` snapshot already prevents the `RuntimeError` this
        would have guarded against, so widening the catch would silently
        hide unrelated future bugs). An OSError from a bad registry path
        (e.g. permission denied, disk full) must still be swallowed and
        not propagate out of `register_pid`; persistence is best-effort."""
        pool = _make_pool(size=1)
        with (
            patch.object(Path, "write_text", side_effect=OSError("disk full")),
            self.assertLogs("agent.granite_container.pty_pool", level="WARNING") as log_ctx,
        ):
            pool.register_pid(22222)  # must not raise, must log a warning
        self.assertIn(22222, pool.get_spawned_pids())
        self.assertTrue(any("could not persist pid registry" in msg for msg in log_ctx.output))

    def test_unregister_pid_swallows_os_error(self) -> None:
        """Same narrow-OSError coverage for the unregister path."""
        pool = _make_pool(size=1)
        pool.register_pid(33333)
        with (
            patch.object(Path, "write_text", side_effect=OSError("permission denied")),
            self.assertLogs("agent.granite_container.pty_pool", level="WARNING"),
        ):
            pool.unregister_pid(33333)  # must not raise, must log a warning
        self.assertNotIn(33333, pool.get_spawned_pids())


class TestAcquireRelease(unittest.TestCase):
    def test_acquire_returns_pair(self) -> None:
        pool = _make_pool(size=2)
        with _patch_spawn_to_succeed():
            asyncio.run(pool.initialize())

            # An acquire should yield (pm, dev, slot_idx). With spawn mocked
            # to a no-op, both are PTYDriver instances whose
            # _child is None. Just assert tuple shape and slot index type.
            async def _runner() -> None:
                async with pool.acquire_pair() as pair:
                    self.assertIsInstance(pair, tuple)
                    self.assertEqual(len(pair), 3)
                    pm, dev, slot_idx = pair
                    self.assertIsInstance(slot_idx, int)

            asyncio.run(_runner())

    def test_acquire_blocks_when_all_slots_locked(self) -> None:
        # Use a short acquire timeout so the test finishes quickly if the
        # third acquirer is not properly blocked before slots free up.
        with patch.object(pty_pool_mod, "PTY_POOL_ACQUIRE_TIMEOUT", 2):
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
                    # Try to acquire a third slot. Should block until released.
                    try:
                        async with pool.acquire_pair():
                            pass
                    except (TimeoutError, PTYPoolError):
                        pass

                async def runner() -> None:
                    # Start two holders (uses both slots).
                    holders = [asyncio.create_task(hold_slot()) for _ in range(2)]
                    # Give the holders a moment to acquire.
                    await asyncio.sleep(0.05)
                    self.assertEqual(acquired_count, 2)
                    # Try to acquire a third; this should block initially.
                    third = asyncio.create_task(try_acquire())
                    # If the third is hung, that's the assertion: it
                    # never completes within 0.2s.
                    try:
                        await asyncio.wait_for(asyncio.shield(third), timeout=0.2)
                    except TimeoutError:
                        pass  # Expected: blocked
                    # Release the holders and let the third try.
                    release_event.set()
                    await asyncio.gather(*holders, return_exceptions=True)
                    # After release the semaphore frees up; the third
                    # task should now complete. Cancel it because we are
                    # just asserting blocking behavior.
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

    def test_stuck_respawning_slot_is_force_recycled_by_bounded_acquirer(self) -> None:
        """A slot stuck in `respawning` with its event never set triggers
        `_force_recycle_slot` via the bounded slot.event.wait() timeout,
        and the next acquirer proceeds within PTY_POOL_WAIT_TIMEOUT."""
        spawn_calls = {"count": 0, "fail": True}

        def _controlled_spawn(self_driver) -> None:
            spawn_calls["count"] += 1
            if spawn_calls["fail"] and spawn_calls["count"] <= 2:
                # First two spawns (pre-warm) fail → slot stuck in respawning.
                raise RuntimeError("simulated pre-warm failure")
            # Subsequent spawns (force-recycle path) succeed.

        with patch("agent.granite_container.pty_pool.PTYDriver.spawn", _controlled_spawn):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize())
            slot = pool._slots[0]
            self.assertEqual(slot.state, "respawning")
            self.assertFalse(slot.event.is_set())

            # Now allow spawns to succeed for the force-recycle path.
            spawn_calls["fail"] = False

            # Use a very short wait timeout so the force-recycle fires quickly.
            with patch.object(pty_pool_mod, "PTY_POOL_WAIT_TIMEOUT", 0.05):
                with patch.object(pty_pool_mod, "PTY_POOL_ACQUIRE_TIMEOUT", 10):

                    async def _try_acquire():
                        async with pool.acquire_pair() as (pm, dev, slot_idx):
                            return slot_idx

                    result = asyncio.run(asyncio.wait_for(_try_acquire(), timeout=5.0))
                    # The acquirer obtained a slot after the force-recycle.
                    self.assertIsInstance(result, int)


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
        session_id: str | None = None,
        settings_path: str | None = None,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self.model = model
        self.env = env
        self.append_system_prompt = append_system_prompt
        self._session_id = session_id
        self._settings_path = settings_path
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
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev, _slot_idx):
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
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev, _slot_idx):
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
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev, _slot_idx):
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
            async with pool.acquire_pair(spawn_spec=spec) as (pm, dev, _slot_idx):
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
            async with pool.acquire_pair() as (pm, dev, _slot_idx):
                self.assertIsNotNone(pm)
                self.assertIsNotNone(dev)
            await pool.drain_respawns()

        with patch("agent.granite_container.pty_pool.PTYDriver", _FlakyDriver):
            asyncio.run(_run())


class _PidTrackingDriver:
    """Fake PTYDriver for D2 pid-persist tests (issue #1817): allocates a
    unique pid per instance on spawn(), optionally raising for one role to
    simulate a dev.spawn() failure after a successful pm.spawn()."""

    _pid_counter = [20000]
    fail_role: str | None = None  # set per-test in setUp

    class _FakeChild:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def __init__(
        self,
        role: str = "pm",
        cwd: str | None = None,
        model: str | None = None,
        env: dict | None = None,
        session_id: str | None = None,
        settings_path: str | None = None,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self._child = None
        self.closed = False

    def spawn(self) -> None:
        if _PidTrackingDriver.fail_role == self.role:
            raise RuntimeError(f"simulated {self.role} spawn failure")
        pid = _PidTrackingDriver._pid_counter[0]
        _PidTrackingDriver._pid_counter[0] += 1
        self._child = _PidTrackingDriver._FakeChild(pid)

    def isalive(self) -> bool:
        return True

    def close(self, force: bool = True) -> None:
        self.closed = True


class TestD2ImmediatePidPersist(unittest.TestCase):
    """D2 (issue #1817): `_spawn_session_pair` persists each child's pid
    IMMEDIATELY after its own spawn() returns, not batched after both roles
    spawn — so a dev.spawn() failure after a successful pm.spawn() doesn't
    strand pm's pid unreapable. A headless role (#1842) leaves `pty` as
    None — no PTY process, correctly nothing to record."""

    def setUp(self) -> None:
        _PidTrackingDriver.fail_role = None
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        self.registry_path = tmp.name

    def tearDown(self) -> None:
        try:
            os.unlink(self.registry_path)
        except OSError:
            pass

    def _read_registry(self) -> list[int]:
        with open(self.registry_path) as f:
            return json.load(f).get("pids", [])

    def test_pm_and_dev_pids_both_persisted_on_success(self) -> None:
        from agent.granite_container.pty_pool import PairSpawnSpec, _Slot

        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry_path)
            slot = _Slot(idx=0)
            spec = PairSpawnSpec(cwd="/x")
            with patch("agent.granite_container.pty_pool.PTYDriver", _PidTrackingDriver):
                await pool._spawn_session_pair(slot, spec)
            pm, dev = slot.pty_pair
            self.assertIn(pm._child.pid, pool.get_spawned_pids())
            self.assertIn(dev._child.pid, pool.get_spawned_pids())
            self.assertEqual(set(self._read_registry()), pool.get_spawned_pids())

        asyncio.run(_run())

    def test_dev_spawn_failure_leaves_pm_pid_persisted(self) -> None:
        """The whole point of D2: a dev.spawn() failure after a successful
        pm.spawn() must not strand pm's pid unpersisted/unreapable."""
        from agent.granite_container.pty_pool import PairSpawnSpec, _Slot

        _PidTrackingDriver.fail_role = "dev"

        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry_path)
            slot = _Slot(idx=0)
            spec = PairSpawnSpec(cwd="/x")
            with patch("agent.granite_container.pty_pool.PTYDriver", _PidTrackingDriver):
                with self.assertRaises(RuntimeError):
                    await pool._spawn_session_pair(slot, spec)
            self.assertEqual(len(pool.get_spawned_pids()), 1)
            self.assertEqual(set(self._read_registry()), pool.get_spawned_pids())

        asyncio.run(_run())

    def test_headless_pair_records_empty_pid_set_and_persists(self) -> None:
        """A fully-headless spec spawns no PTY process; the registry is
        still explicitly persisted with an empty pid set (not skipped)."""
        from agent.granite_container.pty_pool import PairSpawnSpec, _Slot

        # Seed stale content to prove the empty-set write actually happens.
        with open(self.registry_path, "w") as f:
            json.dump({"pids": [99999]}, f)

        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry_path)
            slot = _Slot(idx=0)
            spec = PairSpawnSpec(cwd="/x", pm_transport="headless", dev_transport="headless")
            with patch("agent.granite_container.pty_pool.PTYDriver", _PidTrackingDriver):
                await pool._spawn_session_pair(slot, spec)
            pm, dev = slot.pty_pair
            self.assertIsNone(pm)
            self.assertIsNone(dev)
            self.assertEqual(pool.get_spawned_pids(), set())

        asyncio.run(_run())
        self.assertEqual(self._read_registry(), [])

    def test_mixed_pty_pm_headless_dev_persists_only_pm(self) -> None:
        from agent.granite_container.pty_pool import PairSpawnSpec, _Slot

        async def _run():
            pool = PTYPool(pool_size=1, pid_registry_path=self.registry_path)
            slot = _Slot(idx=0)
            spec = PairSpawnSpec(cwd="/x", dev_transport="headless")
            with patch("agent.granite_container.pty_pool.PTYDriver", _PidTrackingDriver):
                await pool._spawn_session_pair(slot, spec)
            pm, dev = slot.pty_pair
            self.assertIsNotNone(pm)
            self.assertIsNone(dev)
            self.assertEqual(pool.get_spawned_pids(), {pm._child.pid})
            self.assertEqual(self._read_registry(), [pm._child.pid])

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


class TestPTYDriverSessionId(unittest.TestCase):
    """PTYDriver(session_id=...) appends --session-id <uuid> to spawn args
    and exposes a .pid property."""

    def test_session_id_appended_to_spawn_args(self) -> None:
        """When session_id is set, spawn() passes --session-id <uuid> to claude."""
        from agent.granite_container.pty_driver import PTYDriver

        captured: dict = {}

        def _fake_spawn(
            cmd: str,
            args: list,
            *,
            env=None,
            echo=False,
            encoding=None,
            preexec_fn=None,
            cwd=None,
            timeout=None,
        ):
            captured["args"] = args

            class _FakeChild:
                pid = 12345

                def isalive(self):
                    return True

            return _FakeChild()

        test_uuid = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
        driver = PTYDriver(role="pm", session_id=test_uuid)
        with patch("agent.granite_container.pty_driver.pexpect.spawn", _fake_spawn):
            driver.spawn()

        self.assertIn("--session-id", captured["args"])
        idx = captured["args"].index("--session-id")
        self.assertEqual(captured["args"][idx + 1], test_uuid)

    def test_no_session_id_does_not_append_flag(self) -> None:
        """When session_id is None (default), --session-id is NOT passed."""
        from agent.granite_container.pty_driver import PTYDriver

        captured: dict = {}

        def _fake_spawn(cmd, args, **kwargs):
            captured["args"] = args

            class _FakeChild:
                pid = 12346

                def isalive(self):
                    return True

            return _FakeChild()

        driver = PTYDriver(role="dev")
        with patch("agent.granite_container.pty_driver.pexpect.spawn", _fake_spawn):
            driver.spawn()

        self.assertNotIn("--session-id", captured.get("args", []))

    def test_pid_property_returns_child_pid_when_alive(self) -> None:
        """PTYDriver.pid returns the pexpect child's PID when alive."""
        from agent.granite_container.pty_driver import PTYDriver

        class _FakeChild:
            pid = 99999

            def isalive(self):
                return True

        driver = PTYDriver(role="pm")
        driver._child = _FakeChild()
        self.assertEqual(driver.pid, 99999)

    def test_pid_property_returns_none_when_no_child(self) -> None:
        """PTYDriver.pid returns None when _child is None."""
        from agent.granite_container.pty_driver import PTYDriver

        driver = PTYDriver(role="pm")
        self.assertIsNone(driver.pid)

    def test_pid_property_returns_none_when_dead(self) -> None:
        """PTYDriver.pid returns None when child is not alive."""
        from agent.granite_container.pty_driver import PTYDriver

        class _DeadChild:
            pid = 11111

            def isalive(self):
                return False

        driver = PTYDriver(role="pm")
        driver._child = _DeadChild()
        self.assertIsNone(driver.pid)


class TestPairSpawnSpecSessionIds(unittest.TestCase):
    """PairSpawnSpec has pm_session_id and dev_session_id fields."""

    def test_pair_spawn_spec_has_session_id_fields(self) -> None:
        from agent.granite_container.pty_pool import PairSpawnSpec

        spec = PairSpawnSpec(
            pm_session_id="pm-uuid-1234",
            dev_session_id="dev-uuid-5678",
        )
        self.assertEqual(spec.pm_session_id, "pm-uuid-1234")
        self.assertEqual(spec.dev_session_id, "dev-uuid-5678")

    def test_pair_spawn_spec_defaults_none(self) -> None:
        from agent.granite_container.pty_pool import PairSpawnSpec

        spec = PairSpawnSpec()
        self.assertIsNone(spec.pm_session_id)
        self.assertIsNone(spec.dev_session_id)


class TestContainerResultPidAndTranscript(unittest.TestCase):
    """ContainerResult has pm_pid, dev_pid, pm_transcript_path, dev_transcript_path."""

    def test_container_result_has_pid_and_transcript_fields(self) -> None:
        from agent.granite_container.container import ContainerResult

        result = ContainerResult(
            session_id="s1",
            user_message="hello",
            pm_pid=1001,
            dev_pid=1002,
            pm_transcript_path="/home/.claude/projects/-tmp/pm-uuid.jsonl",
            dev_transcript_path="/home/.claude/projects/-tmp/dev-uuid.jsonl",
        )
        self.assertEqual(result.pm_pid, 1001)
        self.assertEqual(result.dev_pid, 1002)
        self.assertEqual(result.pm_transcript_path, "/home/.claude/projects/-tmp/pm-uuid.jsonl")
        self.assertEqual(result.dev_transcript_path, "/home/.claude/projects/-tmp/dev-uuid.jsonl")

    def test_container_result_defaults_none(self) -> None:
        from agent.granite_container.container import ContainerResult

        result = ContainerResult(session_id="s1", user_message="hello")
        self.assertIsNone(result.pm_pid)
        self.assertIsNone(result.dev_pid)
        self.assertIsNone(result.pm_transcript_path)
        self.assertIsNone(result.dev_transcript_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
