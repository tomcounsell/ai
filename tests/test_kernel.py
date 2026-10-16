"""The ledger, effects, stop, and the binding of a top-level Slack DM
message on real Postgres, with no model call."""

import asyncio
import sys
import uuid

import aiohttp
import psycopg
import pytest

from core import broker, db, intake, ledger, machine, notices, runs, session, tasks
from core.gateway import Gateway
from core.machine import State
from tests import bridges, scripted
from tests.performers import OutboxAppend, WorkspaceWrite
from tests.ports import listen
from tests.test_machine import Ledger

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def new_task(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", **kw))


# -- the ledger cannot be edited ------------------------------------------------


def test_kernel_role_cannot_edit_the_ledger(dsn):
    task = run(new_task(dsn))
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in (
            "UPDATE events SET type = 'x' WHERE task_id = %s",
            "DELETE FROM events WHERE task_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (task,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("TRUNCATE events")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE documents SET body = '{}' WHERE id = %s", (task,))


# -- spending is metered; only a stop refuses a call ------------------------------


def test_a_stopped_tasks_call_is_refused_with_a_ledger_row_before_any_provider_call(dsn):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        await gateway.start(port=listen())
        base = gateway.issue(task, "turn-1")
        body = {
            "model": "claude-haiku-4-5",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": "hi"}],
        }
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            refused = (r.status, await r.json())
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return refused, await ledger.read(conn, task), await tasks.status(conn, task)

    (status, error), rows, state = run(go())
    assert status == 400 and "stopped" in error["error"]["message"]
    (row,) = [r for r in rows if r["type"].startswith("gateway.")]
    assert row["type"] == "gateway.refused" and row["payload"]["reason"] == "stopped"
    assert row["payload"]["turn_id"] == "turn-1" and row["payload"]["model"] == "claude-haiku-4-5"
    assert state["spent_usd_micros"] == 0 and not state["open_calls"]


# -- effects: one request, one effect -------------------------------------------


def test_act_is_performed_at_request_and_one_request_id_is_one_effect(dsn, tmp_path):
    perf = broker.Performers(WorkspaceWrite(tmp_path), OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            wrote = await broker.request(
                conn, perf, task, broker.Action("workspace_write", "a.txt", {"text": "a"})
            )
            held = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            twin = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            once = await broker.request(
                conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}), request_id="t1/a.json"
            )
            again = await broker.request(
                conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}), request_id="t1/a.json"
            )
            state = await tasks.status(conn, task)
        return wrote, held, twin, once, again, state

    wrote, held, twin, once, again, state = run(go())
    assert wrote.kind == "done" and (tmp_path / "a.txt").read_text() == "a"
    # Two identical requests are two effects; one request_id is one.
    assert held.kind == "done" and twin.kind == "done" and twin.effect_id != held.effect_id
    assert again.effect_id == once.effect_id and again.kind == "done"
    assert _lines(tmp_path / "outbox.jsonl") == 3
    assert tasks.audit(state) == []


# -- stop is immediate and lossless ---------------------------------------------


def test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger(dsn, tmp_path):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        # A real subprocess that would run for a minute, with a child of its own.
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[
                sys.executable,
                "-c",
                "import subprocess,time; subprocess.Popen(['sleep','60']); time.sleep(60)",
            ],
            env={},
            cwd=str(tmp_path),
            harness="sleeper",
        )
        turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn))
        await asyncio.sleep(1.0)
        started = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            assert await tasks.stop(conn, task, reason="test") is True
            assert await tasks.stop(conn, task, reason="twice") is False
        ended = await turn
        elapsed = asyncio.get_running_loop().time() - started
        await gateway.close()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            with pytest.raises(tasks.TaskStopped):
                await runs.run_turn(gateway, task, build, dsn=dsn)
        return ended, elapsed, state

    ended, elapsed, state = run(go())
    assert ended["outcome"] == "stopped" and ended["returncode"] == -9
    assert elapsed < 20  # far short of the turn's minute, with room for a loaded Mac (3.8 s seen)
    assert state["state"] == "stopped" and tasks.audit(state) == []


