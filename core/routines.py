"""Routines: scheduled work as a standing objective, never a bare script.

A routine is `routines/<name>/routine.toml` in the kernel's own checkout
(`settings.routines_dir`). Its first run registers a standing objective, a
root task carrying `Brief.routine` and the marker `{"objective": NAME}`; a
`routine.registered` row on the `routines` stream records it, and a unique
index keeps one live objective per name and ceiling. Each run is a child of
the objective, so the objective tree's rollup (`tasks.tree_spending`) is the
routine's spending. `routine.ran` rows on the objective record each firing.

`run` is `python -m core routine NAME`: launchd runs it, the routine's
runner starts or continues the run, and the resident kernel drives the
run's tasks as background work (`tasks.background`). `due` is the expiry
sweep's one fold: the guards and routines whose ninety days are up.
`report` is the period spending fold the command line and the status page
both read. Spending is reported and never stops anything.
"""

import hashlib
import os
import plistlib
import re
import sys
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from core import backup, guards, ledger, machine, tasks
from core.settings import resolve_model, settings

STREAM = "routines"
OBJECTIVE = "objective"
LABEL = "com.valor.routine"
ROOT = Path(__file__).resolve().parent.parent
USE_DAYS = 90  # the governance paragraph and Mission item 5: ninety days
EXPIRY_RUNNER = "expiry"
OUTCOMES = ("started", "continued", "nothing_due", "finished", "failed", "running")
SCHEDULE_KEYS = {"minute": "Minute", "hour": "Hour", "day": "Day", "weekday": "Weekday", "month": "Month"}

# The names a launchd job carries: the settings that reach Postgres (the
# names `backup.PLIST_ENV` lists for it) and the replay directory.
PLIST_ENV = (
    *(n for n in backup.PLIST_ENV if n.startswith(("VALOR_PG", "VALOR_DB"))),
    "VALOR_DEMO",
    # What shapes the turn slot, the projects and the workspaces must match
    # the kernel's, or a routine's turns run beside Tom's and resolve other paths.
    "VALOR_MACHINE",
    "VALOR_WORK",
    "VALOR_PROJECTS",
)


class Refused(ValueError):
    """A routine the kernel will not load or run."""


@dataclass(frozen=True)
class Routine:
    """`routine.toml`, as the kernel's checkout holds it. `need` is stored
    and shown, never enforced. `project` and `branch` name the project spec
    and branch a runner that starts a workspace task provisions."""

    name: str
    runner: str
    ceiling: str
    model: str
    mission_item: str
    need: tuple[str, ...]
    created: date
    instruction: str
    schedule: dict[str, int]
    interval: int | None
    digest: str
    project: str | None = None
    branch: str | None = None


