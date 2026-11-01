"""Valor's hours on a client project: the record's blocks (`core/record.py`,
`blocks` and the `hours` verb) and the `hours` routine writing them to the
project's calendar (`routines/hours`). Google Calendar is a stub `gws` that
keeps events in a JSON file; the title model is a stub `claude` calling the
routine's own gateway, over a local upstream. Live spend: none."""

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from aiohttp import web
from psycopg.types.json import Jsonb

from core import db, ledger, record, routines, tasks, workspace
from core.gateway import Gateway
from core.record import TZ
from routines.hours import runner as hours
from tests import bridges
from tests.ports import listen

pytestmark = pytest.mark.spend(usd=0)

SPEC = 'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = [{chats}]\n{more}'


def at(text: str) -> datetime:
    """A Bangkok time, `MM-DD HH:MM` in November 2026 (`10-31 ...` for October)."""
    return datetime.strptime(f"2026-{text}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


async def row(conn, task: str, kind: str, when: datetime, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO events (task_id, type, payload, at) VALUES (%s, %s, %s, %s)",
        (task, kind, Jsonb(payload), when),
    )


async def turn(conn, task: str, start: str, end: str | None, said: str = "", reason: str | None = None,
               calls: tuple[tuple[str, str, dict], ...] = ()) -> str:  # fmt: skip
    """A turn of `task` from `start` to `end` (None: still running), with
    its gateway rows `(type, time, extra payload)`."""
    turn_id = ledger.new_id()
    await row(conn, task, "turn.started", at(start), {"turn_id": turn_id})
    for kind, when, extra in calls:
        await row(conn, task, kind, at(when), {"turn_id": turn_id, "call_id": ledger.new_id(), **extra})
    if end is not None:
        ended = {"turn_id": turn_id, "outcome": "done", "result": {"text": said}}
        if reason:
            ended |= {"outcome": "interrupted", "reason": reason, "result": {}}
        await row(conn, task, "turn.ended", at(end), ended)
    return turn_id


def shown(blocks) -> list[tuple[str, str]]:
    return [(b.start.strftime("%m-%d %H:%M"), b.end.strftime("%m-%d %H:%M")) for b in blocks]


def test_turns_become_blocks_merged_split_rounded_and_widened(fresh):
    async def go():
        async with await db.connect(fresh) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="work", project={"name": "a"}))
            u = await tasks.start(conn, tasks.Brief(instruction="more work", project={"name": "a"}))
            # Overlapping turns of two tasks count once; a 20-minute gap joins.
            await turn(conn, t, "11-03 10:00", "11-03 10:30", "first")
            await turn(conn, u, "11-03 10:15", "11-03 10:50", "second")
            await turn(conn, t, "11-03 11:10", "11-03 11:20")
            # 21 minutes apart: its own block, rounded out to the 0.1h grid.
            await turn(conn, t, "11-03 11:41", "11-03 11:50")
            # Under 0.2h: widened at its end.
            await turn(conn, t, "11-04 14:01", "11-04 14:03")
            # Across midnight: a block on each day.
            await turn(conn, t, "11-04 23:30", "11-05 00:40")
            # Short before midnight: widened backwards, never into the next day.
            await turn(conn, t, "11-05 23:57", "11-05 23:59")
            # Ended by a kernel restart at 15:00 with a call left open, which
            # recovery charged at the restart: it ends at that call's open.
            restarted = "kernel restarted"
            await turn(conn, t, "11-06 09:00", "11-06 15:00", reason=restarted, calls=(
                ("gateway.opened", "11-06 09:20", {}), ("gateway.charged", "11-06 09:25", {}),
                ("gateway.opened", "11-06 09:40", {}),
                ("gateway.charged", "11-06 15:00", {"estimated": True, "reason": restarted}),
                ("turn.reaped", "11-06 15:00", {}),
            ))  # fmt: skip
            # Ended by a restart having made no call: left out.
            await turn(conn, t, "11-07 09:00", "11-07 12:00", reason=restarted)
            # Still running: left out until it ends.
            await turn(conn, t, "11-08 09:00", None)
            # Before the range asked for: left out.
            await turn(conn, t, "10-31 10:00", "10-31 11:00")
            got = await record.blocks(conn, [t, u], at("11-01 00:00").date(), at("11-08 00:00").date())
            again = await record.blocks(conn, [t, u], at("11-01 00:00").date(), at("11-08 00:00").date())
            return got, again, t, u

    got, again, t, u = asyncio.run(go())
    assert shown(got) == [
        ("11-03 10:00", "11-03 11:24"),
        ("11-03 11:36", "11-03 11:54"),
        ("11-04 14:00", "11-04 14:12"),
        ("11-04 23:30", "11-05 00:00"),
        ("11-05 00:00", "11-05 00:42"),
        ("11-05 23:48", "11-06 00:00"),
        ("11-06 09:00", "11-06 09:42"),
    ]
    assert [b.shown()["hours"] for b in got] == [1.4, 0.3, 0.2, 0.5, 0.7, 0.2, 0.7]
    assert got[0].tasks == [t, u] and got[0].said == ["first", "second"]
    assert shown(again) == shown(got)


