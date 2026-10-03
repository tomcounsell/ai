"""The inbound record and binding on real Postgres: one row per message,
the binding table, steering, and a task started by message."""

import asyncio
import dataclasses
import uuid

import pytest

from core import broker, db, intake, ledger, machine, notices, serve, tasks
from core.machine import State
from tests import bridges, scripted
from tests.bridges import OPERATOR, OPERATOR_CHAT, OPERATOR_EMAIL, declared, new_task, of_type, rows
from tests.test_pipeline import drive, to_checks

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


def msg(text, *, reply_to=None, channel="telegram", sender=OPERATOR, chat=OPERATOR_CHAT, mid=None):
    return intake.Inbound(
        channel=channel,
        chat_id=chat,
        chat_kind="dm" if channel == "telegram" else "email",
        message_id=mid or uuid.uuid4().hex,
        sender_id=sender,
        sender_name="Tom",
        sent_at="2026-10-03T00:00:00Z",
        text=text,
        reply_to=reply_to,
    )


async def say(dsn, inbound) -> dict:
    """Receive and bind one message; its binding."""
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await of_type(dsn, "message.bound", received_id=got.received_id)
    return bound


async def mark_sent(dsn, task, about_key) -> str:
    """The bridge sent the task's notice: the message id a reply names."""
    (n,) = [r for r in await of_type(dsn, "notice.requested", about_key=about_key) if r["task_id"] == task]
    mid = uuid.uuid4().hex
    async with await db.connect(dsn) as conn:
        await ledger.append(
            conn,
            task,
            "notice.sent",
            {
                "notice_id": n["notice_id"],
                "sent": [{"channel": "telegram", "chat_id": n["chat_id"], "message_id": mid}],
            },
        )
    return mid


async def a_notice(dsn, task) -> str:
    about = f"test:{uuid.uuid4().hex}"
    async with await db.connect(dsn) as conn:
        await notices.request(conn, task, kind="test", about_key=about, text="about the task")
    return await mark_sent(dsn, task, about)


async def a_send(dsn, task, channel="telegram", chat=OPERATOR_CHAT) -> str:
    """A message the task's bridge send put in the chat: its id."""
    mid = uuid.uuid4().hex
    async with await db.connect(dsn) as conn:
        await ledger.append(
            conn,
            task,
            "effect.outcome",
            {
                "effect_id": ledger.new_id(),
                "idempotency_key": "k",
                "kind": "done",
                "result": {"sent": [{"channel": channel, "chat_id": chat, "message_id": mid}]},
                "error": None,
            },
        )
    return mid


async def owed(dsn, task) -> None:
    async with await db.connect(dsn) as conn:
        await notices.owe(conn, task)


async def held_send(dsn, task) -> str:
    async with await db.connect(dsn) as conn:
        out = await broker.request(
            conn,
            task,
            broker.Action("telegram.send_message", OPERATOR_CHAT, {"text": "hi"}),
            performers=declared(),
        )
    return out.effect_id


async def binding_notices(dsn, received_id) -> list[dict]:
    return [
        r for r in await of_type(dsn, "notice.requested") if r["about_key"].startswith(f"reply:{received_id}")
    ]


def test_duplicate_inbound(dsn, op):
    async def go():
        once = msg("hello there")
        async with await db.connect(dsn) as conn:
            a = await intake.receive(conn, once)
            b = await intake.receive(conn, once)
        twice = msg("hello again")

        async def receive():
            async with await db.connect(dsn) as conn:
                return await intake.receive(conn, twice)

        c, d = await asyncio.gather(receive(), receive())
        async with await db.connect(dsn) as conn:
            await intake.bind(conn)
            await intake.bind(conn)
        return a, b, c, d, twice

    a, b, c, d, twice = run(go())
    assert a.received_id == b.received_id and not a.duplicate and b.duplicate
    assert c.received_id == d.received_id and {c.duplicate, d.duplicate} == {True, False}
    received = run(of_type(dsn, "message.received", message_id=twice.message_id))
    assert len(received) == 1
    assert len(run(of_type(dsn, "message.bound", received_id=a.received_id))) == 1


