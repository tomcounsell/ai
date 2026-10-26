"""Reactions (docs/plans/reactions-fun.md): which messages get one, 👀 at
once, the classifier's handling and done picks from one palette, a done
for every message a turn read, 📝 when there is no pick, each platform's
emoji, and what a session reads of the reaction on a message."""

import asyncio
import base64
import json
import uuid
from types import SimpleNamespace

from core import bridge, db, intake, judgement, ledger, reactions, session, tasks, transcripts
from core.judgement_tasks import BY_SITE, COMPLETED, HANDLING
from tests import bridges, test_kernel
from tests.test_kernel import GROUP, QUIET_GROUP, at, posted, said, tom_says

# The kernel tests' fixtures: a project's team chats, and Tom's Slack DM.
team, dm = test_kernel.team, test_kernel.dm

run = asyncio.run
EYES, MEMO = "\U0001f440", "\U0001f4dd"
LONG_AGO, LATER = "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"


async def requested(dsn, received_id) -> list[dict]:
    return await bridges.of_type(dsn, "reaction.requested", received_id=received_id)


async def owed(dsn, received_id) -> list[dict]:
    return await bridges.of_type(dsn, "reaction.owed", received_id=received_id)


def stages(rows) -> list[tuple[str, str]]:
    return [(r["stage"], r["label"]) for r in rows]


def send(target, text="ok", reply_to=None, files=()):
    """An effect entry as `session.record` makes it: a chat send."""
    payload = {"text": text, "reply_to": reply_to, "files": [{"path": f, "sha256": "x"} for f in files]}
    return {
        "request": {"action_type": "telegram.send_message", "target": target, "payload": payload},
        "kind": "held",
    }


def effect(action_type, target="t", **payload):
    return {"request": {"action_type": action_type, "target": target, "payload": payload}, "kind": "held"}


async def worked(dsn, task, effects=(), tools=(), before=(), state="clarify", outcome="done") -> int:
    """A collected turn of `task`: its transcript holds `tools` (name,
    input) called during it, and `before`, calls of an earlier turn the same
    file still holds. How many reactions `owe` wrote for it."""
    turn_id = ledger.new_id()
    lines = [
        json.dumps({"timestamp": when, "message": {"content": [{"type": "tool_use", "name": n, "input": i}]}})
        for when, calls in ((LONG_AGO, before), (LATER, tools))
        for n, i in calls
    ]
    body = {"turn_id": turn_id, "name": "s.jsonl", "offset": 0, "chunk": 0,
            "base64": base64.b64encode("\n".join(lines).encode()).decode()}  # fmt: skip
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": state})
        await conn.execute(
            "INSERT INTO documents (kind, id, body) VALUES (%s, %s, %s::jsonb)",
            (transcripts.KIND, f"{turn_id}/s.jsonl/0", json.dumps(body)),
        )
        transcript = {"files": [{"name": "s.jsonl"}]}
        await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": outcome, "result": {},
                                                       "transcript": transcript})  # fmt: skip
        did = await reactions.did(conn, task, turn_id, list(effects))
        return await reactions.owe(conn, task, turn_id, list(effects), did)


class Port:
    """A judgement port that picks `picks[site]` (None: abstain; "fail":
    both legs fail; "stopped": the task is stopped), recording what it was
    asked."""

    def __init__(self, handling="thinking", completed="thumbs_up"):
        self.picks = {HANDLING.site: handling, COMPLETED.site: completed}
        self.asked = []

    async def judge(self, task, inputs, *, task_id, ref, dsn=None):
        self.asked.append((task.site, ref["received_id"], dict(inputs)))
        pick = self.picks[task.site]
        if pick == "stopped":
            raise tasks.TaskStopped(task_id)
        jid = ledger.new_id()
        payload = {"judgement_id": jid, "site": task.site, "ref": ref}
        if pick not in (None, "fail"):
            payload["picked"] = {task.questions[0].id: pick}
        async with await db.connect(dsn) as conn:
            kind = "judgement.failed" if pick == "fail" else "judgement.answered"
            await ledger.append(conn, task_id, kind, payload)
        return judgement.Judgement(
            jid, task.site, pick != "fail", {}, {}, (), "primary", "m", 0, (), 0, payload
        )


async def react_all(dsn, port, *bound) -> list:
    """The serve jobs' part for these bindings' messages: each one's next
    reaction written, one pass."""
    rids = {b["received_id"] for b in bound}
    async with await db.connect(dsn) as conn:
        due = [i for i in await reactions.pending(conn) if i["row"]["received_id"] in rids]
        return [(i["stage"], await reactions.react(port, conn, i, dsn)) for i in due]


