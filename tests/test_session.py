"""The working session on real Postgres and real git: a thin request asks,
Tom's answer lands in the session that asked, the plan is recorded from a
committed file, the build's candidate goes through the checks to a merge
held for Tom, and feedback after the merge patches in the same session.

No model call: each turn is a scripted subprocess (`tests/scripted.py`)
that plays its stage by writing `.valor/` files and committing, the way a
`claude -p` turn would. A check verdict a test does not run a runner for is
written straight through `record_check` (`scripted.check`), with the
judgements the local upstream gives.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import bridge, broker, db, ledger, session, signals, spending, tasks
from core.gateway import Gateway
from harnesses import claude_code
from tests import bridges
from tests import judgement_upstream, scripted
from tests.conftest import TEST_DB
from tests.scripted import git
from tools.push_branch import PushBranch

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


async def drive(dsn, task) -> dict:
    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await scripted.route(gateway, task, scripted.RUNNERS, dsn=dsn)
    finally:
        await gateway.close()


def test_a_thin_request_asks_and_the_answer_resumes_the_session_that_asked(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        first = await drive(dsn, task)
        again = await drive(dsn, task)  # runs no turn
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError):
                await session.answer(conn, "no-such-task", "x")
            await session.answer(conn, task, "Morning, Tom.")
            with pytest.raises(LookupError):
                await session.answer(conn, task, "a second answer to nothing")
        second = await drive(dsn, task)
        return task, first, again, second

    _task, first, again, second = run(go())
    assert first["status"] == "waiting" and again["status"] == "waiting"
    assert second["status"] == "no runner" and second["missing"] == ["critique"]
    st = second["state"]
    assert st["state"] == "critique" and st["plan"]["path"] == "docs/plan.md"
    assert st["plan"]["review_rounds"] == 1 and st["plan"]["commit"] == git(ws, "rev-parse", "HEAD")
    t = scripted.turns(ws)
    assert [x["stage"] for x in t] == ["clarify", "clarify", "plan"]
    assert t[0]["prompt"] == "Write Tom a greeting." and t[0]["resume"] == ""
    assert t[1]["prompt"] == "# Tom's answer\n\nMorning, Tom." and t[1]["resume"] == scripted.SESSION
    assert t[2]["prompt"].startswith("# No material question") and t[2]["resume"] == scripted.SESSION
    for x in t:
        assert "# Corrections from Tom" in x["brief"] and "# How this task reaches Tom" in x["brief"]
    assert "push_branch" in t[0]["brief"] and "`merge`" not in t[0]["brief"]
    # the judge's verdict is the kernel's reading of a judgement row: no attention spent
    assert (st["attention_counts"]["question"]["total"], st["attention_counts"]["verdict"]["total"]) == (1, 0)


def test_a_candidate_reaches_a_held_merge_and_feedback_after_the_merge_patches(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    scripted.steer(ws, push="valor/greeting")

    async def go():
        task = await scripted.start(dsn, ws)
        planned = await drive(dsn, task)
        await scripted.critique(dsn, task)
        built = await drive(dsn, task)
        await scripted.checks(dsn, task)
        delivered = await drive(dsn, task)
        st = delivered["state"]
        merge = next(
            e for e, s in st["effects"].items() if s == "pending" and e == st["merge_effect"]["effect_id"]
        )
        push = next(e for e, s in st["effects"].items() if s == "pending" and e != merge)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, push, note="push it")
            await scripted.release(conn, push)
            await broker.approve(conn, merge, note="merge it")
            merged = await scripted.release(conn, merge)
            await session.feedback(conn, task, "Greet him by name.")
        patched = await drive(dsn, task)
        return task, planned, built, delivered, merged, patched

    _task, planned, built, delivered, merged, patched = run(go())
    assert planned["status"] == "no runner" and built["missing"] == ["test", "review", "docs"]
    cand = built["state"]["candidate"]
    assert cand["sha"] == git(ws, "rev-parse", "HEAD~1")  # the patch committed on top since
    assert delivered["status"] == "delivered" and delivered["state"]["delivery"]["outcome"] == "passed"
    assert "greeting.txt" in delivered["state"]["delivered"]
    assert merged.kind == "done" and git(origin, "rev-parse", "main") == cand["sha"]
    assert git(origin, "rev-parse", "valor/greeting") == cand["sha"]
    assert patched["status"] == "no runner" and patched["state"]["candidate"]["sha"] != cand["sha"]
    t = scripted.turns(ws)
    assert [x["stage"] for x in t] == ["plan", "build", "patch"]
    assert t[1]["prompt"].startswith("# Critique: sound")
    assert t[2]["prompt"].startswith("# Tom's feedback on the delivery\n\nGreet him by name.")
    assert all(x["resume"] == scripted.SESSION for x in t[1:])
    assert "Plan: docs/plan.md" in t[1]["brief"] and "# Stage: build" in t[1]["brief"]


def test_a_failed_turn_leaves_the_answer_for_the_next_turn(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        outs = [await drive(dsn, task)]
        async with await db.connect(dsn) as conn:
            await session.answer(conn, task, "Morning, Tom.", by="stand-in", role_played=True)
        scripted.steer(ws, fail_next=True)
        outs.append(await drive(dsn, task))
        outs.append(await drive(dsn, task))
        return outs

    outs = run(go())
    assert [o["status"] for o in outs] == ["waiting", "failed", "no runner"]
    prompts = [x["prompt"] for x in scripted.turns(ws)]
    answered = "# Tom's answer\n\nMorning, Tom."
    assert prompts[1] == answered and prompts[2] == answered  # sent again after the failed turn
    question = outs[2]["state"]["attention"][0]
    assert question["provenance"]["by"] == "stand-in" and question["provenance"]["role_played"] is True


def test_turns_with_no_signal_do_not_end_the_run_and_the_next_prompt_says_continue(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        scripted.steer(ws, build="nothing", turns=3)
        return await drive(dsn, task)

    out = run(go())
    assert out["status"] == "no runner" and out["state"]["state"] == "checks"
    t = [x for x in scripted.turns(ws) if x["stage"] == "build"]
    assert len(t) == 4 and t[0]["prompt"].startswith("# Critique: sound")
    assert [x["prompt"] for x in t[1:]] == ["Continue."] * 3


def test_a_stopped_task_takes_no_feedback_and_an_open_question_takes_an_answer(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        waiting = await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError, match="open question"):
                await session.feedback(conn, task, "feedback while a question is open")
            await tasks.stop(conn, task, reason="test")
            with pytest.raises(LookupError, match="stopped"):
                await session.feedback(conn, task, "feedback on a stopped task")
            with pytest.raises(LookupError):
                await session.answer(conn, task, "an answer to a stopped task")
            return waiting, await ledger.read(conn, task)

    waiting, rows = run(go())
    assert waiting["status"] == "waiting"
    assert not [r for r in rows if r["type"] in ("feedback.given", "question.answered")]


def test_an_unread_effect_request_and_continue_carry_the_outcome_into_the_next_prompt(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    (ws / ".valor" / "effects").mkdir(parents=True)
    (ws / ".valor" / "effects" / "bad.json").write_text("not json")
    (ws / ".valor" / "effects" / "send.json").write_text(
        json.dumps({"action_type": "no_such_action", "target": "tom", "payload": {}})
    )
    (ws / ".valor" / "effects" / "merge.json").write_text(
        json.dumps({"action_type": "merge", "target": "main", "payload": {"head_sha": "x"}})
    )

    async def go():
        task = await scripted.start(dsn, ws)
        found = signals.collect(ws, "turn-1")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": "turn-1", "state": "plan"})
            await ledger.append(
                conn,
                task,
                "turn.ended",
                {"turn_id": "turn-1", "outcome": "done", "result": {"session_id": scripted.SESSION}},
            )
            verdict = await session.record(conn, task, "turn-1", found, state=tasks.machine.State.PLAN,
                                           workspace=str(ws))  # fmt: skip
            return verdict, await session.next_prompt(conn, task), await tasks.status(conn, task)

    verdict, (prompt, resume), state = run(go())
    assert verdict == "idle" and resume == scripted.SESSION and prompt.startswith("Continue.")
    assert "bad.json: unreadable request" in prompt
    assert "- send.json (effect " in prompt and "refused: no performer for this action type" in prompt
    assert "no_such_action" not in prompt  # the request is not repeated into the prompt
    assert "merge.json: the merge is the kernel's to request" in prompt
    assert state["state"] == "plan" and list(state["effects"].values()) == ["refused"]
    assert list((ws / ".valor" / "handled" / "turn-1" / "effects").iterdir())


def test_a_request_file_never_stops_the_turn_being_collected(dsn, tmp_path):
    """Whatever a request file holds, it is answered and the turn collected.
    What Postgres jsonb refuses (a NUL character, a NaN or infinite number,
    nesting past the server's stack depth) is Postgres's to say, and every
    row an effect gets holds its payload whole, so such a request is
    unreadable with Postgres's reason. A surrogate code point left over
    after parsing (lone, swapped, or a pair split between an escape and raw
    bytes) is not text. Nesting past Python's parser is not JSON it can
    read. A send whose text fields are not strings is refused for its
    shape. The other requests go on: one nested 2000 deep reaches the
    broker, and a clean send holding a paired emoji is held."""
    ws, _ = scripted.workspace(tmp_path)
    effects = ws / ".valor" / "effects"
    effects.mkdir(parents=True)
    chat, mail = bridges.OPERATOR_CHAT, bridges.OPERATOR_EMAIL
    nul = "cannot store it: UntranslatableCharacter"
    number = "cannot store it: InvalidTextRepresentation"
    surrogate = "it holds a surrogate code point outside an escaped pair, which is not text"
    requests = {
        "a_path.json": (nul, "telegram.send_message", chat, {"text": "hi", "files": [
            {"path": f"{ws}/a\x00.txt", "sha256": "0" * 64}]}),
        "b_mail.json": (nul, "email.send", mail, {"to": [mail], "subject": "a\x00b"}),
        "c_other.json": (nul, "no_such_action", "tom", {"note": {"deep": ["x\x00"]}}),
        "d_target.json": (nul, "no_such_action", "t\x00m", {}),
        "f_high.json": (surrogate, "email.send", mail, {"subject": "a\ud800b"}),
        "g_low_key.json": (surrogate, "no_such_action", "tom", {"note": {"\udc00": 1}}),
        "h_swapped.json": (surrogate, "no_such_action", "tom", {"note": "\ude00\ud83d"}),
        "i_nan.json": (number, "no_such_action", "tom", {"n": float("nan")}),
        "j_inf.json": (number, "no_such_action", "tom", {"n": [float("-inf")]}),
        "l_type.json": (surrogate, "no_such\udfff", "tom", {}),
        "m_target.json": (surrogate, "no_such_action", "x\udc00", {}),
        "n_deep.json": (surrogate, "no_such_action", "tom", {"note": {"deep": ["x\udc00"]}}),
        "o_path.json": (surrogate, "telegram.send_message", chat, {"text": "hi", "files": [
            {"path": f"{ws}/a\ud800.txt", "sha256": "0" * 64}]}),
        "p_pos_inf.json": (number, "no_such_action", "tom", {"n": float("inf")}),
        "e_ok.json": (None, "telegram.send_message", chat, {"text": "hi \U0001f600"}),
    }  # fmt: skip
    for name, (_, action_type, target, payload) in requests.items():
        body = {"action_type": action_type, "target": target, "payload": payload}
        (effects / name).write_text(json.dumps(body))
    (effects / "k_big.json").write_text('{"action_type": "x", "target": "tom", "payload": {"n": 1e999}}')
    requests["k_big.json"] = (number,)
    head = (
        b'{"action_type": "telegram.send_message", "target": "%s", "payload": {"text": "hi ' % chat.encode()
    )
    (effects / "q_split.json").write_bytes(head + b"\\ud83d" + b"\xed\xb8\x80" + b'"}}')
    (effects / "r_cesu.json").write_bytes(head + b"\xed\xa0\xbd\xed\xb8\x80" + b'"}}')
    requests["q_split.json"] = requests["r_cesu.json"] = (surrogate,)

    def nested(depth: int) -> str:
        return '{"action_type": "no_such_action", "target": "tom", "payload": {"n": %s}}' % (
            "[" * depth + "]" * depth
        )

    (effects / "s_deep_stored.json").write_text(nested(2000))
    (effects / "t_deep_postgres.json").write_text(nested(100_000))
    requests["t_deep_postgres.json"] = ("cannot store it: StatementTooComplex (stack depth limit exceeded)",)
    (effects / "u_deep_parse.json").write_text(nested(400_000))
    requests["u_deep_parse.json"] = ("RecursionError (",)
    shapes = {
        "v_text_int.json": ("telegram.send_message", chat, {"text": 5}, bridge.TEXT_SHAPE),
        "w_text_list.json": ("telegram.send_message", chat, {"text": ["hi"]}, bridge.TEXT_SHAPE),
        "x_subject.json": ("email.send", mail, {"to": [mail], "subject": 5, "body": "b"}, bridge.EMAIL_SHAPE),
        "y_to.json": ("email.send", mail, {"to": [5], "subject": "s", "body": "b"}, bridge.EMAIL_SHAPE),
        "z_body.json": (
            "email.send",
            mail,
            {"to": [mail], "subject": "s", "body": {"a": 1}},
            bridge.EMAIL_SHAPE,
        ),
    }
    for name, (action_type, target, payload, _) in shapes.items():
        (effects / name).write_text(
            json.dumps({"action_type": action_type, "target": target, "payload": payload})
        )

    async def go():
        task = await scripted.start(dsn, ws)
        found = signals.collect(ws, "turn-nul")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": "turn-nul", "state": "plan"})
            await ledger.append(
                conn,
                task,
                "turn.ended",
                {"turn_id": "turn-nul", "outcome": "done", "result": {"session_id": scripted.SESSION}},
            )
            verdict = await session.record(conn, task, "turn-nul", found, state=tasks.machine.State.PLAN,
                                           workspace=str(ws), performers=bridges.declared(str(ws)))  # fmt: skip
            rows = await ledger.read(conn, task)
            return verdict, await session.next_prompt(conn, task), rows

    with bridges.operator(tmp_path):
        verdict, (prompt, _resume), rows = run(go())
    assert verdict == "idle"
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    by_file = {e["file"]: e for e in collected["effects"]}
    for name, (why, *_) in requests.items():
        if why is None:
            continue
        assert "effect_id" not in by_file[name] and "request" not in by_file[name], by_file[name]
        assert why in by_file[name]["error"] and by_file[name]["error"].startswith("unreadable request: "), (
            by_file[name]
        )
        assert f"{name}: unreadable request" in prompt
    for name, (*_, why) in shapes.items():
        assert (by_file[name]["kind"], by_file[name]["error"]) == ("refused", why), by_file[name]
    assert (by_file["s_deep_stored.json"]["kind"], by_file["s_deep_stored.json"]["error"]) == (
        "refused", "no performer for this action type")  # fmt: skip
    assert by_file["e_ok.json"]["kind"] == "pending"
    [effect] = [r["payload"] for r in rows if r["type"] == "effect.held"]
    assert (effect["action_type"], effect["payload"]["text"]) == ("telegram.send_message", "hi \U0001f600")


def test_a_request_is_judged_as_turn_collected_nests_it(dsn, tmp_path):
    """Postgres's stack depth counts the levels the row holds, and
    `turn.collected` holds a request three levels down, so a request at the
    deepest nesting jsonb stores on its own is answered as unreadable rather
    than failing the turn's collection. The depth is found from Postgres."""
    ws, _ = scripted.workspace(tmp_path)
    effects = ws / ".valor" / "effects"
    effects.mkdir(parents=True)

    def request(depth: int) -> dict:
        n: list = []
        for _ in range(depth - 1):
            n = [n]
        return {"action_type": "no_such_action", "target": "tom", "payload": {"n": n}}

    async def deepest() -> int:
        low, high = 1, 1 << 20
        async with await db.connect(dsn) as conn:
            while high - low > 1:
                mid = (low + high) // 2
                low, high = (mid, high) if await ledger.unstorable(conn, request(mid)) is None else (low, mid)
        return low

    depth = run(deepest())
    (effects / "deep.json").write_text(json.dumps(request(depth)))

    async def go():
        task = await scripted.start(dsn, ws)
        found = signals.collect(ws, "turn-deep")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": "turn-deep", "state": "plan"})
            await ledger.append(conn, task, "turn.ended",
                                {"turn_id": "turn-deep", "outcome": "done", "result": {"session_id": scripted.SESSION}})  # fmt: skip
            await session.record(
                conn, task, "turn-deep", found, state=tasks.machine.State.PLAN, workspace=str(ws)
            )
            return await ledger.read(conn, task)

    rows = run(go())
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    [entry] = collected["effects"]
    assert (
        entry["error"].endswith("StatementTooComplex (stack depth limit exceeded)") and "request" not in entry
    )


async def _record_ended(dsn, ws, turn, state, performers=None):
    """Start a task, end `turn` in `state`, and record what it left."""
    task = await scripted.start(dsn, ws)
    found = signals.collect(ws, turn)
    async with await bridges.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn, "state": state.value})
        await ledger.append(conn, task, "turn.ended",
                            {"turn_id": turn, "outcome": "done", "result": {"session_id": scripted.SESSION}})  # fmt: skip
        verdict = await session.record(
            conn, task, turn, found, state=state, workspace=str(ws), performers=performers
        )
        return verdict, await ledger.read(conn, task)


