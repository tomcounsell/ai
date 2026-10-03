"""The resident kernel: `python -m core serve`.

It runs until killed, under launchd. It holds the session lock
`kernel:<settings.machine>` for its life, so a second `serve` waits, and a
restart after a kill waits until Postgres ends the dead session.

On start it recovers (`recover`) what a killed kernel left:

- a turn with no `turn.ended` whose run is not live: its processes are
  reaped, and it ends `interrupted`;
- a call opened with no charge whose holder lock is free: charged at its
  estimate, marked estimated;
- the last working turn of a task that ended and was never collected: its
  signals are read again (`signals.recollect`) and recorded; the
  broker's `request_id` makes a re-request return the first effect;
- an intent of a kernel type with no outcome: reconciled;
- services a killed kernel left up: swept.

Then it starts the gateway, listens on `valor_events`, and on each wake (a
row, or `settings.serve_tick_s` with none) binds every recorded message
(`intake.bind`), requests the notices each task owes (`notices.owe`), and
schedules jobs (`schedule`). A job is one step of one task
(`router.step`), a release Tom asked for, or a provisioning; one job per
task at a time, and one harness job at a time in this process. A turn also
holds the machine's turn slot (`core/slot.py`), which `python -m core run`
shares.

A task is stepped again when it moved, or when a row it did not write
lands on its stream (an answer, a steer, a stop, a release's outcome); a
step that ends anywhere else waits for such a row, so a failing turn is
not retried in a loop.
"""

import asyncio
import json
import os
import plistlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core import (
    broker,
    db,
    intake,
    ledger,
    machine,
    notices,
    router,
    runs,
    session,
    signals,
    spending,
    tasks,
    workspace,
)
from core.gateway import Gateway
from core.machine import State
from core.settings import settings

HARNESS = {State.CLARIFY, State.PLAN, State.CRITIQUE, State.BUILD, State.CHECKS, State.PATCH}
AT_ONCE = {State.JUDGE, State.MERGE}
# Rows the kernel writes beside a task without anything having happened to it.
QUIET = ("notice.requested", "notice.sent")
LABEL = "com.valor.kernel"
ROOT = Path(__file__).resolve().parent.parent


def kernel_key() -> str:
    return f"kernel:{settings.machine}"


async def _try(conn, key: str) -> bool:
    got = await (
        await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
    ).fetchone()
    return bool(got[0])


async def _unlock(conn, key: str) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


