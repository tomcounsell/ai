---
tracking: none
slug: m2-1-port
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# The bridge port (part of 2.1)

The contract 2.2 (Telegram) and 2.3 (email) build against, written into
2.1 from the lead's port decisions (numbered here as there, D1 to D40, with 11a, 15a to 15c, and
34a).
2.1 builds every name below; [m2-1-resident-kernel.md](m2-1-resident-kernel.md)
holds the kernel side. Where a bridge plan and this file disagree, this
file wins.

## Modules a bridge may import (D30)

`core.bridge`, `core.intake`, `core.broker`, `core.settings`, `core.db`,
and `core.credentials` (to read its key files). Nothing else from `core/`.

## The inbound record (`core/intake.py`, D2, D3, D4, D10, D11, D17, D18)

```python
@dataclass(frozen=True)
class Inbound:
    channel: str  # "telegram" or "email"
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
    attachments: list[dict] = field(default_factory=list)  # see below
    headers: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Received:
    received_id: str
    duplicate: bool  # this (channel, chat_id, message_id) is already recorded


async def receive(conn, inbound: Inbound) -> Received: ...
async def highest(conn, channel: str, chat_id: str) -> int | None: ...
async def lowest(conn, channel: str, chat_id: str) -> int | None: ...
async def recorded(conn, channel: str, chat_id: str, ids: list[str]) -> set[str]: ...
async def claimed(conn, channel: str, chat_id: str) -> set[str]: ...
def owns(channel: str, id: str) -> bool: ...
def owned(channel: str) -> list[str]: ...
```

- **Email ids (D10).** `chat_id` is the first `References` id, else the
  message's own Message-ID, on inbound records and on sent entries alike.
  Email ownership and the start rule key on `sender_id`.
- **Attachments (D17, D18).** Each entry is `{name, mime, bytes, path}`,
  or `{name, mime, bytes, skipped: <reason>}` when the download failed.
  The bridge writes the file under `settings.inbound_dir/<channel>/`,
  named by its sha256, before `receive`. There is no file store module.
- **Headers (D11, D17).** The bridge passes raw headers. Email passes
  every `Authentication-Results` and `From` header as received; Telegram
  passes `grouped_id` when set.
- **`receive`** appends `message.received` on the channel's stream
  (`task_id` = the channel) with payload
  `{"received_id": <new_id>, "verified": <bool>, **asdict(inbound)}` in
  its own transaction and returns after commit. A unique violation on
  `events_one_message` returns the first row's id with `duplicate: true`.
  The bridge acknowledges to the platform (read state, IMAP `\Seen`)
  only after `receive` returns. A caller that runs `receive` concurrently
  uses one connection per concurrent call.
- **`verified` (D11, D11a)** is set by `receive`, not by the bridge:
  - email: false. 2.3 adds `intake.dmarc_verified` beside `receive` in
    `core/intake.py`, under its Brief's `governance_grant` (open
    question 17), and `receive` calls it for email.
  - Telegram: true for every record, since MTProto authenticates the
    sender. Whether the sender is the operator is decided at bind
    (`sender_id == operator_telegram_id`).
- **Ids are strings.** Every `chat_id` and `message_id`, in records and
  in `sent` entries alike, is a string, and a Telegram chat id is the
  marked form (`-100...` for a group), so the `sent` index matches.
- **`highest` (D3)** is the largest `message_id` recorded for the chat,
  compared as an integer, for channels with integer ids; a paging hint
  only. Email has no cursor; it polls UNSEEN SINCE for owned senders.
- **`lowest` (D32)** is the smallest integer `message_id` recorded for
  the chat, or None: a gap fill of a chat with no seen entry stops there,
  by membership, not at the high-water mark.
- **`recorded` (D32)** returns which of `ids` are already received. Gap
  fill checks membership over a recent window on connect and in
  `Bridge.tick()` (D38), never a high-water mark alone (Telethon drops
  updates on pts gaps while connected).
- **`claimed` (D33)** returns the message ids already recorded as sent in
  the chat (send outcomes and `notice.sent`). A lookup scan skips them.