def load(name: str, directory: str | Path | None = None) -> Routine:
    """The routine `name` from `directory` (default `settings.routines_dir`).
    Refused: a name that is not a plain directory name, an unknown routine,
    a toml that is malformed, names another routine, lacks a key, or gives
    an unknown ceiling or schedule key."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise Refused(f"{name!r} is not a routine name")
    root = Path(directory or settings.routines_dir)
    path = root / name / "routine.toml"
    try:
        raw = path.read_bytes()
        found = tomllib.loads(raw.decode())
    except FileNotFoundError:
        raise Refused(f"no routine {name} in {root}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise Refused(f"{path}: {exc}") from None
    if found.get("name") != name:
        raise Refused(f"{path} names {found.get('name')!r}, not {name}")
    for key in ("runner", "ceiling", "model", "mission_item", "instruction", "created"):
        if key not in found:
            raise Refused(f"{path} lacks {key!r}")
    if found["ceiling"] not in tasks.EFFECT_RANK:
        raise Refused(f"{path}: unknown effect class {found['ceiling']!r}")
    schedule = dict(found.get("schedule") or {})
    interval = schedule.pop("interval", None)
    unknown = sorted(set(schedule) - set(SCHEDULE_KEYS))
    if unknown or not (schedule or interval):
        raise Refused(f"{path}: [schedule] takes {', '.join([*SCHEDULE_KEYS, 'interval'])}")
    created = found["created"]
    if not isinstance(created, date):
        raise Refused(f"{path}: created is a date")
    return Routine(
        name=name,
        runner=found["runner"],
        ceiling=found["ceiling"],
        model=found["model"],
        mission_item=str(found["mission_item"]),
        need=tuple(found.get("need") or ()),
        created=created,
        instruction=found["instruction"],
        schedule=schedule,
        interval=interval,
        digest=hashlib.sha256(raw).hexdigest(),
        project=found.get("project"),
        branch=found.get("branch"),
    )


def names(directory: str | Path | None = None) -> list[str]:
    """Every routine the checkout holds, sorted."""
    root = Path(directory or settings.routines_dir)
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.glob("*/routine.toml"))


def plist(routine: Routine, *, python: str | None = None, root: Path = ROOT) -> bytes:
    """The launchd job: the kernel's interpreter, `-m core routine NAME`, the
    toml's schedule, the settings in `PLIST_ENV` that were set when it was
    printed, and nothing else. It names no script and carries no secret."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / f"routine-{routine.name}.log"
    job: dict[str, Any] = {
        "Label": f"{LABEL}.{routine.name}",
        "ProgramArguments": [python or sys.executable, "-m", "core", "routine", routine.name],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": env,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    if routine.interval:
        job["StartInterval"] = routine.interval
    else:
        job["StartCalendarInterval"] = {SCHEDULE_KEYS[k]: v for k, v in routine.schedule.items()}
    return plistlib.dumps(job)


# -- registration ---------------------------------------------------------------


async def registrations(conn, name: str) -> list[dict[str, Any]]:
    """Every `routine.registered` row of the routine, in id order: `id`, `at`,
    and the payload's fields."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, payload, at FROM events WHERE task_id = %s AND type = 'routine.registered' "
            "AND payload->>'name' = %s ORDER BY id",
            (STREAM, name),
        )
        rows = await cur.fetchall()
    return [{"id": r["id"], "at": r["at"], **r["payload"]} for r in rows]


def live(registered: list[dict[str, Any]], ceiling: str) -> dict[str, Any] | None:
    """The registration at `ceiling` that no later one replaces."""
    replaced = {r.get("replaces") for r in registered}
    return next(
        (r for r in reversed(registered) if r["ceiling"] == ceiling and r["objective"] not in replaced), None
    )


async def _register(conn, r: Routine, *, replaces: str | None, by: str, via: str) -> str:
    seat_model = resolve_model(r.model)
    brief = tasks.Brief(
        instruction=f"routine {r.name}: {r.instruction}",
        max_effect_class=r.ceiling,
        model=seat_model,
        routine=r.name,
    )
    objective = await tasks.start(conn, brief, marker={OBJECTIVE: r.name}, by=by, via=via)
    await ledger.append(
        conn,
        STREAM,
        "routine.registered",
        {
            "name": r.name,
            "ceiling": r.ceiling,
            "digest": r.digest,
            "need": list(r.need),
            "mission_item": r.mission_item,
            "objective": objective,
            "replaces": replaces,
        },
    )
    return objective


async def ensure(conn, r: Routine, *, restart: bool = False) -> tuple[str, str | None]:
    """The routine's live objective at its ceiling, registered on the first
    run. Returns `(objective id, stopped)`: `stopped` is the id of the
    stopped objective when Tom's stop holds (no restart asked), else None.
    `restart` registers a fresh objective, `replaces` naming the stopped one;
    on a routine that is not stopped it changes nothing."""
    async with conn.transaction():
        await ledger.lock(conn, f"routine:{r.name}")
        found = live(await registrations(conn, r.name), r.ceiling)
        if found is None:
            return await _register(
                conn, r, replaces=None, by="routine", via=f"python -m core routine {r.name}"
            ), None
        objective = found["objective"]
        if await tasks.is_stopped(conn, objective):
            if not restart:
                return objective, objective
            fresh = await _register(
                conn, r, replaces=objective, by="tom", via=f"python -m core routine {r.name} --restart"
            )
            return fresh, None
        return objective, None


# -- spending -------------------------------------------------------------------


async def spend(
    conn, roots: list[str], *, since: datetime | None = None, until: datetime | None = None
) -> dict[str, Any]:
    """The metered spending under `roots` (each root's subtree, and the
    calibration tasks whose `emulator.item_task` is in those subtrees):
    `spent_usd_micros` over the charges with `at` after `since` and not after
    `until`, `charges`, and `open_calls` listed apart. A report."""
    nodes: list[str] = []
    for root in roots:
        nodes += [root, *await tasks.subtree(conn, root)]
    metered = list(roots)
    if nodes:
        found = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'task.started' "
                "AND payload->'emulator'->>'item_task' = ANY(%s)",
                (nodes,),
            )
        ).fetchall()
        metered += [r[0] for r in found]
    charges: list[dict[str, Any]] = []
    open_calls: dict[str, Any] = {}
    for task_id in metered:
        got = await tasks.tree_spending(conn, task_id)
        charges += got["charges"]
        open_calls |= got["tree_open_calls"]
    kept = [
        c
        for c in charges
        if (since is None or datetime.fromisoformat(c["at"]) > since)
        and (until is None or datetime.fromisoformat(c["at"]) <= until)
    ]
    return {
        "spent_usd_micros": sum(c["usd_micros"] for c in kept),
        "charges": kept,
        "open_calls": open_calls,
    }


async def runs(conn, objectives: list[str]) -> list[dict[str, Any]]:
    """The routine's `routine.ran` rows in id order: `run` (task id or None),
    `outcome`, `summary`, `at`, and the objective they are on."""
    if not objectives:
        return []
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, task_id, payload, at FROM events WHERE type = 'routine.ran' AND task_id = ANY(%s) "
            "ORDER BY id",
            (objectives,),
        )
        rows = await cur.fetchall()
    return [{"id": r["id"], "objective": r["task_id"], "at": r["at"], **r["payload"]} for r in rows]


async def state_of(conn, task_id: str) -> str:
    """A task's state for a report: `stopped` for a fenced one, `node` for an
    objective node, else the machine's state."""
    if await tasks.is_stopped(conn, task_id):
        return "stopped"
    f = machine.fold(await ledger.read(conn, task_id))
    return "node" if f.legacy else f.state.value


async def report(conn, name: str, now: datetime | None = None) -> dict[str, Any]:
    """The routine as the command line and the status page show it: its
    objectives, the rolling period's metered spending (charges after
    `now - period` and not after `now`, over every objective of the name) with
    open calls listed apart, the runs in the period, the last run and its
    result, and each run's own spending. Nothing reads it to decide."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=settings.routine_period_days)
    registered = await registrations(conn, name)
    objectives = [r["objective"] for r in registered]
    period = await spend(conn, objectives, since=since, until=now)
    ran = await runs(conn, objectives)
    by_run: dict[str | None, dict[str, Any]] = {}
    for row in ran:
        key = row.get("run") or f"none-{row['id']}"
        first = by_run.setdefault(key, {"run": row.get("run"), "started_at": row["at"], "firings": 0})
        first.update(outcome=row["outcome"], summary=row.get("summary"), at=row["at"])
        first["firings"] += 1
    listed = list(by_run.values())
    for entry in listed:
        entry["spent_usd_micros"] = (
            (await spend(conn, [entry["run"]]))["spent_usd_micros"] if entry["run"] else 0
        )
        entry["state"] = await state_of(conn, entry["run"]) if entry["run"] else None
    in_period = [e for e in listed if since < e["started_at"] <= now]
    last = listed[-1] if listed else None
    return {
        "name": name,
        "objectives": objectives,
        "ceilings": sorted({r["ceiling"] for r in registered}),
        "need": registered[-1]["need"] if registered else [],
        "mission_item": registered[-1]["mission_item"] if registered else None,
        "period_days": settings.routine_period_days,
        "period_spent_usd_micros": period["spent_usd_micros"],
        "period_open_calls": period["open_calls"],
        "period_runs": len(in_period),
        "last": last,
        "runs": listed,
    }


async def reports(conn, now: datetime | None = None) -> list[dict[str, Any]]:
    """`report` for every routine the checkout holds or the ledger registered."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT DISTINCT payload->>'name' FROM events WHERE task_id = %s AND type = 'routine.registered'",
            (STREAM,),
        )
        registered = [r[0] for r in await cur.fetchall()]
    return [await report(conn, n, now) for n in sorted({*names(), *registered})]


