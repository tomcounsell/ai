"""A chat session wakes itself (docs/plans/chat-wake.md): a turn's `wake.md`
asks to be woken after a delay, watching a post; only a reply to that post or
a message in its thread drops it, a finished turn whose prompt gave it spends
it, and the prompt says how long it waited in Tom's time."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core import db, ledger, session, signals, tasks
from core.machine import State
from tests import bridges, scripted
from tests.test_compaction import say, turns, wake

pytestmark = pytest.mark.spend(usd=0)

T0 = datetime(2026, 10, 29, 6, 40, tzinfo=UTC)
CHAT = tasks.Brief(instruction="x", chat={"channel": "slack", "chat_id": "T1/C1"})


def rows_of(*specs) -> list[dict]:
    return [{"id": i + 1, "type": t, "payload": p, "at": at} for i, (t, p, at) in enumerate(specs)]


def collected(tid, wake=None, effects=(), woke=(), verdict="idle", at=T0):
    return "turn.collected", {"turn_id": tid, "verdict": verdict, "wake": wake, "effects": list(effects),
                              "woke": list(woke)}, at  # fmt: skip


def asked(post=None, after_s=300, note=None) -> dict:
    return {"after_s": after_s, "post": post, "note": note}


def sent(eid, *parts, kind="done", error=None):
    return (
        "effect.outcome",
        {"effect_id": eid, "kind": kind, "result": {"sent": list(parts)}, "error": error},
        T0,
    )


def msg(kind, reply_to=None, text="hi", at=T0):
    return kind, {"message_id": "9", "reply_to": reply_to, "text": text, "sender_name": "Tom",
                  "sent_at": at.isoformat()}, at  # fmt: skip


ASK = {"file": "ask.json", "effect_id": "e1", "kind": "released", "error": None}


def test_a_delay_is_seconds_minutes_or_hours_and_names_its_post():
    assert session._wake("90s") == ({"after_s": 90, "post": None, "note": None}, None)
    assert session._wake("20m")[0]["after_s"] == 1200 and session._wake("25")[0]["after_s"] == 1500
    assert session._wake("2h post: ask.json\nThe logo question.\n") == (
        {"after_s": 7200, "post": "ask.json", "note": "The logo question."}, None)  # fmt: skip
    for bad in ("soon", "(empty wake.md)", "20 minutes", "5m ask.json"):
        assert session._wake(bad)[0] is None


def test_wake_md_counts_only_from_a_chat_turn_that_finished():
    found = signals.Signals(wake="5m")
    _, extra, errors = session._stage_verdict(State.BUILD, found, None, "t", True)
    assert "wake" not in extra and errors == ["wake means nothing outside a chat session; ignored"]
    _, extra, errors = session._stage_verdict(State.CLARIFY, found, None, "t", False, CHAT)
    assert "wake" not in extra and errors == ["wake.md from a turn that did not finish; not set"]
    _, extra, errors = session._stage_verdict(
        State.CLARIFY, signals.Signals(wake="later"), None, "t", True, CHAT
    )
    assert "wake" not in extra and "not a delay" in errors[0]
    verdict, extra, errors = session._stage_verdict(State.CLARIFY, found, None, "t", True, CHAT)
    assert verdict == "idle" and extra["wake"] == asked() and errors == []


def test_a_wake_falls_due_at_its_delay_and_a_finished_turn_it_woke_spends_it():
    base = [collected("t1", asked(after_s=300))]
    rows = rows_of(*base)
    assert not session.wake_due(rows, T0 + timedelta(seconds=299)) and not session.owed(
        rows, T0 + timedelta(seconds=299)
    )
    assert session.wake_due(rows, T0 + timedelta(seconds=300))
    # A turn started at the due time: no longer due (a failure retries on backoff) but still owed.
    later = T0 + timedelta(seconds=301)
    started = ("turn.started", {"turn_id": "t2"}, later)
    rows = rows_of(*base, started)
    assert not session.wake_due(rows, later) and session.owed(rows, later)
    assert session.owed(rows_of(*base, started, collected("t2", woke=["t1"], verdict="failed")), later)
    assert session.wakes(rows_of(*base, started, collected("t2", woke=["t1"]))) == []
    # A compaction turn is not a turn that took it.
    assert session.wake_due(
        rows_of(*base, ("turn.started", {"turn_id": "c", "compaction": True}, later)), later
    )


def test_only_a_reply_to_its_post_or_its_thread_drops_a_wake():
    on_post = [collected("t1", asked("ask.json"), [ASK]), sent("e1", {"message_id": "100.1"})]
    elsewhere = rows_of(*on_post, msg("message.steered", "555.5"), msg("message.heard"),
                        ("child.reported", {"child_id": "c", "text": "done"}, T0))  # fmt: skip
    (w,) = session.wakes(elsewhere)
    assert w["status"] == "sent" and w["ids"] == {"100.1"}
    # Tom's reaction on the post arrives as a reply to it.
    reaction = ("message.steered", {"kind": "reaction", "text": ":+1:", "reply_to": "100.1"}, T0)
    assert session.wakes(rows_of(*on_post, reaction)) == []
    assert session.wakes(rows_of(*on_post, msg("message.heard", "100.1"))) == []
    # A post inside a thread: a message in that thread drops it.
    threaded = [
        collected("t1", asked("ask.json"), [ASK]),
        sent("e1", {"message_id": "100.1", "thread_ts": "50.0"}),
    ]
    assert session.wakes(rows_of(*threaded, msg("message.steered", "50.0"))) == []
    # A wake with no post: nothing drops it.
    assert len(session.wakes(rows_of(collected("t1", asked()), msg("message.steered", "100.1")))) == 1


def test_a_wake_on_the_same_post_replaces_it_and_others_are_kept():
    first = [
        collected("t1", asked("ask.json"), [ASK]),
        sent("e1", {"message_id": "100.1", "thread_ts": "50.0"}),
    ]
    other = {"file": "other.json", "effect_id": "e2", "kind": "released", "error": None}
    second = [collected("t2", asked("other.json", note="b"), [other]), sent("e2", {"message_id": "200.2"})]
    assert [w["key"] for w in session.wakes(rows_of(*first, *second))] == ["100.1", "200.2"]
    # A re-judge names the earlier post by id: it replaces that wake, keeps its thread,
    # and stays pending after the turn that set it spends the first.
    rejudge = collected("t3", asked("100.1", after_s=3600, note="still waiting"), woke=["t1"])
    got = session.wakes(rows_of(*first, *second, rejudge))
    assert [(w["key"], w["turn_id"]) for w in got] == [("200.2", "t2"), ("100.1", "t3")]
    assert got[1]["ids"] == {"100.1", "50.0"}
    assert (
        session.wakes(rows_of(*first, *second, rejudge, msg("message.steered", "50.0")))[-1]["key"] == "200.2"
    )
    # A wake with no post replaces the one with no post.
    plain = session.wakes(
        rows_of(collected("a", asked(note="task x")), collected("b", asked(note="tasks x, y")))
    )
    assert [w["note"] for w in plain] == ["tasks x, y"]


def test_a_post_that_did_not_go_out_still_wakes_and_says_so():
    refused = {"file": "ask.json", "effect_id": "e1", "kind": "refused", "error": "not allowed here"}
    (w,) = session.wakes(rows_of(collected("t1", asked("ask.json"), [refused])))
    assert (w["status"], w["why"]) == ("gone", "not allowed here")
    (w,) = session.wakes(rows_of(collected("t1", asked("ask.json"), [ASK])))
    assert w["status"] == "held"
    (w,) = session.wakes(
        rows_of(collected("t1", asked("ask.json"), [ASK]), sent("e1", kind="failed", error="429"))
    )
    assert (w["status"], w["why"]) == ("gone", "429")
    (w,) = session.wakes(rows_of(collected("t1", asked("nope.json"), [ASK])))
    assert w["status"] == "gone" and "no nope.json" in w["why"]
    report = session._wake_report([w])
    assert "it never went out (the turn requested no nope.json)" in report


def test_the_wake_prompt_gives_the_delay_toms_time_and_the_note():
    rows = rows_of(collected("t1", asked("ask.json", note="The logo question; default: blue."), [ASK]),
                   sent("e1", {"message_id": "100.1"}))  # fmt: skip
    report = session._wake_report(session.owed(rows, T0 + timedelta(minutes=5)))
    assert report.startswith("# Your wake-up\n\n- You asked to be woken 5 minutes after your turn that ended at "
                             "13:40 Tom's time (06:40 UTC), watching your post 100.1: nothing has replied")  # fmt: skip
    assert (
        "Your note: The logo question; default: blue.\n\nBefore you choose, run `valor-record waits`"
        in report
    )
    assert report.endswith('"Nothing stalls on one person" says, and say in the chat what you did.')
    assert session._delay(90) == "90 seconds" and session._delay(3600) == "1 hour"
    assert session._wake_report([]) == ""


def test_a_chat_session_sets_a_wake_is_woken_by_it_and_spends_it(dsn, tmp_path):
    """Through `session.run`: the post's ids come from the collected effect's
    `effect_id`, as the bridge's `effect.outcome` names it."""
    ws, _ = scripted.workspace(tmp_path)
    send = {"action_type": "telegram.send_message", "target": "1", "payload": {"text": "Blue or green?"}}
    scripted.steer(ws, chat=[{"wake.md": "1s post: ask.json\nThe logo colour.", "ask": send}, {}])

    async def go():
        chat = {"channel": "telegram", "chat_id": str(uuid.uuid4().int % 10**9)}
        task = await scripted.start(dsn, ws, chat=chat)
        await say(dsn, task, "valor, make us a logo", 1)
        first = await wake(dsn, task)
        rows = await bridges.rows(dsn, task)
        (entry,) = [e for r in rows if r["type"] == "turn.collected" for e in r["payload"]["effects"]]
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "effect.outcome", {"effect_id": entry["effect_id"], "kind": "done",
                                "result": {"sent": [{"message_id": "77"}]}, "error": None})  # fmt: skip
            await ledger.append(conn, task, "message.heard", {"message_id": "78", "sender_name": "Ann",
                                "sent_at": "2026-10-29T06:40:30+00:00", "text": "lunch?"})  # fmt: skip
        await asyncio.sleep(1.1)
        due = session.wake_due(await bridges.rows(dsn, task))
        second = await wake(dsn, task)
        return first, due, second, await bridges.rows(dsn, task)

    first, due, second, rows = asyncio.run(go())
    assert first["status"] == second["status"] == "listening" and due
    woken = turns(ws)[1]["prompt"]
    assert (
        "# Your wake-up" in woken
        and "watching your post 77" in woken
        and "Your note: The logo colour." in woken
    )
    assert "lunch?" in woken
    (one, two) = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    assert two["woke"] == [one["turn_id"]] and session.wakes(rows) == [] and not session.wake_due(rows)


