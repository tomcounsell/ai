"""The decision board: one Slack canvas per project, of the decisions open
for Tom on that project (Tom, 2026-10-27; docs/plans/decision-board.md).

The ledger is the only record. A decision is a task's `question.asked`
row and its answer the `question.answered` row (`session.answer`, with
`by` and `via`); the canvas is a view of them, rebuilt by the chat session
of the channel the spec names as its `board` (the board session), and
whatever order or grouping it shows is presentation.

`owe(conn)` runs on every wake (`core/serve.py`). For each project of this
Mac whose `board` chat this Mac owns, it reads the snapshot: each open
question of the project's tasks (a task waiting on it), and each answer of
the last `DECIDED_DAYS` days. When the snapshot's digest differs from the
one on the latest board row, it writes a `child.reported` row with kind
`board` and no `child_id` on the board session, started when none is open,
which wakes it; the row's text is the snapshot, with each question's board
post and the canvas's link, which the digest leaves out. Projects never
mix: a snapshot reads its own project's tasks only.

A question whose task reports to the board session gets no `question`
report there (`notices.owe` asks `here`): the board row carries it, and
the session posts it once in the channel, the post Tom reacts to. A
question relayed elsewhere (his DM, the operator chat) is listed as asked
there, with no post.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from core import intake, ledger, machine, notices, tasks, workspace
from core.machine import State

DECIDED_DAYS = 14


def spec_of(name: str | None) -> workspace.Spec | None:
    try:
        return workspace.Spec.load(name) if name else None
    except workspace.Refused:
        return None


async def session_of(conn, spec: workspace.Spec | None) -> str | None:
    """The project's board session: its board chat's open session."""
    if spec is None or spec.board is None:
        return None
    channel, _, chat_id = spec.board.partition(":")
    return await intake.chat_session(conn, channel, chat_id)


async def here(conn, task_id: str) -> bool:
    """Whether the task reports to its project's board session: a task the
    board chat's session started."""
    b = await tasks.brief(conn, task_id)
    return b.parent_id is not None and b.parent_id == await session_of(
        conn, spec_of((b.project or {}).get("name"))
    )


async def answers(conn, session_id: str, task_id: str) -> bool:
    """Whether `session_id` is the board session of `task_id`'s project and
    the task waits on its question: the board session answers it, by chat,
    reaction or call, though the task reports elsewhere."""
    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return False
    if session_id != await session_of(conn, spec_of((b.project or {}).get("name"))):
        return False
    return machine.fold(await ledger.read(conn, task_id)).state is State.WAITING


async def snapshot(conn, spec: workspace.Spec) -> dict[str, Any]:
    """The project's open questions and recent answers, oldest first, each
    with its task and the task's request."""
    since = datetime.now(UTC) - timedelta(days=DECIDED_DAYS)
    rows = await (
        await conn.execute(
            "SELECT e.task_id, e.type, e.payload, e.at, d.body->>'instruction' FROM events e "
            "JOIN documents d ON d.kind = 'task' AND d.id = e.task_id "
            "WHERE d.body->'project'->>'name' = %s AND e.type IN ('question.asked', 'question.answered') "
            "ORDER BY e.id",
            (spec.name,),
        )
    ).fetchall()
    asked: dict[tuple[str, str], dict[str, Any]] = {}
    answered: list[tuple[str, dict[str, Any], datetime]] = []
    requests: dict[str, str | None] = {}
    for task_id, kind, p, at, instruction in rows:
        requests[task_id] = notices.short(instruction)
        if kind == "question.asked":
            asked[(task_id, p.get("question_id"))] = {"text": p.get("text", ""), "at": at.isoformat()}
        else:
            answered.append((task_id, p, at))
    done = {(t, p.get("question_id")) for t, p, _ in answered}
    open_ = []
    for (task_id, qid), q in asked.items():
        if (task_id, qid) in done:
            continue
        f = machine.fold(await ledger.read(conn, task_id))
        if f.state is not State.WAITING or f.open_question != qid:
            continue
        open_.append(
            {
                "task_id": task_id,
                "request": requests[task_id],
                "question_id": qid,
                "question": q["text"],
                "asked_at": q["at"],
                "asked_here": await here(conn, task_id),
            }
        )
    decided = []
    for t, p, at in answered:
        if at < since:
            continue
        said = p.get("provenance") or {}
        decided.append(
            {
                "task_id": t,
                "request": requests[t],
                "question_id": p.get("question_id"),
                "question": (asked.get((t, p.get("question_id"))) or {}).get("text", ""),
                "answer": p.get("text", ""),
                "by": said.get("by"),
                "via": said.get("via"),
                "at": at.isoformat(),
            }
        )
    return {"open": open_, "decided": decided}


