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
  signals are read again (`signals.recollect`, in a worker thread) and recorded; the
  broker's `request_id` makes a re-request return the first effect. A
  collection that raises is logged and the kernel starts; the task is
  collected by a job before it is stepped, parked like any job that fails;
- an intent of a kernel type with no outcome: reconciled;
- services a killed kernel left up: swept;
- a kernel merge with a rollout row and no `rollout.ended` that the
  running commit contains: ended `done` (`core/rollout.py`).

Then it starts the gateway, listens on `valor_events`, and on each wake (a
row, or `settings.serve_tick_s` with none) binds every recorded message
(`intake.bind`), requests the notices each task owes (`notices.owe`),
rolls the kernel forward to its own merges (`roll`), and schedules jobs
(`schedule`). A job is one step of one task
(`router.step`), a provisioning, or the collection recover could not
make; one job per
task at a time, and one harness job at a time in this process. A turn also
holds the machine's turn slot (`core/slot.py`), which `python -m core run`
shares.

A task is stepped again when it moved, or when a row it did not write
lands on its stream (an answer, a steer, a stop, an effect's outcome); a
step that ends anywhere else waits for such a row, so a failing turn is
not retried in a loop. A step that answers `parked` (a merge whose facts
git could not read, or whose push has no answer yet) parks the task.

A job that cannot start its work (another process holds the task's
services, or the job raises before the task's own rows record why) parks
the task: it is tried again on the next row on its stream or the next
`serve_tick_s` wake, whichever comes first, and never in a loop. A wake's
failure in binding, notices, or scheduling is logged per task, and the
kernel goes on.

A merge of the kernel's own code (its outcome names the checkout's push
URL and branch) rolls the running kernel forward. One that changes only
`persona/`, `skills/`, `docs/`, `tests/` or top-level `*.md` is
fast-forwarded at once. Any other first holds `schedule` until every job
but the background turn's has ended, then cancels the background turn,
fast-forwards the checkout, runs the merged code's migrate, writes
`rollout.restarting`, and raises `rollout.Restart`, which leaves `serve`;
launchd starts the kernel again. A failed step writes `rollout.failed` and
a notice, lifts the hold, and is tried again on the next `serve_tick_s`
wake; a change to the dependencies or the schema stops before the checkout
moves and is the lead's.
"""

import asyncio
import functools
import json
import os
import plistlib
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg

from core import (
    broker,
    db,
    git,
    intake,
    ledger,
    machine,
    notices,
    rollout,
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
QUIET = ledger.QUIET
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


LAST_WORKING_ENDED = (
    "SELECT DISTINCT ON (e.task_id) e.task_id, e.payload, s.payload FROM events e "
    "JOIN events s ON s.task_id = e.task_id AND s.type = 'turn.started' "
    "AND s.payload->>'turn_id' = e.payload->>'turn_id' "
    "WHERE e.type = 'turn.ended' AND e.payload->>'outcome' <> 'preempted' "
    "AND s.payload->>'state' IS NOT NULL "
    "AND COALESCE((s.payload->>'fresh')::boolean, false) = false "
    "AND (%(task)s::text IS NULL OR e.task_id = %(task)s) "
    "ORDER BY e.task_id, e.id DESC"
)


async def recollect(conn, task_id: str, performers: router.PerformersFactory | None = None) -> str | None:
    """Collect the task's last working turn if it ended and was never
    collected. Returns the turn's id, or None when there is nothing to
    collect or a live run holds the task."""
    found = await (await conn.execute(LAST_WORKING_ENDED, {"task": task_id})).fetchone()
    if found is None:
        return None
    return await _recollect(conn, task_id, found[1], found[2], performers)


async def _recollect(
    conn, task_id: str, ended: dict[str, Any], started: dict[str, Any], performers
) -> str | None:
    turn_id = ended["turn_id"]
    collected = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'turn.collected' AND payload->>'turn_id' = %s",
            (task_id, turn_id),
        )
    ).fetchone()
    if collected is not None:
        return None
    f = machine.fold(await ledger.read(conn, task_id))
    if f.legacy or f.calibration or f.state is State.MERGED:
        return None
    key = f"run:{task_id}"
    if not await _try(conn, key):
        return None
    try:
        b = await tasks.brief(conn, task_id)
        found = (
            await asyncio.to_thread(signals.recollect, b.workspace, turn_id)
            if b.workspace
            else signals.Signals()
        )
        await session.record(
            conn,
            task_id,
            turn_id,
            found,
            state=State(started["state"]),
            workspace=b.workspace,
            finished=ended.get("outcome") == "done" and not (ended.get("result") or {}).get("is_error"),
            brief=b,
            performers=performers(b) if performers else None,
        )
    finally:
        await _unlock(conn, key)
    return turn_id


async def recover(
    conn,
    performers: router.PerformersFactory | None = None,
    kernel_types: tuple[str, ...] = ("push_branch", "merge"),
    *,
    checkout: Path | None = None,
    running: str | None = None,
) -> dict[str, list]:
    """Settle what a killed kernel left. Returns what was done, by kind.
    With the kernel's `checkout` and the `running` commit, also ends the
    rollouts that commit contains (`rolled`)."""
    done: dict[str, list] = {
        "charged": [],
        "interrupted": [],
        "recollected": [],
        "uncollected": [],
        "reconciled": [],
        "swept": [],
        "rolled": [],
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
            metered = await spending.turn_spent(conn, task_id, turn_id)
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
                        "metered_usd_micros": metered,
                    },
                )
            done["interrupted"].append(turn_id)
        finally:
            await _unlock(conn, key)

    # The last working turn of a task, ended and never collected. One whose
    # collection raises is logged and left for the kernel to try again.
    for task_id, ended, started in await (await conn.execute(LAST_WORKING_ENDED, {"task": None})).fetchall():
        try:
            turn_id = await _recollect(conn, task_id, ended, started, performers)
        except Exception as exc:  # noqa: BLE001  one task's collection fails alone; the kernel starts
            _log(f"task {task_id}: collecting turn {ended['turn_id']} failed: {exc!r}")
            done["uncollected"].append(task_id)
            continue
        if turn_id is not None:
            done["recollected"].append(turn_id)

    # Intents of a kernel type with no outcome.
    for effect_id in await broker.dangling(conn, list(kernel_types)):
        row = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'effect.intent' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
        built = performers(await tasks.brief(conn, row[0])) if performers else broker.Performers()
        outcome = await broker.reconcile(conn, built, effect_id)
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

    if checkout is not None and running is not None:
        try:
            done["rolled"] = await _rolled(conn, checkout, running)
        except git.GitError as exc:
            _log(f"the kernel's checkout cannot be read; rollouts not ended: {exc}")
    return done


async def _rolled(conn, checkout: Path, running: str) -> list[str]:
    """End every kernel merge with a `rollout.restarting` or
    `rollout.failed` row, or covered by a restarting row, that has no
    `rollout.ended` and whose sha the running commit contains: `rolled_by`
    the merge whose restart covered it, or `by: started` when no restart
    names it (rolled by hand, or carried by a later restart)."""
    ended = {
        r[0]
        for r in await (
            await conn.execute("SELECT payload->>'effect_id' FROM events WHERE type = 'rollout.ended'")
        ).fetchall()
    }
    named: dict[str, dict[str, Any]] = {}
    for task_id, type_, p in await (
        await conn.execute(
            "SELECT task_id, type, payload FROM events "
            "WHERE type IN ('rollout.restarting', 'rollout.failed') ORDER BY id"
        )
    ).fetchall():
        if type_ == "rollout.restarting":
            named[p["effect_id"]] = {"task": task_id, "sha": p["sha"], "own": True}
            for c in p.get("covers") or []:
                if not named.get(c["effect_id"], {}).get("own"):
                    named[c["effect_id"]] = {"task": c["task_id"], "sha": c["sha"], "by": p["effect_id"]}
        else:
            named.setdefault(p["effect_id"], {"task": task_id, "sha": p["sha"]})
    open_ = {e: v for e, v in named.items() if e not in ended}
    if not open_:
        return []

    def contained() -> set[str]:
        return {e for e, v in open_.items() if git.is_ancestor(checkout, v["sha"], running)}

    held = await git.threaded(contained)
    rolled = []
    for effect_id, v in open_.items():
        if effect_id not in held:
            continue
        payload: dict[str, Any] = {"effect_id": effect_id, "outcome": "done", "head": running}
        if v.get("by") and (v["by"] in held or v["by"] in ended):
            payload["rolled_by"] = v["by"]
        elif not v.get("own"):
            payload["by"] = "started"
        async with conn.transaction():
            await ledger.append(conn, v["task"], "rollout.ended", payload)
        rolled.append(effect_id)
    return rolled


# The kernel's merge outcomes not yet ended, oldest first.
MERGE_OUTCOMES = (
    "SELECT o.id, o.task_id, o.payload->>'effect_id', o.payload->'result' FROM events o "
    "JOIN events i ON i.type = 'effect.intent' AND i.payload->>'effect_id' = o.payload->>'effect_id' "
    "LEFT JOIN events h ON h.type = 'effect.held' AND h.payload->>'effect_id' = o.payload->>'effect_id' "
    "WHERE o.type = 'effect.outcome' AND o.payload @> '{\"kind\": \"done\"}' "
    "AND COALESCE(i.payload->>'action_type', h.payload->>'action_type') = 'merge' "
    "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.type = 'rollout.ended' "
    "AND e.payload->>'effect_id' = o.payload->>'effect_id') ORDER BY o.id"
)


class Kernel:
    """The jobs this process runs, and what each task last saw."""

    def __init__(
        self,
        gateway: Gateway,
        runners: Mapping[State | machine.Check, router.Runner],
        performers: router.PerformersFactory | None,
        dsn: str,
        uncollected: set[str] | None = None,
        *,
        checkout: Path | None = None,
        started: str | None = None,
        migrate: Callable[[str], None] | None = None,
        credential: str | Path | None = None,
    ):
        self.gateway, self.runners, self.performers, self.dsn = gateway, runners, performers, dsn
        # The checkout this process runs from and its commit then; with no
        # checkout, nothing is rolled.
        self.checkout, self.started, self.credential = checkout, started, credential
        self.migrate = migrate or (functools.partial(rollout.migrate, checkout) if checkout else None)
        self.restart_due: rollout.Merge | None = None  # holds `schedule` until the jobs end
        self.judged: set[str] = set()  # merge effects contained in `started`, or not the kernel's
        self.waiting: set[str] = set()  # failed since the last `serve_tick_s` wake
        self.final: set[str] = set()  # failed at a step that repeats: not tried again by this process
        # Tasks whose ended turn recover could not collect: collected by a
        # job before the task is stepped.
        self.uncollected: set[str] = set(uncollected or ())
        self.jobs: dict[str, asyncio.Task] = {}
        self.harness: str | None = None  # the foreground task whose harness job runs
        self.background: str | None = None  # the background task whose harness job runs
        self.services: dict[str, router._Services] = {}
        self.swept: set[str] = set()
        self.seen: dict[str, int] = {}  # the latest row a step of the task read or wrote
        # Tasks whose job could not start its work, with the latest row then:
        # tried again on a newer row or the next `serve_tick_s` wake.
        self.parked: dict[str, int] = {}
        self.parked_at = time.monotonic()
        self.wake = asyncio.Event()

    async def tick(self, conn) -> None:
        """One wake: bind, owe, roll, schedule. A failure for one task is logged,
        and the others go on."""
        now = time.monotonic()
        if now - self.parked_at >= settings.serve_tick_s:
            self.parked.clear()
            self.waiting.clear()
            self.parked_at = now
        await intake.bind(conn)
        for task_id in await self.active(conn):
            try:
                await notices.owe(conn, task_id)
            except Exception as exc:  # noqa: BLE001  one task's notice fails alone
                _log(f"task {task_id}: notices failed: {exc!r}")
        try:
            await self.roll(conn)
        except rollout.Restart:
            raise
        except Exception as exc:  # noqa: BLE001  a rollout's failure leaves the wake to schedule
            self.restart_due = None
            _log(f"rollout failed: {exc!r}")
        await self.schedule(conn)

    def _holding(self) -> bool:
        """A job other than the background turn's is running."""
        return any(t != self.background for t in self.jobs)

    async def roll(self, conn) -> None:
        """Roll the kernel forward to its due merge, if any (module
        docstring). Git runs off the loop; a wake with nothing new runs none."""
        if self.checkout is None or self.started is None:
            return
        if self.restart_due is not None and self._holding():
            return
        merges = []
        for row, task_id, effect_id, result in await (await conn.execute(MERGE_OUTCOMES)).fetchall():
            if effect_id in self.judged or effect_id in self.final:
                continue
            m = rollout.Merge.of(row, task_id, effect_id, result)
            if m is None:
                self.judged.add(effect_id)
            else:
                merges.append(m)
        retry = self.restart_due is not None
        self.restart_due = None
        if not merges or (not retry and all(m.effect_id in self.waiting for m in merges)):
            return
        try:
            due, judged = await git.threaded(rollout.judge, self.checkout, self.started, merges)
        except git.GitError as exc:
            # The failure is the newest merge's to the checkout's origin URL
            # and branch, read from its files alone; no task's when unread.
            try:
                origin = await git.threaded(git.origin, self.checkout)
            except git.GitError:
                origin = None
            ours = [m for m in merges if (m.remote, m.branch) == origin]
            if ours:
                await self._failed(conn, ours[-1], ["fetch"], "fetch", str(exc))
            else:
                self.waiting.update(m.effect_id for m in merges)  # tried again on the next tick
                _log(f"the kernel's checkout cannot be read; no merge rolled: {exc}")
            return
        self.judged.update(judged)
        if not due or (not retry and all(m.effect_id in self.waiting for m in due)):
            return
        plan = await git.threaded(rollout.prepare, self.checkout, self.started, due, self.credential)
        for m in plan.superseded:
            await self._ended(
                conn, m, outcome="superseded", sha=m.sha, reason=f"{m.sha} is not on {m.branch}"
            )
        m = plan.target
        if m is None:
            return
        if plan.failed is not None:
            await self._failed(conn, m, plan.steps, *plan.failed)
            return
        if plan.restart:
            if self._holding():
                self.restart_due = m
                _log(f"rollout of {m.sha}: holding new jobs until the running ones end")
                return
            await self._apply(conn, plan)
            return
        steps = [*plan.steps, "fast-forward"]
        try:
            _, head = await git.threaded(rollout.fast_forward, self.checkout, m.sha)
        except git.GitError as exc:
            await self._failed(conn, m, steps, "fast-forward", str(exc))
            return
        await self._ended(conn, m, outcome="done", head=head, steps=steps)
        for c in plan.covered:
            await self._ended(conn, c, outcome="done", head=head, rolled_by=m.effect_id)
        _log(f"rolled out {m.sha} with no restart")

    async def _apply(self, conn, plan: rollout.Plan) -> None:
        """Cancel the background turn, fast-forward, migrate, and raise
        `Restart`; every other job has ended."""
        m = plan.target
        job = self.jobs.get(self.background) if self.background else None
        if job is not None:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        steps = [*plan.steps, "fast-forward"]
        try:
            before, _ = await git.threaded(rollout.fast_forward, self.checkout, m.sha)
        except git.GitError as exc:
            await self._failed(conn, m, steps, "fast-forward", str(exc))
            return
        steps.append("migrate")
        database = psycopg.conninfo.conninfo_to_dict(self.dsn).get("dbname") or settings.database
        try:
            await git.threaded(self.migrate, database)
        except Exception as exc:  # noqa: BLE001  any failure of the merged migrate is the rollout's
            try:
                mixed = await git.threaded(rollout.step_back, self.checkout, before, m.sha)
            except git.GitError:
                mixed = True
            await self._failed(conn, m, steps, "migrate", str(exc), mixed=mixed)
            return
        covers = [{"effect_id": c.effect_id, "task_id": c.task_id, "sha": c.sha} for c in plan.covered]
        async with conn.transaction():
            await ledger.append(
                conn,
                m.task_id,
                "rollout.restarting",
                {
                    "effect_id": m.effect_id,
                    "sha": m.sha,
                    "from": self.started,
                    "steps": steps,
                    "covers": covers,
                },
            )
        _log(f"rollout of {m.sha}: restarting")
        raise rollout.Restart(m.sha)

    async def _ended(self, conn, m: rollout.Merge, **payload) -> None:
        async with conn.transaction():
            await ledger.append(conn, m.task_id, "rollout.ended", {"effect_id": m.effect_id, **payload})

    async def _failed(
        self, conn, m: rollout.Merge, steps: list[str], step: str, reason: str, *, mixed: bool = False
    ) -> None:
        """A `rollout.failed` row when the step or reason changed, and the
        merge's one notice (and one more when the kernel is left on mixed
        code). Tried again on the next `serve_tick_s` wake, or, for a step
        that repeats, not by this process."""
        self.waiting.add(m.effect_id)
        if step in rollout.FINAL:
            self.final.add(m.effect_id)
        _log(f"rollout of {m.sha} (effect {m.effect_id}) failed at {step}: {reason}")
        latest = await (
            await conn.execute(
                "SELECT payload FROM events WHERE type = 'rollout.failed' AND payload->>'effect_id' = %s "
                "ORDER BY id DESC LIMIT 1",
                (m.effect_id,),
            )
        ).fetchone()
        async with conn.transaction():
            if mixed or latest is None or (latest[0].get("step"), latest[0].get("reason")) != (step, reason):
                await ledger.append(
                    conn,
                    m.task_id,
                    "rollout.failed",
                    {"effect_id": m.effect_id, "sha": m.sha, "step": step, "reason": reason, "steps": steps}
                    | ({"mixed": True} if mixed else {}),
                )
            await notices.request(
                conn,
                m.task_id,
                kind="rollout",
                about_key=f"rollout:{m.effect_id}",
                text=_rollout_text(m, step, reason),
            )
            if mixed:
                await notices.request(
                    conn,
                    m.task_id,
                    kind="rollout",
                    about_key=f"rollout:{m.effect_id}:mixed",
                    text=f"The kernel's checkout could not be moved back after task {m.task_id}'s merge {m.sha} "
                    "failed to migrate: the running kernel's checkout holds code it did not start from (mixed "
                    "code) until it restarts. Fix the migrate or the checkout by hand.",
                )

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
        ready: list[tuple[bool, int, str]] = []
        active = await self.active(conn)
        for task_id in [t for t in self.services if t not in active and t not in self.jobs]:
            # Stopped since its last step: its services stop and its lock is released.
            try:
                await self.settle(task_id)
            except Exception as exc:  # noqa: BLE001  one task's settling fails alone
                _log(f"task {task_id}: settling failed: {exc!r}")
        if self.restart_due is not None:
            return  # a restart is due: no new job until the running ones end
        for task_id in active:
            if task_id in self.jobs:
                continue
            try:
                found = await self._ready(conn, task_id)
            except Exception as exc:  # noqa: BLE001  one task's scheduling fails alone
                _log(f"task {task_id}: scheduling failed: {exc!r}")
                continue
            if found is not None:
                ready.append(found)
        # A foreground step starts beside a background one: it waits for
        # the slot and preempts the background turn holding it.
        foreground = [r for r in ready if not r[0]]
        if self.harness is None and foreground:
            _, latest, task_id = min(foreground)
            self.harness = task_id
            self._start(task_id, self._step(task_id, latest), latest)
        elif self.harness is None and self.background is None and ready:
            _, latest, task_id = min(ready)
            self.background = task_id
            self._start(task_id, self._step(task_id, latest), latest)

    async def _ready(self, conn, task_id: str) -> tuple[bool, int, str] | None:
        """Start the task's job if it has one now; a harness step is
        returned instead, for `schedule` to choose: foreground before
        background, then the oldest."""
        rows = await ledger.read(conn, task_id)
        f = machine.fold(rows)
        if f.legacy or f.calibration or f.state in (State.MERGED, State.STOPPED):
            await self.settle(task_id)
            return None
        latest = max((r["id"] for r in rows if r["type"] not in QUIET), default=0)
        if task_id in self.parked:
            if latest <= self.parked[task_id]:
                return None
            del self.parked[task_id]
        if task_id in self.uncollected:
            self._start(task_id, self._recollect(task_id), latest)
            return None
        b = await tasks.brief(conn, task_id)
        if b.project and not b.workspace:
            if _provision_due(rows):
                self._start(task_id, self._provision(task_id, b.project.get("name")), latest)
            return None
        if latest <= self.seen.get(task_id, 0) or f.state is State.WAITING:
            return None
        if f.state in AT_ONCE:
            self._start(task_id, self._step(task_id, latest), latest)
        elif f.state in HARNESS:
            return (await tasks.background(conn, task_id), latest, task_id)
        return None

    def _start(self, task_id: str, coro, latest: int) -> None:
        job = asyncio.create_task(coro)
        self.jobs[task_id] = job

        def finished(_):
            self.jobs.pop(task_id, None)
            if self.harness == task_id:
                self.harness = None
            if self.background == task_id:
                self.background = None
            if not job.cancelled() and job.exception() is not None:
                _log(f"task {task_id}: job failed: {job.exception()!r}")
                self.parked[task_id] = latest
            self.wake.set()

        job.add_done_callback(finished)

    async def _recollect(self, task_id: str) -> None:
        """Collect the turn recover could not; a failure parks the task."""
        async with await db.connect(self.dsn) as conn:
            await recollect(conn, task_id, self.performers)
        self.uncollected.discard(task_id)

    async def _provision(self, task_id: str, name: str | None) -> None:
        """Provision a task started by message, off the loop. A failure is a
        `workspace.failed` row and a notice.

        Under `provision:<task>`, the lock the only writer of
        `workspace.provisioned` holds, the condition `_ready` scheduled this
        on is read again: a job that waited on the lock does nothing once the
        task was stopped, provisioned, or failed with no steer since. A task
        directory found then is a provisioning that died (a killed kernel):
        it is removed, with what it left running, and made again; its ports
        are free to choose again."""
        async with await db.connect(self.dsn) as conn:
            lock = f"provision:{task_id}"
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
            try:
                rows = await ledger.read(conn, task_id)
                f = machine.fold(rows)
                if f.legacy or f.calibration or f.state in (State.MERGED, State.STOPPED):
                    return
                b = await tasks.brief(conn, task_id)
                if not b.project or b.workspace or not _provision_due(rows):
                    return
                try:
                    if os.path.lexists(workspace.layout(task_id).root):
                        await git.threaded(workspace.remove, task_id)
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
                    # A cancel (the kernel closing) stops its git and setup
                    # commands and waits for its cleanup before the lock goes.
                    made = await git.threaded(workspace.provision, task_id, spec, ports)
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
                # Another process runs the task: tried again on its next row
                # or the next `serve_tick_s` wake.
                await services.close()
                self.parked[task_id] = latest
                return
            self.services[task_id] = services
        first = task_id not in self.swept
        self.swept.add(task_id)
        written: set[int] = set()
        collecting = ledger.WRITTEN.set(written)
        try:
            out = await router.step(
                self.gateway, task_id, self.runners, self.dsn, self.performers, services, sweep=first
            )
        except Exception as exc:  # noqa: BLE001  a step's failure is the task's; the kernel goes on
            _log(f"task {task_id}: step failed: {''.join(traceback.format_exception_only(exc)).strip()}")
            out = {"status": "failed"}
        finally:
            ledger.WRITTEN.reset(collecting)
        async with await db.connect(self.dsn) as conn:
            rows = await ledger.read(conn, task_id)
        self.seen[task_id] = latest
        if out.get("preempted"):
            # Nothing moved: the step is ready again, behind the foreground.
            self.seen.pop(task_id, None)
        elif out.get("status") == "parked":
            # Asked again on the next row or the next `serve_tick_s` wake.
            self.seen.pop(task_id, None)
            self.parked[task_id] = latest
        elif out.get("status") != "moved":
            # Seen: the rows the step read, and the rows it wrote up to the
            # first another writer added while it ran, which steps it again.
            for r in rows:
                if r["id"] <= latest or r["type"] in QUIET:
                    continue
                if r["id"] not in written:
                    break
                self.seen[task_id] = r["id"]
        state = machine.fold(rows).state
        if state in router.SETTLED or state is State.MERGE:
            await self.settle(task_id)

    async def settle(self, task_id: str) -> None:
        """The task needs Tom, is done, or was stopped: its services stop and
        `services:<task>` is released."""
        services = self.services.pop(task_id, None)
        if services is not None:
            try:
                await asyncio.to_thread(services.down)
            finally:
                await services.close()

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for task_id in list(self.services):
            await self.settle(task_id)


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _rollout_text(m: rollout.Merge, step: str, reason: str) -> str:
    head = f"Task {m.task_id}'s merge {m.sha} did not roll out to the running kernel"
    if step == "dependencies":
        return f"{head}: it changes the kernel's dependencies. Run `uv sync` and the rollout by hand."
    if step == "schema":
        return f"{head}: it changes core/schema.sql. Back up, migrate, and restart the kernel by hand."
    if step == "migrate":
        return f"{head}: the merged migrate failed: {reason}. The kernel does not try it again until it restarts."
    return f"{head}: {step} failed: {reason}. It is tried again every {settings.serve_tick_s:g} seconds."


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
    checkout: Path | None = ROOT,
    migrate: Callable[[str], None] | None = None,
    credential: str | Path | None = None,
) -> None:
    """Run the kernel until killed, or until a rollout of its own merge
    raises `rollout.Restart`. `checkout` is the checkout it runs from and
    rolls forward; `credential` the GitHub key file for the fetch
    (default `settings.github_keyfile`)."""
    dsn = dsn or settings.dsn()
    conn = await db.connect(dsn, application_name="valor-kernel")
    listener = await db.connect(dsn, application_name="valor-kernel-listen")
    owned_gateway = gateway is None
    kernel = None
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (kernel_key(),))
        started = None
        if checkout is not None:
            try:
                started = await git.threaded(git.head, checkout)
            except git.GitError as exc:
                _log(f"the kernel's checkout cannot be read; nothing is rolled out by this process: {exc}")
        done = await recover(conn, performers, checkout=checkout, running=started)
        print(f"kernel recovered: {json.dumps({k: len(v) for k, v in done.items()})}", flush=True)
        if gateway is None:
            from core.gateway import ClaudeLogin, OpenAIKey

            gateway = Gateway(dsn, credential=ClaudeLogin(), openai_credential=OpenAIKey())
            await gateway.start()
        kernel = Kernel(
            gateway,
            runners,
            performers,
            dsn,
            set(done["uncollected"]),
            checkout=checkout,
            started=started,
            migrate=migrate,
            credential=credential if credential is not None else settings.github_keyfile,
        )
        await listener.execute("LISTEN valor_events")
        listening = asyncio.create_task(_listen(listener, kernel.wake))
        try:
            while True:
                kernel.wake.clear()
                try:
                    await kernel.tick(conn)
                except rollout.Restart:
                    raise
                except Exception as exc:  # noqa: BLE001  the next wake tries again
                    _log(f"kernel wake failed: {exc!r}")
                    if conn.closed:
                        conn = await db.connect(dsn, application_name="valor-kernel")
                        await conn.execute(
                            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (kernel_key(),)
                        )
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
    "VALOR_MEMORY",
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
    "VALOR_EMAIL_ADDRESS",
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