# A wake on a send's outcome (`outcome post: <file>`): due when the send lands.

MAIL = {"file": "mail.json", "effect_id": "e1", "kind": "released", "error": None}


def outcome(post="mail.json", note=None) -> dict:
    return {"after_s": None, "post": post, "note": note}


def test_outcome_post_is_a_wake_on_this_turns_effect_file_only():
    assert session._wake("outcome post: mail.json\nNote back to #valor-dev.") == (
        {"after_s": None, "post": "mail.json", "note": "Note back to #valor-dev."}, None)  # fmt: skip
    for bad in ("outcome", "outcome post: 100.1"):
        wake, why = session._wake(bad)
        assert wake is None and "`outcome post: <this turn's effect file>`; not set" in why


def test_an_outcome_wake_is_due_when_its_send_lands_and_not_before():
    base = [collected("t1", outcome(), [MAIL])]
    (w,) = session.wakes(rows_of(*base))
    assert w["status"] == "held" and w["due"] is None
    far = T0 + timedelta(days=30)
    assert not session.owed(rows_of(*base), far) and not session.wake_due(rows_of(*base), far)
    landed = T0 + timedelta(minutes=1)
    rows = rows_of(*base, (*sent("e1", {"message_id": "<m1@x>"})[:2], landed))
    (w,) = session.wakes(rows)
    assert (w["status"], w["due"]) == ("sent", landed)
    assert not session.wake_due(rows, T0) and session.wake_due(rows, landed)
    # A finished turn whose prompt gave it spends it.
    assert session.wakes([*rows, {"id": 9, "type": "turn.collected", "at": landed,
                                  "payload": {"turn_id": "t2", "verdict": "idle", "woke": ["t1"]}}]) == []  # fmt: skip