def test_the_hours_verb_reads_the_projects_tasks_and_chat_sessions_only(fresh, tmp_path):
    with bridges.operator(tmp_path):
        projects = tmp_path / "projects"
        (projects / "a.toml").write_text(SPEC.format(name="a", chats='"telegram:-1001"', more=""))
        (projects / "b.toml").write_text(SPEC.format(name="b", chats='"telegram:-1003"', more=""))

        async def go():
            async with await db.connect(fresh) as conn:
                chat = lambda c: {"channel": "telegram", "chat_id": c}
                s = await tasks.start(
                    conn, tasks.Brief(instruction="chat -1001", project={"name": "a"}, chat=chat("-1001"))
                )
                t = await tasks.start_child(conn, s, instruction="build it", project={"name": "a"})
                b = await tasks.start(conn, tasks.Brief(instruction="b's work", project={"name": "b"}))
                sb = await tasks.start(
                    conn, tasks.Brief(instruction="chat -1003", project={"name": "b"}, chat=chat("-1003"))
                )
                await turn(conn, s, "11-03 09:00", "11-03 09:10")
                await turn(conn, t, "11-03 13:00", "11-03 14:00")
                await turn(conn, b, "11-03 16:00", "11-03 17:00")
                await turn(conn, sb, "11-03 18:00", "11-03 19:00")
                return s, t

        s, t = asyncio.run(go())
        got = asyncio.run(record.answer(fresh, s, "hours", {"since": "2026-11-01", "until": "2026-11-05"}))
        assert [(g["start"][11:16], g["end"][11:16], g["hours"], g["tasks"]) for g in got] == [
            ("09:00", "09:12", 0.2, [s]),
            ("13:00", "14:00", 1.0, [t]),
        ]
        with pytest.raises(record.NotFound):
            asyncio.run(record.answer(fresh, s, "hours", {"since": "November"}))


def test_a_spec_names_its_calendar_by_id_and_first_day():
    base = {"name": "a", "repo": "unused", "kind": "plain", "suite": "true"}
    spec = workspace.Spec.from_dict({**base, "calendar": {"id": "cal-a", "since": date(2026, 11, 1)}})
    assert spec.calendar == {"id": "cal-a", "since": "2026-11-01"}
    assert workspace.Spec.from_dict(base).calendar is None
    for bad in ({"id": "cal-a"}, {"id": "", "since": date(2026, 11, 1)}, {"id": "c", "since": "2026-11-01"}):
        with pytest.raises(workspace.Refused):
            workspace.Spec.from_dict({**base, "calendar": bad})


GWS = r"""#!{python}
import json, pathlib, sys
store = pathlib.Path({store!r})
cals = json.loads(store.read_text()) if store.exists() else {{}}
_, _, method = sys.argv[1:4]
params = json.loads(sys.argv[sys.argv.index("--params") + 1])
body = json.loads(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else None
log = pathlib.Path({log!r})
with log.open("a") as f:
    f.write(json.dumps({{"method": method, "params": params, "body": body}}) + "\n")
if params["calendarId"] == "broken":
    print("calendar not found", file=sys.stderr)
    sys.exit(1)
events = cals.setdefault(params["calendarId"], {{}})
if method == "list":
    key, _, value = params["privateExtendedProperty"].partition("=")
    found = [e for e in events.values()
             if ((e.get("extendedProperties") or {{}}).get("private") or {{}}).get(key) == value
             and e["end"]["dateTime"] > params["timeMin"] and e["start"]["dateTime"] < params["timeMax"]]
    print(json.dumps({{"items": found}}))
elif method == "insert":
    body["id"] = f"ev{{sum(len(c) for c in cals.values()) + len(log.read_text().splitlines())}}"
    events[body["id"]] = body
    print("Using account valor\n" + json.dumps(body))
elif method == "patch":
    events[params["eventId"]].update(body)
    print(json.dumps(events[params["eventId"]]))
elif method == "delete":
    del events[params["eventId"]]
store.write_text(json.dumps(cals))
"""

CLAUDE = r"""#!{python}
import json, os, sys, urllib.request
model = sys.argv[sys.argv.index("--model") + 1]
body = {{"model": model, "max_tokens": 64, "messages": [{{"role": "user", "content": sys.argv[-1]}}]}}
req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", json.dumps(body).encode(),
                             {{"content-type": "application/json"}})
got = json.loads(urllib.request.urlopen(req).read())
print(json.dumps({{"result": got["content"][0]["text"], "is_error": False}}))
"""


