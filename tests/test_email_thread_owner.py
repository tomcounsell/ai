"""An email thread belongs to the Mac and the session that sent its root,
whoever replies in it (the incident of 2026-11-21: a project's session on
one Mac emailed a client, Tom replied to all, and the default Mac bound his
reply to a new `valor` session the project never heard)."""

import asyncio
import email.message
import pathlib
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from bridges.email import imap
from core import db, intake, ledger, machine, mail, tasks
from core.settings import settings
from tests import bridges

run = asyncio.run
COWBOY, PIRATE = "Valor the Cowboy", "Valor the Pirate"
CLIENT_ROOM = "slack:T0ABC/C0CLIENT"
OUTSIDE = f"<CAK+{uuid.uuid4().hex}@mail.gmail.com>"  # a root no Mac of ours sent


@pytest.fixture
def macs(tmp_path):
    """valor on the Cowboy (Tom's address is owned there only), and a
    client project on the Pirate that lists its domain and a Slack room."""
    with (
        bridges.operator(tmp_path, email_address="valor@yuda.test"),
        bridges.configure(default_machine=COWBOY),
    ):
        projects = pathlib.Path(settings.projects_dir)
        (projects / "valor.toml").write_text(bridges.VALOR_SPEC + f'machine = "{COWBOY}"\n')
        (projects / "client.toml").write_text(
            'name = "client"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            f'machine = "{PIRATE}"\nchats = ["email:@client.test", "{CLIENT_ROOM}"]\n'
        )
        yield


def on(mac):
    """This process as `mac`'s kernel and bridges; Tom's address is the Cowboy's."""
    return bridges.configure(machine=mac, operator_email=(bridges.OPERATOR_EMAIL,) if mac == COWBOY else ())


async def session_of(conn, project, chat, **kw) -> str:
    channel, _, chat_id = chat.partition(":")
    brief = tasks.Brief(instruction="test", project={"name": project}, max_effect_class="act",
                        chat={"channel": channel, "chat_id": chat_id}, **kw)  # fmt: skip
    return await tasks.start(conn, brief)


async def sends(conn, task) -> str:
    """The task's email, sent from this Mac: the thread's root."""
    root = mail.message_id(ledger.new_id(), settings.email_address)
    entries = [{"channel": "email", "chat_id": root, "message_id": root}]
    await ledger.append(conn, task, "effect.outcome", {"effect_id": ledger.new_id(), "idempotency_key": "k",
                        "kind": "done", "result": {"sent": entries}, "error": None})  # fmt: skip
    return root


async def replied(conn, root, *, sender=bridges.OPERATOR_EMAIL, name="Tom") -> dict:
    """A reply to all in `root`'s thread, received and bound: its binding."""
    mid = f"<{uuid.uuid4().hex}@mail.test>"
    got = await intake.receive(conn, intake.Inbound(
        channel="email", chat_id=root, chat_kind="email", message_id=mid, sender_id=sender,
        sender_name=name, sent_at="2026-11-21T13:32:00+00:00", text="Re: the build\n\nunder 13 too",
        reply_to=root, thread=[{"id": root}],
    ))  # fmt: skip
    await intake.bind(conn)
    found = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.bound' AND payload->>'received_id' = %s",
            (got.received_id,),
        )
    ).fetchone()
    return found[0]


async def email_sessions(conn, *roots) -> list[str]:
    """The email sessions of the threads `roots`."""
    rows = await (
        await conn.execute(
            "SELECT id FROM documents WHERE kind = 'task' AND body->'chat'->>'channel' = 'email' "
            "AND body->'chat'->>'chat_id' = ANY(%s)",
            (list(roots),),
        )
    ).fetchall()
    return [r[0] for r in rows]


def test_a_message_id_names_the_mac_that_sent_it():
    with bridges.configure(machine=PIRATE):
        pirate = mail.message_id("k", "valor@yuda.test")
        assert (
            mail.message_id("k", "valor@yuda.test") == pirate
            and mail.machine_of(pirate) == mail.machine_tag()
        )
    with bridges.configure(machine=COWBOY):
        cowboy = mail.message_id("k", "valor@yuda.test")
    assert cowboy != pirate and mail.machine_of(cowboy) == mail.machine_tag(COWBOY) != mail.machine_tag(
        PIRATE
    )
    assert PIRATE not in pirate and "Pirate" not in pirate
    # Ours from before the tag, and anyone else's, name no Mac.
    assert mail.machine_of("<valor.b66c3275946ba9130bfdba210f089c93@yuda.test>") is None
    assert mail.machine_of("<CAKMa3GW+x@mail.gmail.com>") is None