def test_an_outcome_before_the_turn_was_collected_makes_it_due_at_once():
    """An inline performer writes the outcome while the turn is collected."""
    rows = rows_of(sent("e1", {"message_id": "<m1@x>"}), collected("t1", outcome(), [MAIL]))
    (w,) = session.wakes(rows)
    assert (w["status"], w["due"]) == ("sent", T0) and session.wake_due(rows, T0)


def test_a_send_that_fails_is_refused_or_never_went_wakes_at_once_and_says_so():
    failed = rows_of(collected("t1", outcome(), [MAIL]), sent("e1", kind="failed", error="535 auth"))
    refused = rows_of(collected("t1", outcome(), [MAIL]), ("effect.refused", {"effect_id": "e1",
                      "reason": "over the ceiling"}, T0))  # fmt: skip
    declined = rows_of(
        collected("t1", outcome(), [{"file": "mail.json", "error": "no such email to reply to"}])
    )
    missing = rows_of(collected("t1", outcome("nope.json"), [MAIL]))
    for rows, why in ((failed, "535 auth"), (refused, "over the ceiling"),
                      (declined, "no such email to reply to"), (missing, "the turn requested no nope.json")):  # fmt: skip
        (w,) = session.wakes(rows)
        assert (w["status"], w["why"], w["due"]) == ("gone", why, T0) and session.wake_due(rows, T0)
        assert f"it never went out ({why})" in session._wake_report(session.owed(rows, T0))


