"""A chat session hands a message to another project's chat session, in the
asker's words, who said it, where and when as received (Tom, 2026-11-24;
docs/plans/f09b06da-hand-on.md). Four Macs share one test database here; the
ssh to another Mac runs that Mac's `core receive` in this process, and its
kernel's binding runs with `settings.machine` set to it."""

import asyncio
import contextlib
import io
import sys
import types
import uuid
from contextlib import contextmanager

import pytest

from core import __main__ as kernel_main
from core import broker, db, intake, ledger, machine, session, tasks
from tests import bridges
from tests.test_act import acting
from tests.test_projects import started
from tools import chat_tasks, valor_record

pytestmark = pytest.mark.spend(usd=0)

COWBOY, PIRATE, CAPTAIN, BALD = "Valor the Cowboy", "Valor the Pirate", "Valor the Captain", "Valor the Bald"
TOM_TG, TOM_SLACK, TOM_EMAIL = "4242", "T0ABC/UTOM", "tom@example.com"
DM = f"telegram:{TOM_TG}"
DEV = "slack:T0ABC/C0DEV"  # the app's dev room, on the Pirate, waiting to be mentioned
CLIENT = "slack:T0ABC/C0CLIENT"  # a client's room, on the Captain
OURS = "slack:T0ABC/C0OURS"  # another of valor's rooms, on the Cowboy
FAR = "slack:T0ABC/C0FAR"  # on the Bald, which no Mac here reaches
BASE = 'repo = "unused"\nkind = "plain"\nsuite = "true"\n'
# Case 3's words as Tom wrote them (2026-11-23), typo and all.
TURN_OFF = (
    "It think it’s best to make the yudame tools turn off and he homebrew updates completely. "
    "Send a prompt to yudame-dev channel in Slack"
)


@contextmanager
def on(mac: str):
    with bridges.configure(machine=mac):
        yield


