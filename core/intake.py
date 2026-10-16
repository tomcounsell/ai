"""The inbound record and binding.

A bridge records each message it receives with `receive`, on the
channel's stream, then acknowledges it to the platform. The kernel binds
each recorded message (`bind`) to what it means: an answer, feedback, a
stop, a steer of a running task, or a new task. A task a message starts
runs at ceiling `act`. A bridge holds
no binding logic.

`verified` is set here, not by the bridge, from the channel table
(`core/channels.py`): every Telegram record is verified (MTProto
authenticates the sender), and every Slack record (the socket
authenticates it); a local record is verified (the local bridge writes
one only for a request carrying its token, and the local chat has one
member, Tom); an email record is not. Whether the sender is the operator
is decided at bind.

Ownership: this machine's bridges receive the operator's own chat and
addresses (Tom's email addresses, his Slack ids), and the chats a project
spec lists (`chats`) whose `machine` is this one; a spec naming no
machine belongs to `settings.default_machine`. The operator chat is the
bridge's whose channel is `settings.operator_channel`: the Telegram
operator chat, a Slack chat, or the one local chat, `local`.

A *team chat* is a group chat a spec of this machine lists, other than
Tom's own chat (`team_spec`). It has one long-lived chat session: a task
whose Brief names the chat (`Brief.chat`), started by the chat's first
message and kept until stopped (`chat_session`). Every message there,
anyone's, binds to it, with no rights by sender, as in the old system:
one that wakes it is a `message.steered` row, bound `steer`; any other is
a quiet `message.heard` row, bound `heard`, which reaches the session
with the next one that wakes it. Each names its sender (`by` is the
display name, `tom` for Tom; the received row keeps the `sender_id`).
Every message wakes it, except in a chat the spec lists under
`mentions`, where only one naming Valor (`MENTION`) or replying to a
post of Valor's does. The session decides whether to answer
(`skills/sdlc/converse.md`).

The binding table, first match wins:

| The record | Bound as |
| --- | --- |
| In a team chat, with no text or files | `none` |
| In a team chat, waking the chat session | `steer`, to the chat session |
| In a team chat, otherwise | `heard`, to the chat session |
| Not verified, or not from the operator | `none` |
| A reply to a merge's report, task in `merged`, not `stop` or a near miss | `feedback` |
| A reply to a task's notice or send, task in `ended`, not `stop` or a near miss | `feedback` |
| A reply to a merged or stopped task | `none`, with a notice |
| A reply on a channel where Tom replies (not email), exactly `stop` | `stop` |
| A reply to the open question's notice | `answer` |
| Any other reply to a task's notice or send | `steer` |
| Not a reply, in a DM on a threaded channel, with text, after exactly one live ask among Valor's preceding posts | `answer`, with a notice |
| Not a reply, with text or files | `start`, carrying the preceding posts in a DM on a threaded channel |
| Not a reply, empty | `none` |

"Exactly" is the whole text, trimmed and casefolded. A near miss steers
and owes a notice; by email, `stop` always steers, and the notice says it
comes by reply on the operator channel.

On a threaded channel (Slack) a reply names its thread's root, not the
message it answers, so a reply binds to the newest of Valor's posts in
that thread that is older than it (the root included), read from the
ledger's `sent` entries.

On a threaded channel people also answer in a DM without threading, so a
top-level DM message keeps the posts it follows. Its *preceding posts* are
Valor's top-level posts in the DM older than it and newer than the DM's
previous top-level message, the newest `CARRIED`, one per send or notice.
A post is a *live ask* when it is its task's newest there and is the open
question's notice of a task in `waiting`. Exactly one live ask, still live
once its task is locked, takes the message as its answer (even `stop`,
which then does not stop the task), and a notice in reply says which work
took it. Otherwise (no live ask, two, or files with no text) the message
starts a task whose instruction carries the preceding posts after Tom's
words. A top-level message never stops or steers an older task.

A binding notice goes back in reply only in Tom's own chat: the operator
chat, or his DM on a channel where he replies. About any other message
(a project group, a client channel, an email) it goes to the operator
channel and chat, so kernel text never reaches anyone else. A binding that raises is rolled back
and bound `none` with the error, owing a notice; later messages never wait
behind it. `message.bound` is the first row a binding writes, so a
second binder of the same message stops there and writes nothing. Last in
the same transaction, a message bound to a task on a channel that reacts
gets its reaction row (`reactions.on_bound`).
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from core import channels, ledger, machine, notices, reactions, session, tasks, workspace
from core.machine import State
from core.settings import resolve_seat, settings


@dataclass(frozen=True)
class Inbound:
    channel: str  # "telegram", "email", "local", or "slack"
    # Telegram: marked peer id as text (-100... for groups); email: the
    # thread root; Slack: `<team>/<channel>`, or for a DM the other
    # person's `<team>/<user>`.
    chat_id: str
    chat_kind: str  # "dm", "group", or "email"
    message_id: str  # Telegram: message id as text; email: Message-ID; Slack: `ts`
    sender_id: str  # Telegram: user id as text; email: From address, lowercased; Slack: `<team>/<user>`
    sender_name: str
    sent_at: str  # ISO 8601, UTC
    kind: str = "message"
    text: str = ""  # email: the subject, a blank line, then the body
    reply_to: str | None = None  # Telegram: replied message id; email: In-Reply-To; Slack: thread root
    thread: list[dict] = field(default_factory=list)  # {id, text, attachments}, oldest first
    topic_id: str | None = None  # Telegram forum topic; Slack: `thread_ts`
    attachments: list[dict] = field(default_factory=list)  # {name, mime, bytes, path} or {..., skipped}
    headers: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Received:
    received_id: str
    duplicate: bool  # this (channel, chat_id, message_id) is already recorded


def _verified(inbound: Inbound) -> bool:
    return True


def _unverified(inbound: Inbound) -> bool:
    return False


# Whether a record is verified, by channel, from the channel table.
VERIFY = {c.name: _verified if c.verified else _unverified for c in channels.CHANNELS.values()}

# The local chat page's one chat.
LOCAL_CHAT = "local"


def _replies(channel: str) -> bool:
    """Whether Tom replies on `channel`, so `stop` binds there."""
    row = channels.get(channel)
    return row is not None and row.replies


def _where() -> str:
    channel = settings.operator_channel
    row = channels.get(channel)
    return row.where if row is not None else f"on {channel}"


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


# A Slack `ts`: ten digits, a dot, six digits, so text order is time order.
_TS = "^[0-9]{10}[.][0-9]{6}$"


async def lowest_ts(conn, channel: str, chat_id: str) -> str | None:
    """The smallest Slack `ts` recorded for the chat, compared as text:
    where a gap fill of a chat with no seen entry starts."""
    row = await (
        await conn.execute(
            "SELECT min(payload->>'message_id' COLLATE \"C\") FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'message_id' ~ %s",
            (channel, chat_id, _TS),
        )
    ).fetchone()
    return row[0]


# Every `sent` entry of a send's outcome or a notice, one row each.
_SENT = (
    "FROM events, jsonb_array_elements(COALESCE(payload->'sent', payload->'result'->'sent')) e "
    "WHERE type IN ('notice.sent', 'effect.outcome') "
    "AND jsonb_typeof(COALESCE(payload->'sent', payload->'result'->'sent')) = 'array' "
)


async def threads(conn, channel: str, chat_id: str, since: str) -> set[str]:
    """The roots of the chat's threads with ledger activity at or after
    `since` (ISO 8601): Valor's sends and notices there (a top-level post
    is a root itself), and the threads of messages recorded there."""
    sent = await (
        await conn.execute(
            f"SELECT COALESCE(e->>'thread_ts', e->>'message_id') {_SENT}"
            "AND e->>'channel' = %s AND e->>'chat_id' = %s AND at >= %s",
            (channel, chat_id, since),
        )
    ).fetchall()
    got = await (
        await conn.execute(
            "SELECT payload->>'topic_id' FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'topic_id' IS NOT NULL AND at >= %s",
            (channel, chat_id, since),
        )
    ).fetchall()
    return {r[0] for r in [*sent, *got] if r[0]}


async def root_of(conn, channel: str, chat_id: str, ts: str) -> str | None:
    """The root of the thread `ts` is in, from the ledger: a recorded
    message's `topic_id`, else its own `ts`; a sent entry's `thread_ts`,
    else its `ts`. None when the ledger has neither."""
    row = await (
        await conn.execute(
            "SELECT COALESCE(payload->>'topic_id', payload->>'message_id') FROM events "
            "WHERE type = 'message.received' AND task_id = %s AND payload->>'chat_id' = %s "
            "AND payload->>'message_id' = %s LIMIT 1",
            (channel, chat_id, ts),
        )
    ).fetchone()
    if row is None:
        row = await (
            await conn.execute(
                f"SELECT COALESCE(e->>'thread_ts', e->>'message_id') {_SENT}"
                "AND e->>'channel' = %s AND e->>'chat_id' = %s AND e->>'message_id' = %s LIMIT 1",
                (channel, chat_id, ts),
            )
        ).fetchone()
    return None if row is None else row[0]


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
            f"SELECT e->>'message_id' {_SENT}AND e->>'channel' = %s AND e->>'chat_id' = %s",
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
    if channel == "email":
        ids.extend(settings.operator_email)
    if channel == "slack":
        ids.extend(settings.operator_slack_ids)
    if channel == "local" and settings.operator_channel == "local":
        ids.append(LOCAL_CHAT)
    elif (
        channel in ("telegram", "slack")
        and channel == settings.operator_channel
        and settings.operator_chat
        and settings.operator_chat not in ids
    ):
        ids.append(settings.operator_chat)
    for ident, _ in _project_chats(channel):
        if ident not in ids:
            ids.append(ident)
    return ids


def owns(channel: str, id: str) -> bool:
    row = channels.get(channel)
    return (row.normal(id) if row is not None else id) in owned(channel)


def _from_operator(p: dict[str, Any]) -> bool:
    if not p.get("verified"):
        return False
    if p["channel"] == "telegram":
        return settings.operator_telegram_id is not None and p["sender_id"] == settings.operator_telegram_id
    if p["channel"] == "email":
        return p["sender_id"].lower() in settings.operator_email
    if p["channel"] == "slack":
        return p["sender_id"] in settings.operator_slack_ids
    return p["channel"] == "local"


def team_spec(p: dict[str, Any]) -> workspace.Spec | None:
    """The project whose team chat the message is in: a group chat a spec
    of this machine lists, other than Tom's own chat. None for any other
    message."""
    if not p.get("verified") or p.get("chat_kind") != "group" or _toms_own(p):
        return None
    for chat, spec in _project_chats(p["channel"]):
        if chat == p["chat_id"]:
            return spec
    return None


# A message names Valor (the old system's mention triggers: "valor",
# "@valor", "hey valor").
MENTION = re.compile(r"(?i)(?<![\w])@?valor\b")
# How many of the chat's recorded messages a new chat session starts with.
HISTORY = 20


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
                await _react(conn, p)
        except AlreadyBound:
            continue
        except Exception as exc:  # noqa: BLE001  one message's error never blocks the next
            bound = await _bind_failed(conn, p, exc)
        if bound is not None:
            done.append((p["received_id"], bound))
    return done


async def _react(conn, p: dict[str, Any]) -> None:
    """The reaction the binding owes (`reactions.on_bound`), read from the
    `message.bound` row it just wrote, after the rows it starts a task with."""
    row = await (
        await conn.execute(
            "SELECT payload->>'task_id', payload->>'as' FROM events "
            "WHERE type = 'message.bound' AND payload->>'received_id' = %s",
            (p["received_id"],),
        )
    ).fetchone()
    if row is not None:
        await reactions.on_bound(conn, p, row[0], row[1])


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
            await _notice(conn, task_id or p["channel"], p, await _unread(conn, task_id, p))
    except AlreadyBound:
        return None
    return as_


async def _unread(conn, task_id: str | None, p: dict[str, Any]) -> str:
    """What Tom is told when his message could not be bound: his own words
    quoted, so he knows which; the error stays on `message.bound`."""
    said = notices.short(p.get("text"), 60)
    sentence = (f'your message "{said}"' if said else "your message") + (
        " was not acted on: something went wrong reading it. Send it again, or reply stop."
    )
    return notices.lead(await notices.headline(conn, task_id) if task_id else None, sentence)


async def _replied(conn, p: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """The task a reply belongs to and what it replies to: a notice
    (`{"notice": <notice.requested payload>}`) or a send (`{"send": ...}`).

    On a threaded channel `reply_to` is the thread's root, so the newest
    of Valor's posts in the thread older than the reply binds, the root
    included; on any other, the post `reply_to` names."""
    if not p.get("reply_to"):
        return None, None
    row = None
    kind = channels.get(p["channel"])
    if kind is not None and kind.threaded and p.get("topic_id"):
        row = await (
            await conn.execute(
                f"SELECT task_id, type, payload {_SENT}"
                "AND e->>'channel' = %s AND e->>'chat_id' = %s "
                "AND (e->>'thread_ts' = %s OR e->>'message_id' = %s) "
                "AND (e->>'message_id') COLLATE \"C\" < %s "
                "ORDER BY (e->>'message_id') COLLATE \"C\" DESC, id LIMIT 1",
                (p["channel"], p["chat_id"], p["topic_id"], p["topic_id"], p["message_id"]),
            )
        ).fetchone()
    if row is None:
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


def _toms_own(p: dict[str, Any]) -> bool:
    """Whether the message came from Tom's own chat on a channel where he
    replies: the operator chat, or his DM."""
    channel, chat = p["channel"], p["chat_id"]
    if not _replies(channel):
        return False
    if channel == settings.operator_channel and chat == settings.operator_chat:
        return True
    if channel == "telegram":
        return settings.operator_telegram_id is not None and chat == settings.operator_telegram_id
    if channel == "slack":
        return chat in settings.operator_slack_ids
    return channel == "local" and chat == LOCAL_CHAT


async def _notice(conn, task_id: str, p: dict[str, Any], text: str, suffix: str = "") -> None:
    replies = _toms_own(p)
    await notices.request(
        conn,
        task_id,
        kind="binding",
        about_key=f"reply:{p['received_id']}{suffix}",
        text=text,
        # In reply in Tom's own chat; about any other message, to the
        # operator channel and chat.
        reply_to=p["message_id"] if replies else None,
        chat_id=p["chat_id"] if replies else None,
        channel=p["channel"] if replies else None,
    )


async def _bind(conn, p: dict[str, Any]) -> str | None:
    team = team_spec(p)
    if team is not None:
        return await _chat(conn, p, team)
    if not _from_operator(p):
        return await _bound(conn, p, None, "none")
    task_id, replied = await _replied(conn, p)
    if task_id is None or replied is None:
        posts = await _preceding(conn, p)
        asks = [post for post in posts if post["waits"]]
        if len(asks) == 1 and (bound := await _adjacent(conn, p, asks[0])) is not None:
            return bound
        return await _start(conn, p, posts)
    # Tree, then task: the order every tree writer takes (a stop or
    # feedback below takes the tree's lock again).
    await tasks.lock_tree(conn, task_id)
    await ledger.lock(conn, f"task:{task_id}")
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    text = (p.get("text") or "").strip()
    exact = text.casefold()
    replies = _replies(p["channel"])
    via = p["channel"]
    notice = (replied.get("notice") or {}) if "notice" in replied else {}
    kind, about = notice.get("kind"), notice.get("about_key") or ""
    merge = f.merge_effect or {}
    if (
        kind == "report"
        and f.state is State.MERGED
        and about == f"report:{merge.get('effect_id')}"
        and text
        and not _near(text, "stop")
    ):
        as_ = await _bound(conn, p, task_id, "feedback")
        await session.feedback(conn, task_id, text, by="tom", via=via)
        return as_
    if f.state is State.ENDED and text and not _near(text, "stop"):
        as_ = await _bound(conn, p, task_id, "feedback")
        await session.feedback(conn, task_id, text, by="tom", via=via)
        return as_
    if f.state in (State.MERGED, State.STOPPED):
        as_ = await _bound(conn, p, task_id, "none")
        await _notice(
            conn,
            task_id,
            p,
            notices.lead(
                await notices.headline(conn, task_id),
                f"this work is already {f.state.value}, so your reply changed nothing.",
            ),
        )
        return as_

    if replies and exact == "stop":
        as_ = await _bound(conn, p, task_id, "stop")
        await tasks.stop(conn, task_id, reason=f"Tom replied stop ({p['message_id']})", by="tom", via=via)
        return as_
    if kind == "question" and f.state is State.WAITING and about == f"question:{f.open_question}":
        as_ = await _bound(conn, p, task_id, "answer")
        await session.answer(conn, task_id, text, by="tom", via=via)
        return as_

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
    if _near(text, "stop"):
        said = (
            "that was not a stop, so your message steers the work. To stop it, reply stop."
            if replies
            else f"stops come by reply {_where()}; your email steers the work."
        )
        await _notice(conn, task_id, p, notices.lead(await notices.headline(conn, task_id), said))
    return as_


# How many of Valor's posts a top-level DM message carries, newest kept.
CARRIED = 5


async def _preceding(conn, p: dict[str, Any]) -> list[dict[str, Any]]:
    """Valor's top-level posts in a DM on a threaded channel that a
    top-level message follows: older than it, newer than the chat's
    previous top-level message, oldest first, the newest `CARRIED`. One
    per send or notice, at its first part. Each is `{task_id, ts, kind,
    about_key, text, waits}`; `waits` is true when the post is its task's
    newest here and the task still waits on it for an answer. Empty for a
    reply or any other chat."""
    row = channels.get(p["channel"])
    if p.get("reply_to") or p.get("chat_kind") != "dm" or row is None or not row.threaded:
        return []
    channel, chat, ts = p["channel"], p["chat_id"], p["message_id"]
    (since,) = await (
        await conn.execute(
            "SELECT max(payload->>'message_id' COLLATE \"C\") FROM events WHERE type = 'message.received' "
            "AND task_id = %s AND payload->>'chat_id' = %s AND payload->>'topic_id' IS NULL "
            "AND (payload->>'message_id') COLLATE \"C\" < %s",
            (channel, chat, ts),
        )
    ).fetchone()
    found = await (
        await conn.execute(
            f"SELECT id, task_id, type, payload, e->>'message_id' {_SENT}"
            "AND e->>'channel' = %s AND e->>'chat_id' = %s "
            "AND (e->>'thread_ts' IS NULL OR e->>'thread_ts' = e->>'message_id') "
            "AND (e->>'message_id') COLLATE \"C\" < %s AND (e->>'message_id') COLLATE \"C\" > %s "
            "ORDER BY (e->>'message_id') COLLATE \"C\", id",
            (channel, chat, ts, since or ""),
        )
    ).fetchall()
    first: dict[int, tuple] = {}
    for r in found:
        first.setdefault(r[0], r)
    posts = []
    for _, task_id, type_, payload, at in sorted(first.values(), key=lambda r: (r[4], r[0]))[-CARRIED:]:
        if type_ == "effect.outcome":
            said = await (
                await conn.execute(
                    "SELECT payload->'payload'->>'text' FROM events WHERE type IN ('effect.intent', 'effect.held') "
                    "AND payload->>'effect_id' = %s ORDER BY id LIMIT 1",
                    (payload.get("effect_id"),),
                )
            ).fetchone()
            post = {"kind": "send", "about_key": None, "text": said[0] if said else None}
        else:
            said = await (
                await conn.execute(
                    "SELECT payload FROM events WHERE type = 'notice.requested' AND payload->>'notice_id' = %s",
                    (payload.get("notice_id"),),
                )
            ).fetchone()
            n = said[0] if said else {}
            post = {"kind": n.get("kind"), "about_key": n.get("about_key"), "text": n.get("text")}
        posts.append({"task_id": task_id, "ts": at, **post, "waits": False})
    newest = {post["task_id"]: post for post in posts}
    for task_id, post in newest.items():
        post["waits"] = _waits(machine.fold(await ledger.read(conn, task_id)), post)
    return posts


def _waits(f: machine.Fold, post: dict[str, Any]) -> bool:
    """Whether the task still waits on Tom's answer to `post`, its open
    question's notice."""
    about = post.get("about_key") or ""
    return post["kind"] == "question" and f.state is State.WAITING and about == f"question:{f.open_question}"


