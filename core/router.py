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

`step` is one runner's run under that lock: the resident kernel
(`core/serve.py`) calls it once per event, and `run` calls it until the
task settles. Each step builds the task's performers from its Brief, with
the factory the composition root passes in (a task started by message is
provisioned after it starts, so its Brief grows); every runner gets them in
its `Context`, and the merge and its reconcile use them. A step whose
runner returns `failed`, or raises, writes `step.failed` before it returns
or the error goes on, so the task's record says why. The resident kernel
steps it again itself once `retry_due` says so, and the next turn opens
with the reason (`session.next_prompt`); at `STEP_TRIES` failures in a row
of the same state and check, `task.ended` is written with the last one and
Tom gets a report of what failed (`notices.owe`), except for docs, which
decide `no_change` with the reason as a finding and never hold the merge.

A task's services (its Postgres, its Redis) are held under the session lock
`services:<task>` while up, on the services handle's own connection. `run`
returns `already running` when another process holds either lock, so it
never stops services the kernel keeps between steps.
"""

import asyncio
import sys
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core import broker, db, git, ledger, machine, session, spending, tasks, verdicts, workspace
from core.gateway import Gateway
from core.machine import Check, State


@dataclass(frozen=True)
class Context:
    gateway: Gateway
    task_id: str
    dsn: str
    alive: Callable[[], Awaitable[bool]]
    check: Check | None = None
    performers: broker.Performers = field(default_factory=broker.Performers)


Runner = Callable[[Context], Awaitable[dict[str, Any]]]
SETTLED = {State.WAITING: "waiting", State.MERGED: "merged", State.ENDED: "ended", State.STOPPED: "stopped"}
# A failed step is tried again by the kernel itself (`retry_due`), after a
# delay that doubles from `STEP_BACKOFF_S`; at `STEP_TRIES` failures in a row
# of the same state and check, the task ends with a report to Tom.
STEP_TRIES = 4
STEP_BACKOFF_S = 60.0
STEP_BACKOFF_MAX_S = 900.0
PerformersFactory = Callable[[tasks.Brief], broker.Performers]


async def run(
    gateway: Gateway,
    task_id: str,
    runners: Mapping[State | Check, Runner],
    dsn: str | None = None,
    performers: PerformersFactory | None = None,
) -> dict[str, Any]:
    """Run the task until it settles, or a runner that does not exist.
    `performers` builds the task's own from its Brief (the composition
    root's factory); without it the task has none.
    Returns `status` (`waiting`, `delivered`, `merged`, `ended`, `stopped`, `no
    runner`, `legacy`, `calibration task`, `already running`, `lock lost`,
    `parked` (a merge whose facts git could not read, whose push has no
    answer yet, or whose try again is not due), or what a runner returned:
    `failed`) and the task's state."""
    dsn = dsn or gateway.dsn
    services = await _Services.open(dsn, task_id)
    try:
        if not await services.claim():
            return {"status": "already running"}
        first = True
        while True:
            out = await step(gateway, task_id, runners, dsn, performers, services, sweep=first)
            first = False
            # One task, no other work to give the slot to: an idle turn's
            # step is followed by the next.
            if out.get("status") not in ("moved", "idle"):
                return out
    finally:
        await asyncio.to_thread(services.down)
        await services.close()


