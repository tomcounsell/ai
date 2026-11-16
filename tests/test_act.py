"""A chat session acts in its turn and reads what happened before it writes
the reply (docs/plans/act-then-report.md; Tom, 2026-11-11: "the session has
to see the outcome before it writes the reply"). `valor-record act` asks the
gateway's `act` route, which requests `task.<verb>` from the broker now."""

import asyncio
import contextlib
import io
import json
import pathlib
import re
import urllib.error
import urllib.request

import pytest

from core import __main__ as kernel_main
from core import broker, db, ledger, serve, tasks
from core.gateway import Gateway
from tests.ports import listen
from tests.test_board import channel, session_task
from tests.test_kernel import dm  # noqa: F401  Tom's DM and the operator, as a fixture
from tests.test_projects import names, spec
from tools import chat_tasks, valor_record

pytestmark = pytest.mark.spend(usd=0)
REPO = pathlib.Path(__file__).resolve().parent.parent


def factory(dsn):
    return lambda b: broker.Performers(*chat_tasks.performers(b, dsn))


async def rows_of(dsn, task_id):
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task_id)


async def children(dsn, session) -> list[str]:
    async with await db.connect(dsn) as conn:
        found = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'task.started' AND payload->>'parent_id' = %s "
                "ORDER BY id",
                (session,),
            )
        ).fetchall()
    return [r[0] for r in found]


async def acting(dsn, session, *argvs, turn="turn-1"):
    """Each argv of `valor-record` run by a turn of `session` through a live
    gateway: its exit code, what it printed, and the session's children and
    rows once it returned, before the turn ends."""
    import os

    gateway = Gateway(dsn, upstream="http://127.0.0.1:9", performers=factory(dsn))
    await gateway.start(port=listen())
    base = gateway.issue(session, turn) + "/record"
    os.environ["VALOR_RECORD_URL"] = base  # restored by the `room` fixture's monkeypatch
    out = []
    try:
        for argv in argvs:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = await asyncio.to_thread(valor_record.main, argv)
            out.append((rc, buf.getvalue(), await children(dsn, session)))
    finally:
        await gateway.close()
    return out, base


