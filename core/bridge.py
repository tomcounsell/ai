"""The bridge port: what a channel's bridge process builds against.

A bridge (Telegram, email, Slack, local) is a process of its own. It receives messages
and records them through `core.intake`, and it sends what the outbox
yields: a send a task requested, or an operator notice. It decides
nothing: the kernel binds every message, refuses or releases every send at
request, and checks every release. A bridge imports `core.bridge`, `core.intake`, `core.broker`,
`core.settings`, `core.db`, and `core.credentials`, and nothing else from
`core/`.

The kernel declares each channel's send type here (`DECLARED`): its class,
the usage line a turn reads, and what refuses it. Every task's Performers holds
them, so a turn can request a send and `request` releases it to its
channel's bridge. The
bridge supplies only how to send (`perform`) and how to find a send that
happened (`lookup`); `serve` joins the two into the performer the broker
runs.

Each task's declared sends carry its workspace. A file a send names must
be a regular file there, reached through no link; the kernel takes its
size from the opened file's `fstat` and never reads it, and a file that is
missing, a link, or outside the workspace gets one answer, so a refusal
says nothing about a path outside. Whether the bytes are the ones the
request named is the bridge's: `perform` reads each file once and refuses one
whose sha256 differs.

The limits (`LIMITS`) are protocol facts, so the kernel refuses an
impossible send at request time without importing a bridge:

- Telegram: 4096 UTF-16 code units per message after entity parsing (its
  message length limit); 2000 MiB per file (its upload documentation:
  4000 parts of 512 KiB).
- Email: no per-message text limit. Gmail refuses a message over 25 MB
  ("Gmail sending limits in Google Workspace", Admin Help: maximum email
  size 25 MB), counted as 25,000,000 bytes of the whole encoded message.
  `message_bytes` measures it with `core.mail`, the builder the email
  bridge's performer sends from, so the size refused is the size sent.
- Slack: 4,000 characters per post (Slack's stated guidance for
  `chat.postMessage`, which truncates past 40,000); 1 GB per file (its
  file upload limit).
- Local: none. The local chat page shows any text and no files.
- Meet: 500 characters a post, the same cap for a sentence spoken; no files.
  A call's own process (`core/call.py`) performs both, never a bridge's
  outbox, and turns are not offered them (docs/plans/meet.md).
"""

import asyncio
import dataclasses
import json
import os
import time
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psycopg
from aiohttp import web

from core import broker, db, git, ledger, mail, reactions, tasks
from core import workspace as ws
from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent
# The stamp of a bridge that could not read its head (`stamp`).
NO_HEAD = "none"

# The email bridge builds and measures its messages with the kernel's
# builder, reached here (a bridge imports core.bridge, never core.mail).
email_message = mail.email_message
email_serialized = mail.serialized
email_message_id = mail.message_id
email_reply_all = mail.reply_all


@dataclass(frozen=True)
class ChannelLimits:
    max_text: int | None  # per message, in text_units
    text_units: str  # "utf16" or "chars"
    max_file_bytes: int | None  # per file
    max_message_bytes: int | None = None  # the whole message, measured by message_bytes
    message_bytes: Callable[[broker.Action, list[int]], int] | None = None  # given the file sizes


def _email_message_bytes(action: broker.Action, sizes: list[int]) -> int:
    return mail.email_encoded_bytes(action.payload, sizes)


LIMITS: dict[str, ChannelLimits] = {
    "telegram": ChannelLimits(max_text=4096, text_units="utf16", max_file_bytes=2000 * 1024 * 1024),
    "email": ChannelLimits(
        max_text=None,
        text_units="chars",
        max_file_bytes=None,
        max_message_bytes=25_000_000,
        message_bytes=_email_message_bytes,
    ),
    "local": ChannelLimits(max_text=None, text_units="chars", max_file_bytes=None),
    "slack": ChannelLimits(max_text=4000, text_units="chars", max_file_bytes=1_000_000_000),
    "meet": ChannelLimits(max_text=500, text_units="chars", max_file_bytes=None),
}