async def new_session(dsn) -> None:
    """The group's chat session stopped, so the next message starts one
    with no earlier test's messages in its window."""
    m = await said(dsn, "a new session")
    async with await db.connect(dsn) as conn:
        await tasks.stop(conn, m["task_id"], reason="a new session")


async def place_all(dsn, channel) -> None:
    """The bridge's part: every due reaction on `channel` placed."""
    async with bridges.outbox(dsn, bridges.FakeBridge(channel)) as box:
        for due in await box._reactions():
            await box.reacted(due)


# -- the palette ------------------------------------------------------------------


def test_one_palette_on_each_platform_is_what_both_questions_pick_from():
    labels = set(reactions.PALETTE)
    assert set(reactions.REACTIONS) == {"telegram", "slack"}
    for channel, table in reactions.REACTIONS.items():
        assert set(table) == labels, channel
        assert all("️" not in e for e in table.values()), channel
    assert reactions.REACTIONS["slack"]["seen"] == "eyes" and reactions.REACTIONS["telegram"]["noted"] == MEMO
    assert set(reactions.PICKS) == labels - {"seen"} and set(reactions.ORDER) <= set(reactions.PICKS)
    for site in (HANDLING, COMPLETED):
        (q,) = site.questions
        assert (
            q.kind is judgement.Kind.PICK
            and set(q.labels) == set(reactions.PICKS)
            and q.proceed == set(q.labels)
        )
        assert BY_SITE[site.site] is site and site.site in ledger.QUIET_SITES
    # An activity emoji fits only work that was done or plainly asked for.
    for label in reactions.ORDER:
        assert "only when `did` names it" in reactions.PICKS[label], label
    assert "fits only when `did` names that activity" in COMPLETED.questions[0].text


def test_the_top_pick_is_taken_though_many_emoji_split_the_probability():
    q = COMPLETED.questions[0]
    labels = sorted(q.labels)
    split = {k: 0.7 / (len(labels) - 1) for k in labels} | {"heart": 0.3}
    flat = {k: 1 / len(labels) for k in labels}
    floor = COMPLETED.floor["primary"]
    assert judgement.decide(q, split, floor)["decision"] == "proceed"
    assert judgement.decide(q, flat, floor)["decision"] == "abstain"


# -- who gets 👀 ----------------------------------------------------------------------


def test_in_team_chats_only_a_message_that_wakes_the_session_is_marked(dsn, team):
    """An Eng group's every message, and in a mentions-only group one naming
    Valor or replying to his post, gets 👀; an unaddressed one gets none,
    and no turn that reads it owes it a reaction."""

    async def go():
        eng = await said(dsn, "the build is red")
        named = await said(dsn, "valor, is staging up?", chat=QUIET_GROUP)
        mid = "5550"
        entries = [{"channel": "telegram", "chat_id": QUIET_GROUP, "message_id": mid}]
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, named["task_id"], "notice.sent", {"notice_id": ledger.new_id(), "sent": entries}
            )
        replied = await said(dsn, "yes that one", chat=QUIET_GROUP, reply_to=mid)
        quiet = await said(dsn, "lunch at noon?", chat=QUIET_GROUP)
        await worked(dsn, named["task_id"])
        await react_all(dsn, Port(), quiet)
        return [(b["as"], await requested(dsn, b["received_id"])) for b in (eng, named, replied, quiet)], (
            await owed(dsn, quiet["received_id"])
        )

    (eng, named, replied, quiet), quiet_owed = run(go())
    for as_, rows in (eng, named):
        assert as_ == "steer" and [(r["emoji"], r["label"], r["stage"], r["completes"]) for r in rows] == [
            (EYES, "seen", "seen", True)
        ]
    assert quiet == ("heard", []) and quiet_owed == []
    assert replied[0] == "steer" and replied[1][0]["emoji"] == EYES


def test_a_local_message_is_not_marked(dsn, dm):
    async def go():
        inbound = intake.Inbound(
            channel="local", chat_id="local", chat_kind="dm", message_id=str(uuid.uuid4()), sender_id="tom",
            sender_name="Tom", sent_at="2026-10-17T00:00:00Z", text="hello",
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, inbound)
            await intake.bind(conn)
        return await requested(dsn, got.received_id)

    assert run(go()) == []


