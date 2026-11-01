"""The bridge, the real port, and the kernel's binding, end to end: the
emulator is Telegram and Tom; notices go out through the bridge and Tom's
replies come back through it."""

import asyncio
import socket
import sys
from types import SimpleNamespace

import pytest

from core import broker, db, intake, ledger, machine, notices, tasks
from core.machine import State
from tests import bridges, scripted
from tests.bridges import OPERATOR, VALOR_SPEC, new_task, of_type, rows
from tests.telegram_port import chat, child_env, connected, due, emu_chat, ids, release, send, until
from tests.telegram_port import machine as this_machine

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def op(emu, tmp_path):
    operator = chat()
    emu.control(chats=[emu_chat(operator)])
    with this_machine(tmp_path, [operator]):
        # The project the operator chat's session works under.
        (tmp_path / "projects" / "valor.toml").write_text(VALOR_SPEC)
        yield operator


run = asyncio.run


async def child(tmp_path, op, *args: str):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.telegram_child", *args, env=child_env(tmp_path, [op])
    )


async def tom(emu, dsn, chat_id, text, reply_to=None) -> dict:
    """Tom writes in the chat; the bridge records it; the kernel binds it."""
    mid = emu.inject(int(chat_id), text, sender_id=int(OPERATOR), sender_name="Tom", reply_to=reply_to)
    await until(lambda: _has(dsn, chat_id, mid))
    (got,) = await of_type(dsn, "message.received", chat_id=chat_id, message_id=str(mid))
    async with await db.connect(dsn) as conn:
        await intake.bind(conn)
    (bound,) = await of_type(dsn, "message.bound", received_id=got["received_id"])
    return bound


async def _has(dsn, chat_id, mid) -> bool:
    return str(mid) in await ids(dsn, chat_id)


async def replies_to(dsn, received_id) -> list[dict]:
    return [
        r for r in await of_type(dsn, "notice.requested") if r["about_key"].startswith(f"reply:{received_id}")
    ]


def test_a_message_is_recorded_once_through_intake(emu, dsn, tmp_path, op):
    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            mid = emu.inject(int(op), "hello from Tom", sender_id=7, sender_name="Tom")
            await until(lambda: _has(dsn, op, mid))
            await bridge.tick()
            [r] = await of_type(dsn, "message.received", chat_id=op)
            assert r["message_id"] == str(mid) and r["verified"] is True
            async with bridge.kernel.conn() as c:
                assert await bridge.kernel.lowest(c, "telegram", op) == mid
                assert await bridge.kernel.recorded(c, "telegram", op, [str(mid), "999"]) == {str(mid)}

    run(go())


def test_a_requested_send_goes_out_with_no_tap_and_a_reply_under_it_is_the_sessions(emu, dsn, tmp_path, op):
    async def go():
        task = await new_task(dsn)
        async with connected(emu.url, dsn, tmp_path) as bridge:
            async with await db.connect(dsn) as conn:
                held = await broker.request(conn, bridges.declared(), task, send(op, "the requested words"))
            assert held.kind == "released"
            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, held.effect_id))).kind == "done"
            assert "the requested words" in [m["text"] for m in emu.own(int(op))]
            assert not await of_type(dsn, "child.reported", about_key=f"report:{held.effect_id}")
            (outcome,) = await of_type(dsn, "effect.outcome", effect_id=held.effect_id)
            target = outcome["result"]["sent"][0]["message_id"]

            # Tom's reply under it is the operator chat's session's, with the post and its task.
            near = await tom(emu, dsn, op, "approve", reply_to=int(target))
            stopish = await tom(emu, dsn, op, "stop it now", reply_to=int(target))
            assert near["as"] == stopish["as"] == "steer" and near["task_id"] == stopish["task_id"] != task
            assert not await replies_to(dsn, stopish["received_id"])
            session_rows = await rows(dsn, near["task_id"])
            (first, _) = [r["payload"] for r in session_rows if r["type"] == "message.steered"]
            assert first["replying_to"] == {"text": "the requested words", "task_id": task}

            # Not a reply: the same session hears it, and no task starts from it.
            heard = await tom(emu, dsn, op, "write a haiku")
            assert heard["as"] == "steer" and heard["task_id"] == near["task_id"]
            async with await db.connect(dsn) as conn:
                b = await tasks.brief(conn, heard["task_id"])
                assert b.chat == {"channel": "telegram", "chat_id": op} and b.parent_id is None
                assert (await tasks.status(conn, heard["task_id"]))["spent_usd_micros"] == 0

    run(go())


def test_stop_by_reply_and_a_released_send_of_that_task_is_refused_once(emu, dsn, tmp_path, op):
    async def go():
        effect = await release(dsn, send(op, "never sent"))
        async with await db.connect(dsn) as conn:
            task = await scripted.task_of(conn, effect)
            said = await broker.request(conn, bridges.declared(), task, send(op, "about it"))
        async with connected(emu.url, dsn, tmp_path) as bridge:
            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, said.effect_id))).kind == "done"
            (sent,) = await of_type(dsn, "effect.outcome", effect_id=said.effect_id)
            under = int(sent["result"]["sent"][0]["message_id"])
            stop = await tom(emu, dsn, op, " STOP ", reply_to=under)
            assert stop["as"] == "stop"
            assert machine.fold(await rows(dsn, task)).state is State.STOPPED

            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, effect))).kind == "refused"
                assert effect not in [getattr(i, "effect_id", None) for i in await box.due()]
            assert len(await of_type(dsn, "effect.refused", effect_id=effect)) == 1
            assert "never sent" not in [m["text"] for m in emu.own(int(op))]
            after = await tom(emu, dsn, op, "go on", reply_to=under)
            assert after["as"] == "steer" and after["task_id"] == stop["task_id"]
            assert not await replies_to(dsn, after["received_id"])

    run(go())


