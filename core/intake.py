"""The inbound record and binding.

A bridge records each message it receives with `receive`, on the
channel's stream, then acknowledges it to the platform. The kernel binds
each recorded message (`bind`) to what it means: an answer, feedback, an
approval, a stop, a steer of a running task, or a new task. A bridge holds
no binding logic.

`verified` is set here, not by the bridge: every Telegram record is
verified (MTProto authenticates the sender); a local record is verified
(the local bridge writes one only for a request carrying its token, and
the local chat has one member, Tom); an email record is not. Whether the
sender is the operator is decided at bind.

Ownership: this machine's bridges receive the operator's own chat and
addresses, and the chats a project spec lists (`chats`) whose `machine`
is this one; a spec naming no machine belongs to
`settings.default_machine`. The operator chat is the bridge's whose
channel is `settings.operator_channel`: the Telegram operator chat, or the
one local chat, `local`.

The binding table, first match wins:

| The record | Bound as |
| --- | --- |
| Not verified, or not from the operator | `none` |
| A reply to a merged or stopped task | `none`, with a notice |
| A Telegram or local reply to a task's notice or send, exactly `stop` | `stop` |
| A reply to the open question's notice | `answer` |
| A reply to the delivered notice, task in `merge` | `feedback` |
| A Telegram or local reply to an effect notice, exactly `approve` | `approve` |
| The same, the effect already released or done | `none`, with a notice |
| Any other reply to a task's notice or send | `steer` |
| Not a reply, with text or files | `start` |
| Not a reply, empty | `none` |

"Exactly" is the whole text, trimmed and casefolded. A near miss steers
and owes a notice; by email, `approve` and `stop` always steer, and the
notice says they come by reply on the operator channel. A binding notice
goes back in reply in the message's own chat on Telegram and the local
chat; about an email, it goes to the operator channel and chat. A binding that raises is rolled back
and bound `none` with the error, owing a notice; later messages never wait
behind it. `message.bound` is the first row a binding writes, so a
second binder of the same message stops there and writes nothing.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from core import ledger, machine, notices, session, tasks, workspace
from core.machine import State
from core.settings import resolve_model, settings


@dataclass(frozen=True)
class Inbound:
    channel: str  # "telegram", "email", or "local"
    chat_id: str  # Telegram: marked peer id as text (-100... for groups); email: the thread root
    chat_kind: str  # "dm", "group", or "email"
    message_id: str  # Telegram: message id as text; email: Message-ID
    sender_id: str  # Telegram: user id as text; email: From address, lowercased
    sender_name: str
    sent_at: str  # ISO 8601, UTC
    kind: str = "message"
    text: str = ""  # email: the subject, a blank line, then the body
    reply_to: str | None = None  # Telegram: replied message id; email: In-Reply-To
    thread: list[dict] = field(default_factory=list)  # {id, text, attachments}, oldest first
    topic_id: str | None = None  # Telegram forum topic
    attachments: list[dict] = field(default_factory=list)  # {name, mime, bytes, path} or {..., skipped}
    headers: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Received:
    received_id: str
    duplicate: bool  # this (channel, chat_id, message_id) is already recorded


def _verified_telegram(inbound: Inbound) -> bool:
    return True


def _verified_email(inbound: Inbound) -> bool:
    return False


def _verified_local(inbound: Inbound) -> bool:
    return True


VERIFY = {"telegram": _verified_telegram, "email": _verified_email, "local": _verified_local}

# The channels where Tom replies to a message, so `approve` and `stop`
# bind and a binding notice goes back in reply.
REPLIES = ("telegram", "local")
# The local chat page's one chat.
LOCAL_CHAT = "local"
# Where Tom replies, as the near miss notice for an email names it.
_WHERE = {"telegram": "in Telegram", "local": "on the local chat page"}


def _where() -> str:
    channel = settings.operator_channel
    return _WHERE.get(channel, f"on {channel}")


async def receive(conn, inbound: Inbound) -> Received:
    """Record one inbound message, committed before this returns."""
    received_id = ledger.new_id()
    verify = VERIFY.get(inbound.channel)
    payload = {"received_id": received_id, "verified": bool(verify and verify(inbound)), **asdict(inbound)}
    try:
        async with conn.transaction():
            await ledger.append(conn, inbound.channel, "message.received", payload)
    except psycopg.errors.UniqueViolation:
        row = await (
            await conn.execute(
                "SELECT payload->>'received_id' FROM events WHERE type = 'message.received' "
                "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'message_id' = %s",
                (inbound.channel, inbound.chat_id, inbound.message_id),
            )
        ).fetchone()
        return Received(row[0], True)
    return Received(received_id, False)


# An all-digit message id as a bigint, or NULL when it does not fit one.
# Compared as text, never cast before it is known to fit: a cast of a long
# enough id overflows even numeric.
_DIGITS = "ltrim(payload->>'message_id', '0')"
_FITS = (
    f"CASE WHEN length({_DIGITS}) < 19 OR (length({_DIGITS}) = 19 "
    f"AND {_DIGITS} COLLATE \"C\" <= '9223372036854775807') "
    "THEN (payload->>'message_id')::bigint END"
)


async def highest(conn, channel: str, chat_id: str) -> int | None:
    """The largest integer message id recorded for the chat: a paging hint."""
    row = await (
        await conn.execute(
            f"SELECT max({_FITS}) FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'message_id' ~ '^[0-9]+$'",
            (channel, chat_id),
        )
    ).fetchone()
    return None if row[0] is None else int(row[0])


async def lowest(conn, channel: str, chat_id: str) -> int | None:
    """The smallest integer message id recorded for the chat: where a gap
    fill of a chat with no seen entry stops."""
    row = await (
        await conn.execute(
            f"SELECT min({_FITS}) FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'message_id' ~ '^[0-9]+$'",
            (channel, chat_id),
        )
    ).fetchone()
    return None if row[0] is None else int(row[0])


async def recorded(conn, channel: str, chat_id: str, ids: list[str]) -> set[str]:
    """Which of `ids` are already received in the chat."""
    rows = await (
        await conn.execute(
            "SELECT payload->>'message_id' FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'message_id' = ANY(%s)",
            (channel, chat_id, list(ids)),
        )
    ).fetchall()
    return {r[0] for r in rows}


async def claimed(conn, channel: str, chat_id: str) -> set[str]:
    """The message ids recorded as sent in the chat, by a send or a notice."""
    rows = await (
        await conn.execute(
            "SELECT e->>'message_id' FROM events, "
            "jsonb_array_elements(COALESCE(payload->'sent', payload->'result'->'sent')) e "
            "WHERE type IN ('notice.sent', 'effect.outcome') "
            "AND jsonb_typeof(COALESCE(payload->'sent', payload->'result'->'sent')) = 'array' "
            "AND e->>'channel' = %s AND e->>'chat_id' = %s",
            (channel, chat_id),
        )
    ).fetchall()
    return {r[0] for r in rows}


def _spec_machine(spec: workspace.Spec) -> str:
    return spec.machine or settings.default_machine


def _project_chats(channel: str) -> list[tuple[str, workspace.Spec]]:
    found = []
    for spec in workspace.Spec.all():
        if _spec_machine(spec) != settings.machine:
            continue
        for chat in spec.chats:
            kind, _, ident = chat.partition(":")
            if kind == channel:
                found.append((ident, spec))
    return found


def owned(channel: str) -> list[str]:
    """Every id this machine's bridge on `channel` receives."""
    ids: list[str] = []
    if channel == "telegram" and settings.operator_channel == "telegram" and settings.operator_chat:
        ids.append(settings.operator_chat)
    if channel == "local" and settings.operator_channel == "local":
        ids.append(LOCAL_CHAT)
    if channel == "email":
        ids.extend(settings.operator_email)
    for ident, _ in _project_chats(channel):
        if ident not in ids:
            ids.append(ident)
    return ids


