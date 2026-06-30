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
                                        logs the failure). The next bounded
                                        acquirer that waits past
                                        PTY_POOL_WAIT_TIMEOUT force-recycles
                                        the slot, rescheduling the respawn.

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
4. On failure, the respawn task re-raises (or logs and exits) and
   the per-slot state is left as `respawning` with its event unset.
   The semaphore is NOT released. The next acquirer that waits on
   this slot's event past `PTY_POOL_WAIT_TIMEOUT` calls
   `_force_recycle_slot`, which reschedules `_respawn_slot` under
   the per-slot lock. The slot recovers automatically (the failure
   is still logged loudly so it surfaces in dashboard.json's health
   view).

Worker-shutdown drain: `worker/__main__.py` shutdown hook MUST
`await asyncio.gather(*self._pool._respawn_tasks, return_exceptions=True)`
before the PID-targeted kill step. Without it, a half-spawned slot
can be left in `respawning` and the next `acquire_pair` waits up to
`PTY_POOL_WAIT_TIMEOUT` on its event before force-recycling it.
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

# Timeout (seconds) for the semaphore acquire in `acquire_pair`. If the
# pool is fully locked for this long the pool is likely wedged (POOL-1).
# Provisional default, tune after observing real acquire rates; override
# with the PTY_POOL_ACQUIRE_TIMEOUT env var.
PTY_POOL_ACQUIRE_TIMEOUT: float = float(os.environ.get("PTY_POOL_ACQUIRE_TIMEOUT", "120"))

# Timeout (seconds) for individual waits inside `_wait_for_idle_slot`:
# both the pool-level condition wait and the per-slot event wait.
# A missed notify is re-scanned after this many seconds; a slot stuck
# in `respawning` past this deadline triggers `_force_recycle_slot`.
# Provisional default, tune after observing real wait rates; override
# with the PTY_POOL_WAIT_TIMEOUT env var.
PTY_POOL_WAIT_TIMEOUT: float = float(os.environ.get("PTY_POOL_WAIT_TIMEOUT", "60"))


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