def line(name: str, rep: dict[str, Any], run: str | None, outcome: str, run_spent: int) -> str:
    """The one line `python -m core routine` prints."""
    return (
        f"routine {name}: run {run or 'none'} {outcome}; metered spending {tasks.usd(run_spent)}; "
        f"{name} over the last {rep['period_days']} days: {tasks.usd(rep['period_spent_usd_micros'])} "
        f"in {rep['period_runs']} runs"
    )


def listing(rep: dict[str, Any]) -> str:
    """One routine's line in `python -m core routines`: its last run and
    result, and the period's spending, the figures the status page shows."""
    last = rep["last"]
    seen = (
        f"last run {last['run'] or 'none'} {last['outcome']} at {last['at']:%Y-%m-%d %H:%M}"
        if last
        else "never run"
    )
    return (
        f"{rep['name']}: {seen}; over the last {rep['period_days']} days "
        f"{tasks.usd(rep['period_spent_usd_micros'])} in {rep['period_runs']} runs"
    )


# -- running --------------------------------------------------------------------


@dataclass
class Ran:
    """What a runner did: the run's task id (None when it started none), the
    outcome, and a short summary."""

    run: str | None
    outcome: str
    summary: str


@dataclass
class Context:
    """What a runner is given. `last` is the latest `routine.ran` row on the
    objective (None on the first firing), `start_project` the composition
    root's start of a provisioned workspace task."""

    conn: Any
    routine: Routine
    objective: str
    now: datetime
    last: dict[str, Any] | None
    start_project: Callable[..., Awaitable[str]] | None = None
    dsn: str | None = None
    ran: list[dict[str, Any]] = field(default_factory=list)


