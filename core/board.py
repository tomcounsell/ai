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
mix: a snapshot reads its own project's tasks only, and those of the
projects its spec lists in `projects`, whose repositories its chats work
in (`board_spec`).

A question whose task reports to the board session gets no `question`
report there (`notices.owe` asks `here`): the board row carries it, and
the session posts it once in the channel, the post Tom reacts to, unless
it is already asking Tom that question in the chat (a thread or a reply),
where the canvas points instead. A question relayed elsewhere (his DM, the operator chat) is listed as asked
there, with no post. A question open `STALE_DAYS` or more is `stale`, which
moves the digest the wake it crosses, so the board session is told. Asked
here, it posts once whether it is still worth doing (`"about":
"stale:<id>"`), and Tom's 👎 on that post stops the task. Asked elsewhere,
that chat's session asks it (`notices.owe`, Tom 2026-11-06: "shouldn't that
come through the DM tho?") and the canvas points there. The board session
may stop any task of its project (`covers`).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from core import intake, ledger, machine, notices, tasks, workspace
from core.machine import State

DECIDED_DAYS = 14
STALE_DAYS = 7


def spec_of(name: str | None) -> workspace.Spec | None:
    try:
        return workspace.Spec.load(name) if name else None
    except workspace.Refused:
        return None


def board_spec(name: str | None) -> workspace.Spec | None:
    """The spec whose board covers project `name`: its own when it names a
    board, else the first of this Mac's specs, by name, that lists it in
    `projects` and names a board."""
    spec = spec_of(name)
    if spec is not None and spec.board is not None:
        return spec
    return next((s for s in workspace.Spec.here() if name in s.projects and s.board is not None), spec)


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
        conn, board_spec((b.project or {}).get("name"))
    )


async def covers(conn, session_id: str, task_id: str) -> bool:
    """Whether `session_id` is the board session of `task_id`'s project: it
    stops the task, though the task reports elsewhere."""
    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return False
    return session_id == await session_of(conn, board_spec((b.project or {}).get("name")))


async def answers(conn, session_id: str, task_id: str) -> bool:
    """Whether `session_id` covers `task_id` and the task waits on its
    question: the board session answers it, by chat, reaction or call."""
    if not await covers(conn, session_id, task_id):
        return False
    return machine.fold(await ledger.read(conn, task_id)).state is State.WAITING


async def snapshot(conn, spec: workspace.Spec) -> dict[str, Any]:
    """The project's open questions and recent answers, and its listed
    projects', oldest first, each with its task and the task's request."""
    now = datetime.now(UTC)
    since, stale = now - timedelta(days=DECIDED_DAYS), now - timedelta(days=STALE_DAYS)
    rows = await (
        await conn.execute(
            "SELECT e.task_id, e.type, e.payload, e.at, d.body->>'instruction' FROM events e "
            "JOIN documents d ON d.kind = 'task' AND d.id = e.task_id "
            "WHERE d.body->'project'->>'name' = ANY(%s) AND e.type IN ('question.asked', 'question.answered') "
            "ORDER BY e.id",
            ([spec.name, *spec.projects],),
        )
    ).fetchall()
    asked: dict[tuple[str, str], dict[str, Any]] = {}
    answered: list[tuple[str, dict[str, Any], datetime]] = []
    requests: dict[str, str | None] = {}
    for task_id, kind, p, at, instruction in rows:
        requests[task_id] = notices.short(instruction)
        if kind == "question.asked":
            asked[(task_id, p.get("question_id"))] = {"text": p.get("text", ""), "at": at}
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
                "asked_at": q["at"].isoformat(),
                "asked_here": await here(conn, task_id),
                "stale": q["at"] < stale,
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
    """`about` (`question:<id>`, `stale:<id>`) -> the `ts` of the board post
    the session made for it (a `slack.send_message` to the board chat)."""
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
    return {about: ts for about, ts in rows if about and ts}


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


async def canvas_markdown(conn, spec: workspace.Spec) -> str | None:
    """The markdown of the board canvas's last write that left, as the
    board session wrote it; None before the first, or with no board."""
    if spec.board is None:
        return None
    row = await (
        await conn.execute(
            "SELECT i.payload->'payload'->>'markdown' FROM events i JOIN events o ON o.type = 'effect.outcome' "
            "AND o.payload->>'effect_id' = i.payload->>'effect_id' AND o.payload->>'kind' = 'done' "
            "WHERE i.type = 'effect.intent' AND i.payload->>'action_type' = 'slack.write_canvas' "
            "AND i.payload->>'target' = %s ORDER BY o.id DESC LIMIT 1",
            (spec.board.partition(":")[2],),
        )
    ).fetchone()
    return row[0] if row else None


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
        mine, asked = posts.get(f"stale:{q['question_id']}"), posts.get(f"question:{q['question_id']}")
        if q["stale"] and not q["asked_here"]:
            post = (
                f"unanswered {STALE_DAYS}+ days: asked whether still worth doing in the chat it was asked in "
                "(e.g. Tom's DM), whose session keeps or stops it: no post here"
            )
        elif q["stale"] and mine:
            post = (
                f"unanswered {STALE_DAYS}+ days, asked whether still worth doing in your post "
                f"{mine}: 👍 keeps it waiting, 👎 stops it"
            )
        elif q["stale"]:
            post = (
                f"unanswered {STALE_DAYS}+ days: post here asking whether it is still worth doing, with your "
                f'recommendation, "about": "stale:{q["question_id"]}"'
            )
        elif asked:
            post = f"your post {asked}"
        elif q["asked_here"]:
            post = (
                "no board post: if you are already asking Tom this in this chat (a thread or a reply), "
                "the canvas line points there; else post it here"
            )
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