def test_binding_table(dsn, op, tmp_path, monkeypatch):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        out = {}
        task = await new_task(dsn)
        target = await a_notice(dsn, task)
        # Unverified, and not from the operator.
        out["unverified"] = await say(dsn, msg("hi", channel="email", sender=OPERATOR_EMAIL, chat="t1"))
        out["stranger"] = await say(dsn, msg("hi", sender="99", reply_to=target))
        # A reply steers; not a reply starts.
        out["steer"] = await say(dsn, msg("make it shorter", reply_to=target))
        out["start"] = await say(dsn, msg("write a haiku"))
        # By email, approve and stop steer (email verified for this case).
        monkeypatch.setitem(intake.VERIFY, "email", lambda inbound: True)
        mail = {"channel": "email", "sender": OPERATOR_EMAIL, "chat": "thread-1"}
        mailed = await a_send(dsn, task, "email", "thread-1")
        out["email_stop"] = await say(dsn, msg("stop", reply_to=mailed, **mail))
        out["email_approve"] = await say(dsn, msg("approve", reply_to=mailed, **mail))
        monkeypatch.setitem(intake.VERIFY, "email", intake._verified_email)
        # An effect notice: approve.
        effect = await held_send(dsn, task)
        await owed(dsn, task)
        e_target = await mark_sent(dsn, task, f"effect:{effect}")
        out["approve"] = await say(dsn, msg("Approve", reply_to=e_target))
        out["approve_again"] = await say(dsn, msg("approve", reply_to=e_target))
        # The open question: answer.
        waiting = await scripted.start(dsn, ws, judge="thin")
        await drive(dsn, waiting)
        await owed(dsn, waiting)
        f = machine.fold(await rows(dsn, waiting))
        q_target = await mark_sent(dsn, waiting, f"question:{f.open_question}")
        out["answer"] = await say(dsn, msg("a short one", reply_to=q_target))
        # Stop.
        out["stop"] = await say(dsn, msg(" STOP ", reply_to=target))
        return out, task, waiting, effect

    out, task, waiting, effect = run(go())
    assert out["unverified"]["as"] == "none" and out["stranger"]["as"] == "none"
    assert out["steer"]["as"] == "steer" and out["steer"]["task_id"] == task
    assert out["start"]["as"] == "start" and out["start"]["task_id"] not in (task, None)
    assert out["email_stop"]["as"] == "steer" and out["email_approve"]["as"] == "steer"
    assert run(binding_notices(dsn, out["email_stop"]["received_id"]))
    assert out["approve"]["as"] == "approve"
    assert out["approve_again"]["as"] == "none"
    (release,) = run(of_type(dsn, "release.requested", effect_id=effect))
    assert release["owner"] == "telegram"
    assert len(run(of_type(dsn, "approval.granted", effect_id=effect))) == 1
    assert out["answer"]["as"] == "answer"
    assert machine.fold(run(rows(dsn, waiting))).state is State.CLARIFY
    assert out["stop"]["as"] == "stop"
    assert machine.fold(run(rows(dsn, task))).state is State.STOPPED


