"""A task's turns in one harness session: run until Valor asks Tom a
question, delivers, runs out of money, or is stopped.

Serves Mission item 6, spend attention as carefully as money. Every question
Valor puts to Tom is a `question.asked` row and his answer a
`question.answered` row with his provenance, and his feedback on a delivery a
`feedback.given` row with his provenance, so the attention a task cost is a
fold over its ledger (`tasks.status` returns it as `attention`).

Feedback is how Tom, as project manager, sends a delivered task back to
work: it takes only a delivered task, puts it back to `live`, and the next
run resumes the same session with the feedback as its prompt. A stopped task
takes none (stop is final), and a task waiting on a question takes Tom's
answer instead.

Each turn resumes the harness session the task's first turn opened, so Valor
keeps its working context, and each turn still gets the Brief rendered from
the ledger as it starts, corrections included. Money is conserved across all
of a task's turns because every model call of every turn goes through the
same gateway against the same committed budget.

The turn's prompt is the instruction on the first turn, Tom's answer after a
question, his feedback after a delivery, and "Continue." otherwise, each
followed by what became of the effects the previous turn requested. An
answer or feedback is spent only by a turn that finishes: after a turn that
fails or is stopped, the next one opens with it again. What a turn left under `.valor/` (see
`core.signals`) is recorded in one `turn.collected` row, effect requests go
to the broker, and a question or delivery gets its own row.
"""

from collections.abc import Callable
from typing import Any

from core import broker, db, ledger, runs, signals, tasks
from core.gateway import Gateway
from core.settings import settings

# Builds one turn's command: (prompt, session to resume or None, Brief).
TurnFor = Callable[[str, str | None, tasks.Brief], Callable[[str, str], runs.TurnCommand]]


async def run(gateway: Gateway, task_id: str, turn_for: TurnFor, dsn: str | None = None) -> dict[str, Any]:
    """Run turns until there is something for Tom. Returns `status` (one of
    `waiting`, `delivered`, `stopped`, `budget exhausted`, `failed`, `idle`)
    and what goes with it."""
    dsn = dsn or gateway.dsn
    idle = 0
    while True:
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task_id)
            if state["state"] != "live":
                return _settled(state)
            if state["remaining_usd_micros"] <= 0:
                return {"status": "budget exhausted", "state": state}
            b = await tasks.brief(conn, task_id)
            prompt, resume = await next_prompt(conn, task_id)
        try:
            ended = await runs.run_turn(gateway, task_id, turn_for(prompt, resume, b), dsn=dsn)
        except tasks.TaskStopped:
            return {"status": "stopped"}
        found = signals.collect(b.workspace, ended["turn_id"]) if b.workspace else signals.Signals()
        async with await db.connect(dsn) as conn:
            await record(conn, task_id, ended["turn_id"], found)
            state = await tasks.status(conn, task_id)
            refused = await _refused_in_turn(conn, task_id, ended["turn_id"])
        if state["state"] != "live":
            return _settled(state, ended)
        if refused:
            return {"status": "budget exhausted", "state": state, "turn": ended}
        if ended["outcome"] != "done" or ended["result"].get("is_error"):
            return {"status": "failed", "state": state, "turn": ended}
        idle += 1
        if idle >= settings.idle_turns:
            return {"status": "idle", "state": state, "turn": ended}


async def record(conn, task_id: str, turn_id: str, found: signals.Signals) -> None:
    """Ledger what a turn left, sending each effect request to the broker."""
    effects = []
    for entry in found.effects:
        if "request" in entry:
            r = entry["request"]
            outcome = await broker.request(
                conn, task_id, broker.Action(r["action_type"], r["target"], r["payload"])
            )
            entry = {**entry, "effect_id": outcome.effect_id, "kind": outcome.kind, "error": outcome.error}
        effects.append(entry)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        await ledger.append(
            conn,
            task_id,
            "turn.collected",
            {"turn_id": turn_id, "question": found.question, "done": found.done, "effects": effects},
        )
        if found.question is not None:
            await ledger.append(
                conn,
                task_id,
                "question.asked",
                {"question_id": ledger.new_id(), "turn_id": turn_id, "text": found.question},
            )
        elif found.done is not None:
            await ledger.append(conn, task_id, "task.delivered", {"turn_id": turn_id, "summary": found.done})


