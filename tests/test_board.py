"""The decision board (docs/plans/decision-board.md): a project's open
questions reach its board chat's session as one row a change, never
another project's; the board session answers its project's tasks with
`via`; Tom's Slack reaction on Valor's post is a record that steers the
session and gets no reaction of its own; the canvas is written whole."""

import asyncio
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from bridges.slack import __main__ as slack_main
from bridges.slack import inbound as slack_inbound
from bridges.slack.send import BOARD, Sender
from bridges.slack.wire import InDoubt, Refused
from core import board, broker, db, intake, ledger, machine, notices, session, tasks
from core.machine import State
from core.settings import settings
from tests import bridges, test_kernel
from tests.test_kernel import at, child_of, new_task, tom_says
from tests.test_machine import Ledger

dm = test_kernel.dm  # Tom's Slack DM and the operator, as a fixture

pytestmark = pytest.mark.spend(usd=0)
run = asyncio.run


def project(name: str, chat: str, *, board_chat: bool = True, machine_: str | None = None) -> None:
    (pathlib.Path(settings.projects_dir) / f"{name}.toml").write_text(
        f'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["slack:{chat}"]\n'
        + (f'board = "slack:{chat}"\n' if board_chat else "")
        + (f'machine = "{machine_}"\n' if machine_ else "")
    )


def channel() -> str:
    return f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"


async def say(dsn, chat, text, ts, *, sender="T0ABC/ULEWIS", **kw) -> dict:
    """A message in a team chat, received and bound."""
    inbound = intake.Inbound(channel="slack", chat_id=chat, chat_kind="group", message_id=ts, sender_id=sender,
                             sender_name="Logan", sent_at="2026-10-27T03:00:00Z", text=text, **kw)  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    return bound


async def ask(dsn, task, text="Ship on Friday?") -> str:
    """The task asks its question; the question id."""
    async with await db.connect(dsn) as conn:
        for row in Ledger().ask().rows[1:]:
            p = {
                k: f"{task}-{v}" if k in ("turn_id", "question_id") else v for k, v in row["payload"].items()
            }
            await ledger.append(
                conn, task, row["type"], {**p, "text": text} if row["type"] == "question.asked" else p
            )
        f = machine.fold(await ledger.read(conn, task))
    assert f.state is State.WAITING
    return f.open_question


async def owe(dsn) -> list[str]:
    async with await db.connect(dsn) as conn:
        return await board.owe(conn)


async def board_rows(dsn, board_chat) -> list[dict]:
    async with await db.connect(dsn) as conn:
        rows = await (
            await conn.execute(
                "SELECT task_id, payload FROM events WHERE type = 'child.reported' AND payload->>'board' = %s "
                "ORDER BY id",
                (f"slack:{board_chat}",),
            )
        ).fetchall()
    return [{"task_id": t, **p} for t, p in rows]


async def session_task(dsn, chat) -> str:
    """The chat's session, started by a message there."""
    return (await say(dsn, chat, "hello", at(1)))["task_id"]


async def send(dsn, task, chat, ts, text, **payload) -> None:
    effect = ledger.new_id()
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "effect.intent", {"effect_id": effect, "action_type": "slack.send_message",
                            "target": chat, "payload": {"text": text, **payload}})  # fmt: skip
        await ledger.append(conn, task, "effect.outcome", {"effect_id": effect, "idempotency_key": "k",
                            "kind": "done", "result": {"sent": [{"channel": "slack", "chat_id": chat,
                            "message_id": ts, "thread_ts": None}]}, "error": None})  # fmt: skip


async def stop(dsn, task) -> None:
    async with await db.connect(dsn) as conn:
        await tasks.stop(conn, task, reason="test", by="tom", via="test")


# -- the board row ---------------------------------------------------------------


