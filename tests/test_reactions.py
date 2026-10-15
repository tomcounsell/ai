"""Reactions (docs/plans/3624532d-reactions.md): 👀 at binding, the shape
judgement's reaction replacing it, the rows staying out of a task's
scheduling, the outbox, and each bridge placing them on its emulator.

Judgements go to the real local upstream (`tests/judgement_upstream.py`);
the Telegram bridge runs in this process over `EmulatorWire`, the Slack
bridge in a process of its own (`tests/slack_child.py`).

Live spend: none.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core import db, intake, ledger, machine, notices, reactions, router, serve, tasks, verdicts
from core.bridge import ReactionDue
from core.judgement_tasks import SHAPE
from core.machine import State
from tests import bridges, judgement_upstream, scripted
from tests.bridges import FakeBridge, new_task, of_type, rows
from tests.conftest import TEST_DB
from tests.telegram_emulator import STANDARD_REACTIONS, Emulator
from tests.telegram_port import (
    chat,
    connected,
    emu_chat,
    perform,
    release,
    send,
    until,
)
from tests.telegram_port import (
    machine as tg_machine,
)
from tests.test_intake import TOM_SLACK, a_notice, mark_sent, msg, owed, say, slack
from tests.test_pipeline import drive

pytestmark = pytest.mark.spend(usd=0)

UP = judgement_upstream.shared()
EYES = "\U0001f440"
OK_HAND = "\U0001f44c"
# Each shape's emoji as the exact code points sent: what Telegram's standard
# list holds, with no U+FE0F.
PINNED = {
    "short_answer": "\u26a1",
    "code_change": "\U0001f468\u200d\U0001f4bb",
    "written_report": "\u270d",
    "question_back": "\U0001f914",
    "acknowledge": "\U0001f44c",
    "decline": "\U0001f937",
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


async def reacts(dsn, received_id: str) -> list[dict]:
    return await of_type(dsn, "reaction.requested", received_id=received_id)


def shape_probs(label: str, p: float = 0.9) -> dict:
    rest = [x for x in SHAPE.questions[0].labels if x != label]
    return {"shape": {label: p, **{x: (1 - p) / len(rest) for x in rest}}}


async def pending_for(dsn, received_id: str) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return [s for s in await reactions.pending(conn) if s["received_id"] == received_id]


async def shape(dsn, port, received_id: str) -> str | None:
    (seen,) = await pending_for(dsn, received_id)
    async with await db.connect(dsn) as conn:
        return await reactions.shape(port, conn, seen, dsn)


# -- binding -------------------------------------------------------------------


def test_the_emoji_are_pinned_code_points_on_telegrams_standard_list():
    assert reactions.EMOJI == PINNED and set(reactions.EMOJI) == set(SHAPE.questions[0].labels)
    assert reactions.SEEN == EYES
    for emoji in [reactions.SEEN, *reactions.EMOJI.values()]:
        assert "\ufe0f" not in emoji and emoji in STANDARD_REACTIONS


def test_each_binding_to_a_task_owes_its_reaction_and_none_owes_nothing(dsn, op, monkeypatch):
    async def go():
        out = {}
        task = await new_task(dsn)
        target = await a_notice(dsn, task)
        out["start"] = await say(dsn, msg("write a haiku"))
        out["steer"] = await say(dsn, msg("make it shorter", reply_to=target))
        out["stranger"] = await say(dsn, msg("hi", sender="99", reply_to=target))
        # A reply to an ended task: feedback.
        ended = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            assert await verdicts.end(conn, ended, "merge", "the remote rejected the push")
            await notices.owe(conn, ended)
        (n,) = [r for r in await of_type(dsn, "notice.requested", kind="ended") if r["task_id"] == ended]
        out["feedback"] = await say(
            dsn, msg("try the other branch", reply_to=await mark_sent(dsn, ended, n["about_key"]))
        )
        # Stop: no judgement, 👌 at once.
        out["stop"] = await say(dsn, msg("stop", reply_to=target))
        # Channels that do not react: a verified email start, and the local chat.
        monkeypatch.setitem(intake.VERIFY, "email", lambda inbound: True)
        out["email"] = await say(
            dsn, msg("write a limerick", channel="email", sender=bridges.OPERATOR_EMAIL, chat="t9")
        )
        out["local"] = await say(dsn, msg("write a sonnet", channel="local", chat="local"))
        return out, {k: await reacts(dsn, b["received_id"]) for k, b in out.items()}

    out, got = run(go())  # fmt: skip
    for kind in ("start", "steer", "feedback"):
        assert out[kind]["as"] == kind
        (r,) = got[kind]
        assert r["task_id"] == out[kind]["task_id"] and r["stage"] == "seen" and r["emoji"] == EYES
        assert r["channel"] == "telegram" and r["chat_id"] == bridges.OPERATOR_CHAT
    assert out["stop"]["as"] == "stop"
    (stop,) = got["stop"]
    assert stop["stage"] == "shape" and stop["emoji"] == OK_HAND and stop["judged"] is False
    assert out["stranger"]["as"] == "none" and got["stranger"] == []
    assert out["email"]["as"] == "start" and got["email"] == []
    assert out["local"]["as"] == "start" and got["local"] == []
    # The stop is never judged: nothing of its is pending.
    assert run(pending_for(dsn, out["stop"]["received_id"])) == []


def test_an_answer_to_the_open_question_gets_eyes(dsn, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        waiting = await scripted.start(dsn, ws, judge="thin")
        await drive(dsn, waiting)
        await owed(dsn, waiting)
        f = machine.fold(await rows(dsn, waiting))
        target = await mark_sent(dsn, waiting, f"question:{f.open_question}")
        bound = await say(dsn, msg("a short one", reply_to=target))
        return waiting, bound, await reacts(dsn, bound["received_id"])

    waiting, bound, (r,) = run(go())
    assert bound["as"] == "answer" and r["task_id"] == waiting and r["emoji"] == EYES and r["stage"] == "seen"


def test_a_reaction_is_written_in_the_bindings_own_transaction_after_its_task_starts(dsn, op):
    async def go():
        bound = await say(dsn, msg("new work"))
        got = await rows(dsn, bound["task_id"])
        return [r["type"] for r in got]

    types = run(go())
    assert types[0] == "task.started" and "reaction.requested" in types


def test_slack_messages_react_too(dsn, op):
    async def go():
        with bridges.configure(operator_slack_ids=(TOM_SLACK,)):
            start = await say(dsn, slack("add a footer", "1760000100.000001"))
            await say(dsn, slack("x", "1760000100.000002", sender="T0ABC/U0ELSE"))
        return start, await reacts(dsn, start["received_id"])

    start, (r,) = run(go())
    assert start["as"] == "start" and r["emoji"] == EYES and r["channel"] == "slack"
    assert r["chat_id"] == TOM_SLACK and r["message_id"] == "1760000100.000001"


def test_two_binders_write_one_reaction(dsn, op, monkeypatch):
    real = intake._bind
    arrived: dict[str, int] = {}
    gate = asyncio.Event()

    async def racing(conn, p):
        arrived[p["received_id"]] = arrived.get(p["received_id"], 0) + 1
        if arrived[p["received_id"]] == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 30)
        return await real(conn, p)

    monkeypatch.setattr(intake, "_bind", racing)

    async def binder():
        async with await db.connect(dsn) as conn:
            return await intake.bind(conn)

    async def go():
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, msg("new work, twice"))
        await asyncio.gather(binder(), binder())
        return await reacts(dsn, got.received_id)

    assert len(run(go())) == 1


# -- the shape judgement -----------------------------------------------------------


async def bound_message(dsn, text="add a dark mode toggle") -> dict:
    return await say(dsn, msg(text))


@pytest.mark.parametrize("label", list(PINNED))
def test_each_shape_replaces_eyes_with_its_exact_emoji(dsn, op, label):
    async def go():
        b = await bound_message(dsn)
        sid = UP.script({"probs": shape_probs(label)})
        emoji = await shape(dsn, UP.port(script=sid), b["received_id"])
        return b, emoji, await reacts(dsn, b["received_id"]), UP.seen(sid)

    b, emoji, got, seen = run(go())
    assert emoji == PINNED[label]
    seen_row, shaped = got
    assert seen_row["stage"] == "seen" and shaped["stage"] == "shape" and shaped["judged"] is True
    assert shaped["emoji"] == PINNED[label] and shaped["chat_id"] == seen_row["chat_id"]
    assert shaped["message_id"] == seen_row["message_id"] and shaped["task_id"] == b["task_id"]
    # Judged on the message, as data, once.
    assert [r["leg"] for r in seen] == ["jev"] and seen[0]["body"]["state"][
        "message"
    ] == "add a dark mode toggle"
    assert seen[0]["body"]["state"]["bound_as"] == "start"
    assert run(pending_for(dsn, b["received_id"])) == []


def test_the_judgement_is_metered_on_the_bound_task_and_quiet_there(dsn, op):
    async def go():
        b = await bound_message(dsn)
        sid = UP.script({"probs": shape_probs("code_change")})
        await shape(dsn, UP.port(script=sid), b["received_id"])
        return b, await rows(dsn, b["task_id"])

    b, got = run(go())
    opened = [r for r in got if r["type"] == "gateway.opened" and r["payload"]["site"] == "response.shape"]
    assert len(opened) == 2  # both legs opened before the first call; the fallback charged 0, unused
    (answered,) = [r for r in got if r["type"] == "judgement.answered"]
    assert answered["payload"]["ref"] == {"received_id": b["received_id"]}
    assert all(ledger.quiet(r) for r in got if r["type"].startswith(("reaction.", "judgement.", "gateway.")))


def test_an_abstain_leaves_eyes_and_is_not_asked_again(dsn, op):
    half = {"probs": {"shape": {**shape_probs("acknowledge", 0.5)["shape"], "short_answer": 0.5}}}

    async def go():
        b = await bound_message(dsn, "thanks!")
        emoji = await shape(dsn, UP.port(script=UP.script(half, half)), b["received_id"])
        return b, emoji, await reacts(dsn, b["received_id"])

    b, emoji, got = run(go())
    assert emoji is None and [r["stage"] for r in got] == ["seen"]
    assert run(pending_for(dsn, b["received_id"])) == []


def test_a_failed_judgement_is_asked_again_up_to_the_sites_limit_then_left(dsn, op):
    down = {"status": 500}

    async def go():
        b = await bound_message(dsn)
        port = UP.port(script=UP.script(default=down))
        asked = 0
        while await pending_for(dsn, b["received_id"]):
            assert await shape(dsn, port, b["received_id"]) is None
            asked += 1
            assert asked <= 5
        return b, asked, await rows(dsn, b["task_id"])

    _, asked, got = run(go())
    from core import judgement

    assert asked == judgement.UNANSWERED_RUNS
    assert len([r for r in got if r["type"] == "judgement.failed"]) == judgement.UNANSWERED_RUNS
    assert [r["payload"]["stage"] for r in got if r["type"] == "reaction.requested"] == ["seen"]


def test_a_task_stopped_before_its_judgement_gets_ok_hand_unjudged(dsn, op):
    async def go():
        b = await bound_message(dsn)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, b["task_id"], reason="test")
        sid = UP.script({"probs": shape_probs("code_change")})
        emoji = await shape(dsn, UP.port(script=sid), b["received_id"])
        return b, emoji, await reacts(dsn, b["received_id"]), UP.seen(sid)

    b, emoji, got, seen = run(go())
    assert emoji == OK_HAND and seen == []  # no provider was asked
    assert got[-1]["stage"] == "shape" and got[-1]["judged"] is False and got[-1]["reason"] == "task stopped"
    assert run(pending_for(dsn, b["received_id"])) == []


def test_a_second_shape_for_one_message_writes_nothing(dsn, op):
    async def go():
        b = await bound_message(dsn)
        (seen,) = await pending_for(dsn, b["received_id"])
        port = UP.port(
            script=UP.script({"probs": shape_probs("short_answer")}, {"probs": shape_probs("decline")})
        )
        async with await db.connect(dsn) as conn:
            first = await reactions.shape(port, conn, seen, dsn)
            second = await reactions.shape(port, conn, seen, dsn)
        return first, second, await reacts(dsn, b["received_id"])

    first, second, got = run(go())
    assert first == PINNED["short_answer"] and second is None
    assert [r["emoji"] for r in got] == [EYES, PINNED["short_answer"]]


# -- quiet rows and the resident loop ------------------------------------------------


def _row(i, type_, at=None, **payload):
    return {"id": i, "type": type_, "payload": payload, "at": at or datetime.now(UTC)}


def test_quiet_rows_never_hide_a_failed_step_from_its_retry():
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    failed = [
        _row(1, "task.started"),
        _row(2, "step.failed", long_ago, state="judge", check=None, reason="x"),
    ]
    beside = [
        _row(3, "reaction.requested", stage="seen"),
        _row(4, "gateway.opened", site="response.shape"),
        _row(5, "judgement.answered", site="response.shape"),
        _row(6, "reaction.placed"),
        _row(7, "judgement.failed", site="response.shape"),
        _row(8, "reaction.failed"),
    ]
    assert router.retry_due(failed + beside) and tasks.failed_step(failed + beside)["row"] == 2
    # A judgement of another site is the task's own work: it hides the failure.
    other = failed + [_row(9, "judgement.answered", site="checks.test.breadth")]
    assert not router.retry_due(other) and tasks.failed_step(other) is None
    assert not ledger.quiet(_row(1, "task.started")) and not ledger.quiet(_row(1, "step.failed"))


@pytest.fixture
def fresh() -> str:
    return db.migrate(f"{TEST_DB}_serve", fresh=True)


def only(kernel: serve.Kernel, *tasks_: str) -> serve.Kernel:
    async def active(conn):
        return list(tasks_)

    kernel.active = active
    return kernel


async def tick(kernel: serve.Kernel, dsn: str) -> None:
    async with await db.connect(dsn) as conn:
        await kernel.tick(conn)


async def settled(kernel: serve.Kernel) -> None:
    while kernel.jobs or kernel.shaping:
        await asyncio.gather(*kernel.jobs.values(), *kernel.shaping.values(), return_exceptions=True)


async def seen_on(dsn, task) -> str:
    """A \U0001f440 for a message bound to `task`; its received id."""
    rid = ledger.new_id()
    p = {"received_id": rid, "channel": "telegram", "chat_id": "1000", "message_id": "7"}
    async with await db.connect(dsn) as conn:
        assert await reactions._request(conn, task, p, EYES, "seen")
    return rid


def test_a_shape_judgement_on_a_task_starts_no_step_and_a_failed_step_still_retries(fresh, op, monkeypatch):
    calls = []

    async def failing(ctx):
        calls.append(ctx.task_id)
        return {"status": "failed", "turn": {"result": "no verdict: x"}}

    async def go():
        task = await new_task(fresh)
        port = UP.port(script=UP.script(default={"probs": shape_probs("short_answer")}))
        kernel = only(serve.Kernel(None, {State.JUDGE: failing}, None, fresh, judgement_port=port), task)
        try:
            await tick(kernel, fresh)
            await settled(kernel)
            # The step failed; a shape judgement and its reactions land beside it.
            rid = await seen_on(fresh, task)
            for _ in range(3):
                kernel.parked_at -= 3600
                await tick(kernel, fresh)
                await settled(kernel)
            waited = len(calls)
            shaped = [r for r in await rows(fresh, task) if r["type"] == "reaction.requested"]
            due = tasks.failed_step(await rows(fresh, task))
            # Once its delay passes, the failed step is retried.
            monkeypatch.setattr(router, "STEP_BACKOFF_S", 0.0)
            kernel.parked_at -= 3600
            await tick(kernel, fresh)
            await settled(kernel)
        finally:
            await kernel.close()
        return rid, waited, shaped, due, len(calls)

    _, waited, shaped, due, retried = run(go())
    assert [r["payload"]["stage"] for r in shaped] == ["seen", "shape"]
    assert waited == 1 and due is not None and due["reason"]
    assert retried == 2


def test_a_tasks_step_runs_while_its_messages_shape_job_is_in_flight(fresh, op):
    stepped = asyncio.Event()

    async def judging(ctx):
        stepped.set()
        return {"status": "failed"}

    async def go():
        task = await new_task(fresh)
        slow = UP.port(script=UP.script(default={"delay": 30, "probs": shape_probs("short_answer")}))
        kernel = only(serve.Kernel(None, {State.JUDGE: judging}, None, fresh, judgement_port=slow), task)
        rid = await seen_on(fresh, task)
        try:
            await tick(kernel, fresh)
            await asyncio.wait_for(stepped.wait(), 20)
            in_flight = rid in kernel.shaping and not kernel.shaping[rid].done()
            job = kernel.shaping[rid]
            # A second wake starts no second shape job for the message.
            await tick(kernel, fresh)
            same = kernel.shaping.get(rid) is job
        finally:
            await kernel.close()
        return in_flight, same, job.cancelled()

    in_flight, same, cancelled = run(go())
    assert in_flight and same
    assert cancelled  # close() cancels it


def test_a_shape_job_that_raises_is_asked_again_a_bounded_number_of_times(fresh, op):
    """A judgement that raises writes no `judgement.failed` row, so `pending`
    keeps listing its message; the kernel asks again only up to the site's
    limit, then leaves 👀."""

    async def go():
        ghost = "no-such-task-" + ledger.new_id()  # `spending.open_call` raises KeyError for it
        port = UP.port(script=UP.script(default={"probs": shape_probs("short_answer")}))
        kernel = serve.Kernel(None, {}, None, fresh, judgement_port=port)
        rid = await seen_on(fresh, ghost)
        try:
            for _ in range(6):
                async with await db.connect(fresh) as conn:
                    await kernel.shape(conn)
                await settled(kernel)
        finally:
            await kernel.close()
        async with await db.connect(fresh) as conn:
            still = [s for s in await reactions.pending(conn) if s["received_id"] == rid]
        return rid, kernel.shape_errors.get(rid), still

    from core import judgement

    _, errors, still = run(go())
    assert errors == judgement.UNANSWERED_RUNS
    assert len(still) == 1  # still pending in the ledger; this process stopped asking


# -- the outbox ---------------------------------------------------------------------


async def reaction_rows(
    dsn, task, *stages, channel="telegram", chat_id="1000", message_id="7"
) -> dict[str, str]:
    """A message's reactions at `stages`; each stage's reaction id."""
    p = {"received_id": ledger.new_id(), "channel": channel, "chat_id": chat_id, "message_id": message_id}
    out = {}
    async with await db.connect(dsn) as conn:
        for stage in stages:
            out[stage] = await reactions._request(conn, task, p, EYES if stage == "seen" else OK_HAND, stage)
    return out


def mine(items, ids) -> list:
    return [i for i in items if isinstance(i, ReactionDue) and i.reaction_id in set(ids.values())]


def test_the_outbox_yields_a_reaction_until_its_result_on_its_own_channel_only(dsn, op):
    async def go():
        task = await new_task(dsn)
        ids = await reaction_rows(dsn, task, "seen")
        async with (
            bridges.outbox(dsn, FakeBridge("telegram")) as box,
            bridges.outbox(dsn, FakeBridge("slack")) as other,
        ):
            first = mine(await box.due(), ids)
            elsewhere = mine(await other.due(), ids)
            await box.reacted(first[0])
            await box.reacted(first[0], "late")  # a second result is skipped
            after = mine(await box.due(), ids)
        return ids, first, elsewhere, after

    ids, first, elsewhere, after = run(go())  # fmt: skip
    assert [i.reaction_id for i in first] == [ids["seen"]] and first[0].emoji == EYES
    assert elsewhere == [] and after == []
    assert len(run(of_type(dsn, "reaction.placed", reaction_id=ids["seen"]))) == 1
    assert run(of_type(dsn, "reaction.failed", reaction_id=ids["seen"])) == []


def test_a_shape_supersedes_its_unplaced_eyes_once_however_many_outboxes_ask(dsn, op):
    async def go():
        task = await new_task(dsn)
        ids = await reaction_rows(dsn, task, "seen", "shape")
        bridge = FakeBridge("telegram")
        async with bridges.outbox(dsn, bridge) as a, bridges.outbox(dsn, bridge) as b:
            got = await asyncio.gather(a.due(), b.due())
        return ids, [mine(g, ids) for g in got]

    ids, got = run(go())
    for items in got:
        assert [i.stage for i in items] == ["shape"]
    (closed,) = run(of_type(dsn, "reaction.placed", reaction_id=ids["seen"]))
    assert closed["superseded"] is True


def test_the_outbox_wakes_on_a_reaction(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with bridges.outbox(dsn, FakeBridge("telegram")) as box:
            waiting = asyncio.create_task(box.wait())
            await asyncio.sleep(0.05)
            started = asyncio.get_running_loop().time()
            with bridges.configure(serve_tick_s=30):
                await reaction_rows(dsn, task, "seen")
                await asyncio.wait_for(waiting, 10)
            return asyncio.get_running_loop().time() - started

    assert run(go()) < 10


def test_the_local_and_email_outboxes_never_see_a_telegram_reaction(dsn, op):
    async def go():
        task = await new_task(dsn)
        ids = await reaction_rows(dsn, task, "seen")
        got = []
        for channel in ("local", "email"):
            async with bridges.outbox(dsn, FakeBridge(channel)) as box:
                got.append(mine(await box.due(), ids))
        return got

    assert run(go()) == [[], []]


# -- Telegram, on the emulator --------------------------------------------------


@pytest.fixture
def emu():
    e = Emulator().start()
    yield e
    e.stop()


@pytest.fixture
def tg(emu, tmp_path):
    group = chat()
    emu.control(chats=[emu_chat(group)])
    with tg_machine(tmp_path, [group]):
        yield group


async def tg_rows(dsn, group, mid, *stages, shape_emoji=PINNED["code_change"]) -> tuple[str, dict[str, str]]:
    task = await new_task(dsn)
    p = {"received_id": ledger.new_id(), "channel": "telegram", "chat_id": group, "message_id": str(mid)}
    ids = {}
    async with await db.connect(dsn) as conn:
        for stage in stages:
            ids[stage] = await reactions._request(
                conn, task, p, EYES if stage == "seen" else shape_emoji, stage
            )
    return task, ids


async def place_due(bridge, box, ids) -> list[str]:
    """Each of this test's due reactions placed through the bridge's sender."""
    placed = []
    for item in mine(await box.due(), ids):
        await bridge.sender.react(item, box)
        placed.append(item.stage)
    return placed


def test_telegram_eyes_then_the_shape_leave_one_reaction_the_shape(emu, dsn, tmp_path, tg):
    mid = emu.inject(int(tg), "add a dark mode toggle")

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            task, ids = await tg_rows(dsn, tg, mid, "seen")
            assert await place_due(bridge, box, ids) == ["seen"]
            first = emu.reactions(int(tg), mid)
            (seen,) = await of_type(dsn, "reaction.requested", reaction_id=ids["seen"])
            async with await db.connect(dsn) as conn:
                ids["shape"] = await reactions._request(conn, task, seen, PINNED["code_change"], "shape")
            assert await place_due(bridge, box, ids) == ["shape"]
            return first, emu.reactions(int(tg), mid), ids

    first, last, ids = run(go())
    assert first == [EYES] and last == [PINNED["code_change"]]
    for rid in ids.values():
        assert len(run(of_type(dsn, "reaction.placed", reaction_id=rid))) == 1


def test_telegram_eyes_delayed_by_a_flood_wait_never_lands_over_its_shape(emu, dsn, tmp_path, tg):
    mid = emu.inject(int(tg), "write me a report")

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            task, ids = await tg_rows(dsn, tg, mid, "seen")
            emu.control(flood_react=1)
            assert await place_due(bridge, box, ids) == ["seen"]  # tried; held by the flood wait
            unmarked = await of_type(dsn, "reaction.failed", reaction_id=ids["seen"])
            still_due = [i.stage for i in mine(await box.due(), ids)]
            (seen,) = await of_type(dsn, "reaction.requested", reaction_id=ids["seen"])
            async with await db.connect(dsn) as conn:
                ids["shape"] = await reactions._request(conn, task, seen, PINNED["written_report"], "shape")
            assert await place_due(bridge, box, ids) == ["shape"]  # waits the flood out, then places
            after = mine(await box.due(), ids)
            return unmarked, still_due, after, emu.reactions(int(tg), mid), ids

    unmarked, still_due, after, last, ids = run(go())
    assert unmarked == [] and still_due == ["seen"]
    assert after == [] and last == [PINNED["written_report"]]
    (closed,) = run(of_type(dsn, "reaction.placed", reaction_id=ids["seen"]))
    assert closed["superseded"] is True


def test_telegram_a_flood_wait_or_a_lost_connection_delays_a_reaction_without_dropping_it(
    emu, dsn, tmp_path, tg
):
    mid = emu.inject(int(tg), "thanks")

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            _, ids = await tg_rows(dsn, tg, mid, "seen")
            emu.control(flood_react=1)
            await place_due(bridge, box, ids)
            emu.control(down=True)
            await place_due(bridge, box, ids)
            emu.control(down=False)
            held = await of_type(dsn, "reaction.placed", reaction_id=ids["seen"])
            failed = await of_type(dsn, "reaction.failed", reaction_id=ids["seen"])
            await place_due(bridge, box, ids)
            return held, failed, emu.reactions(int(tg), mid), ids

    held, failed, last, ids = run(go())
    assert held == [] and failed == [] and last == [EYES]
    assert len(run(of_type(dsn, "reaction.placed", reaction_id=ids["seen"]))) == 1


def test_telegram_refuses_an_emoji_off_its_list_and_the_reply_still_sends(emu, dsn, tmp_path, tg):
    mid = emu.inject(int(tg), "a report please")

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            # ✍ with U+FE0F: not the code points Telegram's list holds.
            _, ids = await tg_rows(dsn, tg, mid, "shape", shape_emoji="\u270d\ufe0f")
            await place_due(bridge, box, ids)
            effect = await release(dsn, send(tg, "here it is"))
            out = await perform(dsn, bridge, effect)
            return ids, out

    ids, out = run(go())
    (failed,) = run(of_type(dsn, "reaction.failed", reaction_id=ids["shape"]))
    assert "REACTION_INVALID" in failed["error"]
    assert out.kind == "done" and emu.own(int(tg))[-1]["text"] == "here it is"
    assert emu.reactions(int(tg), mid) == []


def test_telegram_a_reaction_on_a_message_id_that_is_not_telegrams_fails_and_the_loop_goes_on(
    emu, dsn, tmp_path, tg
):
    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            _, ids = await tg_rows(dsn, tg, "not-a-number", "seen")
            await place_due(bridge, box, ids)
            return ids

    ids = run(go())
    (failed,) = run(of_type(dsn, "reaction.failed", reaction_id=ids["seen"]))
    assert failed["error"].startswith("not a Telegram message")


def test_telegram_the_bridge_loop_places_a_reaction_it_is_handed(emu, dsn, tmp_path, tg):
    from tests.telegram_port import Only

    mid = emu.inject(int(tg), "add a footer")

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            _, ids = await tg_rows(dsn, tg, mid, "seen")
            loop = asyncio.create_task(bridge._consume(Only(box, set(ids.values()))))
            try:
                await until(lambda: emu.reactions(int(tg), mid) == [EYES], 10)
            finally:
                loop.cancel()
            return ids

    ids = run(go())
    assert len(run(of_type(dsn, "reaction.placed", reaction_id=ids["seen"]))) == 1


# -- Slack, on the emulator ------------------------------------------------------


@pytest.fixture
def w(dsn, tmp_path):
    from tests.test_slack_bridge import ME, World

    team = "T0" + uuid.uuid4().hex[:10].upper()
    from tests.slack_emulator import Emulator as SlackEmulator

    emu = SlackEmulator().start(team=team, me=ME, channels=["C0OPS"])
    world = World(emu, dsn, tmp_path, team)
    (tmp_path / "projects").mkdir()
    world.state.mkdir()
    with (
        bridges.operator(tmp_path),
        bridges.configure(
            operator_channel="slack",
            operator_chat=world.ops,
            operator_slack_ids=(world.tom,),
            inbound_dir=str(tmp_path / "inbound"),
        ),
    ):
        yield world
    emu.stop()


def names(w, ts: str) -> list[str]:
    (m,) = [m for m in w.emu.messages(f"D{w.tom.partition('/')[2]}") if m["ts"] == ts]
    return [r["name"] for r in m.get("reactions", []) if w.emu.state()["me"] in r["users"]]


def _shape_for(dsn, received_id: str, emoji: str) -> str:
    async def go():
        (seen,) = await of_type(dsn, "reaction.requested", received_id=received_id, stage="seen")
        async with await db.connect(dsn) as conn:
            return await reactions._request(conn, seen["task_id"], seen, emoji, "shape")

    return run(go())


def _result(w, reaction_id: str) -> dict:
    from tests.test_slack_bridge import wait_for

    def got():
        return run(of_type(w.dsn, "reaction.placed", reaction_id=reaction_id)) or run(
            of_type(w.dsn, "reaction.failed", reaction_id=reaction_id)
        )

    wait_for(got)
    return got()[0]


def test_slack_eyes_then_the_shape_leave_the_shapes_name_alone(w):
    with w.bridge():
        ts = w.say(w.tom, "can you add a dark mode toggle?")
        bound = w.bind(w.tom, ts)
        (seen,) = run(reacts(w.dsn, bound["received_id"]))
        _result(w, seen["reaction_id"])
        first = names(w, ts)
        shape_id = _shape_for(w.dsn, bound["received_id"], PINNED["code_change"])
        _result(w, shape_id)
        last = names(w, ts)
    assert first == ["eyes"] and last == ["technologist"]


def test_slack_eyes_rate_limited_never_lands_over_its_shape(w):
    from tests.test_slack_bridge import wait_for

    with w.bridge():
        w.emu.control(
            faults=[
                {"method": "reactions.add", "kind": "429", "retry_after": 1, "nth": n} for n in range(1, 6)
            ]
        )
        ts = w.say(w.tom, "write me a report")
        bound = w.bind(w.tom, ts)
        wait_for(lambda: w.emu.state()["calls"].get("reactions.add", 0) >= 1)
        (seen,) = run(reacts(w.dsn, bound["received_id"]))
        assert run(of_type(w.dsn, "reaction.failed", reaction_id=seen["reaction_id"])) == []
        shape_id = _shape_for(w.dsn, bound["received_id"], PINNED["written_report"])
        placed = _result(w, shape_id)
        last = names(w, ts)
    assert "error" not in placed and last == ["writing_hand"]
    closed = _result(w, seen["reaction_id"])
    assert closed["superseded"] is True


def test_slack_a_missing_scope_is_the_reactions_result_and_the_reply_still_posts(w):
    with w.bridge():
        w.emu.control(faults=[{"method": "reactions.add", "kind": "error", "error": "missing_scope"}])
        ts = w.say(w.tom, "thanks")
        bound = w.bind(w.tom, ts)
        (seen,) = run(reacts(w.dsn, bound["received_id"]))
        failed = _result(w, seen["reaction_id"])
        from tests.test_slack_bridge import send as slack_send

        effect = w.release(slack_send(w.tom, "noted"))
        out = w.outcome(effect)
    assert failed["error"] == "missing_scope"
    assert out["kind"] == "done" and names(w, ts) == []


def test_slack_a_reaction_already_there_counts_as_placed(w):
    with w.bridge():
        ts = w.say(w.tom, "hello")
        bound = w.bind(w.tom, ts)
        (seen,) = run(reacts(w.dsn, bound["received_id"]))
        _result(w, seen["reaction_id"])

        # Another reaction row for the same message: Slack answers already_reacted.
        async def again():
            p = {**seen, "received_id": ledger.new_id()}
            async with await db.connect(w.dsn) as conn:
                return await reactions._request(conn, seen["task_id"], p, EYES, "seen")

        second = _result(w, run(again()))
    assert "error" not in second and names(w, ts) == ["eyes"]