def _lines(path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


# -- reconcile from the intent row ----------------------------------------------------------


class StuckWrite(WorkspaceWrite):
    """Writes the file, then never returns: the process dies mid-perform."""

    def __init__(self, root, wrote: asyncio.Event):
        super().__init__(root)
        self.wrote = wrote

    async def perform(self, action, key):
        out = await super().perform(action, key)
        self.wrote.set()
        await asyncio.Event().wait()
        return out


def test_the_intent_row_carries_the_action_and_reconcile_rebuilds_it_from_that_alone(dsn, tmp_path):
    """A propose-class effect has no `effect.held` row; a perform cut off
    after its intent leaves only the intent, and reconcile settles it."""
    action = broker.Action("workspace_write", "note.txt", {"text": "hello"})

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t"))
        wrote = asyncio.Event()
        stuck = broker.Performers(StuckWrite(tmp_path, wrote))
        conn = await db.connect(dsn)
        requesting = asyncio.create_task(broker.request(conn, stuck, task, action))
        await wrote.wait()
        requesting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requesting
        await conn.close()
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
            (intent,) = [r["payload"] for r in rows if r["type"] == "effect.intent"]
            settled = await broker.reconcile(
                conn, broker.Performers(WorkspaceWrite(tmp_path)), intent["effect_id"]
            )
            return rows, intent, settled, await scripted.task_of(conn, intent["effect_id"]), task

    rows, intent, settled, owner, task = run(go())
    assert not [r for r in rows if r["type"] in ("effect.held", "effect.outcome")]
    assert intent["action_type"] == "workspace_write" and intent["target"] == "note.txt"
    assert intent["payload"] == {"text": "hello"} and intent["effect_class"] == "propose"
    assert intent["payload_sha256"] == ledger.digest({"text": "hello"}) and "approval_id" not in intent
    assert settled.kind == "done" and owner == task


# -- turns run at once up to the machine's limit ---------------------------------


def test_max_turns_holders_run_at_once_and_the_next_waits_for_one(dsn, monkeypatch):
    import dataclasses
    import uuid

    from core import slot

    monkeypatch.setattr(
        slot, "settings", dataclasses.replace(slot.settings, max_turns=2, machine=uuid.uuid4().hex)
    )

    async def go():
        inside: list[str] = []
        release = asyncio.Event()

        async def hold(name: str) -> None:
            async with slot.held(name, dsn):
                inside.append(name)
                await release.wait()

        first = [asyncio.create_task(hold(n)) for n in ("a", "b")]
        while len(inside) < 2:
            await asyncio.sleep(0.05)
        third = asyncio.create_task(hold("c"))
        await asyncio.sleep(3 * slot.POLL_S)
        assert sorted(inside) == ["a", "b"]
        release.set()
        await asyncio.wait_for(asyncio.gather(*first, third), timeout=10)
        assert sorted(inside) == ["a", "b", "c"]

    run(asyncio.wait_for(go(), timeout=15))


# -- the turn's environment ------------------------------------------------------


def test_a_turn_keeps_the_operators_path_and_knows_its_github_repo(tmp_path, monkeypatch):
    from core import workspace

    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    env = workspace.environment({"PATH": "/task/bin:/usr/bin", "REDIS_PORT": "6400"})
    assert env["PATH"] == "/task/bin:/usr/bin:/opt/homebrew/bin" and env["REDIS_PORT"] == "6400"
    spec = workspace.Spec(
        name="p", repo="https://github.com/tomcounsell/popoto.git", kind="python-uv", suite="true"
    )
    lay = workspace.Layout(tmp_path)
    found = workspace.harness_env(lay, spec, {}, {}, bin_dir=tmp_path / "bin", passfile=tmp_path / "pass")
    assert found["GH_REPO"] == "tomcounsell/popoto"


# -- a top-level Slack DM message keeps the posts it follows --------------------


def at(n: int) -> str:
    return f"1780000000.{n:06d}"


@pytest.fixture
def dm(tmp_path):
    """Tom's own Slack DM, fresh per test so no other test's posts precede."""
    tom = f"T0ABC/U{uuid.uuid4().hex[:8].upper()}"
    with bridges.operator(tmp_path, operator_slack_ids=(tom,)):
        yield tom


async def tom_says(dsn, dm, text, ts, chat=None) -> dict:
    """Tom's top-level Slack message, received and bound: its binding, with
    the started task's instruction when it started one."""
    inbound = intake.Inbound(
        channel="slack", chat_id=chat or dm, chat_kind="group" if chat else "dm", message_id=ts,
        sender_id=dm, sender_name="Tom", sent_at="2026-10-15T00:00:00Z", text=text,
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    if bound["as"] == "start":
        async with await db.connect(dsn) as conn:
            bound["instruction"] = (await tasks.brief(conn, bound["task_id"])).instruction
    bound["notices"] = [
        r
        for r in await bridges.of_type(dsn, "notice.requested")
        if r["about_key"] == f"reply:{got.received_id}"
    ]
    return bound


async def posted(dsn, task, chat, ts, text, *, parts=1, thread_ts=None, question=False) -> None:
    """Valor's post in a Slack chat: a send in `parts`, or the notice of the
    task's open question."""
    entries = [
        {"channel": "slack", "chat_id": chat, "message_id": f"{ts}{i}" if i else ts, "thread_ts": thread_ts}
        for i in range(parts)
    ]
    async with await db.connect(dsn) as conn:
        if question:
            about = f"question:{machine.fold(await ledger.read(conn, task)).open_question}"
            nid = await notices.request(conn, task, kind="question", about_key=about, text=text)
            await ledger.append(conn, task, "notice.sent", {"notice_id": nid, "sent": entries})
            return
        effect, payload = ledger.new_id(), {"text": text}
        await ledger.append(conn, task, "effect.intent",
                            {"effect_id": effect, "action_type": "slack.send_message", "target": chat,
                             "payload": payload})  # fmt: skip
        await ledger.append(conn, task, "effect.outcome",
                            {"effect_id": effect, "idempotency_key": "k", "kind": "done",
                             "result": {"sent": entries}, "error": None})  # fmt: skip


async def asking(dsn) -> str:
    """A task waiting on its open question."""
    task = await new_task(dsn, max_effect_class="act")
    async with await db.connect(dsn) as conn:
        for row in Ledger().ask().rows[1:]:
            p = {k: f"{task}-{v}" if k == "turn_id" else v for k, v in row["payload"].items()}
            await ledger.append(conn, task, row["type"], p)
        assert machine.fold(await ledger.read(conn, task)).state is State.WAITING
    return task


async def state(dsn, task) -> State:
    return machine.fold(await bridges.rows(dsn, task)).state


def test_a_top_level_dm_message_after_valors_posts_starts_a_task_that_carries_them(dsn, dm):
    """The incident: a send from one task, a notice from another, then
    Tom's top-level answer. Nothing waits on him, so it starts a task that
    sees both posts, oldest first."""

    async def go():
        a, b = await new_task(dsn), await new_task(dsn)
        await posted(dsn, a, dm, at(10), "Could you DM me 'Ask me one question'?")
        await posted(dsn, b, dm, at(20), "Sorry for the odd stop notice.")
        return a, b, await tom_says(dsn, dm, "sounds good! don't stop for me", at(30))

    a, b, bound = run(go())
    text = bound["instruction"]
    assert bound["as"] == "start" and bound["task_id"] not in (a, b)
    assert text.startswith(
        "sounds good! don't stop for me\n\nTom wrote this in his Slack DM, not in a thread"
    )
    assert text.index(f"from task {a}") < text.index("> Could you DM me") < text.index(f"from task {b}")
    assert "> Sorry for the odd stop notice." in text and "waiting" not in text


def test_a_top_level_dm_message_answers_the_one_open_question_it_follows(dsn, dm):
    """A live question, then an unrelated notice: the message is the
    answer, with a notice in reply in Tom's DM. A later `stop` after
    another live question answers it too and stops nothing."""

    async def go():
        waiting, other = await asking(dsn), await new_task(dsn)
        await posted(dsn, waiting, dm, at(10), "Which colour?", question=True)
        await posted(dsn, other, dm, at(20), "Something else.")
        answer = await tom_says(dsn, dm, "blue", at(30))
        asked = await asking(dsn)
        await posted(dsn, asked, dm, at(40), "Should I go on?", question=True)
        stop = await tom_says(dsn, dm, "stop", at(50))
        return waiting, answer, await bridges.rows(dsn, waiting), asked, stop, await state(dsn, asked)

    waiting, answer, rows_, asked, stop, asked_state = run(go())
    assert (answer["task_id"], answer["as"]) == (waiting, "answer")
    assert [r["payload"]["text"] for r in rows_ if r["type"] == "question.answered"] == ["blue"]
    (n,) = answer["notices"]
    assert (n["channel"], n["chat_id"], n["reply_to"]) == ("slack", dm, at(30))
    assert "as your answer" in n["text"] and "send it again" in n["text"]
    assert (stop["task_id"], stop["as"]) == (asked, "answer") and asked_state is not State.STOPPED


def test_two_stale_or_superseded_or_textless_asks_start_a_task(dsn, dm):
    """Two open questions; an answered one; a question its task's own later
    send superseded; files with no text after a live one: each starts a
    task, and no question is answered."""

    async def go():
        out = []
        a, b = await asking(dsn), await asking(dsn)
        await posted(dsn, a, dm, at(10), "Which colour?", question=True)
        await posted(dsn, b, dm, at(20), "Which size?", question=True)
        out.append(await tom_says(dsn, dm, "blue", at(30)))
        stale = await asking(dsn)
        await posted(dsn, stale, dm, at(40), "Which colour?", question=True)
        async with await db.connect(dsn) as conn:
            await session.answer(conn, stale, "answered elsewhere")
        out.append(await tom_says(dsn, dm, "blue", at(50)))
        later = await asking(dsn)
        await posted(dsn, later, dm, at(60), "Which colour?", question=True)
        await posted(dsn, later, dm, at(70), "Never mind, I picked blue.")
        out.append(await tom_says(dsn, dm, "ok", at(80)))
        files = await asking(dsn)
        await posted(dsn, files, dm, at(90), "Send the logo?", question=True)
        inbound = intake.Inbound(
            channel="slack", chat_id=dm, chat_kind="dm", message_id=at(100), sender_id=dm, sender_name="Tom",
            sent_at="2026-10-15T00:00:00Z", attachments=[{"name": "logo.png", "mime": "image/png", "bytes": 1,
                                                          "path": "/tmp/logo.png"}],
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, inbound)
            await intake.bind(conn)
        (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
        out.append(bound)
        return out, [await state(dsn, t) for t in (a, b, later, files)]

    (two, stale, later, files), states = run(go())
    assert all(b["as"] == "start" for b in (two, stale, later, files))
    assert two["instruction"].count("still waiting on his answer") == 2
    assert "waiting" not in stale["instruction"] and "waiting" not in later["instruction"]
    assert states == [State.WAITING] * 4


def test_the_preceding_posts_are_those_since_toms_last_top_level_message_newest_five(dsn, dm):
    async def go():
        task = await new_task(dsn)
        await posted(dsn, task, dm, at(10), "first post")
        first = await tom_says(dsn, dm, "one", at(20))
        await posted(dsn, task, dm, at(30), "second post")
        second = await tom_says(dsn, dm, "two", at(40))
        third = await tom_says(dsn, dm, "three", at(50))
        for n in range(1, 8):
            await posted(dsn, task, dm, at(100 + n), f"post {n}", parts=2 if n == 7 else 1)
        capped = await tom_says(dsn, dm, "ok", at(200))
        return first, second, third, capped

    first, second, third, capped = run(go())
    assert "> first post" in first["instruction"]
    assert "> second post" in second["instruction"] and "first post" not in second["instruction"]
    assert third["instruction"] == "three"
    assert [n for n in range(1, 8) if f"> post {n}" in capped["instruction"]] == [3, 4, 5, 6, 7]
    assert capped["instruction"].count("> post 7") == 1


def test_threaded_posts_other_chats_later_posts_and_group_channels_are_not_carried(dsn, dm):
    """And a top-level `stop` after a running task's send starts a task,
    stopping nothing."""

    async def go():
        task = await new_task(dsn)
        group = f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"
        await posted(dsn, task, dm, at(10), "in a thread", thread_ts=at(5))
        await posted(dsn, task, f"T0ABC/U{uuid.uuid4().hex[:8].upper()}", at(11), "another DM")
        await posted(dsn, task, dm, at(40), "after the message")
        in_dm = await tom_says(dsn, dm, "hello", at(30))
        await posted(dsn, task, group, at(50), "in the channel")
        in_group = await tom_says(dsn, dm, "hello", at(60), chat=group)
        await posted(dsn, task, dm, at(70), "working on it")
        stop = await tom_says(dsn, dm, "stop", at(80))
        return task, in_dm, in_group, stop, await state(dsn, task)

    task, in_dm, in_group, stop, task_state = run(go())
    assert (in_dm["as"], in_dm["instruction"]) == ("start", "hello")
    assert (in_group["as"], in_group["instruction"]) == ("start", "hello")
    assert stop["as"] == "start" and stop["task_id"] != task and task_state is not State.STOPPED


# -- core shape and the queue (docs/plans/ask-reconciliation.md) --------------------


def test_queue_order_is_start_order_moved_by_each_placement_in_turn():
    def at(where, task, anchor):
        return {"task": task, "anchor": anchor, "where": where}

    def order(*moves):
        q = tasks.queue("abcd", moves)
        return "".join(sorted(q, key=q.get))

    assert order() == "abcd"
    assert order(at("after", "a", "c")) == "bcad"  # moved later
    assert order(at("before", "d", "b"), at("before", "c", "d")) == "acdb"  # a chain, in order
    assert order(at("before", "a", "b"), at("before", "b", "a")) == "bacd"  # a cycle: the latest wins
    assert order(at("before", "d", "gone"), at("after", "a", "a")) == "abcd"  # not open, or itself


def test_core_shape_labels_the_decision_steers_an_amend_and_places_the_task(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            x, y, z, bare = [await new_task(dsn) for _ in range(4)]
            await ledger.append(conn, x, "reconcile.decided", {"decision": "overlaps", "named": y})
            await ledger.append(
                conn, z, "reconcile.decided", {"decision": "overlaps", "named": x, "placement": "before"}
            )
            await ledger.append(
                conn, y, "reconcile.decided", {"decision": "overlaps", "named": x, "placement": "before"}
            )
            refused = []
            for t, kw in ((bare, {"confirm": True}), (x, {"before": x}), (x, {"after": "nope"}),
                          (x, {"confirm": True, "amend": "y"}), (x, {})):  # fmt: skip
                try:
                    await session.shape(conn, t, by="valor", **kw)
                except (LookupError, ValueError) as exc:
                    refused.append(str(exc))
            got = [
                await session.shape(conn, x, confirm=True, by="valor"),
                await session.shape(conn, y, amend="only the tests half", after=z, by="valor"),
            ]
            ids = [x, y, z, bare]
            queued = tasks.queue(ids, await tasks.moves(conn, ids))
            return got, refused, queued, ids, await ledger.read(conn, x), await ledger.read(conn, y)

    got, refused, queued, (x, y, z, bare), xs, ys = run(go())
    assert got == [["shape.confirmed"], ["shape.confirmed", "message.steered", "task.placed"]]
    assert len(refused) == 5 and "no reconciler decision" in refused[0]
    # z's proposal applies (z before x); y's writer placed it, so y's own proposal does not.
    assert sorted(queued, key=queued.get) == [z, y, x, bare]
    label = next(r["payload"] for r in xs if r["type"] == "shape.confirmed")
    assert (label["confirmed"], label["decision"], label["provenance"]["by"]) == (True, "overlaps", "valor")
    steer = next(r["payload"] for r in ys if r["type"] == "message.steered")
    assert session._steering([steer]) == "Written to you while you worked:\n- valor: only the tests half"