def test_feedback_on_the_delivered_notice(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        f = machine.fold(await rows(dsn, task))
        assert f.state is State.MERGE
        await owed(dsn, task)
        target = await mark_sent(dsn, task, f"delivered:{f.delivery['candidate']['sha']}")
        return task, await say(dsn, msg("rename the file", reply_to=target))

    task, bound = run(go())
    assert bound["as"] == "feedback" and bound["task_id"] == task


def test_reply_to_bridge_send_binds(dsn, op):
    async def go():
        task = await new_task(dsn)
        mid = await a_send(dsn, task)
        return task, await say(dsn, msg("thanks, also add a footer", reply_to=mid))

    task, bound = run(go())
    assert bound["as"] == "steer" and bound["task_id"] == task


def test_reply_to_stopped_task(dsn, op):
    async def go():
        task = await new_task(dsn)
        target = await a_notice(dsn, task)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        bound = await say(dsn, msg("one more thing", reply_to=target))
        return task, bound, await binding_notices(dsn, bound["received_id"])

    task, bound, owed_ = run(go())
    assert bound["as"] == "none" and bound["task_id"] == task
    (n,) = owed_
    assert "stopped" in n["text"] and n["chat_id"] == OPERATOR_CHAT and n["reply_to"]


def test_near_approve(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held_send(dsn, task)
        await owed(dsn, task)
        target = await mark_sent(dsn, task, f"effect:{effect}")
        out = []
        for text in ("Approve.", "approve it"):
            bound = await say(dsn, msg(text, reply_to=target))
            out.append((bound, await binding_notices(dsn, bound["received_id"])))
        return effect, out

    effect, out = run(go())
    for bound, said in out:
        assert bound["as"] == "steer"
        assert any("Not an approval" in n["text"] for n in said)
        assert any(n["about_key"].endswith(":waiting") for n in said)
    assert not run(of_type(dsn, "approval.granted", effect_id=effect))


def test_approve_crash(dsn, op, monkeypatch):
    real = ledger.append

    async def dies(conn, task_id, type_, payload):
        if type_ == "release.requested":
            raise RuntimeError("killed")
        return await real(conn, task_id, type_, payload)

    async def go():
        task = await new_task(dsn)
        effect = await held_send(dsn, task)
        await owed(dsn, task)
        target = await mark_sent(dsn, task, f"effect:{effect}")
        monkeypatch.setattr(ledger, "append", dies)
        first = await say(dsn, msg("approve", reply_to=target))
        monkeypatch.setattr(ledger, "append", real)
        neither = (
            await of_type(dsn, "approval.granted", effect_id=effect),
            await of_type(dsn, "release.requested", effect_id=effect),
        )
        second = await say(dsn, msg("approve", reply_to=target))
        both = (
            await of_type(dsn, "approval.granted", effect_id=effect),
            await of_type(dsn, "release.requested", effect_id=effect),
        )
        return first, neither, second, both, await binding_notices(dsn, first["received_id"])

    first, neither, second, both, said = run(go())
    assert first["as"] == "none" and "killed" in first["error"] and said
    assert neither == ([], [])
    assert second["as"] == "approve" and [len(x) for x in both] == [1, 1]


def test_binding_error_binds_none(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held_send(dsn, task)
        await owed(dsn, task)
        target = await mark_sent(dsn, task, f"effect:{effect}")
        # Released from the command line before Tom's reply.
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="from the command line")
            await broker.release(conn, effect, performers=declared())
        late = await say(dsn, msg("approve", reply_to=target))
        again = await say(dsn, msg("approve", reply_to=target))
        after = await say(dsn, msg("and sign it", reply_to=target))
        return (
            late,
            again,
            after,
            await binding_notices(dsn, late["received_id"]),
            await binding_notices(dsn, again["received_id"]),
        )

    late, again, after, said_late, said_again = run(go())
    assert late["as"] == "none" and again["as"] == "none"
    assert "already released" in said_late[0]["text"] and said_again
    assert after["as"] == "steer"


def test_email_unverified(dsn, op):
    async def go():
        async with await db.connect(dsn) as conn:
            got = await intake.receive(
                conn, msg("start this", channel="email", sender=OPERATOR_EMAIL, chat="t9")
            )
        (row,) = await of_type(dsn, "message.received", received_id=got.received_id)
        return row, await say(dsn, msg("x", channel="email", sender=OPERATOR_EMAIL, chat="t9"))

    row, bound = run(go())
    assert row["verified"] is False and bound["as"] == "none"


def test_steer_while_awaiting_approval(dsn, op):
    async def go():
        task = await new_task(dsn)
        await held_send(dsn, task)
        target = await a_notice(dsn, task)
        bound = await say(dsn, msg("is it ready?", reply_to=target))
        return bound, await binding_notices(dsn, bound["received_id"])

    bound, said = run(go())
    assert bound["as"] == "steer"
    (n,) = said
    assert "waiting on approval" in n["text"]


def test_steer_mid_turn(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        scripted.steer(ws, sleep_once=3)
        task = await scripted.start(dsn, ws, judge="thin")
        target = await a_notice(dsn, task)
        driving = asyncio.create_task(drive(dsn, task))
        while not [r for r in await rows(dsn, task) if r["type"] == "turn.started"]:
            await asyncio.sleep(0.05)
        bound = await say(dsn, msg("use a haiku", reply_to=target))
        await driving
        # An interrupted turn does not spend it.
        turn_id = ledger.new_id()
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "waiting"})
            await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "interrupted"})
            pending = machine.fold(await ledger.read(conn, task)).steering
            await scripted_answer(conn, task)
        await drive(dsn, task)
        return bound, pending, machine.fold(await rows(dsn, task))

    bound, pending, f = run(go())
    assert bound["as"] == "steer"
    logged = scripted.turns(ws)
    assert "use a haiku" not in logged[0]["prompt"]
    assert [p["text"] for p in pending] == ["use a haiku"]
    assert "Tom wrote while you worked:\n- use a haiku" in logged[1]["prompt"]
    assert all("use a haiku" not in t["prompt"] for t in logged[2:])
    assert f.steering == []