def test_a_projects_board_carries_its_own_questions_once_a_change_and_never_another_projects(dsn, dm):
    """Northwind's question is on northwind's board only; nothing changed writes
    nothing; a question stopped leaves Open, and opening one more then
    stopping it brings the digest back to an earlier one, which still
    writes a row (it is compared with the latest)."""
    mine, theirs = channel(), channel()
    a, b = f"p{uuid.uuid4().hex[:6]}", f"p{uuid.uuid4().hex[:6]}"
    project(a, mine)
    project(b, theirs)

    async def go():
        here = await session_task(dsn, mine)
        there = await session_task(dsn, theirs)
        first = await child_of(dsn, here)
        q1 = await ask(dsn, first, "Use Postgres for the queue?")
        other = await child_of(dsn, there)
        await ask(dsn, other, "Raise the day rate?")
        await owe(dsn)
        again = await owe(dsn)
        second = await child_of(dsn, here)
        await ask(dsn, second, "Rename the app?")
        await owe(dsn)
        await stop(dsn, second)
        await owe(dsn)
        return here, q1, again, await board_rows(dsn, mine), await board_rows(dsn, theirs)

    here, q1, again, rows, theirs_rows = run(go())
    assert again == [] and len(rows) == 3 and {r["task_id"] for r in rows} == {here}
    assert rows[0]["digest"] == rows[2]["digest"] != rows[1]["digest"]
    assert all(r.get("child_id") is None and r["kind"] == "board" for r in rows)
    assert "Use Postgres for the queue?" in rows[0]["text"] and f"question {q1}" in rows[0]["text"]
    assert "no post yet: post it here" in rows[0]["text"]
    assert "Who reads it: not known until the first write." in rows[0]["text"]
    assert "Rename the app?" in rows[1]["text"] and "Rename the app?" not in rows[2]["text"]
    assert all("Raise the day rate?" not in r["text"] for r in rows)
    assert len(theirs_rows) == 1 and "Raise the day rate?" in theirs_rows[0]["text"]


def test_an_answer_moves_to_decided_with_who_and_how_and_a_post_alone_writes_no_row(dsn, dm):
    chat = channel()
    name = f"p{uuid.uuid4().hex[:6]}"
    project(name, chat)

    async def go():
        here = await session_task(dsn, chat)
        child = await child_of(dsn, here)
        q = await ask(dsn, child, "Use Postgres for the queue?")
        await owe(dsn)
        await send(dsn, here, chat, at(50), "Use Postgres? I recommend yes.", about=f"question:{q}")
        quiet = await owe(dsn)
        effect = ledger.new_id()
        result = {
            "canvas_id": "F0B",
            "url": "https://x.slack.com/docs/T0ABC/F0B",
            "readers": ["Tom Counsell"],
        }
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, here, "effect.intent", {"effect_id": effect, "action_type": "slack.write_canvas",
                                "target": chat, "payload": {"markdown": "# Valor Decisions"}})  # fmt: skip
            await ledger.append(conn, here, "effect.outcome", {"effect_id": effect, "idempotency_key": "k",
                                "kind": "done", "result": result, "error": None})  # fmt: skip
            await session.answer(conn, child, "Accepted: yes", by="tom", via="slack reaction :+1:")
        await owe(dsn)
        return quiet, await board_rows(dsn, chat)

    quiet, rows = run(go())
    assert quiet == [] and len(rows) == 2
    assert rows[1]["text"].startswith(f"Board chat slack:{chat}; canvas: https://x.slack.com/docs/T0ABC/F0B.")
    assert "Who reads it: Tom Counsell." in rows[1]["text"]
    decided = rows[1]["text"].split("Decided")[1]
    assert 'by tom via slack reaction :+1:: "Accepted: yes"' in decided
    assert "asked: Use Postgres for the queue?" in decided
    assert rows[1]["text"].split("Decided")[0].endswith("Open:\n- none\n\n")


def test_no_board_another_macs_board_or_nothing_to_show_writes_nothing(dsn, dm):
    plain, elsewhere, empty = channel(), channel(), channel()
    project(f"p{uuid.uuid4().hex[:6]}", plain, board_chat=False)
    project(f"p{uuid.uuid4().hex[:6]}", elsewhere, machine_="m-other")
    project(f"p{uuid.uuid4().hex[:6]}", empty)

    async def go():
        child = await child_of(dsn, await session_task(dsn, plain))
        await ask(dsn, child)
        await owe(dsn)
        async with await db.connect(dsn) as conn:
            started = await intake.chat_session(conn, "slack", empty)
        return [await board_rows(dsn, c) for c in (plain, elsewhere, empty)], started

    rows, started = run(go())
    assert rows == [[], [], []] and started is None