# Each string is storable alone; two exceed jsonb's size for one value.
BIG = 130 << 20


@pytest.mark.parametrize("left", ["requests", "texts"])
def test_parts_storable_alone_and_not_together(dsn, tmp_path, left):
    """Two requests, or two text signals, each storable on its own, are
    together more than one jsonb value holds. The larger is answered as
    unreadable with Postgres's reason, and the rest of the turn is kept:
    the other part, and a clean send beside them, which is held."""
    ws, _ = scripted.workspace(tmp_path)
    valor = ws / ".valor"
    (valor / "effects").mkdir(parents=True)
    send = {
        "action_type": "telegram.send_message",
        "target": bridges.OPERATOR_CHAT,
        "payload": {"text": "hi"},
    }
    (valor / "effects" / "ok.json").write_text(json.dumps(send))
    if left == "requests":
        for name, size in (("a.json", BIG + 1), ("b.json", BIG)):
            body = {"action_type": "no_such_action", "target": "tom", "payload": {"note": "x" * size}}
            (valor / "effects" / name).write_text(json.dumps(body))
        state = tasks.machine.State.PLAN
    else:
        (valor / "question.md").write_text("q" * (BIG + 1))
        (valor / "done.md").write_text("d" * BIG)
        state = tasks.machine.State.BUILD
    turn = ledger.new_id()
    with bridges.operator(tmp_path):
        _, rows = run(_record_ended(dsn, ws, turn, state, bridges.declared(str(ws))))
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    refused = "the ledger's JSON (Postgres jsonb) cannot store it beside the turn's other parts: ProgramLimitExceeded"
    by_file = {e["file"]: e for e in collected["effects"]}
    assert by_file["ok.json"]["kind"] == "pending"
    [held] = [r["payload"] for r in rows if r["type"] == "effect.held"]
    assert held["request_id"] == f"{turn}/ok.json"
    if left == "requests":
        assert set(by_file["a.json"]) == {"file", "error"}
        assert by_file["a.json"]["error"].startswith(f"unreadable request: {refused}")
        assert by_file["b.json"]["kind"] == "refused"
    else:
        assert collected["question"] is None and len(collected["done"]) == BIG
        assert any(e.startswith(f"question.md is unreadable: {refused}") for e in collected["errors"])


