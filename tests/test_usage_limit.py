"""A model usage limit (plan docs/plans/3a022d3c-usage-limit.md).

The incident: 2026-11-20 two sessions of Tom's DM each failed four turns
with "You've hit your Fable limit · resets 2pm (Asia/Bangkok)…" and ended
with nothing sent; he heard nothing for 22 hours. Now a limit is no try: the
work waits for the reset and runs again, and a chat with a person's message
waiting hears one notice per limit window."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core import broker, db, ledger, limit, machine, memory, notices, router, session, tasks
from core.bridge import bound_performers
from core.machine import State
from tests import bridges
from tests.bridges import FakeBridge, new_task, typed

LIMIT = "You've hit your Fable limit · resets 2pm (Asia/Bangkok). Switch to another model to continue."
DATED = (
    "You've hit your Fable limit · resets Nov 21 at 2pm (Asia/Bangkok). Switch to another model to continue."
)
BKK = ZoneInfo("Asia/Bangkok")


def run(coro):
    return asyncio.run(coro)


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


# --- the reset, read from the harness's words ---


def test_the_incidents_limit_resets_at_2pm_bangkok_the_next_day():
    # Session 2a14f74c4cd6 failed at 14:24 UTC (21:24 Bangkok): 2pm there had passed.
    assert limit.until(f"the turn ended failed: {LIMIT}", utc(2026, 11, 20, 14, 24)) == utc(
        2026, 11, 21, 7, 0
    )
    assert limit.until(DATED, utc(2026, 11, 20, 14, 24)) == utc(2026, 11, 21, 7, 0)
    assert limit.until(LIMIT, utc(2026, 11, 20, 14, 24)).tzinfo.key == "Asia/Bangkok"


def test_a_retry_just_after_the_reset_reads_the_same_reset():
    assert limit.until(LIMIT, utc(2026, 11, 21, 7, 10)) == utc(2026, 11, 21, 7, 0)
    assert limit.until(LIMIT, utc(2026, 11, 21, 8, 30)) == utc(2026, 11, 22, 7, 0)


def test_minutes_another_zone_a_new_year_date_and_other_failures():
    assert limit.until("You've hit your limit · resets 2:30pm (Europe/London)", utc(2026, 11, 21, 9)) == utc(
        2026, 11, 21, 14, 30
    )
    assert limit.until("hit your Opus limit · resets 12am (America/New_York)", utc(2026, 11, 21, 9)) == utc(
        2026, 11, 22, 5, 0
    )
    assert limit.until("hit your limit · resets Jan 1 at 9am (Asia/Bangkok)", utc(2026, 12, 31, 20)) == utc(
        2027, 1, 1, 2, 0
    )
    for other in (
        "the turn ended failed: API Error: 500",
        "resets 2pm (Nowhere/Land) hit your limit",
        None,
        "",
    ):
        assert limit.until(other, utc(2026, 11, 21)) is None


def test_the_reset_as_a_reader_says_it():
    reset = utc(2026, 11, 21, 7, 0)
    assert limit.when(reset, BKK, utc(2026, 11, 21, 1)) == "2pm Bangkok time"
    assert limit.when(reset, BKK, utc(2026, 11, 20, 14, 24)) == "2pm Bangkok time tomorrow"
    assert limit.when(reset, BKK, utc(2026, 11, 19, 14)) == "2pm Bangkok time on Nov 21"
    assert limit.when(utc(2026, 11, 21, 14, 30), ZoneInfo("Europe/London"), reset) == "2:30pm London time"


# --- tries and retries, over rows ---


def failed(i, at, reset=None):
    p = {"state": "clarify", "check": None, "reason": "x"}
    if reset is not None:
        p |= {"until": reset.isoformat(), "zone": "Asia/Bangkok"}
    return {"id": i, "type": "step.failed", "payload": p, "at": at}


def test_limit_failures_are_no_tries_and_wait_for_the_reset_then_space_out():
    at, reset = utc(2026, 11, 20, 14, 24), utc(2026, 11, 21, 7, 0)
    rows = [{"id": 0, "type": "task.started", "payload": {}, "at": at}]
    rows += [failed(i, at + timedelta(minutes=2 * i), reset) for i in range(1, 7)]
    assert router.failures(rows) == 0
    last = rows[-1]["at"]
    assert not router.retry_due(rows, now=last + timedelta(hours=5))
    assert router.retry_due(rows, now=reset)
    # The limit outlasts its reset: 60, 120, 240 s, not every minute.
    t = reset
    for gap in (60, 120, 240):
        t += timedelta(seconds=1)
        rows.append(failed(len(rows), t, reset))
        assert not router.retry_due(rows, now=t + timedelta(seconds=gap - 1))
        assert router.retry_due(rows, now=t + timedelta(seconds=gap))
        t += timedelta(seconds=gap)


def test_a_limit_ends_the_run_of_ordinary_failures():
    at, reset = utc(2026, 11, 20, 1), utc(2026, 11, 20, 7)
    rows = [failed(i, at, None) for i in range(1, 4)]
    rows.append(failed(4, at, reset))
    rows.append(failed(5, reset + timedelta(minutes=1), None))
    assert router.failures(rows) == 1
    assert router.failures(rows[:3]) == 3


# --- against the ledger ---


def limited(text=LIMIT):
    async def runner(ctx):
        return {"status": "failed", "turn": {"outcome": "failed", "result": {"text": text, "is_error": True}}}

    return runner


async def step(dsn, task, runner):
    runners = {s: runner for s in State}
    return await router.step(None, task, runners, dsn, lambda b: bridges.declared())


async def chat_task(dsn, channel="slack", chat_id="T1/D1", **kw):
    return await new_task(dsn, chat={"channel": channel, "chat_id": chat_id}, **kw)


async def says(dsn, task, text, mid="1700000000.000100", by="tom", name="Tom", **more):
    async with await db.connect(dsn) as conn:
        await ledger.append(
            conn, task, "message.steered",
            {"message_id": mid, "received_id": ledger.new_id(), "text": text, "sender_name": name,
             "provenance": {"by": by}, **more},
        )  # fmt: skip


def held(written):
    return [p for p in typed(written, "effect.held") if str(p.get("request_id")).startswith("limit/")]


def test_the_incident_a_chat_session_on_its_limit_waits_and_tells_tom_once(dsn, tmp_path):
    with bridges.operator(tmp_path):
        task = run(chat_task(dsn))
        run(says(dsn, task, "Are your systems down? Run out of subscription credit?"))
        for _ in range(4):
            run(step(dsn, task, limited()))
        run(says(dsn, task, "hello?", mid="1700000000.000200"))
        for _ in range(2):
            run(step(dsn, task, limited()))
        written = run(bridges.rows(dsn, task))
    assert not typed(written, "task.ended") and len(typed(written, "step.failed")) == 6
    (notice,) = held(written)
    assert notice["action_type"] == "slack.send_message" and notice["target"] == "T1/D1"
    assert notice["payload"]["reply_to"] == "1700000000.000100"  # sent at the first failure
    assert notice["payload"]["text"].startswith("I'm offline until 2pm Bangkok time")
    assert notice["payload"]["text"].endswith(": I've hit my model usage limit. I'll pick this up then.")
    assert notice["quiet"] is True and all(p.get("quiet") for p in typed(written, "release.requested"))
    # Asleep until the reset; then due, its prompt holding the message and the notice.
    reset = datetime.fromisoformat(typed(written, "step.failed")[-1]["until"])
    assert not router.retry_due(written, now=reset - timedelta(minutes=1))
    assert router.retry_due(written, now=reset + timedelta(seconds=1))
    assert session.chat_waiting(written)
    prompt = limit.sent(written)
    assert notice["payload"]["text"] in prompt and "without sending that notice again" in prompt


def test_the_notices_rows_through_its_bridges_release_keep_the_session_asleep(dsn, tmp_path):
    bridge = FakeBridge("slack")

    async def go(fail):
        task = await chat_task(dsn)
        await says(dsn, task, "a question")
        await step(dsn, task, limited())
        async with await db.connect(dsn) as conn:
            (notice,) = held(await ledger.read(conn, task))
            mine = bound_performers(bridge, conn)
            if fail:
                mine = broker.Performers()  # no performer at release: refused
            try:
                await broker.release(conn, mine, notice["effect_id"])
            except broker.Refused:
                pass
            return await ledger.read(conn, task)

    with bridges.operator(tmp_path):
        for fail in (False, True):
            written = run(go(fail))
            kinds = [r["type"] for r in written if r["type"].startswith(("effect.", "release."))]
            assert ("effect.refused" if fail else "effect.outcome") in kinds
            assert all(ledger.quiet(r) for r in written if r["type"].startswith(("effect.", "release.")))
            assert tasks.failed_step(written) is not None
            last = next(r for r in reversed(written) if not ledger.quiet(r))
            assert last["type"] == "step.failed"
            assert not router.retry_due(written, now=last["at"] + timedelta(minutes=30))


def test_a_later_limit_window_sends_again(dsn, tmp_path):
    with bridges.operator(tmp_path):
        task = run(chat_task(dsn))
        run(says(dsn, task, "one"))
        run(step(dsn, task, limited()))
        run(step(dsn, task, limited("You've hit your Fable limit · resets Dec 3 at 9am (Asia/Bangkok).")))
        written = run(bridges.rows(dsn, task))
    assert len(held(written)) == 2


def test_no_notice_for_a_session_holding_only_a_task_report_or_for_background_work(dsn, tmp_path):
    with bridges.operator(tmp_path):
        chat = run(chat_task(dsn))
        child = run(new_task(dsn, parent_id=chat))

        async def report():
            async with await db.connect(dsn) as conn:
                await ledger.append(
                    conn, chat, "child.reported",
                    {"from": child, "kind": "question", "about_key": "question:q", "child_id": child, "text": "?"},
                )  # fmt: skip

        run(report())
        run(step(dsn, chat, limited()))
        run(step(dsn, child, limited()))
        chat_rows, child_rows = run(bridges.rows(dsn, chat)), run(bridges.rows(dsn, child))
    assert not held(chat_rows) and not held(child_rows)
    assert typed(child_rows, "step.failed")[0]["until"] and not typed(child_rows, "task.ended")
    assert session.chat_waiting(chat_rows)


def test_a_session_holding_only_a_handed_on_message_tells_its_sender(dsn, tmp_path):
    """The incident's second session, 2a14f74c4cd6: it held Tom's message
    only as the first session's `ended` report."""

    async def go():
        first = await chat_task(dsn, "telegram", "179")
        await says(dsn, first, "Are your systems down?", mid="12004")
        second = await chat_task(dsn, "telegram", "179")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, second, "child.reported",
                                {"from": first, "kind": "ended", "about_key": "ended:1", "child_id": first,
                                 "text": "unanswered"})  # fmt: skip
        await step(dsn, second, limited())
        return await bridges.rows(dsn, second)

    with bridges.operator(tmp_path):
        (notice,) = held(run(go()))
    assert notice["action_type"] == "telegram.send_message" and notice["target"] == "179"
    assert notice["payload"]["reply_to"] == "12004"