def test_with_no_board_session_open_the_row_starts_one(dsn, dm):
    """A question from a task that reports to Tom's DM, under a project with
    a board, starts the board chat's session; it is listed as asked there,
    and still reported to the DM session."""
    chat = channel()
    (pathlib.Path(settings.projects_dir) / "valor.toml").write_text(
        bridges.VALOR_SPEC + f'chats = ["slack:{dm}", "slack:{chat}"]\nboard = "slack:{chat}"\n'
    )

    async def go():
        dm_session = (await tom_says(dsn, dm, "hello", at(10)))["task_id"]
        child = await child_of(dsn, dm_session)
        await ask(dsn, child, "Which colour?")
        async with await db.connect(dsn) as conn:
            await notices.owe(conn, child)
            started = await intake.chat_session(conn, "slack", chat)
        assert started is None
        await owe(dsn)
        async with await db.connect(dsn) as conn:
            started = await intake.chat_session(conn, "slack", chat)
            b = await tasks.brief(conn, started)
        return dm_session, await bridges.rows(dsn, dm_session), started, b, await board_rows(dsn, chat)

    _, dm_rows, started, b, rows = run(go())
    assert [r["payload"]["kind"] for r in dm_rows if r["type"] == "child.reported"] == ["question"]
    assert b.chat == {"channel": "slack", "chat_id": chat} and b.project == {"name": "valor"}
    assert len(rows) == 1 and rows[0]["task_id"] == started
    assert "asked in another chat (Tom's DM): no post here" in rows[0]["text"]


def test_a_board_sessions_own_tasks_question_is_its_board_row_not_a_report(dsn, dm):
    """Its post, once made with `about`, is named in the next row; the row
    renders as the board line, and `reporting` holds no None."""
    chat = channel()
    project(f"p{uuid.uuid4().hex[:6]}", chat)

    async def go():
        here = await session_task(dsn, chat)
        child = await child_of(dsn, here)
        q = await ask(dsn, child, "Use Postgres for the queue?")
        async with await db.connect(dsn) as conn:
            await notices.owe(conn, child)
        await owe(dsn)
        await send(dsn, here, chat, at(50), "Use Postgres? I recommend yes.", about=f"question:{q}")
        other = await child_of(dsn, here)
        await ask(dsn, other, "Rename it?")
        await owe(dsn)
        rows = await bridges.rows(dsn, here)
        async with await db.connect(dsn) as conn:
            reporting = await tasks.reporting(conn, here)
        return here, child, other, rows, reporting, await board_rows(dsn, chat)

    _, child, other, rows, reporting, board_ = run(go())
    assert [r["payload"]["kind"] for r in rows if r["type"] == "child.reported"] == ["board", "board"]
    assert f"your post {at(50)}" in board_[1]["text"]
    assert reporting == [child, other]
    rendered = session.chat_prompt(rows)
    assert "- The decision board changed:" in rendered and "Your task None" not in rendered


# -- answering from the board ------------------------------------------------------


def answer_action(task, **payload):
    return broker.Action("task.answer", task, {"text": "Accepted: yes", "by": "tom", **payload})


def test_the_board_session_answers_its_projects_tasks_with_via_and_no_one_else_does(dsn, dm):
    from tools import chat_tasks

    chat, other_chat = channel(), channel()
    name = f"p{uuid.uuid4().hex[:6]}"
    (pathlib.Path(settings.projects_dir) / f"{name}.toml").write_text(
        f'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        f'chats = ["slack:{chat}", "slack:{other_chat}"]\nboard = "slack:{chat}"\n'
    )
    foreign = channel()
    project(f"p{uuid.uuid4().hex[:6]}", foreign)

    async def go():
        here = await session_task(dsn, chat)
        side = await say(dsn, other_chat, "hi", at(2))
        side_session = side["task_id"]
        waiting = await child_of(dsn, side_session)
        await ask(dsn, waiting)
        idle = await child_of(dsn, side_session)
        theirs = await child_of(dsn, await session_task(dsn, foreign))
        await ask(dsn, theirs)
        async with await db.connect(dsn) as conn:
            mine = chat_tasks.performers(await tasks.brief(conn, here), dsn)
            sides = chat_tasks.performers(await tasks.brief(conn, side_session), dsn)
        answer = next(p for p in mine if p.action_type == "task.answer")
        side_answer = next(p for p in sides if p.action_type == "task.answer")
        async with await db.connect(dsn) as conn:
            refusals = [
                await answer.refuse(conn, answer_action(idle)),
                await answer.refuse(conn, answer_action(theirs)),
            ]
            assert await answer.refuse(conn, answer_action(waiting)) is None
        done = await answer.perform(answer_action(waiting, via="slack reaction :+1:"), ledger.new_id())
        # A same-project chat that is not the board still answers only its own.
        async with await db.connect(dsn) as conn:
            fresh = await child_of(dsn, here)
            await ask(dsn, fresh)
            refusals.append(await side_answer.refuse(conn, answer_action(fresh)))
        return done, refusals, await bridges.rows(dsn, waiting)

    done, refusals, rows = run(go())
    assert all(r and "does not report to this chat" in r for r in refusals)
    (answered,) = [r["payload"] for r in rows if r["type"] == "question.answered"]
    assert answered["text"] == "Accepted: yes" and done["question_id"] == answered["question_id"]
    assert (answered["provenance"]["by"], answered["provenance"]["via"]) == ("tom", "slack reaction :+1:")


