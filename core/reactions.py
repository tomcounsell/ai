"""Reactions: 👀 on a message Valor reads, then an emoji for the activity
his response takes (docs/plans/reactions-activity.md).

Binding (`core/intake.py`) calls `on_bound` in the binding's transaction.
A Telegram or Slack message bound to a task as `start`, `answer`,
`feedback`, or `steer` gets a `seen` reaction, 👀, which the activity
judgement then reads. One bound `none` with a task (Tom's reply to a
merged or stopped task) gets 👀 with `judged: false`: its only response
is the kernel's notice, so no activity follows. A `stop` is not judged: it
gets the `shape` reaction 👌 at once, with `judged: false`. A message bound
`none` with no task, or `heard`, and any message on a channel that does
not react (email, local), gets nothing; so in a team chat whose spec lists
it under `mentions`, only a message that names Valor or replies to him is
marked.

The resident kernel (`core/serve.py`) then asks the `response.shape`
judgement once for each judged `seen` reaction with no `shape` (`pending`,
`shape`). The judgement is metered on the bound task. Its label is the
classifier's read of the activity Valor's response will take, a
prediction made apart from the session's turn, not a record of it. `reply`
writes nothing, so 👀 stays; any other label's `shape` reaction replaces
👀. An abstain, or a judgement both legs failed `judgement.UNANSWERED_RUNS`
times, writes nothing either. A task stopped before its judgement runs gets
👌 with `judged: false`, so no message shows 👀 for good.

`REACTIONS` is the one mapping, per channel, from a label (`seen`, `stop`,
and each activity) to what the bridge places: on Telegram the emoji's exact
code points, which the bridge sends as a standard reaction when Telegram's
list has it and as a Premium custom emoji for that glyph when not
(`bridges/telegram/wire.py`); on Slack the emoji's name. Each
`reaction.requested` row carries the `label` and that `emoji`.

The bridge places each `reaction.requested` row through its outbox
(`core/bridge.py`, `ReactionDue`) with its own account, as it sends
notices, and writes `reaction.placed` or `reaction.failed`. There is no
broker effect, approval, or report: a reaction is a reversible mark in a
chat Tom owns and carries no text. Every reaction row is quiet
(`ledger.quiet`), so none moves the task it sits on.

The session reads the reaction on each message beside it (`shown`, through
`intake.chat_line`), named as the classifier's, so asked about an emoji
Valor says what set it.
"""

from typing import Any

import psycopg

from core import channels, judgement, ledger, tasks
from core.judgement_tasks import SHAPE

# Telegram: exact code points, none with the U+FE0F variation selector,
# which Telegram can refuse. Slack: the emoji's name.
REACTIONS = {
    "telegram": {
        "seen": "\U0001f440",  # 👀
        "stop": "\U0001f44c",  # 👌
        "task": "\U0001fae1",  # 🫡
        "code": "\U0001f468\u200d\U0001f4bb",  # 👨‍💻
        "web_search": "\U0001f50d",  # 🔍
        "lookup": "\U0001f9d0",  # 🧐
        "document": "\u270d",  # ✍
        "present": "\U0001f4ca",  # 📊
        "issue": "\U0001f3ab",  # 🎫
        "schedule": "\U0001f4c5",  # 📅
        "send": "\U0001f4e4",  # 📤
    },
    "slack": {
        "seen": "eyes",
        "stop": "ok_hand",
        "task": "saluting_face",
        "code": "technologist",
        "web_search": "mag",
        "lookup": "face_with_monocle",
        "document": "writing_hand",
        "present": "bar_chart",
        "issue": "ticket",
        "schedule": "date",
        "send": "outbox_tray",
    },
}
# The activity that leaves 👀 as it is.
REPLY = "reply"
# What a message is bound as for it to get 👀 and a judgement.
JUDGED = ("start", "answer", "feedback", "steer")


async def on_bound(conn, p: dict[str, Any], task_id: str | None, as_: str) -> None:
    """The reaction a binding owes, in the binding's transaction: `seen` for
    a message the activity judgement will read, `seen` unjudged for one
    bound `none` to a task, 👌 at once for a `stop`."""
    row = channels.get(p["channel"])
    if task_id is None or row is None or not row.reacts:
        return
    if as_ in JUDGED:
        await _request(conn, task_id, p, "seen", "seen")
    elif as_ == "none":
        await _request(conn, task_id, p, "seen", "seen", judged=False)
    elif as_ == "stop":
        await _request(conn, task_id, p, "stop", "shape", judged=False, reason="stop")