@dataclass
class PairSpawnSpec:
    """Per-session spawn requirements for an acquired (pm, dev) pair.

    Environment variables and the system-prompt overlay can only be
    injected at process spawn, so a session whose cwd/env/persona/model
    differs from the pool's spawn-time defaults requires a fresh pair
    spawned at acquire time (spawn-on-acquire, PR #1612 review B1+B2).
    The freshly spawned pair occupies the SAME slot as the pre-warmed
    pair it replaces — the bounded-slot invariant is preserved, and the
    pair is released/respawned through the normal lifecycle.

    A spec that carries no per-session requirements (all fields None and
    cwd equal to the pool's spawn cwd) lets the pre-warmed pair be used
    as-is.
    """

    cwd: str | None = None
    env: dict[str, str] | None = None
    pm_model: str | None = None
    dev_model: str | None = None
    # pm_system_prompt removed — persona is now delivered via prime commands
    # (issue #1692). Field kept as a stub for backward compat during transition.
    # UUID4 values pre-generated by BridgeAdapter and threaded to the PTYDriver
    # so Claude Code names its transcripts deterministically at spawn time.
    # `claude --session-id <uuid>` sets the transcript basename to <uuid>.jsonl.
    pm_session_id: str | None = None
    dev_session_id: str | None = None


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
        # Notified whenever a slot transitions to `idle` (after a spawn
        # or respawn completes). `_wait_for_idle_slot` waits on this
        # instead of sleep-polling when every slot is locked.
        self._slot_available = asyncio.Condition()
        # cwd used for every spawn (pre-warm AND respawn). Captured by
        # `initialize()`; without it, respawned pairs silently fell back
        # to the worker process's cwd instead of the configured one.
        self._cwd: str | None = None

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
        self._cwd = cwd
        # Load any pids persisted by a prior process BEFORE the first
        # spawn — `_spawn_slot` calls `_persist_pids()`, which would
        # otherwise overwrite the registry and lose the prior pids.
        # (The worker startup hook `_kill_orphaned_pty_pids` normally
        # reaps and clears them first; this ordering is the safety net
        # for callers that skip that hook.)
        self._load_persisted_pids()
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
    async def acquire_pair(self, spawn_spec: PairSpawnSpec | None = None):
        """Acquire a (pm_pty, dev_pty) pair. Blocks if all slots are
        locked or respawning.

        On entry: the slot is idle with a live pair. The slot's
        `pty_pair` is set; the respawn task on `release_pair` will
        create a fresh pair under the per-slot lock.

        `spawn_spec` (spawn-on-acquire, PR #1612 review B1+B2): when the
        session needs a different cwd or per-session env/persona/model
        than the pool's spawn-time defaults, the pre-warmed pair is
        closed and a fresh pair is spawned with the spec — env vars and
        the system-prompt overlay can only be injected at process spawn.
        The fresh pair occupies the same slot (bounded-slot invariant
        preserved) and is released/respawned normally on exit. The
        latency hit is accepted; a spec matching the pool defaults uses
        the pre-warmed pair as-is.

        On exit: the slot is released, old PTYs are closed, and a
        background respawn is scheduled.
        """
        sem_acquired = False
        try:
            await asyncio.wait_for(self._sem.acquire(), PTY_POOL_ACQUIRE_TIMEOUT)
            sem_acquired = True
        except TimeoutError:
            raise PTYPoolError(
                f"PTY pool semaphore acquire timed out after {PTY_POOL_ACQUIRE_TIMEOUT:g}s;"
                " pool may be wedged"
            ) from None
        slot: _Slot | None = None
        recycles = 0
        try:
            while True:
                slot = await self._wait_for_idle_slot()
                if slot.state != "idle":
                    # Should not happen — `_wait_for_idle_slot` should
                    # have returned a slot whose event is set, which is
                    # the contract for `idle`. Defensive.
                    raise PTYPoolError(
                        f"PTYPool slot {slot.idx} returned in non-idle state: {slot.state}"
                    )
                # Liveness check: an idle pair can have died while
                # parked (claude crash, external kill, machine sleep).
                # Handing out a dead pair guarantees a hung session —
                # recycle the slot through a respawn and pick another.
                if not self._pair_is_live(slot):
                    recycles += 1
                    if recycles > self._pool_size + 2:
                        # Every recycle produced another dead pair —
                        # spawning itself is broken (claude binary
                        # missing, OOM-killer, ...). Fail loud instead
                        # of recycling forever.
                        raise PTYPoolError(
                            f"PTYPool could not acquire a live pair after "
                            f"{recycles} recycles; spawn appears broken"
                        )
                    logger.warning("[pty-pool] slot %d pair dead at acquire; recycling", slot.idx)
                    await self._release_pair(slot)
                    slot = None
                    continue
                slot.state = "locked"
                break
            if self._needs_session_spawn(spawn_spec):
                await self._spawn_session_pair(slot, spawn_spec)
            pm, dev = slot.pty_pair
            # Surface the slot index so callers (BridgeAdapter) can stamp it
            # onto ContainerResult for dashboard observability. The index is
            # stable for the slot's lifetime; it does NOT identify the pair —
            # use co-persisted pm_pid/dev_pid to correlate the specific process.
            yield (pm, dev, slot.idx)
        finally:
            if slot is not None:
                # Release: close old PTYs, schedule respawn.
                await self._release_pair(slot)
            if sem_acquired:
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
                # All locked, no respawning slots. Granite sessions can
                # hold slots for hours, so a sleep-poll here would spin
                # for the duration (PR #1612 review nit). Wait on the
                # pool-level condition instead; `_spawn_slot` notifies
                # it whenever a slot transitions to `idle`. Re-check
                # under the condition lock to close the scan→wait race.
                async with self._slot_available:
                    if any(s.state == "idle" and s.event.is_set() for s in self._slots):
                        continue
                    try:
                        await asyncio.wait_for(self._slot_available.wait(), PTY_POOL_WAIT_TIMEOUT)
                    except TimeoutError:
                        # Missed notify or spurious timeout: fall through
                        # to the outer `continue` and re-scan the slots.
                        pass
                continue
            # Wait on the first respawning slot we see.
            slot = respawning[0]
            try:
                await asyncio.wait_for(slot.event.wait(), PTY_POOL_WAIT_TIMEOUT)
            except TimeoutError:
                # The slot is stuck in `respawning` (POOL-1 hazard).
                # Force-recycle it and re-scan.
                await self._force_recycle_slot(slot)
                continue
            # Loop again — the slot we just waited on might be
            # `idle` now, or a different one might have become
            # `idle` while we waited.

    async def _force_recycle_slot(self, slot: _Slot) -> None:
        """Force-recycle a slot that has been stuck in `respawning` past
        `PTY_POOL_WAIT_TIMEOUT` seconds (POOL-1 hazard).

        Reschedules `_respawn_slot` under the per-slot lock if (and only
        if) the slot is still in `respawning` with its event unset. The
        rescheduled spawn's SUCCESS path sets `slot.event` and notifies
        `_slot_available`, so the caller MUST NOT do either; doing so
        would race against the new spawn task (a busy-spin at the event
        wait plus a spurious PTYPoolError via the non-idle-state guard).
        """
        logger.error(
            "[pty-pool] slot %d stuck in respawning past %ss; force-recycling",
            slot.idx,
            PTY_POOL_WAIT_TIMEOUT,
        )
        try:
            async with slot.lock:
                if slot.state == "respawning" and not slot.event.is_set():
                    # Prune done tasks first (same pattern as _release_pair).
                    self._respawn_tasks = [t for t in self._respawn_tasks if not t.done()]
                    task = asyncio.create_task(self._respawn_slot(slot))
                    self._respawn_tasks.append(task)
                else:
                    logger.debug(
                        "[pty-pool] slot %d no longer stuck; skipping force-recycle",
                        slot.idx,
                    )
        except Exception as e:
            logger.error("[pty-pool] slot %d force-recycle failed: %s", slot.idx, e)

    def _pair_is_live(self, slot: _Slot) -> bool:
        """Whether the slot holds a pair whose PTY children are both alive.

        A driver with NO `_child` is treated as live: production
        `_spawn_slot` always leaves a real pexpect child behind (dead
        or alive), so `_child is None` only occurs when `spawn` was
        patched to a no-op (unit tests) — distrusting it would recycle
        forever. A driver whose real child reports dead is the actual
        failure this check exists for.
        """
        if slot.pty_pair is None:
            return False
        for pty in slot.pty_pair:
            if getattr(pty, "_child", None) is None:
                continue
            try:
                if not pty.isalive():
                    return False
            except Exception:
                return False
        return True

    def _needs_session_spawn(self, spec: PairSpawnSpec | None) -> bool:
        """Whether `spec` requires a fresh per-session spawn.

        Returns True if the spec carries ANY per-session identity: env,
        model, cwd-override, OR session-id. Env vars and the system-prompt
        overlay can only be injected at process spawn; a cwd differing from
        the pool's spawn cwd means the pre-warmed pair is running in the
        wrong directory (the #887 worktree-contamination class); and a spec
        carrying pm/dev session-ids must spawn so the PTYs resume those exact
        sessions (otherwise the transcript path the container computes from
        the spec's session-ids would never match the prewarmed pair's own
        ids). Conservative: any per-session requirement triggers
        spawn-on-acquire.
        """
        if spec is None:
            return False
        return bool(
            spec.env
            or spec.pm_model
            or spec.dev_model
            or (spec.cwd is not None and spec.cwd != self._cwd)
            or spec.pm_session_id
            or spec.dev_session_id
        )

    async def _spawn_session_pair(self, slot: _Slot, spec: PairSpawnSpec) -> None:
        """Replace the slot's pre-warmed pair with a per-session pair.

        Runs at acquire time, with the slot already `locked` and the
        semaphore held — the bounded-slot invariant holds throughout.
        The old pair is closed best-effort and its pids are dropped
        from the orphan registry; the new pair's pids are recorded.
        On spawn failure the exception propagates to `acquire_pair`,
        whose `finally` releases the slot (scheduling a normal pool
        respawn) and the semaphore.
        """
        async with slot.lock:
            old_pair = slot.pty_pair
            if old_pair is not None:
                for pty in old_pair:
                    old_pid = getattr(getattr(pty, "_child", None), "pid", None)
                    try:
                        pty.close(force=True)
                    except Exception:
                        pass
                    if old_pid is not None:
                        self._spawned_pids.discard(old_pid)
                slot.pty_pair = None
            cwd = spec.cwd if spec.cwd is not None else self._cwd
            pm = PTYDriver(
                role="pm",
                cwd=cwd,
                model=spec.pm_model,
                env=spec.env,
                session_id=spec.pm_session_id,
            )
            dev = PTYDriver(
                role="dev",
                cwd=cwd,
                model=spec.dev_model,
                env=spec.env,
                session_id=spec.dev_session_id,
            )
            pm.spawn()
            dev.spawn()
            slot.pty_pair = (pm, dev)
            for pty in (pm, dev):
                pid = getattr(getattr(pty, "_child", None), "pid", None)
                if pid is not None:
                    self._spawned_pids.add(pid)
            self._persist_pids()
        logger.info(
            "[pty-pool] slot %d: spawned per-session pair (cwd=%s, env_keys=%d, pm_model=%s)",
            slot.idx,
            cwd,
            len(spec.env or {}),
            spec.pm_model or "<granite-default>",
        )

    async def _release_pair(self, slot: _Slot) -> None:
        """Close old PTYs, transition slot to `respawning`, schedule
        background respawn. The semaphore is released by the caller
        in `acquire_pair`'s `finally`."""
        slot.state = "respawning"
        slot.event.clear()
        # Close the old pair if any. Best-effort.
        if slot.pty_pair is not None:
            pm, dev = slot.pty_pair
            for pty in (pm, dev):
                try:
                    pty.close(force=True)
                except Exception:
                    pass
            slot.pty_pair = None
        # Schedule respawn in the background. Prune completed tasks
        # first — without the prune, the list grows by one Task per
        # session release for the worker's whole lifetime.
        self._respawn_tasks = [t for t in self._respawn_tasks if not t.done()]
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
        `respawning` with its event unset. The next acquirer that
        waits on this slot past `PTY_POOL_WAIT_TIMEOUT` force-recycles
        it (reschedules this respawn), so the failure is recovered
        automatically while still being logged loudly.
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
            # Wake any acquirer parked in `_wait_for_idle_slot`'s
            # all-locked condition wait (replaces the old sleep-poll).
            async with self._slot_available:
                self._slot_available.notify_all()
        except Exception as e:
            logger.error("[pty-pool] slot %d spawn failed: %s", idx, e)
            # Re-raise so initialize can surface it; for respawn
            # tasks, the exception is captured by the task's own
            # state and the slot stays in `respawning` (event not
            # set). The next bounded acquirer force-recycles the slot
            # after PTY_POOL_WAIT_TIMEOUT.
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
            # The respawn reuses the cwd captured by `initialize()` so
            # recycled pairs spawn in the same directory as pre-warmed
            # ones (not the worker process's cwd).
            await asyncio.shield(self._spawn_slot(slot.idx, cwd=self._cwd))
        except asyncio.CancelledError:
            # Worker shutdown. The shielded section has already
            # completed (or raised) by the time we see this — the
            # lock was released before the shield broke. Slot state
            # is whatever the spawn left it as.
            logger.info("[pty-pool] respawn for slot %d cancelled", slot.idx)
        except Exception as e:
            # Spawn failed. Slot is in `respawning`; event is not
            # set. The next bounded acquirer force-recycles the slot
            # after PTY_POOL_WAIT_TIMEOUT. Log loudly.
            logger.error("[pty-pool] respawn for slot %d failed: %s", slot.idx, e)
            # Do not set the event; the slot remains unavailable
            # until a force-recycle reschedules the respawn.