async def recover(
    conn,
    performers: router.PerformersFactory | None = None,
    kernel_types: tuple[str, ...] = ("push_branch", "merge"),
) -> dict[str, list]:
    """Settle what a killed kernel left. Returns what was done, by kind."""
    done: dict[str, list] = {
        "charged": [],
        "interrupted": [],
        "recollected": [],
        "reconciled": [],
        "swept": [],
    }

    # Calls opened with no charge, whose holder is gone: the worst case.
    for task_id, call in await (
        await conn.execute(
            "SELECT o.task_id, o.payload FROM events o WHERE o.type IN ('gateway.opened', %s) "
            "AND NOT EXISTS (SELECT 1 FROM events c WHERE c.type = 'gateway.charged' "
            "AND c.payload->>'call_id' = o.payload->>'call_id') ORDER BY o.id",
            (tasks.LEGACY_OPENED,),
        )
    ).fetchall():
        holder = call.get("holder")
        if not holder or not await _try(conn, holder):
            continue
        try:
            await spending.charge(
                conn,
                task_id,
                call["call_id"],
                int(call.get("estimate_usd_micros") or 0),
                {"turn_id": call.get("turn_id"), "estimated": True, "reason": "kernel restarted"},
            )
            done["charged"].append(call["call_id"])
        finally:
            await _unlock(conn, holder)

    # Turns with no end whose run is not live.
    for task_id, turn_id in await (
        await conn.execute(
            "SELECT s.task_id, s.payload->>'turn_id' FROM events s WHERE s.type = 'turn.started' "
            "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.task_id = s.task_id AND e.type = 'turn.ended' "
            "AND e.payload->>'turn_id' = s.payload->>'turn_id') ORDER BY s.id"
        )
    ).fetchall():
        key = f"run:{task_id}"
        if not await _try(conn, key):
            continue
        try:
            reaped = await asyncio.to_thread(runs.reap, turn_id)
            reaped += await asyncio.to_thread(runs.reap_sandboxed, f"valor.turn.{turn_id}", "valor.turn.none")
            metered = await (
                await conn.execute(
                    "SELECT COALESCE(sum((payload->>'usd_micros')::bigint), 0) FROM events "
                    "WHERE task_id = %s AND type = 'gateway.charged' AND payload->>'turn_id' = %s",
                    (task_id, turn_id),
                )
            ).fetchone()
            async with conn.transaction():
                if reaped:
                    await ledger.append(
                        conn, task_id, "turn.reaped", {"turn_id": turn_id, "processes": reaped}
                    )
                await ledger.append(
                    conn,
                    task_id,
                    "turn.ended",
                    {
                        "turn_id": turn_id,
                        "outcome": "interrupted",
                        "returncode": None,
                        "result": {},
                        "reason": "kernel restarted",
                        "metered_usd_micros": int(metered[0]),
                    },
                )
            done["interrupted"].append(turn_id)
        finally:
            await _unlock(conn, key)

    # The last working turn of a task, ended and never collected.
    for task_id, ended, started in await (
        await conn.execute(
            "SELECT DISTINCT ON (e.task_id) e.task_id, e.payload, s.payload FROM events e "
            "JOIN events s ON s.task_id = e.task_id AND s.type = 'turn.started' "
            "AND s.payload->>'turn_id' = e.payload->>'turn_id' "
            "WHERE e.type = 'turn.ended' AND s.payload->>'state' IS NOT NULL "
            "AND COALESCE((s.payload->>'fresh')::boolean, false) = false "
            "ORDER BY e.task_id, e.id DESC"
        )
    ).fetchall():
        turn_id = ended["turn_id"]
        collected = await (
            await conn.execute(
                "SELECT 1 FROM events WHERE task_id = %s AND type = 'turn.collected' AND payload->>'turn_id' = %s",
                (task_id, turn_id),
            )
        ).fetchone()
        if collected is not None:
            continue
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy or f.calibration or f.state is State.MERGED:
            continue
        key = f"run:{task_id}"
        if not await _try(conn, key):
            continue
        try:
            b = await tasks.brief(conn, task_id)
            found = signals.recollect(b.workspace, turn_id) if b.workspace else signals.Signals()
            chosen = broker.CURRENT.set(performers(b) if performers else None)
            try:
                await session.record(
                    conn,
                    task_id,
                    turn_id,
                    found,
                    state=State(started["state"]),
                    workspace=b.workspace,
                    finished=ended.get("outcome") == "done"
                    and not (ended.get("result") or {}).get("is_error"),
                    brief=b,
                )
            finally:
                broker.CURRENT.reset(chosen)
            done["recollected"].append(turn_id)
        finally:
            await _unlock(conn, key)

    # Intents of a kernel type with no outcome.
    for effect_id in await broker.dangling(conn, list(kernel_types)):
        row = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'effect.intent' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
        built = performers(await tasks.brief(conn, row[0])) if performers else None
        outcome = await broker.reconcile(conn, effect_id, performers=built)
        if outcome is not None:
            done["reconciled"].append(effect_id)

    # Services a killed kernel left up.
    reaped = await workspace.sweep(conn, "", workspace.work_dir())
    by_task: dict[str, list] = {}
    for r in reaped:
        by_task.setdefault(r["task"], []).append(r)
    for task_id, processes in by_task.items():
        if not processes[0].get("orphan"):
            async with conn.transaction():
                await ledger.append(conn, task_id, "services.reaped", {"processes": processes})
    done["swept"] = reaped
    return done


