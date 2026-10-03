"""The bridge port on real Postgres, with a fake bridge: the outbox, the
declared send types, their refusals and limits, and reconcile."""

import asyncio
import dataclasses
import hashlib
from unittest import mock

import pytest

from core import broker, db, notices, tasks
from core.__main__ import _performers
from core.bridge import (
    DECLARED,
    LIMITS,
    Declared,
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
        out = await broker.request(conn, task, action, performers=declared())
    assert out.kind == "pending", out
    return out.effect_id


async def approved(dsn, task, effect_id) -> str:
    """Tom's approval and the kernel's release: the release is requested of
    the bridge."""
    async with await db.connect(dsn) as conn:
        approval = await broker.approve(conn, effect_id, note="approve")
        out = await broker.release(conn, effect_id, performers=declared())
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
            bridge.store.clear()  # the send did not land
            again = await box.reconcile()
        # Not yet past the settle time: the effect stays in flight.
        assert out.kind == "unknown" and again == []
        assert await of_type(dsn, "effect.intent", effect_id=effect)
        assert not await of_type(dsn, "effect.outcome", effect_id=effect)

    run(go())


def test_two_identical_sends(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            a = await broker.request(conn, task, send(), request_id="t1/a.json", performers=declared())
            b = await broker.request(conn, task, send(), request_id="t1/b.json", performers=declared())
            a2 = await broker.request(conn, task, send(), request_id="t1/a.json", performers=declared())
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


def test_file_hash_mismatch(dsn, op, tmp_path):
    f = tmp_path / "report.txt"
    f.write_text("the report")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()

    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            wrong = await broker.request(
                conn, task, send(files=[{"path": str(f), "sha256": "0" * 64}]), performers=declared()
            )
        assert wrong.kind == "refused" and "sha256 differs" in wrong.error
        effect = await held(dsn, task, send(files=[{"path": str(f), "sha256": sha}]))
        f.write_text("another report")
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="approve")
            # A release from the command line refuses and writes nothing.
            with pytest.raises(broker.Refused, match="sha256 differs"):
                await broker.release(conn, effect, performers=declared())
        assert not await of_type(dsn, "effect.refused", effect_id=effect)

    run(go())


def test_oversize_file_refused_at_request(dsn, op, tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 3000)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    to = bridges.OPERATOR_EMAIL

    def email(body: str, files: bool = True) -> broker.Action:
        attached = [{"path": str(f), "sha256": sha}] if files else []
        return broker.Action("email.send", to, {"to": [to], "subject": "s", "body": body, "files": attached})

    async def go():
        task = await new_task(dsn)
        sized = dataclasses.replace(LIMITS["email"], message_bytes=lambda a: len(a.payload["body"]) + 3000)
        async with await db.connect(dsn) as conn:
            # Until the email bridge sets the size function, no email is
            # refused for size.
            unmeasured = await broker.request(conn, task, email("b" * 30_000_000), performers=declared())
            with mock.patch.dict(LIMITS, {"email": sized}):
                over = await broker.request(
                    conn, task, email("b" * (25_000_000 - 2999)), performers=declared()
                )
                under = await broker.request(
                    conn, task, email("b" * (25_000_000 - 3000)), performers=declared()
                )
            nobody = await broker.request(
                conn, task, broker.Action("email.send", to, {"to": [], "body": "x"}), performers=declared()
            )
        return unmeasured, over, under, nobody

    unmeasured, over, under, nobody = run(go())
    assert LIMITS["email"].max_message_bytes == 25_000_000 and LIMITS["email"].message_bytes is None
    assert unmeasured.kind == "pending"
    assert over.kind == "refused" and "over email's limit of 25000000 bytes" in over.error
    assert under.kind == "pending"
    assert nobody.kind == "refused" and "no recipient" in nobody.error


def test_telegram_refuses_a_chat_it_does_not_receive_and_an_empty_send(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            elsewhere = await broker.request(
                conn,
                task,
                broker.Action("telegram.send_message", "777", {"text": "hi"}),
                performers=declared(),
            )
            empty = await broker.request(conn, task, send(" \n "), performers=declared())
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
            await asyncio.sleep(0.7)
            assert bridge.ticks >= 1 and not waiting.done()
            async with await db.connect(dsn) as conn:
                notice = await notices.request(conn, task, kind="test", about_key="tick", text="woken")
            item = await asyncio.wait_for(waiting, 5)
        assert isinstance(item, NoticeDue) and item.notice_id == notice

    run(go())


def test_settle_after_function(dsn, tmp_path):
    action = broker.Action("email.send", "a@b.c", {"to": ["a@b.c"], "subject": "s", "body": "b" * 1_000_000})
    with bridges.operator(tmp_path) as s:
        assert DECLARED["email.send"].settle(action) == s.reconcile_after_s
        sized = Declared("x", "act", "", "email", settle_after_s=lambda a: len(a.payload["body"]) / 1000)
        assert sized.settle(action) == 1000.0
        assert DECLARED["telegram.send_message"].settle(action) is None
        assert Declared("x", "act", "", "email", settle_after_s=5.0).settle(action) == 5.0

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
            soon = await box.reconcile()
            with bridges.configure(reconcile_after_s=0.0):
                await asyncio.sleep(0.1)
                later = await box.reconcile()
        return soon, later

    with bridges.operator(tmp_path):
        soon, later = run(go())
    assert soon == []
    assert [o.kind for o in later] == ["failed"]


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
