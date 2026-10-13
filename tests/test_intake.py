"""The inbound record and binding on real Postgres: one row per message,
the binding table, steering, and a task started by message."""

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core import broker, db, intake, ledger, machine, notices, serve, tasks, verdicts
from core.machine import State
from core.settings import resolve_model, resolve_seat
from tests import bridges, scripted
from tests.bridges import OPERATOR, OPERATOR_CHAT, OPERATOR_EMAIL, declared, new_task, of_type, rows
from tests.ports import listen
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
            conn, declared(), task, broker.Action("telegram.send_message", OPERATOR_CHAT, {"text": "hi"})
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


@pytest.mark.macos
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
        # By email, stop and any other word steer (email verified for this case).
        monkeypatch.setitem(intake.VERIFY, "email", lambda inbound: True)
        mail = {"channel": "email", "sender": OPERATOR_EMAIL, "chat": "thread-1"}
        mailed = await a_send(dsn, task, "email", "thread-1")
        out["email_stop"] = await say(dsn, msg("stop", reply_to=mailed, **mail))
        out["email_approve"] = await say(dsn, msg("approve", reply_to=mailed, **mail))
        monkeypatch.setitem(intake.VERIFY, "email", intake._unverified)
        # A reply `approve` to a send is plain steering: nothing waits on a tap.
        sent = await held_send(dsn, task)
        out["approve"] = await say(dsn, msg("approve", reply_to=await a_send(dsn, task)))
        # The open question: answer.
        waiting = await scripted.start(dsn, ws, judge="thin")
        await drive(dsn, waiting)
        await owed(dsn, waiting)
        f = machine.fold(await rows(dsn, waiting))
        q_target = await mark_sent(dsn, waiting, f"question:{f.open_question}")
        out["answer"] = await say(dsn, msg("a short one", reply_to=q_target))
        # Stop.
        out["stop"] = await say(dsn, msg(" STOP ", reply_to=target))
        return out, task, waiting, sent

    out, task, waiting, sent = run(go())
    assert out["unverified"]["as"] == "none" and out["stranger"]["as"] == "none"
    assert out["steer"]["as"] == "steer" and out["steer"]["task_id"] == task
    assert out["start"]["as"] == "start" and out["start"]["task_id"] not in (task, None)
    assert out["email_stop"]["as"] == "steer" and out["email_approve"]["as"] == "steer"
    assert run(binding_notices(dsn, out["email_stop"]["received_id"]))
    assert out["approve"]["as"] == "steer"
    (release,) = run(of_type(dsn, "release.requested", effect_id=sent))
    assert release["owner"] == "telegram"  # released when it was requested
    assert out["answer"]["as"] == "answer"
    assert machine.fold(run(rows(dsn, waiting))).state is State.CLARIFY
    assert out["stop"]["as"] == "stop"
    assert machine.fold(run(rows(dsn, task))).state is State.STOPPED


@pytest.mark.macos
def test_a_reply_to_the_ended_report_is_feedback_and_reopens_the_work(dsn, op):
    """A task Valor ended owes Tom one report that asks for nothing; a reply
    to it is feedback, which sends the work back to patch."""

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            assert await verdicts.end(conn, task, "merge", "the remote rejected the push")
            await notices.owe(conn, task)
            await notices.owe(conn, task)  # owed once
        (n,) = [r for r in await of_type(dsn, "notice.requested", kind="ended") if r["task_id"] == task]
        target = await mark_sent(dsn, task, n["about_key"])
        bound = await say(dsn, msg("try it on the other branch", reply_to=target))
        return task, n, bound, machine.fold(await rows(dsn, task))

    task, n, bound, f = run(go())
    assert "the remote rejected the push" in n["text"]
    assert notices.report_link(task) in n["text"] and "Reply to" not in n["text"]
    assert bound["as"] == "feedback" and bound["task_id"] == task
    assert f.state is State.PATCH and f.entry["type"] == "feedback.given"


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


async def merged_report(dsn, ws) -> tuple[str, str]:
    """A task merged by itself and the message id of its merge report."""
    task = await to_checks(dsn, ws)
    await scripted.checks(dsn, task)
    f = machine.fold(await rows(dsn, task))
    assert f.state is State.MERGED
    await owed(dsn, task)
    # A delivery that merged by itself is told once, by the merge's report.
    assert not [n for n in await of_type(dsn, "notice.requested", kind="delivered") if n["task_id"] == task]
    return task, await mark_sent(dsn, task, f"report:{f.merge_effect['effect_id']}")