def owns(channel: str, id: str) -> bool:
    return (id.lower() if channel == "email" else id) in owned(channel)


def _from_operator(p: dict[str, Any]) -> bool:
    if not p.get("verified"):
        return False
    if p["channel"] == "telegram":
        return settings.operator_telegram_id is not None and p["sender_id"] == settings.operator_telegram_id
    if p["channel"] == "email":
        return p["sender_id"].lower() in settings.operator_email
    return p["channel"] == "local"


def _bare(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).strip().casefold()


def _near(text: str, word: str) -> bool:
    bare = _bare(text)
    return bare == word or bare.startswith(word + " ")


async def bind(conn) -> list[tuple[str, str]]:
    """Bind every recorded message not yet bound, in order. Returns each
    `(received_id, as)`."""
    rows = await (
        await conn.execute(
            "SELECT r.payload FROM events r WHERE r.type = 'message.received' AND NOT EXISTS ("
            "SELECT 1 FROM events b WHERE b.type = 'message.bound' "
            "AND b.payload->>'received_id' = r.payload->>'received_id') ORDER BY r.id"
        )
    ).fetchall()
    done = []
    for (p,) in rows:
        try:
            async with conn.transaction():
                bound = await _bind(conn, p)
        except AlreadyBound:
            continue
        except Exception as exc:  # noqa: BLE001  one message's error never blocks the next
            bound = await _bind_failed(conn, p, exc)
        if bound is not None:
            done.append((p["received_id"], bound))
    return done


