"""The inbound record and binding.

A bridge records each message it receives with `receive`, on the
channel's stream, then acknowledges it to the platform. The kernel binds
each recorded message (`bind`) to what it means: an answer, feedback, a
stop, a steer of a running task or of a chat's session, or a new task. A task a message starts
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
addresses (Tom's email addresses), and the chats a project spec lists
(`chats`) whose `machine` is this one; a spec naming no machine belongs to
`settings.default_machine`. Tom's Telegram and Slack DMs are owned only
through a spec (`valor.toml`), so a second Mac's bridge never takes them. The operator chat is the
bridge's whose channel is `settings.operator_channel`: the Telegram
operator chat, a Slack chat, or the one local chat, `local`.

A *team chat* is a group chat a spec of this machine lists, other than
Tom's own chat (`team_spec`); someone else's DM a spec lists is one too
(the old system answered them), with them as its one other member. It has one long-lived chat session: a task
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

An *email thread* is a chat too (Tom, 2026-10-17: "email threads are chat
threads too"). Its id is the thread's root (`chat_id`, the first id in
`References`), and it has one chat session, under the project listing
the sender (`email:<address>`), else its domain (`email:@<domain>`),
else `valor`. Every email the bridge
records in it, Tom's or a project sender's, is a `message.steered` row
on that session naming its sender, as in a team chat. No email is
verified, so none carries Tom's rights: `stop` there is a steer.

*Tom's own chat* (`_toms_own`: the operator chat, his Telegram DM, his
Slack DMs, the local chat) has a chat session too, under the project
listing the chat, else `valor` (`_project_for`). Only Tom is heard there,
and every message of his, a reply under any post included, is a
`message.steered` row on the chat's open session (one that ended goes to a
new one): only a chat session speaks as Valor, so only it answers him
(docs/plans/one-voice.md; on 2026-11-01 his questions in a routine
report's thread were bound as feedback and never answered). A reply under
one of Valor's posts carries the post (`replying_to`, `_replying_to`): its
text, and its task when that is no chat session, which from then on
reports to the session (`tasks.reporting`), so the session answers, steers
or reopens it (`tools/chat_tasks.py`). Stop is immediate there by
structure: an exact `stop` stops at once the task whose post it replies
to, else every open task that reports to the session, by Tom, never the
session itself. With no project to put the session under, the message
starts a task.

A chat's session is picked under the chat's lock (`open_session`) and
read again once its tree and task locks are held; if it ended in between,
a new session takes the message, in the same transaction. A report with a
chat to go to and no open session there starts one (`session_for`); the
operator chat's session takes the reports of work under no chat session
(`operator_session`, `core/notices.py`).

The binding table, first match wins:

| The record | Bound as |
| --- | --- |
| In Valor's engineering room (its spec's `update_chat`), exactly `/update` or `/update@<bot>` | `update`: an update request (`request_update`), its report to the room's session when it ends |
| In a team chat, with no text or files | `none` |
| In a team chat, waking the chat session | `steer`, to the chat session |
| In a team chat, otherwise | `heard`, to the chat session |
| An email, with no text or files | `none` |
| An email | `steer`, to the thread's session |
| Not verified, or not from the operator | `none` |
| In Tom's own chat, with no text or files | `none` |
| In Tom's own chat, exactly `stop` | `stop`: the replied-to task, else each open task reporting to the session, stopped, and a steer to the session |
| In Tom's own chat | `steer`, to the chat's session |
| Anywhere else, with text or files (a chat with no project) | `start` |
| Anywhere else, empty | `none` |

"Exactly" is the whole text, trimmed and casefolded (`/update`: trimmed,
any verified member of the room).

A chat session passes what its chat says about a task that reports to it
with `task.steer` (`tools/chat_tasks.py`): on a task in `merged` or
`ended` it is `feedback` (with the sender's name, via `chat`), on a
stopped task it stays a steer, otherwise it is a steer; and answers its
question with `task.answer`.

On a threaded channel (Slack) a reply names its thread's root, not the
message it answers, so a reply resolves to the newest of Valor's posts in
that thread that is older than it (the root included), read from the
ledger's `sent` entries: a session's sends, and the kernel's notices
before 2026-11-01.

A binding that raises is rolled back and bound `none` with the error,
reported to the session (`notices.request`, kind `binding`); later
messages never wait behind it. `message.bound` is the first row a binding
writes, so a second binder of the same message stops there and writes
nothing. Last in the same transaction, a message bound to a task on a
channel that reacts gets its reaction row (`reactions.on_bound`), and a
session reads each message with the reaction on it (`chat_line`).
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from core import channels, ledger, machine, notices, reactions, tasks, workspace
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
    kind: str = "message"  # or "reaction": Tom's reaction on Valor's Slack post, its text the emoji's name
    text: str = ""  # email: the subject, a blank line, then the body
    reply_to: str | None = None  # Telegram: replied message id; email: In-Reply-To; Slack: thread root
    thread: list[dict] = field(default_factory=list)  # {id, text, attachments}, oldest first
    topic_id: str | None = None  # Telegram forum topic; Slack: `thread_ts`
    # {name, mime, bytes, path} or {..., skipped}; audio also has transcript or transcript_failed
    attachments: list[dict] = field(default_factory=list)
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


def _taking(channel: str, chat_id: str) -> tuple[str, workspace.Spec] | None:
    """The spec of this machine that takes the chat, and the `chats` entry
    it takes it by: one listing it, else one whose `slack:<team>/*` covers
    a Slack DM that no spec on any Mac lists by id (`Spec.takes`). A spec
    that does not load lists nothing. None when no spec here takes it."""
    row = channels.get(channel)
    key = f"{channel}:{row.normal(chat_id) if row is not None else chat_id}"
    every = workspace.Spec.all()
    here = [s for s in every if _spec_machine(s) == settings.machine]
    for spec in here:
        if key in spec.chats:
            return key, spec
    if any(key in s.chats for s in every):
        return None
    for spec in here:
        if (found := spec.takes(key)) is not None:
            return found, spec
    return None


def _caught(channel: str, chat_id: str) -> bool:
    """Whether a workspace's `/*` entry takes the chat here, not its id."""
    taken = _taking(channel, chat_id)
    return taken is not None and taken[0].endswith("/*")


