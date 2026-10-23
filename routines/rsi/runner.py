"""The `rsi` routine: what Tom said and what failed, each started as one task.

Plain SQL, no turn. Three sources are read from the ledger: `correction.recorded`
rows, Tom's verified DMs that link a repository of the allowed set, and
`task.ended` rows of kind `step` (retries exhausted) on tasks of the allowed set
that `rsi` did not start. A row is unseen when it is newer than the routine's
first registration and no `task.started` carries `rsi:<event id>`; each unseen
row, oldest first, goes through `findings.start_fix`. An ended, stopped or merged
task never restarts its row. What Tom used is counted over what was started.
"""

import re
from dataclasses import dataclass

from core import findings, intake, ledger, notices, outcomes, routines, tasks, workspace
from core.findings import Finding
from core.routines import Context, Ran

NAME = "rsi"
ALLOWED = ("valor", "popoto")  # open-source projects (Tom, 2026-10-21)
GITHUB = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+)", re.IGNORECASE)


def _slug(owner: str, repo: str) -> str:
    return f"{owner}/{repo.removesuffix('.git')}".lower()


def allowed() -> dict[str, str]:
    """The `owner/repo` of each allowed project, from its spec read directly."""
    found = {}
    for name in ALLOWED:
        try:
            m = GITHUB.search(workspace.Spec.load(name).repo)
        except workspace.Refused:
            continue
        if m:
            found[_slug(*m.groups())] = name
    return found


def project_of(text: str | None) -> str | None:
    """The allowed project whose repository `text` links first, else None."""
    known = allowed()
    for m in GITHUB.finditer(text or ""):
        if _slug(*m.groups()) in known:
            return known[_slug(*m.groups())]
    return None


@dataclass(frozen=True)
class Row:
    id: int
    source: str  # "correction", "message" or "step"
    finding: Finding

    @property
    def key(self) -> str:
        return self.finding.keys[0]


async def _of_rsi(conn, task_id: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'task.started' "
            "AND payload->'finding'->>'source' = %s",
            (task_id, NAME),
        )
    ).fetchone()
    return row is not None


async def since(conn) -> int:
    """The id of the routine's first registration: older rows are never its."""
    return (await routines.registrations(conn, NAME))[0]["id"]


async def rows(conn, after: int) -> list[Row]:
    """Every row of the three sources newer than `after`, oldest first."""
    cur = await conn.execute(
        "SELECT id, type, task_id, payload FROM events WHERE id > %s AND (type = 'correction.recorded' "
        "OR (type = 'message.received' AND payload->>'chat_kind' = 'dm') "
        "OR (type = 'task.ended' AND payload->>'kind' = 'step')) ORDER BY id",
        (after,),
    )
    found, ended = [], set()
    for id_, kind, task_id, p in await cur.fetchall():
        key = (f"rsi:{id_}",)
        if kind == "correction.recorded":
            found.append(
                Row(
                    id_,
                    "correction",
                    Finding(
                        key,
                        f"Tom corrected Valor (correction {p['number']}, scope {p['scope']}). Find what in "
                        "the persona, a skill or the code made the mistake, and fix it.",
                        findings.head(p["text"]),
                    ),
                )
            )
        elif kind == "message.received":
            project = project_of(p.get("text"))
            if project and intake._from_operator(p):
                found.append(
                    Row(
                        id_,
                        "message",
                        Finding(
                            key,
                            f"Tom sent a link to the {project} repository. Do what his message asks.",
                            findings.head(p["text"]),
                            project,
                        ),
                    )
                )
        elif task_id not in ended and not await _of_rsi(conn, task_id):
            ended.add(task_id)
            b = await tasks.brief(conn, task_id)
            project = project_of((b.project or {}).get("repo") or b.origin_url)
            if project:
                found.append(
                    Row(
                        id_,
                        "step",
                        Finding(
                            key,
                            f"Task {task_id} stopped: its {p.get('check') or 'step'} failed {p.get('tries')} "
                            "times in a row. Find why and fix it.",
                            f"{findings.head(p.get('reason') or '')}\n\nIts instruction:\n"
                            f"{findings.head(b.instruction)}",
                            project,
                        ),
                    )
                )
    return found


async def unseen(conn, after: int) -> list[Row]:
    return [r for r in await rows(conn, after) if not await findings.started(conn, r.key)]


async def started_tasks(conn) -> list[str]:
    cur = await conn.execute(
        "SELECT DISTINCT task_id FROM events WHERE type = 'task.started' "
        "AND payload->'finding'->>'source' = %s",
        (NAME,),
    )
    return [r[0] for r in await cur.fetchall()]


async def used_over_started(conn) -> tuple[int, int]:
    """(rsi's tasks Tom used, rsi's tasks started)."""
    ids = await started_tasks(conn)
    used = 0
    for task_id in ids:
        used += outcomes.used_by(await ledger.read(conn, task_id), "tom") > 0
    return used, len(ids)


async def status(conn) -> str:
    """`python -m core rsi`: used over started, the unseen rows, and the
    rows whose task ended or stopped unused."""
    used, total = await used_over_started(conn)
    lines = [f"used {used} of {total} started"]
    stuck = []
    for task_id in await started_tasks(conn):
        if await routines.state_of(conn, task_id) in ("ended", "stopped") and not outcomes.used_by(
            await ledger.read(conn, task_id), "tom"
        ):
            stuck.append(task_id)
    lines.append(f"ended or stopped unused: {', '.join(stuck) or 'none'}")
    registered = await routines.registrations(conn, NAME)
    left = await unseen(conn, registered[0]["id"]) if registered else []
    lines.append(f"unseen: {len(left)}")
    lines += [f"  rsi:{r.id} ({r.source}): {r.finding.text}" for r in left]
    return "\n".join(lines)


async def run(ctx: Context) -> Ran:
    r = ctx.routine
    if ctx.start_project is None or not r.project:
        raise routines.Refused(f"routine {r.name} starts project tasks; none is given")
    async with routines.holding(ctx.conn, f"routine:{r.name}"):
        fresh = await unseen(ctx.conn, await since(ctx.conn))
        run_id, new = None, 0
        for row in fresh:
            run_id, made = await findings.start_fix(
                ctx.conn, row.finding, parent=ctx.objective, routine=r, start_project=ctx.start_project
            )
            new += made
        year, week, _ = ctx.now.isocalendar()
        await notices.request(
            ctx.conn,
            ctx.objective,
            kind="report",
            about_key=f"rsi-week:{year}-W{week:02d}",
            text=await status(ctx.conn),
        )
    if not fresh:
        return Ran(None, "nothing_due", "no unseen rows")
    return Ran(run_id, "started", f"started {new} of {len(fresh)} unseen rows")