class Kernel:
    """The jobs this process runs, and what each task last saw."""

    def __init__(
        self,
        gateway: Gateway,
        runners: Mapping[State | machine.Check, router.Runner],
        performers: router.PerformersFactory | None,
        dsn: str,
    ):
        self.gateway, self.runners, self.performers, self.dsn = gateway, runners, performers, dsn
        self.jobs: dict[str, asyncio.Task] = {}
        self.harness: str | None = None  # the task whose harness job runs
        self.services: dict[str, router._Services] = {}
        self.swept: set[str] = set()
        self.seen: dict[str, int] = {}  # the latest row a step of the task has answered
        self.wake = asyncio.Event()

    async def tick(self, conn) -> None:
        """One wake: bind, owe, schedule."""
        await intake.bind(conn)
        for task_id in await self.active(conn):
            await notices.owe(conn, task_id)
        await self.schedule(conn)

    async def active(self, conn) -> list[str]:
        rows = await (
            await conn.execute(
                "SELECT t.task_id FROM events t WHERE t.type = 'task.started' AND NOT EXISTS ("
                "SELECT 1 FROM events s WHERE s.task_id = t.task_id AND s.type = 'task.stopped') "
                "ORDER BY t.id"
            )
        ).fetchall()
        return [r[0] for r in rows]

    async def schedule(self, conn) -> None:
        ready: list[tuple[int, str]] = []
        for task_id in await self.active(conn):
            if task_id in self.jobs:
                continue
            rows = await ledger.read(conn, task_id)
            f = machine.fold(rows)
            if f.legacy or f.calibration or f.state in (State.MERGED, State.STOPPED):
                await self.settle(task_id)
                continue
            released = await self._kernel_release(conn, task_id)
            if released is not None:
                self._start(task_id, self._release(task_id, released))
                continue
            b = await tasks.brief(conn, task_id)
            if b.project and not b.workspace:
                if _provision_due(rows):
                    self._start(task_id, self._provision(task_id, b.project.get("name")))
                continue
            latest = max((r["id"] for r in rows if r["type"] not in QUIET), default=0)
            if latest <= self.seen.get(task_id, 0) or f.state is State.WAITING:
                continue
            if f.state in AT_ONCE:
                self._start(task_id, self._step(task_id, latest))
            elif f.state in HARNESS:
                ready.append((latest, task_id))
        if self.harness is None and ready:
            latest, task_id = min(ready)
            self.harness = task_id
            self._start(task_id, self._step(task_id, latest))

    async def _kernel_release(self, conn, task_id: str) -> str | None:
        row = await (
            await conn.execute(
                "SELECT r.payload->>'effect_id' FROM events r WHERE r.task_id = %s AND r.type = 'release.requested' "
                "AND r.payload->>'owner' = 'kernel' AND NOT EXISTS (SELECT 1 FROM events e "
                "WHERE e.type IN ('effect.intent', 'effect.outcome', 'effect.refused') "
                "AND e.payload->>'effect_id' = r.payload->>'effect_id') ORDER BY r.id LIMIT 1",
                (task_id,),
            )
        ).fetchone()
        return None if row is None else row[0]

    def _start(self, task_id: str, coro) -> None:
        job = asyncio.create_task(coro)
        self.jobs[task_id] = job

        def finished(_):
            self.jobs.pop(task_id, None)
            if self.harness == task_id:
                self.harness = None
            self.wake.set()

        job.add_done_callback(finished)

    async def _release(self, task_id: str, effect_id: str) -> None:
        async with await db.connect(self.dsn) as conn:
            built = self.performers(await tasks.brief(conn, task_id)) if self.performers else None
            try:
                await broker.release(conn, effect_id, performers=built)
            except (broker.Refused, broker.NotApproved, tasks.TaskStopped) as exc:
                print(f"release {effect_id} of task {task_id} refused: {exc}", file=sys.stderr, flush=True)

    async def _provision(self, task_id: str, name: str | None) -> None:
        """Provision a task started by message, off the loop. A failure is a
        `workspace.failed` row and a notice."""
        async with await db.connect(self.dsn) as conn:
            lock = f"provision:{task_id}"
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
            try:
                try:
                    spec = workspace.Spec.load(name or "")
                    await conn.execute("SELECT pg_advisory_lock(hashtextextended('workspace:ports', 0))")
                    try:
                        taken = await workspace.taken_ports(conn)
                        ports: dict[str, int] = {}
                        if "postgres" in spec.services:
                            ports["postgres"] = workspace.choose_port(settings.pg_ports, taken)
                        if "redis" in spec.services:
                            ports["redis"] = workspace.choose_port(settings.redis_ports, taken)
                        workspace.reserve(task_id, ports)
                    finally:
                        await _unlock(conn, "workspace:ports")
                    made = await asyncio.to_thread(workspace.provision, task_id, spec, ports)
                except Exception as exc:  # noqa: BLE001  any failure is the task's to report, not the kernel's
                    async with conn.transaction():
                        await ledger.lock(conn, f"task:{task_id}")
                        row = await ledger.append(conn, task_id, "workspace.failed", {"reason": str(exc)})
                        await notices.request(
                            conn,
                            task_id,
                            kind="workspace_failed",
                            about_key=f"workspace-failed:{row}",
                            text=f"Task {task_id}'s workspace could not be made: {exc}\n\n"
                            "Reply to steer it and it is tried again, or reply `stop`.",
                        )
                    return
                async with conn.transaction():
                    await ledger.append(
                        conn, task_id, "workspace.provisioned", {"fields": made.brief_fields()}
                    )
            finally:
                await _unlock(conn, lock)

    async def _step(self, task_id: str, latest: int) -> None:
        services = self.services.get(task_id)
        if services is None:
            services = await router._Services.open(self.dsn, task_id)
            if not await services.claim():
                await services.close()
                return
            self.services[task_id] = services
        first = task_id not in self.swept
        self.swept.add(task_id)
        try:
            out = await router.step(
                self.gateway, task_id, self.runners, self.dsn, self.performers, services, sweep=first
            )
        except Exception as exc:  # noqa: BLE001  a step's failure is the task's; the kernel goes on
            print(f"task {task_id}: step failed: {exc!r}", file=sys.stderr, flush=True)
            out = {"status": "failed"}
        status = out.get("status")
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, task_id)
        if status != "moved":
            # Wait for a row this step did not write.
            self.seen[task_id] = max((r["id"] for r in rows if r["type"] not in QUIET), default=latest)
        else:
            self.seen[task_id] = latest
        state = machine.fold(rows).state
        if state in router.SETTLED or state is State.MERGE:
            await self.settle(task_id)

    async def settle(self, task_id: str) -> None:
        """The task needs Tom or is done: its services stop."""
        services = self.services.pop(task_id, None)
        if services is not None:
            await asyncio.to_thread(services.down)
            await services.close()

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for task_id in list(self.services):
            await self.settle(task_id)