async def _adjacent(conn, p: dict[str, Any], post: dict[str, Any]) -> str | None:
    """Bind a top-level DM message as the answer to the one open question
    it follows, with a notice in reply saying so. None, and nothing
    written, when the message has no text or the question is no longer
    open once the task is locked."""
    text = (p.get("text") or "").strip()
    if not text:
        return None
    task_id = post["task_id"]
    await tasks.lock_tree(conn, task_id)
    await ledger.lock(conn, f"task:{task_id}")
    if not _waits(machine.fold(await ledger.read(conn, task_id)), post):
        return None
    as_ = await _bound(conn, p, task_id, "answer")
    await session.answer(conn, task_id, text, by="tom", via=p["channel"])
    said = (
        "I took your message as your answer to my question. "
        "If it was new work, send it again and it will start on its own."
    )
    await _notice(conn, task_id, p, notices.lead(await notices.headline(conn, task_id), said))
    return as_


def _context(posts: list[dict[str, Any]]) -> str:
    """The posts a top-level DM message followed, as a started task's
    instruction carries them."""
    lines = [
        "Tom wrote this in his Slack DM, not in a thread, right after these posts of mine there, oldest first:"
    ]
    for post in posts:
        at = datetime.fromtimestamp(float(post["ts"]), UTC).strftime("%Y-%m-%d %H:%M UTC")
        waits = ", still waiting on his answer" if post["waits"] else ""
        lines.append(f"- {at}, from task {post['task_id']}{waits}:")
        lines += tasks.quoted(post["text"] or "(its text is not in the ledger)")
    return "\n".join(lines)