def test_toms_reply_to_stopped_work_ends_on_noted_without_a_judgement(dsn, dm):
    """Tom's reply to a stopped task's post is bound `none` to it: 👀, its
    done owed at once, and, the task stopped and so not billable, 📝 with
    no judgement; nothing is due after."""

    async def go():
        async with await db.connect(dsn) as conn:
            done = await tasks.start(conn, tasks.Brief(instruction="old work", max_effect_class="act"))
        await posted(dsn, done, dm, at(20), "Done.")
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, done, reason="test")
        late = await tom_says(dsn, dm, "one more thing", at(30), reply_to=at(20))
        port = Port()
        first = await react_all(dsn, port, late)
        again = await react_all(dsn, port, late)
        return late, first, again, port.asked, await requested(dsn, late["received_id"])

    late, first, again, asked, rows = run(go())
    assert late["as"] == "none" and first == [("done", "noted")] and again == [] and asked == []
    assert stages(rows) == [("seen", "seen"), ("done", "noted")]
    assert rows[1]["why"] == "done: noted, its task was stopped"


def test_a_message_gets_a_handling_pick_then_noted_when_there_is_none(dsn, team):
    """A steer's handling is the classifier's pick from its text; an
    abstain gives 📝, as does a judgement both legs failed twice, and no
    port at all."""

    async def go():
        q = await said(dsn, "valor, should we open on Sundays?")
        port = Port(handling="thinking")
        picked = await react_all(dsn, port, q)
        a = await said(dsn, "what a week")
        abstained = await react_all(dsn, Port(handling=None), a)
        f = await said(dsn, "ping")
        failing = Port(handling="fail")
        failed = [await react_all(dsn, failing, f), await react_all(dsn, failing, f)]
        n = await said(dsn, "hello")
        unported = await react_all(dsn, None, n)
        rows = [await requested(dsn, m["received_id"]) for m in (q, a, f, n)]
        return port.asked, picked, abstained, failed, unported, rows

    asked, picked, abstained, failed, unported, rows = run(go())
    ((site, _, inputs),) = asked
    assert site == "reaction.handling" and inputs["message"] == "valor, should we open on Sundays?"
    assert inputs["bound_as"] == "steer" and set(inputs) == set(HANDLING.inputs)
    assert picked == [("handling", "thinking")] and abstained == [("handling", "noted")]
    assert failed == [[("handling", None)], [("handling", "noted")]] and unported == [("handling", "noted")]
    assert [stages(r) for r in rows] == [[("seen", "seen"), ("handling", label)]
                                         for label in ("thinking", "noted", "noted", "noted")]  # fmt: skip
    assert rows[0][1]["emoji"] == "\U0001f914"
    assert rows[0][1]["why"].startswith("handling, the classifier's pick: read as a question or idea")


def test_every_message_a_turn_read_gets_its_own_done(dsn, team):
    """Three messages, one turn: each owes a done, told what the turn sent
    for it (a reply to it, a reply in the chat to another, or none), and
    each is judged once for it, never for a handling."""

    async def go():
        await new_session(dsn)
        a = await said(dsn, "how's the shop?")
        b = await said(dsn, "and the florist?")
        c = await said(dsn, "thanks valor")
        task = a["task_id"]
        n = await worked(dsn, task, [send(GROUP, "busy, and open till 6", reply_to=a["message_id"])],
                         [("WebSearch", {"query": "florist"})])  # fmt: skip
        again = await worked(dsn, task)
        port = Port(completed="heart")
        done = await react_all(dsn, port, a, b, c)
        return (
            a,
            n,
            again,
            port.asked,
            done,
            [await owed(dsn, m["received_id"]) for m in (a, b, c)],
            [await requested(dsn, m["received_id"]) for m in (a, b, c)],
        )

    a, n, again, asked, done, owing, rows = run(go())
    assert n == 3 and again == 0
    assert [o["replied"] for (o,) in owing] == [
        "a reply to this message: busy, and open till 6",
        f"replied in this chat to message {a['message_id']}: busy, and open till 6",
        f"replied in this chat to message {a['message_id']}: busy, and open till 6",
    ]
    assert {(o["stage"], tuple(o["did"]), o["outcome"]) for (o,) in owing} == {
        ("done", ("web_search",), "done")
    }
    assert done == [("done", "heart")] * 3 and {s for s, _, _ in asked} == {"reaction.completed"}
    assert [stages(r) for r in rows] == [[("seen", "seen"), ("done", "heart")]] * 3
    inputs = asked[0][2]
    assert (
        set(inputs) == set(COMPLETED.inputs) and inputs["did"] == "web_search" and inputs["handling"] == EYES
    )
    assert rows[1][1]["why"] == (
        f"done, the classifier's pick after your turn (you replied in this chat to message {a['message_id']}; "
        "you did web_search): read as thanks, kindness, something lovely"
    )


