"""Calls from calendar invites (docs/plans/invite-calls.md): the `invites`
routine matches each invite with a Meet link to one project by its specs'
`invites`, accepts it once on the series, and wakes the project's call
chat once per event and start, from 12 minutes before it to its end. Google
Calendar is a stub `gws` keeping events in a JSON file. Live spend: none."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import db, routines, tasks, workspace
from routines.invites import runner as invites
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)

START = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
LINK = "https://meet.google.com/abc-defg-hij"
VALOR = {"email": "valor@yuda.me", "self": True, "responseStatus": "needsAction"}
SPEC = 'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = [{chats}]\n{more}'

GWS = r"""#!{python}
import json, pathlib, sys
store = pathlib.Path({store!r})
events = json.loads(store.read_text()) if store.exists() else {{}}
method = sys.argv[3]
params = json.loads(sys.argv[sys.argv.index("--params") + 1])
body = json.loads(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else None
with pathlib.Path({log!r}).open("a") as f:
    f.write(json.dumps({{"method": method, "params": params, "body": body}}) + "\n")
if method == "list":
    found = [e for e in events.values() if "recurrence" not in e
             and e["end"]["dateTime"] > params["timeMin"] and e["start"]["dateTime"] < params["timeMax"]]
    print(json.dumps({{"items": sorted(found, key=lambda e: e["start"]["dateTime"])}}))
elif method == "get":
    print(json.dumps(events[params["eventId"]]))
elif method == "patch":
    for e in events.values():
        if e["id"] == params["eventId"] or e.get("recurringEventId") == params["eventId"]:
            e.update(body)
    print(json.dumps(events[params["eventId"]]))
store.write_text(json.dumps(events))
"""


class Calendar:
    def __init__(self, tmp: Path):
        self.store, self.log, self.gws = tmp / "events.json", tmp / "gws.jsonl", tmp / "gws"
        self.gws.write_text(GWS.format(python=sys.executable, store=str(self.store), log=str(self.log)))
        self.gws.chmod(0o755)

    def put(self, event_id: str, *, organizer: str, attendees=(), start=START, minutes=30, me=VALOR, **more):
        events = json.loads(self.store.read_text()) if self.store.exists() else {}
        people = [{"email": a, "responseStatus": "accepted"} for a in attendees] + ([dict(me)] if me else [])
        events[event_id] = {
            "id": event_id, "summary": f"Call {event_id}", "status": "confirmed", "hangoutLink": LINK,
            "organizer": {"email": organizer}, "attendees": people,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat()}, **more,
        }  # fmt: skip
        self.store.write_text(json.dumps(events))

    def mine(self, event_id: str) -> str:
        event = json.loads(self.store.read_text())[event_id]
        return next(a["responseStatus"] for a in event["attendees"] if a.get("self"))

    def patches(self) -> list[dict]:
        found = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c for c in found if c["method"] == "patch"]


@pytest.fixture
def setup(tmp_path):
    """Three projects on this Mac (`valor` with a board, `acme` naming its
    call chat, `globex` with two group chats) and one elsewhere."""
    cal = Calendar(tmp_path)
    with bridges.operator(tmp_path):
        p = tmp_path / "projects"
        (p / "valor.toml").write_text(
            SPEC.format(
                name="valor",
                chats='"slack:T0A/C0VALOR", "slack:T0A/U0TOM"',
                more='board = "slack:T0A/C0VALOR"\n',
            )
        )
        (p / "acme.toml").write_text(
            SPEC.format(
                name="acme",
                chats='"slack:T0A/U0DM", "slack:T0A/C0ACMEDEV", "slack:T0A/C0ACMECALL"',
                more='invites = ["@acme.com", "boss@gmail.com"]\ncall_chat = "slack:T0A/C0ACMECALL"\n',
            )
        )
        (p / "globex.toml").write_text(
            SPEC.format(
                name="globex",
                chats='"telegram:577", "telegram:-1001", "slack:T0A/C0GLOBEX"',
                more='invites = ["@globex.com", "@gmail.com"]\n',
            )
        )
        (p / "far.toml").write_text(
            SPEC.format(
                name="far", chats='"telegram:-1009"', more='invites = ["@far.com"]\nmachine = "elsewhere"\n'
            )
        )
        yield cal  # fmt: skip


def fire(dsn, cal, now: datetime) -> str:
    async def go():
        async with await db.connect(dsn) as conn:
            return await routines.run(conn, "invites", {"invites": invites.runner(gws=str(cal.gws))}, now=now)

    return asyncio.run(go())


async def _wakes(dsn) -> list[dict]:
    async with await db.connect(dsn) as conn:
        found = await (
            await conn.execute(
                "SELECT e.task_id, e.payload FROM events e WHERE e.type = 'child.reported' "
                "AND e.payload->>'about_key' LIKE 'invite:%%' ORDER BY e.id"
            )
        ).fetchall()
        out = []
        for task_id, payload in found:
            chat = (await tasks.brief(conn, task_id)).chat
            out.append({"chat": f"{chat['channel']}:{chat['chat_id']}", **payload})
        return out


def wakes(dsn) -> list[dict]:
    return asyncio.run(_wakes(dsn))


def test_the_spec_names_its_invites_and_call_chat():
    base = {"name": "a", "repo": "unused", "kind": "plain", "suite": "true"}
    chats = ["slack:T0A/U0DM", "telegram:-100", "slack:T0A/c0b"]
    spec = workspace.Spec.from_dict({**base, "chats": chats, "invites": ["Boss@Acme.com", "@acme.com"]})
    assert spec.invites == ("boss@acme.com", "@acme.com") and spec.call_chat == "telegram:-100"
    assert (
        workspace.Spec.from_dict({**base, "chats": chats, "board": "slack:T0A/c0b"}).call_chat
        == "slack:T0A/C0B"
    )
    named = workspace.Spec.from_dict(
        {**base, "chats": chats, "board": "slack:T0A/C0B", "call_chat": "telegram:-100"}
    )
    assert named.call_chat == "telegram:-100"
    assert workspace.Spec.from_dict({**base, "chats": ["slack:T0A/U0DM"]}).call_chat is None
    for bad in ({"invites": ["acme.com"]}, {"invites": ["a@"]}, {"call_chat": "slack:T0A/U0DM"},
                {"call_chat": "telegram:-999"}):  # fmt: skip
        with pytest.raises(workspace.Refused):
            workspace.Spec.from_dict({**base, "chats": chats, **bad})


def test_an_invite_is_matched_by_its_people_never_yudames_own(setup):
    specs = workspace.Spec.all()

    def project(organizer, *attendees):
        return invites.match({"organizer": {"email": organizer}, "attendees": [{"email": a} for a in attendees]},
                             specs)  # fmt: skip

    # Tom organizes from his own or a Yudame address: the attendees decide.
    assert project(bridges.OPERATOR_EMAIL, "jo@acme.com") == ("acme", None)
    assert project("ops@yuda.me", "jo@globex.com", "valor@yuda.me") == ("globex", None)
    # An address beats a domain another spec lists.
    assert project("boss@gmail.com") == ("acme", None)
    assert project("someone@gmail.com") == ("globex", None)
    # None, or two projects: Valor's own, saying why.
    name, why = project(bridges.OPERATOR_EMAIL, "ops@yuda.me")
    assert name == "valor" and "matched no project" in why
    name, why = project("jo@acme.com", "kim@globex.com")
    assert name == "valor" and "acme and globex" in why


def test_an_invite_is_accepted_once_on_its_series_and_others_are_left(setup, fresh):
    cal = setup
    cal.put("one", organizer="jo@acme.com", start=START + timedelta(days=1))
    cal.put(
        "series", organizer="jo@acme.com", start=START + timedelta(days=2), recurrence=["RRULE:FREQ=WEEKLY"]
    )
    cal.put("series_1", organizer="jo@acme.com", start=START + timedelta(days=2), recurringEventId="series")
    cal.put("declined", organizer="jo@acme.com", me={**VALOR, "responseStatus": "declined"})
    cal.put("maybe", organizer="jo@acme.com", me={**VALOR, "responseStatus": "tentative"})
    cal.put("nolink", organizer="jo@acme.com", hangoutLink=None)
    cal.put("cancelled", organizer="jo@acme.com", status="cancelled")
    cal.put("notme", organizer="jo@acme.com", attendees=["kim@acme.com"], me=None)
    cal.put("far", organizer="jo@far.com")
    fire(fresh, cal, START - timedelta(days=1))
    assert cal.mine("one") == cal.mine("series") == cal.mine("series_1") == "accepted"
    assert {p["params"]["eventId"] for p in cal.patches()} == {"one", "series"}
    assert all(p["params"]["sendUpdates"] == "all" for p in cal.patches())
    assert (cal.mine("declined"), cal.mine("maybe")) == ("declined", "tentative")
    assert cal.mine("nolink") == cal.mine("cancelled") == cal.mine("far") == "needsAction"
    # A second run patches nothing, even when Google still says needsAction.
    cal.put("one", organizer="jo@acme.com", start=START + timedelta(days=1))
    fire(fresh, cal, START - timedelta(days=1) + timedelta(minutes=5))
    assert len(cal.patches()) == 2


def test_the_call_chat_is_woken_once_inside_the_window_and_again_after_a_move(setup, fresh):
    cal = setup
    cal.put("acme1", organizer="jo@acme.com")
    cal.put("globex1", organizer="kim@globex.com", start=START + timedelta(hours=2))
    cal.put(
        "tom1", organizer=bridges.OPERATOR_EMAIL, attendees=["ops@yuda.me"], start=START + timedelta(hours=4)
    )
    fire(fresh, cal, START - timedelta(minutes=13))
    assert wakes(fresh) == []
    fire(fresh, cal, START - timedelta(minutes=8))
    (w,) = wakes(fresh)
    assert w["chat"] == "slack:T0A/C0ACMECALL" and w["kind"] == "call"
    assert (
        LINK in w["text"] and "starts 2026-11-12T14:00:00+00:00" in w["text"] and "jo@acme.com" in w["text"]
    )
    # A later run inside the window: not again.
    fire(fresh, cal, START - timedelta(minutes=3))
    assert len(wakes(fresh)) == 1
    # Moved: a new start wakes again.
    cal.put("acme1", organizer="jo@acme.com", start=START + timedelta(minutes=30))
    fire(fresh, cal, START + timedelta(minutes=25))
    assert [x["chat"] for x in wakes(fresh)] == ["slack:T0A/C0ACMECALL"] * 2
    # A late run, after the start but before the end, still wakes; the
    # board's chat, else the first group chat; no match goes to valor's board.
    fire(fresh, cal, START + timedelta(hours=2, minutes=1))
    fire(fresh, cal, START + timedelta(hours=4, minutes=29))
    found = wakes(fresh)
    assert [x["chat"] for x in found[2:]] == ["telegram:-1001", "slack:T0A/C0VALOR"]
    assert "matched no project" in found[3]["text"]
    # After the end: nothing.
    cal.put("acme2", organizer="jo@acme.com", start=START + timedelta(hours=5))
    fire(fresh, cal, START + timedelta(hours=5, minutes=31))
    assert len(wakes(fresh)) == 4


def test_a_woken_chat_session_ended_between_runs_is_not_woken_again(setup, fresh):
    cal = setup
    cal.put("acme1", organizer="jo@acme.com")
    fire(fresh, cal, START - timedelta(minutes=10))
    assert len(wakes(fresh)) == 1

    async def end_it():
        async with await db.connect(fresh) as conn:
            sessions = await (
                await conn.execute(
                    "SELECT task_id FROM events WHERE type = 'child.reported' AND payload->>'about_key' LIKE 'invite:%%'"
                )
            ).fetchall()
            for (s,) in sessions:
                await tasks.stop(conn, s, reason="ended", by="tom", via="chat")

    asyncio.run(end_it())
    fire(fresh, cal, START - timedelta(minutes=5))
    fire(fresh, cal, START)
    assert len(wakes(fresh)) == 1


def test_the_routine_is_registered_with_the_kernel():
    from core.__main__ import _routine_runners

    r = routines.load("invites")
    assert r.runner in _routine_runners() and r.interval == 300 and r.ceiling == "act"
    assert invites.WAKE == timedelta(minutes=12)
