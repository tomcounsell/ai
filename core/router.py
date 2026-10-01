"""The router: `python -m core run TASK`.

Each run folds the ledger to the task's state and runs the runner the
composition root registered for it, then folds again, until the task needs
Tom or something the router cannot run. It reads verdicts and follows the
table; it never writes a verdict and never decides authority.

Runners come in as a mapping from `State` or `Check` to an async callable
taking a `Context`, never from a module global, so the judge runner (which
asks the judgement port) and the critique and check runners (1.4) are
entries and nothing here changes. A state, or a branch of `checks` still without a verdict for the
current candidate, that has no runner ends the run with `no runner`, naming
what is missing and writing nothing.

One run per task at a time: a run holds a session-level advisory lock on
`run:<task>` on a connection it keeps for the whole run, and a second run
returns `already running`. A session lock lives as long as its connection,
so the run checks that connection (`SELECT 1`) before each turn and before
each write a runner makes, and returns `lock lost` when it has died. A
transaction-pooling proxy between the kernel and Postgres would break this,
since it does not keep a session; the kernel connects directly.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import broker, db, ledger, machine, tasks, verdicts, workspace
from core.gateway import Gateway
from core.machine import Check, State


@dataclass(frozen=True)
class Context:
    gateway: Gateway
    task_id: str
    dsn: str
    alive: Callable[[], Awaitable[bool]]
    check: Check | None = None


Runner = Callable[[Context], Awaitable[dict[str, Any]]]
SETTLED = {State.WAITING: "waiting", State.MERGED: "merged", State.STOPPED: "stopped"}


async def run(
    gateway: Gateway, task_id: str, runners: Mapping[State | Check, Runner], dsn: str | None = None
) -> dict[str, Any]:
    """Run the task until it needs Tom or a runner that does not exist.
    Returns `status` (`waiting`, `delivered`, `merged`, `stopped`, `no
    runner`, `legacy`, `calibration task`, `already running`, `lock lost`, or what a runner
    returned: `budget exhausted`, `failed`, `idle`) and the task's state."""
    dsn = dsn or gateway.dsn
    holder = await db.connect(dsn)
    try:
        key = f"run:{task_id}"
        got = await (
            await holder.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
        ).fetchone()
        if not got[0]:
            return {"status": "already running"}

        async def alive() -> bool:
            try:
                await holder.execute("SELECT 1")
                return True
            except Exception:  # noqa: BLE001  any failure means the session, and its lock, are gone
                return False

        services = await _Services.open(dsn, task_id)
        await services.sweep()
        try:
            return await _loop(gateway, task_id, runners, dsn, alive, services)
        finally:
            await asyncio.to_thread(services.down)
    finally:
        await holder.close()


class _Services:
    """The task's workspace services (its Postgres, its Redis): started just
    before the first runner of the run, stopped when the run returns. At the
    start of every run, the services of other tasks a killed kernel left up
    are stopped, unless their own run is live (`workspace.sweep`)."""

    def __init__(self, task_id: str, dsn: str, names: list[str], ports: dict[str, int], lay):
        self.task_id, self.dsn, self.names, self.ports, self.lay = task_id, dsn, names, ports, lay
        self.started = False

    @classmethod
    async def open(cls, dsn: str, task_id: str) -> _Services:
        async with await db.connect(dsn) as conn:
            try:
                b = await tasks.brief(conn, task_id)
            except KeyError:
                return cls(task_id, dsn, [], {}, None)
        names, ports = workspace.services_of(b)
        lay = workspace.Layout(Path(b.mirror).parent) if b.mirror else None
        return cls(task_id, dsn, names if lay else [], ports, lay)

    async def sweep(self) -> None:
        """At the start of every run: stop what a killed kernel left up."""
        async with await db.connect(self.dsn) as conn:
            reaped = await workspace.sweep(conn, self.task_id)
            if reaped:
                async with conn.transaction():
                    await ledger.append(conn, self.task_id, "services.reaped", {"processes": reaped})

    async def up(self) -> str | None:
        """Start them if they are not up; why not, or None."""
        if self.started or not self.names:
            return None
        try:
            await asyncio.to_thread(workspace.start_services, self.task_id, self.lay, self.names, self.ports)
        except workspace.Refused as exc:
            return str(exc)
        self.started = True
        return None

    def down(self) -> None:
        if self.started:
            workspace.stop_services(self.task_id, self.lay)
            self.started = False


async def _loop(gateway, task_id, runners, dsn, alive, services: _Services) -> dict[str, Any]:
    while True:
        if not await alive():
            return {"status": "lock lost"}
        async with await db.connect(dsn) as conn:
            f = machine.fold(await ledger.read(conn, task_id))
            if f.legacy:
                return {"status": "legacy", "state": await tasks.status(conn, task_id)}
            if f.calibration:
                return {"status": "calibration task", "state": await tasks.status(conn, task_id)}
            if f.state in SETTLED:
                return {"status": SETTLED[f.state], "state": await tasks.status(conn, task_id)}
            if f.state is State.MERGE:
                # A release that died mid-merge: settle it from the target, then fold again.
                dangling = f.merge_effect and f.merge_effect["state"] == "in_flight"
                if dangling and await broker.reconcile(conn, f.merge_effect["effect_id"]) is not None:
                    continue
                await verdicts.ensure_merge(conn, task_id)
                return {"status": "delivered", "state": await tasks.status(conn, task_id)}
        ctx = Context(gateway, task_id, dsn, alive)
        if runners.get(f.state) is not None or (
            f.state is State.CHECKS and any(c in runners for c in Check if c not in f.checks)
        ):
            why = await services.up()
            if why:
                async with await db.connect(dsn) as conn:
                    state = await tasks.status(conn, task_id)
                return {"status": "failed", "state": state, "turn": {"result": why}}
        if f.state is State.CHECKS:
            missing = [c for c in Check if c not in f.checks]
            ran = False
            for check in missing:
                if check in runners:
                    out = await runners[check](Context(gateway, task_id, dsn, alive, check))
                    if out.get("status") != "moved":
                        return out
                    ran = True
                    break
            if ran:
                continue
            async with await db.connect(dsn) as conn:
                return {
                    "status": "no runner",
                    "missing": [c.value for c in missing],
                    "state": await tasks.status(conn, task_id),
                }
        runner = runners.get(f.state)
        if runner is None:
            async with await db.connect(dsn) as conn:
                return {
                    "status": "no runner",
                    "missing": [f.state.value],
                    "state": await tasks.status(conn, task_id),
                }
        out = await runner(ctx)
        if out.get("status") != "moved":
            return out