async def step(
    gateway: Gateway,
    task_id: str,
    runners: Mapping[State | Check, Runner],
    dsn: str | None = None,
    performers: PerformersFactory | None = None,
    services: _Services | None = None,
    *,
    sweep: bool = True,
) -> dict[str, Any]:
    """One step of the task under `run:<task>`: fold, run the state's
    runner once, return. `moved` means it moved and another step follows;
    any other status is what `run` returns. A task whose run is held
    elsewhere returns `already running`. `services` is the task's handle,
    kept by the caller across steps; without one the step opens its own
    and stops them when it returns."""
    dsn = dsn or gateway.dsn
    holder = await db.connect(dsn)
    own = services is None
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

        if own:
            services = await _Services.open(dsn, task_id)
            if not await services.claim():
                return {"status": "already running"}
        if sweep:
            await services.sweep()
        built = broker.Performers()
        if performers is not None:
            async with await db.connect(dsn) as conn:
                built = performers(await tasks.brief(conn, task_id))
        held = spending.HOLDER.set(key)
        ran: dict[str, Any] = {}
        try:
            try:
                out = await _once(gateway, task_id, runners, dsn, alive, services, built, ran)
            except Exception as exc:
                reason = "".join(traceback.format_exception_only(exc)).strip()
                try:
                    await _failed(dsn, task_id, alive, ran, reason, None)
                except Exception as written:  # noqa: BLE001  the step's own failure is the one raised
                    _log(f"task {task_id}: step.failed not written: {written!r}")
                raise
            if out.get("status") != "failed":
                return out
            turn = out.get("turn") or {}
            got = await _failed(dsn, task_id, alive, ran, _reason(turn), turn.get("turn_id"))
            if got is not None:
                return {"status": got} if got == "lock lost" else out
            async with await db.connect(dsn) as conn:
                return {**out, "state": await tasks.status(conn, task_id)}
        finally:
            spending.HOLDER.reset(held)
            if own:
                await asyncio.to_thread(services.down)
                await services.close()
    finally:
        await holder.close()


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _reason(turn: dict[str, Any]) -> str:
    """A failed step's reason as text: the runner's own, or, for a turn that
    did not finish, its outcome and what the harness said."""
    result = turn.get("result")
    if isinstance(result, str):
        return result
    said = (result or {}).get("text") or (result or {}).get("error")
    return f"the turn ended {turn.get('outcome')}" + (f": {said}" if said else "")


async def _failed(
    dsn: str, task_id: str, alive, ran: dict[str, Any], reason: str, turn_id: str | None
) -> str | None:
    """Write `step.failed`, as a runner writes its rows: only while the run
    holds its lock, and never beside a stop. Returns `lock lost` or
    `stopped` when it wrote nothing, else None."""
    if not await alive():
        return "lock lost"
    payload = {"state": ran.get("state"), "check": ran.get("check"), "reason": reason, "turn_id": turn_id}
    async with await db.connect(dsn) as conn, conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        if await tasks.is_stopped(conn, task_id):
            return "stopped"
        why = await ledger.unstorable(conn, payload)
        if why is not None:
            payload["reason"] = f"the reason: {ledger.UNSTORABLE}: {why}"
        await ledger.append(conn, task_id, "step.failed", payload)
        rows = await ledger.read(conn, task_id)
        tries = failures(rows)
        f = machine.fold(rows)
        if tries >= STEP_TRIES and payload["check"] == Check.DOCS.value and f.state is State.CHECKS:
            # Docs never hold the merge: a docs runner that keeps failing
            # decides `no_change`, its reason a finding on the delivery.
            await verdicts.record_check(
                conn, task_id, Check.DOCS, "no_change", leg="kernel",
                findings=[{"kind": "docs failed", "text": payload["reason"]}],
            )  # fmt: skip
        elif tries >= STEP_TRIES and not (f.legacy or f.calibration) and f.state not in SETTLED:
            await ledger.append(
                conn,
                task_id,
                "task.ended",
                {
                    "kind": "step",
                    "by": "kernel",
                    "tries": tries,
                    **{k: payload[k] for k in ("state", "check", "reason")},
                },
            )
    return None


def failures(rows: list[dict[str, Any]]) -> int:
    """How many times in a row the task's latest step failed in the same
    state and check: the `step.failed` rows since the last row that shows
    the work moving (`PROGRESS`, or a collected turn that did not fail)."""
    last = None
    count = 0
    for r in reversed(rows):
        kind, p = r["type"], r["payload"]
        if kind == "step.failed":
            key = (p.get("state"), p.get("check"))
            if last is not None and key != last:
                break
            last = key
            count += 1
        elif kind in PROGRESS or (kind == "turn.collected" and p.get("verdict") != "failed"):
            break
    return count


# Rows that show a task's work moved, after which a failure counts from one.
PROGRESS = frozenset(
    {
        "task.started",
        "judge.decided",
        "critique.decided",
        "test.decided",
        "review.decided",
        "docs.decided",
        "plan.written",
        "question.asked",
        "question.answered",
        "feedback.given",
        "message.steered",
        "task.delivered",
        "rebase.requested",
        "verdicts.carried",
        "workspace.provisioned",
        "task.ended",
    }
)