Runner = Callable[[Context], Awaitable[Ran]]


async def run(
    conn,
    name: str,
    runners: dict[str, Runner],
    *,
    restart: bool = False,
    now: datetime | None = None,
    start_project: Callable[..., Awaitable[str]] | None = None,
    directory: str | Path | None = None,
    dsn: str | None = None,
) -> str:
    """`python -m core routine NAME`: load the toml, register the objective on
    the first run, and stop at once, writing nothing, when Tom stopped it
    (`restart` begins a fresh one). Otherwise the routine's runner starts or
    continues the run, and `routine.ran` and the printed line record it."""
    r = load(name, directory)
    if r.runner not in runners:
        raise Refused(
            f"routine {name} names runner {r.runner!r}; the kernel has {', '.join(sorted(runners))}"
        )
    objective, stopped = await ensure(conn, r, restart=restart)
    if stopped:
        return f"routine {name} is stopped (task {stopped}); --restart starts it again"
    given = now
    now = now or datetime.now(UTC)
    ran = await runs(conn, [objective])
    ctx = Context(
        conn=conn,
        routine=r,
        objective=objective,
        now=now,
        last=ran[-1] if ran else None,
        start_project=start_project,
        dsn=dsn,
        ran=ran,
    )
    did = await runners[r.runner](ctx)
    if did.outcome not in OUTCOMES:
        raise Refused(f"runner {r.runner} returned the outcome {did.outcome!r}")
    spent = (await spend(conn, [did.run]))["spent_usd_micros"] if did.run else 0
    await ledger.append(
        conn,
        objective,
        "routine.ran",
        {"run": did.run, "outcome": did.outcome, "summary": did.summary, "spent_usd_micros": spent},
    )
    return line(name, await report(conn, name, given or datetime.now(UTC)), did.run, did.outcome, spent)