def test_a_done_outcome_that_sent_no_message_is_reported_done():
    rows = rows_of(collected("t1", outcome("make.json"), [{**MAIL, "file": "make.json"}]),
                   ("effect.outcome", {"effect_id": "e1", "kind": "done", "result": {}}, T0))  # fmt: skip
    (w,) = session.wakes(rows)
    assert w["status"] == "done" and "watching your post (make.json): it is done." in session._wake_report(
        [w]
    )


def test_a_reply_to_the_send_before_its_outcome_drops_the_wake():
    rows = rows_of(collected("t1", outcome(), [MAIL]), sent("e1", {"message_id": "<m1@x>"}),
                   msg("message.heard", "<m1@x>"))  # fmt: skip
    assert session.wakes(rows) == []


def test_a_send_with_no_wake_adds_no_turn_when_it_lands():
    rows = rows_of(collected("t1", None, [MAIL]), sent("e1", {"message_id": "<m1@x>"}))
    assert session.wakes(rows) == [] and not session.wake_due(rows, T0 + timedelta(days=1))


def test_the_24_nov_mastery_case_the_note_back_follows_the_test_mail(dsn, tmp_path):
    """2026-11-24, the Captain's #mastery-dev session: handed a message from
    the #valor-dev session asking for a test mail and a note back, its turn
    requested the mail and ended owing the note. Its outcome now steps it,
    with nothing else arriving, and the prompt says the mail went out."""
    ws, _ = scripted.workspace(tmp_path)
    mail = {"action_type": "email.send", "target": "valor@example.com",
            "payload": {"to": ["valor@example.com"], "subject": "Test: mailbox send", "body": "test"}}  # fmt: skip
    note = "Note back to #valor-dev (hand-on 5d9f14be6623): did the test mail go?"
    scripted.steer(ws, chat=[{"wake.md": f"outcome post: mail.json\n{note}", "mail": mail}, {}])

    async def go():
        chat = {"channel": "slack", "chat_id": f"T1/C{uuid.uuid4().int % 10**9}"}
        task = await scripted.start(dsn, ws, chat=chat)
        await say(dsn, task, "From #valor-dev: send a test mail and tell me when it has gone.", 1)
        first = await wake(dsn, task)
        rows = await bridges.rows(dsn, task)
        (entry,) = [e for r in rows if r["type"] == "turn.collected" for e in r["payload"]["effects"]]
        before = session.wake_due(rows, datetime.now(UTC) + timedelta(days=1))
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "effect.outcome", {"effect_id": entry["effect_id"], "kind": "done",
                                "result": {"sent": [{"channel": "email", "message_id": "<m1@x>"}]},
                                "error": None})  # fmt: skip
        due = session.wake_due(await bridges.rows(dsn, task))
        second = await wake(dsn, task)
        return entry, first, before, due, second, await bridges.rows(dsn, task)

    entry, first, before, due, second, rows = asyncio.run(go())
    assert entry["error"] is None and first["status"] == second["status"] == "listening"
    assert not before and due
    woken = turns(ws)[1]["prompt"]
    assert (
        "# Your wake-up" in woken and "once mail.json, from your turn" in woken and "had its outcome" in woken
    )
    assert "watching your post <m1@x>" in woken and f"Your note: {note}" in woken
    assert not session.wakes(rows) and not session.wake_due(rows)
