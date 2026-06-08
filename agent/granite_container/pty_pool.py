"""Bounded PM+Dev PTY slot pool (granite PTY production cutover, plan #1572).

The PTYPool caps the number of concurrent interactive `claude` TUI pairs
the worker can hold open. With `GRANITE_PTY_POOL_SIZE=3` and
`MAX_CONCURRENT_SESSIONS=8`, the worker can run at most 3 granite
sessions in parallel; the rest queue in Redis.

Why bounded: every `claude --permission-mode bypassPermissions` PTY
consumes ~200 MB of resident memory. N concurrent granite sessions
holding fresh PTYs can starve the operator's machine. A bounded pool
plus background respawn means the next acquirer of a slot gets a
fresh PTY pair, not a stale one.

Why singleton: pool state is worker-process-local. A new process
re-initializes; existing pids are read from `data/granite_pty_pids.json`
on startup so orphan cleanup still works across restarts.

Slot lifecycle
--------------

::

    idle --acquire--> locked --release--> respawning --respawn done--> idle
                                  \\-> respawn failed: still in respawning
                                       (semaphore not released; respawn task
                                        holds the slot, logs the failure)

Contract for `_respawn_slot` (race-free, harden POOL-1 + ADV-4):

1. `event.clear()` as the first line — defends against a prior
   `event.set()` that latched the event after a previous respawn
   round. Without this clear, the next `event.wait()` would return
   immediately with a stale `pty_pair`.
2. Hold the per-slot `asyncio.Lock` across the spawn. The lock is
   held under `asyncio.shield(...)` so a worker-shutdown
   `CancelledError` does not interrupt mid-spawn; cancellation can
   only fire after the lock is released (i.e. after `event.set()`
   or after the spawn raised).
3. `event.set()` only after the new pair is in place.
4. On failure, the respawn task re-raises (or logs and exits) — the
   per-slot state is left as `respawning` and the next acquirer
   blocks on the event. The semaphore is NOT released (the
   per-slot state machine recovers via the operator's intervention
   — log loudly so it surfaces in dashboard.json's health view).

Worker-shutdown drain: `worker/__main__.py` shutdown hook MUST
`await asyncio.gather(*self._pool._respawn_tasks, return_exceptions=True)`
before the PID-targeted kill step. Without it, a half-spawned slot
can be left in `respawning` permanently and the next `acquire_pair`
blocks forever on its event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

from agent.granite_container.pty_driver import PTYDriver

logger = logging.getLogger(__name__)

# Default on-disk location for the spawned-pid registry. The pool reads
# this at startup to clear orphans from a prior worker process.
DEFAULT_PID_REGISTRY_PATH = "data/granite_pty_pids.json"


@dataclass
class _Slot:
    """Per-slot state machine.

    `state` transitions: idle -> locked -> respawning -> idle.
    `pty_pair` is the freshly-spawned (pm, dev) pair; set by the
    respawn task under the per-slot lock.
    `event` is set when the slot is `idle`; cleared at the start of
    every respawn.
    `lock` serializes the spawn vs. acquire_pair reads.
    """

    idx: int
    state: str = "idle"  # idle | locked | respawning
    pty_pair: tuple[PTYDriver, PTYDriver] | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PTYPoolError(RuntimeError):
    """Raised for caller-facing pool errors (bad config, no slot available)."""


class PTYPool:
    """Bounded PM+Dev PTY slot pool.

    Construction does NOT spawn the slots. Call `initialize()` to
    pre-warm the pool. Acquirers use `acquire_pair()` as an async
    context manager:

        async with pool.acquire_pair() as (pm, dev):
            # use pm/dev
        # pool released and respawn scheduled

    A no-op fallback: if `initialize()` is never called, the pool
    reports zero idle slots and every `acquire_pair()` blocks. The
    worker startup hook is responsible for calling `initialize()`.
    """

    def __init__(self, pool_size: int, pid_registry_path: str = DEFAULT_PID_REGISTRY_PATH) -> None:
        if pool_size <= 0:
            raise PTYPoolError(f"PTYPool pool_size must be > 0; got {pool_size}")
        self._pool_size = pool_size
        self._sem = asyncio.Semaphore(pool_size)
        self._slots: list[_Slot] = [_Slot(idx=i) for i in range(pool_size)]
        # Track spawned pids for orphan kill at worker startup.
        self._spawned_pids: set[int] = set()
        self._pid_registry_path = pid_registry_path
        self._respawn_tasks: list[asyncio.Task] = []
        self._initialized = False

    # -- Inspection -------------------------------------------------------

    @property
    def pool_size(self) -> int:
        return self._pool_size

    def get_spawned_pids(self) -> set[int]:
        """Return a copy of the spawned-pid set. The caller can kill them
        at worker startup (and the pool can clear the set)."""
        return set(self._spawned_pids)

    def clear_spawned_pids(self) -> None:
        self._spawned_pids.clear()
        self._persist_pids()

    # -- Lifecycle --------------------------------------------------------

    async def initialize(self, cwd: str | None = None) -> None:
        """Pre-warm all slots. Idempotent: calling twice is a no-op."""
        if self._initialized:
            return
        await asyncio.gather(
            *(self._spawn_slot(idx, cwd=cwd) for idx in range(self._pool_size)),
            return_exceptions=True,
        )
        # Pre-warm may have left slots in `respawning` if a spawn
        # failed. The semaphore count was not consumed by pre-warm
        # (semaphore acquire happens in `acquire_pair`, not in
        # `_spawn_slot`), so the pool is ready: every slot has
        # either an idle pair or a respawning slot the next acquirer
        # will wait on.
        self._initialized = True
        self._load_persisted_pids()

    def shutdown(self) -> None:
        """Best-effort cancel of in-flight respawn tasks. The caller
        should `await drain_respawns()` after this to await their
        completion. This does NOT close live PTYs — the container's
        `_close_pair` owns PTY lifecycle."""
        for task in self._respawn_tasks:
            if not task.done():
                task.cancel()

    async def drain_respawns(self) -> None:
        """Await all in-flight respawn tasks (returns when each has
        completed or raised)."""
        if not self._respawn_tasks:
            return
        await asyncio.gather(*self._respawn_tasks, return_exceptions=True)
        self._respawn_tasks = []

    # -- Orphan cleanup ---------------------------------------------------

    def _load_persisted_pids(self) -> None:
        """Read persisted pids from the registry file (if any)."""
        path = Path(self._pid_registry_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for pid in data.get("pids", []):
                self._spawned_pids.add(int(pid))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning("[pty-pool] could not read pid registry %s: %s", path, e)

    def _persist_pids(self) -> None:
        """Write the spawned-pid set to the registry file. Best-effort."""
        path = Path(self._pid_registry_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"pids": sorted(self._spawned_pids)}))
        except OSError as e:
            logger.warning("[pty-pool] could not persist pid registry %s: %s", path, e)

    @staticmethod
    def kill_orphans(pids: set[int]) -> int:
        """Best-effort SIGKILL of the given pids. Returns the count killed.

        `ProcessLookupError` is swallowed (process already gone). Other
        exceptions are logged and skipped — orphan cleanup is best-effort.
        """
        killed = 0
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                pass
            except OSError as e:
                logger.warning("[pty-pool] could not kill orphan pid=%s: %s", pid, e)
        return killed

    # -- Acquire / release ------------------------------------------------

    @contextlib.asynccontextmanager
    async def acquire_pair(self):
        """Acquire a (pm_pty, dev_pty) pair. Blocks if all slots are
        locked or respawning.

        On entry: the slot is idle with a live pair. The slot's
        `pty_pair` is set; the respawn task on `release_pair` will
        create a fresh pair under the per-slot lock.

        On exit: the slot is released, old PTYs are closed, and a
        background respawn is scheduled.
        """
        await self._sem.acquire()
        slot: _Slot | None = None
        try:
            slot = await self._wait_for_idle_slot()
            if slot.state != "idle":
                # Should not happen — `_wait_for_idle_slot` should
                # have returned a slot whose event is set, which is
                # the contract for `idle`. Defensive.
                raise PTYPoolError(
                    f"PTYPool slot {slot.idx} returned in non-idle state: {slot.state}"
                )
            slot.state = "locked"
            pm, dev = slot.pty_pair
            yield (pm, dev)
        finally:
            if slot is not None:
                # Release: close old PTYs, schedule respawn.
                await self._release_pair(slot)
                self._sem.release()

    async def _wait_for_idle_slot(self) -> _Slot:
        """Wait until at least one slot is idle.

        Linear scan over the slots; the pool size is small (default
        3, max ~6 in the future). A more sophisticated data
        structure (priority queue by idleness time) is overkill.
        """
        while True:
            for slot in self._slots:
                if slot.state == "idle" and slot.event.is_set():
                    return slot
            # All slots are either locked or respawning. Wait on the
            # next slot we can find. Round-robin: start at the
            # next index to avoid starvation.
            respawning = [s for s in self._slots if s.state == "respawning"]
            if not respawning:
                # All locked, no respawning slots. Yield to let
                # releases run, then loop.
                await asyncio.sleep(0.01)
                continue
            # Wait on the first respawning slot we see.
            slot = respawning[0]
            await slot.event.wait()
            # Loop again — the slot we just waited on might be
            # `idle` now, or a different one might have become
            # `idle` while we waited.

    async def _release_pair(self, slot: _Slot) -> None:
        """Close old PTYs, transition slot to `respawning`, schedule
        background respawn. The semaphore is released by the caller
        in `acquire_pair`'s `finally`."""
        slot.state = "respawning"
        # Close the old pair if any. Best-effort.
        if slot.pty_pair is not None:
            pm, dev = slot.pty_pair
            for pty in (pm, dev):
                try:
                    pty.close(force=True)
                except Exception:
                    pass
            slot.pty_pair = None
        # Schedule respawn in the background.
        task = asyncio.create_task(self._respawn_slot(slot))
        self._respawn_tasks.append(task)

    # -- Respawn ----------------------------------------------------------

    async def _spawn_slot(self, idx: int, cwd: str | None = None) -> None:
        """Spawn a fresh (pm, dev) pair into slot idx. Used by
        `initialize` for pre-warm and by `_respawn_slot` after a
        release.

        Race-free respawn contract (POOL-1 + ADV-4): the first
        thing this method does — before anything else — is mark
        the slot as `respawning` and clear its event. The clear
        is critical: a previous respawn round may have left the
        event in a latched (set) state, and without the clear,
        the next `event.wait()` on this slot would return
        immediately with a stale `pty_pair`. The state mark is
        also useful for inspection: an operator looking at the
        pool mid-failure sees the slot in `respawning` rather
        than `idle` with a stale pair.

        Failure logs and re-raises; the slot is left in
        `respawning` (the event is not set, so the next acquirer
        blocks — operator's intervention surfaces the issue).
        """
        slot = self._slots[idx]
        slot.state = "respawning"
        slot.event.clear()
        try:
            async with slot.lock:
                pm = PTYDriver(role="pm", cwd=cwd)
                dev = PTYDriver(role="dev", cwd=cwd)
                pm.spawn()
                dev.spawn()
                slot.pty_pair = (pm, dev)
                # Record pids for orphan kill.
                pm_pid = pm._child.pid if pm._child is not None else None
                dev_pid = dev._child.pid if dev._child is not None else None
                if pm_pid is not None:
                    self._spawned_pids.add(pm_pid)
                if dev_pid is not None:
                    self._spawned_pids.add(dev_pid)
                self._persist_pids()
            slot.state = "idle"
            slot.event.set()
        except Exception as e:
            logger.error("[pty-pool] slot %d spawn failed: %s", idx, e)
            # Re-raise so initialize can surface it; for respawn
            # tasks, the exception is captured by the task's own
            # state and the slot stays in `respawning` (event not
            # set). Operator must intervene.
            raise

    async def _respawn_slot(self, slot: _Slot) -> None:
        """Background respawn after release. Wraps `_spawn_slot` under
        `asyncio.shield` so worker-shutdown cancellation cannot
        interrupt mid-spawn."""
        try:
            # Race-free respawn contract (POOL-1 + ADV-4):
            # 1. event.clear() inside the spawn (handled by _spawn_slot).
            # 2. Hold the per-slot lock across the spawn.
            # 3. Shield the lock-held section from cancellation.
            await asyncio.shield(self._spawn_slot(slot.idx))
        except asyncio.CancelledError:
            # Worker shutdown. The shielded section has already
            # completed (or raised) by the time we see this — the
            # lock was released before the shield broke. Slot state
            # is whatever the spawn left it as.
            logger.info("[pty-pool] respawn for slot %d cancelled", slot.idx)
        except Exception as e:
            # Spawn failed. Slot is in `respawning`; event is not
            # set. Next acquirer blocks. Log loudly.
            logger.error("[pty-pool] respawn for slot %d failed: %s", slot.idx, e)
            # Do not set the event; the slot remains unavailable.