@pytest.mark.macos
def test_a_reply_to_the_merge_report_is_feedback_and_a_stop_binds_none(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task, target = await merged_report(dsn, ws)
        out = {}
        other = await say(dsn, msg("and this", reply_to=await a_notice(dsn, task)))
        for text in ("stop", "Stop.", "rename the file"):
            bound = await say(dsn, msg(text, reply_to=target))
            out[text] = (bound, await binding_notices(dsn, bound["received_id"]))
        return task, out, other, machine.fold(await rows(dsn, task))

    task, out, other, f = run(go())
    for text in ("stop", "Stop."):
        bound, said = out[text]
        assert bound["as"] == "none" and any("merged; nothing was done" in n["text"] for n in said)
    bound, _ = out["rename the file"]
    assert bound["as"] == "feedback" and bound["task_id"] == task
    assert f.state is not State.STOPPED
    assert other["as"] == "none"  # only the merge report takes feedback after the merge


def test_near_stop(dsn, op):
    async def go():
        task = await new_task(dsn)
        target = await a_notice(dsn, task)
        out = []
        for text in ("Stop.", "stop it"):
            bound = await say(dsn, msg(text, reply_to=target))
            out.append((bound, await binding_notices(dsn, bound["received_id"])))
        return task, out

    task, out = run(go())
    for bound, said in out:
        assert bound["as"] == "steer"
        assert any("Not a stop" in n["text"] for n in said)
    assert machine.fold(run(rows(dsn, task))).state is not State.STOPPED


@pytest.mark.macos
def test_a_binding_that_fails_binds_none_and_the_next_reply_binds(dsn, op, tmp_path, monkeypatch):
    ws, _ = scripted.workspace(tmp_path)
    real = ledger.append

    async def dies(conn, task_id, type_, payload):
        if type_ == "feedback.given":
            raise RuntimeError("killed")
        return await real(conn, task_id, type_, payload)

    async def go():
        task, target = await merged_report(dsn, ws)
        monkeypatch.setattr(ledger, "append", dies)
        first = await say(dsn, msg("rename the file", reply_to=target))
        monkeypatch.setattr(ledger, "append", real)
        neither = [r for r in await rows(dsn, task) if r["type"] == "feedback.given"]
        second = await say(dsn, msg("rename the file", reply_to=target))
        return first, neither, second, await binding_notices(dsn, first["received_id"])

    first, neither, second, said = run(go())
    assert first["as"] == "none" and "killed" in first["error"] and said
    assert neither == []
    assert second["as"] == "feedback"


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


@pytest.mark.macos
def test_steer_mid_turn(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        scripted.steer(ws, sleep_once=3)
        task = await scripted.start(dsn, ws, judge="thin")
        target = await a_notice(dsn, task)
        driving = asyncio.create_task(drive(dsn, task))
        while not [r for r in await rows(dsn, task) if r["type"] == "turn.started"]:
            if driving.done():
                driving.result()  # a drive that failed fails the test with its own error
                raise AssertionError("the drive ended before a turn started")
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


@pytest.mark.macos
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


@pytest.mark.macos
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
        await gateway.start(port=listen())
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
    assert b.workspace, [r["payload"] for r in written if r["type"] == "workspace.failed"]
    types = [r["type"] for r in written]
    assert "workspace.provisioned" in types and "turn.ended" in types


def test_a_failed_provision_is_tried_again_and_then_the_task_ends(dsn, op, tmp_path, monkeypatch):
    """No notice per failure and no wait on a steer: the kernel provisions
    again once the delay after the last failure has passed (none here),
    and at `PROVISION_TRIES` failures the task ends and Tom gets one
    report."""
    toy_spec(tmp_path, tmp_path / "no-such-repo")
    monkeypatch.setattr(serve, "PROVISION_BACKOFF_S", 0.0)

    async def go():
        bound = await say(dsn, msg("Write Tom a greeting.", chat="-100555"))
        task = bound["task_id"]
        kernel = only(serve.Kernel(None, {}, None, dsn), task)
        async with await db.connect(dsn) as conn:
            for _ in range(serve.PROVISION_TRIES + 1):
                await kernel.schedule(conn)
                await asyncio.gather(*kernel.jobs.values())
            await notices.owe(conn, task)
        return await rows(dsn, task)

    written = run(go())
    types = [r["type"] for r in written]
    assert types.count("workspace.failed") == serve.PROVISION_TRIES and "task.ended" in types
    assert machine.fold(written).state is State.ENDED
    owed_ = [r["payload"] for r in written if r["type"] == "notice.requested"]
    assert [n["kind"] for n in owed_] == ["ended"]
    assert "could not set up its workspace" in owed_[0]["text"]


def test_a_provision_is_due_after_a_doubling_delay_and_a_steer_counts_again():
    t0 = datetime(2026, 10, 10, tzinfo=UTC)

    def failed(i, minutes):
        return {"id": i, "type": "workspace.failed", "payload": {}, "at": t0 + timedelta(minutes=minutes)}

    one = [failed(1, 0)]
    two = [*one, failed(2, 5)]
    assert serve._provision_due([]) is True
    assert not serve._provision_due(one, t0 + timedelta(seconds=serve.PROVISION_BACKOFF_S - 1))
    assert serve._provision_due(one, t0 + timedelta(seconds=serve.PROVISION_BACKOFF_S))
    later = t0 + timedelta(minutes=5)
    assert not serve._provision_due(two, later + timedelta(seconds=2 * serve.PROVISION_BACKOFF_S - 1))
    assert serve._provision_due(two, later + timedelta(seconds=2 * serve.PROVISION_BACKOFF_S))
    spent = [failed(i, i) for i in range(serve.PROVISION_TRIES)]
    assert not serve._provision_due(spent, t0 + timedelta(days=1))
    steered = [*spent, {"id": 99, "type": "message.steered", "payload": {}, "at": t0}]
    assert serve._provision_due(steered, t0)


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
        out, seats = {}, {}
        async with await db.connect(dsn) as conn:
            for name, bound in (("project", project), ("elsewhere", elsewhere), ("mailed", mailed),
                                ("unknown_reply", unknown_reply)):  # fmt: skip
                brief = await tasks.brief(conn, bound["task_id"])
                out[name] = (bound["as"], brief.project)
                assert brief.max_effect_class == "act"  # a task Tom starts from any channel runs at act
                seats[name] = (brief.harness_name, brief.model)
        return out, seats, empty

    out, seats, empty = run(go())
    assert out["project"] == ("start", {"name": "toy"})
    assert out["mailed"] == ("start", {"name": "toy"})
    assert out["elsewhere"] == ("start", None)  # no valor spec in this projects directory
    assert out["unknown_reply"][0] == "start"
    assert empty["as"] == "none"
    # Every start runs on the frontier seat, never the light one (task 814d4aa2403e, 2026-10-07).
    for seat in seats.values():
        assert seat == resolve_seat("frontier")
        assert seat[1] != resolve_model("light")
    assert len(seats) == 4


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
    assert (brief.harness_name, brief.model) == resolve_seat("frontier")


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


def test_highest_and_lowest_are_scoped_by_chat(dsn, op):
    chat = uuid.uuid4().hex

    async def go():
        async with await db.connect(dsn) as conn:
            empty = (
                await intake.lowest(conn, "telegram", chat),
                await intake.highest(conn, "telegram", chat),
            )
            for mid in ("90", "7", "300", "not-a-number"):
                await intake.receive(conn, msg("x", mid=mid, chat=chat))
            await intake.receive(conn, msg("y", mid="1", chat=uuid.uuid4().hex))
            await intake.receive(conn, msg("z", mid="2", channel="email", sender=OPERATOR_EMAIL, chat=chat))
            return empty, (
                await intake.lowest(conn, "telegram", chat),
                await intake.highest(conn, "telegram", chat),
            )

    empty, found = run(go())
    assert empty == (None, None)
    assert found == (7, 300)


TEAM = "T0ABC"
TOM_SLACK = f"{TEAM}/U0TOM"


def slack(text, ts, *, chat=TOM_SLACK, sender=TOM_SLACK, root=None):
    """A Slack record: a thread reply names the root as `reply_to` and
    `topic_id`."""
    return intake.Inbound(
        channel="slack",
        chat_id=chat,
        chat_kind="dm" if chat == sender else "group",
        message_id=ts,
        sender_id=sender,
        sender_name="Tom",
        sent_at="2026-10-03T00:00:00Z",
        text=text,
        reply_to=root,
        topic_id=root,
    )


async def slack_sent(dsn, task, chat, ts, thread_ts=None, *, notice=None) -> None:
    """Valor's post in a Slack chat: a send's outcome, or a notice's."""
    entry = [{"channel": "slack", "chat_id": chat, "message_id": ts, "thread_ts": thread_ts}]
    async with await db.connect(dsn) as conn:
        if notice is None:
            payload = {
                "effect_id": ledger.new_id(),
                "idempotency_key": "k",
                "kind": "done",
                "result": {"sent": entry},
                "error": None,
            }
            await ledger.append(conn, task, "effect.outcome", payload)
        else:
            await ledger.append(conn, task, "notice.sent", {"notice_id": notice, "sent": entry})


@pytest.fixture
def slack_op(op):
    with bridges.configure(operator_slack_ids=(TOM_SLACK,)):
        yield op


def test_slack_operator_ids_are_verified_owned_and_start_work(dsn, slack_op, tmp_path):
    """Tom's Slack DM is owned whatever the operator channel; his record
    starts a task; the same text from anyone else is context."""
    assert intake.owns("slack", TOM_SLACK) and intake.owns("slack", TOM_SLACK.lower())
    assert not intake.owns("slack", f"{TEAM}/U0ELSE")
    with bridges.configure(operator_channel="slack", operator_chat=f"{TEAM}/C0OPS"):
        assert intake.owned("slack") == [TOM_SLACK, f"{TEAM}/C0OPS"]
    (tmp_path / "projects" / "s.toml").write_text(
        'name = "s"\nrepo = "/r"\nkind = "plain"\nsuite = "true"\nchats = ["slack:t0abc/c0proj"]\n'
    )
    assert intake.owned("slack") == [TOM_SLACK, f"{TEAM}/C0PROJ"]

    async def go():
        tom = await say(dsn, slack("add a footer", "1760000000.000100", chat=f"{TEAM}/C0PROJ"))
        other = await say(dsn, slack("add a footer", "1760000000.000200", sender=f"{TEAM}/U0ELSE"))
        (rec,) = await of_type(dsn, "message.received", received_id=tom["received_id"])
        return tom, other, rec

    tom, other, rec = run(go())
    assert rec["verified"] and tom["as"] == "start" and other["as"] == "none"
    assert intake._project_for(rec).name == "s"


def test_a_slack_thread_reply_binds_to_the_newest_of_valors_posts_before_it(dsn, slack_op):
    """A reply names the root; it binds to the newest post of Valor's in
    the thread older than itself, found in the ledger with no cap, so a
    notice 58 replies deep still binds."""

    async def go():
        older, newer = await new_task(dsn), await new_task(dsn)
        root = "1760000000.000001"
        await say(dsn, slack("start something", root))
        await slack_sent(dsn, older, TOM_SLACK, "1760000000.000010", root)
        await slack_sent(dsn, newer, TOM_SLACK, "1760000000.000058", root)
        await slack_sent(dsn, older, TOM_SLACK, "1760000000.000070", root)  # after the reply
        reply = await say(dsn, slack("more like this", "1760000000.000060", root=root))
        top = "1760000001.000001"  # Valor's own top-level post is its thread's root
        await slack_sent(dsn, older, TOM_SLACK, top)
        on_top = await say(dsn, slack("thanks", "1760000001.000005", root=top))
        return older, newer, reply, on_top

    older, newer, reply, on_top = run(go())
    assert (reply["task_id"], reply["as"]) == (newer, "steer")
    assert (on_top["task_id"], on_top["as"]) == (older, "steer")


def test_slack_stop_binds_and_a_near_miss_steers_with_a_notice(dsn, slack_op):
    async def go():
        task = await new_task(dsn)
        await slack_sent(dsn, task, TOM_SLACK, "1760000002.000001")
        near = await say(dsn, slack("stop please", "1760000002.000002", root="1760000002.000001"))
        stop = await say(dsn, slack("Stop", "1760000002.000003", root="1760000002.000001"))
        return task, near, await binding_notices(dsn, near["received_id"]), stop

    task, near, said, stop = run(go())
    assert near["as"] == "steer" and stop["as"] == "stop" and stop["task_id"] == task
    (n,) = said
    assert n["channel"] == "slack" and n["chat_id"] == TOM_SLACK and n["reply_to"] == "1760000002.000002"


@pytest.mark.parametrize(
    ("channel", "chat", "sender", "own"),
    [
        ("telegram", OPERATOR_CHAT, OPERATOR, True),  # the operator chat
        ("telegram", OPERATOR, OPERATOR, True),  # Tom's DM
        ("telegram", "-1009", OPERATOR, False),  # a project group
        ("slack", TOM_SLACK, TOM_SLACK, True),  # Tom's DM
        ("slack", f"{TEAM}/C0CLIENT", TOM_SLACK, False),  # a client channel
    ],
)
def test_a_binding_notice_goes_in_reply_only_in_toms_own_chat(dsn, slack_op, channel, chat, sender, own):
    """A reply to a stopped task owes a notice: in reply where the chat is
    Tom's own, else to the operator channel and chat, so kernel text
    never reaches a group or a client."""

    async def go():
        task = await new_task(dsn)
        target = uuid.uuid4().hex
        entry = {"channel": channel, "chat_id": chat, "message_id": target}
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "notice.sent", {"notice_id": uuid.uuid4().hex, "sent": [entry]})
            await tasks.stop(conn, task, reason="test")
        inbound = dataclasses.replace(
            msg("one more thing", reply_to=target, channel=channel, sender=sender, chat=chat),
            topic_id=None,
        )
        bound = await say(dsn, inbound)
        return bound, await binding_notices(dsn, bound["received_id"])

    bound, (n,) = run(go())
    assert bound["as"] == "none"
    if own:
        assert (n["channel"], n["chat_id"]) == (channel, chat) and n["reply_to"]
    else:
        assert (n["channel"], n["chat_id"], n["reply_to"]) == ("telegram", OPERATOR_CHAT, None)


def test_slack_ledger_reads(dsn, slack_op):
    """`lowest_ts` compares a `ts` as text; `threads` lists the roots with
    recent activity; `root_of` resolves a message or a post to its root."""
    chat = f"{TEAM}/C{uuid.uuid4().hex[:8].upper()}"

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            empty = await intake.lowest_ts(conn, "slack", chat)
            for ts in ("1760000009.000002", "1759999999.999999", "1760000010.000001"):
                await intake.receive(conn, slack("x", ts, chat=chat, root=None))
            await intake.receive(conn, slack("r", "1760000011.000001", chat=chat, root="1760000009.000002"))
            await intake.receive(conn, msg("t", mid="1", chat=chat))
        await slack_sent(dsn, task, chat, "1760000012.000001")
        await slack_sent(dsn, task, chat, "1760000013.000001", "1760000010.000001")
        async with await db.connect(dsn) as conn:
            return (
                empty,
                await intake.lowest_ts(conn, "slack", chat),
                await intake.threads(conn, "slack", chat, "2000-01-01T00:00:00Z"),
                await intake.threads(conn, "slack", chat, "2999-01-01T00:00:00Z"),
                [
                    await intake.root_of(conn, "slack", chat, ts)
                    for ts in ("1760000011.000001", "1760000010.000001", "1760000013.000001", "9.1")
                ],
            )

    empty, low, recent, none, roots = run(go())
    assert empty is None and low == "1759999999.999999"
    assert recent == {"1760000009.000002", "1760000012.000001", "1760000010.000001"} and none == set()
    assert roots == ["1760000009.000002", "1760000010.000001", "1760000010.000001", None]


def test_a_slack_send_to_tom_owes_no_report(slack_op):
    to_tom = {"action_type": "slack.send_message", "target": TOM_SLACK.lower()}
    assert notices._to_tom(to_tom)
    assert not notices._to_tom({**to_tom, "target": f"{TEAM}/C0CLIENT"})
    with bridges.configure(operator_channel="slack", operator_chat=f"{TEAM}/C0OPS"):
        assert notices._to_tom({**to_tom, "target": f"{TEAM}/C0OPS"})


def test_highest_and_lowest_skip_ids_past_a_bigint(dsn, op):
    """An all-digit id longer than a bigint holds is not an integer id of
    the chat: neither raises, and the ids that fit still count."""
    chat = uuid.uuid4().hex
    biggest = "9223372036854775807"

    async def go():
        async with await db.connect(dsn) as conn:
            for mid in ("5", biggest, "9223372036854775808", "1" * 25, "0" * 30 + "3", "9" * 131073):
                await intake.receive(conn, msg("x", mid=mid, chat=chat))
            return (
                await intake.lowest(conn, "telegram", chat),
                await intake.highest(conn, "telegram", chat),
            )

    assert run(go()) == (3, int(biggest))


def test_a_notice_whose_text_the_ledger_cannot_store_is_written_in_kernel_words(dsn, op):
    """A report whose text Postgres jsonb refuses (a NUL character) is
    written with kernel text naming the effect and Postgres's reason, so
    Tom still hears of it."""

    async def go():
        task = await new_task(dsn)
        effect_id = ledger.new_id()
        effect = {
            "effect_id": effect_id,
            **broker.Action("telegram.send_message", "-100777", {"text": "a\x00b"}).describe(
                "act", False, effect_id
            ),
        }
        async with await db.connect(dsn) as conn:
            await notices.report(conn, task, effect, {})
        return task, effect_id, await of_type(dsn, "notice.requested")

    task, effect_id, written = run(go())
    [notice] = [n for n in written if n["about_key"] == f"report:{effect_id}"]
    assert notice["text"].startswith(f"Task {task} has a notice (report, report:{effect_id}) whose text ")
    assert len(notice["text"]) < 1000
