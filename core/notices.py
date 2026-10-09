"""Operator notices: what the kernel tells Tom, as rows a bridge sends.

`owe(conn, task_id)` writes the `notice.requested` rows a task's fold owes
and has not requested, to `settings.operator_channel` and
`settings.operator_chat`:

- `question` (`about_key` `question:<id>`): the open question.
- `delivered` (`delivered:<sha>`, or `delivered:<sha>:<effect>` for a
  merge refused or failed): the candidate and each check's outcome, owed
  only for a delivery that will not merge by itself: it did not pass, a
  governance instance awaits Tom's grant, or its merge was refused or
  failed, with the reason. A merge refused or failed for its payload is
  not asked again for that payload, so this notice is the question Tom
  gets about it.

`report(conn, task_id, effect, result)` writes the `report` notice
(`report:<effect>`) an `act` effect owes once its outcome is `done`: what
left. A merge's carries the delivery and ends with how to give feedback; a
send's carries its channel, recipients, and text. A push owes none (a step
inside the task), nor does a send that itself reached Tom (to the operator
chat or Tom's Telegram id, an email only to Tom's addresses, any local
send), nor any act of a replay task (`Brief.replay`).

A notice with no channel or chat (the operator's unset) is written with a
`notice.undeliverable` row beside it, so the ledger shows it was never
sent. `request` writes one notice; binding (`core/intake.py`) uses it for
its own. A notice is requested once
per task and `about_key` (`events_one_notice`): a second request, from
another kernel or a retry, is skipped in a savepoint. A notice whose text
Postgres jsonb refuses (a held send's text, as large as the ledger stores
once, rendered twice) is written with kernel text in its place, naming
what it is about and Postgres's reason. Each text carries the
notice's short id, so a bridge's lookup matches the sent message exactly.
The bridge sends notices as they come.
"""

from pathlib import Path
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
    if f.delivery and f.state is State.MERGE:
        owed = _delivered_owed(f, rows)
        if owed is not None:
            about, why = owed
            made.append(
                await request(
                    conn, task_id, kind="delivered", about_key=about, text=delivered_text(task_id, f.delivery, why)
                )
            )
    return [m for m in made if m]


def _delivered_owed(f: machine.Fold, rows: list[dict[str, Any]]) -> tuple[str, str | None] | None:
    """The `delivered` notice's `about_key` and the reason the delivery will
    not merge by itself, or None when it will (its merge's report says what
    left)."""
    sha = ((f.delivery.get("candidate") or {}).get("sha")) or ""
    current = f.candidate is not None and (f.delivery.get("candidate") or {}) == {
        "sha": f.candidate.sha,
        "turn_id": f.candidate.turn_id,
    }
    if not current or f.delivery.get("outcome") not in ("passed", "gaps"):
        return f"delivered:{sha}", None
    if f.ungranted():
        return f"delivered:{sha}", "a governance instance awaits Tom's grant (`python -m core grant`)"
    effect = f.merge_effect
    if effect and effect["state"] in ("refused", "failed"):
        why = next(
            (
                r["payload"].get("reason") or r["payload"].get("error")
                for r in reversed(rows)
                if r["type"] in ("effect.refused", "effect.outcome")
                and r["payload"].get("effect_id") == effect["effect_id"]
            ),
            None,
        )
        return f"delivered:{sha}:{effect['effect_id']}", f"its merge {effect['state']}: {why}"
    return None


def _to_tom(effect: dict[str, Any]) -> bool:
    """Whether a send is itself a message to Tom."""
    kind, target, payload = effect.get("action_type"), str(effect.get("target") or ""), effect.get("payload") or {}
    if kind == "local.send_message":
        return True
    if kind == "telegram.send_message":
        own = {settings.operator_telegram_id}
        if settings.operator_channel == "telegram":
            own.add(settings.operator_chat)
        return target in own - {None}
    if kind == "email.send":
        sent = [*(payload.get("to") or []), *(payload.get("cc") or [])]
        mine = {a.casefold() for a in settings.operator_email}
        return bool(sent) and not payload.get("reply_to") and all(str(a).casefold() in mine for a in sent)
    return False


async def report(conn, task_id: str, effect: dict[str, Any], result: dict[str, Any]) -> str | None:
    """The `report` notice a `done` act owes Tom, inside the caller's
    transaction; None when it owes none (see the module docstring)."""
    from core import tasks

    kind = effect.get("action_type")
    if effect.get("effect_class") != "act" or kind == "push_branch" or _to_tom(effect):
        return None
    if (await tasks.brief(conn, task_id)).replay:
        return None
    payload = effect.get("payload") or {}
    if kind == "merge":
        f = machine.fold(await ledger.read(conn, task_id))
        lines = [
            (
                f"Task {task_id} merged {str(payload.get('head_sha') or '')[:12]} into "
                f"{payload.get('target_branch') or effect.get('target')} ({payload.get('url') or ''})."
            ),
            "",
        ]
        if f.delivery:
            lines += [str(f.delivery.get("summary") or ""), ""]
            lines += [f"- {c}: {o}" for c, o in (f.delivery.get("checks") or {}).items()]
            lines.append("")
        lines.append("Reply to this message to give feedback.")
        text = "\n".join(lines)
    else:
        body = payload.get("text") or payload.get("subject") or payload.get("body") or ""
        to = ", ".join(payload.get("to") or []) or effect.get("target")
        if kind == "email.send" and payload.get("reply_to"):
            to = f"all of {payload['reply_to']}"
        files = [Path(f.get("path", "")).name for f in payload.get("files") or []]
        text = f"Task {task_id} sent {kind} to {to}:\n\n{body}" + (
            f"\n\nFiles: {', '.join(files)}" if files else ""
        )
    return await request(conn, task_id, kind="report", about_key=f"report:{effect['effect_id']}", text=text)


def _unstorable(task_id: str, kind: str, about_key: str, why: str, notice_id: str) -> str:
    """A notice's text when Postgres jsonb refuses the one rendered: kernel
    words only, naming what it is about, so Tom still hears of it."""
    return (
        f"Task {task_id} has a notice ({kind}, {about_key}) whose text the ledger's JSON (Postgres jsonb) "
        f"cannot store: {why}. The task's ledger holds what it is about.\n\n{tag(notice_id)}"
    )


def delivered_text(task_id: str, delivery: dict[str, Any], why: str | None = None) -> str:
    candidate = delivery.get("candidate") or {}
    lines = [
        f"Task {task_id} delivered {candidate.get('sha', '')[:12]} ({delivery.get('outcome', 'delivered')}):",
        "",
        str(delivery.get("summary") or ""),
    ]
    if why:
        lines += ["", f"It is not merged: {why}.", ""]
    for check, outcome in (delivery.get("checks") or {}).items():
        lines.append(f"- {check}: {outcome}")
    for finding in delivery.get("findings") or []:
        lines.append(f"- [{finding.get('source')}, {finding.get('kind')}] {finding.get('text')}")
    lines += ["", "Reply to this message to give feedback."]
    return "\n".join(lines)