def test_a_turn_that_sends_no_text_is_answered_by_its_reaction(dsn, team):
    """A no-text turn on a yes-or-no question is judged with `no text sent`,
    under the rule that no text means agreement, and nothing done."""

    async def go():
        q = await said(dsn, "valor, can we ship tomorrow?")
        await worked(dsn, q["task_id"], tools=[("Read", {"file_path": "/w/notes.md"})])
        port = Port(completed="thumbs_up")
        done = await react_all(dsn, port, q)
        return done, port.asked, await requested(dsn, q["received_id"])

    done, asked, rows = run(go())
    ((_, _, inputs),) = asked
    assert inputs["replied"] == "no text sent" and inputs["did"] == "nothing beyond reading and replying"
    assert "No text sent means he agreed or acknowledged" in COMPLETED.questions[0].text
    assert done == [("done", "thumbs_up")] and rows[-1]["emoji"] == "\U0001f44d"
    assert "no text sent, so the reaction is your answer" in rows[-1]["why"]


def test_an_errored_turn_leaves_stalled_and_the_next_turn_the_done(dsn, team):
    """A turn that errors after reading two steers owes each `stalled`, and
    nothing for one bound after its prompt; the next turn owes all three
    their done, which replaces the stalled."""

    async def go():
        await new_session(dsn)
        a = await said(dsn, "valor, look into the 500s")
        b = await said(dsn, "and the slow page")
        task = a["task_id"]
        async with await db.connect(dsn) as conn:
            turn_id = ledger.new_id()
            await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify"})
        c = await said(dsn, "also the logo")
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "failed", "result": {}}
            )
            stalled = await reactions.owe(conn, task, turn_id, [], [])
        port = Port(handling="zap", completed="grin")
        first = await react_all(dsn, port, a, b, c)
        later = await worked(dsn, task, tools=[("Grep", {"pattern": "500"})])
        second = await react_all(dsn, port, a, b, c)
        return stalled, first, later, second, [await requested(dsn, m["received_id"]) for m in (a, b, c)]

    stalled, first, later, second, rows = run(go())
    assert stalled == 2 and later == 3
    assert first == [("stalled", "grin"), ("stalled", "grin"), ("handling", "zap")]
    assert second == [("done", "grin")] * 3
    assert [[s for s, _ in stages(r)] for r in rows] == [["seen", "stalled", "done"]] * 2 + [
        ["seen", "handling", "done"]
    ]


def test_a_fresh_or_compaction_turn_owes_nothing(dsn, team):
    async def go():
        await new_session(dsn)
        m = await said(dsn, "valor, find the florist's supplier")
        task = m["task_id"]
        fresh = await worked(dsn, task, state="fresh")
        compacted = await worked(dsn, task, state="compaction")
        done = await worked(dsn, task)
        return (fresh, compacted, done), await owed(dsn, m["received_id"])

    counts, rows = run(go())
    assert counts == (0, 0, 1) and [r["stage"] for r in rows] == ["done"]


def test_a_stop_ends_on_ok_at_once(dsn, dm):
    async def go():
        await tom_says(dsn, dm, "hello", at(10))
        stop = await tom_says(dsn, dm, "stop", at(20))
        return stop, await react_all(dsn, Port(), stop), await requested(dsn, stop["received_id"])

    stop, due, rows = run(go())
    assert stop["as"] == "stop" and due == []
    assert [(r["stage"], r["emoji"], r.get("why")) for r in rows] == [
        ("seen", "eyes", None),
        ("done", "ok_hand", "done: you stopped the work, as asked"),
    ]


