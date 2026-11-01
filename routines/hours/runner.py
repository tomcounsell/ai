"""The `hours` routine: the time Valor worked on each client project placed
on this Mac, from its record (`core/record.py`, `blocks`), written to the
project's Google Calendar as valor@yuda.me (Tom, 2026-11-01), for invoicing.

For each `Spec.here()` whose spec names a `calendar`, the window is the
whole Bangkok days from the later of its `since` and seven days ago, to
today. The calendar's events in the window tagged with the private
extended property `valor_hours=1` are matched one-to-one to the record's
blocks, blocks in start order: a block takes the earliest unassigned tagged
event overlapping it, moved to the block's times with its title kept, and
deletes the other unassigned tagged events overlapping it; a block with none
gets a new tagged event. A tagged event left over is deleted; an untagged
one, made by hand, is never touched. A re-run over an unchanged record
writes nothing.

A new block's title is one call on the light seat (`claude -p`, no tools)
through a gateway this runner starts, on a grant for the run, so it is
metered on the run, from what the block's turns delivered. Events carry no
attendees and are written with `sendUpdates=none`, so no mail leaves.

Each write is a `calendar.written` row on the run; a failed write or title
is `calendar.unwritten`, and the rest go on.
"""

import asyncio
import json
import textwrap
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import ledger, record, routines, tasks, workspace
from core.gateway import TURN_TOKEN, ClaudeLogin, Gateway
from core.record import TZ, Block
from core.routines import Context, Ran
from core.settings import resolve_model, settings

DAYS = 7
TAG = "valor_hours"
TITLE = (
    "You name a block of work on a client's calendar. You are given what Valor, an AI developer, "
    "reported delivering in it. Reply with one line of at most 60 characters naming the value "
    "delivered, in plain words a client reads: no tools, branches, tests, task ids or chat ids."
)


class Failed(Exception):
    """gws or the title call did not do what was asked."""


async def _gws(gws: str, method: str, params: dict[str, Any], body: dict[str, Any] | None = None) -> dict:
    argv = [gws, "calendar", "events", method, "--params", json.dumps(params)]
    if body is not None:
        argv += ["--json", json.dumps(body)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        raise Failed(f"gws: {exc}") from None
    out, err = await proc.communicate()
    text = out.decode(errors="replace")
    if proc.returncode != 0:
        raise Failed(f"gws calendar events {method}: {(err.decode(errors='replace') or text).strip()[:300]}")
    return json.loads(text[text.index("{") :]) if "{" in text else {}


def clip(title: str) -> str:
    """A title of at most 60 characters, cut at a word."""
    return textwrap.shorten(title, 60, placeholder="…", break_on_hyphens=False)


async def _title(claude: str, base: str, home: Path, block: Block) -> str:
    said = "\n\n".join(s[:1500] for s in block.said)[:6000] or "(nothing reported)"
    argv = [claude, "-p", "--model", resolve_model("light"), "--system-prompt", TITLE, "--tools", "",
            "--setting-sources", "", "--strict-mcp-config", "--output-format", "json", said]  # fmt: skip
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "ANTHROPIC_BASE_URL": base,
           "CLAUDE_CONFIG_DIR": str(home), "CLAUDE_CODE_OAUTH_TOKEN": TURN_TOKEN}  # fmt: skip
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, cwd=str(home), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError as exc:
        raise Failed(f"the title call: {exc}") from None
    out, _ = await proc.communicate()
    try:
        got = json.loads(out)
        title = clip(str(got["result"]).strip().splitlines()[0].strip().strip('"'))
    except ValueError, KeyError, IndexError:
        raise Failed(f"the title call returned {out.decode(errors='replace')[:200]!r}") from None
    if proc.returncode != 0 or got.get("is_error") or not title:
        raise Failed(f"the title call failed: {str(got.get('result'))[:200]}")
    return title


def _times(event: dict) -> tuple[datetime, datetime]:
    return (
        datetime.fromisoformat(event["start"]["dateTime"]),
        datetime.fromisoformat(event["end"]["dateTime"]),
    )


def _at(at: datetime) -> dict[str, str]:
    return {"dateTime": at.isoformat(), "timeZone": "Asia/Bangkok"}