def large_turn(ws: Path, field: str) -> None:
    """A turn whose one field is large, beside a clean send. Kernel text
    names such a field and never copies it, so every row fits."""
    valor = ws / ".valor"
    (valor / "effects").mkdir(parents=True, exist_ok=True)
    send = {
        "action_type": "telegram.send_message",
        "target": bridges.OPERATOR_CHAT,
        "payload": {"text": "hi"},
    }
    (valor / "effects" / "ok.json").write_text(json.dumps(send))
    if field == "action_type":
        big = {"action_type": "a" * (90 << 20), "target": "tom", "payload": {}}
        (valor / "effects" / "big.json").write_text(json.dumps(big))
    else:
        plan = {"path": "p", "critique_rounds": "x" * (140 << 20), "review_rounds": 0}
        (valor / "plan.json").write_text(json.dumps(plan))


def assert_large_turn(field: str, turn: str, rows: list) -> None:
    [collected] = [
        r["payload"] for r in rows if r["type"] == "turn.collected" and r["payload"]["turn_id"] == turn
    ]
    by_file = {e["file"]: e for e in collected["effects"]}
    assert by_file["ok.json"]["kind"] == "pending"
    [held] = [r["payload"] for r in rows if r["type"] == "effect.held"]
    assert held["request_id"] == f"{turn}/ok.json"
    if field == "action_type":
        [refused] = [r["payload"] for r in rows if r["type"] == "effect.refused"]
        assert refused["reason"] == broker.NO_PERFORMER and len(refused["action_type"]) == 90 << 20
        assert (by_file["big.json"]["kind"], by_file["big.json"]["error"]) == ("refused", broker.NO_PERFORMER)
        assert len(refused["idempotency_key"]) < 100
    else:
        assert not [r for r in rows if r["type"] == "plan.written"]
        assert "plan.json's critique_rounds is a string; each count is 0, 1, or 2" in collected["errors"]
    assert all(len(e) < 1000 for e in collected["errors"])