def test_a_reply_is_received_on_the_mac_that_started_the_thread_whoever_sends_it(macs):
    with on(PIRATE):
        clients = mail.message_id("a", settings.email_address)
    with on(COWBOY):
        valors = mail.message_id("b", settings.email_address)
    other = "<CAK+someone@mail.gmail.com>"
    took = {}
    for mac in (COWBOY, PIRATE):
        with on(mac):
            took[mac] = {
                (who, root): intake.takes_email(who, root)
                for who in (bridges.OPERATOR_EMAIL, "bryan@client.test", "x@nowhere.test", "valor@yuda.test")
                for root in (clients, valors, other)
            }
    for who in (bridges.OPERATOR_EMAIL, "bryan@client.test", "x@nowhere.test"):
        # The incident: Tom (owned on the Cowboy only) in the client's thread.
        assert took[PIRATE][(who, clients)] and not took[COWBOY][(who, clients)]
        assert took[COWBOY][(who, valors)] and not took[PIRATE][(who, valors)]
    # A thread nobody here started: the sender's owner, as before.
    assert took[COWBOY][(bridges.OPERATOR_EMAIL, other)] and not took[PIRATE][(bridges.OPERATOR_EMAIL, other)]
    assert took[PIRATE][("bryan@client.test", other)] and not took[COWBOY][("bryan@client.test", other)]
    assert not any(took[m][("x@nowhere.test", other)] for m in took)
    # Valor's own mail, never.
    assert not any(v for m in took for (who, _), v in took[m].items() if who == "valor@yuda.test")


def test_toms_reply_in_a_project_rooms_thread_steers_the_room_session_on_its_mac(dsn, macs):
    """The incident: the client room's session on the Pirate sent the root;
    Tom's reply, and the client's own, steer that session, with no email
    session of their own; once it has ended, one email session of the
    client's takes the thread."""

    async def go():
        with on(PIRATE):
            async with await db.connect(dsn) as conn:
                room = await session_of(conn, "client", CLIENT_ROOM)
                root = await sends(conn, room)
                toms = await replied(conn, root)
                clients = await replied(conn, root, sender="bryan@client.test", name="Bryan")
                none_yet = await email_sessions(conn, root)
                steered = [
                    r["payload"] for r in await ledger.read(conn, room) if r["type"] == "message.steered"
                ]
                await tasks.stop(conn, room, reason="test")
                after = [await replied(conn, root), await replied(conn, root, sender="bryan@client.test")]
                b = await tasks.brief(conn, after[0]["task_id"])
        return room, root, toms, clients, none_yet, steered, after, b

    room, root, toms, clients, none_yet, steered, after, b = run(go())
    assert (toms["as"], toms["task_id"]) == ("steer", room) and (clients["as"], clients["task_id"]) == (
        "steer",
        room,
    )
    assert none_yet == []
    assert [s["provenance"]["by"] for s in steered] == ["Tom", "Bryan"]
    assert {s["thread_tag"] for s in steered} == {f"an email in the thread {root}"}
    assert [a["as"] for a in after] == ["steer", "steer"] and after[0]["task_id"] == after[1][
        "task_id"
    ] != room
    assert b.chat == {"channel": "email", "chat_id": root} and b.project == {"name": "client"}


def test_a_build_tasks_thread_gets_its_projects_email_session_and_a_stray_root_nothing(dsn, macs):
    """A task that is no chat session sent the root: the reply opens the
    thread's own session under the task's project, with the task under it
    (`replying_to`). A root tagged with this Mac that no task here sent,
    from a sender no spec here lists, is bound to nothing."""

    async def go():
        with on(PIRATE):
            async with await db.connect(dsn) as conn:
                build = await tasks.start(conn, tasks.Brief(instruction="build", project={"name": "client"}))
                got = await replied(conn, await sends(conn, build), sender="x@nowhere.test")
                b = await tasks.brief(conn, got["task_id"])
                steered = [r["payload"] for r in await ledger.read(conn, got["task_id"])
                           if r["type"] == "message.steered"]  # fmt: skip
                stray = await replied(conn, mail.message_id(ledger.new_id(), settings.email_address),
                                      sender="x@nowhere.test")  # fmt: skip
        return build, got, b, steered, stray

    build, got, b, steered, stray = run(go())
    assert got["as"] == "steer" and got["task_id"] != build
    assert b.chat["channel"] == "email" and b.project == {"name": "client"}
    assert steered[0]["replying_to"]["task_id"] == build
    assert stray["as"] == "none" and stray.get("task_id") is None