# -- Module-level singleton + worker hooks (plan #1572) ------------------------


# The pool is a process-local singleton owned by the worker. A new
# process re-initializes from scratch; existing pids come from
# `data/granite_pty_pids.json` so orphan cleanup still works across
# restarts (OPS-3 / Risk 1).
_pty_pool: PTYPool | None = None


def initialize_pty_pool() -> PTYPool:
    """Return (or build) the worker's singleton PTYPool.

    Reads the pool size from `config.settings` (env-overridable as
    GRANITE__PTY_POOL_SIZE). Idempotent: a second call returns the
    existing pool.

    This helper only CONSTRUCTS the pool — it does not pre-warm any
    slots. The caller owns the pre-warm: the worker's async startup
    hook calls `await pool.initialize()` after building the singleton
    (`worker/__main__.py` Step 4c).
    """
    global _pty_pool
    if _pty_pool is not None:
        return _pty_pool
    # Late import: settings pulls pydantic; tests that don't use the
    # pool should not need it.
    from config.settings import settings

    pool_size = settings.granite.pty_pool_size
    _pty_pool = PTYPool(pool_size=pool_size)
    return _pty_pool


def get_pty_pool() -> PTYPool:
    """Return the singleton pool, building it on first access. Raises
    `PTYPoolError` if no pool is registered and settings cannot be read."""
    if _pty_pool is None:
        return initialize_pty_pool()
    return _pty_pool


def _kill_orphaned_pty_pids() -> int:
    """Read `data/granite_pty_pids.json` (if any), kill the listed pids,
    and clear the file. Returns the count killed.

    This is the worker startup hook called BEFORE the pool singleton is
    built: a fresh worker process needs to reap any orphans left by a
    previous (now-dead) worker process. After the kill, the pool is
    built and the fresh pool's `_load_persisted_pids` finds an empty
    registry.
    """
    path = Path(DEFAULT_PID_REGISTRY_PATH)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
        pids = {int(p) for p in data.get("pids", [])}
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("[pty-pool] could not read pid registry %s: %s", path, e)
        return 0
    killed = PTYPool.kill_orphans(pids)
    if killed:
        logger.info("[pty-pool] killed %d orphan pids from %s", killed, path)
    # Truncate the registry so the new worker process starts clean.
    try:
        path.write_text(json.dumps({"pids": []}))
    except OSError as e:
        logger.warning("[pty-pool] could not clear pid registry %s: %s", path, e)
    return killed