@pytest.mark.parametrize("field", ["action_type", "critique_rounds"])
def test_a_large_turn_field_is_named_never_copied(dsn, tmp_path, field):
    """A 90 MiB action type with no performer, or a 140 MiB critique_rounds,
    each fits a row once. The kernel's reason, key and errors name the field
    and never repeat it, so the turn is collected with it refused and the
    clean send beside it held."""
    ws, _ = scripted.workspace(tmp_path)
    large_turn(ws, field)
    turn = ledger.new_id()
    with bridges.operator(tmp_path):
        verdict, rows = run(_record_ended(dsn, ws, turn, tasks.machine.State.PLAN, bridges.declared(str(ws))))
    assert verdict == "idle"
    assert_large_turn(field, turn, rows)


def test_a_signal_postgres_refuses_is_unreadable(dsn, tmp_path):
    """A text signal holding a NUL and a `plan.json` nested deeper than the
    server's stack depth (yet within Python's parser) are each answered as
    unreadable with Postgres's reason, and the rest of the turn is
    collected: a clean send beside them is held."""
    ws, _ = scripted.workspace(tmp_path)
    valor = ws / ".valor"
    (valor / "effects").mkdir(parents=True)
    (valor / "question.md").write_text("which one?\x00")
    (valor / "plan.json").write_text('{"p": %s}' % ("[" * 50_000 + "]" * 50_000))
    send = {
        "action_type": "telegram.send_message",
        "target": bridges.OPERATOR_CHAT,
        "payload": {"text": "hi"},
    }
    (valor / "effects" / "ok.json").write_text(json.dumps(send))
    with bridges.operator(tmp_path):
        verdict, rows = run(
            _record_ended(dsn, ws, ledger.new_id(), tasks.machine.State.PLAN, bridges.declared(str(ws)))
        )
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    store = "is unreadable: the ledger's JSON (Postgres jsonb) cannot store it: "
    assert verdict == "idle"
    assert (collected["question"], collected["plan"]) == (None, None)
    assert any(e.startswith(f"question.md {store}UntranslatableCharacter") for e in collected["errors"])
    assert any(e.startswith(f"plan.json {store}") for e in collected["errors"])
    [entry] = collected["effects"]
    assert entry["kind"] == "pending" and entry["request"] == send
    assert not [r for r in rows if r["type"] == "question.asked"]


