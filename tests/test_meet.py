"""Valor in Google Meet, the kernel's side (docs/plans/meet.md): `meet.join`
from a group chat opens one call session and reports its link once; the
call session runs no turn and owes no notice; its end is written once,
answers the questions Tom's read-backs settle, refuses what it never
delivered, and reports to the chat that asked; a call whose process died
is ended by the kernel's sweep; the call's transcript reads through
`valor-record call` from the asking chat's project only."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from core import board, broker, call, db, intake, ledger, machine, notices, record, router, session, tasks
from core.machine import State
from core.settings import settings
from tests import bridges
from tests.test_board import ask
from tests.test_kernel import at
from tools import chat_tasks, meet

pytestmark = pytest.mark.spend(usd=0)
run = asyncio.run
URL, CODE = "https://meet.google.com/abc-defg-hij", "abc-defg-hij"
TOM = "Tom Counsell"


@pytest.fixture
def team(tmp_path):
    """A project, `acme`, with one Slack channel, and this Mac's call
    profile signed in."""
    chat = f"T0ABC/C{uuid.uuid4().hex[:8].upper()}"
    profile = tmp_path / "meet-chrome"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(
        json.dumps({"account_info": [{"email": "valor@example.com"}]})
    )
    here = f"m-{uuid.uuid4().hex[:6]}"  # one call at a time per Mac: each test a Mac of its own
    with (
        bridges.operator(tmp_path, meet_profile=str(profile), operator_name=TOM),
        bridges.configure(machine=here, default_machine=here),
    ):
        name = f"acme{uuid.uuid4().hex[:6]}"
        (tmp_path / "projects" / f"{name}.toml").write_text(
            f'name = "{name}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["slack:{chat}"]\n'
        )
        yield SimpleNamespace(chat=chat, project=name, profile=profile)


async def said(dsn, chat, text, ts, **kw) -> dict:
    inbound = intake.Inbound(channel="slack", chat_id=chat, chat_kind="group", message_id=ts,
                             sender_id="T0ABC/ULOGAN", sender_name="Logan", sent_at="2026-10-31T02:00:00Z",
                             text=text, **kw)  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
    return bound


async def asking_session(dsn, team) -> tasks.Brief:
    bound = await said(dsn, team.chat, "Can we jump on a call about the launch?", at(1))
    async with await db.connect(dsn) as conn:
        return await tasks.brief(conn, bound["task_id"])


def joiner(brief, dsn, started):
    async def create():
        return URL, CODE

    return meet.MeetJoin(brief, dsn, create=create, start=started.append)


async def join(dsn, brief, started, *, target="new", request_id="t1/join.json", **payload):
    payload = {"reply_to": at(1), "topic_id": None, "prep": "The launch: two decisions open.", **payload}
    async with await db.connect(dsn) as conn:
        return await broker.request(conn, broker.Performers(joiner(brief, dsn, started)), brief.id,
                                    broker.Action("meet.join", target, payload), request_id=request_id)  # fmt: skip


async def reports(dsn, session_id) -> list[dict]:
    return [r["payload"] for r in await bridges.rows(dsn, session_id) if r["type"] == "child.reported"]


async def a_call(dsn, team) -> tuple[tasks.Brief, str]:
    """The asking session and the call session it opened."""
    brief = await asking_session(dsn, team)
    out = await join(dsn, brief, [])
    assert out.kind == "done", out
    return brief, out.result["session_id"]


async def append(dsn, task_id, *rows) -> None:
    async with await db.connect(dsn) as conn:
        for kind, payload in rows:
            await ledger.append(conn, task_id, kind, payload)


async def held(dsn, session_id, action_type, payload, *, done: bool | None, target=CODE) -> str:
    """An effect of the call session, held for its performer, then
    performed (`done`), failed (`False`), or never released (None)."""
    effect = ledger.new_id()
    described = {"action_type": action_type, "effect_class": "act", "target": target, "payload": payload,
                 "idempotency_key": f"k:{effect}", "payload_sha256": ledger.digest(payload)}  # fmt: skip
    rows = [("effect.held", {"effect_id": effect, **described}),
            ("release.requested", {"effect_id": effect, "owner": action_type.split(".")[0]})]  # fmt: skip
    if done is not None:
        rows += [("effect.intent", {"effect_id": effect, **described}),
                 ("effect.outcome", {"effect_id": effect, "idempotency_key": f"k:{effect}",
                                     "kind": "done" if done else "failed",
                                     "result": {"posted": payload["text"]} if done else {}, "error": None})]  # fmt: skip
    await append(dsn, session_id, *rows)
    return effect


async def end(dsn, session_id, reason="the call ended", **kw) -> bool:
    async with await db.connect(dsn) as conn:
        return await call.end(conn, session_id, reason, **kw)


# -- joining -----------------------------------------------------------------------


def test_meet_join_is_offered_to_a_group_chats_session_and_never_a_dm():
    """A Telegram group or a Slack channel, with a workspace; never a DM,
    an email thread, or a task that is no chat session."""

    def brief(channel, chat_id, workspace="/w"):
        return tasks.Brief(
            instruction="x", workspace=workspace, chat={"channel": channel, "chat_id": chat_id}
        )

    assert meet.group_chat({"channel": "telegram", "chat_id": "-1001234"})
    assert meet.group_chat({"channel": "slack", "chat_id": "T0ABC/C0123"})
    assert meet.group_chat({"channel": "slack", "chat_id": "T0ABC/G0123"})
    assert not meet.group_chat({"channel": "telegram", "chat_id": "179144806"})
    assert not meet.group_chat({"channel": "slack", "chat_id": "T0ABC/U0123"})
    assert not meet.group_chat({"channel": "email", "chat_id": "<a@b>"})
    assert not meet.group_chat(None)
    from core.__main__ import _performers

    offered = _performers(brief("slack", "T0ABC/C0123")).offered("act")
    assert any(u.startswith("`meet.join`") for u in offered)
    for b in (brief("slack", "T0ABC/U0123"), brief("telegram", "42"), brief("slack", "T0ABC/C0123", None),
              tasks.Brief(instruction="x", workspace="/w")):  # fmt: skip
        assert not any(u.startswith("`meet.join`") for u in _performers(b).offered("act"))
    # A call's own say and post are no turn's to request.
    assert not any(u.startswith("`meet.") and "join" not in u for u in offered)


def test_a_join_opens_one_call_session_reports_its_link_once_and_starts_its_process(dsn, team):
    """The call session is a root chat session on `meet` under the asking
    chat's project and ceiling, carrying the thread and the prep; the
    asking session gets the link in one `call` report naming the call
    session, so it can stop or steer it; a retry of the request, and a
    re-performed effect, make no second session."""

    async def go():
        brief = await asking_session(dsn, team)
        started: list[str] = []
        first = await join(dsn, brief, started)
        again = await join(dsn, brief, started)
        (intent,) = await bridges.of_type(dsn, "effect.intent", effect_id=first.effect_id)
        found = await joiner(brief, dsn, started).lookup(
            broker.Action("meet.join", "new", {}), intent["idempotency_key"]
        )
        session_id = first.result["session_id"]
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, session_id)
            info = await call.marker(conn, session_id)
            reporting = await tasks.reporting(conn, brief.id)
        return brief, first, again, found, started, b, info, await reports(dsn, brief.id), reporting

    brief, first, again, found, started, b, info, got, reporting = run(go())
    assert first.kind == "done" and first.result["url"] == URL
    assert again.effect_id == first.effect_id and found == first.result
    session_id = first.result["session_id"]
    assert started == [session_id]
    assert b.chat == {"channel": "meet", "chat_id": CODE} and b.project == {"name": team.project}
    assert b.max_effect_class == brief.max_effect_class == "act" and b.parent_id is None
    assert info["asked_by"] == {"channel": "slack", "chat_id": team.chat, "reply_to": at(1), "topic_id": None,
                                "session": brief.id}  # fmt: skip
    assert info["prep"].startswith("The launch") and info["machine"] == settings.machine
    (link,) = [r for r in got if r["kind"] == "call"]
    assert link["child_id"] == session_id and URL in link["text"] and at(1) in link["text"]
    assert session_id in reporting


def test_a_second_call_on_this_mac_fails_naming_the_first_and_the_chat_hears_why(dsn, team):
    async def go():
        brief, first = await a_call(dsn, team)
        started: list[str] = []
        second = await join(
            dsn, brief, started, target="https://meet.google.com/xyz-abcd-efg", request_id="t2/join.json"
        )
        return brief, first, second, started, await reports(dsn, brief.id)

    _brief, _first, second, started, got = run(go())
    assert second.kind == "failed" and URL in second.error and started == []
    assert any(r["about_key"].endswith(":failed") and URL in r["text"] and r["child_id"] is None for r in got)


def test_a_join_with_the_profile_signed_out_names_the_sign_in(dsn, team):
    (team.profile / "Default" / "Preferences").write_text(json.dumps({"account_info": []}))

    async def go():
        brief = await asking_session(dsn, team)
        return await join(dsn, brief, [])

    out = run(go())
    assert out.kind == "failed" and "meet-chrome" in out.error and "accounts.google.com" in out.error


def test_meet_join_refuses_a_target_that_is_no_meet_and_an_overlong_prep(dsn, team):
    async def go():
        brief = await asking_session(dsn, team)
        bad = await join(dsn, brief, [], target="https://zoom.us/j/1")
        long = await join(dsn, brief, [], prep="x" * 4001, request_id="t2/j.json")
        return bad, long

    bad, long = run(go())
    assert bad.kind == "refused" and "Meet link" in bad.error
    assert long.kind == "refused" and "4000" in long.error


# -- the call session -------------------------------------------------------------


def test_the_call_session_runs_no_turn_and_owes_no_notice(dsn, team):
    """A steer of it wakes no step; its end owes no notice to the meet chat."""

    async def go():
        _brief, session_id = await a_call(dsn, team)
        await append(dsn, session_id, ("message.steered", {"text": "tell them we're running late"}))
        rows = await bridges.rows(dsn, session_id)
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, session_id)
            stepping = router.has_step(rows, machine.fold(rows), b.chat)
        await end(dsn, session_id)
        async with await db.connect(dsn) as conn:
            owed = await notices.owe(conn, session_id)
        return stepping, owed, machine.fold(await bridges.rows(dsn, session_id)).state

    stepping, owed, state = run(go())
    assert stepping is False and owed == [] and state is State.ENDED


def test_a_steer_of_an_ended_call_fails_and_reopens_nothing(dsn, team):
    async def go():
        brief, session_id = await a_call(dsn, team)
        steer = chat_tasks.TaskSteer(brief, dsn)
        action = broker.Action("task.steer", session_id, {"text": "say hi", "by": "Logan"})
        async with await db.connect(dsn) as conn:
            before = await steer.refuse(conn, action)
        await end(dsn, session_id)
        async with await db.connect(dsn) as conn:
            after = await steer.refuse(conn, action)
        return before, after, machine.fold(await bridges.rows(dsn, session_id)).state

    before, after, state = run(go())
    assert before is None and after == "the call has ended" and state is State.ENDED


def test_a_call_worker_turn_reads_its_stage_and_the_asking_chats_room_and_no_verdict(dsn, team):
    (team.profile.parent / "projects" / f"{team.project}.toml").write_text(
        (team.profile.parent / "projects" / f"{team.project}.toml").read_text()
        + f'[rooms]\n"slack:{team.chat}" = "The launch team."\n'
    )

    async def go():
        _brief, session_id = await a_call(dsn, team)
        async with await db.connect(dsn) as conn:
            return await tasks.dispatch(conn, session_id, fresh="call")

    text = run(go())["text"]
    assert "# Stage: call" in text and "Room: The launch team." in text
    assert tasks.verdict_text() not in text


# -- the end -----------------------------------------------------------------------


def test_the_end_is_written_once_refuses_what_never_left_and_leaves_copies_to_their_bridge(dsn, team):
    """A say never released is refused; a post caught mid-perform is
    failed; a thread copy still goes; the asking session's report names
    the transcript, the thread, the late answer and the dropped question."""

    async def go():
        brief, session_id = await a_call(dsn, team)
        never = await held(dsn, session_id, "meet.say", {"text": "Hello.", "turn": "t"}, done=None)
        caught = ledger.new_id()
        payload = {"text": "Posting."}
        described = {"action_type": "meet.send_message", "effect_class": "act", "target": CODE, "payload": payload,
                     "idempotency_key": f"k:{caught}", "payload_sha256": ledger.digest(payload)}  # fmt: skip
        await append(dsn, session_id, ("effect.held", {"effect_id": caught, **described}),
                     ("effect.intent", {"effect_id": caught, **described}))  # fmt: skip
        copy = await held(dsn, session_id, "slack.send_message", {"text": "The answer.", "reply_to": at(1)},
                          done=None, target=team.chat)  # fmt: skip
        late = [{"question": "What did we ship?", "answer": "The import."}]
        first = await end(dsn, session_id, "everyone else had left", late=late, unanswered=["Who owns it?"])
        second = await end(dsn, session_id, "again")
        return brief, session_id, never, caught, copy, first, second

    brief, session_id, never, caught, copy, first, second = run(go())
    assert first is True and second is False
    rows = run(bridges.rows(dsn, session_id))
    kinds = [r["type"] for r in rows]
    assert kinds.count("call.ended") == 1 and kinds.count("task.ended") == 1
    refused = {r["payload"]["effect_id"] for r in rows if r["type"] == "effect.refused"}
    failed = {r["payload"]["effect_id"] for r in rows if r["type"] == "effect.outcome"
              and r["payload"]["kind"] == "failed"}  # fmt: skip
    assert refused == {never} and failed == {caught} and copy not in refused | failed
    (ended,) = [r for r in run(reports(dsn, brief.id)) if r["about_key"].endswith(":ended")]
    assert f"valor-record call {CODE}" in ended["text"] and at(1) in ended["text"]
    assert "The import." in ended["text"] and "Who owns it?" in ended["text"]


def test_a_stopped_call_ends_without_task_ended_and_names_the_copies_it_dropped(dsn, team):
    async def go():
        brief, session_id = await a_call(dsn, team)
        await held(
            dsn, session_id, "slack.send_message", {"text": "Half an answer."}, done=None, target=team.chat
        )
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, session_id, reason="stop the call", by="tom", via="chat")
        await end(dsn, session_id, "the call was stopped", unanswered=["What broke?"])
        return brief, session_id

    brief, session_id = run(go())
    kinds = [r["type"] for r in run(bridges.rows(dsn, session_id))]
    assert "call.ended" in kinds and "task.ended" not in kinds
    (ended,) = [r for r in run(reports(dsn, brief.id)) if r["about_key"].endswith(":ended")]
    assert "Half an answer." in ended["text"] and "What broke?" in ended["text"]


def test_the_end_reaches_the_asking_chat_after_its_session_closed(dsn, team):
    async def go():
        brief, session_id = await a_call(dsn, team)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, brief.id, reason="closed", by="tom", via="chat")
        await end(dsn, session_id)
        async with await db.connect(dsn) as conn:
            now = await intake.chat_session(conn, "slack", team.chat)
        return brief, session_id, now

    brief, session_id, now = run(go())
    assert now is not None and now != brief.id
    assert any(r["about_key"] == f"call:{session_id}:ended" for r in run(reports(dsn, now)))


def test_the_sweep_ends_a_call_whose_process_is_gone_and_leaves_one_still_starting(dsn, team, monkeypatch):
    """Started with its lock free: ended. Started with its lock held: left.
    Never started and young: left; never started past the limit: ended."""

    async def go():
        _, gone = await a_call(dsn, team)
        await append(dsn, gone, ("call.started", {}))
        alive = await opened(dsn, team, "https://meet.google.com/aaa-bbbb-ccc")
        await append(dsn, alive, ("call.started", {}))
        young = await opened(dsn, team, "https://meet.google.com/ddd-eeee-fff")
        holder = await db.connect(dsn)
        await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"meet:call:{alive}",))
        async with await db.connect(dsn) as conn:
            swept = await call.sweep(conn)
        monkeypatch.setattr(call, "NEVER_STARTED_S", 0)
        async with await db.connect(dsn) as conn:
            later = await call.sweep(conn)
        await holder.close()
        return gone, alive, young, swept, later

    gone, alive, young, swept, later = run(go())
    # Other tests' calls share the database: only these three are looked at.
    assert gone in swept and young not in swept and young in later and alive not in swept + later
    reasons = [r["payload"]["reason"] for r in run(bridges.rows(dsn, young)) if r["type"] == "call.ended"]
    assert reasons == ["Valor's call process never started"]
    assert not any(r["type"] == "call.ended" for r in run(bridges.rows(dsn, alive)))


async def opened(dsn, team, url) -> str:
    """Another call session, written as `meet.join` writes one."""
    async with await db.connect(dsn) as conn:
        brief = await tasks.brief(conn, await intake.chat_session(conn, "slack", team.chat))
        async with conn.transaction():
            return await call.begin(conn, brief, url=url, code=url.rsplit("/", 1)[1], reply_to=None,
                                    topic_id=None, prep="", effect_key=ledger.new_id())  # fmt: skip


# -- decisions ----------------------------------------------------------------------


async def waiting(dsn, team, text) -> tuple[str, str]:
    """A task of the project waiting on its question: (task, question)."""
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, tasks.Brief(instruction=text, project={"name": team.project}))
    return task, await ask(dsn, task, text)


async def readback(dsn, session_id, text, settles, *, done=True) -> None:
    await held(dsn, session_id, "meet.send_message", {"text": text, "settles": settles}, done=done)


async def ask_again(dsn, task, text) -> str:
    """A task that was answered asks its next question."""
    tid, qid = f"{task}-t9", f"{task}-q9"
    async with await db.connect(dsn) as conn:
        state = machine.fold(await ledger.read(conn, task)).state.value
        for kind, payload in (("turn.started", {"turn_id": tid, "state": state}),
                              ("turn.ended", {"turn_id": tid, "outcome": "done", "result": {"session_id": "s"}}),
                              ("turn.collected", {"turn_id": tid, "state": state, "verdict": "asked"}),
                              ("question.asked", {"question_id": qid, "turn_id": tid, "text": text, "state": state})):  # fmt: skip
            await ledger.append(conn, task, kind, payload)
        assert machine.fold(await ledger.read(conn, task)).open_question == qid
    return qid


def answers(dsn, task) -> list[dict]:
    return [r["payload"] for r in run(bridges.rows(dsn, task)) if r["type"] == "question.answered"]


def test_tom_alone_with_valor_settles_three_questions_with_one_readback_and_a_correction_wins(dsn, team):
    """Each settled question is answered with the latest done read-back, by
    Tom, via `meet call`; a failed read-back settles nothing; the board
    then shows them decided."""

    async def go():
        brief, session_id = await a_call(dsn, team)
        await append(dsn, session_id, ("call.participant", {"id": "d1", "name": TOM}))
        qs = [await waiting(dsn, team, t) for t in ("Ship Friday?", "Freeze the API?", "Drop IE?")]
        settles = [{"task_id": t, "question_id": q} for t, q in qs]
        await readback(dsn, session_id, "Ship Friday, freeze the API, drop IE.", settles)
        await readback(dsn, session_id, "Correction: ship Monday.", settles[:1])
        await readback(dsn, session_id, "Never left.", settles[1:2], done=False)
        await end(dsn, session_id)
        async with await db.connect(dsn) as conn:
            snap = await board.snapshot(conn, board.spec_of(team.project))
        return brief, qs, snap

    _brief, qs, snap = run(go())
    first, second, third = (answers(dsn, t) for t, _ in qs)
    assert [a["text"] for a in first] == ["Correction: ship Monday."]
    assert (
        [a["text"] for a in second] == [a["text"] for a in third] == ["Ship Friday, freeze the API, drop IE."]
    )
    assert all(
        a["provenance"]["by"] == "tom" and a["provenance"]["via"] == "meet call" for a in first + second
    )
    assert not snap["open"] and len(snap["decided"]) == 3


def test_a_readback_of_a_question_since_replaced_is_refused_and_named_and_the_new_one_stays_open(dsn, team):
    async def go():
        brief, session_id = await a_call(dsn, team)
        await append(dsn, session_id, ("call.participant", {"id": "d1", "name": TOM}))
        task, q1 = await waiting(dsn, team, "Ship Friday?")
        async with await db.connect(dsn) as conn:
            await session.answer(conn, task, "yes", by="tom", via="slack reaction :+1:")
        q2 = await ask_again(dsn, task, "Which region?")
        await readback(dsn, session_id, "Ship Friday.", [{"task_id": task, "question_id": q1}])
        await end(dsn, session_id)
        return brief, task, q2

    brief, task, q2 = run(go())
    assert [a["text"] for a in answers(dsn, task)] == ["yes"]
    assert machine.fold(run(bridges.rows(dsn, task))).open_question == q2
    (ended,) = [r for r in run(reports(dsn, brief.id)) if r["about_key"].endswith(":ended")]
    assert "Not answered" in ended["text"] and task in ended["text"]


def test_tom_not_in_the_call_settles_nothing(dsn, team):
    async def go():
        _brief, session_id = await a_call(dsn, team)
        await append(dsn, session_id, ("call.participant", {"id": "d2", "name": "Logan"}))
        task, q = await waiting(dsn, team, "Ship Friday?")
        await readback(dsn, session_id, "Ship Friday.", [{"task_id": task, "question_id": q}])
        await end(dsn, session_id)
        return task

    task = run(go())
    assert answers(dsn, task) == []


def test_with_others_present_readbacks_go_to_toms_slack_dm_which_answers_on_his_thumbs_up(
    dsn, team, tmp_path
):
    """The DM's session gets one `call` report with each read-back, and its
    `task.answer` with the read-back's question id is accepted; another
    session's is refused, and so is a stale question id."""
    tom = "T0ABC/U0TOM"

    async def go():
        with bridges.configure(operator_channel="slack", operator_chat=tom, operator_slack_ids=(tom,)):
            (tmp_path / "projects" / "valor.toml").write_text(
                bridges.VALOR_SPEC + f'chats = ["slack:{tom}"]\n'
            )
            brief, session_id = await a_call(dsn, team)
            await append(dsn, session_id, ("call.participant", {"id": "d1", "name": TOM}),
                         ("call.participant", {"id": "d2", "name": "Logan"}))  # fmt: skip
            task, q = await waiting(dsn, team, "Ship Friday?")
            await readback(dsn, session_id, "Ship Friday.", [{"task_id": task, "question_id": q}])
            await end(dsn, session_id)
            async with await db.connect(dsn) as conn:
                dm = await intake.chat_session(conn, "slack", tom)
                dm_brief = await tasks.brief(conn, dm)
            got = await reports(dsn, dm)
            answer = chat_tasks.TaskAnswer(dm_brief, dsn)
            other = chat_tasks.TaskAnswer(brief, dsn)
            stale = broker.Action("task.answer", task, {"text": "Ship Friday.", "by": "tom", "via": "meet call",
                                                        "question_id": "not-it"})  # fmt: skip
            good = broker.Action("task.answer", task, {"text": "Ship Friday.", "by": "tom", "via": "meet call",
                                                       "question_id": q})  # fmt: skip
            async with await db.connect(dsn) as conn:
                refused_elsewhere = await other.refuse(conn, good)
                refused_here = await answer.refuse(conn, good)
            with pytest.raises(LookupError):
                await answer.perform(stale, "k1")
            done = await answer.perform(good, "k2")
            return task, q, got, refused_elsewhere, refused_here, done

    task, q, got, refused_elsewhere, refused_here, done = run(go())
    (confirm,) = [r for r in got if r["about_key"].endswith(":confirm")]
    assert confirm["confirm"] == [{"task_id": task, "question_id": q, "text": "Ship Friday."}]
    assert "Ship Friday." in confirm["text"] and "👍" in confirm["text"]
    assert refused_elsewhere and refused_here is None and done["question_id"] == q
    (a,) = answers(dsn, task)
    assert a["provenance"]["via"] == "meet call"


