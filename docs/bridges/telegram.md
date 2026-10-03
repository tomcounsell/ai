# Telegram bridge

The Telegram bridge is how Tom talks to Valor from his phone. It receives
messages on Valor's own Telegram account, hands them to `core/` as records,
and delivers the messages the broker releases. It holds no opinion about what
a message means, who should handle it, or whether a reply may go.

This doc also owns the bridge port, the contract in `core/` that every bridge
conforms to. The email bridge ([email.md](email.md)) conforms to the same port
and documents only what is particular to email.

**Status.** The port is built: `core/bridge.py` (the port, the declared
send types, their limits, and the outbox), `core/intake.py` (the record
and binding), and `core/notices.py` (operator notices), run by the
resident kernel, `python -m core serve`. The Telegram bridge is built in
`bridges/telegram/`, run by `python -m bridges.telegram run`; it is live
only during Tom's test windows. Its tests run over the real port and the
test database, with a local emulator (`tests/telegram_emulator.py`) as
Telegram. Tom also reaches a task through `python -m core` (`answer`,
`feedback`, `approve`, `release`, `stop`, `correct`), and those records
carry `via: "the command line"`. The section "As built" below says what
the bridge's code does.

## What it serves

| Mechanism | Serves |
|---|---|
| Receiving Tom's messages anywhere, any time | Mission item 1 (Tom gives work in conversation and never coordinates the gaps) |
| Questions, deliveries, and approval prompts reaching Tom where he already is | Mission item 6 (attention spent as carefully as money) |
| Replies bound to the task they answer, by structure | Constraint "Reliable stop, recovery, and correction": corrections and feedback carry provenance |
| Every send an `act` effect released by the broker | Constraint "Bounded authority, metered spending"; effect classes [11] |
| Idempotent receipt and send, acknowledged only after the ledger commits | Constraint "Reliable stop, recovery, and correction": stop and restart lose nothing |
| Inbound text treated as data, never as authority | Retrieved content can act as instructions [7]; the kernel decides what a thing may do |

The demonstration showed the need for the second door. Its second kernel
finding was "No feedback path": Tom could not send a delivery back, and
`core feedback` was added mid-demo (rebuild-demonstration.md, Kernel findings,
item 2). Every PM intervention in that record went through a command line run
by the orchestrator, not through Tom's own hands (rebuild-demonstration.md,
Where Tom acted as project manager).

## The bridge port

The port lives in `core/` and is the only thing a bridge imports. It has
three parts: intake for what comes in, performers for what goes out, and the
outbox that connects released effects to the process that owns the
connection.

### Intake

A bridge turns each platform event into one `Inbound` record and passes it to
`intake.receive`. The record carries facts the platform states. It carries no
interpretation.

| Field | Meaning |
|---|---|
| `channel` | `telegram` or `email` |
| `chat_id` | The conversation: a Telegram chat id, or for email the thread root's Message-ID |
| `chat_kind` | `dm`, `group`, or `email` |
| `message_id` | The platform's id for this message, unique within `chat_id` |
| `sender_id` | The platform's stable id for the sender: a Telegram user id, or an email address |
| `sender_name` | Display name, for rendering only; never used to identify anyone |
| `sent_at` | The platform's timestamp |
| `kind` | `message` |
| `text` | The body as the platform delivered it, unaltered |
| `reply_to` | The `message_id` this message replies to, if any |
| `thread` | The ancestors the bridge could fetch, oldest first, each `{id, text, attachments}`, nothing downloaded |
| `topic_id` | The Telegram forum topic, when the message is in one |
| `attachments` | Files the bridge downloaded, each `{name, mime, bytes, path}`, or `{name, mime, bytes, skipped}` with the reason it could not fetch the file |
| `headers` | Channel facts with no field above: for Telegram, `grouped_id` of an album; for email, the raw headers |