def _project_for(p: dict[str, Any]) -> workspace.Spec | None:
    ident = p["sender_id"] if p["channel"] == "email" else p["chat_id"]
    for chat, spec in _project_chats(p["channel"]):
        if chat == ident:
            return spec
    try:
        return workspace.Spec.load("valor")
    except workspace.Refused, FileNotFoundError:
        return None


async def _start(conn, p: dict[str, Any], posts: list[dict[str, Any]] | None = None) -> str | None:
    """A new task from Tom's message, under the project listing the chat
    (by email: the sender), else `valor`. Its workspace is provisioned by a
    kernel job after it starts. A message of files alone starts a task
    whose instruction lists them; one that followed Valor's `posts` in a
    DM carries them after Tom's words."""
    text = (p.get("text") or "").strip()
    files = _files(p.get("attachments") or [])
    if files:
        text = f"{text}\n\n{files}" if text else f"Tom sent files with no text:\n{files}"
    if not text:
        return await _bound(conn, p, None, "none")
    if posts:
        text = f"{text}\n\n{_context(posts)}"
    spec = _project_for(p)
    harness_name, model = resolve_seat("frontier")
    brief = tasks.Brief(
        instruction=text,
        model=model,
        harness_name=harness_name,
        project={"name": spec.name} if spec else None,
        max_effect_class="act",
    )
    as_ = await _bound(conn, p, brief.id, "start")
    await tasks.start(conn, brief, by="tom", via=p["channel"])
    return as_