class Calendar:
    """The stub gws's calendars and calls, and the title upstream's prompts."""

    def __init__(self, tmp: Path):
        self.store, self.log = tmp / "calendars.json", tmp / "gws.jsonl"
        self.gws, self.claude = tmp / "gws", tmp / "claude"
        self.gws.write_text(GWS.format(python=sys.executable, store=str(self.store), log=str(self.log)))
        self.claude.write_text(CLAUDE.format(python=sys.executable))
        self.gws.chmod(0o755)
        self.claude.chmod(0o755)
        self.prompts: list[str] = []

    def put(self, cal: str, event_id: str, start: str, end: str, summary: str, tagged: bool) -> None:
        cals = json.loads(self.store.read_text()) if self.store.exists() else {}
        e = {"id": event_id, "summary": summary, "start": {"dateTime": at(start).isoformat()},
             "end": {"dateTime": at(end).isoformat()}}  # fmt: skip
        if tagged:
            e["extendedProperties"] = {"private": {"valor_hours": "1"}}
        cals.setdefault(cal, {})[event_id] = e
        self.store.write_text(json.dumps(cals))

    def events(self, cal: str) -> list[tuple[str, str, str, bool]]:
        found = json.loads(self.store.read_text()).get(cal, {}).values()
        out = []
        for e in found:
            s = datetime.fromisoformat(e["start"]["dateTime"]).astimezone(TZ).strftime("%m-%d %H:%M")
            t = datetime.fromisoformat(e["end"]["dateTime"]).astimezone(TZ).strftime("%m-%d %H:%M")
            out.append((s, t, e["summary"], "extendedProperties" in e))
        return sorted(out)

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    async def upstream(self) -> tuple[web.AppRunner, str]:
        async def handle(request: web.Request) -> web.Response:
            body = await request.json()
            self.prompts.append(body["messages"][0]["content"])
            return web.json_response({
                "id": "msg", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": f"Title {len(self.prompts)}"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 3},
            })  # fmt: skip

        app = web.Application()
        app.router.add_post("/v1/messages", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", listen())
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


class _Gateway(Gateway):
    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        return await super().start(host, listen())


def test_the_routine_writes_each_block_once_and_keeps_hand_made_events(fresh, tmp_path):
    cal = Calendar(tmp_path)
    with bridges.operator(tmp_path):
        projects = tmp_path / "projects"
        calendar = lambda c: f'calendar = {{id = "{c}", since = 2026-11-01}}\n'
        (projects / "a-broken.toml").write_text(
            SPEC.format(name="broken", chats='"telegram:-1009"', more=calendar("broken"))
        )
        (projects / "a.toml").write_text(
            SPEC.format(name="a", chats='"telegram:-1001"', more=calendar("cal-a"))
        )
        (projects / "b.toml").write_text(SPEC.format(name="b", chats='"telegram:-1003"', more=""))
        (projects / "z.toml").write_text(
            SPEC.format(
                name="z", chats='"telegram:-1004"', more=calendar("cal-z") + 'machine = "elsewhere"\n'
            )
        )
        # Made by hand: never touched. October's tagged event: before `since`.
        cal.put("cal-a", "hand", "11-03 10:30", "11-03 11:00", "Call with Tom", tagged=False)
        cal.put("cal-a", "october", "10-30 10:00", "10-30 11:00", "October work", tagged=True)
        # A tagged event whose block is gone.
        cal.put("cal-a", "gone", "11-02 14:00", "11-02 15:00", "Gone", tagged=True)
        # One tagged event overlapping two blocks.
        cal.put("cal-a", "wide", "11-05 08:00", "11-05 12:00", "Wide", tagged=True)

        async def go():
            runner, url = await cal.upstream()
            run = hours.runner(
                gws=str(cal.gws), claude=str(cal.claude), gateway=lambda d: _Gateway(d, upstream=url)
            )
            chat = {"channel": "telegram", "chat_id": "-1001"}
            async with await db.connect(fresh) as conn:
                s = await tasks.start(
                    conn, tasks.Brief(instruction="You are in the chat (chat id -1001)", project={"name": "a"},
                                      chat=chat),
                )  # fmt: skip
                t = await tasks.start_child(conn, s, instruction="build it", project={"name": "a"})

                async def fire(now: str) -> str:
                    when = at(now).astimezone(UTC)
                    return await routines.run(conn, "hours", {"hours": run}, now=when, dsn=fresh)

                await turn(conn, s, "11-01 23:30", "11-02 00:30", "Answered Kim about the release date")
                await turn(conn, s, "11-03 10:00", "11-03 10:30", "Fixed the login page for Kim")
                await turn(conn, t, "11-03 11:00", "11-03 11:10", "Shipped the export button")
                running = await turn(conn, t, "11-04 09:00", None)
                await turn(conn, t, "11-05 08:00", "11-05 08:30", "Planned the reports page")
                await turn(conn, t, "11-05 11:00", "11-05 11:30", "Built the reports page")
                first = await fire("11-06 12:00")
                one = cal.events("cal-a")
                calls = len(cal.calls())
                await fire("11-06 12:30")
                rerun = cal.calls()[calls:]
                # A later turn fills the gap between two blocks; the running turn ends.
                await turn(conn, t, "11-03 10:25", "11-03 11:05", "Reviewed the export with Kim")
                await row(conn, t, "turn.ended", at("11-04 09:40"),
                          {"turn_id": running, "outcome": "done", "result": {"text": "Fixed the invoice PDF"}})  # fmt: skip
                await fire("11-07 12:00")
                three = cal.events("cal-a")
                calls = len(cal.calls())
                # Days later the window starts on 11-02: the block across its start is written once.
                await fire("11-09 12:00")
                last = cal.calls()[calls:]
                run_rows = []
                for child in await tasks.children(
                    conn, (await routines.ensure(conn, routines.load("hours")))[0]
                ):
                    run_rows += await ledger.read(conn, child)
            await runner.cleanup()
            return first, one, rerun, three, last, run_rows

        first, one, rerun, three, last, run_rows = asyncio.run(go())

    titled = {
        ("11-01 23:30", "11-02 00:00"), ("11-02 00:00", "11-02 00:30"), ("11-03 10:00", "11-03 10:30"),
        ("11-03 11:00", "11-03 11:12"), ("11-05 08:00", "11-05 08:30"), ("11-05 11:00", "11-05 11:30"),
    }  # fmt: skip
    assert one == sorted([
        ("10-30 10:00", "10-30 11:00", "October work", True),
        ("11-01 23:30", "11-02 00:00", "Title 1", True),
        ("11-02 00:00", "11-02 00:30", "Title 2", True),
        ("11-03 10:00", "11-03 10:30", "Title 3", True),
        ("11-03 10:30", "11-03 11:00", "Call with Tom", False),
        ("11-03 11:00", "11-03 11:12", "Title 4", True),
        ("11-05 08:00", "11-05 08:30", "Wide", True),
        ("11-05 11:00", "11-05 11:30", "Title 5", True),
    ])  # fmt: skip
    assert {(s, t) for s, t, _, tagged in one if tagged and s >= "11"} == titled
    writes = [c for c in cal.calls() if c["method"] != "list"]
    assert all(c["params"]["sendUpdates"] == "none" for c in writes)
    assert all("attendees" not in (c["body"] or {}) for c in writes)
    assert not any(c["params"]["calendarId"] == "cal-z" for c in cal.calls())
    # Titles come from what the turns delivered, never the chat session's instruction.
    assert "Answered Kim about the release date" in cal.prompts[0] and "-1001" not in "".join(cal.prompts)
    assert [c["method"] for c in rerun] == ["list", "list"]  # the broken calendar's, then a's
    assert three == sorted([
        ("10-30 10:00", "10-30 11:00", "October work", True),
        ("11-01 23:30", "11-02 00:00", "Title 1", True),
        ("11-02 00:00", "11-02 00:30", "Title 2", True),
        ("11-03 10:00", "11-03 11:12", "Title 3", True),
        ("11-03 10:30", "11-03 11:00", "Call with Tom", False),
        ("11-04 09:00", "11-04 09:42", "Title 6", True),
        ("11-05 08:00", "11-05 08:30", "Wide", True),
        ("11-05 11:00", "11-05 11:30", "Title 5", True),
    ])  # fmt: skip
    assert [c["method"] for c in last] == ["list", "list"]
    assert [c["params"]["timeMin"][:10] for c in last] == ["2026-11-02", "2026-11-02"]
    assert len(cal.prompts) == 6
    written = [r["payload"] for r in run_rows if r["type"] == "calendar.written"]
    unwritten = [r["payload"] for r in run_rows if r["type"] == "calendar.unwritten"]
    assert {(w["op"], w["event_id"]) for w in written if w["op"] != "insert"} >= {
        ("delete", "gone"), ("patch", "wide"),
    }  # fmt: skip
    assert unwritten and {u["project"] for u in unwritten} == {"broken"}
    assert all(u["op"] == "list" for u in unwritten)
    charged = [r["payload"] for r in run_rows if r["type"] == "gateway.charged"]
    assert len(charged) == 6 and {c["turn_id"] for c in charged} == {"titles"}
    assert "hours" in first
