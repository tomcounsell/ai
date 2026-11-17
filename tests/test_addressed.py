"""An untagged message meant for Valor in a mentions-only team chat
(docs/plans/3138d3b5-addressed.md): a reply down a chain that reaches his
post wakes him; any other heard message is asked `intake.addressed`, and
its label steers, marks, reacts, or leaves it heard."""

import asyncio
import json
import pathlib

import pytest

from core import db, intake, judgement, judgement_sites, ledger, reactions, session, tasks
from core.judgement_tasks import ADDRESSED, BY_SITE
from core.settings import settings
from tests import bridges
from tests.test_kernel import OTHER, rows_of, said, turn

# A mentions-only group and one that takes every message, of their own, so
# the sessions these tests leave open steer no other test's.
QUIET_GROUP, GROUP = "-1011", "-1012"
run = asyncio.run


@pytest.fixture
def team(tmp_path):
    with bridges.operator(tmp_path):
        (pathlib.Path(settings.projects_dir) / "meadow.toml").write_text(
            'name = "meadow"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
            f'chats = ["telegram:{GROUP}", "telegram:{QUIET_GROUP}"]\nmentions = ["telegram:{QUIET_GROUP}"]\n'
        )
        yield


EYES = "\U0001f440"


async def fresh(dsn) -> str:
    """The quiet group's session stopped, so the next message starts one."""
    m = await said(dsn, "a new session", chat=QUIET_GROUP)
    async with await db.connect(dsn) as conn:
        await tasks.stop(conn, m["task_id"], reason="a new session")
    return m["task_id"]


async def valor_posts(dsn, task, mid, text="what's the deadline?", chat=QUIET_GROUP) -> None:
    """Valor's Telegram send `mid` in `chat`, on `task`."""
    effect = ledger.new_id()
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "effect.intent", {"effect_id": effect, "action_type": "telegram.send_message",
                            "target": chat, "payload": {"text": text}})  # fmt: skip
        await ledger.append(conn, task, "effect.outcome", {"effect_id": effect, "idempotency_key": "k", "kind": "done",
                            "result": {"sent": [{"channel": "telegram", "chat_id": chat, "message_id": mid}]},
                            "error": None})  # fmt: skip


class Port:
    """A judgement port answering `intake.addressed` with `pick` (None: an
    abstain; "fail": both legs fail), recording what it was asked."""

    def __init__(self, pick):
        self.pick, self.asked = pick, []

    async def judge(self, task, inputs, *, task_id, ref, dsn=None):
        self.asked.append((task.site, ref["received_id"], dict(inputs)))
        jid = ledger.new_id()
        payload = {"judgement_id": jid, "site": task.site, "ref": ref}
        if self.pick not in (None, "fail"):
            payload["picked"] = {task.questions[0].id: self.pick}
        async with await db.connect(dsn) as conn:
            kind = "judgement.failed" if self.pick == "fail" else "judgement.answered"
            await ledger.append(conn, task_id, kind, payload)
        return judgement.Judgement(
            jid, task.site, self.pick != "fail", {}, {}, (), "primary", "m", 0, (), 0, payload
        )