async def _chat(conn, p: dict[str, Any], spec: workspace.Spec) -> str:
    """Bind a team chat's message to its chat session, started here when
    the chat has none open."""
    text = (p.get("text") or "").strip()
    if not text and not p.get("attachments"):
        return await _bound(conn, p, None, "none")
    replied, _ = await _replied(conn, p)
    wakes = not _mentions_only(spec, p) or bool(MENTION.search(text)) or replied is not None
    await ledger.lock(conn, f"chat:{p['channel']}:{p['chat_id']}")
    task_id = await chat_session(conn, p["channel"], p["chat_id"])
    brief = None
    if task_id is None:
        harness_name, model = resolve_seat("frontier")
        brief = tasks.Brief(
            instruction=await _chat_start(conn, p, spec),
            model=model,
            harness_name=harness_name,
            project={"name": spec.name},
            max_effect_class="act",
            chat={"channel": p["channel"], "chat_id": p["chat_id"]},
        )
        task_id = brief.id
    as_ = await _bound(conn, p, task_id, "steer" if wakes else "heard")
    by = "tom" if _from_operator(p) else (p.get("sender_name") or p["sender_id"])
    if brief is not None:
        await tasks.start(conn, brief, by=by, via=p["channel"])
    await ledger.lock(conn, f"task:{task_id}")
    keep = (
        "received_id",
        "message_id",
        "reply_to",
        "sender_id",
        "sender_name",
        "sent_at",
        "text",
        "attachments",
    )
    await ledger.append(
        conn,
        task_id,
        "message.steered" if wakes else "message.heard",
        {**{k: p.get(k) for k in keep}, "provenance": ledger.provenance(by, p["channel"], False)},
    )
    return as_