@pytest.fixture
def macs(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    (root / "valor.toml").write_text(
        f'name = "valor"\n{BASE}machine = "{COWBOY}"\nchats = ["{DM}", "slack:{TOM_SLACK}", "{OURS}"]\n'
    )
    (root / "tools.toml").write_text(
        f'name = "tools"\n{BASE}machine = "{PIRATE}"\nchats = ["{DEV}"]\nmentions = ["{DEV}"]\n'
    )
    (root / "client.toml").write_text(f'name = "client"\n{BASE}machine = "{CAPTAIN}"\nchats = ["{CLIENT}"]\n')
    (root / "far.toml").write_text(f'name = "far"\n{BASE}machine = "{BALD}"\nchats = ["{FAR}"]\n')
    with bridges.configure(
        projects_dir=str(root), work_dir=str(tmp_path / "work"), machine=COWBOY, default_machine=COWBOY,
        operator_telegram_id=TOM_TG, operator_slack_ids=(TOM_SLACK,), operator_email=(TOM_EMAIL,),
        operator_channel="telegram", operator_chat=TOM_TG,
    ):  # fmt: skip
        yield


@pytest.fixture
def peers(dsn, monkeypatch):
    """Another Mac over ssh: its `core receive` on this database. `calls`
    holds each (host, command); `refused` set makes ssh fail to connect."""
    calls: list[tuple[str, str]] = []
    state = types.SimpleNamespace(calls=calls, refused=None, hold=None)
    monkeypatch.setattr(
        kernel_main, "db", types.SimpleNamespace(connect=lambda *a, **k: db.connect(dsn, **k))
    )

    async def ssh(host, command, stdin=None):
        calls.append((host, command))
        if state.hold is not None:
            await state.hold()
        if state.refused:
            return 255, state.refused
        out = io.StringIO()

        def run():
            was, sys.stdin = sys.stdin, io.StringIO(stdin.decode())
            try:
                with contextlib.redirect_stdout(out):
                    kernel_main._receive("--lookup" in command)
            finally:
                sys.stdin = was

        await asyncio.to_thread(run)
        return 0, out.getvalue()

    monkeypatch.setattr(chat_tasks.remote, "ssh", ssh)
    return state


async def receive(dsn, chat, text, message_id, *, sender, name, kind=None, **kw) -> tuple[int, dict]:
    """A message received and bound on this Mac: its record number and binding."""
    channel, _, chat_id = chat.partition(":")
    kind = kind or (
        "email" if channel == "email" else "group" if chat_id.startswith(("-", "T0ABC/C")) else "dm"
    )
    inbound = intake.Inbound(channel=channel, chat_id=chat_id, chat_kind=kind, message_id=message_id,
                             sender_id=sender, sender_name=name, sent_at="2026-11-23T02:54:58+00:00", text=text, **kw)  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
        (row,) = await (
            await conn.execute(
                "SELECT r.id, b.payload FROM events r JOIN events b ON b.type = 'message.bound' AND "
                "b.payload->>'received_id' = r.payload->>'received_id' WHERE r.type = 'message.received' "
                "AND r.payload->>'received_id' = %s",
                (got.received_id,),
            )
        ).fetchall()
    return row[0], row[1]


async def tom_in_dm(dsn, text=TURN_OFF, message_id="12023", **kw) -> tuple[int, str]:
    record, bound = await receive(dsn, DM, text, message_id, sender=TOM_TG, name="Tom Counsell", **kw)
    return record, bound["task_id"]


async def hand(dsn, session_id, into, record, *, by="valor", note=None, key=None) -> broker.Outcome:
    key = key or uuid.uuid4().hex
    payload = {"in": into, "from": str(record), "by": by} | ({"text": note} if note else {})
    async with await db.connect(dsn) as conn:
        b = await tasks.brief(conn, session_id)
        return await broker.request(conn, broker.Performers(*chat_tasks.performers(b, dsn)), session_id,
                                    broker.Action("task.steer", into, payload), request_id=f"turn/act/{key}")  # fmt: skip


async def bound_there(dsn, mac, chat) -> tuple[dict, dict, list[dict]]:
    """On `mac`, its kernel binds: the handed-on record in `chat`, its
    binding, and its session's rows."""
    channel, _, chat_id = chat.partition(":")
    with on(mac):
        async with await db.connect(dsn) as conn:
            await intake.bind(conn)
            (received, b) = await (
                await conn.execute(
                    "SELECT r.payload, b.payload FROM events r JOIN events b ON b.type = 'message.bound' AND "
                    "b.payload->>'received_id' = r.payload->>'received_id' WHERE r.type = 'message.received' "
                    "AND r.task_id = %s AND r.payload->>'chat_id' = %s AND r.payload ? 'handed_on' "
                    "ORDER BY r.id DESC LIMIT 1",
                    (channel, chat_id),
                )
            ).fetchone()
            rows = await ledger.read(conn, b["task_id"]) if b.get("task_id") else []
    return received, b, rows


def steered(rows, received_id) -> dict:
    (r,) = [r for r in rows if r["type"] == "message.steered" and r["payload"]["received_id"] == received_id]
    return r["payload"]


async def reacted_or_sent(dsn, received_id) -> list[str]:
    async with await db.connect(dsn) as conn:
        rows = await (
            await conn.execute(
                "SELECT type FROM events WHERE (type LIKE 'reaction.%%' AND payload->>'received_id' = %s) "
                "OR type IN ('effect.held', 'release.requested')",
                (received_id,),
            )
        ).fetchall()
    return [r[0] for r in rows]


def test_case_3_toms_words_reach_the_dev_rooms_session_on_the_pirate_as_he_wrote_them(dsn, macs, peers):
    """2026-11-23: Tom's "turn off" was relayed to the dev room as Valor's own
    "remove". Handed on, the Pirate's session reads Tom's words, by Tom."""
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    o = asyncio.run(hand(dsn, dm_session, DEV, record, by="valor", note="Tom asked me to pass this to you"))
    assert o.kind == "done" and o.result["machine"] == PIRATE
    assert peers.calls == [("valor-the-pirate", "cd ~/src/ai && .venv/bin/python -m core receive")]
    received, b, rows = asyncio.run(bound_there(dsn, PIRATE, DEV))
    assert b["as"] == "steer" and received["text"] == TURN_OFF and received["verified"] is True
    assert (received["sender_id"], received["sender_name"], received["sent_at"]) == (
        TOM_TG, "Tom Counsell", "2026-11-23T02:54:58+00:00"
    )  # fmt: skip
    h = steered(rows, received["received_id"])["handed_on"]
    assert (h["chat"], h["by"], h["asked_by"], h["project"], h["machine"]) == (
        DM,
        "tom",
        "valor",
        "valor",
        COWBOY,
    )
    assert steered(rows, received["received_id"])["provenance"]["by"] == "tom"
    assert asyncio.run(reacted_or_sent(dsn, received["received_id"])) == []
    line = intake.chat_line(steered(rows, received["received_id"]))
    assert f"handed on from {DM}" in line and "not said in this chat" in line and TURN_OFF in line
    assert "Valor's note on handing it on: Tom asked me to pass this to you" in line


def test_case_1_a_hand_on_wakes_a_mentions_room_and_the_task_it_starts_reports_there(dsn, macs, peers):
    """2026-11-17: work asked in Tom's DM for the dev room ran under valor and
    merged without that room's check. Handed on, the dev room's session wakes
    with no mention, and the task it starts is its own."""
    record, dm_session = asyncio.run(
        tom_in_dm(dsn, "Clean up the leftover Chrome copies in the app", "11950")
    )
    assert asyncio.run(hand(dsn, dm_session, DEV, record)).kind == "done"
    _, b, rows = asyncio.run(bound_there(dsn, PIRATE, DEV))
    assert b["as"] == "steer" and session.chat_waiting(rows)
    dev_session = b["task_id"]

    async def its_task():
        with on(PIRATE):
            child = await started(dsn, dev_session)
            async with await db.connect(dsn) as conn:
                return (
                    child,
                    await tasks.reporting(conn, dev_session),
                    (await tasks.brief(conn, child)).project,
                )

    child, reporting, project = asyncio.run(its_task())
    assert child in reporting and project["name"] == "tools"


def test_case_2_an_email_reaches_the_client_rooms_session_verbatim_and_unverified(dsn, macs, peers):
    """2026-11-21: Tom's email reply reached the valor session, not the client
    room's. Handed on, it arrives as written, with no more rights than an email."""
    body = "Re: the signup plan\n\nKeep the consent step in scope; hold the rest."
    record, bound = asyncio.run(
        receive(dsn, "email:<root@mail.example>", body, "<reply@mail.example>", sender=TOM_EMAIL, name="Tom")
    )
    assert asyncio.run(hand(dsn, bound["task_id"], CLIENT, record)).kind == "done"
    assert peers.calls[-1][0] == "valor-the-captain"
    received, b, rows = asyncio.run(bound_there(dsn, CAPTAIN, CLIENT))
    assert received["text"] == body and received["sender_id"] == TOM_EMAIL and received["verified"] is False
    assert steered(rows, received["received_id"])["handed_on"]["by"] == "Tom" and b["as"] == "steer"


def test_a_record_handed_on_again_keeps_its_first_chat_and_who_said_it(dsn, macs, peers):
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    asyncio.run(hand(dsn, dm_session, DEV, record))
    received, b, _ = asyncio.run(bound_there(dsn, PIRATE, DEV))

    async def again():
        async with await db.connect(dsn) as conn:
            (n,) = await (
                await conn.execute(
                    "SELECT id FROM events WHERE type = 'message.received' AND payload->>'received_id' = %s",
                    (received["received_id"],),
                )
            ).fetchone()
        with on(PIRATE):
            return await hand(dsn, b["task_id"], OURS, n)

    assert asyncio.run(again()).kind == "done"
    back, _, rows = asyncio.run(bound_there(dsn, COWBOY, OURS))
    h = back["handed_on"]
    assert (h["chat"], h["by"], h["verified"], h["machine"]) == (DM, "tom", True, PIRATE)
    assert back["text"] == TURN_OFF and steered(rows, back["received_id"])["provenance"]["by"] == "tom"


def test_a_handed_on_stop_into_toms_own_chat_is_a_steer_and_stops_nothing(dsn, macs, peers):
    _, dm_session = asyncio.run(tom_in_dm(dsn, "Look at the backups", "12000"))
    child = asyncio.run(started(dsn, dm_session))
    with on(PIRATE):
        record, b = asyncio.run(
            receive(dsn, DEV, "stop", "1795400000.000100", sender="T0ABC/ULOGAN", name="Logan")
        )
        assert asyncio.run(hand(dsn, b["task_id"], DM, record)).kind == "done"
    received, bound, _ = asyncio.run(bound_there(dsn, COWBOY, DM))

    async def state():
        async with await db.connect(dsn) as conn:
            return machine.fold(await ledger.read(conn, child)).state

    assert bound["as"] == "steer" and bound["task_id"] == dm_session and received["text"] == "stop"
    assert asyncio.run(state()) not in tasks.TERMINAL


def test_what_names_the_other_room_is_left_out_and_its_files_stay_on_the_mac(dsn, macs, peers):
    record, dm_session = asyncio.run(
        tom_in_dm(dsn, "this one", "12030", reply_to="12029", topic_id="7", thread=[{"id": "12029", "text": "x"}],
                  attachments=[{"name": "shot.png", "mime": "image/png", "bytes": 9, "path": "/private/shot.png"}])
    )  # fmt: skip
    asyncio.run(hand(dsn, dm_session, DEV, record))
    received, _, rows = asyncio.run(bound_there(dsn, PIRATE, DEV))
    assert (received["reply_to"], received["topic_id"], received["thread"]) == (None, None, [])
    assert received["chat_kind"] == "group" and received["message_id"].startswith("handon:")
    assert received["attachments"] == [
        {"name": "shot.png", "mime": "image/png", "bytes": 9, "kept_on": COWBOY}
    ]
    p = steered(rows, received["received_id"])
    line = intake.chat_line(p)
    assert p["thread_tag"] is None and "message handon" not in line and "top-level" not in line
    assert f"kept on {COWBOY}, not here" in line and "/private/shot.png" not in line
    assert "handed on from" in intake.chat_line(received)  # as a new session's history shows it

    async def paging():
        async with await db.connect(dsn) as conn:
            return await intake.highest(conn, "slack", "T0ABC/C0DEV"), await intake.lowest_ts(
                conn, "slack", "T0ABC/C0DEV"
            )

    assert not [
        x for x in asyncio.run(paging()) if str(x).startswith("handon:")
    ]  # the gap fill pages past it


def test_the_same_hand_on_twice_records_once_and_its_lookup_finds_it(dsn, macs, peers):
    record, dm_session = asyncio.run(tom_in_dm(dsn))

    async def twice():
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, dm_session)
        p = next(x for x in chat_tasks.performers(b, dsn) if x.action_type == "task.steer")
        action = broker.Action("task.steer", DEV, {"in": DEV, "from": str(record), "by": "valor"})
        return (
            await p.perform(action, "same"),
            await p.perform(action, "same"),
            await p.lookup(action, "same"),
        )

    first, second, found = asyncio.run(twice())
    assert first["received_id"] == second["received_id"] == found["received_id"]
    assert peers.calls[-1][1].endswith("core receive --lookup")