# -- what the call writes ----------------------------------------------------------------


def test_a_calls_posts_says_and_thread_copies_owe_no_report_and_a_send_elsewhere_does(dsn, team):
    async def go():
        _brief, session_id = await a_call(dsn, team)
        out = {}
        async with await db.connect(dsn) as conn:
            for name, kind, target in (("post", "meet.send_message", CODE), ("say", "meet.say", CODE),
                                       ("copy", "slack.send_message", team.chat),
                                       ("elsewhere", "slack.send_message", "T0ABC/C0OTHER")):  # fmt: skip
                effect = {"action_type": kind, "effect_class": "act", "target": target,
                          "payload": {"text": "hi"}, "effect_id": ledger.new_id()}  # fmt: skip
                out[name] = await notices.report(conn, session_id, effect, {})
        return out

    out = run(go())
    assert out["post"] is None and out["say"] is None and out["copy"] is None and out["elsewhere"]


def test_a_calls_post_is_refused_empty_over_500_characters_or_with_bad_settles(dsn, team):
    from core import bridge

    check = bridge.DECLARED["meet.send_message"].check

    async def go():
        async with await db.connect(dsn) as conn:
            return [
                await check(conn, broker.Action("meet.send_message", CODE, p))
                for p in ({"text": "ok"}, {"text": " "}, {"text": "x" * 501}, {"text": "ok", "settles": [{"task_id": 1}]},
                          {"text": "ok", "settles": [{"task_id": "t", "question_id": "q"}]})
            ]  # fmt: skip

    ok, empty, long, bad, good = run(go())
    assert ok is None and good is None and "empty" in empty and "501" in long and "settles" in bad