async def asked(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return [i for i in await intake.unaddressed(conn) if i["task_id"] == task]


async def address(dsn, port, task) -> list:
    out = []
    for item in await asked(dsn, task):
        async with await db.connect(dsn) as conn:
            out.append(await intake.address(port, conn, item, dsn))
    return out


def steers(rows, rid) -> list[dict]:
    return [
        r["payload"] for r in rows if r["type"] == "message.steered" and r["payload"]["received_id"] == rid
    ]


# -- the chain -------------------------------------------------------------------------


def test_a_reply_down_a_chain_that_reaches_valors_post_wakes_him(dsn, team):
    """Case 2's shape: a reply to someone's reply to Valor's post; a chain
    whose middle message was never recorded here; the newest of his posts
    up the chain binds. A chain that never reaches him, or reaches a post
    of the same id in another chat, stays heard."""

    async def go():
        await fresh(dsn)
        first = await said(dsn, "valor, morning", chat=QUIET_GROUP)
        s = first["task_id"]
        await valor_posts(dsn, s, "100", "the older post")
        await valor_posts(dsn, s, "200", "the newer post")
        await valor_posts(dsn, s, "300", "elsewhere", chat=OTHER)
        a = await said(dsn, "yes", chat=QUIET_GROUP, reply_to="200", thread=[{"id": "200", "text": "x"}])
        b = await said(dsn, "and also this", chat=QUIET_GROUP, reply_to=a["message_id"],
                       thread=[{"id": "200", "text": "x"}, {"id": a["message_id"], "text": "yes"}])  # fmt: skip
        gap = await said(dsn, "three up", chat=QUIET_GROUP, reply_to="999",
                         thread=[{"id": "100"}, {"id": "150"}, {"id": "200"}, {"id": "999"}])  # fmt: skip
        never = await said(
            dsn, "lunch?", chat=QUIET_GROUP, reply_to="998", thread=[{"id": "997"}, {"id": "998"}]
        )
        other = await said(
            dsn, "that one", chat=QUIET_GROUP, reply_to="301", thread=[{"id": "300"}, {"id": "301"}]
        )
        return [x["as"] for x in (a, b, gap, never, other)], await rows_of(dsn, s), (b, gap, never)

    bound, rows, (b, gap, never) = run(go())
    assert bound == ["steer", "steer", "steer", "heard", "heard"]
    assert steers(rows, b["received_id"])[0]["replying_to"]["text"] == "the newer post"
    assert steers(rows, gap["received_id"])[0]["replying_to"]["text"] == "the newer post"
    heard = [
        r["payload"]
        for r in rows
        if r["type"] == "message.heard" and r["payload"]["received_id"] == never["received_id"]
    ]
    assert heard[0]["addressed"] == intake.ASKED


def test_a_message_in_a_chat_that_takes_every_message_is_never_asked(dsn, team):
    async def go():
        m = await said(dsn, "lunch at noon?", chat=GROUP)
        return m, await asked(dsn, m["task_id"])

    m, items = run(go())
    assert m["as"] == "steer" and items == []


# -- the question --------------------------------------------------------------------


def test_the_site_is_declared_quiet_a_pick_leaning_to_none_and_calibrated_on_pick_labels(tmp_path):
    (q,) = ADDRESSED.questions
    assert BY_SITE[ADDRESSED.site] is ADDRESSED and ADDRESSED.site in ledger.QUIET_SITES
    assert q.kind is judgement.Kind.PICK and set(q.labels) == {"engaged", "aside", "react", "none"}
    assert q.proceed == set(q.labels) and ADDRESSED.floor == {"primary": 0.5, "fallback": 0.5}
    assert "none is the usual answer" in q.text and judgement.route(ADDRESSED) == ("gemini", "jev")
    assert ledger.quiet({"type": "judgement.answered", "payload": {"site": ADDRESSED.site}})
    flat = {k: 0.25 for k in q.labels}
    assert judgement.decide(q, flat, 0.5)["decision"] == "abstain"
    inputs = {k: "x" for k in ADDRESSED.inputs}
    case = {"id": "c1", "inputs": inputs, "labels": {"addressed": {"value": "engaged", "source": "tom"}}}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"site": ADDRESSED.site, "cases": [case]}))
    assert judgement_sites.check_calibration(path)[0] == ADDRESSED.site
    _, expected = judgement_sites.case_shape(ADDRESSED.site, case)
    assert expected["addressed"].action == "engaged"
    case["labels"]["addressed"]["value"] = "maybe"
    try:
        judgement_sites.case_shape(ADDRESSED.site, case)
        raise AssertionError("an unknown label is not a case")
    except ValueError:
        pass


def test_its_inputs_are_the_message_the_room_before_it_with_valors_sends_and_his_last_post(dsn, team):
    """Valor's sends into this chat only (one to another chat is not this
    room's), and the reply chain as the bridge fetched it."""

    async def go():
        await fresh(dsn)
        first = await said(dsn, "valor, when is it due?", chat=QUIET_GROUP)
        s = first["task_id"]
        await valor_posts(dsn, s, "500", "Friday, I think")
        for n in range(7):
            await said(dsn, f"chatter {n}", chat=QUIET_GROUP, name="Kim")
        await valor_posts(dsn, s, "600", "a reply in another chat", chat=OTHER)
        chain = [{"id": "498", "text": "who owns the deadline?"}, {"id": "499", "text": "  not me  "}]
        m = await said(dsn, "friday works", chat=QUIET_GROUP, name="Sam", reply_to="499", thread=chain)
        (item,) = [i for i in await asked(dsn, s) if i["payload"]["received_id"] == m["received_id"]]
        async with await db.connect(dsn) as conn:
            received = await (
                await conn.execute(
                    "SELECT payload FROM events WHERE type = 'message.received' AND payload->>'received_id' = %s",
                    (m["received_id"],),
                )
            ).fetchone()
        return intake.addressed_inputs(await rows_of(dsn, s), item, received[0])

    got = run(go())
    assert got["message"] == "Sam: friday works"
    room = got["room"].splitlines()
    assert (
        len(room) == intake.ROOM_LINES
        and room[-1] == "Kim: chatter 6"
        and room[0] == "Valor: Friday, I think"
    )
    assert "another chat" not in got["room"]
    assert got["valor_last"].startswith("Valor last posted 7 messages ago")
    assert got["thread"] == "who owns the deadline?\nnot me"


