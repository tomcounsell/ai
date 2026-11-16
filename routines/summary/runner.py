"""The `summary` routine: a page per project on where it stands, rebuilt
on a night after work on it, on top of the previous summary (Macro's
nightly memory, issue #3679), for Valor's own sessions (Tom, 2026-11-16:
not posted to the team or to Tom).

For each `Spec.here()`, the window is the Bangkok days after the last
summary's `through` (at most `DAYS` back) to yesterday, and its material
is the record's blocks over the project's reach (`record.blocks`): each
day's hours, the instructions of the tasks worked on, and what their turns
said. A project with no block in its window gets no call and no row, so a
quiet week adds nothing; no project with one starts no run.

Otherwise one run, and per project one call on the routine's seat through a
gateway this runner starts, metered on the run, given the previous summary
and the window. Its reply is a `summary.written` row on the run (`project`,
`through`, `text`); the latest is the one each of the project's sessions'
Briefs carries (`core/record.py`, `summary`). A failed call is
`summary.unwritten`, so `through` stays and the next night covers the days
again; the other projects go on.
"""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core import ledger, record, routines, tasks, workspace
from core.gateway import ClaudeLogin, Gateway, Unanswered, ask
from core.record import TZ, Block
from core.routines import Context, Ran
from core.settings import resolve_model, settings

DAYS = 7
SAID, MATERIAL, PREVIOUS = 1500, 40000, 12000
SYSTEM = """You keep Valor's private summary of one project: what Valor, an AI developer and product owner, reads first when it starts work on the project. You are given the previous summary and the record of the work since. Write the new summary in markdown, about one page, and nothing else.

## Where it stands
Rewritten each time: the open work and its stage, what is waiting on whom and since when, the decisions in force and their reasons, and how Tom wants things done on this project. Keep what is still true, drop what the record no longer supports, and leave out detail a search of the record finds when needed. State only what the material says; never guess a role, a date or a reason.

## Progress
Entries newest first, each headed by its date or date range (YYYY-MM-DD, or YYYY-MM-DD to YYYY-MM-DD), each as long as the progress in it is large. A day that delivered, merged or decided something gets its own entry. A day of little progress joins the latest entry when that entry is a stretch of little progress begun within seven days; otherwise it starts one. Never one entry per day for its own sake. Compress older entries to a line or two; drop what is no longer worth knowing."""


def _window(through: date | None, today: date) -> tuple[date, date]:
    first = today - timedelta(days=DAYS)
    if through is not None:
        first = max(first, through + timedelta(days=1))
    return first, today - timedelta(days=1)


def material(previous: dict[str, Any] | None, first: date, last: date, found: list[Block], bodies) -> str:
    """What the call is given: the previous summary, then each block."""
    lines = [f"# Previous summary (through {previous['through']})", "", previous["text"][:PREVIOUS]] if previous else [
        "# Previous summary", "", "(none: this is the first)"]  # fmt: skip
    lines += ["", f"# The record from {first} to {last}"]
    for b in found:
        lines += ["", f"## {b.start.date()} {b.start:%H:%M}-{b.end:%H:%M} ({b.shown()['hours']}h)"]
        for t in b.tasks:
            body = bodies.get(t) or {}
            if not body.get("chat"):  # a chat session's instruction is its chat's id
                lines.append(f"Task {t}: {str(body.get('instruction') or '')[:SAID]}")
        lines += [s[:SAID] for s in b.said]
    return "\n".join(lines)[:MATERIAL]


def runner(
    *,
    projects: Callable[[], list[workspace.Spec]] = workspace.Spec.here,
    claude: str | None = None,
    gateway: Callable[[str | None], Gateway] = lambda dsn: Gateway(dsn, credential=ClaudeLogin()),
):
    """The `summary` runner. `claude` and `gateway` default to the kernel's
    own; tests give theirs."""

    async def run(ctx: Context) -> Ran:
        today = ctx.now.astimezone(TZ).date()
        due = []
        for spec in projects():
            previous = await record.latest_summary(ctx.conn, spec.name)
            first, last = _window(date.fromisoformat(previous["through"]) if previous else None, today)
            bodies = await record.reach(ctx.conn, spec)
            found = await record.blocks(ctx.conn, list(bodies), first, last) if first <= last else []
            if found:
                due.append((spec, last, material(previous, first, last, found, bodies)))
        if not due:
            return Ran(None, "nothing_due", "no project placed here had work since its summary")
        run_id = await tasks.start_child(
            ctx.conn,
            ctx.objective,
            ceiling=ctx.routine.ceiling,
            marker={"objective": ctx.routine.name},
            by="routine",
            via=f"python -m core routine {ctx.routine.name}",
            instruction=f"routine {ctx.routine.name}: {today}",
            model=resolve_model(ctx.routine.model),
            routine=ctx.routine.name,
        )
        home = Path(settings.work_dir).expanduser() / run_id / "summaries"
        home.mkdir(parents=True, exist_ok=True)
        gw = gateway(ctx.dsn)
        await gw.start()
        base = gw.issue(run_id, "summaries")
        written = []
        try:
            # The gateway records this lock as each call's holder.
            async with routines.holding(ctx.conn, f"run:{run_id}"):
                for spec, last, text in due:
                    try:
                        said = await ask(
                            claude or settings.claude, base, home, ctx.routine.model, SYSTEM, text
                        )
                    except Unanswered as exc:
                        await ledger.append(
                            ctx.conn, run_id, "summary.unwritten", {"project": spec.name, "why": str(exc)}
                        )
                        continue
                    row = {"project": spec.name, "through": last.isoformat(), "text": said}
                    await ledger.append(ctx.conn, run_id, "summary.written", row)
                    written.append(spec.name)
                await gw.drain(run_id)
        finally:
            await gw.close()
        return Ran(
            run_id, "finished", f"{len(written)} of {len(due)} summaries written: {', '.join(written)}"
        )

    return run