def _units(ch: str, units: str) -> int:
    return len(ch.encode("utf-16-le")) // 2 if units == "utf16" else 1


def split_text(channel: str, text: str) -> list[str]:
    """`text` as the messages the channel sends it in, each within its
    limit counted in the channel's units, broken at a newline, else a
    space, else wherever the limit falls (never inside a character)."""
    if not text.strip():
        return []
    limits = LIMITS[channel]
    if limits.max_text is None:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        count, cut = 0, 0
        for i, ch in enumerate(rest):
            count += _units(ch, limits.text_units)
            if count > limits.max_text:
                break
            cut = i + 1
        if cut >= len(rest):
            parts.append(rest)
            break
        head = rest[:cut]
        brk = head.rfind("\n")
        if brk <= 0:
            brk = head.rfind(" ")
        if brk > 0:
            cut = brk + 1
        parts.append(rest[:cut])
        rest = rest[cut:]
    return [p for p in parts if p.strip()]


def _file_size(workspace: str, path: str) -> int | None:
    """The size of `path` when it names a regular file with one link inside
    `workspace`, reached without following a link; else None. Opens the
    file and never reads it."""
    prefix = workspace.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    try:
        root = os.open(workspace, ws.DIR_FLAGS)
    except OSError:
        return None
    try:
        fd, st, _why = ws.open_plain_file(root, path[len(prefix) :])
    finally:
        os.close(root)
    if fd is None:
        return None
    os.close(fd)
    return st.st_size


FILES_SHAPE = "files must be a list of {path, sha256} objects"
TEXT_SHAPE = "the text must be a string"
EMAIL_SHAPE = (
    "subject, body and in_reply_to must each be a string or null, and to, cc and references lists of strings"
)


def _strings(payload: dict[str, Any], strings: tuple[str, ...], lists: tuple[str, ...] = ()) -> bool:
    """Whether each field in `strings` is absent, None, or a string, and each
    in `lists` absent, None, or a list of strings: the port's shape, which
    the refusals and the size function read as text."""
    return all(payload.get(k) is None or isinstance(payload[k], str) for k in strings) and all(
        payload.get(k) is None
        or (isinstance(payload[k], list) and all(isinstance(v, str) for v in payload[k]))
        for k in lists
    )


async def _size_refusal(channel: str, action: broker.Action, workspace: str | None) -> str | None:
    """`files` absent or None is no files; otherwise it is the port's list
    of {path, sha256} objects, each a string, or the send is refused for
    its shape, since the bridge's `perform` reads each entry's path and
    sha256. Each file must then be a regular file in the task's workspace,
    within the channel's limit, and the whole message within its limit once
    the channel's size function is set. A refusal names the file by its
    place in `files`, not its path. The kernel never reads a file a turn
    names: one that is missing, a link, or outside the workspace gets the
    same answer, and whether its bytes are the ones requested is the bridge's
    `perform`."""
    limits = LIMITS[channel]
    files = action.payload.get("files")
    if files is None:
        files = []
    if not isinstance(files, list) or not all(
        isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str)
        for f in files
    ):
        return FILES_SHAPE
    sizes = []
    for i, f in enumerate(files):
        size = await asyncio.to_thread(_file_size, workspace, f["path"]) if workspace else None
        if size is None:
            return f"files[{i}] is not a regular file in the task's workspace"
        if limits.max_file_bytes is not None and size > limits.max_file_bytes:
            return f"files[{i}] is {size} bytes, over {channel}'s limit of {limits.max_file_bytes} bytes per file"
        sizes.append(size)
    if limits.message_bytes is not None and limits.max_message_bytes is not None:
        total = limits.message_bytes(action, sizes)
        if total > limits.max_message_bytes:
            return f"the message is {total} bytes, over {channel}'s limit of {limits.max_message_bytes} bytes"
    return None