# What Tom's own chat is for (his Telegram and Slack DMs, the local chat;
# the operator group's own spec entry wins), and an email thread.
TOMS_OWN_ROOM = "Running the business with Tom, Yudame first; engineering only when he starts it."
EMAIL_ROOM = "Valor's own correspondence for the business."


def room(channel: str, chat_id: str, project: str | None = None) -> str | None:
    """What the chat is for: its `rooms` entry in a spec this machine
    owns, else Tom's own chat's, else for an email thread its `project`'s
    `rooms.email` or `EMAIL_ROOM`, else None (its session's opening names
    only its project and kind)."""
    taken = _taking(channel, chat_id)
    if taken is not None and taken[0] in taken[1].rooms:
        return taken[1].rooms[taken[0]]
    if _toms_own({"channel": channel, "chat_id": chat_id}):
        return TOMS_OWN_ROOM
    if channel != "email":
        return None
    return workspace.Spec.load(project).rooms.get("email", EMAIL_ROOM) if project else EMAIL_ROOM


def role(channel: str, chat_id: str, project: str | None = None) -> str | None:
    """The role Valor plays in the chat (`skills/sdlc/roles/<role>.md`): its
    `chat_roles` entry in a spec this machine owns, else for an email thread
    its `project`'s `chat_roles.email`, else None, as in Tom's own chats."""
    taken = _taking(channel, chat_id)
    if taken is not None and taken[0] in taken[1].chat_roles:
        return taken[1].chat_roles[taken[0]]
    if channel != "email" or not project or _toms_own({"channel": channel, "chat_id": chat_id}):
        return None
    return workspace.Spec.load(project).chat_roles.get("email")


def owned(channel: str) -> list[str]:
    """Every id this machine's bridge on `channel` receives."""
    ids: list[str] = []
    if channel == "email":
        ids.extend(settings.operator_email)
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
        if ident not in ids and not ident.endswith("/*"):
            ids.append(ident)
    return ids