async def sync(
    conn, run_id: str, spec: workspace.Spec, now: datetime, gws: str, title: Callable
) -> tuple[int, int]:
    """Write one project's blocks over its window; (written, unwritten)."""
    cal = spec.calendar or {}
    today = now.astimezone(TZ).date()
    first = max(datetime.fromisoformat(cal["since"]).date(), today - timedelta(days=DAYS))
    if first > today:
        return 0, 0
    lo, hi = record._midnight(first), record._midnight(today + timedelta(days=1))
    blocks = await record.blocks(conn, list(await record.reach(conn, spec)), first, today)
    base = {"project": spec.name}
    written = unwritten = 0

    async def wrote(op: str, start: datetime, end: datetime, **more) -> None:
        nonlocal written
        written += 1
        row = {**base, "op": op, "start": start.isoformat(), "end": end.isoformat(), **more}
        await ledger.append(conn, run_id, "calendar.written", row)

    async def failed(op: str, start: datetime, end: datetime, exc: Exception) -> None:
        nonlocal unwritten
        unwritten += 1
        row = {**base, "op": op, "start": start.isoformat(), "end": end.isoformat(), "why": str(exc)}
        await ledger.append(conn, run_id, "calendar.unwritten", row)

    try:
        listed = await _gws(gws, "list", {
            "calendarId": cal["id"], "timeMin": lo.isoformat(), "timeMax": hi.isoformat(),
            "privateExtendedProperty": f"{TAG}=1", "singleEvents": True, "maxResults": 2500,
        })  # fmt: skip
    except Failed as exc:
        await failed("list", lo, hi, exc)
        return written, unwritten
    events = [e for e in listed.get("items") or [] if "dateTime" in (e.get("start") or {})]
    events = sorted((e for e in events if _times(e)[0] >= lo), key=lambda e: _times(e)[0])
    free = {e["id"]: e for e in events}
    ids = {"calendarId": cal["id"], "sendUpdates": "none"}
    for b in blocks:
        over = [e for e in free.values() if _times(e)[0] < b.end and _times(e)[1] > b.start]
        if over:
            kept, *extra = over
            del free[kept["id"]]
            for e in extra:
                del free[e["id"]]
                s, t = _times(e)
                try:
                    await _gws(gws, "delete", {**ids, "eventId": e["id"]})
                    await wrote("delete", s, t, event_id=e["id"])
                except Failed as exc:
                    await failed("delete", s, t, exc)
            if _times(kept) == (b.start, b.end):
                continue
            try:
                await _gws(
                    gws, "patch", {**ids, "eventId": kept["id"]}, {"start": _at(b.start), "end": _at(b.end)}
                )
                await wrote("patch", b.start, b.end, event_id=kept["id"])
            except Failed as exc:
                await failed("patch", b.start, b.end, exc)
            continue
        try:
            body = {
                "summary": await title(b),
                "description": f"Valor's working time, from its work record (tasks {', '.join(b.tasks)}).",
                "start": _at(b.start),
                "end": _at(b.end),
                "extendedProperties": {"private": {TAG: "1"}},
            }
            made = await _gws(gws, "insert", ids, body)
            await wrote("insert", b.start, b.end, event_id=made.get("id"), title=body["summary"])
        except Failed as exc:
            await failed("insert", b.start, b.end, exc)
    for e in free.values():
        s, t = _times(e)
        try:
            await _gws(gws, "delete", {**ids, "eventId": e["id"]})
            await wrote("delete", s, t, event_id=e["id"])
        except Failed as exc:
            await failed("delete", s, t, exc)
    return written, unwritten


def runner(
    *,
    projects: Callable[[], list[workspace.Spec]] = workspace.Spec.here,
    gws: str | None = None,
    claude: str | None = None,
    gateway: Callable[[str | None], Gateway] = lambda dsn: Gateway(dsn, credential=ClaudeLogin()),
):
    """The `hours` runner. `gws`, `claude` and `gateway` default to the
    kernel's own; tests give theirs."""

    async def run(ctx: Context) -> Ran:
        placed = [s for s in projects() if s.calendar]
        if not placed:
            return Ran(None, "nothing_due", "no project on this Mac names a calendar")
        run_id = await tasks.start_child(
            ctx.conn,
            ctx.objective,
            ceiling=ctx.routine.ceiling,
            marker={"objective": ctx.routine.name},
            by="routine",
            via=f"python -m core routine {ctx.routine.name}",
            instruction=f"routine {ctx.routine.name}: {ctx.now.astimezone(TZ).date()}",
            model=resolve_model(ctx.routine.model),
            routine=ctx.routine.name,
        )
        home = Path(settings.work_dir).expanduser() / run_id / "titles"
        home.mkdir(parents=True, exist_ok=True)
        gw = gateway(ctx.dsn)
        await gw.start()
        base = gw.issue(run_id, "titles")
        written = unwritten = 0
        try:
            # The gateway records this lock as each call's holder.
            async with routines.holding(ctx.conn, f"run:{run_id}"):
                for spec in placed:
                    w, u = await sync(
                        ctx.conn, run_id, spec, ctx.now, gws or settings.gws,
                        lambda b: _title(claude or settings.claude, base, home, b),
                    )  # fmt: skip
                    written, unwritten = written + w, unwritten + u
                await gw.drain(run_id)
        finally:
            await gw.close()
        summary = f"{len(placed)} calendars: {written} written, {unwritten} not written"
        return Ran(run_id, "finished", summary)

    return run