def test_a_chat_on_this_mac_is_recorded_here_with_no_ssh(dsn, macs, peers):
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    assert asyncio.run(hand(dsn, dm_session, OURS, record)).kind == "done" and peers.calls == []
    received, b, _ = asyncio.run(bound_there(dsn, COWBOY, OURS))
    assert b["as"] == "steer" and received["text"] == TURN_OFF


def test_a_mac_with_no_route_fails_plainly_and_nothing_is_recorded(dsn, macs, peers):
    peers.refused = "ssh: connect to host valor-the-bald port 22: Operation timed out"
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    o = asyncio.run(hand(dsn, dm_session, FAR, record))
    assert o.kind == "failed" and f"no route from {COWBOY} to {BALD}: ssh: connect to host" in o.error

    async def none_there():
        async with await db.connect(dsn) as conn:
            return await (
                await conn.execute(
                    "SELECT 1 FROM events WHERE type = 'message.received' AND payload ? 'handed_on' "
                    "AND payload->>'chat_id' = 'T0ABC/C0FAR'"
                )
            ).fetchall()

    assert asyncio.run(none_there()) == []


def test_another_projects_record_or_an_unknown_chat_is_a_plain_failure(dsn, macs, peers):
    _, dm_session = asyncio.run(tom_in_dm(dsn))
    with on(CAPTAIN):
        theirs, _ = asyncio.run(
            receive(dsn, CLIENT, "client words", "1795400000.000200", sender="T0ABC/UC", name="C")
        )
    o = asyncio.run(hand(dsn, dm_session, DEV, theirs))
    assert o.kind == "failed" and f"no record #{theirs} in project valor's messages" in o.error
    record, _ = asyncio.run(tom_in_dm(dsn, "x", "12040"))
    o = asyncio.run(hand(dsn, dm_session, "slack:T0ABC/C0NOBODY", record))
    assert o.kind == "failed" and "no project on any Mac takes slack:T0ABC/C0NOBODY" in o.error
    assert peers.calls == []