@pytest.fixture
def room(dsn, dm, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VALOR_RECORD_URL", "")
    chat = channel()
    site, _ = names()
    spec(site, chats=[chat])
    return asyncio.run(session_task(dsn, chat))


START = [
    "act",
    "start",
    "--instruction",
    "Fix the tiling hotkey",
    "--by",
    "tom",
    "--context",
    "from the dev chat",
]


def test_an_act_start_prints_the_childs_id_and_the_child_exists_before_the_turn_ends(dsn, room):
    ((rc, said, kids),), _ = asyncio.run(acting(dsn, room, START))
    assert rc == 0 and kids and said.strip() == f"done: task_id {kids[0]}"
    started = asyncio.run(rows_of(dsn, kids[0]))
    assert started[0]["type"] == "task.started"


def test_the_same_act_twice_in_a_turn_starts_one_task(dsn, room):
    out, _ = asyncio.run(acting(dsn, room, START, START))
    (rc1, said1, _), (rc2, said2, kids) = out
    assert rc1 == rc2 == 0 and said1 == said2 and len(kids) == 1


def test_the_2026_11_11_case_replayed_a_start_into_another_projects_chat_says_it_failed(dsn, room):
    """Session bcfdda8e1251, 16:04 +07: `task.start` with `in` a chat outside
    the project. The turn now reads the failure before it writes its reply."""
    outside = f"slack:{channel()}"
    ((rc, said, kids),), _ = asyncio.run(acting(dsn, room, [*START, "--in", outside]))
    assert rc == 1 and kids == []
    assert (
        said.startswith("valor-record: failed: ") and f"{outside} is not one of this project's chats" in said
    )


def test_a_steer_of_a_task_that_does_not_report_here_is_refused_and_says_why(dsn, room):
    async def other():
        async with await db.connect(dsn) as conn:
            return await tasks.start(conn, tasks.Brief(instruction="elsewhere"))

    stranger = asyncio.run(other())
    ((rc, said, _),), _ = asyncio.run(
        acting(dsn, room, ["act", "steer", stranger, "--text", "x", "--by", "tom"])
    )
    assert rc == 1 and f"refused: task {stranger} does not report to this chat" in said


def test_the_route_does_only_task_actions_a_merge_or_a_send_is_not_performed(dsn, room):
    async def go():
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9", performers=factory(dsn))
        await gateway.start(port=listen())
        base = gateway.issue(room, "turn-1") + "/record"
        got = []
        for verb in ("merge", "slack.send_message", "task.start"):
            body = {"verb": verb, "target": "main", "payload": {"text": "hi"}, "key": "k"}
            req = urllib.request.Request(f"{base}/act", data=json.dumps(body).encode())
            try:
                await asyncio.to_thread(urllib.request.urlopen, req)
            except urllib.error.HTTPError as e:
                got.append(e.code)
        await gateway.close()
        return got

    assert asyncio.run(go()) == [404, 404, 404]
    assert not [r for r in asyncio.run(rows_of(dsn, room)) if r["type"].startswith("effect.")]


def test_an_act_whose_outcome_is_not_known_says_so_and_names_where_to_read(dsn, room, monkeypatch):
    def stuck(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(valor_record.urllib.request, "urlopen", stuck)
    monkeypatch.setenv("VALOR_RECORD_URL", "http://127.0.0.1:9/t/x/record")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert valor_record.main(START) == 1
    assert "not known yet" in err.getvalue() and "valor-record task <your Brief's Task id>" in err.getvalue()


def test_an_in_flight_act_says_not_known_with_its_effect(monkeypatch, capsys):
    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    said = {"kind": "refused", "error": "in flight", "result": {}, "effect_id": "e1"}
    monkeypatch.setattr(
        valor_record.urllib.request, "urlopen", lambda *a, **k: Answer(json.dumps(said).encode())
    )
    monkeypatch.setenv("VALOR_RECORD_URL", "http://127.0.0.1:9/t/x/record")
    assert valor_record.main(START) == 1
    assert "not known yet (effect e1)" in capsys.readouterr().err


class StuckStart(chat_tasks.TaskStart):
    """Starts the child, then never returns: a kernel killed mid-act."""

    async def perform(self, action, key):
        out = await super().perform(action, key)
        self.started.set()
        await asyncio.Event().wait()
        return out


def test_a_killed_act_or_collect_leaves_a_start_that_recover_settles_live_or_not(fresh, dm, monkeypatch):  # noqa: F811
    chat = channel()
    site, _ = names()
    spec(site, chats=[chat])
    action = broker.Action("task.start", "work", {"instruction": "Fix it", "by": "tom"})

    async def killed(session, request_id) -> str:
        async with await db.connect(fresh) as conn:
            b = await tasks.brief(conn, session)
        p = StuckStart(b, fresh)
        p.started = asyncio.Event()
        conn = await db.connect(fresh)
        requesting = asyncio.create_task(
            broker.request(conn, broker.Performers(p), session, action, request_id=request_id)
        )
        await p.started.wait()
        requesting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requesting
        await conn.close()
        (intent,) = [r for r in await rows_of(fresh, session) if r["type"] == "effect.intent"
                     and r["payload"]["request_id"] == request_id]  # fmt: skip
        return intent["payload"]["effect_id"]

    async def go():
        session = await session_task(fresh, chat)
        in_turn = await killed(session, "turn-1/act/k")
        at_collect = await killed(session, "turn-1/start.json")
        async with await db.connect(fresh) as conn:
            live = await serve.recover(conn, factory(fresh), live=set())
            after = await serve.recover(conn, factory(fresh), live=set())
        return session, {in_turn, at_collect}, live, after

    session, dangling, live, after = asyncio.run(go())
    assert set(live["reconciled"]) == dangling and after["reconciled"] == []
    outcomes = [r["payload"] for r in asyncio.run(rows_of(fresh, session)) if r["type"] == "effect.outcome"]
    assert sorted(o["kind"] for o in outcomes) == ["done", "done"]


def test_a_chat_session_is_offered_no_task_effect_file_and_its_prompts_name_the_command(dsn, room):
    async def brief():
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, room)

    offered = kernel_main._performers(asyncio.run(brief())).offered()
    assert offered and not [u for u in offered if "task." in u]
    effect = re.compile(r"`task\.(start|answer|steer|stop)`")
    from core import notices

    prompts = ("skills/sdlc/converse.md", "skills/sdlc/roles/product.md", "skills/sdlc/roles/teammate.md",
               "skills/valor/skills/product-sheet/SKILL.md", "core/call.py")  # fmt: skip
    texts = {p: (REPO / p).read_text() for p in prompts}
    texts["notices.STALE"] = notices.STALE
    for name, text in texts.items():
        assert "valor-record act" in text and not effect.search(
            text if name != "core/call.py" else text[text.index("Tom settled these") :]
        ), name


def test_both_the_task_read_and_the_act_command_parse(monkeypatch, capsys):
    monkeypatch.delenv("VALOR_RECORD_URL", raising=False)
    for argv in (["task", "abc"], START, ["act", "stop", "t1", "--why", "done", "--by", "tom"]):
        assert valor_record.main(argv) == 2  # parsed; no kernel in this turn
    assert capsys.readouterr().err.count("VALOR_RECORD_URL is not set") == 3