def _mentions_only(spec: workspace.Spec, p: dict[str, Any]) -> bool:
    row = channels.get(p["channel"])
    chat = row.normal(p["chat_id"]) if row is not None else p["chat_id"]
    return f"{p['channel']}:{chat}" in spec.mentions


async def chat_session(conn, channel: str, chat_id: str) -> str | None:
    """The chat's open chat session: the newest root task naming the chat
    that is not merged, ended or stopped. None when it has none."""
    rows = await (
        await conn.execute(
            "SELECT id FROM documents WHERE kind = 'task' AND body->'chat'->>'channel' = %s "
            "AND body->'chat'->>'chat_id' = %s AND body->>'parent_id' IS NULL "
            "ORDER BY body->>'created_at' DESC",
            (channel, chat_id),
        )
    ).fetchall()
    for (task_id,) in rows:
        if machine.fold(await ledger.read(conn, task_id)).state not in tasks.TERMINAL:
            return task_id
    return None


async def _chat_start(conn, p: dict[str, Any], spec: workspace.Spec) -> str:
    """A new chat session's instruction: the chat it serves, and the
    chat's last `HISTORY` recorded messages before this one, oldest first."""
    rows = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND task_id = %s "
            "AND payload->>'chat_id' = %s AND id < (SELECT id FROM events WHERE type = 'message.received' "
            "AND payload->>'received_id' = %s) ORDER BY id DESC LIMIT %s",
            (p["channel"], p["chat_id"], p["received_id"], HISTORY),
        )
    ).fetchall()
    lines = [
        (
            f"You are in the {spec.name} team chat on {p['channel'].capitalize()} (chat id {p['chat_id']}). "
            "This session is the chat's: every message said in it comes to you here."
        )
    ]
    if rows:
        lines += ["", "What was said before this session began, oldest first:"]
        lines += [f"- {chat_line(r[0])}" for r in reversed(rows)]
    return "\n".join(lines)


def chat_line(m: dict[str, Any]) -> str:
    """One chat message as a chat session reads it: when, who, its id and
    the message it replies to, its words, and its files."""
    try:
        when = datetime.fromisoformat(str(m.get("sent_at"))).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        when = "an unknown time"
    who = m.get("sender_name") or m.get("sender_id") or "someone"
    ids = f"message {m.get('message_id')}" + (f", replying to {m['reply_to']}" if m.get("reply_to") else "")
    files = _files(m.get("attachments") or [])
    said = (m.get("text") or "").strip() or "(no text)"
    return f"[{when}] {who} ({ids}): {said}" + (f"\n{files}" if files else "")


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