def owns(channel: str, id: str) -> bool:
    row = channels.get(channel)
    ident = row.normal(id) if row is not None else id
    ids = owned(channel)
    return (
        ident in ids
        or (channel == "email" and _domain(ident) in ids)
        or (channel == "slack" and _taking(channel, ident) is not None)
    )


def _domain(address: str) -> str:
    """An email chats entry naming every sender at the address's domain:
    `@northwind.example` for `someone@client.example`."""
    return "@" + address.rpartition("@")[2]


def _from_operator(p: dict[str, Any]) -> bool:
    if not p.get("verified"):
        return False
    if p["channel"] == "telegram":
        return settings.operator_telegram_id is not None and p["sender_id"] == settings.operator_telegram_id
    if p["channel"] == "slack":
        return p["sender_id"] in settings.operator_slack_ids
    return p["channel"] == "local"


def team_spec(p: dict[str, Any]) -> workspace.Spec | None:
    """The project whose team chat the message is in: a group chat or
    someone else's DM a spec of this machine lists, other than Tom's own
    chat. None for any other message."""
    if not p.get("verified") or p.get("chat_kind") not in ("group", "dm") or _toms_own(p):
        return None
    taken = _taking(p["channel"], p["chat_id"])
    return taken[1] if taken is not None else None


# A message names Valor (the old system's mention triggers: "valor",
# "@valor", "hey valor", and his Telegram handle, @valorengels). A Slack tag
# of Valor arrives labelled `<@U..|valor>` by its bridge.
MENTION = re.compile(r"(?i)(?<![\w])@?valor(?:engels)?\b")
# How many of the chat's recorded messages a new chat session starts with.
HISTORY = 20
# Valor's engineering room is the valor spec's `update_chat`: the one chat
# where a message updates this Mac (Tom's grant, docs/plans/update.md; guard
# `update.room`).
# Until every Mac's private valor spec names it (a Mac on older code refuses
# the key), the room it had in code; this goes in stage 2b
# (docs/plans/private-projects.md).
UPDATE_CHAT = "telegram:-1003449100931"
UPDATE_COMMAND = re.compile(r"/update(@\w+)?")
# The stream of every update request and its `rollout.*` rows.
UPDATES = "updates"
# The open update request, `(row, request_id)`: no `rollout.ended` names it.
OPEN_UPDATE = (
    "SELECT r.id, r.payload->>'request_id' FROM events r WHERE r.task_id = 'updates' "
    "AND r.type = 'update.requested' AND NOT EXISTS (SELECT 1 FROM events e WHERE "
    "e.type = 'rollout.ended' AND e.payload->>'effect_id' = r.payload->>'request_id') ORDER BY r.id LIMIT 1"
)


async def request_update(conn, *, by: str, via: str, reply: dict[str, str] | None = None) -> str:
    """Ask the kernel to roll this Mac to its branch's tip (`Kernel.roll`):
    the open request's id, else a new `update.requested` row's on
    `UPDATES`; a request made while one is open joins it, unreplied. Called
    by `python -m core update`, the room, and the update routine."""
    async with conn.transaction():
        await ledger.lock(conn, UPDATES)
        found = await (await conn.execute(OPEN_UPDATE)).fetchone()
        if found is not None:
            return found[1]
        request_id = ledger.new_id()
        payload = {"request_id": request_id, "by": by, "via": via} | ({"reply": reply} if reply else {})
        await ledger.append(conn, UPDATES, "update.requested", payload)
        return request_id


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
    except Exception:  # noqa: BLE001, S110  the report comes from the channel's stream instead
        pass
    try:
        async with conn.transaction():
            as_ = await _bound(conn, p, task_id, "none", error=repr(exc))
            await notices.request(
                conn,
                task_id or p["channel"],
                kind="binding",
                about_key=f"reply:{p['received_id']}",
                text=await _unread(conn, task_id, p),
            )
    except AlreadyBound:
        return None
    return as_