async def _refuse_telegram(conn, action: broker.Action) -> str | None:
    # Any chat Valor's account can reach is a target, owned or not: the
    # boundary is the account's access (docs/business.md, Business item 8).
    if not _strings(action.payload, ("text",)):
        return TEXT_SHAPE
    text = action.payload.get("text") or ""
    files = action.payload.get("files") or []
    if not split_text("telegram", text) and not files:
        return "the text and the files are both empty: Telegram would send nothing"
    return None


async def _refuse_slack(conn, action: broker.Action) -> str | None:
    # Any chat Valor's account can reach is a target, owned or not, as on
    # Telegram (docs/business.md, Business item 8).
    if not _strings(action.payload, ("text", "reply_to")):
        return "the text and reply_to must each be a string or null"
    text = action.payload.get("text") or ""
    files = action.payload.get("files") or []
    if not split_text("slack", text) and not files:
        return "the text and the files are both empty: Slack would post nothing"
    return None


async def _refuse_canvas(conn, action: broker.Action) -> str | None:
    markdown = action.payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        return "the canvas needs its markdown"
    if action.payload.get("files"):
        return "a canvas write names no files"
    return None


async def _refuse_email(conn, action: broker.Action) -> str | None:
    if not _strings(action.payload, ("subject", "body", "in_reply_to"), ("to", "cc", "references")):
        return EMAIL_SHAPE
    if not action.payload.get("to"):
        return "the email has no recipient"
    return None


async def _refuse_local(conn, action: broker.Action) -> str | None:
    from core import intake

    if not intake.owns("local", action.target):
        return "the target is not the local chat this machine's local bridge serves"
    if not _strings(action.payload, ("text",)):
        return TEXT_SHAPE
    if action.payload.get("files"):
        return "the local chat shows text only, so a send names no files"
    return None


async def _refuse_meet(conn, action: broker.Action) -> str | None:
    """A call's post or spoken sentence: text within `LIMITS["meet"]`, and a
    post's `settles`, the open questions a read-back answers, as
    `[{"task_id", "question_id"}]`."""
    if not _strings(action.payload, ("text",)):
        return TEXT_SHAPE
    text = (action.payload.get("text") or "").strip()
    most = LIMITS["meet"].max_text
    if not text:
        return "the text is empty: the call would hear nothing"
    if len(text) > most:
        return f"the text is {len(text)} characters, over a call's {most}"
    if action.payload.get("files"):
        return "a call takes text, no files"
    settles = action.payload.get("settles")
    if settles is not None and not (
        isinstance(settles, list)
        and all(isinstance(q, dict) and isinstance(q.get("task_id"), str) and isinstance(q.get("question_id"), str)
                for q in settles)
    ):  # fmt: skip
        return 'settles must be a list of {"task_id", "question_id"} objects'
    return None


@dataclass(frozen=True)
class Declared:
    """A channel's send type as a task's Performers holds it. `refuse`
    checks the send and, given the task's `workspace`, sizes its files
    there; a bridge's `Bound` checks the send alone, since the bridge reads
    each file at `perform` and refuses one that differs."""

    action_type: str
    effect_class: str
    usage: str | None  # None: no turn is offered it
    owner: str  # "telegram", "email", "slack", "local", or "meet"
    check: Callable[[Any, broker.Action], Awaitable[str | None]] | None = None
    workspace: str | None = None

    async def refuse(self, conn, action: broker.Action) -> str | None:
        said = await self.check(conn, action) if self.check else None
        return said or await _size_refusal(self.owner, action, self.workspace)