def test_an_ordinary_failure_still_ends_at_four_tries(dsn, tmp_path):
    with bridges.operator(tmp_path):
        task = run(chat_task(dsn))
        run(says(dsn, task, "hi"))
        for _ in range(3):
            run(step(dsn, task, limited("API Error: 500")))
        run(step(dsn, task, limited()))
        run(step(dsn, task, limited("API Error: 500")))
        mixed = run(bridges.rows(dsn, task))
        for _ in range(3):
            run(step(dsn, task, limited("API Error: 500")))
        written = run(bridges.rows(dsn, task))
    assert not typed(mixed, "task.ended")
    (ended,) = typed(written, "task.ended")
    assert ended["tries"] == 4 and "until" not in typed(written, "step.failed")[0]
    assert len(held(written)) == 1  # the one limit failure's


def test_who_is_told_the_reason(dsn, tmp_path, monkeypatch):
    async def people(conn, spec, about):
        team = "internal" if about == "Sam Teammate" else "external"
        return {"people": [{"name": about, "aliases": [], "team": team}]}

    async def told(name, by, project=True):
        task = await chat_task(dsn, chat_id=f"T1/{ledger.new_id().upper()}",
                               project={"name": "valor"} if project else None)  # fmt: skip
        await says(dsn, task, "hey", by=by, name=name)
        await step(dsn, task, limited())
        (notice,) = held(await bridges.rows(dsn, task))
        return notice["payload"]["text"]

    with bridges.operator(tmp_path):
        (tmp_path / "projects" / "valor.toml").write_text(bridges.VALOR_SPEC)
        assert "usage limit" in run(told("Tom", "tom"))
        outside = run(told("Pat Client", "Pat Client"))
        assert outside.startswith("I'm offline until 2pm Bangkok time") and "usage limit" not in outside
        assert outside.endswith(" and will pick this up then.")
        monkeypatch.setattr(memory, "people", people)
        assert "usage limit" in run(told("Sam Teammate", "Sam Teammate"))
        assert "usage limit" not in run(told("Pat Client", "Pat Client"))

        async def down(conn, spec, about):
            raise memory.Unavailable("off")

        monkeypatch.setattr(memory, "people", down)
        assert "usage limit" not in run(told("Sam Teammate", "Sam Teammate"))