async def _unread(conn, task_id: str | None, p: dict[str, Any]) -> str:
    """What the session hears when a message could not be bound: its words
    quoted, where and who sent it; the error stays on `message.bound`."""
    said = notices.short(p.get("text"), 60)
    who = "Tom" if _from_operator(p) else (p.get("sender_name") or "someone")
    sentence = (
        f"{who}'s message {p['message_id']} on {p['channel']}"
        + (f' ("{said}")' if said else "")
        + " was not acted on: something went wrong reading it."
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
        # His DM in a workspace whose `/*` entry takes it is a teammate DM
        # like anyone's (Tom, 2026-11-03).
        return chat in settings.operator_slack_ids and not _caught(channel, chat)
    return channel == "local" and chat == LOCAL_CHAT


async def _bind(conn, p: dict[str, Any]) -> str | None:
    team = team_spec(p)
    if team is None and p["channel"] == "email":
        team = _project_for(p)
    if team is not None:
        text = (p.get("text") or "").strip()
        if f"{p['channel']}:{p['chat_id']}" == (team.update_chat or UPDATE_CHAT) and UPDATE_COMMAND.fullmatch(
            text
        ):
            as_ = await _bound(conn, p, None, "update")
            by = "tom" if _from_operator(p) else (p.get("sender_name") or p["sender_id"])
            reply = {"channel": p["channel"], "chat_id": p["chat_id"], "message_id": p["message_id"]}
            await request_update(conn, by=by, via=p["channel"], reply=reply)
            return as_
        return await _chat(conn, p, team)
    if not _from_operator(p):
        return await _bound(conn, p, None, "none")
    spec = _project_for(p) if _toms_own(p) else None
    if spec is not None:
        return await _chat(conn, p, spec, own=True)
    return await _start(conn, p)


def _project_for(p: dict[str, Any]) -> workspace.Spec | None:
    """The spec listing the chat; for an email, the sender's address, else
    its domain (`email:@<domain>`); else `valor`."""
    ident = p["sender_id"] if p["channel"] == "email" else p["chat_id"]
    found = dict(reversed(_project_chats(p["channel"])))
    if p["channel"] == "email" and ident not in found:
        ident = _domain(ident)
    if ident in found:
        return found[ident]
    try:
        return workspace.Spec.load("valor")
    except workspace.Refused, FileNotFoundError:
        return None


async def _start(conn, p: dict[str, Any]) -> str | None:
    """A new task from Tom's message, under the project listing the chat,
    else `valor`. Its workspace is provisioned by a
    kernel job after it starts. A message of files alone starts a task
    whose instruction lists them."""
    text = (p.get("text") or "").strip()
    files = _files(p.get("attachments") or [])
    if files:
        text = f"{text}\n\n{files}" if text else f"Tom sent files with no text:\n{files}"
    if not text:
        return await _bound(conn, p, None, "none")
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


async def _chat(conn, p: dict[str, Any], spec: workspace.Spec, own: bool = False) -> str:
    """Bind a message to its chat's session, started here when the chat
    has none open: a team chat's, or with `own` Tom's own chat's, where
    every message wakes it and an exact `stop` also stops at once the task
    whose post it replies to, else every open task that reports to the
    session. A session picked here and found ended once its locks are held
    gives way to a new one. A reply under one of Valor's posts carries the
    post (`replying_to`: its text, and the task when that is no chat
    session, which then reports to this one, `tasks.reporting`)."""
    text = (p.get("text") or "").strip()
    if not text and not p.get("attachments"):
        return await _bound(conn, p, None, "none")
    replied, post = await _replied(conn, p)
    wakes = own or not _mentions_only(spec, p) or bool(MENTION.search(text)) or replied is not None
    channel, chat = p["channel"], p["chat_id"]
    task_id, brief = await open_session(conn, channel, chat, spec, own=own, before=p["received_id"])
    if brief is None:
        # Tree, then task, the order every tree writer takes; a stop that
        # landed after the pick shows in the fold read under them.
        await tasks.lock_tree(conn, task_id)
        await ledger.lock(conn, f"task:{task_id}")
        if machine.fold(await ledger.read(conn, task_id)).state in tasks.TERMINAL:
            brief = await _session_brief(conn, channel, chat, spec, own=own, before=p["received_id"])
            task_id = brief.id
    stop = own and text.casefold() == "stop"
    as_ = await _bound(conn, p, task_id, "stop" if stop else "steer" if wakes else "heard")
    by = "tom" if _from_operator(p) else (p.get("sender_name") or p["sender_id"])
    if brief is not None:
        await tasks.start(conn, brief, by=by, via=channel)
        await ledger.lock(conn, f"task:{task_id}")
    keep = (
        "received_id",
        "message_id",
        "reply_to",
        "sender_id",
        "sender_name",
        "sent_at",
        "kind",
        "text",
        "attachments",
    )
    under = await _replying_to(conn, replied, post)
    await ledger.append(
        conn,
        task_id,
        "message.steered" if wakes else "message.heard",
        {
            **{k: p.get(k) for k in keep},
            "thread_tag": thread_tag(p),
            **({"replying_to": under} if under else {}),
            "provenance": ledger.provenance(by, channel, False),
        },
    )
    if stop:
        reach = [under["task_id"]] if under and under.get("task_id") else await tasks.reporting(conn, task_id)
        for t in reach:
            if machine.fold(await ledger.read(conn, t)).state not in tasks.TERMINAL:
                await tasks.stop(conn, t, reason=f"Tom said stop ({p['message_id']})", by="tom", via=channel)
    return as_


# How much of the post a reply is under the session reads with it.
POST_CHARS = 500


async def _replying_to(conn, task_id: str | None, post: dict[str, Any] | None) -> dict[str, Any] | None:
    """The post a reply is under, as its session reads it: its text, cut
    at `POST_CHARS`, and its task when that is no chat session. None when
    the reply resolves to no post of Valor's."""
    if task_id is None or post is None:
        return None
    if "notice" in post:
        text = str(post["notice"].get("text") or "")
    else:
        held = await (
            await conn.execute(
                "SELECT payload->'payload'->>'text' FROM events WHERE type IN ('effect.held', 'effect.intent') "
                "AND payload->>'effect_id' = %s ORDER BY id LIMIT 1",
                (post["send"].get("effect_id"),),
            )
        ).fetchone()
        text = (held and held[0]) or ""
    if len(text) > POST_CHARS:
        text = text[: POST_CHARS - 3] + "..."
    try:
        session = bool((await tasks.brief(conn, task_id)).chat)
    except KeyError:
        session = True  # a stream's post: no task to reach
    return {"text": text, **({} if session else {"task_id": task_id})}


def _mentions_only(spec: workspace.Spec, p: dict[str, Any]) -> bool:
    row = channels.get(p["channel"])
    chat = f"{p['channel']}:{row.normal(p['chat_id']) if row is not None else p['chat_id']}"
    return (spec.takes(chat) or chat) in spec.mentions


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


async def open_session(
    conn, channel: str, chat_id: str, spec: workspace.Spec, *, own: bool, before: str | None = None
) -> tuple[str, tasks.Brief | None]:
    """Under the chat's lock: its open session and None, or a new
    session's id and its Brief, for the caller to start in the same
    transaction. `before` is the received id the new session's history
    stops at."""
    await ledger.lock(conn, f"chat:{channel}:{chat_id}")
    task_id = await chat_session(conn, channel, chat_id)
    if task_id is not None:
        return task_id, None
    brief = await _session_brief(conn, channel, chat_id, spec, own=own, before=before)
    return brief.id, brief


async def operator_session(conn) -> str | None:
    """The session of the operator chat (`settings.operator_channel` and
    `operator_chat`), started when it has none open; None when that chat
    is not Tom's own chat or has no project to work under, or is one of
    his DMs that no spec placed on this machine lists: owned only through
    a spec, its replies reach another Mac's bridge, never this one's."""
    channel = settings.operator_channel
    chat = LOCAL_CHAT if channel == "local" else settings.operator_chat
    p = {"channel": channel, "chat_id": chat, "sender_id": ""}
    if not chat or not _toms_own(p):
        return None
    dm = chat == settings.operator_telegram_id or chat in settings.operator_slack_ids
    if dm and chat not in (ident for ident, _ in _project_chats(channel)):
        return None
    return await session_for(conn, channel, chat)


async def session_for(conn, channel: str, chat_id: str, spec: workspace.Spec | None = None) -> str | None:
    """The chat's open session, started when it has none (a report that
    has a chat to go to, `core/notices.py`): under `spec`, else the spec
    listing the chat, else for Tom's own chat the one `_project_for` names.
    None when no project takes the chat."""
    found = await chat_session(conn, channel, chat_id)
    if found is not None:
        return found
    p = {"channel": channel, "chat_id": chat_id, "sender_id": chat_id}
    own = _toms_own(p)
    taken = _taking(channel, chat_id)
    spec = spec or (taken[1] if taken else None) or (_project_for(p) if own else None)
    if spec is None:
        return None
    task_id, brief = await open_session(conn, channel, chat_id, spec, own=own)
    if brief is not None:
        await tasks.start(conn, brief, by="valor", via="kernel")
    return task_id


async def _session_brief(
    conn, channel: str, chat_id: str, spec: workspace.Spec, *, own: bool, before: str | None
) -> tasks.Brief:
    harness_name, model = resolve_seat("frontier")
    return tasks.Brief(
        instruction=await _chat_start(conn, channel, chat_id, spec, own, before),
        model=model,
        harness_name=harness_name,
        project={"name": spec.name},
        max_effect_class="act",
        chat={"channel": channel, "chat_id": chat_id},
    )


async def _chat_start(
    conn, channel: str, chat_id: str, spec: workspace.Spec, own: bool, before: str | None
) -> str:
    """A new chat session's instruction: the chat it serves (a team chat,
    someone else's DM, an email thread, or with `own` Tom's own chat on its channel), and the chat's last
    `HISTORY` recorded messages (before the one received as `before`),
    oldest first."""
    rows = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND task_id = %s "
            "AND payload->>'chat_id' = %s AND (%s::text IS NULL OR id < (SELECT id FROM events "
            "WHERE type = 'message.received' AND payload->>'received_id' = %s)) ORDER BY id DESC LIMIT %s",
            (channel, chat_id, before, before, HISTORY),
        )
    ).fetchall()
    where = "the local chat page" if channel == "local" else channel.capitalize()
    dm = await (
        await conn.execute(
            "SELECT payload->>'sender_name' FROM events WHERE type = 'message.received' AND task_id = %s "
            "AND payload->>'chat_id' = %s AND payload->>'chat_kind' = 'dm' ORDER BY id DESC LIMIT 1",
            (channel, chat_id),
        )
    ).fetchone()
    lines = [
        (
            f"You are in an email thread (its first message {chat_id}); your work here is under the "
            f"{spec.name} project. This session is the thread's: every email in it comes to you here, "
            "its sender as its From header names them."
            if channel == "email"
            else f"You are in Tom's own chat on {where} (chat id {chat_id}), a 1:1 with him, not a team "
            f"chat; your work here is under the {spec.name} project. This session is the chat's: every "
            "message Tom sends in it comes to you here."
            if own
            else f"You are in a direct message with {dm[0] or 'someone'} on {where} (chat id {chat_id})"
            f"{'' if chat_id in settings.operator_slack_ids else ', not Tom'}; your work here is under the {spec.name} project. This session is the chat's: every "
            "message they send in it comes to you here."
            if dm and channel != "email"
            else f"You are in the {spec.name} team chat on {where} (chat id {chat_id}). "
            "This session is the chat's: every message said in it comes to you here."
        )
    ]
    if rows:
        shown = await reactions.shown_for(conn, [r[0].get("received_id") for r in rows])
        lines += ["", "What was said before this session began, oldest first:"]
        lines += [f"- {chat_line(r[0], shown.get(r[0].get('received_id')))}" for r in reversed(rows)]
    return "\n".join(lines)