def digest(snap: dict[str, Any]) -> str:
    return hashlib.sha256(ledger.canonical(snap)).hexdigest()


async def _posts(conn, chat_id: str) -> dict[str, str]:
    """question id -> the `ts` of the board post the session made for it
    (a `slack.send_message` to the board chat with `about`)."""
    rows = await (
        await conn.execute(
            "SELECT i.payload->'payload'->>'about', o.payload->'result'->'sent'->0->>'message_id' "
            "FROM events i JOIN events o ON o.type = 'effect.outcome' "
            "AND o.payload->>'effect_id' = i.payload->>'effect_id' AND o.payload->>'kind' = 'done' "
            "WHERE i.type = 'effect.intent' AND i.payload->>'action_type' = 'slack.send_message' "
            "AND i.payload->>'target' = %s AND i.payload->'payload' ? 'about' ORDER BY i.id",
            (chat_id,),
        )
    ).fetchall()
    return {about.removeprefix("question:"): ts for about, ts in rows if about and ts}


async def _canvas(conn, chat_id: str) -> dict[str, Any]:
    """The board canvas's last write: its `url` and `readers`, the
    channel's members then; {} before the first."""
    row = await (
        await conn.execute(
            "SELECT o.payload->'result' FROM events i JOIN events o ON o.type = 'effect.outcome' "
            "AND o.payload->>'effect_id' = i.payload->>'effect_id' AND o.payload->>'kind' = 'done' "
            "WHERE i.type = 'effect.intent' AND i.payload->>'action_type' = 'slack.write_canvas' "
            "AND i.payload->>'target' = %s ORDER BY o.id DESC LIMIT 1",
            (chat_id,),
        )
    ).fetchone()
    return row[0] if row else {}


def text(board: str, snap: dict[str, Any], posts: dict[str, str], canvas: dict[str, Any]) -> str:
    """The board row as the board session reads it."""
    readers = canvas.get("readers")
    lines = [
        f"Board chat {board}; canvas: {canvas.get('url') or 'none written yet'}.",
        f"Who reads it: {', '.join(readers) if readers else 'not known until the first write'}.",
        "",
        "Open:",
    ]
    for q in snap["open"]:
        if q["question_id"] in posts:
            post = f"your post {posts[q['question_id']]}"
        elif q["asked_here"]:
            post = "no post yet: post it here"
        else:
            post = "asked in another chat (Tom's DM): no post here"
        head = f'- question {q["question_id"]} of task {q["task_id"]} ("{q["request"] or ""}"), asked {q["asked_at"]}'
        lines += [f"{head}; {post}", *("  " + line for line in str(q["question"]).splitlines())]
    lines += ["" if snap["open"] else "- none", "", f"Decided (last {DECIDED_DAYS} days):"]
    for d in snap["decided"]:
        head = f'- question {d["question_id"]} of task {d["task_id"]} ("{d["request"] or ""}")'
        lines.append(f"{head}, answered {d['at']} by {d['by']} via {d['via']}: {json.dumps(d['answer'])}")
        lines += ["  asked: " + line for line in str(d["question"]).splitlines()[:1]]
    if not snap["decided"]:
        lines.append("- none")
    return "\n".join(lines)


async def owe(conn) -> list[str]:
    """Write a board row for each of this Mac's boards whose snapshot
    changed; the boards it wrote."""
    wrote = []
    for spec in workspace.Spec.here():
        if spec.board is None:
            continue
        channel, _, chat_id = spec.board.partition(":")
        if not intake.owns(channel, chat_id):
            continue
        snap = await snapshot(conn, spec)
        now = digest(snap)
        async with conn.transaction():
            await ledger.lock(conn, f"board:{spec.board}")
            last = await (
                await conn.execute(
                    "SELECT id, payload->>'digest' FROM events WHERE type = 'child.reported' "
                    "AND payload->>'kind' = 'board' AND payload->>'board' = %s ORDER BY id DESC LIMIT 1",
                    (spec.board,),
                )
            ).fetchone()
            if (last and last[1] == now) or (last is None and not snap["open"] and not snap["decided"]):
                continue
            session_id, brief = await intake.open_session(conn, channel, chat_id, spec, own=False)
            if brief is not None:
                await tasks.start(conn, brief, by="valor", via="kernel")
            body = text(spec.board, snap, await _posts(conn, chat_id), await _canvas(conn, chat_id))
            row = {"kind": "board", "about_key": f"board:{last[0] if last else 0}", "board": spec.board}
            await ledger.append(conn, session_id, "child.reported", {**row, "digest": now, "text": body})
        wrote.append(spec.board)
    return wrote
