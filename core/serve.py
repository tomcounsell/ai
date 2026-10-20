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
  running commit contains: ended `done` (`core/rollout.py`). When there is
  one, first each bridge holding its lock on code the running commit
  changed where it reaches the bridge is restarted (`restart_bridges`),
  and the row names the bridges restarted and any that failed to.

Then it starts the gateway, listens on `valor_events`, and on each wake (a
row, or `settings.serve_tick_s` with none) binds every recorded message
(`intake.bind`), requests the notices each task owes (`notices.owe`),
rolls the kernel forward to its own merges (`roll`), and schedules jobs
(`schedule`). A job is one step of one task
(`router.step`), a provisioning, or the collection recover could not
make; one job per
task at a time, and at most `settings.max_turns` harness jobs at once,
foreground first, then in queue order (`tasks.queue`: start order, moved
by the writers' placements); a clarify step (a question to Tom) is started whenever
it is ready, outside that count. A turn also holds one of the machine's turn slots
(`core/slot.py`), which `python -m core run` shares.

A task is stepped again when it moved, when its turn finished `idle`
(behind every ready task that did not go idle, and among idle tasks the
oldest latest row first, so an idle task never holds the slot from other
work, whatever its queue place), or when a row it did not write
lands on its stream (an answer, a steer, a stop, an effect's outcome); a
chat session only when a message wakes it or one of its tasks reports
(`session.chat_waiting`), and its provisioning runs no setup and starts
no services. A step that failed is stepped again by the kernel once `router.retry_due`
says the delay after it has passed, and the task ends with a report at
`router.STEP_TRIES` failures in a row. A step that answers `parked` (a
merge whose facts git could not read, whose push has no answer yet, or
whose try again is not due) parks the task. A provisioning that failed is
tried again after `PROVISION_BACKOFF_S`, doubling, and at
`PROVISION_TRIES` failures the task ends with a report.

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
backs up the database when the merge changes `core/schema.sql` (`python -m
core backup`'s dump), fast-forwards the checkout, runs `uv sync --frozen`
when it changes `uv.lock` or `pyproject.toml`, runs the merged code's
migrate, writes `rollout.restarting`, and raises `rollout.Restart`, which
leaves `serve`; launchd starts the kernel again, and the new kernel's
start restarts each bridge the merge reached. A failed step writes
`rollout.failed`, steps the checkout (and its dependencies) back when it
had moved, lifts the hold, and is tried again on the next `serve_tick_s`
wake; after `rollout.NOTICE_AFTER` failures of one merge, Tom gets one
notice saying what failed.
"""

import asyncio
import dataclasses
import functools
import json
import os
import plistlib
import re
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from core import (
    backup,
    bridge,
    broker,
    db,
    git,
    intake,
    ledger,
    machine,
    notices,
    rollout,
    router,
    routines,
    runs,
    session,
    signals,
    spending,
    tasks,
    verdicts,
    workspace,
)
from core.gateway import Gateway
from core.machine import State
from core.settings import settings

PROVISION_TRIES = 3
# A finished task's workspace is removed a day after it merged, stopped or
# ended (`workspace.reclaim`): feedback on a merge and a restart of ended
# work use it until then. On the Cowboy, 2026-10-16, 26 finished
# workspaces (67 GB) had filled the disk and stopped a popoto turn.
RECLAIM_AFTER_S = 24 * 3600.0
RECLAIM_EVERY_S = 600.0
PROVISION_BACKOFF_S = 60.0
HARNESS = {State.PLAN, State.CRITIQUE, State.BUILD, State.CHECKS, State.PATCH}
# Started whenever ready, outside `max_turns`: a question to Tom (clarify)
# never waits on the build of another task.
AT_ONCE = {State.JUDGE, State.CLARIFY, State.MERGE}
LABEL = "com.valor.kernel"
# A bridge's lock connection (`core.bridge.serve`): its channel, and its stamp.
BRIDGE_CONNECTION = re.compile(r"valor-([a-z]+)(?:@([0-9a-z]+))?")
# The connection in this database holding a bridge's lock, `%s` its key
# (a bigint lock is held as classid, its high half, objid, its low half).
LOCK_HOLDER = (
    "SELECT a.application_name FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
    "CROSS JOIN (SELECT hashtextextended(%s, 0) AS k) h "
    "WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1 "
    "AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
    "AND a.datname = current_database() "
    "AND l.classid = ((h.k >> 32) & 4294967295)::oid AND l.objid = (h.k & 4294967295)::oid"
)
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
    "AND COALESCE((s.payload->>'compaction')::boolean, false) = false "
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
    kick: Callable[[str], str | None] | None = None,
) -> dict[str, list]:
    """Settle what a killed kernel left. Returns what was done, by kind.
    With the kernel's `checkout` and the `running` commit, also ends the
    rollouts that commit contains (`rolled`); with `kick` too, when there
    is one to end, first restarts each bridge on code the running commit
    changed (`restart_bridges`, `bridges`) and records it on those rows."""
    done: dict[str, list] = {
        "charged": [],
        "interrupted": [],
        "recollected": [],
        "uncollected": [],
        "reconciled": [],
        "swept": [],
        "bridges": [],
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
            ending = await _rolled(conn, checkout, running)
        except git.GitError as exc:
            _log(f"the kernel's checkout cannot be read; rollouts not ended: {exc}")
            ending = []
        kicked: dict[str, dict] = {}
        if ending and kick is not None:
            try:
                kicked = await restart_bridges(conn, checkout, running, kick)
            except Exception as exc:  # noqa: BLE001  the bridges' restart never stops the kernel's start
                _log(f"the bridges were not restarted: {exc!r}")
            done["bridges"] = sorted(kicked.get("restarted", {}))
        for task_id, payload in ending:
            async with conn.transaction():
                await ledger.append(conn, task_id, "rollout.ended", payload | kicked)
            done["rolled"].append(payload["effect_id"])
    return done


async def restart_bridges(
    conn, checkout: Path, running: str, kick: Callable[[str], str | None]
) -> dict[str, dict]:
    """Restart each bridge holding its lock, `bridge:<channel>:<machine>`,
    in this database, whose start commit differs from `running` in code it
    runs (`rollout.stale`), one at a time, off the loop. Returns
    `restarted`, each label with the stamp it replaced (None when
    unstamped), and `restart_failed`, each label with why; empty ones left
    out."""
    live: list[tuple[str, str | None]] = []
    for channel in rollout.BRIDGES:
        row = await (await conn.execute(LOCK_HOLDER, (f"bridge:{channel}:{settings.machine}",))).fetchone()
        found = BRIDGE_CONNECTION.fullmatch(row[0] or "") if row else None
        if found and found[1] == channel:
            live.append((channel, found[2]))
            if found[2] == rollout.NO_HEAD:
                _log(f"the {channel} bridge could not read its head; it is not restarted")
    due = await git.threaded(rollout.stale, checkout, running, live) if live else set()
    restarted: dict[str, str | None] = {}
    failed: dict[str, str] = {}
    for channel, stamp in live:
        if channel not in due:
            continue
        label = rollout.BRIDGES[channel]
        why = await git.threaded(kick, label)
        if why is None:
            restarted[label] = stamp
            _log(f"restarted {label}, started at {stamp}, for {running}")
        else:
            failed[label] = why
            _log(f"restarting {label} failed: {why}")
    return {k: v for k, v in (("restarted", restarted), ("restart_failed", failed)) if v}


async def _rolled(conn, checkout: Path, running: str) -> list[tuple[str, dict[str, Any]]]:
    """The `rollout.ended` rows owed, `(task, payload)`: one for every
    kernel merge with a `rollout.restarting` or `rollout.failed` row, or
    covered by a restarting row, that has no `rollout.ended` and whose sha
    the running commit contains, `rolled_by` the merge whose restart
    covered it, or `by: started` when no restart names it (rolled by hand,
    or carried by a later restart). `recover` writes them."""
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
        rolled.append((v["task"], payload))
    return rolled


# The kernel's merge outcomes not yet ended, oldest first.
# Tasks with a provisioned workspace not removed since.
RECLAIMABLE = (
    "SELECT DISTINCT p.task_id FROM events p WHERE p.type = 'workspace.provisioned' AND NOT EXISTS ("
    "SELECT 1 FROM events r WHERE r.task_id = p.task_id AND r.type = 'workspace.removed' AND r.id > p.id)"
)
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
        sync: Callable[[], None] | None = None,
        backup: Callable[[str], None] | None = None,
    ):
        self.gateway, self.runners, self.performers, self.dsn = gateway, runners, performers, dsn
        # The checkout this process runs from and its commit then; with no
        # checkout, nothing is rolled.
        self.checkout, self.started, self.credential = checkout, started, credential
        self.migrate = migrate or (functools.partial(rollout.migrate, checkout) if checkout else None)
        self.sync = sync or (functools.partial(rollout.sync, checkout, settings.uv) if checkout else None)
        self.backup = backup or _backup
        self.restart_due: rollout.Merge | None = None  # holds `schedule` until the jobs end
        self.judged: set[str] = set()  # merge effects contained in `started`, or not the kernel's
        self.waiting: set[str] = set()  # failed since the last `serve_tick_s` wake
        self.failures: dict[str, int] = {}  # failed tries of each merge's rollout in this process
        self.backed_up: set[str] = set()  # merges whose database backup this process took
        # Tasks whose ended turn recover could not collect: collected by a
        # job before the task is stepped.
        self.uncollected: set[str] = set(uncollected or ())
        self.jobs: dict[str, asyncio.Task] = {}
        self.harness: set[str] = set()  # the foreground tasks whose harness jobs run
        self.background: set[str] = set()  # the background tasks whose harness jobs run
        self.services: dict[str, router._Services] = {}
        self.swept: set[str] = set()
        self.seen: dict[str, int] = {}  # the latest row a step of the task read or wrote
        # Tasks whose job could not start its work, with the latest row then:
        # tried again on a newer row or the next `serve_tick_s` wake.
        self.parked: dict[str, int] = {}
        # Tasks whose last step went idle: they queue behind every other
        # ready task of their kind, and rotate among themselves by latest row,
        # so an idle task never holds a slot by its queue place.
        self.idle: set[str] = set()
        self.parked_at = time.monotonic()
        self.reclaimed_at = float("-inf")
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
        try:
            await self.reclaim(conn)
        except Exception as exc:  # noqa: BLE001  a removal that fails is tried on the next pass
            _log(f"workspace reclaim failed: {exc!r}")

    async def reclaim(self, conn) -> None:
        """Every `RECLAIM_EVERY_S`, remove the workspace of each task that
        finished `RECLAIM_AFTER_S` ago and runs no job here."""
        if time.monotonic() - self.reclaimed_at < RECLAIM_EVERY_S:
            return
        self.reclaimed_at = time.monotonic()
        before = datetime.now(UTC) - timedelta(seconds=RECLAIM_AFTER_S)
        for (task_id,) in await (await conn.execute(RECLAIMABLE)).fetchall():
            if task_id in self.jobs or task_id in self.services:
                continue
            if await workspace.reclaim(conn, task_id, by="kernel", via="serve", before=before):
                _log(f"task {task_id}: removed its finished workspace")

    def _holding(self) -> bool:
        """A job other than a background turn's is running."""
        return any(t not in self.background for t in self.jobs)

    async def roll(self, conn) -> None:
        """Roll the kernel forward to its due merge, if any (module
        docstring). Git runs off the loop; a wake with nothing new runs none."""
        if self.checkout is None or self.started is None:
            return
        if self.restart_due is not None and self._holding():
            return
        merges = []
        for row, task_id, effect_id, result in await (await conn.execute(MERGE_OUTCOMES)).fetchall():
            if effect_id in self.judged:
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
        """Cancel the background turns, back up, fast-forward, sync, migrate,
        and raise `Restart`; every other job has ended."""
        m = plan.target
        running = [self.jobs[t] for t in self.background if t in self.jobs]
        for job in running:
            job.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        steps = list(plan.steps)
        database = psycopg.conninfo.conninfo_to_dict(self.dsn).get("dbname") or settings.database
        if plan.backup and m.effect_id not in self.backed_up:
            steps.append("backup")
            try:
                await git.threaded(self.backup, database)
            except Exception as exc:  # noqa: BLE001  any failure of the backup is the rollout's
                await self._failed(conn, m, steps, "backup", str(exc))
                return
            self.backed_up.add(m.effect_id)
        steps.append("fast-forward")
        try:
            before, _ = await git.threaded(rollout.fast_forward, self.checkout, m.sha)
        except git.GitError as exc:
            await self._failed(conn, m, steps, "fast-forward", str(exc))
            return
        for step, run in (("dependencies", self.sync if plan.sync else None), ("migrate", self.migrate)):
            if run is None:
                continue
            steps.append(step)
            try:
                await git.threaded(run, *([database] if step == "migrate" else []))
            except Exception as exc:  # noqa: BLE001  any failure of the merged code's step is the rollout's
                mixed = await self._step_back(before, m.sha, plan.sync)
                await self._failed(conn, m, steps, step, str(exc), mixed=mixed)
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

    async def _step_back(self, before: str | None, sha: str, synced: bool) -> bool:
        """The checkout back to `before`, and its dependencies synced to it
        again when the rollout synced them; whether it is left mixed."""
        try:
            mixed = await git.threaded(rollout.step_back, self.checkout, before, sha)
        except git.GitError:
            return True
        if synced and not mixed:
            try:
                await git.threaded(self.sync)
            except Exception:  # noqa: BLE001  a failed sync back leaves the environment mixed
                return True
        return mixed

    async def _ended(self, conn, m: rollout.Merge, **payload) -> None:
        async with conn.transaction():
            await ledger.append(conn, m.task_id, "rollout.ended", {"effect_id": m.effect_id, **payload})

    async def _failed(
        self, conn, m: rollout.Merge, steps: list[str], step: str, reason: str, *, mixed: bool = False
    ) -> None:
        """A `rollout.failed` row when the step or reason changed; the
        merge's one notice once it has failed `rollout.NOTICE_AFTER` times,
        and one more when the kernel is left on mixed code. Tried again on
        the next `serve_tick_s` wake."""
        self.waiting.add(m.effect_id)
        self.failures[m.effect_id] = tries = self.failures.get(m.effect_id, 0) + 1
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
            if tries >= rollout.NOTICE_AFTER:
                await notices.request(
                    conn,
                    m.task_id,
                    kind="rollout",
                    about_key=f"rollout:{m.effect_id}",
                    text=notices.lead(await notices.headline(conn, m.task_id), _rollout_text(step, tries)),
                )
            if mixed:
                await notices.request(
                    conn,
                    m.task_id,
                    kind="rollout",
                    about_key=f"rollout:{m.effect_id}:mixed",
                    text=notices.lead(
                        await notices.headline(conn, m.task_id),
                        "it is merged, but putting it live failed partway and I could not undo it, so my "
                        f"system now runs a mix of old and new code. I try again every {settings.serve_tick_s:g} "
                        "seconds.",
                    ),
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
        open_ids: list[str] = []
        for task_id in active:
            if task_id in self.jobs:
                open_ids.append(task_id)
                continue
            try:
                found = await self._ready(conn, task_id)
            except Exception as exc:  # noqa: BLE001  one task's scheduling fails alone
                _log(f"task {task_id}: scheduling failed: {exc!r}")
                continue
            if found is False:
                continue
            open_ids.append(task_id)
            if found is not None:
                ready.append(found)
        # Foreground before background, then queue order, up to the limit; a
        # task whose last step went idle goes behind the rest, oldest latest
        # row first (`self.idle`).
        positions = tasks.queue(open_ids, await tasks.moves(conn, open_ids))
        free = settings.max_turns - len(self.harness) - len(self.background)

        def key(r: tuple[bool, int, str]) -> tuple:
            idle = r[2] in self.idle
            return (r[0], idle, r[1] if idle else positions.get(r[2], len(positions)), r[2])

        ranked = sorted(ready, key=key)
        for background, latest, task_id in ranked[: max(free, 0)]:
            _log(f"task {task_id}: step started, queue position {positions.get(task_id)} of {len(positions)}")
            (self.background if background else self.harness).add(task_id)
            self._start(task_id, self._step(task_id, latest), latest)

    async def _ready(self, conn, task_id: str) -> tuple[bool, int, str] | bool | None:
        """Start the task's job if it has one now; a harness step is
        returned instead, for `schedule` to choose: foreground before
        background, then queue order. False: the task is closed (merged,
        ended, stopped, calibration, or legacy) and has no queue place."""
        rows = await ledger.read(conn, task_id)
        f = machine.fold(rows)
        if f.legacy or f.calibration or f.state in tasks.TERMINAL:
            await self.settle(task_id)
            return False
        latest = max((r["id"] for r in rows if not ledger.quiet(r)), default=0)
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
        if f.state is State.WAITING:
            return None
        if latest <= self.seen.get(task_id, 0) and not router.retry_due(rows):
            return None
        if b.chat and not session.chat_waiting(rows) and not router.retry_due(rows):
            return None  # a chat session waits for the chat or its tasks, not its own outcomes
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
            self.harness.discard(task_id)
            self.background.discard(task_id)
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
        `workspace.failed` row, tried again when `_provision_due`; the one
        that makes `PROVISION_TRIES` ends the task (`task.ended`, kind
        `provision`), and Tom gets a report.

        Under `provision:<task>`, the lock the only writer of
        `workspace.provisioned` holds, the condition `_ready` scheduled this
        on is read again: a job that waited on the lock does nothing once the
        task was stopped, ended, provisioned, or failed with no try due. A task
        directory found then is a provisioning that died (a killed kernel):
        it is removed, with what it left running, and made again; its ports
        are free to choose again."""
        async with await db.connect(self.dsn) as conn:
            lock = f"provision:{task_id}"
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
            try:
                rows = await ledger.read(conn, task_id)
                f = machine.fold(rows)
                if f.legacy or f.calibration or f.state in (State.MERGED, State.ENDED, State.STOPPED):
                    return
                b = await tasks.brief(conn, task_id)
                if not b.project or b.workspace or not _provision_due(rows):
                    return
                try:
                    if os.path.lexists(workspace.layout(task_id).root):
                        await git.threaded(workspace.remove, task_id)
                    spec = workspace.Spec.load(name or "")
                    if b.chat:
                        # A chat session reads; its tasks build. No setup or services.
                        spec = dataclasses.replace(spec, setup=(), services=())
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
                        await ledger.append(conn, task_id, "workspace.failed", {"reason": str(exc)})
                    tries = _provision_failures(await ledger.read(conn, task_id))
                    if tries >= PROVISION_TRIES:
                        await verdicts.end(conn, task_id, "provision", str(exc), tries=tries)
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
        (self.idle.add if out.get("status") == "idle" else self.idle.discard)(task_id)
        if out.get("status") == "parked":
            # Asked again on the next row or the next `serve_tick_s` wake.
            self.seen.pop(task_id, None)
            self.parked[task_id] = latest
        elif out.get("status") not in ("moved", "idle"):
            # An idle step is stepped again, after the ready tasks whose
            # latest row is older. Otherwise seen: the rows the step read, and the rows it wrote up to the
            # first another writer added while it ran, which steps it again.
            for r in rows:
                if r["id"] <= latest or ledger.quiet(r):
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
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for task_id in list(self.services):
            await self.settle(task_id)


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


# A rollout step (`rollout.prepare`, `Kernel._restart`), in Tom's words.
ROLLOUT_STEPS = {
    "fetch": "fetching the merged code",
    "restart class": "working out what the change needs",
    "uncommitted": "checking that my own code has no uncommitted changes the restart would run",
    "fast-forward": "moving my running code onto it",
    "backup": "backing up my database first",
    "dependencies": "installing its dependencies",
    "migrate": "updating my database",
}


def _rollout_text(step: str, tries: int) -> str:
    """The rollout notice's sentence: what failed, in plain words; the step
    and reason stay on `rollout.failed`."""
    return (
        "it is merged, but I could not put it live on my own system: "
        f"{ROLLOUT_STEPS.get(step, 'one step of putting it live')} failed {tries} times. "
        f"I try again every {settings.serve_tick_s:g} seconds."
    )


def _backup(database: str) -> None:
    """The rollout's backup before a schema change: `python -m core
    backup`'s dump of the kernel database."""
    backup.dump(database=database)


def _provision_failures(rows: list[dict[str, Any]]) -> int:
    """The provisionings that failed since Tom last steered the task or gave
    feedback on it."""
    count = 0
    for r in reversed(rows):
        if r["type"] in ("message.steered", "feedback.given"):
            break
        count += r["type"] == "workspace.failed"
    return count


def _provision_due(rows: list[dict[str, Any]], now: datetime | None = None) -> bool:
    """No provisioning failed, or the delay after the last failure, doubling
    from `PROVISION_BACKOFF_S`, has passed while fewer than
    `PROVISION_TRIES` have failed. A steer or feedback starts the count
    again."""
    tries = _provision_failures(rows)
    if tries == 0:
        return True
    if tries >= PROVISION_TRIES:
        return False
    last = next(r for r in reversed(rows) if r["type"] == "workspace.failed")
    at = last.get("at")
    now = now or datetime.now(UTC)
    return at is None or now >= at + timedelta(seconds=PROVISION_BACKOFF_S * 2 ** (tries - 1))


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
    kick: Callable[[str], str | None] | None = rollout.kick,
) -> None:
    """Run the kernel until killed, or until a rollout of its own merge
    raises `rollout.Restart`. `checkout` is the checkout it runs from and
    rolls forward; `credential` the GitHub key file for the fetch
    (default `settings.github_keyfile`); `kick` restarts a bridge's
    launchd job at start (`restart_bridges`)."""
    dsn = dsn or settings.dsn()
    started = None
    if checkout is not None:
        try:
            started = await git.threaded(git.head, checkout)
        except git.GitError as exc:
            _log(f"the kernel's checkout cannot be read; nothing is rolled out by this process: {exc}")
    # Stamped like a bridge's lock connection, so the dashboard names the commit it runs.
    name = f"valor-kernel@{started or bridge.NO_HEAD}"
    conn = await db.connect(dsn, application_name=name)
    listener = await db.connect(dsn, application_name="valor-kernel-listen")
    owned_gateway = gateway is None
    kernel = None
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (kernel_key(),))
        done = await recover(conn, performers, checkout=checkout, running=started, kick=kick)
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
                        conn = await db.connect(dsn, application_name=name)
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
    # Where the rollout's backup before a schema change goes, and how many
    # dumps it keeps: the same as the backup job's (`backup.PLIST_ENV`).
    "VALOR_BACKUP_DIR",
    "VALOR_BACKUP_KEEP",
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
    "VALOR_OPERATOR_SLACK_IDS",
    "VALOR_OPERATOR_CHANNEL",
    "VALOR_OPERATOR_CHAT",
    "VALOR_EMAIL_ADDRESS",
    "VALOR_INBOUND",
    "VALOR_SERVE_TICK_S",
)


def plist(*, python: str | None = None, root: Path = ROOT) -> bytes:
    """The launchd job for the kernel: kept alive, started at load."""
    env = {"HOME": str(Path.home()), "PATH": routines.launchd_path()}
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