def test_a_message_that_starts_a_task_owes_its_done_at_once(dsn, tmp_path):
    """With no project to hold a chat session, Tom's message starts a task:
    👀, and its done owed at once, judged with no handling first."""

    async def go():
        with bridges.operator(tmp_path):
            inbound = intake.Inbound(
                channel="telegram", chat_id=bridges.OPERATOR, chat_kind="dm", message_id="77",
                sender_id=bridges.OPERATOR, sender_name="Tom", sent_at="2026-10-17T15:00:00Z", text="ship it",
            )  # fmt: skip
            async with await db.connect(dsn) as conn:
                got = await intake.receive(conn, inbound)
                await intake.bind(conn)
        (start,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
        port = Port(completed="task")
        return start, await react_all(dsn, port, start), port.asked, await requested(dsn, got.received_id)

    start, done, asked, rows = run(go())
    assert start["as"] == "start" and done == [("done", "task")]
    assert [s for s, _, _ in asked] == ["reaction.completed"]
    assert asked[0][2]["outcome"] == "handed to a new task, which does the work"
    assert stages(rows) == [("seen", "seen"), ("done", "task")]


def test_a_message_on_a_stopped_task_ends_on_noted(dsn, team):
    """A steer whose task is stopped before a turn reads it, and one whose
    task is stopped while its judgement runs, each end on 📝, with nothing
    due after."""

    async def go():
        m = await said(dsn, "valor, plan the party")
        await react_all(dsn, Port(handling="party"), m)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, m["task_id"], reason="a new session")
        port = Port()
        ended = [await react_all(dsn, port, m), await react_all(dsn, port, m)]
        n = await said(dsn, "valor, and the cake?")
        raced = [await react_all(dsn, Port(handling="stopped"), n)]
        return ended, port.asked, raced, [await requested(dsn, x["received_id"]) for x in (m, n)]

    ended, asked, raced, (m_rows, n_rows) = run(go())
    assert ended == [[("done", "noted")], []] and asked == []
    assert stages(m_rows) == [("seen", "seen"), ("handling", "party"), ("done", "noted")]
    assert raced == [[("handling", "noted")]] and n_rows[-1]["why"] == "handling: noted, its task was stopped"


def test_a_refused_done_is_replaced_by_noted_once(dsn, team):
    async def go():
        m = await said(dsn, "valor, nice work")
        await worked(dsn, m["task_id"])
        await react_all(dsn, Port(completed="unicorn"), m)
        async with bridges.outbox(dsn, bridges.FakeBridge("telegram")) as box:
            for d in await box._reactions():
                if d.received_id == m["received_id"]:
                    await box.reacted(d, "REACTION_INVALID" if d.stage == "done" else None)
        replaced = await react_all(dsn, Port(), m)
        async with bridges.outbox(dsn, bridges.FakeBridge("telegram")) as box:
            for d in await box._reactions():
                if d.received_id == m["received_id"]:
                    await box.reacted(d, "REACTION_INVALID")
        after = await react_all(dsn, Port(), m)
        return replaced, after, await requested(dsn, m["received_id"])

    replaced, after, rows = run(go())
    assert replaced == [("noted", "noted")] and after == []
    assert stages(rows) == [("seen", "seen"), ("done", "unicorn"), ("noted", "noted")]
    assert rows[-1]["why"] == "noted: your reaction \U0001f984 was refused by the chat"


def test_a_message_bound_before_the_change_is_left_as_it_is(dsn):
    """A 👀 with no `completes` (bound before 2026-10-26), with or without
    its old activity reaction, on a running task or a stopped one, is never
    judged or reacted on again."""

    async def go():
        async with await db.connect(dsn) as conn:
            live = await tasks.start(conn, tasks.Brief(instruction="a chat", max_effect_class="act"))
            gone = await tasks.start(conn, tasks.Brief(instruction="a chat", max_effect_class="act"))
            rids = []
            for task, stages_ in ((live, ("seen",)), (live, ("seen", "shape")), (gone, ("seen",))):
                rid = ledger.new_id()
                rids.append({"received_id": rid})
                for stage in stages_:
                    await ledger.append(conn, task, "reaction.requested", {
                        "reaction_id": ledger.new_id(), "received_id": rid, "channel": "telegram",
                        "chat_id": GROUP, "message_id": "1", "emoji": EYES, "label": "seen", "stage": stage})  # fmt: skip
            await tasks.stop(conn, gone, reason="test")
        return await react_all(dsn, Port(), *rids)

    assert run(go()) == []