- **Ownership (D4).** `owns(channel, id)` is true for the operator's own
  chat and addresses, and for an id a project spec lists in `chats`
  (`"telegram:<chat id>"`, `"email:<sender address>"`) whose `machine` is
  `settings.machine`; a spec with no `machine` belongs to
  `settings.default_machine`, so each chat has one owner. `owned(channel)` lists them, for gap fill
  and for the email search. A bridge receives only owned ids. There is no
  `telegram_chats` or `email_senders` setting.

## The bridge (`core/bridge.py`, D1, D25, D27, D28, D29)

```python
@dataclass(frozen=True)
class ChannelLimits:
    max_text: int | None  # per message, in text_units
    text_units: str  # "utf16" or "chars"
    max_file_bytes: int | None  # per file
    max_message_bytes: int | None = None  # the whole message, measured by message_bytes
    message_bytes: Callable[[broker.Action], int] | None = None


LIMITS: dict[str, ChannelLimits]  # "telegram", "email"

PerformFn = Callable[[broker.Action, str], Awaitable[dict[str, Any]]]
LookupFn = Callable[[broker.Action, str, str], Awaitable[dict[str, Any] | None]]


class Bridge(Protocol):
    channel: str

    def performers(self) -> dict[str, tuple[PerformFn, LookupFn]]: ...
    async def run(self, outbox: Outbox) -> None: ...
    async def tick(self) -> None: ...


def split_text(channel: str, text: str) -> list[str]: ...
async def serve(bridge: Bridge) -> None: ...
```

Each bridge's `__main__` keeps its verbs (`run`, `login`, `keys`,
`--plist`); `run` calls `bridge.serve(<the bridge>)`. `serve`:

1. Connects with `application_name` `valor-<channel>` (the performing
   connection is `valor-<channel>-perform`) and takes the session lock
   `bridge:<channel>:<settings.machine>`, blocking. No flock.
2. Builds a `broker.Performers` of the channel's entries in `DECLARED`,
   each joined with the `(perform, lookup)` the bridge returns for that
   type. Class, usage, refusal, and settle time are the kernel's; the
   bridge supplies only how.
3. Reconciles `broker.dangling(conn, <its types>)`.
4. Runs `bridge.run(outbox)`, with `intake` called by the bridge as
   module functions; when it returns or raises, exits nonzero and launchd
   restarts it.
5. On every `serve_tick_s` wake, after the outbox's reconcile, awaits
   `bridge.tick()` (D38). Telegram's gap fill runs there.