DECLARED: dict[str, Declared] = {
    "telegram.send_message": Declared(
        action_type="telegram.send_message",
        effect_class="act",
        usage=(
            '`telegram.send_message`: target the chat id as text, payload `{"text": "...", '
            '"reply_to": null, "topic_id": null, "files": [{"path": "...", "sha256": "..."}]}`, each path absolute '
            "and inside your workspace; it leaves when you request it."
        ),
        owner="telegram",
        check=_refuse_telegram,
    ),
    "email.send": Declared(
        action_type="email.send",
        effect_class="act",
        usage=(
            '`email.send`: target the `to` addresses, lowercased, sorted, comma-joined, payload `{"to": '
            '[...], "cc": [...], "subject": "...", "body": "...", "in_reply_to": null, "references": [], '
            '"files": [{"path": "...", "sha256": "..."}]}`, each path absolute and inside your workspace; '
            'or, to reply to all of a received email, payload `{"reply_to": "<its message id>", "body": "...", '
            '"files": [...]}` with any target, and the recipients, subject, and threading are filled in from it; '
            "it leaves when you request it."
        ),
        owner="email",
        check=_refuse_email,
    ),
    "slack.send_message": Declared(
        action_type="slack.send_message",
        effect_class="act",
        usage=(
            "`slack.send_message`: target `<team id>/<channel id>`, or `<team id>/<user id>` for a DM, payload "
            '`{"text": "...", "reply_to": null, "files": [{"path": "...", "sha256": "..."}]}`; a `reply_to` '
            "(a message's ts) posts in that message's thread; each path absolute and inside your workspace; "
            "it leaves when you request it."
        ),
        owner="slack",
        check=_refuse_slack,
    ),
    "slack.write_canvas": Declared(
        action_type="slack.write_canvas",
        effect_class="act",
        usage=(
            '`slack.write_canvas`: target `<team id>/<channel id>`, payload `{"markdown": "..."}`; writes the '
            "channel's decision board canvas whole (made the first time), replacing what it held; it leaves "
            "when you request it."
        ),
        owner="slack",
        check=_refuse_canvas,
    ),
    "local.send_message": Declared(
        action_type="local.send_message",
        effect_class="act",
        usage=(
            '`local.send_message`: target `local`, payload `{"text": "..."}`; shown on Tom\'s local chat page '
            "when you request it."
        ),
        owner="local",
        check=_refuse_local,
    ),
    "meet.say": Declared(
        action_type="meet.say", effect_class="act", usage=None, owner="meet", check=_refuse_meet
    ),
    "meet.send_message": Declared(
        action_type="meet.send_message", effect_class="act", usage=None, owner="meet", check=_refuse_meet
    ),
}


def declared_performers(workspace: str | None = None) -> list[Declared]:
    """Every declared send, sizing files in `workspace`, the task's."""
    return [dataclasses.replace(d, workspace=workspace) for d in DECLARED.values()]


PerformFn = Callable[[broker.Action, str], Awaitable[dict[str, Any]]]
LookupFn = Callable[[broker.Action, str, str], Awaitable[dict[str, Any] | None]]


class Bound:
    """A declared type joined with its bridge's perform and lookup: the
    performer the bridge's broker calls. The broker's two-argument lookup
    gets `since`, the intent's `at`, found by the effect id that ends the
    key. The bridge's lookup returns the send it finds, None only when the
    platform can no longer record the send, and raises `broker.Unknown`
    while it still might, so a miss is `failed` only once it is final."""

    def __init__(self, declared: Declared, perform: PerformFn, lookup: LookupFn, conn):
        self.declared = declared
        self.action_type = declared.action_type
        self.effect_class = declared.effect_class
        self.usage = declared.usage
        self.owner = declared.owner
        self._perform, self._lookup, self._conn = perform, lookup, conn

    async def refuse(self, conn, action: broker.Action) -> str | None:
        return await self.declared.check(conn, action) if self.declared.check else None

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        return await self._perform(action, key)

    async def lookup(self, action: broker.Action, key: str) -> dict[str, Any] | None:
        return await self._lookup(action, key, await _since(self._conn, key.rsplit(":", 1)[-1]))


async def _since(conn, effect_id: str) -> str:
    row = await (
        await conn.execute(
            "SELECT at FROM events WHERE payload->>'effect_id' = %s "
            "AND type IN ('effect.intent', 'release.requested', 'effect.held') "
            "ORDER BY CASE type WHEN 'effect.intent' THEN 0 WHEN 'release.requested' THEN 1 ELSE 2 END LIMIT 1",
            (effect_id,),
        )
    ).fetchone()
    return row[0].isoformat() if row else ""