def test_an_unreadable_signal_reaches_the_turn_collected_errors(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-marker")
    (ws / ".valor").mkdir()
    (ws / ".valor" / "question.md").symlink_to(outside)

    async def go():
        task = await scripted.start(dsn, ws)
        found = signals.collect(ws, "turn-unreadable")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": "turn-unreadable", "state": "plan"})
            await ledger.append(conn, task, "turn.ended",
                                {"turn_id": "turn-unreadable", "outcome": "done", "result": {"session_id": "s"}})  # fmt: skip
            await session.record(
                conn, task, "turn-unreadable", found, state=tasks.machine.State.PLAN, workspace=str(ws)
            )
            return await ledger.read(conn, task)

    rows = run(go())
    collected = [r["payload"] for r in rows if r["type"] == "turn.collected"][-1]
    assert "question.md is a link, not a plain file" in collected["errors"]
    assert "outside-marker" not in json.dumps([r["payload"] for r in rows])


@pytest.mark.parametrize("path", ["docs/plans/p.md", "../outside/fifo"])
def test_a_plan_path_that_is_a_link_to_a_fifo_or_climbs_out_is_never_opened(tmp_path, path):
    """The payload comes from the committed blob; the turn's copy is never
    read, so a link to a FIFO there cannot block the kernel."""
    ws, _ = scripted.workspace(tmp_path)
    (tmp_path / "outside").mkdir()
    os.mkfifo(tmp_path / "outside" / "fifo")
    (ws / "docs" / "plans").mkdir(parents=True)
    (ws / "docs" / "plans" / "p.md").symlink_to(tmp_path / "outside" / "fifo")
    git(ws, "add", "docs/plans/p.md")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "plan")
    got: dict = {}

    def go():
        got["value"] = session._plan(str(ws), {"path": path, "critique_rounds": 1, "review_rounds": 1})

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "_plan blocked"
    plan, why = got["value"]
    if path == "docs/plans/p.md":
        target = str(tmp_path / "outside" / "fifo").encode()
        assert why is None and plan["sha256"] == hashlib.sha256(target).hexdigest()
    else:
        assert plan is None and why == "plan.json's path is not committed at HEAD"