# -- Tom's reaction -----------------------------------------------------------------


class Wire(SimpleNamespace):
    team_id = "T0ABC"
    user_id = "UVALOR"

    async def user_name(self, user):
        return "Logan" if user == "ULEWIS" else "Tom Counsell"


def reacted(user="UTOM", item_user="UVALOR", channel_id="C0TEAM", name="+1", ts="1793071000.000100"):
    return {"type": "reaction_added", "user": user, "reaction": name, "item_user": item_user,
            "item": {"type": "message", "channel": channel_id, "ts": ts}, "event_ts": "1793071100.000200"}  # fmt: skip


def test_only_toms_reaction_on_valors_own_post_is_a_record():
    toms = ("T0ABC/UTOM",)
    got = run(slack_inbound.reaction(Wire(), reacted(), toms))
    assert got["chat_id"] == "T0ABC/C0TEAM" and got["kind"] == "reaction" and got["text"] == ":+1:"
    assert (
        got["message_id"] == "1793071000.000100/+1"
        and got["reply_to"] == got["topic_id"] == "1793071000.000100"
    )
    assert got["sender_id"] == "T0ABC/UTOM" and got["sender_name"] == "Tom Counsell"
    in_dm = run(slack_inbound.reaction(Wire(), reacted(channel_id="D0TOM"), toms))
    assert (in_dm["chat_id"], in_dm["chat_kind"]) == ("T0ABC/UTOM", "dm")
    for other in (
        reacted(user="ULEWIS"),
        reacted(item_user="ULEWIS"),
        {**reacted(), "item": {"type": "file"}},
    ):
        assert run(slack_inbound.reaction(Wire(), other, toms)) is None


def test_toms_reaction_lands_once_steers_the_session_and_gets_no_reaction(dsn, dm):
    """The record binds to the board chat's session as a steer (a reply to
    Valor's post); a redelivery is the same record; no reaction is owed on
    it, and gap fill lists only the post as its thread."""
    chat = channel()
    project(f"p{uuid.uuid4().hex[:6]}", chat)
    tom = dm

    async def go():
        here = await session_task(dsn, chat)
        await send(dsn, here, chat, at(50), "Use Postgres? I recommend yes.")
        fields = await slack_inbound.reaction(
            Wire(team_id="T0ABC"),
            reacted(user=tom.partition("/")[2], channel_id=chat.partition("/")[2], ts=at(50)),
            (tom,),
        )
        async with await db.connect(dsn) as conn:
            first = await intake.receive(conn, intake.Inbound(**fields))
            again = await intake.receive(conn, intake.Inbound(**fields))
            await intake.bind(conn)
            threads = await intake.threads(conn, "slack", chat, "2000-01-01T00:00:00+00:00")
        (bound,) = await bridges.of_type(dsn, "message.bound", received_id=first.received_id)
        rows = await bridges.rows(dsn, here)
        return here, first, again, bound, rows, threads

    here, first, again, bound, rows, threads = run(go())
    assert again.duplicate and again.received_id == first.received_id
    assert (bound["task_id"], bound["as"]) == (here, "steer")
    assert not [
        r
        for r in rows
        if r["type"].startswith("reaction.") and r["payload"]["received_id"] == first.received_id
    ]
    (steered,) = [
        r["payload"]
        for r in rows
        if r["type"] == "message.steered" and r["payload"]["received_id"] == first.received_id
    ]
    assert intake.chat_line(steered).endswith(f"Tom Counsell reacted :+1: to your post {at(50)}")
    assert at(50) in threads and not any("/" in t for t in threads)