# -- what each answer does -----------------------------------------------------------


def test_engaged_steers_the_session_as_a_mention_does(dsn, team):
    async def go():
        await fresh(dsn)
        m = await said(dsn, "the deadline is friday", chat=QUIET_GROUP)
        s = m["task_id"]
        before = session.chat_waiting(await rows_of(dsn, s))
        labels = await address(dsn, Port("engaged"), s)
        rows = await rows_of(dsn, s)
        async with await db.connect(dsn) as conn:
            inputs = await reactions.inputs(
                conn, {"stage": "handling", "row": {"received_id": m["received_id"]}}
            )
        return m, before, labels, rows, await asked(dsn, s), inputs

    m, before, labels, rows, again, inputs = run(go())
    (steer,) = steers(rows, m["received_id"])
    assert not before and labels == ["engaged"] and session.chat_waiting(rows) and again == []
    assert steer["addressed"] == "engaged" and steer["judgement_id"]
    seen = [
        r["payload"]
        for r in rows
        if r["type"] == "reaction.requested" and r["payload"]["received_id"] == m["received_id"]
    ]
    assert [(p["emoji"], p["stage"]) for p in seen] == [(EYES, "seen")] and inputs["bound_as"] == "steer"
    prompt = session.chat_prompt(rows)
    assert prompt.count("the deadline is friday") == 1 and "not addressed to you" not in prompt


def test_aside_steers_it_marked_not_addressed_with_no_eyes(dsn, team):
    async def go():
        await fresh(dsn)
        m = await said(dsn, "does anyone know the staging url?", chat=QUIET_GROUP)
        s = m["task_id"]
        await address(dsn, Port("aside"), s)
        return m, await rows_of(dsn, s)

    m, rows = run(go())
    (steer,) = steers(rows, m["received_id"])
    assert steer["addressed"] == "aside" and session.chat_waiting(rows)
    assert not [
        r
        for r in rows
        if r["type"] == "reaction.requested" and r["payload"]["received_id"] == m["received_id"]
    ]
    prompt = session.chat_prompt(rows)
    assert prompt.count("does anyone know the staging url?") == 1 and "not addressed to you" in prompt


def test_react_owes_a_done_told_it_is_an_acknowledgement_and_wakes_nothing(dsn, team):
    async def go():
        await fresh(dsn)
        m = await said(dsn, "we closed the deal!", chat=QUIET_GROUP)
        s = m["task_id"]
        await address(dsn, Port("react"), s)
        rows = await rows_of(dsn, s)
        async with await db.connect(dsn) as conn:
            (item,) = [
                i for i in await reactions.pending(conn) if i["row"]["received_id"] == m["received_id"]
            ]
            inputs = await reactions.inputs(conn, item)
        return m, rows, item, inputs

    m, rows, item, inputs = run(go())
    assert not steers(rows, m["received_id"]) and not session.chat_waiting(rows)
    assert (
        item["stage"] == "done"
        and inputs["outcome"] == intake.ACKNOWLEDGED
        and inputs["replied"] == "no text sent"
    )
    assert inputs["bound_as"] == "heard" and inputs["did"].startswith("nothing")


def test_none_and_an_abstain_leave_it_heard_and_it_is_asked_once(dsn, team):
    async def go():
        out = []
        for pick in ("none", None):
            await fresh(dsn)
            m = await said(dsn, "see you at lunch", chat=QUIET_GROUP)
            s = m["task_id"]
            labels = await address(dsn, Port(pick), s)
            out.append((labels, await rows_of(dsn, s), await asked(dsn, s), m))
        return out

    for labels, rows, again, m in run(go()):
        assert labels == ["none"] and not steers(rows, m["received_id"]) and again == []
        assert not session.chat_waiting(rows)