async def scripted_answer(conn, task) -> None:
    from core import session

    await session.answer(conn, task, "a short one", by="test", via="the test suite", role_played=True)


def test_steer_during_checks(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        target = await a_notice(dsn, task)
        bound = await say(dsn, msg("keep the old name", reply_to=target))
        await scripted.checks(dsn, task, review="changes")
        await drive(dsn, task)
        return bound

    bound = run(go())
    assert bound["as"] == "steer"
    (patch,) = [t for t in scripted.turns(ws) if t["stage"] == "patch"]
    assert "- keep the old name" in patch["prompt"]


def toy_spec(tmp_path, repo, **more) -> None:
    chats = more.pop("chats", ['"telegram:-100555"'])
    lines = [
        'name = "toy"',
        f'repo = "{repo}"',
        'kind = "plain"',
        'suite = "true"',
        f"chats = [{', '.join(chats)}]",
        *(f'{k} = "{v}"' for k, v in more.items()),
    ]
    (tmp_path / "projects" / "toy.toml").write_text("\n".join(lines) + "\n")


def test_start_provisions(dsn, op, tmp_path):
    src = scripted.toy_repo(tmp_path)
    toy_spec(tmp_path, src)

    async def go():
        bound = await say(dsn, msg("Write Tom a greeting.", chat="-100555"))
        task = bound["task_id"]
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, task)
        assert b.project == {"name": "toy"} and b.workspace is None
        gateway = scripted_gateway(dsn)
        await gateway.start()
        kernel = only(serve.Kernel(gateway, scripted.RUNNERS, None, dsn), task)
        try:
            async with await db.connect(dsn) as conn:
                for _ in range(400):
                    await kernel.schedule(conn)
                    if [r for r in await rows(dsn, task) if r["type"] == "turn.ended"]:
                        break
                    await asyncio.sleep(0.05)
        finally:
            await kernel.close()
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return task, await tasks.brief(conn, task), await ledger.read(conn, task)

    _task, b, written = run(go())
    assert b.workspace
    types = [r["type"] for r in written]
    assert "workspace.provisioned" in types and "turn.ended" in types