# -- the canvas -------------------------------------------------------------------------


class CanvasWire(SimpleNamespace):
    team_id = "T0ABC"
    user_id = "UVALOR"

    def __init__(self, tabs=(), fail=None):
        super().__init__(calls=[], tabs=list(tabs), fail=fail)

    async def canvas_tab(self, channel, title):
        self.calls.append(("tab", channel, title))
        return next((t for t, name in self.tabs if name == title), None)

    async def create_canvas(self, channel, title, markdown):
        if self.fail:
            raise self.fail
        self.calls.append(("create", channel, title, markdown))
        self.tabs.append(("F0NEW", title))
        return "F0NEW"

    async def replace_canvas(self, canvas_id, markdown):
        if self.fail:
            raise self.fail
        self.calls.append(("replace", canvas_id, markdown))

    async def permalink(self, file_id):
        return f"https://x.slack.com/docs/T0ABC/{file_id}"

    async def members(self, channel):
        return ["UVALOR", "UTOM", "ULEWIS"]

    async def user_name(self, user):
        return {"UTOM": "Tom Counsell", "ULEWIS": "Logan Pratt"}[user]


def write(wire, markdown="# Valor Decisions"):
    kernel = SimpleNamespace(split_text=None, Failed=broker.Failed, Unknown=broker.Unknown)
    sender = Sender(wire, kernel)
    action = broker.Action("slack.write_canvas", "t0abc/C0TEAM", {"markdown": markdown})
    return sender, action


def test_the_canvas_is_made_once_then_replaced_whole_and_a_refusal_fails():
    wire = CanvasWire()
    sender, action = write(wire)
    made = run(sender.write_canvas(action, "k"))
    again = run(sender.canvas_lookup(action, "k", ""))
    url = "https://x.slack.com/docs/T0ABC/F0NEW"
    assert made == again == {"canvas_id": "F0NEW", "url": url, "readers": ["Tom Counsell", "Logan Pratt"]}
    assert [c[0] for c in wire.calls] == ["tab", "create", "tab", "replace"]
    assert wire.calls[1][1:3] == ("C0TEAM", BOARD) == ("C0TEAM", "Valor Decisions")
    toms = CanvasWire(tabs=[("F0TOMS", BOARD)])
    sender, action = write(toms)
    assert run(sender.write_canvas(action, "k"))["canvas_id"] == "F0TOMS" and toms.calls[-1][:2] == (
        "replace",
        "F0TOMS",
    )
    sender, action = write(CanvasWire(fail=Refused("not_allowed")))
    with pytest.raises(broker.Failed, match="not_allowed"):
        run(sender.write_canvas(action, "k"))
    assert run(sender.canvas_lookup(action, "k", "")) is None
    sender, action = write(CanvasWire(fail=InDoubt("cut")))
    with pytest.raises(broker.Unknown):
        run(sender.write_canvas(action, "k"))


def test_a_canvas_write_needs_markdown_and_owes_no_report_to_its_own_chat(dsn, dm):
    from core import bridge

    chat = channel()
    declared = bridge.DECLARED["slack.write_canvas"]

    async def go():
        task = await new_task(dsn, chat={"channel": "slack", "chat_id": chat}, max_effect_class="act")
        effect = {
            "action_type": "slack.write_canvas",
            "effect_class": "act",
            "target": chat,
            "payload": {"markdown": "x"},
        }
        async with await db.connect(dsn) as conn:
            empty = await declared.refuse(conn, broker.Action("slack.write_canvas", chat, {"markdown": " "}))
            fine = await declared.refuse(conn, broker.Action("slack.write_canvas", chat, {"markdown": "# x"}))
            owed = await notices.report(
                conn, task, {**effect, "effect_id": ledger.new_id()}, {"canvas_id": "F"}
            )
        return empty, fine, owed

    empty, fine, owed = run(go())
    assert empty == "the canvas needs its markdown" and fine is None and owed is None


def test_the_manifest_asks_for_reactions_and_canvases():
    m = slack_main.manifest()
    assert {"reactions:read", "canvases:write"} <= set(m["oauth_config"]["scopes"]["user"])
    assert "reaction_added" in m["settings"]["event_subscriptions"]["user_events"]