def chat_line(m: dict[str, Any], reaction: str | None = None) -> str:
    """One chat message as a chat session reads it: when, who, its id and
    the message it replies to, the reaction on it (`reactions.shown`), its
    words, and its files; Tom's reaction on a post, as that."""
    try:
        when = datetime.fromisoformat(str(m.get("sent_at"))).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        when = "an unknown time"
    who = m.get("sender_name") or m.get("sender_id") or "someone"
    if m.get("kind") == "reaction":
        return f"[{when}] {who} reacted {m.get('text')} to your post {m.get('reply_to')}"
    ids = f"message {m.get('message_id')}" + (f", replying to {m['reply_to']}" if m.get("reply_to") else "")
    where = m["thread_tag"] if "thread_tag" in m else thread_tag(m)
    ids += f", {where}" if where else ""
    ids += f"; {reaction}" if reaction else ""
    files = _files(m.get("attachments") or [])
    said = (m.get("text") or "").strip() or "(no text)"
    under = m.get("replying_to")
    post = ""
    if under:
        whose = f"task {under['task_id']}'s post" if under.get("task_id") else "your post"
        quoted = (
            "\n".join("  > " + line for line in str(under.get("text") or "").splitlines()) or "  > (no text)"
        )
        post = f"\n  under {whose}:\n{quoted}"
    return f"[{when}] {who} ({ids}): {said}" + post + (f"\n{files}" if files else "")