def test_a_failed_provision_owes_a_notice(dsn, op, tmp_path):
    toy_spec(tmp_path, tmp_path / "no-such-repo")

    async def go():
        bound = await say(dsn, msg("Write Tom a greeting.", chat="-100555"))
        task = bound["task_id"]
        kernel = only(serve.Kernel(None, {}, None, dsn), task)
        async with await db.connect(dsn) as conn:
            await kernel.schedule(conn)
            await asyncio.gather(*kernel.jobs.values())
            await kernel.schedule(conn)  # failed, and not steered since: not tried again
            again = dict(kernel.jobs)
        return task, again, await rows(dsn, task)

    _task, again, written = run(go())
    assert again == {}
    assert [r["type"] for r in written].count("workspace.failed") == 1
    (n,) = [r["payload"] for r in written if r["type"] == "notice.requested"]
    assert n["kind"] == "workspace_failed"


def only(kernel: serve.Kernel, task: str) -> serve.Kernel:
    """A kernel that sees one task of the shared test database."""

    async def active(conn):
        return [task]

    kernel.active = active
    return kernel


def scripted_gateway(dsn):
    from core.gateway import Gateway

    return Gateway(dsn)


def test_start_rule(dsn, op, tmp_path, monkeypatch):
    src = scripted.toy_repo(tmp_path)
    toy_spec(tmp_path, src, chats=['"telegram:-100555"', '"email:Boss@Example.com"'])

    async def go():
        project = await say(dsn, msg("in the toy project", chat="-100555"))
        elsewhere = await say(dsn, msg("anywhere"))
        unknown_reply = await say(dsn, msg("a reply to nothing known", reply_to="123456"))
        empty = await say(dsn, msg("   "))
        monkeypatch.setitem(intake.VERIFY, "email", lambda inbound: True)
        with bridges.configure(operator_email=(OPERATOR_EMAIL, "boss@example.com")):
            mailed = await say(dsn, msg("by mail", channel="email", sender="boss@example.com", chat="t2"))
        out = {}
        async with await db.connect(dsn) as conn:
            for name, bound in (("project", project), ("elsewhere", elsewhere), ("mailed", mailed),
                                ("unknown_reply", unknown_reply)):  # fmt: skip
                out[name] = (bound["as"], (await tasks.brief(conn, bound["task_id"])).project)
        return out, empty

    out, empty = run(go())
    assert out["project"] == ("start", {"name": "toy"})
    assert out["mailed"] == ("start", {"name": "toy"})
    assert out["elsewhere"] == ("start", None)  # no valor spec in this projects directory
    assert out["unknown_reply"][0] == "start"
    assert empty["as"] == "none"


def test_owns(op, tmp_path):
    projects = tmp_path / "projects"
    (projects / "a.toml").write_text(
        'name = "a"\nrepo = "/r"\nkind = "plain"\nsuite = "true"\nchats = ["telegram:-1001", "email:A@B.com"]\n'
    )
    (projects / "b.toml").write_text(
        'name = "b"\nrepo = "/r"\nkind = "plain"\nsuite = "true"\nchats = ["telegram:-1002"]\nmachine = "other"\n'
    )
    assert intake.owns("telegram", OPERATOR_CHAT)
    assert intake.owns("telegram", "-1001")  # no machine: the default machine, this one
    assert not intake.owns("telegram", "-1002")
    assert intake.owns("email", "a@b.com") and intake.owns("email", "A@B.COM")
    assert intake.owns("email", OPERATOR_EMAIL)
    with bridges.configure(machine="other"):
        assert intake.owns("telegram", "-1002") and not intake.owns("telegram", "-1001")


