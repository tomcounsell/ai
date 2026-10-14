"""Reactions: 👀 on a message Valor acts on, then the shape of the ideal
response (docs/plans/3624532d-reactions.md).

Binding (`core/intake.py`) calls `on_bound` in the binding's transaction.
A Telegram or Slack message bound to a task as `start`, `answer`,
`feedback`, or `steer` gets a `seen` reaction, 👀. A `stop` is not judged:
its ideal response is the acknowledgement, so it gets the `shape` reaction
👌 at once, with `judged: false`. A message bound `none`, and any message on
a channel that does not react (email, local), gets nothing.

The resident kernel (`core/serve.py`) then asks the `response.shape`
judgement once for each `seen` reaction with no `shape` (`pending`,
`shape`). The judgement is metered on the bound task. The label it picks
names the `shape` reaction's emoji, which replaces 👀. An abstain, or a
judgement both legs failed `judgement.UNANSWERED_RUNS` times, writes
nothing, so 👀 stays. A task stopped before its judgement runs gets 👌 with
`judged: false`, so no message shows 👀 for good. The label picks an emoji
and nothing reads it for anything else.

The bridge places each `reaction.requested` row through its outbox
(`core/bridge.py`, `ReactionDue`) with its own account, as it sends
notices, and writes `reaction.placed` or `reaction.failed`. There is no
broker effect, approval, or report: a reaction is a reversible mark in a
chat Tom owns and carries no text. Every reaction row is quiet
(`ledger.quiet`), so none moves the task it sits on.

Each emoji is the exact code points Telegram's standard reaction list
holds: none carries the U+FE0F variation selector, which Telegram can
refuse.
"""

from typing import Any

import psycopg

from core import channels, judgement, ledger, tasks
from core.judgement_tasks import SHAPE

SEEN = "\U0001f440"  # 👀
EMOJI = {
    "short_answer": "\u26a1",  # ⚡
    "code_change": "\U0001f468\u200d\U0001f4bb",  # 👨‍💻
    "written_report": "\u270d",  # ✍
    "question_back": "\U0001f914",  # 🤔
    "acknowledge": "\U0001f44c",  # 👌
    "decline": "\U0001f937",  # 🤷
}
# What a message is bound as for it to get 👀 and a judgement.
JUDGED = ("start", "answer", "feedback", "steer")


async def on_bound(conn, p: dict[str, Any], task_id: str | None, as_: str) -> None:
    """The reaction a binding owes, in the binding's transaction: `seen` for
    a message the shape judgement will read, 👌 at once for a `stop`."""
    row = channels.get(p["channel"])
    if task_id is None or row is None or not row.reacts:
        return
    if as_ in JUDGED:
        await _request(conn, task_id, p, SEEN, "seen")
    elif as_ == "stop":
        await _request(conn, task_id, p, EMOJI["acknowledge"], "shape", judged=False, reason="stop")


async def _request(conn, task_id: str, p: dict[str, Any], emoji: str, stage: str, **extra) -> str | None:
    """One `reaction.requested` row; its id, or None when this message
    already has a reaction at this stage."""
    reaction_id = ledger.new_id()
    try:
        async with conn.transaction():
            await ledger.append(
                conn,
                task_id,
                "reaction.requested",
                {
                    "reaction_id": reaction_id,
                    "received_id": p["received_id"],
                    "channel": p["channel"],
                    "chat_id": p["chat_id"],
                    "message_id": p["message_id"],
                    "emoji": emoji,
                    "stage": stage,
                    **extra,
                },
            )
    except psycopg.errors.UniqueViolation:
        return None
    return reaction_id


async def pending(conn) -> list[dict[str, Any]]:
    """Every `seen` reaction whose message has no `shape` reaction, no
    answered `response.shape` judgement, and fewer than
    `judgement.UNANSWERED_RUNS` failed ones: each its row's payload with
    `task_id`, oldest first."""
    rows = await (
        await conn.execute(
            "SELECT s.task_id, s.payload FROM events s WHERE s.type = 'reaction.requested' "
            "AND s.payload->>'stage' = 'seen' AND NOT EXISTS (SELECT 1 FROM events h "
            "WHERE h.type = 'reaction.requested' AND h.payload->>'stage' = 'shape' "
            "AND h.payload->>'received_id' = s.payload->>'received_id') "
            "AND NOT EXISTS (SELECT 1 FROM events a WHERE a.task_id = s.task_id "
            "AND a.type = 'judgement.answered' AND a.payload->>'site' = %s "
            "AND a.payload->'ref'->>'received_id' = s.payload->>'received_id') "
            "AND (SELECT count(*) FROM events f WHERE f.task_id = s.task_id "
            "AND f.type = 'judgement.failed' AND f.payload->>'site' = %s "
            "AND f.payload->'ref'->>'received_id' = s.payload->>'received_id') < %s "
            "ORDER BY s.id",
            (SHAPE.site, SHAPE.site, judgement.UNANSWERED_RUNS),
        )
    ).fetchall()
    return [{**payload, "task_id": task_id} for task_id, payload in rows]


async def inputs(conn, seen: dict[str, Any]) -> dict[str, str]:
    """The shape judgement's inputs for a `seen` reaction: the message's
    text, its reply chain oldest first, and what it was bound as."""
    received = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND payload->>'received_id' = %s",
            (seen["received_id"],),
        )
    ).fetchone()
    bound = await (
        await conn.execute(
            "SELECT payload->>'as' FROM events WHERE type = 'message.bound' AND payload->>'received_id' = %s",
            (seen["received_id"],),
        )
    ).fetchone()
    p = received[0] if received else {}
    thread = "\n".join(str(m.get("text") or "") for m in p.get("thread") or [] if isinstance(m, dict))
    return {"message": str(p.get("text") or ""), "thread": thread, "bound_as": bound[0] if bound else ""}


async def shape(port: judgement.JudgementPort, conn, seen: dict[str, Any], dsn: str) -> str | None:
    """Ask the shape judgement for one `seen` reaction and write the `shape`
    reaction it picks; its emoji, or None when it writes none. `conn` writes
    the reaction; the port opens its own connections on `dsn`."""
    task_id = seen["task_id"]
    try:
        j = await port.judge(
            SHAPE,
            await inputs(conn, seen),
            task_id=task_id,
            ref={"received_id": seen["received_id"]},
            dsn=dsn,
        )
    except tasks.TaskStopped:
        emoji = EMOJI["acknowledge"]
        written = await _request(conn, task_id, seen, emoji, "shape", judged=False, reason="task stopped")
        return emoji if written else None
    label = (j.payload.get("picked") or {}).get(SHAPE.questions[0].id) if j.answered else None
    if label is None:
        return None
    emoji = EMOJI[label]
    written = await _request(conn, task_id, seen, emoji, "shape", judged=True, judgement_id=j.judgement_id)
    return emoji if written else None