async def open_run(ctx: Context) -> str | None:
    """The objective's latest run task when it is neither merged nor
    stopped."""
    for row in reversed(ctx.ran):
        if row.get("run"):
            state = await state_of(ctx.conn, row["run"])
            return None if state in ("merged", "stopped") else row["run"]
    return None


# -- the expiry sweep -----------------------------------------------------------


def _day(at: datetime) -> date:
    return at.astimezone(UTC).date()


async def _sweeps(conn) -> list[dict[str, Any]]:
    """Every sweep task that is open or merged, with what it listed and when."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT task_id, payload, at FROM events WHERE type = 'task.started' AND payload ? 'due' "
            "AND payload->>'sweep' = 'expiry' ORDER BY id"
        )
        found = await cur.fetchall()
    return [r for r in found if not await tasks.is_stopped(conn, r["task_id"])]


async def due(conn, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """What the expiry sweep proposes to delete at `now`: `items` (each
    `kind` guard, grant, or routine, with its id, name, incident, mission
    items, grant and expiry dates, `last_firing`, and whether a firing
    record exists) and `outside`, the instance grants on tasks of another
    project, listed and not removed. One fold over kernel-written rows."""
    today = _day(now)
    listed: dict[tuple[str, str], date] = {}
    removed: set[str] = set()  # instance grants a merged sweep listed
    for sweep in await _sweeps(conn):
        merged = await state_of(conn, sweep["task_id"]) == "merged"
        for item in sweep["payload"]["due"]:
            key = (item["kind"], item["id"])
            listed[key] = max(listed.get(key, date.min), _day(sweep["at"]))
            if merged and item["kind"] == "grant":
                removed.add(item["id"])

    def listed_recently(key: tuple[str, str]) -> bool:
        return key in listed and today < listed[key] + timedelta(days=USE_DAYS)

    firings: dict[str, date] = {}
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT task_id, payload->>'guard_id' AS guard_id, at FROM events "
            "WHERE type = ANY(%s) AND payload->>'guard_id' IS NOT NULL ORDER BY id",
            (list(machine.VERDICT_ROWS),),
        )
        verdicts = await cur.fetchall()
    background: dict[str, bool] = {}
    for v in verdicts:
        if v["task_id"] not in background:
            background[v["task_id"]] = await tasks.background(conn, v["task_id"])
        if not background[v["task_id"]]:
            firings[v["guard_id"]] = max(firings.get(v["guard_id"], date.min), _day(v["at"]))

    items: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT task_id, payload, at FROM events WHERE type = 'guard.granted' ORDER BY id")
        granted = await cur.fetchall()
    seeded = {g["guard_id"] for g in guards.SEEDED}
    for row in granted:
        p = row["payload"]
        guard_id = p["guard_id"]
        expires = date.fromisoformat(p["expires"])
        instance = bool(p.get("instance_id"))
        kind = "grant" if instance else "guard"
        if not instance and guard_id not in seeded:
            continue  # a seeded guard the checkout no longer holds is gone
        if instance and guard_id in removed:
            continue  # a grant has no code left to check: the merged sweep removed it
        fired = firings.get(guard_id)
        if instance or fired is None:
            is_due = today > expires
        else:
            is_due = today >= fired + timedelta(days=USE_DAYS) and today > expires
        if not is_due or listed_recently((kind, guard_id)):
            continue
        item = {
            "kind": kind,
            "id": guard_id,
            "name": p.get("name"),
            "incident": p.get("incident"),
            "mission_items": p.get("mission_items"),
            "granted_at": p.get("granted_at"),
            "expires": p["expires"],
            "last_firing": fired.isoformat() if fired else None,
            "firing_record": not instance,
        }
        if instance:
            try:
                project = (await tasks.brief(conn, row["task_id"])).project
            except KeyError:
                project = None
            if (project or {}).get("name") != "valor":
                outside.append(item)
                continue
        items.append(item)

    expiry_names = set()
    for name in names():
        try:
            if load(name).runner == EXPIRY_RUNNER:
                expiry_names.add(name)
        except Refused:
            continue
    cutoff = now - timedelta(days=USE_DAYS)
    for name in names():
        if name in expiry_names or listed_recently(("routine", name)):
            continue
        registered = await registrations(conn, name)
        if not registered or registered[0]["at"] > cutoff:
            continue
        if await _used(conn, [r["objective"] for r in registered], cutoff):
            continue
        r = load(name)
        items.append(
            {
                "kind": "routine",
                "id": name,
                "name": f"the routine {name}",
                "incident": "; ".join(r.need) or None,
                "mission_items": [r.mission_item],
                "granted_at": _day(registered[0]["at"]).isoformat(),
                "expires": (_day(registered[0]["at"]) + timedelta(days=USE_DAYS)).isoformat(),
                "last_firing": None,
                "firing_record": True,
            }
        )
    return {"items": items, "outside": outside}


async def _used(conn, objectives: list[str], cutoff: datetime) -> bool:
    """Whether a run in the window led to use: an effect performed, a
    question Tom answered, or a child task that delivered."""
    for row in await runs(conn, objectives):
        if row["at"] < cutoff or not row.get("run"):
            continue
        nodes = [row["run"], *await tasks.subtree(conn, row["run"])]
        found = await (
            await conn.execute(
                "SELECT 1 FROM events WHERE (task_id = ANY(%s) AND ((type = 'effect.outcome' "
                "AND payload->>'kind' = 'done') OR type = 'question.answered')) "
                "OR (task_id = ANY(%s) AND type = 'task.delivered') LIMIT 1",
                (nodes, nodes[1:]),
            )
        ).fetchone()
        if found:
            return True
    return False


def render(found: dict[str, list[dict[str, Any]]], r: Routine) -> str:
    """The sweep task's instruction: the routine's own text, then each item
    with its incident, mission item, grant date, expiry, and last firing."""

    def entry(i: dict[str, Any]) -> str:
        firing = (
            f"last fired {i['last_firing']}"
            if i["last_firing"]
            else ("never fired" if i["firing_record"] else "no firing record")
        )
        return (
            f"- {i['kind']} {i['id']}: {i['name']}\n"
            f"  incident: {i['incident']}\n"
            f"  mission items: {', '.join(str(m) for m in i['mission_items'] or [])}\n"
            f"  granted {i['granted_at']}, expires {i['expires']}, {firing}"
        )

    parts = [r.instruction.strip(), "", "Due now:", *(entry(i) for i in found["items"])]
    if found["outside"]:
        parts += ["", "Outside this repository: listed, not removed:", *(entry(i) for i in found["outside"])]
    return "\n".join(parts)


async def expiry_runner(ctx: Context) -> Ran:
    """Continue the open sweep; else fold `due` and, with something due,
    start one task on the routine's project for the deletion. Nothing due
    starts no task and runs no turn."""
    open_ = await open_run(ctx)
    if open_:
        return Ran(open_, "continued", f"sweep {open_} is open")
    found = await due(ctx.conn, ctx.now)
    if not found["items"]:
        return Ran(None, "nothing_due", "nothing due")
    if ctx.start_project is None or not ctx.routine.project:
        raise Refused(f"routine {ctx.routine.name} starts a project task; none is given")
    listed = [{"kind": i["kind"], "id": i["id"]} for i in [*found["items"], *found["outside"]]]
    run_id = await ctx.start_project(
        ctx.conn,
        parent=ctx.objective,
        instruction=render(found, ctx.routine),
        routine=ctx.routine.name,
        ceiling=ctx.routine.ceiling,
        model=ctx.routine.model,
        project=ctx.routine.project,
        branch=ctx.routine.branch,
        marker={"sdlc": 1, "sweep": EXPIRY_RUNNER, "due": listed},
    )
    return Ran(run_id, "started", f"{len(found['items'])} due, {len(found['outside'])} outside")