**Limits (D15b, D15c).** Protocol facts, cited per channel, in
`core/bridge.py`'s `LIMITS`, so the kernel renders and refuses without
importing a bridge, and bridges read them from there:
- Telegram: `max_text` 4096 UTF-16 code units after entity parsing
  (Telegram's message length limit); `max_file_bytes` the account's
  upload limit, 2000 MiB (Telegram's file upload documentation: 4000
  parts of 512 KiB).
- Email: `max_text` None. The limit is on the whole message: Gmail
  refuses a message over 25 MB, counted as 25,000,000 bytes of the whole
  encoded message (D15c): `max_message_bytes` 25,000,000. Its size
  function, `message_bytes`, is 2.3's `email_encoded_bytes`; in 2.1 it is
  unset, so 2.1 refuses no email for size at request time.
`split_text` splits a text over `max_text`, counting in the channel's
units, into several messages, so `sent` is a list. A send over the
limit (a Telegram file over `max_file_bytes`, an email whose
`message_bytes`, once set, exceeds `max_message_bytes`) is refused at request time, with
the protocol limit as the reason, so Tom never approves an impossible
send.

## Performers (D9, D19, D22, D24)

`perform(action, key)` returns the result. `lookup(action, key, since)`
returns the result of a send that happened, None when the platform holds
none. `since` is the `at` of the effect's intent (D34); `bridge.serve`
wraps the bridge's lookup so the broker's two-argument call passes it,
finding the intent by the effect id, the key's last segment.
A bridge reads `since` where its platform has nothing better. The
Telegram lookup reads no date: it reads the chat's history above the
newest message id the bridge recorded before the send's first message,
keeps the account's own messages, and skips `claimed` ids; there is no
stop-at-newest-claimed rule. When part of a split send is on screen, it
sends the rest under their own `random_id`s and returns the whole.
Both results carry `{"sent": [{"channel", "chat_id", "message_id"}]}`,
one entry per platform message.

- A performer raises `broker.Unknown` when a send is in doubt (D24). After
  a failed perform, `broker.Unknown` (from `perform`, or from the `lookup`
  the broker asks next) leaves the intent in flight with no outcome; the
  outbox's reconcile settles it on a later tick.
- Any other exception: the broker asks `lookup` (as today); a send found
  is `done`, none found is `failed`. Only a definite refusal is `failed`.
- Outbound files are `files: [{path, sha256}]`. `perform` reads each file
  once, hashes those bytes, raises before sending on a mismatch, and
  sends the bytes it hashed.
- The key ends in the effect id (see the kernel plan), so a Telegram
  `random_id` or an email Message-ID derived from it differs between two
  identical sends and repeats for a retry of one.

## Declared actions (`core/bridge.py`, D19, D20, D22)

```python
@dataclass(frozen=True)
class Declared:
    action_type: str
    effect_class: str
    usage: str
    owner: str  # "telegram" or "email"
    refuse: Callable[[Any, broker.Action], Awaitable[str | None]] | None = None
    settle_after_s: float | Callable[[broker.Action], float] | None = None


DECLARED: dict[str, Declared]  # telegram.send_message, email.send


def declared_performers() -> list[Declared]: ...
```

`refuse` has 1.4d's shape, `async (conn, action)`. `settle_after_s`
(D22, D37) is a number or a function of the action, resolved against the intent's action before
`broker.reconcile`. Reconcile runs once the effect's performing lock
is free, then reads the remote; it waits on no age. 2.3 sets email's
with its own cited basis. Every task's Performers holds `declared_performers()`, so `request` holds a send for
Tom and `dispatch(offered=...)` tells the turn the send exists.

- `telegram.send_message`, `act`. Target: the chat id as text. Payload
  `{"text": str, "reply_to": str | None, "topic_id": str | None,
  "files": [{"path": str, "sha256": str}]}`. Refused when the chat is not
  owned (this machine's bridge cannot send there), when text and files
  are both empty (the platform sends nothing), when a file is missing or
  its sha256 differs (it is not what Tom approved), or when a file is over
  `max_file_bytes`, or a text that splits into no message.
- `email.send`, `act`. Target: the `to` list, lowercased, sorted,
  comma-joined (D20). Payload `{"to": [str], "cc": [str], "subject": str,
  "body": str, "in_reply_to": str | None, "references": [str], "files":
  [{"path": str, "sha256": str}]}`. Refused when `to` is empty (no
  recipient), a file is missing or differs, or the whole message is over
  the limit.
- **Reply-all (D21)** lives in `core/session.py`'s request collection;
  2.3 owns that code. The turn's `reply_to` is removed before `request`.

## The outbox (`core/bridge.py`, D5, D6, D7, D8, D23)

```python
@dataclass(frozen=True)
class Release:
    effect_id: str
    at: str  # the release.requested row's at, which precedes the intent (D34)


@dataclass(frozen=True)
class NoticeDue:
    notice_id: str
    task_id: str
    chat_id: str
    text: str  # carries the notice's short id (D35)
    reply_to: str | None
    at: str  # the notice.requested row's at (D34)


class Outbox:
    def __aiter__(self) -> AsyncIterator[Release | NoticeDue]: ...
    async def perform(self, item: Release) -> broker.Outcome: ...
    async def sent(self, item: NoticeDue, sent: list[dict[str, str]]) -> None: ...
```

- Iterating yields, oldest first, every `release.requested` whose `owner`
  is this channel and whose effect has no intent, no outcome, and no
  `effect.refused`; then every `notice.requested` on this channel with no
  `notice.sent`. It then waits on `valor_events` for a row of either
  type, or `settings.serve_tick_s`, and yields again.
- On every wake it reconciles `broker.dangling(<own types>)`, not only at
  start.
- `perform(item)` calls `broker.release(conn, performers, effect_id)`:
  the checks, the intent, the bridge's `perform`, the outcome. A release
  the checks refuse (task stopped, approval used, file changed) appends
  `effect.refused` with the reason, once, and owes a notice, so it is
  never yielded again (D40 for the kernel's, the same rule).
- `sent(item, sent)` appends `notice.sent` `{"notice_id", "sent"}` on the
  notice's task stream. A notice sent but not marked is yielded again with
  the same `notice_id`; Telegram's `random_id` derived from it makes the
  platform drop the second send.
- A rendered notice carries its short id in its text, so a notice lookup
  matches exactly (D35).
- Flood waits are state the bridge keeps in memory; it writes no rows
  beyond what the port writes (D36).
- The bridge sends a notice to the row's `chat_id` (D8). It never calls
  `broker.release` itself, never writes an approval, intent, or outcome,
  sends nothing the outbox did not yield, and keeps no loop of its own.

## Rows (D7, D8, D9)

| Row | Stream | Payload |
| --- | --- | --- |
| `message.received` | channel | `received_id`, `verified`, the `Inbound` fields |
| `message.bound` | channel | `received_id`, `task_id` or null, `as` |
| `message.steered` | task | `received_id`, `channel`, `chat_id`, `message_id`, `text`, `attachments`, `provenance` |
| `notice.requested` | task | `notice_id`, `channel`, `chat_id`, `kind`, `about_key`, `text`, `reply_to` |
| `notice.sent` | task | `notice_id`, `sent` |
| `release.requested` | task | `effect_id`, `approval_id`, `owner` (`kernel`, `telegram`, or `email`) |

Notify channel: `valor_events`, from a trigger on every insert, carrying
`{id, task_id, type}`. `pg_notify('valor_stop')` stays beside it (D6).

## Binding (D10, D12, D13, D14, D15, D15a, D16)

The kernel binds; a bridge holds no binding logic. A reply finds its
task by `(channel, chat_id, message_id)` among the `sent` entries of
`notice.sent` rows and of bridge send outcomes. An email In-Reply-To that
matches no sent message is not a reply. The first matching row wins:

| The record | Bound as | Writes |
| --- | --- | --- |
| Not `verified`, or not from the operator | `none` | nothing |
| Reply to a task that is `merged` or `stopped` | `none` | a notice "task <id> is stopped" |
| Telegram reply to a task's notice or send, text exactly `stop` | `stop` | `task.stopped` |
| Reply to a `question` notice whose question is open | `answer` | `question.answered` |
| Reply to a `delivered` notice, task in `merge` | `feedback` | `feedback.given` |
| Telegram reply to an `effect` notice, text exactly `approve`, effect not yet released | `approve` | `approval.granted` and `release.requested` |
| The same, effect already released, done, or refused | `none` | a notice "already released" or "already done" |
| Any other reply to a task's notice or send | `steer` | `message.steered`; a notice when it looks like `approve` or `stop` (D15a) |
| Not a reply | `start` | `task.started` under the project listing the chat (email: the sender), else `valor` |

"Exactly" is the whole text, trimmed and casefolded. A text that, with
punctuation removed, is or begins with `approve` or `stop` but is not
exact owes the notice "Not an approval; reply `approve`" (or "reply
`stop`"); by email the notice says approvals and stops come by Telegram.
Email binds only `answer`, `steer`, and `start`; never `stop` or
`approve` (D12). A binding that raises binds `none` and owes a notice;
it never blocks later messages (D39). A reply that would steer a task
waiting on approval also owes "task <id> is waiting on approval; reply
`approve` or `stop`". In the operator group only messages from
`operator_telegram_id` bind; Valor's own messages there are not the
operator's and bind `none` (D16).

## Settings (D26)

| Field | Env | Default |
| --- | --- | --- |
| `machine` | `VALOR_MACHINE` | the short host name |
| `default_machine` | `VALOR_DEFAULT_MACHINE` | `machine`: the owner of a chat whose spec names none |
| `operator_telegram_id` | `VALOR_OPERATOR_TELEGRAM_ID` | none |
| `operator_email` | `VALOR_OPERATOR_EMAIL` | none (a tuple of Tom's addresses) |
| `operator_channel` | `VALOR_OPERATOR_CHANNEL` | `telegram` |
| `operator_chat` | `VALOR_OPERATOR_CHAT` | none: the "Valor rebuild" group's `-100...` id |
| `inbound_dir` | `VALOR_INBOUND` | `~/valor-inbound` |
| `serve_tick_s` | `VALOR_SERVE_TICK_S` | 60, a wake interval |

The operator chat is the Telegram group "Valor rebuild", holding only Tom
and Valor's account; its `chat_kind` is `group` (D16).

## Plists (D31)

Every service prints its plist for Tom to load, as backup does:
`python -m core serve --plist` for the kernel, each bridge's `--plist`
for itself.
