"""Operator notices: what the kernel tells Tom, as rows a bridge sends.

`owe(conn, task_id)` writes the `notice.requested` rows a task's fold owes
and has not requested, to `settings.operator_channel` and
`settings.operator_chat`:

- `question` (`about_key` `question:<id>`): the open question.
- `effect` (`effect:<id>`): a held effect: its action type, target, effect
  id, and payload in full (a message as it will be sent), then how to
  approve it.
- `delivered` (`delivered:<sha>`): the candidate and each check's outcome.

A notice with no channel or chat (the operator's unset) is written with a
`notice.undeliverable` row beside it, so the ledger shows it was never
sent. `request` writes one notice; binding (`core/intake.py`) and a refused
release (`core/broker.py`) use it for theirs. A notice is requested once
per task and `about_key` (`events_one_notice`): a second request, from
another kernel or a retry, is skipped in a savepoint. A notice whose text
Postgres jsonb refuses (a held send's text, as large as the ledger stores
once, rendered twice) is written with kernel text in its place, naming
what it is about and Postgres's reason. Each text carries the
notice's short id, so a bridge's lookup matches the sent message exactly.
Notices are not held for approval; the bridge sends them as they come.
"""

import json
from typing import Any

import psycopg

from core import ledger, machine
from core.machine import State
from core.settings import settings


def tag(notice_id: str) -> str:
    """The short id a rendered notice carries."""
    return f"[n:{notice_id}]"


async def request(
    conn,
    task_id: str,
    *,
    kind: str,
    about_key: str,
    text: str,
    reply_to: str | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
) -> str | None:
    """One `notice.requested` row; its id, or None when one with this
    `about_key` already stands for the task."""
    found = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'notice.requested' "
            "AND payload->>'about_key' = %s",
            (task_id, about_key),
        )
    ).fetchone()
    if found is not None:
        return None
    notice_id = ledger.new_id()
    channel = channel or settings.operator_channel
    chat_id = chat_id if chat_id is not None else settings.operator_chat
    try:
        async with conn.transaction():
            notice = {
                "notice_id": notice_id,
                "channel": channel,
                "chat_id": chat_id,
                "kind": kind,
                "about_key": about_key,
                "text": f"{text}\n\n{tag(notice_id)}",
                "reply_to": reply_to,
            }
            written, why = await ledger.try_append(conn, task_id, "notice.requested", notice)
            if written is None:
                await ledger.append(conn, task_id, "notice.requested", {**notice, "text": _unstorable(
                    task_id, kind, about_key, why, notice_id)})  # fmt: skip
            if not channel or not chat_id:
                # No bridge sends a notice with no chat: the ledger says so.
                await ledger.append(
                    conn,
                    task_id,
                    "notice.undeliverable",
                    {
                        "notice_id": notice_id,
                        "reason": "no operator channel or chat is set "
                        "(VALOR_OPERATOR_CHANNEL, VALOR_OPERATOR_CHAT)",
                    },
                )
    except psycopg.errors.UniqueViolation:
        return None
    return notice_id


async def owe(conn, task_id: str) -> list[str]:
    """Request every notice the task's fold owes. Returns the new ids."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.legacy or f.calibration or f.state in (State.STOPPED, State.MERGED):
        return []
    made: list[str | None] = []
    if f.state is State.WAITING and f.open_question:
        asked = next(
            (r["payload"] for r in rows if r["type"] == "question.asked"
             and r["payload"].get("question_id") == f.open_question),
            None,
        )  # fmt: skip
        if asked is not None:
            made.append(
                await request(
                    conn,
                    task_id,
                    kind="question",
                    about_key=f"question:{f.open_question}",
                    text=f"Task {task_id} asks:\n\n{asked.get('text', '')}\n\nReply to this message to answer.",
                )
            )
    for effect in _held(rows):
        made.append(
            await request(
                conn,
                task_id,
                kind="effect",
                about_key=f"effect:{effect['effect_id']}",
                text=effect_text(task_id, effect),
            )
        )
    if f.delivery and f.state is State.MERGE:
        sha = ((f.delivery.get("candidate") or {}).get("sha")) or ""
        made.append(
            await request(
                conn,
                task_id,
                kind="delivered",
                about_key=f"delivered:{sha}",
                text=delivered_text(task_id, f.delivery),
            )
        )
    return [m for m in made if m]


def _held(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Effects held for Tom with no approval, intent, outcome, or refusal."""
    held: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] == "effect.held":
            held[p["effect_id"]] = p
        elif r["type"] in ("approval.granted", "effect.intent", "effect.outcome", "effect.refused"):
            held.pop(p.get("effect_id"), None)
    return list(held.values())


def _unstorable(task_id: str, kind: str, about_key: str, why: str, notice_id: str) -> str:
    """A notice's text when Postgres jsonb refuses the one rendered: kernel
    words only, naming what it is about, so Tom still hears of it."""
    return (
        f"Task {task_id} has a notice ({kind}, {about_key}) whose text the ledger's JSON (Postgres jsonb) "
        f"cannot store: {why}. The task's ledger holds what it is about.\n\n{tag(notice_id)}"
    )


def effect_text(task_id: str, effect: dict[str, Any]) -> str:
    payload = effect.get("payload") or {}
    shown = payload["text"] if isinstance(payload.get("text"), str) and len(payload) <= 4 else None
    body = shown if shown is not None else json.dumps(payload, indent=2, sort_keys=True)
    return (
        f"Task {task_id} asks to {effect['action_type']} -> {effect['target']} "
        f"(effect {effect['effect_id']}):\n\n{body}\n\n"
        + (json.dumps(payload, indent=2, sort_keys=True) + "\n\n" if shown is not None else "")
        + "Reply approve to send it."
    )


def delivered_text(task_id: str, delivery: dict[str, Any]) -> str:
    candidate = delivery.get("candidate") or {}
    lines = [
        f"Task {task_id} delivered {candidate.get('sha', '')[:12]} ({delivery.get('outcome', 'delivered')}):",
        "",
        str(delivery.get("summary") or ""),
    ]
    for check, outcome in (delivery.get("checks") or {}).items():
        lines.append(f"- {check}: {outcome}")
    for finding in delivery.get("findings") or []:
        lines.append(f"- [{finding.get('source')}, {finding.get('kind')}] {finding.get('text')}")
    lines += ["", "Reply to this message to give feedback."]
    return "\n".join(lines)