def test_a_thread_valor_started_and_one_nobody_here_started(dsn, macs):
    """valor's own session on the Cowboy hears Tom's reply to its email;
    a thread rooted in no send here keeps the sender's rule: Tom's goes to
    a new `valor` email session."""

    async def go():
        with on(COWBOY):
            async with await db.connect(dsn) as conn:
                own = await session_of(conn, "valor", "telegram:-1009000000777")
                ours = await replied(conn, await sends(conn, own))
                theirs = await replied(conn, OUTSIDE)
                b = await tasks.brief(conn, theirs["task_id"])
                state = machine.fold(await ledger.read(conn, own)).state
                for t in (own, theirs["task_id"]):
                    await tasks.stop(conn, t, reason="test")  # no open session left for other tests
        return own, ours, theirs, b, state

    own, ours, theirs, b, state = run(go())
    assert (ours["as"], ours["task_id"]) == ("steer", own) and state not in tasks.TERMINAL
    assert theirs["as"] == "steer" and theirs["task_id"] != own
    assert b.chat == {"channel": "email", "chat_id": OUTSIDE} and b.project == {"name": "valor"}


class Box:
    """An INBOX of unseen mail, as `poll` reads it."""

    def __init__(self, mails: dict[bytes, email.message.EmailMessage]):
        self.mails, self.seen = mails, []


def test_poll_receives_its_threads_on_a_mac_that_owns_no_address(dsn, macs, tmp_path, monkeypatch):
    """A Mac with no email chats and not Tom's addresses still receives a
    reply to its own thread; another Mac's thread is left unseen for it."""
    with on(PIRATE):
        ours = mail.message_id("a", settings.email_address)
    with on(COWBOY):
        theirs = mail.message_id("b", settings.email_address)

    def made(root):
        m = email.message.EmailMessage()
        m["From"], m["To"], m["Subject"] = f"Tom <{bridges.OPERATOR_EMAIL}>", "valor@yuda.test", "Re: x"
        m["Message-ID"], m["References"], m["In-Reply-To"] = f"<{uuid.uuid4().hex}@mail.test>", root, root
        m.set_content("under 13 too")
        return m

    box = Box({b"1": made(ours), b"2": made(theirs)})

    def heads(conn, uids):
        return {u: {"sender": bridges.OPERATOR_EMAIL, "message_id": box.mails[u]["Message-ID"],
                    "chat_id": box.mails[u]["References"]} for u in uids}  # fmt: skip

    monkeypatch.setattr(imap, "select_inbox", lambda conn: "7")
    monkeypatch.setattr(imap, "search", lambda conn, since: sorted(box.mails))
    monkeypatch.setattr(imap, "headers", heads)
    monkeypatch.setattr(imap, "fetch", lambda conn, u: (bytes(box.mails[u]), None))
    monkeypatch.setattr(imap, "mark_seen", lambda conn, u: box.seen.append(u))

    async def go():
        with (
            on(PIRATE),
            bridges.configure(projects_dir=str(tmp_path / "none"), inbound_dir=str(tmp_path / "in")),
        ):
            assert intake.owned("email") == []
            async with await db.connect(dsn) as conn:
                n = await imap.poll(
                    SimpleNamespace(since=date(2026, 11, 1), address="valor@yuda.me"), None, conn
                )
                rows = await (
                    await conn.execute(
                        "SELECT payload->>'chat_id' FROM events WHERE type = 'message.received' "
                        "AND payload->>'chat_id' = ANY(%s)",
                        ([ours, theirs],),
                    )
                ).fetchall()
        return n, [r[0] for r in rows]

    (tmp_path / "none").mkdir()
    n, received = run(go())
    assert n == 1 and received == [ours] and box.seen == [b"1"]