def test_the_call_transcript_reads_in_order_from_the_asking_chats_project_only_a_page_at_a_time(
    dsn, team, tmp_path
):
    """Heard turns, a spoken answer cut short and a post, newest first,
    paged by `before`; a call longer than a page is read whole by paging;
    another project's turn finds no call."""

    async def go():
        brief, session_id = await a_call(dsn, team)
        rows = [("call.started", {})]
        for i in range(record.MOST + 5):
            rows.append(("call.heard", {"turn_id": f"t{i}", "who": "d2", "name": "Logan", "via": "spoke",
                                        "text": f"line {i}"}))  # fmt: skip
        rows.append(("effect.outcome", {"effect_id": ledger.new_id(), "kind": "done",
                                        "result": {"spoken_text": "We ship", "cut": True}}))  # fmt: skip
        rows.append(("effect.outcome", {"effect_id": ledger.new_id(), "kind": "done",
                                        "result": {"posted": "Ship Monday."}}))  # fmt: skip
        await append(dsn, session_id, *rows)
        pages, before = [], None
        while True:
            q = {"code": CODE, **({"before": str(before)} if before else {})}
            page = await record.answer(dsn, brief.id, "call", q)
            pages += page
            if len(page) < record.LIMIT:
                break
            before = page[-1]["record"]
        other = f"other{uuid.uuid4().hex[:6]}"
        (tmp_path / "projects" / f"{other}.toml").write_text(
            f'name = "{other}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = ["slack:T0ABC/C0ELSE"]\n'
        )
        async with await db.connect(dsn) as conn:
            outsider = await tasks.start(conn, tasks.Brief(instruction="x", project={"name": other}))
        with pytest.raises(record.NotFound):
            await record.answer(dsn, outsider, "call", {"code": CODE})
        return pages

    pages = run(go())
    lines = list(reversed(pages))
    assert len(lines) == record.MOST + 8
    assert lines[0]["how"] == "joined" and lines[1]["text"] == "line 0"
    assert lines[-2] == {**lines[-2], "who": "Valor", "how": "spoke (cut off)", "text": "We ship"}
    assert lines[-1]["how"] == "typed" and lines[-1]["text"] == "Ship Monday."
    assert lines[-3]["text"] == f"line {record.MOST + 4}"