def retry_due(rows: list[dict[str, Any]], now: datetime | None = None) -> bool:
    """Whether the kernel should step a task again whose latest step failed:
    its latest row (but for quiet rows, `ledger.quiet`) is `step.failed`, and
    the delay after it, doubling from `STEP_BACKOFF_S` with each failure in
    a row, has passed. The task ends at `STEP_TRIES` failures (`_failed`)."""
    last = next((r for r in reversed(rows) if not ledger.quiet(r)), None)
    if last is None or last["type"] != "step.failed":
        return False
    tries = failures(rows)
    delay = min(STEP_BACKOFF_MAX_S, STEP_BACKOFF_S * 2 ** max(tries - 1, 0))
    at = last.get("at")
    now = now or datetime.now(UTC)
    return at is None or now >= at + timedelta(seconds=delay)


def has_step(rows: list[dict[str, Any]], f: machine.Fold, chat: bool | dict[str, str] | None) -> bool:
    """Whether an open task has a step to run on its new rows: not waiting
    on an answer, and for a chat session (`chat`, its Brief's chat), a
    message or report to answer (`session.chat_waiting`), a wake it asked
    for due (`session.wake_due`), or a failed step due again. A Meet call's
    session never has one: its call's own process is Valor there
    (`core/call.py`). The kernel schedules by it, and the queue line counts
    only these as ahead."""
    if f.state is State.WAITING or (isinstance(chat, dict) and chat.get("channel") == "meet"):
        return False
    return not chat or session.chat_waiting(rows) or session.wake_due(rows) or retry_due(rows)


async def queue_line(conn, task_id: str) -> str:
    """What a writer sees after starting an ask: how many tasks are open on
    its project, how many open tasks with a step to run (`has_step`)
    are ahead of it in the one queue the kernel runs, and which on the
    project wait on an answer."""
    found = await tasks.open_ids(conn)
    ids = [t for t, _, _ in found]
    positions = tasks.queue(ids, await tasks.moves(conn, ids))
    if task_id not in positions:
        return f"queue: task {task_id} is not open"
    project = tasks.project_name(await tasks.brief(conn, task_id))
    same = [(t, f) for t, _, f in found if tasks.project_name(await tasks.brief(conn, t)) == project]
    waiting = [t for t, f in same if f.state is State.WAITING]
    ahead = 0
    for t, rows, f in found:
        if positions[t] < positions[task_id] and has_step(rows, f, (await tasks.brief(conn, t)).chat):
            ahead += 1
    line = f"queue: {len(same)} open on {project}, {ahead} ahead of {task_id} in the kernel's queue"
    return line + (f"; waiting on an answer: {', '.join(waiting)}" if waiting else "")