_FILLER = frozenset(
    re.findall(
        r"\w+",
        "a an the and or but so if of to in on at by for with from into about as is are was were be been "
        "it its this that these those i im ive id me my we our you youre your he she they them their "
        "do does did dont doesnt didnt can cant could would should will wont just also maybe some any "
        "not no yes ok okay hey hi hello please thanks thank here there what when where why how which "
        "who all very really think wondering valor",
    )
)
_ISSUE = re.compile(r"https?://github\.com/[^\s>|]+/(issues|pull)/(\d+)\S*")


def slug(text: str) -> str:
    """A thread's tag from its first message's words: Slack's markup read
    as its words (a mention, link markup, a channel name), a GitHub issue
    or pull link as `issue N` / `pr N`, other links dropped; the first
    three words that are not filler, then the first number when none of
    them is one. Empty when no word is left."""
    text = re.sub(r"<@[^>]*>|<![^>]*>", " ", text)
    text = re.sub(r"<(?:[^>|]*\|)([^>]*)>", r" \1 ", text)
    text = _ISSUE.sub(
        lambda g: f" {'issue' if g[1] == 'issues' else 'pr'} {g[2]} ",
        text.replace("<", " ").replace(">", " "),
    )
    text = re.sub(r"https?://\S+", " ", text).casefold().replace("'", "").replace("\u2019", "")
    tokens = re.findall(r"[a-z0-9]+", text)
    words = [t for t in tokens if not t.isdigit() and t not in _FILLER][:3]
    number = next((t for t in tokens if t.isdigit()), None)
    return "-".join(words + ([number] if number else [])) if words or number else ""