def test_record_writes_the_owed_reactions_with_the_collection(dsn, team, monkeypatch):
    """Owed reactions are written in `record`'s transaction: a failure
    after them leaves neither they nor `turn.collected`, and collecting
    again writes both."""
    from core import signals
    from core.machine import State

    async def go():
        m = await said(dsn, "valor, search for a vase supplier")
        task = m["task_id"]
        turn_id = ledger.new_id()
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify"})
            await ledger.append(
                conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {}}
            )
            b = await tasks.brief(conn, task)
            owe = reactions.owe

            async def dies(*a):
                await owe(*a)
                raise RuntimeError("killed")

            monkeypatch.setattr(reactions, "owe", dies)
            try:
                await session.record(
                    conn, task, turn_id, signals.Signals(), state=State.CLARIFY, workspace=None, brief=b
                )
            except RuntimeError:
                pass
            killed = (
                await owed(dsn, m["received_id"]),
                await bridges.of_type(dsn, "turn.collected", turn_id=turn_id),
            )
            monkeypatch.setattr(reactions, "owe", owe)
            await session.record(
                conn, task, turn_id, signals.Signals(), state=State.CLARIFY, workspace=None, brief=b
            )
        return (
            killed,
            await owed(dsn, m["received_id"]),
            await bridges.of_type(dsn, "turn.collected", turn_id=turn_id),
        )

    killed, after, collected = run(go())
    assert killed == ([], []) and [r["stage"] for r in after] == ["done"] and len(collected) == 1


# -- the outbox and the senders ------------------------------------------------------


def test_eyes_go_out_first_and_a_late_handling_never_lands_over_the_done(dsn, team):
    """👀 is yielded before its handling, never skipped; when a handling and
    the done are both due, only the done is placed, carrying the names
    already placed below it."""

    async def go():
        m = await said(dsn, "valor, file an issue for the 500")
        await react_all(dsn, Port(handling="issue"), m)
        async with bridges.outbox(dsn, bridges.FakeBridge("telegram")) as box:
            first = [d for d in await box._reactions() if d.received_id == m["received_id"]]
            await box.reacted(first[0])
            await worked(dsn, m["task_id"], tools=[("Bash", {"command": "gh issue create -t 500"})])
            await react_all(dsn, Port(completed="issue"), m)
            second = [d for d in await box._reactions() if d.received_id == m["received_id"]]
        placed = await bridges.of_type(dsn, "reaction.placed", received_id=m["received_id"])
        return first, second, placed

    first, second, placed = run(go())
    assert [(d.stage, d.label) for d in first] == [("seen", "seen"), ("handling", "issue")]
    assert [(d.stage, d.label, d.previous) for d in second] == [("done", "issue", (EYES,))]
    assert [p.get("superseded") for p in placed] == [None, True]


def test_a_rows_wake_yields_again_before_the_bridges_tick(dsn):
    """On a row's wake the outbox yields what is due before it reconciles
    and runs the bridge's tick (the gap fill), so a 👀 never waits on them;
    on the tick's wake it does not."""

    async def go(woke):
        fake = bridges.FakeBridge("telegram")
        async with bridges.outbox(dsn, fake) as box:
            calls = []

            async def due():
                calls.append(fake.ticks)
                return [] if len(calls) == 1 else [f"due after {fake.ticks} ticks"]

            async def wait():
                return woke

            async def reconcile():
                return []

            box.due, box.wait, box.reconcile = due, wait, reconcile
            return await anext(box.__aiter__())

    assert run(go(True)) == "due after 0 ticks"
    assert run(go(False)) == "due after 1 ticks"


def test_each_activity_from_effects_and_tools():
    here = {"-1001"}
    by_effect = {
        "task": effect("task.start"), "code": effect("push_branch"), "send": effect("email.send"),
        "present": send("-1001", files=["/w/deck.pptx", "/w/reply.ogg"]),
        "document": send("-1001", files=["/w/brief.pdf"]),
    }  # fmt: skip
    for label, e in by_effect.items():
        assert reactions.effect_did(e["request"], here) == label, label
    assert reactions.effect_did(send("-1001", files=["/w/reply.ogg"])["request"], here) is None
    assert reactions.effect_did(send("-1009")["request"], here) == "send"
    assert reactions.effect_did(send("-1001", files=["/w/photo.png"])["request"], here) == "send"
    by_tool = {
        ("WebFetch", "{}"): "web_search", ("mcp__byob__browser_read", "{}"): "web_search",
        ("Agent", '{"subagent_type": "Explore"}'): "lookup", ("Glob", "{}"): "lookup",
        ("Skill", '{"skill": "yudame:present"}'): "present", ("Skill", '{"skill": "yudame:calendar"}'): "schedule",
        ("Skill", '{"skill": "yudame-dev:do-issue"}'): "issue", ("CronCreate", "{}"): "schedule",
        ("Edit", '{"file_path": "/w/app.py"}'): "code", ("Write", '{"file_path": "/w/plan.md"}'): "document",
        ("Bash", '{"command": "gh issue create -t x"}'): "issue", ("Bash", '{"command": "git commit -m x"}'): "code",
        ("Bash", '{"command": "valor-telegram read --search x"}'): "lookup",
        ("Bash", '{"command": "gws calendar events insert --json {}"}'): "schedule",
        ("Bash", '{"command": "cat x | grep y"}'): "lookup",
        ("Bash", '{"command": "ls"}'): None, ("Read", '{"file_path": "/w/a.py"}'): None,
        ("Write", '{"file_path": "/w/repo/.valor/effects/a.json"}'): None,
        ("Write", '{"file_path": "/t/state/work/claude/projects/-t-repo/memory/tom-prefers-pdf.md"}'): None,
        ("Edit", '{"file_path": "/t/state/work/claude/projects/-t-repo/memory/MEMORY.md"}'): None,
        ("Write", '{"file_path": "/Users/v/.claude/plans/x.md"}'): None,
    }  # fmt: skip
    for (name, given), label in by_tool.items():
        assert reactions.tool_did(name, json.loads(given)) == label, (name, given)