async def _request(conn, task_id: str, p: dict[str, Any], label: str, stage: str, **extra) -> str | None:
    """One `reaction.requested` row for `label`, its emoji the channel's;
    its id, or None when this message already has a reaction at this
    stage."""
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
                    "emoji": REACTIONS[p["channel"]][label],
                    "label": label,
                    "stage": stage,
                    **extra,
                },
            )
    except psycopg.errors.UniqueViolation:
        return None
    return reaction_id


async def pending(conn) -> list[dict[str, Any]]:
    """Every judged `seen` reaction whose message has no `shape` reaction, no
    answered `response.shape` judgement, and fewer than
    `judgement.UNANSWERED_RUNS` failed ones: each its row's payload with
    `task_id`, oldest first."""
    rows = await (
        await conn.execute(
            "SELECT s.task_id, s.payload FROM events s WHERE s.type = 'reaction.requested' "
            "AND s.payload->>'stage' = 'seen' AND s.payload->>'judged' IS DISTINCT FROM 'false' AND NOT EXISTS (SELECT 1 FROM events h "
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
    """Ask the activity judgement for one `seen` reaction and write the
    `shape` reaction for the activity it picks; its emoji, or None when it
    writes none (`reply` included). `conn` writes the reaction; the port
    opens its own connections on `dsn`."""
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
        written = await _request(conn, task_id, seen, "stop", "shape", judged=False, reason="task stopped")
        return REACTIONS[seen["channel"]]["stop"] if written else None
    label = (j.payload.get("picked") or {}).get(SHAPE.questions[0].id) if j.answered else None
    if label is None or label == REPLY:
        return None
    written = await _request(conn, task_id, seen, label, "shape", judged=True, judgement_id=j.judgement_id)
    return REACTIONS[seen["channel"]][label] if written else None


# The rows `shown` reads.
SHOWN_ROWS = ("reaction.requested", "reaction.failed", "judgement.answered")


def emoji_of(p: dict[str, Any]) -> str:
    """A `reaction.requested` row's emoji as a reader sees it: Slack's by
    name, `:mag:`."""
    e = str(p.get("emoji") or "")
    return f":{e}:" if p.get("channel") == "slack" and p.get("label") else e


def shown(rows: list[dict]) -> dict[str, str]:
    """By `received_id`, what a session reads of the reaction on the
    message now: the newest requested reaction that did not fail, and who
    set it; with 👀 alone, whether the classifier read it as a plain
    reply."""
    failed = {r["payload"].get("reaction_id") for r in rows if r["type"] == "reaction.failed"}
    replies = {
        (r["payload"].get("ref") or {}).get("received_id")
        for r in rows
        if r["type"] == "judgement.answered"
        and r["payload"].get("site") == SHAPE.site
        and (r["payload"].get("picked") or {}).get(SHAPE.questions[0].id) == REPLY
    }
    out: dict[str, str] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] != "reaction.requested" or p.get("reaction_id") in failed:
            continue
        rid, e = p.get("received_id"), emoji_of(p)
        if p.get("stage") == "seen":
            out[rid] = f"reacted {e} (read)" + (
                "; the reaction classifier read it as a plain reply" if rid in replies else ""
            )
        elif p.get("judged") and p.get("label"):
            out[rid] = f"reacted {e}: the reaction classifier read it as {p['label']}"
        elif p.get("reason"):
            out[rid] = f"reacted {e}: set at once ({p['reason']})"
        else:
            out[rid] = f"reacted {e}"
    return out


async def shown_for(conn, received_ids: list[str]) -> dict[str, str]:
    """`shown` for these messages, read from the ledger wherever their
    rows sit."""
    if not received_ids:
        return {}
    found = await (
        await conn.execute(
            "SELECT type, payload FROM events WHERE type = ANY(%s) AND "
            "COALESCE(payload->>'received_id', payload->'ref'->>'received_id') = ANY(%s) ORDER BY id",
            (list(SHOWN_ROWS), received_ids),
        )
    ).fetchall()
    return shown([{"type": t, "payload": p} for t, p in found])