def test_two_binders_bind_once(dsn, op, monkeypatch):
    """Two binders take the same message at once: one binding, one set of
    effects; the other writes nothing."""
    real = intake._bind
    arrived: dict[str, int] = {}
    both: dict[str, asyncio.Event] = {}

    async def racing(conn, p):
        rid = p["received_id"]
        arrived[rid] = arrived.get(rid, 0) + 1
        gate = both.setdefault(rid, asyncio.Event())
        if arrived[rid] == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 30)
        return await real(conn, p)

    monkeypatch.setattr(intake, "_bind", racing)

    async def binder():
        async with await db.connect(dsn) as conn:
            return await intake.bind(conn)

    async def go():
        task = await new_task(dsn)
        target = await a_notice(dsn, task)
        out = {}
        for name, inbound in (
            ("steer", msg("use the other branch", reply_to=target)),
            ("start", msg("new work")),
        ):
            async with await db.connect(dsn) as conn:
                got = await intake.receive(conn, inbound)
            await asyncio.gather(binder(), binder())
            out[name] = got.received_id
        return out

    out = run(go())
    for rid in out.values():
        assert len(run(of_type(dsn, "message.bound", received_id=rid))) == 1
    assert len(run(of_type(dsn, "message.steered", received_id=out["steer"]))) == 1
    (bound,) = run(of_type(dsn, "message.bound", received_id=out["start"]))
    assert len([r for r in run(rows(dsn, bound["task_id"])) if r["type"] == "task.started"]) == 1


def test_a_notice_with_no_chat_is_undeliverable(dsn, op):
    async def go():
        task = await new_task(dsn)
        with bridges.configure(operator_chat=None):
            async with await db.connect(dsn) as conn:
                notice = await notices.request(conn, task, kind="test", about_key="test:nowhere", text="hi")
        return task, notice

    task, notice = run(go())
    (row,) = [r["payload"] for r in run(rows(dsn, task)) if r["type"] == "notice.undeliverable"]
    assert row["notice_id"] == notice and "VALOR_OPERATOR_CHAT" in row["reason"]


def test_files_alone_start_a_task(dsn, op, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")
    inbound = dataclasses.replace(
        msg(""),
        attachments=[
            {"name": "shot.png", "mime": "image/png", "bytes": 3, "path": str(shot)},
            {"name": "big.mov", "mime": "video/quicktime", "bytes": 9, "skipped": "too large"},
        ],
    )

    async def go():
        bound = await say(dsn, inbound)
        async with await db.connect(dsn) as conn:
            return bound, await tasks.brief(conn, bound["task_id"])

    bound, brief = run(go())
    assert bound["as"] == "start"
    assert str(shot) in brief.instruction and "not downloaded: too large" in brief.instruction


def test_recorded_is_scoped_by_channel_and_chat(dsn, op):
    async def go():
        ids = [uuid.uuid4().hex for _ in range(4)]
        async with await db.connect(dsn) as conn:
            await intake.receive(conn, msg("a", mid=ids[0]))
            await intake.receive(conn, msg("b", mid=ids[1], chat="2000"))
            await intake.receive(
                conn, msg("c", mid=ids[2], channel="email", sender=OPERATOR_EMAIL, chat=OPERATOR_CHAT)
            )
            return ids, (
                await intake.recorded(conn, "telegram", OPERATOR_CHAT, ids),
                await intake.recorded(conn, "telegram", "2000", ids),
                await intake.recorded(conn, "email", OPERATOR_CHAT, ids),
            )

    ids, (here, other_chat, other_channel) = run(go())
    assert here == {ids[0]}
    assert other_chat == {ids[1]}
    assert other_channel == {ids[2]}


def test_claimed_reads_notices_and_sends_in_the_chat(dsn, op):
    async def go():
        task = await new_task(dsn)
        noticed = await a_notice(dsn, task)
        sent = await a_send(dsn, task)
        elsewhere = await a_send(dsn, task, chat="2000")
        by_email = await a_send(dsn, task, channel="email")
        async with await db.connect(dsn) as conn:
            return (noticed, sent, elsewhere, by_email), (
                await intake.claimed(conn, "telegram", OPERATOR_CHAT),
                await intake.claimed(conn, "telegram", "2000"),
                await intake.claimed(conn, "email", OPERATOR_CHAT),
            )

    (noticed, sent, elsewhere, by_email), (here, other_chat, other_channel) = run(go())
    assert {noticed, sent} <= here and not {elsewhere, by_email} & here
    assert elsewhere in other_chat and not {noticed, sent, by_email} & other_chat
    assert by_email in other_channel and not {noticed, sent, elsewhere} & other_channel