async def received_already_bound(conn, channel, payload):
    """A received message, bound, so no test's binder picks it up."""
    await ledger.append(conn, channel, "message.received", payload)
    await ledger.append(
        conn, channel, "message.bound", {"received_id": payload["received_id"], "as": "heard"}
    )


@pytest.mark.parametrize(
    ("channel", "chat_id", "kind", "fields"),
    [
        ("telegram", "179", "telegram.send_message", {"reply_to": "12004", "topic_id": "7"}),
        ("slack", "T1/C1", "slack.send_message", {"reply_to": "7"}),
        ("local", "local", "local.send_message", {}),
    ],
)
def test_each_chat_kind_sends_through_its_own_send(dsn, channel, chat_id, kind, fields):
    async def go():
        received = ledger.new_id()
        async with await db.connect(dsn) as conn:
            await received_already_bound(
                conn, channel, {"received_id": received, "message_id": "12004", "topic_id": "7"}
            )
            return await limit._action(
                conn,
                {"channel": channel, "chat_id": chat_id},
                {"message_id": "12004", "received_id": received},
                "x",
            )

    action = run(go())
    assert action.action_type == kind and action.payload == {"text": "x", **fields}


def test_an_email_chat_is_answered_in_its_thread(dsn, tmp_path):
    async def go():
        r = ledger.new_id()
        async with await db.connect(dsn) as conn:
            await received_already_bound(conn, "email", {
                "received_id": r, "channel": "email", "chat_id": f"<root-{r}@client.test>",
                "message_id": f"<{r}@client.test>", "sender_id": "pat@client.test",
                "headers": {"subject": "Invoice", "to": ["valor@valor.test"]},
            })  # fmt: skip
            return await limit._action(conn, {"channel": "email", "chat_id": "pat@client.test"},
                                       {"received_id": r}, "x")  # fmt: skip

    with bridges.configure(email_address="valor@valor.test"):
        action = run(go())
    assert action.action_type == "email.send" and action.payload["to"] == ["pat@client.test"]
    assert action.payload["in_reply_to"].endswith("@client.test>") and action.payload["body"] == "x"
    assert action.payload["subject"].lower().startswith("re:")