`receive` appends one `message.received` row to the ledger on the bridge's
channel stream (the same pattern as the `corrections` stream: `task_id` names
the stream) and returns only after the transaction commits. A unique index on
`(channel, chat_id, message_id)` makes receipt idempotent: a message replayed
after a restart lands once. The bridge acknowledges the message to the
platform (advances its update state, marks it read) only after `receive`
returns. A crash between the two replays the message, and the index absorbs
the replay. This is how a stop or a restart loses nothing.

`receive` decides nothing. Turning a received message into a task, an answer,
or anything else happens afterward in `core/` (see "How a message becomes
work" below), in a separate step that reads `message.received` rows.

### Performers

Every outbound operation is a broker performer, with the same shape as the
`push_branch` performer that exists today: an `action_type`, a declared
`effect_class`, `perform(action, key)`, and `lookup(action, key, since)`,
`since` being the time of the effect's intent. The broker
holds every `act` request until Tom approves it, writes `effect.intent`
before `perform` runs, and writes `effect.outcome` after. A kill between the
two leaves a dangling intent, which `lookup` reconciles by asking the platform
whether the message with that idempotency key exists.

| Action type | Class | Target | Payload |
|---|---|---|---|
| `telegram.send_message` | `act` | chat id | `text`, `reply_to`, `topic_id`, `files` (each a path and its sha256) |
| `email.send` | `act` | the `To` addresses, lowercased, sorted, comma-joined | see [email.md](email.md) |

The payload is the message. The digest Tom approves binds the exact text, the
reply target, and each file's bytes, so what leaves is what he saw.

### The outbox

The Telegram connection belongs to one process: an MTProto session cannot be
shared safely between two. So performers for a channel run inside that
channel's bridge process, not in whichever process ran `core release`.

The outbox is a query over the ledger, not a separate queue: held effects
of the bridge's action types that carry an unused `approval.granted` and no
`effect.intent` yet. Where [architecture.md](../architecture.md) separates
release from approval, the query also requires the release.

The bridge listens on a Postgres notification channel (`valor_events`, notified by every new row), and on each wake it calls
`broker.release` for each such effect in its own process. Release reads the
`task.stopped` fence, binds the unused approval, writes the intent, calls the
performer, and writes the outcome. The bridge performs nothing the broker
has not passed to it.

### Operator notices

Some messages are the kernel speaking to Tom about his own work: a question a
turn asked, a delivery, a held effect waiting for his tap, a failed workspace, a message that was not acted on. These are operator notices.

A notice goes only to Tom's operator chat, which is fixed in settings. No
turn, task, or payload names the recipient, so a notice cannot reach anyone
else. The kernel writes `notice.requested` with the text and the record it
concerns (`question_id`, `task.delivered` row, `effect_id`); the bridge's
outbox sends it and writes `notice.sent` with the platform's `message_id`.
That `message_id` is what lets Tom's reply bind to the record (next section).

Notices are the approval surface itself, so they do not wait for an
approval (Tom, 2026-10-01). The README's effect table names "send" as
`act`; a notice to Tom's own chat is the one send outside it. Every other message, including a reply in a group where Tom is one
member, is an `act` effect. Each notice is also an attention item: the
Evidence section counts "decisions escalated to Tom per finished task", and
`notice.sent` rows are where that count starts.

### The port in code

```python
class Bridge(Protocol):
    channel: str  # "telegram" or "email"

    def performers(self) -> dict[str, tuple[PerformFn, LookupFn]]: ...
    async def run(self, outbox: Outbox) -> None: ...
    async def tick(self) -> None: ...
```

`performers` maps each send type the kernel declares for the channel to how
the bridge sends it and how it finds a send that happened. `run` owns the
connection: it receives, records each message through `intake.receive`,
acknowledges, and sends what the outbox yields until the process stops. The
outbox calls `tick` on every wake. The limits (`LIMITS` in `core/bridge.py`)
are protocol facts the kernel holds, so it refuses an impossible send when
it is requested and the bridge never changes a message to send it.

## Receiving

**The account.** The bridge signs in as Valor's own Telegram user account
over MTProto, not as a bot. A bot cannot post into a user-to-user chat, so
its messages would land in a separate bot conversation, outside the thread
that binds a reply to its task, and it would be a second identity. The API
id and hash live in `telegram-keys` and the session in `telegram.session`,
both mode 600 in the kernel key directory (the one holding the database
password file), which every turn's sandbox denies.

**What arrives.** New messages in the chats this machine owns
(`intake.owned("telegram")`). The bridge delivers each as one `Inbound`
record. Edits, reactions, and deletions are not part of the port.

**Reply chains.** When a message replies to another, the bridge fetches the
ancestors through the API, up to a fixed number of hops, and puts them in
`thread`. Fetching is I/O and belongs here. Choosing which ancestor roots the
conversation, and which task that root belongs to, is `core/`'s.

**Attachments.** Photos, documents, and voice notes are downloaded to
`inbound_dir/telegram/`, each named by its sha256, and listed with path,
type, and size. A download
that fails or times out is listed with its reason, so a turn can say exactly
what it could not read. Transcribing or describing media is not the bridge's
work.

**After downtime.** The bridge fills the gap itself, on every connect and
every tick: it pages back through each owned chat to the newest message
id its last pass saw, kept in `telegram-seen.json` in the key directory
(for a chat with no entry there, to the lowest id recorded for it),
and receives every message `intake.recorded` does not list. Message ids
in a Telegram chat only grow, so nothing newer is skipped. The receipt index makes each
replay land once.

## How a message becomes work

None of this runs in the bridge. It is described here because it is what the
bridge's records feed, and the bridge's fields are shaped for it.

### Bound replies: decided by the kernel

A message from Tom's verified `sender_id` that replies to a notice binds
deterministically to the record the notice carried:

| Tom replies to | Becomes | Ledger row |
|---|---|---|
| A question notice | His answer to that question | `question.answered` |
| A delivery notice | Feedback on that delivery, which puts the task back to work | `feedback.given` |
| An approval prompt, with exactly `approve` | His tap on that held effect; his literal message is the note | `approval.granted` |
| An approval prompt, anything else | A message about the effect; the effect stays held | `message.received` only |
| Any notice of a task, with exactly `stop` | Stop | `task.stopped` |

`question.answered` and `feedback.given` already exist and already carry
`provenance` with `by`, `via`, `at`, and `role_played`. A bridge-delivered
reply records `by` as Tom, `via` as `telegram`, and `role_played: false`. A
stand-in speaking for Tom does so through the command line with
`--role-played`, never through Tom's account. The demonstration's fifth
finding is why both fields exist: rows 177 and 207 both read `"by": "tom"`
and 207 was role-played (rebuild-demonstration.md, Kernel findings, item 5).

Approval and stop bind by structure, a verified sender replying to a known
message with a fixed token, because they decide what may happen. A classifier
decides what a thing is; it never decides what a thing may do. Free text that
reads like approval does not approve: the effect stays held, and the
judgement layer may raise it with Tom.

Approving from chat records `approval.granted` the same way `core approve`
does. Performing an approved send is the outbox's job; the approve-then-release
sequence itself belongs to [architecture.md](../architecture.md).

### Unbound messages: classified by the judgement layer

A message from Tom that replies to nothing, or to a message that is not a
notice, goes to the judgement layer ([judgement-layer.md](../judgement-layer.md)),
which reads it with its thread and says what it is:

- **A new request.** It becomes a task. Before the first turn, the
  request-underspecification classifier (`intake.underspecified`) reads it: an underspecified request (a
  one-line ask, an ask leaning on an example, an ask naming existing UI
  without scope) goes to a clarify turn, and a precise one goes straight to
  build. That classifier is a guard Tom granted, ledgered with its incidents
  (psyoptimal #894 in rebuild-demonstration.md, Attention log; popoto #191
  and #188 in rebuild-baseline.md, "What this says about the old SDLC
  stages", Clarify), serving Mission items 3 and 6, with a ninety-day expiry.
- **A steer for a running task.** It is queued as steering for that task
  and drained at the next turn boundary. Steering belongs to
  [architecture.md](../architecture.md).
- **Feedback or an answer** that Tom sent without using reply. It binds to
  the task the judgement layer names, and the binding is ledgered with its
  confidence.
- **A correction.** "From now on..." becomes a `correction.recorded` row with
  `source_class: direct`, rendered into every later turn.
- **An exemplar.** Work Tom loved, and why, becomes a `correction.recorded`
  row with `source_class: exemplar`, the same store with a distinct source
  class (Mission, Evidence: "Tom's feedback, both directions").
- **Conversation.** Recorded, nothing more.

A low-confidence call takes its judgement task's abstain route
([judgement-layer.md](../judgement-layer.md), Confidence gating) rather than
being acted on. Where that route reaches Tom it costs attention, so the
floor is set from the calibration record and the attention log, not once.

A task started from a message takes its effect ceiling
from settings, never from the message text, which would let a classifier
set authority, with every push or send still waiting for Tom's tap (Tom,
2026-10-01).

A correction or exemplar is content, rendered into turns. It never widens a
ceiling or grants governance. Those change only through
`core/` commands and approvals.

### Other people

Messages from anyone other than Tom are recorded as `message.received` and
serve as thread context. They do not start tasks, answer questions, give
feedback, or record corrections. A request someone else makes reaches work
only when Tom asks for it.

## Sending

**Verbatim.** The bridge sends the payload's text as it is, with the reply
target and files the payload names. Persona rendering happens in `core/`
before the request, so the digest Tom approves is of the final text. A bridge
that reformatted, trimmed, or prefixed a message would send something he did
not approve.

**Length.** Telegram refuses a text message over 4,096 UTF-16 code units
after entity parsing. A longer text is sent as consecutive messages
(`split_text`), each within the limit, broken at a newline, else a space;
the bytes are unchanged, so the approval still holds. A file over 2000 MiB
is refused when it is requested.

**Idempotency.** MTProto's send request carries a `random_id` the server
uses to detect a repeated send. The performer derives it from the broker's
idempotency key and the message's place in the send, so a resend of the
same effect is refused by Telegram as a duplicate rather than delivered
twice. Before a key's first send, the bridge records the chat's newest
message id under the key in `telegram-sends.json` in the key directory.
`lookup` reconciles a dangling intent by scanning the account's own
messages in the target chat with ids above that one, since message ids in
a chat only grow, skipping ids already recorded as sent, for that send's
text, files, reply target, and topic. Clock skew between the Mac and
Telegram cannot hide a message, since no date is read. A key with no
record was never sent, so its lookup finds nothing. Two matches, or part of a split send
found, is `broker.Unknown`: nothing is concluded. The outcome records `chat_id` and `message_id`,
which is how a later reply to the sent message binds back to its task.

**One attempt.** `perform` makes one delivery attempt. A flood wait, a
refusal, or no connection returns a failed outcome with the reason and,
for a flood wait, the wait time, which later requests wait out. A
connection lost after a request was written is `broker.Unknown`: no
outcome, and the outbox's reconcile settles it through `lookup`. Sending again is a new request and a new
approval. The bridge keeps no retry loop, dead-letter queue, or resend
schedule.

## Stop and recovery

A stopped task's held effects stay held, and `broker.release` refuses them
because it reads the `task.stopped` fence. A bridge process killed at any
point loses nothing: an unacknowledged inbound message replays and lands
once; a send killed after its intent is reconciled by `lookup` on restart; a
send killed before its intent was never sent and is still in the outbox.

launchd runs the bridge as a resident process and restarts it if it exits.
Its resident memory counts against the RAM plan in
[machine.md](../machine.md).

## What the bridge never does

- **Routing.** It does not decide which task, workspace, or repository a
  message belongs to, or whether a message deserves a response.
- **Triage or judgement.** It does not classify intent, detect requests,
  screen for prompt injection, or rank urgency. Inbound text is data [7]; the
  judgement layer reads it and the kernel decides what it may do.
- **Persona rewriting.** It does not draft, rephrase, shorten, prefix, or
  add links to outbound text.
- **Retries without approval.** A failed send stays failed until a new
  request is approved.
- **Lifecycle signalling.** It posts no acknowledgement reactions, typing
  indicators, or progress messages of its own. Progress Tom needs comes as a
  notice from `core/`.
- **State of its own.** It keeps no queue, deduplication store, session map,
  or message history outside the ledger. Its only local state is the
  MTProto session, the downloaded files, and a flood wait held in memory.

## Conforming an implementation

The bridge may survive as existing code. An implementation conforms to the
port when:

1. **It keeps the transport.** The MTProto client and sign-in, the event
   handler for new messages, reply-chain fetching, media download, and
   sending text and files. These are I/O and stay.
2. **Its handler ends at `intake.receive`.** Everything a handler does after
   building the record (choosing a project or session, deciding whether to
   respond, steering, classifying intent, starting a session, writing
   memory, reacting with emoji) moves to `core/` or is removed.
3. **Its sends are performers.** A function that sends a message becomes
   `perform`, with `random_id` derived from the key, plus a `lookup`. A
   relay that pops messages from a queue becomes the outbox loop over
   released effects. Retry counters, dead-letter routing, and requeueing are
   removed.
4. **Its ids go to the ledger.** Sent message ids are recorded in
   `effect.outcome` and `notice.sent` rows, and received ids in
   `message.received`. Any other store mapping messages to sessions is
   removed.
5. **It imports only `core/` ports.** No settings module, model, or helper
   from outside `core/`, and nothing from another bridge.
6. **It passes the integration tests** in `tests/`: the emulator as its
   own process, the bridge killed by its pid, real Telegram test servers
   with test accounts (`tests/test_live_telegram_dc.py`), and a real
   Postgres. A message replayed after a kill lands
   once; a send killed after its intent is reconciled and not repeated; a
   reply to a question notice records `question.answered` with
   `via: telegram`.

## Gaps

- **`random_id` duplicate behaviour.** That Telegram refuses a repeated
  `random_id` from a user account, rather than silently delivering again,
  is what `tests/test_live_telegram_dc.py` checks on the test servers. The
  broker never performs one effect twice, so for effects it is defence in
  depth; for notices, which the outbox yields until marked sent, the scan
  for the notice's short id backs it.
- **Approving sends one at a time.** Every reply Valor sends to anyone other
  than Tom waits for his tap. In a busy group that is many taps. A standing
  grant (say, "replies in this chat") would cut them, but the broker has no
  standing grants: every release consumes one approval bound to one digest.
  Whether to add one is Tom's call, since it widens authority.

## As built

`bridges/telegram/`, run as `python -m bridges.telegram run` by the launchd
job `com.valor.kernel.telegram` that `--plist` prints.

| Module | Does |
|---|---|
| `wire.py` | The only module importing Telethon: one client with sequential updates, no automatic flood sleep, reconnect, or request retry; raw `SendMessageRequest` and `SendMediaRequest` with a given `random_id`, no parse mode, no link preview |
| `inbound.py` | One message to the `Inbound` fields: raw `message.message`, the forum topic and reply target, the reply chain (20 hops), files by sha256 with a download timeout of ten seconds or five plus one per MB, tried once more at twice that |
| `gap.py`, `bridge.py` | Gap fill on connect and in `tick()`; the live handler, which drops the connection when `receive` fails so the next connect's fill takes the message; the outbox loop; SIGTERM lets an in-flight send and its outcome finish |
| `send.py` | `telegram.send_message` perform and lookup, and the notice send to the row's `chat_id`, marked through `outbox.sent` |
| `state.py` | The bridge's own files beside the session: `telegram-seen.json` (the newest id each chat's last gap-fill pass saw) and `telegram-sends.json` (each send key's chat and the newest id before its first send), each written whole and renamed into place |
| `kernel.py` | The port gathered into one object, so the bridge imports only `core.bridge`, `core.intake`, `core.broker`, `core.settings`, `core.db`, and `core.credentials` |
| `login.py`, `__main__.py` | `login` (Tom types the code and password; nothing stores them), `keys` (copies `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from the vault into `telegram-keys`), `run`, `--plist` |

Nothing the bridge prints holds any part of the API hash.