def item(emoji, label, stage="seen", previous=(), chat="T0ABC/C0DEF"):
    return bridge.ReactionDue("r1", "t1", "m1", chat, "1.2", emoji, stage, "now", label, previous)


class Box:
    def __init__(self):
        self.results = []

    async def reacted(self, item, error=None):
        self.results.append((item.reaction_id, error))


class SlackWire:
    team_id = "T0ABC"

    def __init__(self):
        self.calls = []

    async def react(self, channel, ts, name):
        self.calls.append(("add", name))

    async def unreact(self, channel, ts, name):
        self.calls.append(("remove", name))


def slack_sender():
    from bridges.slack.send import Sender

    kernel = SimpleNamespace(split_text=lambda t: [t], Failed=RuntimeError)
    return Sender(SlackWire(), kernel)


def test_slack_adds_the_name_and_removes_each_differing_earlier_one():
    """A done removes 👀 and the handling; a done that repeats its handling
    keeps it; a row with no label (written before rows named it) is `eyes`."""

    async def go():
        s, box = slack_sender(), Box()
        await s.react(item("tada", "party", "done", ("eyes", "hourglass_flowing_sand")), box)
        await s.react(item("+1", "thumbs_up", "done", ("eyes", "+1")), box)
        await s.react(item(EYES, None), box)
        return s.wire.calls, box.results

    calls, results = run(go())
    assert calls == [("add", "tada"), ("remove", "eyes"), ("remove", "hourglass_flowing_sand"),
                     ("add", "+1"), ("remove", "eyes"), ("add", "eyes")]  # fmt: skip
    assert [e for _, e in results] == [None, None, None]


class Client:
    """Telethon's client, as far as `TelethonWire.react` uses it."""

    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    async def get_input_entity(self, chat):
        return chat

    async def __call__(self, request):
        from telethon.tl.functions.messages import (
            GetAvailableReactionsRequest,
            SearchCustomEmojiRequest,
            SendReactionRequest,
        )

        if isinstance(request, GetAvailableReactionsRequest):
            listed = [
                SimpleNamespace(reaction=EYES, inactive=None),
                SimpleNamespace(reaction="\U0001f4a9", inactive=True),
            ]
            return SimpleNamespace(reactions=listed)
        if isinstance(request, SearchCustomEmojiRequest):
            self.sent.append(("search", request.emoticon))
            return SimpleNamespace(
                document_id=[5368324170671202286] if request.emoticon == "\U0001f50d" else []
            )
        assert isinstance(request, SendReactionRequest)
        self.sent.append(("react", request.reaction[0]))


def test_telegram_sends_a_listed_emoji_as_itself_and_another_as_premium_custom_emoji(tmp_path):
    from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji

    from bridges.telegram.wire import TelethonWire

    async def go():
        wire = TelethonWire(tmp_path / "s", 1, "h")
        wire.client = Client()
        for emoji in (EYES, "\U0001f50d", "\U0001f50d", "\U0001f4a9"):
            await wire.react(1, 2, emoji)
        return wire.client.sent

    sent = run(go())
    assert sent == [
        ("react", ReactionEmoji(EYES)),
        ("search", "\U0001f50d"),
        ("react", ReactionCustomEmoji(5368324170671202286)),
        ("react", ReactionCustomEmoji(5368324170671202286)),
        ("search", "\U0001f4a9"),
        ("react", ReactionEmoji("\U0001f4a9")),
    ]


# -- what the session reads --------------------------------------------------------


def test_a_session_reads_the_handling_and_the_done_its_turn_earned(dsn, team):
    """The line names the reaction on the message and what set it; the done
    set after a turn read the message reaches the next turn as its own line;
    a message with no reaction says nothing."""

    async def go():
        first = await said(dsn, "valor, find me the florist's opening hours")
        task = first["task_id"]
        port = Port(handling="web_search", completed="check")
        await react_all(dsn, port, first)
        read = session.chat_prompt(await bridges.rows(dsn, task))
        await worked(dsn, task, [send(GROUP, "9 to 5", reply_to=first["message_id"])],
                     [("WebSearch", {"query": "florist"})])  # fmt: skip
        await react_all(dsn, port, first)
        second = await said(dsn, "and book a table")
        return first, second, read, session.chat_prompt(await bridges.rows(dsn, task))

    first, second, read, later = run(go())
    assert (
        f"(message {first['message_id']}; reacted \U0001f50d: handling, the classifier's pick: read as "
        "searching or reading the web" in read
    )
    assert (
        f"- Your reaction on message {first['message_id']} is now ✅: done, the classifier's pick after your "
        "turn (you replied to it; you did web_search): read as confirmed, done, checked off" in later
    )
    assert f"(message {second['message_id']}; reacted {EYES} (read))" in later
    assert "florist" not in later


def test_chat_line_shows_the_latest_stage_and_rows_from_before_the_change():
    m = {"sent_at": "2026-10-17T13:30:00+00:00", "sender_name": "Tom", "message_id": "9", "text": "go"}
    shown = reactions.shown([
        {"type": "reaction.requested", "payload": {"reaction_id": "a", "received_id": "r", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "b", "received_id": "r", "channel": "slack",
                                                   "emoji": "+1", "label": "thumbs_up", "stage": "done",
                                                   "why": "done: yes"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "c", "received_id": "r", "channel": "slack",
                                                   "emoji": "zap", "label": "zap", "stage": "handling",
                                                   "why": "handling: late"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "d", "received_id": "f", "channel": "slack",
                                                   "emoji": "eyes", "label": "seen", "stage": "seen"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "e", "received_id": "f", "channel": "slack",
                                                   "emoji": "mag", "label": "web_search", "stage": "shape",
                                                   "turn_id": "t2"}},
        {"type": "reaction.requested", "payload": {"reaction_id": "g", "received_id": "f", "channel": "slack",
                                                   "emoji": "memo", "label": "noted", "stage": "done",
                                                   "why": "done: noted"}},
        {"type": "reaction.failed", "payload": {"reaction_id": "g", "received_id": "f", "error": "x"}},
    ])  # fmt: skip
    assert (
        intake.chat_line(m, shown["r"])
        == "[2026-10-17 13:30 UTC] Tom (message 9; reacted :+1:: done: yes): go"
    )
    assert shown["f"] == "reacted :mag:: your turn did web_search"
    assert intake.chat_line(m) == "[2026-10-17 13:30 UTC] Tom (message 9): go"


def test_a_new_sessions_history_shows_the_reactions_on_earlier_messages(dsn, team):
    async def go():
        first = await said(dsn, "deploy the menu page")
        await worked(dsn, first["task_id"], tools=[("Edit", {"file_path": "/w/menu.py"})])
        await react_all(dsn, Port(completed="rocket"), first)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, first["task_id"], reason="a new session")
        second = await said(dsn, "valor?")
        async with await db.connect(dsn) as conn:
            return first, (await tasks.brief(conn, second["task_id"])).instruction

    first, instruction = run(go())
    assert f"(message {first['message_id']}; reacted \U0001f680: done, the classifier's pick" in instruction
    assert "you did code): read as launch, deploy, ship it)" in instruction


def test_a_working_turn_reads_the_reaction_on_toms_steer():
    steer = {"received_id": "r", "text": "use the blue one", "provenance": {"by": "tom"}}
    shown = {"r": "reacted 👀 (read)"}
    assert (
        session._steering([steer], shown)
        == "Tom wrote while you worked:\n- use the blue one (reacted 👀 (read))"
    )


def test_the_resident_kernel_writes_each_due_reaction_in_a_job_of_its_own(dsn, team):
    from core import serve

    async def go():
        m = await said(dsn, "valor, the shop looks great today")
        kernel = serve.Kernel(None, {}, None, dsn, judgement_port=Port(handling="star_struck"))
        async with await db.connect(dsn) as conn:
            await kernel.react(conn)
        await asyncio.gather(*kernel.reacting.values())
        return await requested(dsn, m["received_id"])

    assert stages(run(go())) == [("seen", "seen"), ("handling", "star_struck")]