HEAD = "Ship the quarterly invoices"
# What a notice to Tom never carries: ids, commits, chat ids, links, commands.
LEAKS = ("[n:", "127.0.0.1", "http", "python -m", "/", "deadbeef", "-100777", "E1", "keys")


def test_every_notice_names_the_work_by_its_request_and_carries_no_internals(dsn, tmp_path):
    from bridges.email import EmailBridge
    from core import ledger, serve

    async def go():
        async with await db.connect(dsn) as conn:
            brief = tasks.Brief(instruction=f"{HEAD}\nand the rest", max_effect_class="act")
            task = await tasks.start(conn, brief)
            head = await notices.headline(conn, task)
            why = [{"kind": "merge", "why": w, "reason": "! [rejected] deadbeef"} for w in notices.MERGE_WHY]
            ended = [
                {"kind": "decided", "text": "Not worth it."},
                {"kind": "step", "check": "test", "tries": 4, "reason": "/tmp/x: boom"},
                {"kind": "step", "state": "build", "tries": 4, "reason": "boom"},
                {"kind": "provision", "tries": 3, "reason": "git clone /x failed"},
                *why,
                {"kind": "merge", "reason": "deadbeef"},
                {"kind": "other", "reason": "deadbeef"},
            ]
            fold = lambda p: SimpleNamespace(entry={"type": "task.ended", "payload": p}, delivery=None)
            texts = [notices.ended_text(fold(p), head) for p in ended]
            assert texts[1] != texts[2] and len(set(texts[4 : 5 + len(why)])) == len(why) + 1
            for missing in (True, False):
                await notices.credential(conn, task, f"deadbeef{missing}", missing=missing)
            await ledger.append(conn, task, "effect.held", {"effect_id": "E1x", "target": "a@b.com"})
            await EmailBridge.in_doubt(object(), conn, "E1x")
            await notices.request(conn, task, kind="send_in_doubt", about_key="bad", text="\x00")
            requested = [r["text"] for r in await of_type(dsn, "child.reported") if r.get("from") == task]
            texts += requested + [
                notices.lead(head, serve._rollout_text(s, 3)) for s in (*serve.ROLLOUT_STEPS, "?")
            ]
            for text in texts:
                leaked = [s for s in (*LEAKS, task) if s in text]
                assert text.startswith(f'On "{HEAD}": ') and not leaked, text
            assert requested[0] != requested[1] and "may or may not have gone" in requested[2]
            assert "I could not store" in requested[-1]
            # No Brief (a failed binding names the channel): no empty quote.
            assert await notices.headline(conn, "telegram") is None
            unread = await intake._unread(
                conn, None, {"text": "do the thing", "channel": "telegram", "message_id": "9", "sender_name": "Kim"}
            )
            assert unread.startswith('Kim\'s message 9 on telegram ("do the thing") was not acted on')

    with this_machine(tmp_path, ["9001", "-100777"]):
        run(go())


def test_the_mailbox_connection_keeps_its_idle_path_alive():
    from bridges.email import smtp  # an IDLE the network would drop stays open, or is found dead

    with socket.create_server(("127.0.0.1", 0)) as server:
        c = object.__new__(smtp._IMAP)
        c.host, c.port, c.ends = "127.0.0.1", server.getsockname()[1], None
        c.ssl_context = SimpleNamespace(wrap_socket=lambda sock, server_hostname: sock)
        with c._create_socket(5) as sock:
            idle = getattr(socket, "TCP_KEEPIDLE", None) or socket.TCP_KEEPALIVE
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
            assert sock.getsockopt(socket.IPPROTO_TCP, idle) == smtp.KEEPALIVE["idle"]


def test_a_task_with_no_session_to_hear_it_keeps_its_report_in_its_record(dsn, tmp_path):
    """A message in a chat with no project started the task, so no session
    hears its report: it is written on the task itself, quiet, and no row
    is left for a bridge to send to the chat it came from."""

    async def go(chat):
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="Hi Valor"))
            got = {"channel": "telegram", "chat_id": chat, "message_id": "9", "received_id": task}
            await ledger.append(conn, "telegram", "message.received", got)
            await ledger.append(
                conn, "telegram", "message.bound", {"as": "start", "task_id": task, "received_id": task}
            )
            await notices.request(conn, task, kind="question", about_key="q", text="What next?")
        mine = await rows(dsn, task)
        return [(r["task_id"], r["payload"]["quiet"]) for r in mine if r["type"] == "child.reported"] == [
            (task, True)
        ] and not [r for r in mine if r["type"] == "notice.requested"]

    with bridges.configure(
        operator_telegram_id=OPERATOR,
        operator_channel="slack",
        operator_chat="T/U",
        projects_dir=str(tmp_path),
    ):
        assert run(go(OPERATOR)) and run(go("-100"))