async def answer(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Record the answer to the task's open question. `by` names who wrote
    it and `role_played` says whether they stood in for Tom. Returns its id."""
    text = text.strip()
    if not text:
        raise ValueError("an answer has text")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        question = await open_question(conn, task_id)
        if question is None:
            raise LookupError(f"task {task_id} has no open question")
        await ledger.append(
            conn,
            task_id,
            "question.answered",
            {
                "question_id": question["question_id"],
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return question["question_id"]


async def feedback(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Record feedback on the task's latest delivery, which puts the task
    back to work. `by` and `role_played` are as for `answer`. Returns the
    feedback's id."""
    text = text.strip()
    if not text:
        raise ValueError("feedback has text")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        state = await tasks.status(conn, task_id)
        if state["state"] == "stopped":
            raise LookupError(f"task {task_id} is stopped; a stopped task takes no feedback")
        if state["state"] == "waiting for Tom":
            raise LookupError(f"task {task_id} has an open question; answer it with `answer`")
        if state["state"] != "delivered":
            raise LookupError(f"task {task_id} has not delivered; feedback is on a delivery")
        feedback_id = ledger.new_id()
        await ledger.append(
            conn,
            task_id,
            "feedback.given",
            {
                "feedback_id": feedback_id,
                "on_delivery": state["delivered"],
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return feedback_id


async def open_question(conn, task_id: str) -> dict[str, Any] | None:
    state = await tasks.status(conn, task_id)
    unanswered = [q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None]
    return unanswered[-1] if unanswered else None


async def next_prompt(conn, task_id: str) -> tuple[str, str | None]:
    """The next turn's prompt and the harness session it resumes."""
    rows = await ledger.read(conn, task_id)
    session = None
    last = None
    answered = None
    feedback = None
    for row in rows:
        p = row["payload"]
        if row["type"] == "turn.ended":
            result = p.get("result") or {}
            session = result.get("session_id") or session
            if p["outcome"] == "done" and not result.get("is_error"):
                answered = feedback = None
        elif row["type"] == "turn.collected":
            last = p
        elif row["type"] == "question.answered":
            answered = p["text"]
        elif row["type"] == "feedback.given":
            feedback = p["text"]
    if session is None:
        return (await tasks.brief(conn, task_id)).instruction, None
    if feedback is not None:
        prompt = (
            "Tom reviewed your delivery and, as project manager, sends it back with this feedback:\n\n"
            f"{feedback}\n\n"
            "Act on it in this workspace. When the work is ready again, write a new `.valor/done.md`."
        )
    elif answered is not None:
        prompt = f"Tom answered your question:\n\n{answered}"
    else:
        prompt = "Continue."
    report = _effects_report(last, (await tasks.status(conn, task_id))["effects"])
    return (f"{prompt}\n\n{report}" if report else prompt), session


def _effects_report(collected: dict[str, Any] | None, now: dict[str, str]) -> str:
    """What became of the last turn's effect requests, as the ledger has
    them now (a held push Tom has since released reads as done)."""
    if not collected or not collected["effects"]:
        return ""
    lines = ["What became of the effects you requested last turn:"]
    for e in collected["effects"]:
        if "error" in e and "effect_id" not in e:
            lines.append(f"- {e['file']}: {e['error']}")
            continue
        r = e["request"]
        kind = now.get(e["effect_id"], e["kind"])
        said = {
            "pending": "held for Tom's approval",
            "done": "done",
            "refused": f"refused: {e['error']}",
            "failed": f"failed: {e['error']}",
        }.get(kind, kind)
        lines.append(f"- {r['action_type']} -> {r['target']} (effect {e['effect_id']}): {said}")
    return "\n".join(lines)


def _settled(state: dict[str, Any], turn: dict[str, Any] | None = None) -> dict[str, Any]:
    status = {"stopped": "stopped", "delivered": "delivered", "waiting for Tom": "waiting"}[state["state"]]
    out = {"status": status, "state": state}
    if turn is not None:
        out["turn"] = turn
    if status == "waiting":
        out["question"] = next(
            q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None
        )
    return out


async def _refused_in_turn(conn, task_id: str, turn_id: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'gateway.refused' "
            "AND payload->>'turn_id' = %s AND payload->>'reason' <> 'task stopped'",
            (task_id, turn_id),
        )
    ).fetchone()
    return row is not None