def thread_tag(m: dict[str, Any]) -> str | None:
    """Where a Slack or Telegram message sits in its chat, for the session
    reading it (Tom, team chat, 2026-10-29): `in #tag` for a message in a
    thread, the tag slugged from the thread's first message (`slug`); `top-
    level, starts #tag` for one not in a thread, slugged from its own text,
    the tag its replies will carry. A Telegram forum topic is `in
    #topic-<id>` for every message in it; a Telegram reply chain also names
    its first message's id, since its `replying to` is the message
    answered, not the first (the chain as recorded: one whose first message
    was deleted starts at the oldest left). None on another channel, and
    for a reply whose first message's text is not recorded."""
    channel = m.get("channel")
    if channel not in ("slack", "telegram"):
        return None
    if channel == "telegram" and m.get("topic_id"):
        return f"in #topic-{m['topic_id']}"
    if m.get("reply_to"):
        first = (m.get("thread") or [None])[0]
        tag = slug(str(first.get("text") or "")) if first else ""
        if not tag:
            return None
        return f"in #{tag}" + (f", from {first.get('id')}" if channel == "telegram" else "")
    tag = slug(str(m.get("text") or ""))
    return f"top-level, starts #{tag}" if tag else "top-level"


def _files(attachments: list[dict[str, Any]]) -> str:
    """One line per attachment: its name, type, and path, or why it was
    not downloaded; under an audio file, its transcript or why there is
    none."""
    lines = []
    for a in attachments:
        where = a.get("path") or f"not downloaded: {a.get('skipped')}"
        lines.append(
            f"- {a.get('name') or 'file'} ({a.get('mime') or 'unknown type'}, {a.get('bytes')} bytes): {where}"
        )
        if "transcript" in a:
            lines.append(f"  transcript: {a['transcript'] or '(no speech heard)'}")
        elif "transcript_failed" in a:
            lines.append(f"  not transcribed: {a['transcript_failed']}")
    return "\n".join(lines)