def test_a_failed_ask_is_asked_again_until_the_unanswered_runs_then_never(dsn, team):
    async def go():
        await fresh(dsn)
        m = await said(dsn, "anyone around?", chat=QUIET_GROUP)
        s, port = m["task_id"], Port("fail")
        runs = [await address(dsn, port, s) for _ in range(judgement.UNANSWERED_RUNS + 1)]
        return runs, await rows_of(dsn, s), m

    runs, rows, m = run(go())
    assert runs == [[None]] * judgement.UNANSWERED_RUNS + [[]]
    assert not steers(rows, m["received_id"])


def test_a_message_the_session_already_read_is_not_steered_again(dsn, team):
    """Read by a finished turn's prompt, by a running turn's prompt, or
    taken mid-turn with another message's steer: no second wake."""

    async def go():
        out = []
        # A finished turn read it.
        await fresh(dsn)
        m = await said(dsn, "first, unaddressed", chat=QUIET_GROUP)
        await turn(dsn, m["task_id"])
        out.append((await address(dsn, Port("engaged"), m["task_id"]), m))
        # A running turn took it mid-turn with a mention's steer.
        await fresh(dsn)
        opener = await said(dsn, "valor, start", chat=QUIET_GROUP)
        s, turn_id = opener["task_id"], ledger.new_id()
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, s, "turn.started", {"turn_id": turn_id, "state": "clarify"})
        m = await said(dsn, "second, unaddressed", chat=QUIET_GROUP)
        y = await said(dsn, "valor, and this", chat=QUIET_GROUP)
        (y_row,) = [
            r
            for r in await rows_of(dsn, s)
            if r["type"] == "message.steered" and r["payload"]["received_id"] == y["received_id"]
        ]
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, s, "message.delivered", {"steered_id": y_row["id"], "turn_id": turn_id})
        out.append((await address(dsn, Port("engaged"), s), m))
        return out, [await rows_of(dsn, x["task_id"]) for _, x in out]

    out, rows = run(go())
    for (labels, m), r in zip(out, rows, strict=True):
        assert labels == ["engaged"] and not steers(r, m["received_id"])


def test_a_heard_row_from_before_the_mark_is_never_asked(dsn, team):
    async def go():
        await fresh(dsn)
        m = await said(dsn, "an old message", chat=QUIET_GROUP)
        s = m["task_id"]
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, s, "message.heard", {"received_id": "old-1", "text": "before the rollout"}
            )
        return [i["payload"]["received_id"] for i in await asked(dsn, s)], m

    rids, m = run(go())
    assert rids == [m["received_id"]]


def test_a_running_turn_reads_a_message_judged_meant_for_valor_once(dsn, team):
    """Heard after the turn's prompt, then steered: `mid_turn` gives it
    once, at its steer, marked when aside."""

    async def go():
        await fresh(dsn)
        opener = await said(dsn, "valor, start", chat=QUIET_GROUP)
        s = opener["task_id"]
        upto = (await rows_of(dsn, s))[-1]["id"]
        await said(dsn, "is the demo still on?", chat=QUIET_GROUP)
        await address(dsn, Port("aside"), s)
        return session.mid_turn(await rows_of(dsn, s), upto, True)

    owed = run(go())
    text = "\n".join(t for _, t in owed)
    assert len(owed) == 1 and text.count("is the demo still on?") == 1 and "not addressed to you" in text


def test_the_resident_kernel_asks_each_heard_message_in_a_job_of_its_own(fresh, team):
    """On a database of its own, beside the reaction jobs."""
    from core import serve

    dsn = fresh

    async def go():
        m = await said(dsn, "the build is green now", chat=QUIET_GROUP)
        port = Port("engaged")
        kernel = serve.Kernel(None, {}, None, dsn, judgement_port=port)
        async with await db.connect(dsn) as conn:
            await kernel.react(conn)
        await asyncio.gather(*kernel.reacting.values())
        return m, port.asked, await rows_of(dsn, m["task_id"])

    m, asked_, rows = run(go())
    assert [(site, rid) for site, rid, _ in asked_] == [(ADDRESSED.site, m["received_id"])]
    assert steers(rows, m["received_id"])[0]["addressed"] == "engaged"