def test_no_lock_of_the_asking_session_is_held_while_the_other_mac_is_reached(dsn, macs, peers):
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    took = []

    async def meanwhile():
        async with await db.connect(dsn) as conn, conn.transaction():
            await asyncio.wait_for(ledger.lock(conn, f"task:{dm_session}"), 5)
            took.append(True)

    peers.hold = meanwhile
    assert asyncio.run(hand(dsn, dm_session, DEV, record)).kind == "done" and took == [True]


def test_the_command_hands_on_by_record_number_and_messages_shows_it_was_handed_on(dsn, macs, peers, capsys):
    record, dm_session = asyncio.run(tom_in_dm(dsn))
    ((rc, said, _),), _ = asyncio.run(
        acting(dsn, dm_session, ["act", "steer", "--in", DEV, "--from", f"#{record}", "--by", "valor"])
    )
    assert rc == 0 and said.startswith("done") and PIRATE in said
    received, _, _ = asyncio.run(bound_there(dsn, PIRATE, DEV))
    line = valor_record.message_line({"record": 1, "chat": DEV, "message": received, "bound": None})
    assert f"handed on from {DM} by the valor session on {COWBOY}; not said here" in line
    for wrong in (
        ["act", "steer", "--in", DEV, "--by", "valor"],
        ["act", "steer", "t1", "--in", DEV, "--from", "1", "--by", "v"],
    ):
        assert valor_record.main(wrong) == 2
    assert "act steer takes TASK --text, or --in CHAT --from RECORD" in capsys.readouterr().err
