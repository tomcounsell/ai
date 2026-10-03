# Email bridge

The email bridge reads Valor's mailbox over IMAP, hands each message to
`core/` as a record, and sends the replies the broker releases over SMTP. It
conforms to the bridge port defined in [telegram.md](telegram.md#the-bridge-port):
intake, performers, and the outbox. This doc covers what is particular to
email.

**Status.** Built in `bridges/email/`. `python -m bridges.email run`
runs it under `bridge.serve`; `keys` copies its credentials and `--plist`
prints its launchd job. The last section lists the modules.

## What it serves

| Mechanism | Serves |
|---|---|
| Email as a second door for requests, answers, and feedback | Mission item 1: work arrives the way the people around Tom already send it |
| Threads bound to tasks through `Message-ID`, `In-Reply-To`, and `References` | Constraint "Reliable stop, recovery, and correction": feedback and answers carry provenance and reach the right task |
| Every send an `act` effect, with recipients inside the approved digest | Constraint "Bounded authority, metered spending"; effect classes [11] |
| Receipt acknowledged only after the ledger commits; sends reconciled by `Message-ID` | Constraint "Reliable stop, recovery, and correction": nothing is lost or sent twice |
| Inbound mail treated as data, never as authority | Retrieved content can act as instructions [7] |

## Receiving

**The mailbox.** One mailbox, Valor's (`email_address`), on Gmail over IMAP
(`imap_host`, port 993) and SMTP (`smtp_host`, port 587, STARTTLS), with
certificates verified. The bridge signs in with an app password that
`python -m bridges.email keys` copies from the vault `.env` into
`mail-keys` in the kernel key directory (mode 600), printing each name
with `written`, `kept`, or `missing` and never a value. Every message Valor
sends leaves from this address, as Valor.

**The watch.** The bridge holds one IMAP connection, with no socket
timeout, and waits on it with IDLE (RFC 2177), so new mail arrives as an
event: the server's `EXISTS` response ends the wait. RFC 3501 section 5.4
lets a server log out a client idle for 30 minutes, so the bridge ends
IDLE and issues it again every 29 minutes (`imap.IDLE_REISSUE_S`), as RFC
2177 advises; that is the only read bound while idling. After each wait,
and once on connecting, the bridge:

1. selects `INBOX` and reads its `UIDVALIDITY`;
2. searches `UNSEEN SINCE <email_since>` with an `OR` tree of `FROM` terms
   over `intake.owned("email")`, and keeps a match only when
   `intake.owns("email", sender)`, since `FROM` matches substrings;
3. fetches the headers of every match and asks `intake.recorded` which are
   already received; those are marked `\Seen` without a body fetch;
4. for each other message, oldest first, fetches it with `BODY.PEEK[]` so
   the fetch changes nothing, parses it, saves its attachments, calls
   `intake.receive`, and sets `\Seen` only after `receive` returns.

A crash between `receive` and `\Seen` leaves the message unseen; the next
search finds it recorded and marks it, and the receipt index on `(channel,
chat_id, message_id)` lands it once in any case. A message whose parse,
save, or receive raises is logged with its UID and left unseen, and the
search goes on; it is tried again after the next wait. A failed login or
connection, or a dropped one, is one line in the log; the bridge connects
again on the outbox's next wake (`Bridge.tick()`, every `serve_tick_s` or
on a ledger row). Mail Gmail files as spam never reaches `INBOX`, and mail
opened in webmail before the bridge searches is seen already and is not
received until it is marked unread.

**The record.** Email fills the port's fields this way:

| Field | From |
|---|---|
| `message_id` | The `Message-ID` header. A message without one gets `sha256:<digest of its bytes>`, the same on every fetch |
| `chat_id` | The thread root: the first id in `References`, else the message's own `Message-ID` |
| `chat_kind` | `email` |
| `sender_id` | The address in `From`, lowercased |
| `sender_name` | The display name in `From` |
| `reply_to` | `In-Reply-To` |
| `thread` | One `{"id"}` per id in `References`, oldest first. Their bodies are already in the ledger as received or sent messages, so the bridge does not fetch them |
| `text` | The subject, a blank line, then the first `text/plain` part, decoded (an unknown charset read as UTF-8); for HTML-only mail, the HTML reduced to text with scripts and styles dropped |
| `attachments` | Each attachment part saved under `inbound_dir/email/`, named by the sha256 of its bytes, as `{name, mime, bytes, path}` with the filename sanitized; a part that does not decode is `{name, mime, bytes, skipped}` |
| `headers` | `subject`, `date`, `to`, `cc`, the raw `from` and `authentication_results` values as received (topmost first), and `uid` and `uidvalidity` |

