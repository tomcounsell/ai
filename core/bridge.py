"""The bridge port: what a channel's bridge process builds against.

A bridge (Telegram, email) is a process of its own. It receives messages
and records them through `core.intake`, and it sends what the outbox
yields: a release Tom approved, or an operator notice. It decides nothing:
the kernel binds every message, holds every send for Tom, and checks every
release. A bridge imports `core.bridge`, `core.intake`, `core.broker`,
`core.settings`, `core.db`, and `core.credentials`, and nothing else from
`core/`.

The kernel declares each channel's send type here (`DECLARED`): its class,
the usage line a turn reads, and what refuses it. Every task's Performers holds
them, so a turn can request a send and `request` holds it for Tom. The
bridge supplies only how to send (`perform`) and how to find a send that
happened (`lookup`); `serve` joins the two into the performer the broker
runs.

Each task's declared sends carry its workspace. A file a send names must
be a regular file there, reached through no link; the kernel takes its
size from the opened file's `fstat` and never reads it, and a file that is
missing, a link, or outside the workspace gets one answer, so a refusal
says nothing about a path outside. Whether the bytes are what Tom
approved is the bridge's: `perform` reads each file once and refuses one
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
- Local: none. The local chat page shows any text and no files.
"""

import asyncio
import dataclasses
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from core import broker, db, ledger, mail, tasks
from core import workspace as ws
from core.settings import settings

# The email bridge builds and measures its messages with the kernel's
# builder, reached here (a bridge imports core.bridge, never core.mail).
email_message = mail.email_message
email_serialized = mail.serialized
email_message_id = mail.message_id


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
    same answer, and whether its bytes are what Tom approved is the bridge's
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
    from core import intake

    if not intake.owns("telegram", action.target):
        return "the target chat is not one this machine's Telegram bridge receives"
    if not _strings(action.payload, ("text",)):
        return TEXT_SHAPE
    text = action.payload.get("text") or ""
    files = action.payload.get("files") or []
    if not split_text("telegram", text) and not files:
        return "the text and the files are both empty: Telegram would send nothing"
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
    if not (action.payload.get("text") or "").strip():
        return "the text is empty: the local chat would show nothing"
    if action.payload.get("files"):
        return "the local chat shows text only, so a send names no files"
    return None


@dataclass(frozen=True)
class Declared:
    """A channel's send type as a task's Performers holds it. `refuse`
    checks the send and, given the task's `workspace`, sizes its files
    there; a bridge's `Bound` checks the send alone, since the bridge reads
    each file at `perform` and refuses one that differs."""

    action_type: str
    effect_class: str
    usage: str
    owner: str  # "telegram", "email", or "local"
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
            "and inside your workspace; sent once Tom approves."
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
            "sent once Tom approves."
        ),
        owner="email",
        check=_refuse_email,
    ),
    "local.send_message": Declared(
        action_type="local.send_message",
        effect_class="act",
        usage=(
            '`local.send_message`: target `local`, payload `{"text": "..."}`; shown on Tom\'s local chat page '
            "once Tom approves."
        ),
        owner="local",
        check=_refuse_local,
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
    text: str  # carries the notice's short id
    reply_to: str | None
    at: str  # the notice.requested row's at


class Bridge(Protocol):
    channel: str

    def performers(self) -> dict[str, tuple[PerformFn, LookupFn]]: ...

    async def run(self, outbox: Outbox) -> None: ...

    # A bridge may define `async def reconcile(self, outbox)`, which then
    # replaces the outbox's own serial reconcile at start and on each wake,
    # for a bridge that settles each effect as its own task.

    async def tick(self) -> None: ...


class Outbox:
    """What the bridge sends, from the ledger. Iterating yields, oldest
    first, every release Tom approved for this channel with no intent,
    outcome, or refusal, then every notice on this channel not yet sent;
    then waits for a row of either kind, or `settings.serve_tick_s`, and on
    that wake reconciles this channel's dangling sends, calls the bridge's
    `tick`, and yields again."""

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

    def __aiter__(self) -> AsyncIterator[Release | NoticeDue]:
        return self._items()

    async def _items(self) -> AsyncIterator[Release | NoticeDue]:
        while True:
            for item in await self.due():
                yield item
            await self.wait()
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

    async def due(self) -> list[Release | NoticeDue]:
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
        return [Release(e, at.isoformat()) for e, at in releases] + [
            NoticeDue(p["notice_id"], t, p["chat_id"], p["text"], p.get("reply_to"), at.isoformat())
            for t, p, at in notices
        ]

    async def wait(self) -> None:
        """Until a release or notice row is written, or the tick."""
        try:
            async for note in self.listener.notifies(timeout=settings.serve_tick_s):
                if json.loads(note.payload).get("type") in ("release.requested", "notice.requested"):
                    return
        except psycopg.OperationalError:
            # The listening connection died: wait the tick, then listen on a
            # new one; `due` catches up what was missed.
            await asyncio.sleep(settings.serve_tick_s)
            try:
                listener = await db.connect(self.dsn, application_name=f"valor-{self.channel}-listen")
                await listener.execute("LISTEN valor_events")
            except psycopg.OperationalError:
                return
            await self.listener.close()
            self.listener = listener

    async def perform(self, item: Release, conn=None) -> broker.Outcome:
        """Release the effect through the broker: the checks, the intent,
        the bridge's perform, the outcome. A release the checks refuse is
        recorded refused, once, and never yielded again. `conn` is a
        connection the caller owns, for a bridge that performs releases
        side by side; the outbox's own is used when None."""
        try:
            return await broker.release(conn or self.perform_conn, self.performers, item.effect_id)
        except (broker.Refused, broker.NotApproved, tasks.TaskStopped) as exc:
            return broker.Outcome(item.effect_id, "refused", error=str(exc) or type(exc).__name__)

    async def sent(self, item: NoticeDue, sent: list[dict[str, str]]) -> None:
        try:
            async with self.conn.transaction():
                await ledger.append(
                    self.conn, item.task_id, "notice.sent", {"notice_id": item.notice_id, "sent": sent}
                )
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


async def serve(bridge: Bridge, dsn: str | None = None) -> None:
    """Run a bridge until it stops; then exit nonzero, for launchd to
    restart it. Holds `bridge:<channel>:<machine>` for its life."""
    channel = bridge.channel
    conn = await db.connect(dsn, application_name=f"valor-{channel}")
    perform_conn = await db.connect(dsn, application_name=f"valor-{channel}-perform")
    listener = await db.connect(dsn, application_name=f"valor-{channel}-listen")
    outbox = None
    try:
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"bridge:{channel}:{settings.machine}",)
        )
        await listener.execute("LISTEN valor_events")
        outbox = Outbox(bridge, bound_performers(bridge, conn), conn, perform_conn, listener, dsn)
        if not hasattr(bridge, "reconcile"):
            await outbox.reconcile()
        await bridge.run(outbox)
    finally:
        for c in (outbox.listener if outbox else listener, perform_conn, conn):
            await c.close()
    raise SystemExit(1)