def test_a_plan_changed_after_its_commit_is_no_plan(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.commit(ws, "docs/plans/p.md", "the plan\n", "plan")
    raw = {"path": "docs/plans/p.md", "critique_rounds": 1, "review_rounds": 1}
    plan, why = session._plan(str(ws), raw)
    assert why is None and plan["sha256"] == hashlib.sha256(b"the plan\n").hexdigest()
    (ws / "docs" / "plans" / "p.md").write_text("changed\n")
    assert session._plan(str(ws), raw) == (None, "plan.json's path has changes not committed")


@pytest.mark.parametrize(
    "path,change",
    [
        ("docs/plans/the plan.md", "edit"),
        ("docs/plans/plän.md", "edit"),
        ("docs/plans/p.md", "rename"),
    ],
)
def test_a_plan_git_quotes_or_renames_away_with_changes_not_committed_is_no_plan(tmp_path, path, change):
    """Git quotes a path holding a space or a character outside ASCII and
    names a rename's old path on the left of ` -> `; the plan is checked by
    a literal pathspec, so each is still refused."""
    ws, _ = scripted.workspace(tmp_path)
    scripted.commit(ws, path, "the plan\n", "plan")
    raw = {"path": path, "critique_rounds": 1, "review_rounds": 1}
    assert session._plan(str(ws), raw)[1] is None
    if change == "edit":
        (ws / path).write_text("changed\n")
    else:
        git(ws, "mv", path, "docs/plans/elsewhere.md")
    assert session._plan(str(ws), raw) == (None, "plan.json's path has changes not committed")


@pytest.mark.parametrize("path", ["docs/plans/p.md", "docs/plans/*.md"])
def test_a_plan_is_not_refused_for_changes_to_other_files(tmp_path, path):
    """The pathspec is literal: a plan named `*.md` is not refused for a
    change to another plan it would match as a glob."""
    ws, _ = scripted.workspace(tmp_path)
    scripted.commit(ws, path, "the plan\n", "plan")
    (ws / "docs" / "plans" / "p.md.bak").write_text("other\n")
    (ws / "docs" / "plans" / "q.md").write_text("other\n")
    raw = {"path": path, "critique_rounds": 1, "review_rounds": 1}
    assert session._plan(str(ws), raw)[1] is None


def _hide_an_edit(ws: Path, path: str, route: str) -> None:
    """Change `path` after its commit in a way the turn's own index or
    config keeps out of a plain `git status`."""
    f = ws / path
    if route in ("assume-unchanged", "skip-worktree"):
        git(ws, "update-index", f"--{route}", path)
        f.write_text("changed\n")
    elif route == "stat cache":
        # Only mtime and size are compared, and the index's stat data
        # predates the edit, so git never reads the file again.
        git(ws, "config", "core.checkStat", "minimal")
        git(ws, "config", "core.trustctime", "false")
        before = f.stat().st_mtime - 100
        os.utime(f, (before, before))
        git(ws, "update-index", "--refresh")
        f.write_text(f.read_text().replace("the", "tha"))  # the same size
        os.utime(f, (before, before))
    else:  # file mode
        git(ws, "config", "core.fileMode", "false")
        f.chmod(0o755)
    assert git(ws, "status", "--porcelain", "--", path) == ""


HIDDEN = ["assume-unchanged", "skip-worktree", "stat cache", "file mode"]


@pytest.mark.parametrize("route", HIDDEN)
def test_a_plan_changed_where_the_turns_index_or_config_hides_it_is_no_plan(tmp_path, route):
    """The turn writes its own index and config; the plan is compared with
    HEAD through a fresh index, so neither can hide a change."""
    ws, _ = scripted.workspace(tmp_path)
    path = "docs/plans/p.md"
    scripted.commit(ws, path, "the plan\n", "plan")
    raw = {"path": path, "critique_rounds": 1, "review_rounds": 1}
    assert session._plan(str(ws), raw)[1] is None
    _hide_an_edit(ws, path, route)
    assert session._plan(str(ws), raw) == (None, "plan.json's path has changes not committed")


@pytest.mark.parametrize("route", HIDDEN)
def test_a_candidate_with_a_change_the_turns_index_or_config_hides_is_no_candidate(tmp_path, route):
    ws, _ = scripted.workspace(tmp_path)
    scripted.commit(ws, "app.py", "the code\n", "code")
    assert session._candidate(str(ws), "t1")[1] is None
    _hide_an_edit(ws, "app.py", route)
    candidate, why = session._candidate(str(ws), "t1")
    assert candidate is None and why.startswith("done.md with uncommitted changes") and "app.py" in why


def test_opus_5_5_has_its_own_price_and_one_hour_cache_writes_cost_double_input():
    price = spending.prices("claude-opus-5-5")
    assert price["input"] == 4_000_000 and price["output"] == 20_000_000
    assert spending.prices("claude-opus-5-20260101")["input"] == 5_000_000
    usage = {
        "cache_creation_input_tokens": 1_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 250_000, "ephemeral_1h_input_tokens": 750_000},
    }
    assert spending.cost(usage, price) == round(0.25 * 5_000_000 + 0.75 * 8_000_000)
    assert spending.cost({"cache_creation_input_tokens": 1_000_000}, price) == 8_000_000


def test_a_workspace_turn_resumes_runs_sandboxed_and_carries_no_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    build = claude_code.workspace_turn(
        "Continue.",
        cwd="/w",
        resume="abc",
        model="opus",
        harness={
            "sandbox_profile": "/p.sb",
            "gitconfig": "/g",
            "gh_config_dir": "/gh",
            "env": {"TEST_DB_PORT": "5439"},
        },
    )
    command = build("http://127.0.0.1:4321/t/token", "# Brief", "turn1")
    argv = command.argv
    assert argv[:7] == [
        "/usr/bin/sandbox-exec",
        "-D",
        "GATEWAY_PORT=4321",
        "-D",
        "VALOR_TURN=turn1",
        "-f",
        "/p.sb",
    ]
    assert argv[argv.index("--resume") + 1] == "abc"
    assert argv[argv.index("--system-prompt-snapshot") + 1] == "off"
    assert argv[argv.index("--append-system-prompt") + 1] == "# Brief"
    assert "GH_TOKEN" not in command.env and "SSH_AUTH_SOCK" not in command.env
    assert command.env["GH_CONFIG_DIR"] == "/gh" and command.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert command.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321/t/token"
    assert command.env["TEST_DB_PORT"] == "5439"
    assert command.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