def test_a_failed_notice_send_leaves_the_limit_wait_as_it_is(dsn, tmp_path, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("the connection dropped")

    monkeypatch.setattr(broker, "request", boom)
    with bridges.operator(tmp_path):
        task = run(chat_task(dsn))
        run(says(dsn, task, "hi"))
        run(step(dsn, task, limited()))
        written = run(bridges.rows(dsn, task))
    assert typed(written, "step.failed")[0]["until"] and not held(written)


def test_an_unanswered_message_survives_any_number_of_ended_sessions(dsn, tmp_path):
    """Task 709b0d649ddb (2026-11-19): the second session to end on a
    message handed nothing on, so the client's DMs were held by no one."""
    ended = {"by": "kernel", "kind": "step", "check": None, "state": "clarify", "tries": 4, "reason": "boom"}

    async def go():
        first = await chat_task(dsn, "telegram", bridges.OPERATOR)
        await says(dsn, first, "Can this be used?", mid="900")
        chain = [first]
        async with await db.connect(dsn) as conn:
            for _ in range(3):
                await ledger.append(conn, chain[-1], "task.ended", ended)
                await notices.owe(conn, chain[-1])
                found = await (
                    await conn.execute(
                        "SELECT task_id, payload FROM events WHERE type = 'child.reported' AND payload->>'from' = %s",
                        (chain[-1],),
                    )
                ).fetchone()
                assert found is not None and found[0] != chain[-1]
                chain.append(found[0])
                text = found[1]["text"]
            last = chain[-1]
            said = await notices.unanswered(conn, last, await ledger.read(conn, last))
            state = machine.fold(await ledger.read(conn, last)).state
        return text, said, state

    with bridges.operator(tmp_path):
        (tmp_path / "projects" / "valor.toml").write_text(bridges.VALOR_SPEC)
        text, said, state = run(go())
    assert said["message_id"] == "900" and state is not State.ENDED
    assert 'message 900 ("Can this be used?")' in text