class _Services:
    """The task's workspace services (its Postgres, its Redis): started just
    before the first runner that needs them, stopped when the caller says
    (`run`: when it returns; the kernel: when the task settles or the
    kernel exits). The handle holds `services:<task>` on a connection of its
    own from `claim` to `close`, so no sweep stops them meanwhile. At the
    start of every run, the services of other tasks a killed kernel left up
    are stopped, unless their own run is live or a kernel keeps them
    (`workspace.sweep`). Starting
    them has no time limit: a stop of the task, or a cancel of the run,
    interrupts it (`git.interruptible()`), and whatever did start is
    stopped with the run."""

    def __init__(self, task_id: str, dsn: str, names: list[str], ports: dict[str, int], lay):
        self.task_id, self.dsn, self.names, self.ports, self.lay = task_id, dsn, names, ports, lay
        self.started = False
        self.conn = None

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

    async def claim(self) -> bool:
        """Take `services:<task>`; False when another process holds it."""
        if self.conn is not None:
            return True
        conn = await db.connect(self.dsn)
        got = await (
            await conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"services:{self.task_id}",)
            )
        ).fetchone()
        if not got[0]:
            await conn.close()
            return False
        self.conn = conn
        return True

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def refresh(self) -> None:
        """Read the task's services again while the handle knows no
        workspace (a task started by message is provisioned after its handle
        was opened)."""
        if self.started or self.lay is not None:
            return
        fresh = await _Services.open(self.dsn, self.task_id)
        self.names, self.ports, self.lay = fresh.names, fresh.ports, fresh.lay

    async def sweep(self) -> None:
        """At the start of every run: stop what a killed kernel left up."""
        async with await db.connect(self.dsn) as conn:
            reaped = await workspace.sweep(conn, self.task_id, self.lay.root.parent if self.lay else None)
            if reaped:
                async with conn.transaction():
                    await ledger.append(conn, self.task_id, "services.reaped", {"processes": reaped})

    async def up(self) -> str | None:
        """Start them if they are not up; why not, or None."""
        await self.refresh()
        if self.started or not self.names:
            return None
        self.started = True  # from the first program on, `down` stops what started
        with git.interruptible() as held:
            job = asyncio.ensure_future(asyncio.to_thread(self._start))
            stopped = asyncio.ensure_future(self._stopped())
            try:
                await asyncio.wait({job, stopped}, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                await self._interrupt(held, job)
                job.cancelled() or job.exception()  # the interrupt, not the thread's error, is raised
                raise
            finally:
                stopped.cancel()
            if not job.done():
                await self._interrupt(held, job)
                if not stopped.cancelled() and stopped.exception() is not None:
                    job.cancelled() or job.exception()
                    raise stopped.exception()  # the listener failed; the task was not stopped
            try:
                await job
            except workspace.Refused as exc:
                return str(exc)
            except git.Interrupted:
                return "the task was stopped while its services started"
        return None

    def _start(self) -> None:
        """Reap whatever is under the task's service mark and remove stale
        `checks/*-svc/` directories first: a check's Redis that a killed
        kernel left on the task's port would otherwise pass for the task's
        own."""
        workspace.stop_services(self.task_id, self.lay)
        workspace.remove_check_services(self.lay)
        workspace.start_services(self.task_id, self.lay, self.names, self.ports)

    async def _stopped(self) -> None:
        """Returns once the task is stopped."""
        async with await db.connect(self.dsn) as listener:
            await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
            if await tasks.is_stopped(listener, self.task_id):
                return
            async for note in listener.notifies():
                if note.payload == self.task_id:
                    return

    @staticmethod
    async def _interrupt(held: git.Interruptible, job: asyncio.Future) -> None:
        """End the start and wait for its thread, whatever else is cancelled."""
        await git.interrupt(held, job)

    def down(self) -> None:
        if self.started:
            workspace.stop_services(self.task_id, self.lay)
            self.started = False


async def _once(
    gateway,
    task_id,
    runners,
    dsn,
    alive,
    services: _Services,
    performers: broker.Performers,
    ran: dict[str, Any],
) -> dict[str, Any]:
    """`ran` gets the state stepped and the check run, for `step.failed`."""
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
            # A merge that died mid-push: settle it from the target, then fold again.
            dangling = f.merge_effect and f.merge_effect["state"] == "in_flight"
            if dangling and await broker.reconcile(conn, performers, f.merge_effect["effect_id"]) is not None:
                return {"status": "moved"}
            asked = await verdicts.ensure_merge(conn, performers, task_id)
            if asked is not None and asked.kind in ("done", "failed", "refused"):
                # Merged: the next step settles it. Did not land: the next
                # step acts on why (`verdicts.ensure_merge`).
                return {"status": "moved"}
            if machine.fold(await ledger.read(conn, task_id)).state is not State.MERGE:
                return {"status": "moved"}  # sent back to rebase, or ended
            if dangling or (asked is not None and asked.kind == "unknown"):
                # Git could not say, the merge is in flight with no answer
                # yet, or a try again is not due: asked again on the next
                # row or tick.
                return {"status": "parked", "state": await tasks.status(conn, task_id)}
            return {"status": "delivered", "state": await tasks.status(conn, task_id)}
    ran["state"] = f.state.value
    ctx = Context(gateway, task_id, dsn, alive, performers=performers)
    if runners.get(f.state) is not None or (
        f.state is State.CHECKS and any(c in runners for c in Check if c not in f.checks)
    ):
        why = await services.up()
        if why:
            async with await db.connect(dsn) as conn:
                if await tasks.is_stopped(conn, task_id):
                    return {"status": "moved"}  # the fold settles it as stopped
                state = await tasks.status(conn, task_id)
            return {"status": "failed", "state": state, "turn": {"result": why}}
    if f.state is State.CHECKS:
        missing = [c for c in Check if c not in f.checks]
        for check in missing:
            if check in runners:
                ran["check"] = check.value
                return await runners[check](Context(gateway, task_id, dsn, alive, check, performers))
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
    return await runner(ctx)
