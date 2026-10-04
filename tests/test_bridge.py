"""The bridge port on real Postgres, with a fake bridge: the outbox, the
declared send types, their refusals and limits, and reconcile."""

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
from unittest import mock

import pytest

from core import broker, db, notices, tasks
from core.__main__ import _performers
from core.bridge import (
    DECLARED,
    LIMITS,
    NoticeDue,
    Release,
    split_text,
)
from tests import bridges
from tests.bridges import FakeBridge, declared, new_task, of_type

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


def send(text="hi", **more) -> broker.Action:
    return broker.Action("telegram.send_message", bridges.OPERATOR_CHAT, {"text": text, **more})


async def held(dsn, task, action) -> str:
    async with await db.connect(dsn) as conn:
        out = await broker.request(conn, declared(), task, action)
    assert out.kind == "pending", out
    return out.effect_id


async def approved(dsn, task, effect_id) -> str:
    """Tom's approval and the kernel's release: the release is requested of
    the bridge."""
    async with await db.connect(dsn) as conn:
        approval = await broker.approve(conn, effect_id, note="approve")
        out = await broker.release(conn, declared(), effect_id)
    assert out.kind == "released"
    return approval


def test_release_requested_then_performed(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held(dsn, task, send())
        approval = await approved(dsn, task, effect)
        assert await of_type(dsn, "release.requested", effect_id=effect)
        assert not await of_type(dsn, "effect.intent", effect_id=effect)
        bridge = FakeBridge()
        async with bridges.outbox(dsn, bridge) as box:
            due = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            assert [i.effect_id for i in due] == [effect]
            out = await box.perform(due[0])
            again = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
        assert out.kind == "done" and len(bridge.performed) == 1 and again == []
        (intent,) = await of_type(dsn, "effect.intent", effect_id=effect)
        (outcome,) = await of_type(dsn, "effect.outcome", effect_id=effect)
        assert intent["approval_id"] == approval and intent["action_type"] == "telegram.send_message"
        assert outcome["kind"] == "done" and outcome["result"]["sent"][0]["chat_id"] == bridges.OPERATOR_CHAT

    run(go())


def test_refused_release_yielded_once(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held(dsn, task, send())
        await approved(dsn, task, effect)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        bridge = FakeBridge()
        async with bridges.outbox(dsn, bridge) as box:
            (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            first = await box.perform(item)
            second = await box.perform(item)
            left = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
        assert first.kind == "refused" and second.kind == "refused" and left == []
        assert bridge.performed == []
        assert len(await of_type(dsn, "effect.refused", effect_id=effect)) == 1
        owed = await of_type(dsn, "notice.requested", about_key=f"effect-refused:{effect}")
        assert len(owed) == 1

    run(go())


def test_bridge_crash_between_intent_and_outcome(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held(dsn, task, send())
        await approved(dsn, task, effect)
        bridge = FakeBridge()
        bridge.hang = asyncio.Event()
        async with bridges.outbox(dsn, bridge) as box, bridges.outbox(dsn, bridge) as other:
            (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            performing = asyncio.create_task(box.perform(item))
            while not bridge.performed:
                await asyncio.sleep(0.02)
            # The first perform holds the effect's lock: no second perform.
            assert await other.reconcile() == []
            assert [i for i in await other.due() if isinstance(i, Release) and i.effect_id == effect] == []
            # The bridge dies with the intent written and no outcome.
            performing.cancel()
            await asyncio.gather(performing, return_exceptions=True)
            assert await of_type(dsn, "effect.intent", effect_id=effect)
            assert not await of_type(dsn, "effect.outcome", effect_id=effect)
            # The restarted bridge reconciles through lookup.
            settled = await other.reconcile()
        assert [o.kind for o in settled] == ["done"]
        assert len(bridge.performed) == 1
        (outcome,) = await of_type(dsn, "effect.outcome", effect_id=effect)
        assert outcome["reconciled"] is True

    run(go())


@pytest.mark.parametrize("how", ["perform", "lookup"])
def test_unknown_leaves_intent(dsn, op, how):
    async def go():
        task = await new_task(dsn)
        effect = await held(dsn, task, send())
        await approved(dsn, task, effect)
        bridge = FakeBridge()
        if how == "perform":
            bridge.fail = broker.Unknown("no answer")
        else:
            bridge.fail = RuntimeError("timeout")
            bridge.lookup_fail = broker.Unknown("no answer")
        async with bridges.outbox(dsn, bridge) as box:
            (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            out = await box.perform(item)
            assert out.kind == "unknown"
            assert await of_type(dsn, "effect.intent", effect_id=effect)
            assert not await of_type(dsn, "effect.outcome", effect_id=effect)
            bridge.store.clear()  # the send did not land
            again = await box.reconcile()
        # A lookup that answers settles it; one that does not leaves it in flight.
        if how == "perform":
            assert [o.kind for o in again] == ["failed"]
        else:
            assert again == []
            assert not await of_type(dsn, "effect.outcome", effect_id=effect)

    run(go())


def test_a_definite_failure_is_failed_and_asks_no_lookup(dsn, op):
    async def go():
        task = await new_task(dsn)
        effect = await held(dsn, task, send())
        await approved(dsn, task, effect)
        bridge = FakeBridge()
        bridge.fail = broker.Failed("refused")
        bridge.lookup_fail = AssertionError("a definite failure asks no lookup")
        async with bridges.outbox(dsn, bridge) as box:
            (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            out = await box.perform(item)
        (row,) = await of_type(dsn, "effect.outcome", effect_id=effect)
        return out, row

    out, row = run(go())
    assert out.kind == "failed" and row["kind"] == "failed" and "refused" in row["error"]


def test_two_identical_sends(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            a = await broker.request(conn, declared(), task, send(), request_id="t1/a.json")
            b = await broker.request(conn, declared(), task, send(), request_id="t1/b.json")
            a2 = await broker.request(conn, declared(), task, send(), request_id="t1/a.json")
        return a, b, a2

    a, b, a2 = run(go())
    assert a.effect_id != b.effect_id
    assert a2.effect_id == a.effect_id and a2.kind == "pending"


def test_notice_crash_before_sent(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            notice = await notices.request(conn, task, kind="test", about_key="test:1", text="hello")
            twice = await notices.request(conn, task, kind="test", about_key="test:1", text="hello")
        assert notice and twice is None
        bridge = FakeBridge()
        async with bridges.outbox(dsn, bridge) as box:
            first = [i for i in await box.due() if isinstance(i, NoticeDue) and i.notice_id == notice]
            # The bridge died after sending, before `sent`: yielded again,
            # and its text carries the id its lookup matches on.
            second = [i for i in await box.due() if isinstance(i, NoticeDue) and i.notice_id == notice]
            assert len(first) == len(second) == 1
            assert first[0].text.endswith(notices.tag(notice))
            sent = [{"channel": "telegram", "chat_id": bridges.OPERATOR_CHAT, "message_id": "9"}]
            await box.sent(second[0], sent)
            await box.sent(second[0], sent)
            left = [i for i in await box.due() if isinstance(i, NoticeDue) and i.notice_id == notice]
        assert left == []
        assert len(await of_type(dsn, "notice.sent", notice_id=notice)) == 1

    run(go())


def test_files_refused_alike_and_never_read(dsn, op, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report.txt").write_text("the report")
    (ws / "hosts").symlink_to("/etc/hosts")
    (ws / "etc").symlink_to("/etc")
    os.mkfifo(ws / "pipe")
    (ws / "copy.txt").write_text("a copy")
    (ws / "twin").hardlink_to(ws / "copy.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("not the task's")
    named = {
        "link": ws / "hosts",
        "linked dir": ws / "etc" / "hosts",
        "missing": ws / "nothing.txt",
        "outside": outside,
        "absent outside": tmp_path / "absent.txt",
        "dotdot": f"{ws}/../outside.txt",
        "fifo": ws / "pipe",
        "hard link": ws / "twin",
        "relative": "report.txt",
    }
    sent = declared(str(ws)).get("telegram.send_message")

    async def go():
        said = {}
        with contextlib.ExitStack() as stack:
            for p in (mock.patch("builtins.open", _no_read), mock.patch("os.read", _no_read)):
                stack.enter_context(p)
            for what, path in named.items():
                said[what] = await sent.refuse(None, send(files=[{"path": str(path), "sha256": "0" * 64}]))
            # A regular file in the workspace is sized, never hashed: its
            # bytes are the bridge's `perform` to check.
            good = await sent.refuse(None, send(files=[{"path": str(ws / "report.txt"), "sha256": "0" * 64}]))
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            out = await broker.request(
                conn, declared(str(ws)), task, send(files=[{"path": str(ws / "hosts"), "sha256": "0" * 64}])
            )
            nowhere = await broker.request(
                conn, declared(), task, send(files=[{"path": str(ws / "report.txt"), "sha256": "0" * 64}])
            )
        return said, good, out, nowhere

    said, good, out, nowhere = run(go())
    assert set(said.values()) == {"files[0] is not a regular file in the task's workspace"}, said
    assert good is None
    assert out.kind == "refused" and "not a regular file in the task's workspace" in out.error
    # A task with no workspace sends no file.
    assert nowhere.kind == "refused"


SHAPE = "files must be a list of {path, sha256} objects"
NOT_IN_WS = "is not a regular file in the task's workspace"


@pytest.mark.parametrize(
    "files, said",
    [
        # Not the port's shape: refused for its shape, whatever it holds.
        (["x"], SHAPE),
        ([None], SHAPE),
        (5, SHAPE),
        ({"a": 1}, SHAPE),
        ([[1]], SHAPE),
        ({"path": "<ws>/a.txt", "sha256": "0" * 64}, SHAPE),
        ("<ws>/a.txt", SHAPE),
        (["<ws>/a.txt"], SHAPE),
        ([{"path": "<ws>/a.txt"}], SHAPE),
        ([{"path": "<ws>/a.txt", "sha256": 5}], SHAPE),
        ([{"path": 5, "sha256": "0" * 64}], SHAPE),
        (0, SHAPE),
        ("", SHAPE),
        ({}, SHAPE),
        # The port's shape, naming a file the workspace does not hold.
        ([{"path": "<ws>/absent.txt", "sha256": "0" * 64}], NOT_IN_WS),
        (
            [{"path": "<ws>/a.txt", "sha256": "0" * 64}, {"path": "<ws>/absent.txt", "sha256": "0" * 64}],
            NOT_IN_WS,
        ),
    ],
    ids=repr,
)
def test_malformed_files_refused_at_request(dsn, op, tmp_path, files, said):
    """`files` absent or None is no files; otherwise it is a list of
    objects each with a string path and sha256, or the send is refused for
    its shape without the value echoed. A well-formed entry whose path is
    not a regular file in the workspace gets the file answer. Either way
    the refusal is ledgered, so the turn is collected."""
    (tmp_path / "a.txt").write_text("a")
    files = json.loads(json.dumps(files).replace("<ws>", str(tmp_path)))

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            return task, await broker.request(conn, declared(str(tmp_path)), task, send(files=files))

    task, out = run(go())
    assert out.kind == "refused" and out.error.endswith(said), out
    if said == SHAPE:
        assert out.error == SHAPE, out
    refused = run(of_type(dsn, "effect.refused", effect_id=out.effect_id))
    assert [r["task_id"] for r in refused] == [task], refused


@pytest.mark.parametrize("files", [None, [], [{"path": "<ws>/a.txt", "sha256": "0" * 64}]], ids=repr)
def test_well_formed_files_pass_to_sizing(dsn, op, tmp_path, files):
    """No files, or a list of {path, sha256} naming a regular file in the
    workspace, is sized and held for Tom."""
    (tmp_path / "a.txt").write_text("a")
    files = json.loads(json.dumps(files).replace("<ws>", str(tmp_path)))

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            return await broker.request(conn, declared(str(tmp_path)), task, send(files=files))

    out = run(go())
    assert out.kind == "pending", out


def test_a_nul_in_a_file_path_gets_the_file_answer(op, tmp_path):
    """A path holding a NUL character names no file, so it gets the one
    file answer rather than an error from the open."""
    (tmp_path / "a").write_text("a")
    performer = declared(str(tmp_path)).get("telegram.send_message")
    for path in (f"{tmp_path}/a\x00.txt", f"{tmp_path}/a\x00/b.txt"):
        said = run(performer.refuse(None, send(files=[{"path": path, "sha256": "0" * 64}])))
        assert said == "files[0] is not a regular file in the task's workspace"


def _no_read(*a, **k):
    raise AssertionError(f"the kernel read {a[:1]}")


def test_oversize_file_refused_at_request(dsn, op, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    limit = LIMITS["telegram"].max_file_bytes
    big, edge = ws / "big.bin", ws / "edge.bin"
    for f, size in ((big, limit + 1), (edge, limit)):
        with f.open("wb") as fh:  # sparse: sized, never written or read
            fh.truncate(size)
    f = ws / "a.bin"
    f.write_bytes(b"x" * 3000)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    to = bridges.OPERATOR_EMAIL

    def email(body: str, files: bool = True) -> broker.Action:
        attached = [{"path": str(f), "sha256": sha}] if files else []
        return broker.Action("email.send", to, {"to": [to], "subject": "s", "body": body, "files": attached})

    async def go():
        task = await new_task(dsn)
        sized = dataclasses.replace(
            LIMITS["email"], message_bytes=lambda a, sizes: len(a.payload["body"]) + sum(sizes)
        )
        performers = declared(str(ws))
        async with await db.connect(dsn) as conn:
            with mock.patch("builtins.open", _no_read), mock.patch("os.read", _no_read):
                over_file = await broker.request(
                    conn, performers, task, send(files=[{"path": str(big), "sha256": "0" * 64}])
                )
                at_file = await broker.request(
                    conn, performers, task, send(files=[{"path": str(edge), "sha256": "0" * 64}])
                )
            # The email size function measures the whole encoded message.
            measured = await broker.request(conn, performers, task, email("b" * 30_000_000))
            with mock.patch.dict(LIMITS, {"email": sized}):
                over = await broker.request(conn, performers, task, email("b" * (25_000_000 - 2999)))
                under = await broker.request(conn, performers, task, email("b" * (25_000_000 - 3000)))
            nobody = await broker.request(
                conn, performers, task, broker.Action("email.send", to, {"to": [], "body": "x"})
            )
        return over_file, at_file, measured, over, under, nobody

    over_file, at_file, measured, over, under, nobody = run(go())
    assert over_file.kind == "refused" and f"over telegram's limit of {limit} bytes" in over_file.error
    assert at_file.kind == "pending"
    assert LIMITS["email"].max_message_bytes == 25_000_000
    assert measured.kind == "refused" and "over email's limit of 25000000 bytes" in measured.error
    assert over.kind == "refused" and "over email's limit of 25000000 bytes" in over.error
    assert under.kind == "pending"
    assert nobody.kind == "refused" and "no recipient" in nobody.error


def test_telegram_refuses_a_chat_it_does_not_receive_and_an_empty_send(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            elsewhere = await broker.request(
                conn, declared(), task, broker.Action("telegram.send_message", "777", {"text": "hi"})
            )
            empty = await broker.request(conn, declared(), task, send(" \n "))
        return elsewhere, empty

    elsewhere, empty = run(go())
    assert elsewhere.kind == "refused" and "not one this machine" in elsewhere.error
    assert empty.kind == "refused" and "both empty" in empty.error


def test_split_utf16():
    emoji = "\U0001f600"  # two UTF-16 code units
    parts = split_text("telegram", emoji * 2049)
    assert len(parts) == 2 and "".join(parts) == emoji * 2049
    assert all(len(p.encode("utf-16-le")) // 2 <= 4096 for p in parts)
    lines = ("a" * 3000 + "\n") * 2
    parts = split_text("telegram", lines)
    assert parts == ["a" * 3000 + "\n", "a" * 3000 + "\n"]
    words = "word " * 1000
    assert all(p.endswith(" ") for p in split_text("telegram", words)[:-1])
    assert split_text("telegram", "  \n ") == []
    assert split_text("email", "x" * 100_000) == ["x" * 100_000]


def test_declared_in_every_task(dsn, op):
    b = tasks.Brief(instruction="test", max_effect_class="act")
    built = _performers(b)
    for action_type in DECLARED:
        assert broker.declared(built.get(action_type))
    offered = "\n".join(built.offered())
    assert "`telegram.send_message`" in offered and "`email.send`" in offered


def test_tick_called(dsn, op):
    async def go():
        task = await new_task(dsn)
        bridge = FakeBridge()
        async with bridges.outbox(dsn, bridge) as box:

            async def until():
                async for item in box:
                    if isinstance(item, NoticeDue) and item.task_id == task:
                        return item

            waiting = asyncio.create_task(until())
            await asyncio.wait_for(bridge.ticked.wait(), 30)
            assert bridge.ticks >= 1 and not waiting.done()
            async with await db.connect(dsn) as conn:
                notice = await notices.request(conn, task, kind="test", about_key="tick", text="woken")
            item = await asyncio.wait_for(waiting, 5)
        assert isinstance(item, NoticeDue) and item.notice_id == notice

    run(go())


def test_a_killed_send_the_server_never_got_reconciles_failed(dsn, tmp_path):
    """A send whose perform ended with no outcome, absent at the server, is
    failed on the next reconcile: its lock file is free once the perform
    ended."""

    async def go():
        task = await new_task(dsn)
        to = bridges.OPERATOR_EMAIL
        mail = broker.Action("email.send", to, {"to": [to], "subject": "s", "body": "hello"})
        effect = await held(dsn, task, mail)
        await approved(dsn, task, effect)
        bridge = FakeBridge("email")
        bridge.hang = asyncio.Event()
        async with bridges.outbox(dsn, bridge) as box:
            (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect]
            performing = asyncio.create_task(box.perform(item))
            while not bridge.performed:
                await asyncio.sleep(0.02)
            performing.cancel()
            await asyncio.gather(performing, return_exceptions=True)
            bridge.store.clear()  # the send never reached the server
            first = await box.reconcile()
            again = await box.reconcile()
        return first, again

    with bridges.operator(tmp_path):
        first, again = run(go())
    assert [o.kind for o in first] == ["failed"]
    assert again == []


def test_outbox_listener_reconnects(dsn, op):
    """The listening connection drops: the next wake listens on a new one,
    and a notice wakes the outbox again."""

    async def go():
        task = await new_task(dsn)
        async with bridges.outbox(dsn, FakeBridge()) as box:
            dropped = box.listener
            await dropped.close()
            await asyncio.wait_for(box.wait(), 10)
            with bridges.configure(serve_tick_s=60):  # only the notice ends this wait
                waiting = asyncio.create_task(box.wait())
                await asyncio.sleep(0.05)
                async with await db.connect(dsn) as conn:
                    await notices.request(conn, task, kind="test", about_key="test:wake", text="wake")
                await asyncio.wait_for(waiting, 10)
            return dropped, box.listener

    dropped, listener = run(go())
    assert listener is not dropped and dropped.closed


def test_one_bridge_per_channel_and_machine(dsn, op):
    """A second bridge of the same channel on the same machine waits for
    the first to exit; another channel's runs beside it."""
    from core import bridge as port

    class Held(FakeBridge):
        def __init__(self, channel, name, entered):
            super().__init__(channel)
            self.name, self.entered = name, entered
            self.leave = asyncio.Event()

        async def run(self, outbox):
            self.entered.append(self.name)
            await self.leave.wait()

    async def serving(b):
        with pytest.raises(SystemExit):
            await port.serve(b, dsn)

    async def go():
        entered: list[str] = []
        first = Held("telegram", "first", entered)
        second = Held("telegram", "second", entered)
        email = Held("email", "email", entered)
        jobs = [asyncio.create_task(serving(first))]
        while "first" not in entered:
            await asyncio.sleep(0.05)
        jobs += [asyncio.create_task(serving(second)), asyncio.create_task(serving(email))]
        while "email" not in entered:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        while_held = list(entered)
        first.leave.set()
        while "second" not in entered:
            await asyncio.sleep(0.05)
        second.leave.set()
        email.leave.set()
        await asyncio.wait_for(asyncio.gather(*jobs), 30)
        return while_held, entered

    while_held, entered = run(go())
    assert sorted(while_held) == ["email", "first"]
    assert entered[-1] == "second"