@dataclass(frozen=True)
class Release:
    effect_id: str
    at: str  # the release.requested row's at, which precedes the intent


@dataclass(frozen=True)
class NoticeDue:
    notice_id: str
    task_id: str
    chat_id: str
    text: str  # plain text for Tom; the notice id stays in the row
    reply_to: str | None
    at: str  # the notice.requested row's at


@dataclass(frozen=True)
class ReactionDue:
    reaction_id: str
    task_id: str
    received_id: str
    chat_id: str
    message_id: str
    emoji: str  # the channel's own: Telegram's code points, Slack's name (`core/reactions.py`)
    stage: str  # `seen` (👀), `handling`, `stalled`, `done`, or `noted`, each replacing the last (`reactions.RANK`)
    at: str  # the reaction.requested row's at
    label: str | None = None  # `reactions.REACTIONS`'s; None on a row written before labels
    previous: tuple[str, ...] = ()  # the emoji of the message's lower stages placed


class Bridge(Protocol):
    channel: str

    def performers(self) -> dict[str, tuple[PerformFn, LookupFn]]: ...

    async def run(self, outbox: Outbox) -> None: ...

    # A bridge may define `async def reconcile(self, outbox)`, which then
    # replaces the outbox's own serial reconcile at start and on each wake,
    # for a bridge that settles each effect as its own task.
    # A bridge may define `async def connected(self)`, whether its own
    # connection to its service is up; the outbox names its listening
    # connection by it (`Outbox.report`). A bridge without one is up while
    # its outbox runs.
    # A bridge may define `async def read(self, chat, q, limit, before)`,
    # a chat's messages newest first (`q` a text search, `before` a message
    # id to page back from); `serve` then answers it for turns (`reads`).

    async def tick(self) -> None: ...