def test_a_workspace_turn_without_a_sandbox_profile_is_refused_before_anything_runs(dsn, tmp_path):
    for harness in (None, {}, {"gitconfig": "/g"}, {"sandbox_profile": ""}):
        with pytest.raises(claude_code.Unsandboxed):
            claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness)

    async def start():
        async with await db.connect(dsn) as conn:
            return await tasks.start(
                conn,
                tasks.Brief(instruction="x", workspace=str(tmp_path)),
            )

    task = run(start())
    jev, ow = judgement_upstream.shared().urls(fixed="precise")
    out = subprocess.run(
        [sys.executable, "-m", "core", "run", task],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB, "VALOR_JEV_URL": jev, "VALOR_OPEN_WEIGHT_URL": ow},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode != 0 and "sandbox profile" in out.stderr

    async def rows():
        async with await db.connect(dsn) as conn:
            return [r["type"] for r in await ledger.read(conn, task)]

    got = run(rows())  # the judge ran through the local upstream; no turn started
    assert got[0] == "task.started" and "judge.decided" in got and "turn.started" not in got
    assert not (tmp_path / ".valor").exists()


def test_a_push_runs_nothing_the_workspace_config_or_hooks_name(tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    marker = tmp_path / "ran"
    hook = f"#!/bin/sh\necho $0 >> {marker}\n"
    for path in (ws / ".git" / "hooks" / "pre-push", tmp_path / "hooks" / "pre-push"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(hook)
        path.chmod(0o755)
    receive = tmp_path / "receive"
    receive.write_text(f'#!/bin/sh\necho receive >> {marker}\nexec git-receive-pack "$@"\n')
    receive.chmod(0o755)
    head = git(ws, "rev-parse", "HEAD").strip()

    # A hook in the default directory is pinned off; a hooks path or a
    # receive program in the workspace's config refuses the push outright.
    pushed = asyncio.run(
        PushBranch(ws).perform(broker.Action("push_branch", "valor/x", {"head_sha": head}), "key")
    )
    assert pushed["sha"] == head and git(origin, "rev-parse", "valor/x").strip() == head
    git(ws, "config", "core.hooksPath", str(tmp_path / "hooks"))
    git(ws, "config", "remote.origin.receivepack", str(receive))
    with pytest.raises(ValueError, match="core.hookspath"):
        asyncio.run(
            PushBranch(ws).perform(broker.Action("push_branch", "valor/y", {"head_sha": head}), "key")
        )
    assert not marker.exists()


def test_a_request_that_starts_with_a_dash_reaches_claude_as_the_prompt(tmp_path):
    """Tom's request for pso-a began "- Create new flag ..."; as a bare argv
    element after `-p`, claude rejected it ("unknown option") and the turn
    failed before any model call. The prompt now follows `--`. The real CLI
    parses it: pointed at a dead gateway, it is still retrying after two
    seconds, not exiting on an option error."""
    request = "- Create new flag, separate from the old one"
    for build in (
        claude_code.turn(request, cwd=str(tmp_path)),
        claude_code.workspace_turn(
            request, cwd=str(tmp_path), resume="abc", harness={"sandbox_profile": "/p.sb"}
        ),
    ):
        argv = build("http://127.0.0.1:9/t/x", "# Brief", "turn1").argv
        assert argv[-2:] == ["--", request]
    argv = claude_code.turn(request, cwd=str(tmp_path))("http://127.0.0.1:9/t/x", "# Brief", "turn1").argv
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}
    proc = subprocess.Popen(
        argv, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        out, _ = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    assert "unknown option" not in out


def test_done_with_uncommitted_changes_names_every_file(tmp_path):
    from tests import scripted

    repo = scripted.toy_repo(tmp_path)
    names = [f"f{i:02}.txt" for i in range(30)]
    for name in names:
        (repo / name).write_text("x\n")
    candidate, why = session._candidate(str(repo), "t")
    assert candidate is None and all(name in why for name in names)


def test_a_failing_git_call_raises_with_gits_whole_stderr(tmp_path):
    from core import git
    from tests import scripted

    repo = scripted.toy_repo(tmp_path)
    ref = "x" * 400
    with pytest.raises(git.GitError) as failed:
        git.trusted(repo, "show", "--quiet", "--format=%H", ref)
    assert str(failed.value).endswith(f"'{ref}': File name too long")  # past the first 300 characters


def test_a_stopped_turn_record_kills_the_mirror_fetch_and_the_loop_runs_meanwhile(dsn, tmp_path, monkeypatch):
    """The fetch into the kernel mirror runs in a worker thread: the loop
    keeps running while it does, and stopping `record` kills the fetch's
    whole process group (here a fetch that never ends, run as the real
    fetch runs, through `workspace.bounded`)."""
    import types

    from core import workspace as kws

    ws, _ = scripted.workspace(tmp_path)
    scripted.commit(ws, "docs/plan.md", "a plan\n", "Plan")
    (ws / ".valor").mkdir(exist_ok=True)
    (ws / ".valor" / "plan.json").write_text(
        json.dumps(
            {"path": "docs/plan.md", "stakes": "s", "critique_rounds": 1, "review_rounds": 1, "scope": []}
        )
    )
    pidfile = tmp_path / "pid"

    def never_ends(mirror, source, sha, ref, profile, mark, **kw):
        argv = ["/bin/sh", "-c", f"/bin/sleep 30 & echo $! > {pidfile}; wait"]
        kws.bounded(argv, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, max_bytes=1024, max_footprint=1024**3)

    monkeypatch.setattr(session.workspace, "fetch_into_mirror", never_ends)
    brief = types.SimpleNamespace(
        mirror=str(tmp_path / "mirror"), workspace=str(ws), harness={"sandbox_profile": ""}
    )

    async def go():
        found = signals.collect(ws, "turn-1")
        async with await db.connect(dsn) as conn:
            recording = asyncio.create_task(
                session.record(
                    conn, "t", "turn-1", found, state=tasks.machine.State.PLAN, workspace=str(ws), brief=brief
                )
            )
            while (
                not pidfile.exists() or not pidfile.read_text().strip()
            ):  # the loop runs while the fetch does
                assert not recording.done(), recording
                await asyncio.sleep(0.05)
            recording.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recording
        return int(pidfile.read_text())

    child = run(go())
    for _ in range(200):  # the group was sent SIGKILL; wait for the kernel to retire the child
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the fetch outlived its stopped caller")


class Refusing:
    """A performer whose refusal is as long as the request asks."""

    action_type = "refusing"
    effect_class = "propose"

    async def refuse(self, conn, action):
        return "r" * action.payload["n"]


def _refusing_turn(ws: Path, sizes: dict[str, int]):
    effects = ws / ".valor" / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    send = {
        "action_type": "telegram.send_message",
        "target": bridges.OPERATOR_CHAT,
        "payload": {"text": "hi"},
    }
    (effects / "ok.json").write_text(json.dumps(send))
    for name, n in sizes.items():
        (effects / name).write_text(
            json.dumps({"action_type": "refusing", "target": "tom", "payload": {"n": n}})
        )
    return broker.Performers(Refusing(), *bridge.declared_performers(str(ws)))


def test_a_refusal_the_ledger_cannot_store_is_refused_in_kernel_words(dsn, tmp_path):
    """A performer's reason past what jsonb holds: the broker's row is
    written with no turn content and Postgres's reason, and the turn is
    collected with the clean send beside it held."""
    ws, _ = scripted.workspace(tmp_path)
    performers = _refusing_turn(ws, {"big.json": 260 << 20})
    with bridges.operator(tmp_path):
        verdict, rows = run(_record_ended(dsn, ws, ledger.new_id(), tasks.machine.State.PLAN, performers))
    [refused] = [r["payload"] for r in rows if r["type"] == "effect.refused"]
    assert set(refused) <= {"effect_id", "reason", *broker.BARE_FIELDS}
    assert refused["reason"].startswith(f"{broker.UNSTORABLE}: ProgramLimitExceeded")
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    by_file = {e["file"]: e for e in collected["effects"]}
    assert (by_file["big.json"]["kind"], by_file["big.json"]["error"]) == ("refused", refused["reason"])
    assert by_file["ok.json"]["kind"] == "pending" and verdict == "idle"


def test_a_turn_collected_row_past_what_jsonb_holds_is_written_bare(dsn, tmp_path):
    """Two refusals each stored on their own are together more than one
    jsonb value holds: `turn.collected` is written with no turn content and
    Postgres's reason, and each effect row stands."""
    ws, _ = scripted.workspace(tmp_path)
    performers = _refusing_turn(ws, {"a.json": BIG, "b.json": BIG})
    with bridges.operator(tmp_path):
        verdict, rows = run(_record_ended(dsn, ws, ledger.new_id(), tasks.machine.State.PLAN, performers))
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    [why] = collected["errors"]
    assert why.startswith(f"the turn's signals: {session.UNSTORABLE} with all it holds: ProgramLimitExceeded")
    assert collected["effects"] == [] and verdict == "idle"
    assert len([r for r in rows if r["type"] == "effect.refused"]) == 2
    assert len([r for r in rows if r["type"] == "effect.held"]) == 1


def test_an_error_the_ledger_cannot_store_is_answered_in_its_place(dsn, tmp_path, monkeypatch):
    """An error in `turn.collected` that jsonb refuses on its own is
    replaced by kernel text with Postgres's reason, and the rest of the
    row is written."""
    ws, _ = scripted.workspace(tmp_path)
    (ws / ".valor").mkdir()
    (ws / ".valor" / "question.md").write_text("which one?")
    verdict_of = session._verdict

    def with_nul(*args, **kw):
        verdict, candidate, errors = verdict_of(*args, **kw)
        return verdict, candidate, [*errors, "a\x00b"]

    monkeypatch.setattr(session, "_verdict", with_nul)
    verdict, rows = run(_record_ended(dsn, ws, ledger.new_id(), tasks.machine.State.PLAN))
    [collected] = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    assert verdict == "asked" and collected["question"] == "which one?"
    assert collected["errors"][-1].startswith(
        f"an error is unrecorded: {session.UNSTORABLE}: UntranslatableCharacter"
    )


def test_a_plan_path_too_long_for_gits_arguments_is_no_plan(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    plan = {"path": "p" * (2 << 20), "critique_rounds": 0, "review_rounds": 0}
    assert session._plan(str(ws), plan) == (
        None,
        "plan.json's path cannot be passed to git: Argument list too long",
    )