class AlreadyBound(Exception):
    """Another binder wrote this message's `message.bound` first."""


async def _bound(conn, p: dict[str, Any], task_id: str | None, as_: str, **extra) -> str:
    """`message.bound`, written before any row the binding writes, in the
    same transaction: a second binder of the same message waits on the
    unique index, then raises `AlreadyBound` and writes nothing."""
    try:
        async with conn.transaction():
            await ledger.append(
                conn,
                p["channel"],
                "message.bound",
                {"received_id": p["received_id"], "task_id": task_id, "as": as_, **extra},
            )
    except psycopg.errors.UniqueViolation:
        raise AlreadyBound(p["received_id"]) from None
    return as_


async def _bind_failed(conn, p: dict[str, Any], exc: Exception) -> str | None:
    task_id = None
    try:
        task_id, _ = await _replied(conn, p)
    except Exception:  # noqa: BLE001, S110  the notice goes to the channel's stream instead
        pass
    try:
        async with conn.transaction():
            as_ = await _bound(conn, p, task_id, "none", error=repr(exc))
            await _notice(
                conn, task_id or p["channel"], p, f"Your message ({p['message_id']}) was not acted on: {exc}"
            )
    except AlreadyBound:
        return None
    return as_


async def _replied(conn, p: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """The task a reply belongs to and what it replies to: a notice
    (`{"notice": <notice.requested payload>}`) or a send (`{"send": ...}`)."""
    if not p.get("reply_to"):
        return None, None
    sent = Jsonb([{"channel": p["channel"], "chat_id": p["chat_id"], "message_id": p["reply_to"]}])
    row = await (
        await conn.execute(
            "SELECT task_id, type, payload FROM events WHERE type IN ('notice.sent', 'effect.outcome') "
            "AND COALESCE(payload->'sent', payload->'result'->'sent') @> %s ORDER BY id LIMIT 1",
            (sent,),
        )
    ).fetchone()
    if row is None:
        return None, None
    task_id, type_, payload = row
    if type_ == "effect.outcome":
        return task_id, {"send": payload}
    notice = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'notice.requested' AND payload->>'notice_id' = %s",
            (payload["notice_id"],),
        )
    ).fetchone()
    return task_id, {"notice": notice[0] if notice else {}}


async def _notice(conn, task_id: str, p: dict[str, Any], text: str, suffix: str = "") -> None:
    replies = p["channel"] in REPLIES
    await notices.request(
        conn,
        task_id,
        kind="binding",
        about_key=f"reply:{p['received_id']}{suffix}",
        text=text,
        # One in reply in the chat the message came from; one about an
        # email to the operator channel and chat.
        reply_to=p["message_id"] if replies else None,
        chat_id=p["chat_id"] if replies else None,
        channel=p["channel"] if replies else None,
    )