The bridge receives mail from owned senders only: Tom's addresses
(`operator_email`) and the `email:<address>` chats a project spec on this
machine lists. Replies to Valor's mail from anyone else are not recorded
as thread context. A message with an empty body, or with no `From` (whose
sender is empty, so never owned), is not dropped by the parser. There is no
inbound size or part cap.

## How a message becomes work

The path is the one in [telegram.md](telegram.md#how-a-message-becomes-work):
the kernel binds replies to notices by structure, and the judgement layer
classifies the rest as a new request, a steer, feedback, an answer, a
correction, an exemplar, or conversation. Three things differ for email.

**Who counts as Tom.** A `From` header is a claim anyone can write.
`intake.receive` sets an email record's `verified` with
`intake.dmarc_verified(headers, email_authserv_id)`: true only when the
record has one `From` header holding one address, and the topmost
`Authentication-Results` value (the one Gmail writes, `mx.google.com`)
reports `dmarc=pass` with `header.from` that address's domain. Lines below
the topmost are the sender's own and count for nothing. An unverified
record from Tom's address is recorded and starts nothing. The test is the
`email.dmarc` guard in `core/guards.py`, granted under open question 17
and seeded by `migrate`; it fires when a record from `operator_email` is
not verified, and each firing is a `guard.fired` row on the `guards`
stream written with the record (`guards.fired` reads them). The bridge
never sets `verified`.

**No approvals or stops by email.** An email reply carries quoted history,
signatures, and client furniture around what the person typed, so a fixed
token such as `approve` cannot be read from it without interpretation, and
interpretation does not decide authority. Email binds only `answer`,
`steer`, and `start`: a reply whose text is exactly `stop` or `approve`
binds as a steer. Approvals and stops come through Telegram or the command
line.

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

**The performer.** `email.send` is `act`. Its target is the `to` list,
lowercased, sorted, and comma-joined, and its payload is the whole message:

| Payload field | Meaning |
|---|---|
| `to`, `cc` | Every recipient, as addresses |
| `subject` | The final subject line, including any `Re:` |
| `body` | Plain text, UTF-8 |
| `in_reply_to`, `references` | Threading headers, when the message replies |
| `files` | Each attachment as a path and its sha256 |

The digest Tom approves covers the recipients, the subject, the body, and
each file's bytes. `perform` reads each file once and compares its sha256
with the payload's; a mismatch or a missing file fails before SMTP
connects, so a file changed after Tom's tap never leaves. The message is
built by `core/mail.py`'s `email_message`, which the bridge reaches through
`core.bridge`: a UTF-8 `text/plain` body, files as `multipart/mixed`
parts, the subject and `References` chain unchanged, and only the headers
transport requires added: `From` (Valor's address), `Date`, and
`Message-ID`.

**Size.** Gmail refuses a message over 25 MB encoded. Email's entry in
`core/bridge.py`'s `LIMITS` has `max_message_bytes` 25,000,000 and
`message_bytes`, `mail.email_encoded_bytes`: the length of the message
`email_message` builds, attachments base64 encoded. A send over
25,000,000 bytes is refused at request time with that limit as the
reason, so Tom never approves an impossible send. The performer builds
with the same function, so the size measured is the size sent. `MAIL FROM`
carries `SIZE`.

**Recipients are `core/`'s choice.** A turn replies to a thread by
requesting `email.send` with `reply_to` (the received email's
`received_id` or `Message-ID`), a `body`, and any `files`.
`core/session.py` finds that message in the ledger and fills in reply-all
through `mail.reply_all`: the original sender in `To`, every other
`To` and `Cc` address in `Cc`, minus Valor's own, lowercased and without
repeats, with a `Re:` subject, `In-Reply-To`, and the whole `References`
chain. A `reply_to` that names no received email is
an error to the turn and requests nothing. Tom sees the recipient list in
the approval prompt. The bridge sends to
the addresses in the payload and to no others.

**Idempotency.** The `Message-ID` is `<valor.<first 32 hex of the key's
sha256>@<Valor's domain>>`, so a retry of one effect repeats its id and two
effects never share one. `lookup(action, key, since)` opens the folder
whose `LIST` flags include `\Sent` and searches for that exact id among
messages from the day before `since` (the intent's time): with Gmail's
`X-GM-RAW "rfc822msgid:"` when the server offers it, else `HEADER
Message-ID`. Found, it returns the result. Not found, or a mailbox it
cannot read, raises `broker.Unknown`: Gmail copies mail sent over SMTP into
Sent Mail ("Choose your IMAP email client settings for Gmail", Gmail
Help), and no document gives how long that takes, so a miss does not show
the send failed. The bridge reads Sent Mail only for a send in doubt
(below).
The result's `sent` entry carries the `Message-ID` and the thread root, which
is how a reply from any recipient binds back to its task.

**One attempt.** `perform` submits once over SMTP with STARTTLS. The steps
RFC 5321 section 4.5.3.2 gives a client timeout wait under it: the
greeting, `MAIL`, and `RCPT` 5 minutes, `DATA`'s reply 2 minutes, each
send call of the body 3 minutes, at most one TLS record (16,384 bytes,
RFC 8446 section 5.1) per call, and the final reply 10 minutes. EHLO,
STARTTLS, and AUTH have no value there and wait with no timer. No timer
spans the upload, so a large message takes the time its size takes. A
server accepts a message only on the end of data line (RFC 5321 section
4.1.1.4). Before that line has gone out in full, any end (a refused
connection or login, a refused `MAIL`, every recipient refused, a refused
`DATA`, a write that fails or stalls) is definite: the outcome is `failed`
with the reason, and Sent Mail is not read. After it, a 250 is `done`, and
a 4xx or 5xx reply is `failed` (section 4.2.1: the action did not occur).
Any other end (no reply, the 10 minute timeout, a garbled reply, a reply
line too long to read, another code) raises `broker.Unknown`: the server
may have stored the message. The intent stays in flight with no outcome,
and the outbox reconciles it on each wake (every `serve_tick_s` or ledger
row) by reading Sent Mail for its Message-ID once no process performs the
send: found is `done`; not found leaves it in flight for the next wake,
and `tasks.audit` lists it. After the 250,
`QUIT` is sent and its reply is not awaited, since the send is already
done. When the server accepts
the message for some recipients and refuses others, the outcome is `done`
with `refused` listing each address and its reply; reaching them is a new
request and a new approval. The bridge keeps no retry loop, backoff
schedule, or dead-letter queue.

**Operator notices.** Notices go to Tom's operator channel, Telegram. The
email bridge performs the outbox's releases and ignores its notices.

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
- **State of its own.** It keeps no message-id map, UID cursor, history
  cache, or queue outside the ledger. Its only local state is the inbound
  directory.
- **Alerts.** It raises no operator alerts of its own. A login that fails is
  a line in its log and, for a send, a failed outcome on the ledger;
  telling Tom is `core/`'s job.

## The implementation

| Module | Holds |
|---|---|
| `bridges/email/__init__.py` | `EmailBridge`: `channel`, `limits`, `performers()` giving `email.send`'s `(perform, lookup)`, and `run(outbox)`, the IMAP watch beside each released send, reconnected on `tick()` |
| `bridges/email/__main__.py` | The verbs `run`, `keys`, and `--plist` (`KeepAlive`, logs in `log_dir/email.log`) |
| `bridges/email/config.py` | `Config`, from settings and `mail-keys`, and the SMTP timeouts (`SMTPTimeouts`); the passwords are held here only |
| `bridges/email/parse.py` | Raw mail to a record's fields, and attachments to files |
| `bridges/email/imap.py` | The watch: search, receive, and IDLE (`IDLE_REISSUE_S`) |
| `bridges/email/smtp.py` | `perform` and `lookup` |
| `core/mail.py` | `reply_all`, the message builder, and its encoded size |
| `core/session.py` | A `reply_to` request filled in as reply-all |
| `core/intake.py` | `dmarc_verified` |

The bridge imports only `core.bridge`, `core.intake`, `core.broker`,
`core.settings`, `core.db`, and `core.credentials`, and nothing from the
Telegram bridge. Its tests run against Dovecot and a local SMTP server
(`tests/mailserver.py`) with a real Postgres and no mocks.

## Gaps

- **The sent folder.** `lookup` relies on Gmail filing mail submitted over
  SMTP into the folder flagged `\Sent`. A send in doubt that is never
  filed (a send the server never stored, or a provider that does not file
  sent mail) stays in flight with no outcome, and each outbox wake reads
  Sent Mail for it again over a new IMAP connection.
- **Waits with no timer.** EHLO, STARTTLS, AUTH, and every IMAP command
  outside IDLE wait as long as the server takes. A server that accepts a
  connection and then never answers holds that send or lookup, and the
  outbox behind it, until the connection drops.
- **DMARC as the test of Tom's identity.** It holds only while Tom's
  domain publishes a DMARC policy and Gmail writes `Authentication-Results`.
  A record fails the test when either is missing, and then starts nothing.
  The `email.dmarc` guard row expires in ninety days; deleting it at expiry
  does not change the test, which stays in `core/intake.py` until a diff
  removes it.
- **Approving every send.** As on Telegram, each send to anyone but Tom
  waits for his tap, and the broker has no standing grants.