class Outbox:
    """What the bridge sends, from the ledger. Iterating yields, oldest
    first, every release requested for this channel with no intent,
    outcome, or refusal, then every notice on this channel not yet sent,
    then every reaction on this channel not yet placed or failed; then
    waits for a row of any of these kinds, or `settings.serve_tick_s`. On a
    row's wake it yields again first, so a 👀 is placed before the bridge's
    gap fill runs; then on either wake it reconciles this channel's
    dangling sends, calls the bridge's `tick`, and yields again.

    Before each round it reports whether the bridge is connected (`report`):
    its listening connection is named `valor-<channel>-up@<epoch>`, the
    second it came up, while the bridge's own connection is up, and
    `valor-<channel>-listen` while it is down, which `rollout.services`
    reads. The lock connection is held from start, before the bridge
    connects, so it says only that the process runs."""

    def __init__(
        self,
        bridge: Bridge,
        performers: broker.Performers,
        conn,
        perform_conn,
        listener,
        dsn: str | None = None,
    ):
        self.dsn = dsn
        self.bridge = bridge
        self.channel = bridge.channel
        self.performers = performers
        self.conn = conn
        self.perform_conn = perform_conn
        self.listener = listener
        self.up_since: int | None = None
        self.named = f"valor-{self.channel}-listen"

    def __aiter__(self) -> AsyncIterator[Release | NoticeDue | ReactionDue]:
        return self._items()

    async def _items(self) -> AsyncIterator[Release | NoticeDue | ReactionDue]:
        while True:
            await self.report()
            for item in await self.due():
                yield item
            if await self.wait():
                for item in await self.due():
                    yield item
            if hasattr(self.bridge, "reconcile"):
                await self.bridge.reconcile(self)
            else:
                await self.reconcile()
            await self.bridge.tick()

    def types(self) -> list[str]:
        return [d.action_type for d in DECLARED.values() if d.owner == self.channel]

    async def reconcile(self) -> list[broker.Outcome]:
        """Every dangling send of this channel settled in turn, on the
        outbox's connection."""
        settled = []
        for effect_id in await self.dangling():
            outcome = await self.settle(effect_id)
            if outcome is not None:
                settled.append(outcome)
        return settled

    async def dangling(self) -> list[str]:
        return await broker.dangling(self.conn, self.types())

    async def settle(self, effect_id: str, conn=None) -> broker.Outcome | None:
        """One dangling effect asked of its target (`broker.reconcile`) on
        `conn`, the outbox's connection when None."""
        return await broker.reconcile(conn or self.conn, self.performers, effect_id)

    async def due(self) -> list[Release | NoticeDue | ReactionDue]:
        releases = await (
            await self.conn.execute(
                "SELECT r.payload->>'effect_id', r.at FROM events r WHERE r.type = 'release.requested' "
                "AND r.payload->>'owner' = %s AND NOT EXISTS (SELECT 1 FROM events e "
                "WHERE e.type IN ('effect.intent', 'effect.outcome', 'effect.refused') "
                "AND e.payload->>'effect_id' = r.payload->>'effect_id') ORDER BY r.id",
                (self.channel,),
            )
        ).fetchall()
        notices = await (
            await self.conn.execute(
                "SELECT n.task_id, n.payload, n.at FROM events n WHERE n.type = 'notice.requested' "
                "AND n.payload->>'channel' = %s AND n.payload->>'chat_id' IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM events s WHERE s.type = 'notice.sent' "
                "AND s.payload->>'notice_id' = n.payload->>'notice_id') ORDER BY n.id",
                (self.channel,),
            )
        ).fetchall()
        return (
            [Release(e, at.isoformat()) for e, at in releases]
            + [
                NoticeDue(p["notice_id"], t, p["chat_id"], p["text"], p.get("reply_to"), at.isoformat())
                for t, p, at in notices
            ]
            + await self._reactions()
        )

    async def _reactions(self) -> list[ReactionDue]:
        """This channel's reactions with no result, oldest first, so a
        message's 👀 is placed before the reaction that replaces it. One
        below a later stage of its message (`reactions.RANK`) is closed here
        instead, `reaction.placed` with `superseded` and nothing sent: a 👀
        once that stage is placed (a flood wait's retry), any other once it
        is requested and not refused, so a slow handling never lands over a
        done. Each carries the names of its message's lower stages placed
        (`previous`), which Slack removes."""
        rows = await (
            await self.conn.execute(
                "SELECT r.task_id, r.payload, r.at, (SELECT coalesce(jsonb_agg(jsonb_build_object("
                "'p', o.payload, 'result', x.type, 'superseded', x.payload->'superseded') ORDER BY o.id), '[]') "
                "FROM events o LEFT JOIN events x ON x.type IN ('reaction.placed', 'reaction.failed') "
                "AND x.payload->>'reaction_id' = o.payload->>'reaction_id' "
                "WHERE o.type = 'reaction.requested' AND o.payload->>'received_id' = r.payload->>'received_id' "
                "AND o.id <> r.id) FROM events r WHERE r.type = 'reaction.requested' AND r.payload->>'channel' = %s "
                "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.type IN ('reaction.placed', 'reaction.failed') "
                "AND e.payload->>'reaction_id' = r.payload->>'reaction_id') ORDER BY r.id",
                (self.channel,),
            )
        ).fetchall()
        due = []
        for task_id, p, at, others in rows:
            rank = reactions.RANK.get(p["stage"], 0)
            above = [o for o in others if reactions.RANK.get(o["p"].get("stage"), 0) > rank
                     and o["result"] != "reaction.failed"]  # fmt: skip
            placed = [o for o in others if o["result"] == "reaction.placed" and not o["superseded"]]
            item = ReactionDue(
                p["reaction_id"],
                task_id,
                p["received_id"],
                p["chat_id"],
                p["message_id"],
                p["emoji"],
                p["stage"],
                at.isoformat(),
                p.get("label"),
                tuple(o["p"]["emoji"] for o in placed if reactions.RANK.get(o["p"].get("stage"), 0) < rank),
            )
            if any(o in placed for o in above) or (p["stage"] != "seen" and above):
                await self.reacted(item, superseded=True)
            else:
                due.append(item)
        return due

    def listen_name(self) -> str:
        if self.up_since is None:
            return f"valor-{self.channel}-listen"
        return f"valor-{self.channel}-up@{self.up_since}"

    async def report(self) -> None:
        """Name the listening connection by whether the bridge is connected
        (class docstring). Run between waits, never inside `notifies`. A
        dead listener is left to `wait`, which names its new one."""
        connected = getattr(self.bridge, "connected", None)
        up = await connected() if connected is not None else True
        if not up:
            self.up_since = None
        elif self.up_since is None:
            self.up_since = int(time.time())
        name = self.listen_name()
        if name == self.named:
            return
        try:
            await self.listener.execute("SELECT set_config('application_name', %s, false)", (name,))
        except psycopg.OperationalError:
            return
        self.named = name

    async def wait(self) -> bool:
        """Until a release, notice, or reaction row is written (true), or the
        tick (false)."""
        try:
            async for note in self.listener.notifies(timeout=settings.serve_tick_s):
                if json.loads(note.payload).get("type") in (
                    "release.requested",
                    "notice.requested",
                    "reaction.requested",
                ):
                    return True
        except psycopg.OperationalError:
            # The listening connection died: wait the tick, then listen on a
            # new one; `due` catches up what was missed.
            await asyncio.sleep(settings.serve_tick_s)
            try:
                listener = await db.connect(self.dsn, application_name=self.listen_name())
                await listener.execute("LISTEN valor_events")
            except psycopg.OperationalError:
                return False
            await self.listener.close()
            self.listener = listener
            self.named = self.listen_name()
        return False

    async def perform(self, item: Release, conn=None) -> broker.Outcome:
        """Release the effect through the broker: the checks, the intent,
        the bridge's perform, the outcome. A release the checks refuse is
        recorded refused, once, and never yielded again. `conn` is a
        connection the caller owns, for a bridge that performs releases
        side by side; the outbox's own is used when None."""
        try:
            return await broker.release(conn or self.perform_conn, self.performers, item.effect_id)
        except (broker.Refused, tasks.TaskStopped) as exc:
            return broker.Outcome(item.effect_id, "refused", error=str(exc) or type(exc).__name__)

    async def sent(self, item: NoticeDue, sent: list[dict[str, str]], conn=None) -> None:
        conn = conn or self.conn
        try:
            async with conn.transaction():
                await ledger.append(
                    conn, item.task_id, "notice.sent", {"notice_id": item.notice_id, "sent": sent}
                )
        except psycopg.errors.UniqueViolation:
            pass

    async def reacted(self, item: ReactionDue, error: str | None = None, *, superseded: bool = False) -> None:
        """The reaction's one result: `reaction.failed` with `error`, else
        `reaction.placed`. A second result (another bridge, a retry) is
        skipped by the index. A reaction left in doubt (a flood wait, a lost
        connection) gets no result here, so it is yielded again."""
        kind = "reaction.failed" if error is not None else "reaction.placed"
        payload: dict[str, Any] = {"reaction_id": item.reaction_id, "received_id": item.received_id}
        if error is not None:
            payload["error"] = error
        if superseded:
            payload["superseded"] = True
        try:
            async with self.conn.transaction():
                await ledger.append(self.conn, item.task_id, kind, payload)
        except psycopg.errors.UniqueViolation:
            pass


def bound_performers(bridge: Bridge, conn) -> broker.Performers:
    """The channel's declared types, each joined with the bridge's perform
    and lookup."""
    given = bridge.performers()
    return broker.Performers(
        *(
            Bound(d, *given[d.action_type], conn)
            for d in DECLARED.values()
            if d.owner == bridge.channel and d.action_type in given
        )
    )


def stamp() -> str:
    """The commit of the checkout this code runs from, or `NO_HEAD` when it
    cannot be read for any reason: the stamp never stops a bridge."""
    try:
        return git.head(ROOT) or NO_HEAD
    except Exception:  # noqa: BLE001  no trusted git, a refused config: unstamped, and it serves
        return NO_HEAD


# Parakeet hears an hour of speech in a few minutes on this Mac's CPU; a
# voice process that holds a request longer than this is hung, not working.
TRANSCRIBE_TIMEOUT_S = 600.0


def transcribe(attachment: dict[str, Any]) -> dict[str, Any]:
    """An inbound attachment as recorded: an `audio/*` file that was
    downloaded gains `transcript`, the words the voice process
    (`tools/voice.py`) heard, or `transcript_failed` with why not; any
    other attachment is returned as it was. It blocks until the voice
    process answers or `TRANSCRIBE_TIMEOUT_S` passes, so a bridge runs it
    off its loop."""
    if not attachment.get("path") or not str(attachment.get("mime") or "").startswith("audio/"):
        return attachment
    request = urllib.request.Request(
        f"http://127.0.0.1:{settings.voice_port}/transcribe",
        data=json.dumps({"path": attachment["path"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TRANSCRIBE_TIMEOUT_S) as answer:
            return {**attachment, "transcript": json.load(answer)["text"]}
    except Exception as e:  # noqa: BLE001  the message is recorded either way, saying why
        return {**attachment, "transcript_failed": str(e) or type(e).__name__}


async def reads(read: Callable[..., Awaitable[list[dict[str, Any]]]], port: int) -> web.AppRunner:
    """Answer `GET /history?chat=&q=&limit=&before=` on 127.0.0.1:`port`
    with `read`'s messages as JSON, newest first, for a turn's
    `valor-<channel> read`. `limit` is 50 unless given, at most 100. A
    `ValueError` (a malformed chat or id) answers 400 and any other failure
    503, each with its sentence."""

    async def history(request: web.Request) -> web.Response:
        q = request.query
        try:
            if not q.get("chat"):
                raise ValueError("name the chat: ?chat=<id>")
            limit = min(max(int(q.get("limit") or 50), 1), 100)
            found = await read(q["chat"], q.get("q") or None, limit, q.get("before") or None)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001  the reader gets the sentence, the bridge runs on
            return web.json_response({"error": str(e) or type(e).__name__}, status=503)
        return web.json_response({"messages": found})

    app = web.Application()
    app.router.add_get("/history", history)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def serve(bridge: Bridge, dsn: str | None = None) -> None:
    """Run a bridge until it stops; then exit nonzero, for launchd to
    restart it. Holds `bridge:<channel>:<machine>` for its life, on the
    connection named `valor-<channel>@<stamp>`, the commit it started at,
    which a kernel's start reads to restart a bridge a merge left on old
    code (`core.serve.restart_bridges`)."""
    channel = bridge.channel
    head = await asyncio.to_thread(stamp)
    conn = await db.connect(dsn, application_name=f"valor-{channel}@{head}")
    perform_conn = await db.connect(dsn, application_name=f"valor-{channel}-perform")
    listener = await db.connect(dsn, application_name=f"valor-{channel}-listen")
    outbox = runner = None
    try:
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"bridge:{channel}:{settings.machine}",)
        )
        await listener.execute("LISTEN valor_events")
        if hasattr(bridge, "read"):
            runner = await reads(bridge.read, getattr(settings, f"{channel}_read_port"))
        outbox = Outbox(bridge, bound_performers(bridge, conn), conn, perform_conn, listener, dsn)
        if not hasattr(bridge, "reconcile"):
            await outbox.reconcile()
        await bridge.run(outbox)
    finally:
        if runner is not None:
            await runner.cleanup()
        for c in (outbox.listener if outbox else listener, perform_conn, conn):
            await c.close()
    raise SystemExit(1)