def _provision_due(rows: list[dict[str, Any]]) -> bool:
    """No provisioning failed, or Tom steered the task since the last did."""
    failed = max((r["id"] for r in rows if r["type"] == "workspace.failed"), default=None)
    if failed is None:
        return True
    return any(r["type"] == "message.steered" and r["id"] > failed for r in rows)


async def _listen(listener, wake: asyncio.Event) -> None:
    async for _ in listener.notifies():
        wake.set()


async def serve(
    runners: Mapping[State | machine.Check, router.Runner],
    performers: router.PerformersFactory | None = None,
    *,
    dsn: str | None = None,
    gateway: Gateway | None = None,
) -> None:
    """Run the kernel until killed."""
    dsn = dsn or settings.dsn()
    conn = await db.connect(dsn, application_name="valor-kernel")
    listener = await db.connect(dsn, application_name="valor-kernel-listen")
    owned_gateway = gateway is None
    kernel = None
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (kernel_key(),))
        done = await recover(conn, performers)
        print(f"kernel recovered: {json.dumps({k: len(v) for k, v in done.items()})}", flush=True)
        if gateway is None:
            from core.gateway import ClaudeLogin

            gateway = Gateway(dsn, credential=ClaudeLogin())
            await gateway.start()
        kernel = Kernel(gateway, runners, performers, dsn)
        await listener.execute("LISTEN valor_events")
        listening = asyncio.create_task(_listen(listener, kernel.wake))
        try:
            while True:
                kernel.wake.clear()
                await kernel.tick(conn)
                try:
                    await asyncio.wait_for(kernel.wake.wait(), settings.serve_tick_s)
                except TimeoutError:
                    pass
                if listening.done():
                    # The listening connection died: a new one, and the
                    # tick catches up what was missed.
                    await listener.close()
                    listener = await db.connect(dsn, application_name="valor-kernel-listen")
                    await listener.execute("LISTEN valor_events")
                    listening = asyncio.create_task(_listen(listener, kernel.wake))
        finally:
            listening.cancel()
    finally:
        if kernel is not None:
            await kernel.close()
        if owned_gateway and gateway is not None:
            await gateway.close()
        await listener.close()
        await conn.close()


PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_OWNER",
    "VALOR_PG_BIN",
    "VALOR_PG_PASSFILE",
    "VALOR_CLAUDE",
    "VALOR_GIT",
    "VALOR_WORK",
    "VALOR_PROJECTS",
    "VALOR_LOG_DIR",
    "VALOR_MACHINE",
    "VALOR_DEFAULT_MACHINE",
    "VALOR_OPERATOR_TELEGRAM_ID",
    "VALOR_OPERATOR_EMAIL",
    "VALOR_OPERATOR_CHANNEL",
    "VALOR_OPERATOR_CHAT",
    "VALOR_INBOUND",
    "VALOR_SERVE_TICK_S",
)


def plist(*, python: str | None = None, root: Path = ROOT) -> bytes:
    """The launchd job for the kernel: kept alive, started at load."""
    path = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    for d in (os.path.dirname(settings.claude), os.path.dirname(settings.git_bin), settings.pg_bin):
        if d and d not in path:
            path.append(d)
    env = {"HOME": str(Path.home()), "PATH": ":".join(path)}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / "kernel.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [python or str(root / ".venv" / "bin" / "python"), "-m", "core", "serve"],
            "WorkingDirectory": str(root),
            "EnvironmentVariables": env,
            "KeepAlive": True,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )
