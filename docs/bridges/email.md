# Email bridge

The email bridge reads Valor's mailbox over IMAP, hands each message to
`core/` as a record, and sends the replies the broker releases over SMTP. It
conforms to the bridge port defined in [telegram.md](telegram.md#the-bridge-port):
intake, performers, and the outbox. This doc covers what is particular to
email.

**Status.** The kernel side of the port is built (`core/bridge.py`,
`core/intake.py`, `core/notices.py`); the email bridge process is not.
Every email record is `verified=false`, so it binds as nothing until DMARC
verification is built. `core/bridge.py` states Gmail's limit as 25,000,000
bytes of the whole encoded message; its size function is the email
bridge's, and until the bridge sets it, an email is not refused for size
at request time. Until the bridge is built, answers and feedback reach a task
through `python -m core` and record `via: "the command line"`. The email
code that exists today can be adapted to the port; the last sections say
what a conforming implementation keeps and what it hands to `core/`.

## What it serves

| Mechanism | Serves |
|---|---|
| Email as a second door for requests, answers, and feedback | Mission item 1: work arrives the way the people around Tom already send it |
| Threads bound to tasks through `Message-ID`, `In-Reply-To`, and `References` | Constraint "Reliable stop, recovery, and correction": feedback and answers carry provenance and reach the right task |
| Every send an `act` effect, with recipients inside the approved digest | Constraint "Bounded authority, metered spending"; effect classes [11] |
| Receipt acknowledged only after the ledger commits; sends reconciled by `Message-ID` | Constraint "Reliable stop, recovery, and correction": nothing is lost or sent twice |
| Inbound mail treated as data, never as authority | Retrieved content can act as instructions [7] |

## Receiving

**The mailbox.** One mailbox, Valor's, on a provider that offers IMAP and
SMTP. The bridge signs in with an app password kept in Keychain. Every
message Valor sends leaves from this address, as Valor.

**Polling.** The bridge polls `INBOX` for unseen messages on an interval set
in settings, fetching with `BODY.PEEK[]` so the fetch itself changes nothing.
For each message it builds one `Inbound` record, calls `intake.receive`, and
sets `\Seen` only after `receive` returns. A crash in between leaves the
message unseen; the next poll fetches it again, and the receipt index on
`(channel, chat_id, message_id)` lands it once. Mail the provider files as
spam never reaches `INBOX` and never reaches the bridge.

**The record.** Email fills the port's fields this way:

| Field | From |
|---|---|
| `message_id` | The `Message-ID` header. A message without one gets `uid:<UIDVALIDITY>:<UID>` |
| `chat_id` | The thread root: the first id in `References`, else `In-Reply-To`, else the message's own `Message-ID` |
| `chat_kind` | `email` |
| `sender_id` | The address in `From`, lowercased |
| `sender_name` | The display name in `From` |
| `reply_to` | `In-Reply-To` |
| `thread` | The ids in `References`, oldest first. Their bodies are already in the ledger as received or sent messages, so the bridge does not fetch them |
| `text` | The `text/plain` part, decoded; for HTML-only mail, the HTML reduced to text |
| `attachments` | Each attachment part saved to the media directory, with filename, media type, and size, within a per-message size and count cap; parts over the cap are listed as skipped |
| `headers` | `Subject`, `To`, `Cc`, `Date`, and the receiving server's `Authentication-Results` |

The bridge records every message in `INBOX`. It does not filter senders,
and it does not drop a message for having an empty body.

## How a message becomes work

The path is the one in [telegram.md](telegram.md#how-a-message-becomes-work):
the kernel binds replies to notices by structure, and the judgement layer
classifies the rest as a new request, a steer, feedback, an answer, a
correction, an exemplar, or conversation. Three things differ for email.

**Who counts as Tom.** A `From` header is a claim anyone can write. The
kernel treats an email as Tom's only when the record's
`Authentication-Results` shows the receiving server's DMARC pass for his
address. Without it, the message is someone else's: recorded as thread
context, and nothing more.

**No approvals or stops by email.** An email reply carries quoted history,
signatures, and client furniture around what the person typed, so a fixed
token such as `approve` cannot be read from it without interpretation, and
interpretation does not decide authority. Approvals and stops come through
Telegram or the command line. An email that reads like an approval leaves
the effect held, and the judgement layer may raise it with Tom.

**Answers and feedback.** A reply from Tom whose `In-Reply-To` is the
`Message-ID` of a question or delivery notice binds to that record and is
ledgered as `question.answered` or `feedback.given` with `by` Tom,
`via: email`, and `role_played: false`. The provenance fields are the ones
the kernel already records, added after the demonstration found that rows 177
and 207 could not tell Tom from a stand-in (rebuild-demonstration.md, Kernel
findings, item 5).

A new request that arrives by email meets the same request-underspecification
classifier (`intake.underspecified`) as one from Telegram, the guard Tom granted for Mission items 3
and 6 with its incidents in rebuild-demonstration.md (Attention log) and
rebuild-baseline.md (popoto #191 and #188).

## Sending

**The performer.** `email.send` is `act`. Its target is the `To`
addresses, lowercased, sorted, and comma-joined, and its payload is the whole message:

| Payload field | Meaning |
|---|---|
| `to`, `cc` | Every recipient, as addresses |
| `subject` | The final subject line, including any `Re:` |
| `body` | Plain text, UTF-8 |
| `in_reply_to`, `references` | Threading headers, when the message replies |
| `files` | Each attachment as a path and its sha256 |

The digest Tom approves covers the recipients, the subject, the body, and
each file's bytes. The bridge builds the MIME message from exactly these
fields: `text/plain` with attachments as `multipart/mixed` parts when there
are files. It adds only the headers transport requires: `From` (Valor's
address), `Date`, and `Message-ID`.

**Recipients are `core/`'s choice.** Replying to a thread, `core/` proposes
reply-all: the original sender plus every `To` and `Cc` address, minus
Valor's own. Tom sees that list in the approval prompt. The bridge sends to
the addresses in the payload and to no others.

**Idempotency.** The performer derives the `Message-ID` from the broker's
idempotency key, so the same effect always carries the same id. `lookup`
reconciles a dangling intent by searching the mailbox's sent folder for that
`Message-ID` header. A search that finds nothing is read as never sent only
after `reconcile_after_s` (`core/settings.py`) has passed since the intent;
until the bridge ships its own settle function, that is the rule. The outcome records the `Message-ID`, which is how a
reply to the sent message binds back to its task.

**One attempt.** `perform` submits once over SMTP with STARTTLS. A refused
connection, a refused login, or a refused message returns a failed outcome
with the server's reply. When the server accepts the message for some
recipients and refuses others, the outcome is `done` and lists the refused
addresses; reaching them is a new request and a new approval. The bridge
keeps no retry loop, backoff schedule, or dead-letter queue.

**Operator notices.** Notices go to Tom's operator channel, which is set in
settings and is Telegram by default. When settings name email, the bridge
sends notices to Tom's address with a `Message-ID` it records in
`notice.sent`, so his reply binds the same way.

## Stop and recovery

A stopped task's held sends stay held; `broker.release` refuses them on the
`task.stopped` fence. A bridge killed at any point loses nothing: an unseen
message is fetched again and lands once; a send killed after its intent is
found or not found by `lookup`; a send killed before its intent is still in
the outbox. launchd runs the bridge as a resident process and restarts it if
it exits.

## What the bridge never does

- **Routing.** It does not map senders or domains to projects, workspaces,
  or customers, and does not merge threads by subject line.
- **Triage or judgement.** It does not classify mail, screen for prompt
  injection, or decide what deserves a reply. Inbound mail is data [7].
- **Persona rewriting.** It does not draft, rephrase, add prefixes, or
  convert formats. The body it sends is the body Tom approved.
- **Retries without approval.** A failed send stays failed.
- **State of its own.** It keeps no message-id map, history cache, or queue
  outside the ledger. Its only local state is the media directory.
- **Alerts.** It raises no operator alerts of its own. A login that fails is
  a failed poll in its log and, for a send, a failed outcome on the ledger;
  telling Tom is `core/`'s job.

## Conforming an implementation

The bridge may survive as existing code. An implementation conforms to the
port when:

1. **It keeps the transport.** IMAP connection and polling, message
   parsing (addresses, subject, body extraction, attachment extraction and
   persistence), MIME assembly with `In-Reply-To` and `References`, and SMTP
   submission. These are I/O and stay.
2. **It marks `\Seen` after `receive`.** A poller that marks messages seen
   before fetching them changes to fetch with `BODY.PEEK[]` and mark after
   the ledger commits.
3. **Its handler ends at `intake.receive`.** Project lookup, customer
   resolution, subject coalescing, session ids, persona choice, triage,
   injection screening, and session enqueueing move to `core/` or are
   removed.
4. **Its send is a performer.** The SMTP send becomes `perform`, with a
   `Message-ID` derived from the key, plus a `lookup` over the sent folder.
   An outbox relay over a queue becomes the outbox loop over released
   effects. Reply-all recipient computation moves to `core/`. Retry
   counters, backoff, and dead-letter writing are removed.
5. **Its ids go to the ledger.** Inbound `Message-ID`s in
   `message.received`, outbound ones in `effect.outcome` and `notice.sent`.
   Any other store mapping message ids to sessions is removed.
6. **It imports only `core/` ports**, and nothing from the Telegram bridge.
7. **It passes the integration tests** in `tests/`: a real test mailbox, a
   real Postgres, no mocks. A message fetched before a kill lands once; a
   send killed after its intent is found by `lookup` and not repeated; a
   reply to a delivery notice records `feedback.given` with `via: email`.

## Gaps

- **The sent folder.** `lookup` assumes the provider files mail submitted
  over SMTP into the sent folder, as Gmail does. On a provider that does not,
  the performer appends the message to the sent folder over IMAP after
  submitting, and a kill between the two leaves a send `lookup` cannot see.
- **DMARC as the test of Tom's identity.** It holds only while Tom's
  domain publishes a DMARC policy and the receiving server writes
  `Authentication-Results`. Whether this counts as a check under the
  governance paragraph, and so needs Tom's grant, is his call.
- **Approving every send.** As on Telegram, each send to anyone but Tom
  waits for his tap, and the broker has no standing grants.