async def _bind(conn, p: dict[str, Any]) -> str | None:
    if not _from_operator(p):
        return await _bound(conn, p, None, "none")
    task_id, replied = await _replied(conn, p)
    if task_id is None or replied is None:
        return await _start(conn, p)
    # Tree, then task: the order every tree writer takes (a stop or
    # feedback below takes the tree's lock again).
    await tasks.lock_tree(conn, task_id)
    await ledger.lock(conn, f"task:{task_id}")
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.state in (State.MERGED, State.STOPPED):
        as_ = await _bound(conn, p, task_id, "none")
        await _notice(conn, task_id, p, f"Task {task_id} is {f.state.value}; nothing was done.")
        return as_
    text = (p.get("text") or "").strip()
    exact = text.casefold()
    replies = p["channel"] in REPLIES
    via = p["channel"]
    notice = (replied.get("notice") or {}) if "notice" in replied else {}
    kind, about = notice.get("kind"), notice.get("about_key") or ""

    if replies and exact == "stop":
        as_ = await _bound(conn, p, task_id, "stop")
        await tasks.stop(conn, task_id, reason=f"Tom replied stop ({p['message_id']})", by="tom", via=via)
        return as_
    if kind == "question" and f.state is State.WAITING and about == f"question:{f.open_question}":
        as_ = await _bound(conn, p, task_id, "answer")
        await session.answer(conn, task_id, text, by="tom", via=via)
        return as_
    if kind == "delivered" and f.state is State.MERGE:
        as_ = await _bound(conn, p, task_id, "feedback")
        await session.feedback(conn, task_id, text, by="tom", via=via)
        return as_
    if replies and exact == "approve" and kind == "effect":
        return await _approve(conn, p, task_id, about.removeprefix("effect:"))

    as_ = await _bound(conn, p, task_id, "steer")
    await ledger.append(
        conn,
        task_id,
        "message.steered",
        {
            "received_id": p["received_id"],
            "channel": p["channel"],
            "chat_id": p["chat_id"],
            "message_id": p["message_id"],
            "text": text,
            "attachments": p.get("attachments") or [],
            "provenance": ledger.provenance("tom", via, False),
        },
    )
    for word in ("approve", "stop"):
        if _near(text, word):
            said = (
                f"Not {'an approval' if word == 'approve' else 'a stop'}; reply `{word}`. "
                "Your message steers the task."
                if replies
                else f"Approvals and stops come by reply {_where()}; your email steers the task."
            )
            await _notice(conn, task_id, p, said)
            break
    if notices._held(rows):
        await _notice(
            conn, task_id, p, f"Task {task_id} is waiting on approval; reply `approve` or `stop`.", ":waiting"
        )
    elif f.state is State.MERGE:
        await _notice(
            conn,
            task_id,
            p,
            f"Task {task_id} is in merge; reply to the delivered notice to give feedback.",
            ":waiting",
        )
    return as_


async def _approve(conn, p: dict[str, Any], task_id: str, effect_id: str) -> str | None:
    from core import broker
    from core.bridge import DECLARED

    standing = {
        r[0]
        for r in await (
            await conn.execute(
                "SELECT type FROM events WHERE payload->>'effect_id' = %s AND type IN "
                "('release.requested', 'effect.intent', 'effect.outcome', 'effect.refused')",
                (effect_id,),
            )
        ).fetchall()
    }
    if standing:
        as_ = await _bound(conn, p, task_id, "none")
        said = "already done" if standing & {"effect.outcome", "effect.refused"} else "already released"
        await _notice(conn, task_id, p, f"Effect {effect_id} is {said}; nothing was done.")
        return as_
    as_ = await _bound(conn, p, task_id, "approve")
    held = await broker._held(conn, effect_id)
    approval_id = await broker.approve(conn, effect_id, note=p.get("text") or "", by="tom", via=p["channel"])
    declared = DECLARED.get(held["payload"]["action_type"])
    await ledger.append(
        conn,
        task_id,
        "release.requested",
        {
            "effect_id": effect_id,
            "approval_id": approval_id,
            "owner": declared.owner if declared else "kernel",
        },
    )
    return as_


def _project_for(p: dict[str, Any]) -> workspace.Spec | None:
    ident = p["sender_id"] if p["channel"] == "email" else p["chat_id"]
    for chat, spec in _project_chats(p["channel"]):
        if chat == ident:
            return spec
    try:
        return workspace.Spec.load("valor")
    except workspace.Refused, FileNotFoundError:
        return None


async def _start(conn, p: dict[str, Any]) -> str | None:
    """A new task from Tom's message, under the project listing the chat
    (by email: the sender), else `valor`. Its workspace is provisioned by a
    kernel job after it starts. A message of files alone starts a task
    whose instruction lists them."""
    text = (p.get("text") or "").strip()
    files = _files(p.get("attachments") or [])
    if files:
        text = f"{text}\n\n{files}" if text else f"Tom sent files with no text:\n{files}"
    if not text:
        return await _bound(conn, p, None, "none")
    spec = _project_for(p)
    brief = tasks.Brief(
        instruction=text,
        model=resolve_model("light"),
        project={"name": spec.name} if spec else None,
    )
    as_ = await _bound(conn, p, brief.id, "start")
    await tasks.start(conn, brief, by="tom", via=p["channel"])
    return as_


def _files(attachments: list[dict[str, Any]]) -> str:
    """One line per attachment: its name, type, and path, or why it was
    not downloaded."""
    lines = []
    for a in attachments:
        where = a.get("path") or f"not downloaded: {a.get('skipped')}"
        lines.append(
            f"- {a.get('name') or 'file'} ({a.get('mime') or 'unknown type'}, {a.get('bytes')} bytes): {where}"
        )
    return "\n".join(lines)
