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
| Email received and recorded, and sent as an approved `act` effect | Mission item 1: work arrives the way the people around Tom already send it |
| Each send's `Message-ID` and thread root recorded with its result | Constraint "Reliable stop, recovery, and correction": a send carries provenance in the ledger |
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

**The watch.** The bridge holds one IMAP connection and waits on it with
IDLE (RFC 2177), so new mail arrives as an event: the server's `EXISTS`
response ends the wait. RFC 3501 section 5.4 lets a server log out a client
idle for 30 minutes, so the bridge ends IDLE and issues it again every 29
minutes (`imap.IDLE_REISSUE_S`), as RFC 2177 advises. Gmail sends nothing
while a client idles (measured: no line in 11 minutes), so a path that drops
with no reset is silent to the client. A route monitor (`route -n monitor`,
`imap.network_changes`) reads the kernel's interface and address changes
(`RTM_IFINFO`, `RTM_NEWADDR`, `RTM_DELADDR`, from `net/route.h`). Most of
them do not touch the mail path (a container's bridge, `awdl0`, `utun`, a
temporary IPv6 address), so on each the monitor reads the interface that
carries the default route (`route -n get default`) and its `ifconfig` status
and addresses, and acts only when that differs from the last read. Then the
watch ends its connection and reconnects at once, and the new connection's
first search finds mail the old path hid. A reconnect the network started
that fails before IDLE is accepted waits for the next wake, whatever else
changes meanwhile. The monitor is not started again if it ends; a line is
logged and the re-issue is the only probe. Mail can still wait up to the
re-issue when the path dies with no change on this Mac; the 29 minutes is
RFC 2177's ceiling, and a shorter probe is a cost-benefit choice that is not
made. A transport failure (`OSError`, an IMAP abort) after the server
accepted IDLE also reconnects at once; a server that does not offer IDLE,
answers it `NO`, or ends the session as it begins waits for the next wake.
Mail the server announces during another command (a search or a fetch) is
not announced again in IDLE, so before idling the bridge reads those
responses and, when it finds one,
searches again instead of waiting. After each wait, and once on
connecting, the bridge:

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
on a ledger row), or at once in the two cases under The watch. Mail Gmail files as spam never reaches `INBOX`, and mail
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
the bridge hands each message to intake, which records it. Two things
differ for email, and together they mean no email reaches the judgement
layer.

**Who counts as Tom.** A `From` header is a claim anyone can write.
`intake.receive` sets every email record's `verified` false, so a record
from Tom's address is recorded and starts nothing. The bridge never sets
`verified`.

**Nothing binds.** Every email record is unverified, so intake binds it as
`none`: mail from Tom is recorded in the ledger and does not start, answer,
steer, approve, or stop anything. Approvals and stops come through the
operator channel (Telegram, or the local chat page) or the command line, which an email reply could not carry reliably anyway
(quoted history and signatures surround what the person typed).

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
each file's bytes. `subject`, `body` and `in_reply_to` are each absent,
null, or a string, and `to`, `cc` and `references` each absent, null, or a
list of strings; any other shape is refused with "subject, body and
in_reply_to must each be a string or null, and to, cc and references lists
of strings". `files` is absent, null, or a list of objects each with
a string `path` and a string `sha256`; any other shape is refused with
"files must be a list of {path, sha256} objects". Each path is absolute and
inside the task's workspace, sized there by the kernel without reading it,
and one that is missing, a link, or outside the workspace is refused with
one answer. `perform` reads each file once and compares its sha256
with the payload's; a mismatch or a missing file fails before SMTP
connects, so a file changed after Tom's tap never leaves. The message is
built by `core/mail.py`'s `email_message`, which the bridge reaches through
`core.bridge`: a UTF-8 `text/plain` body, files as `multipart/mixed`
parts (each file's own bytes in base64; a `message/*` type, such as
`.eml`, goes as `application/octet-stream`, since `message/rfc822` may not be
base64 and would need its lines rewritten), the subject and `References`
chain unchanged, and only the headers
transport requires added: `From` (Valor's address), `Date`, and
`Message-ID`.

**Size.** Gmail refuses a message over 25 MB encoded. Email's entry in
`core/bridge.py`'s `LIMITS` has `max_message_bytes` 25,000,000 and
`message_bytes(action, sizes)`, `mail.email_encoded_bytes`: the length of
the message `email_message` builds, each attachment base64 encoded, from
the sizes the kernel took without reading the files. A send over
25,000,000 bytes is refused at request time with that limit as the
reason, so Tom never approves an impossible send. The performer builds
with the same builder, so the size measured is the size sent. `MAIL FROM`
carries `SIZE` when the server advertises it.

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
The result's `sent` entry carries the `Message-ID` and the thread root.
No email reply binds to a task; every one is recorded with binding
`none`.

**One attempt.** `perform` submits once over SMTP with STARTTLS. No command
has a timer: the greeting, EHLO, STARTTLS, AUTH, `MAIL`, `RCPT`, `DATA`,
every write of the body, and the final reply each wait until the server
answers or a stop ends the connection. A stop shuts the connection's
socket down from the stopping side, which returns the blocked read or
write; the thread that was waiting ends, and nothing is left behind. A stop
is Tom stopping the send's task, a launchd SIGTERM, or any other end of the
bridge; each call runs on a thread of its own, so no call waits behind
another. A large message takes the time its size takes. A
server accepts a message only on the end of data line (RFC 5321 section
4.1.1.4). Before that line has gone out in full, any end (a refused
connection or login, a refused `MAIL`, every recipient refused, a refused
`DATA`, or a write that fails) is definite: the outcome is `failed`
with the reason, and Sent Mail is not read. A stop before that line went is the same: nothing was sent, so the
outcome is `failed`, with no Sent Mail read and no notice. A stop after the
line writes no outcome: the send stays in flight, as after a kill, and the
next wake reads Sent Mail for it. After it, a 250 is `done`, and
a 4xx or 5xx reply is `failed` (section 4.2.1: the action did not occur).
Any other end (no reply because the connection closed, a garbled reply, a reply
line too long to read, another code) raises `broker.Unknown`: the server
may have stored the message. The intent stays in flight with no outcome, and
a notice goes to Tom at once, once per send, saying it is in doubt. The send
is settled on the next wake (every `serve_tick_s` or ledger row, not at once)
by reading Sent Mail for its Message-ID once no process performs it: found is
`done`; not found leaves it in flight for the next wake, and `tasks.audit`
lists it. A send cut off by a restart before it was settled gets the same
notice on the first wake after. After the 250,
`QUIT` is sent and its reply is not awaited, since the send is already
done. When the server accepts
the message for some recipients and refuses others, the outcome is `done`
with `refused` listing each address and its reply; reaching them is a new
request and a new approval. The bridge keeps no retry loop, backoff
schedule, or dead-letter queue.

**Operator notices.** Notices go to Tom's operator channel, Telegram or the
local chat page. The
email bridge performs the outbox's releases and ignores its notices.

## Stop and recovery

A stopped task's held sends stay held, and `broker.release` refuses a
release for it on the `task.stopped` fence and records it refused. The
bridge ends a call only around its own server call: an SMTP send, after the
broker has written the intent, and a Sent Mail lookup. When Tom stops the
task, a send blocked on its server ends: its connection is shut down. A send
that had not yet sent the end of data line is settled `failed` with no
notice, since the server cannot have taken it. A send that had is written
no outcome and no notice, and what Tom sees after his own stop is nothing at
that moment. It stays in flight, and each later wake asks
Sent Mail for it (a lookup for a task stopped earlier runs in full, and only
a stop that arrives while it runs ends it). Sent Mail holding it settles it
`done`, silently. A miss sends the one `send_in_doubt` notice, which says
the email may or may not have gone. A failure of the stop listener is not a stop: the call
runs on, and a listener that dropped is set up again on the next wake, so
Tom's stop still ends a send or lookup blocked on its server. The new
listener also reads at once whether the task was stopped while none was
listening. A lookup restarted this way for an already stopped task ends
with no outcome and runs again, whole, on the next wake. SIGTERM
ends every call the same way before the process exits, with status 1 for
launchd to restart it. A bridge killed at any point loses nothing: an unseen
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
  telling Tom is `core/`'s job. The one notice it writes is `send_in_doubt`
  (`EmailBridge.in_doubt`), a `notice.requested` row for a send that may or
  may not have gone, which the operator channel's outbox delivers.

## The implementation

| Module | Holds |
|---|---|
| `bridges/email/__init__.py` | `EmailBridge`: `channel`, `limits`, `performers()` giving `email.send`'s `(perform, lookup)`, and `run(outbox)`, the IMAP watch beside each released send and each Sent Mail lookup as a task of its own, reconnected on `tick()` |
| `bridges/email/__main__.py` | The verbs `run`, `keys`, and `--plist` (`KeepAlive`, logs in `log_dir/email.log`); `run` logs at INFO, each line `time level message` |
| `bridges/email/config.py` | `Config`, from settings and `mail-keys`; the passwords are held here only |
| `bridges/email/parse.py` | Raw mail to a record's fields, and attachments to files |
| `bridges/email/imap.py` | The watch: search, receive, and IDLE (`IDLE_REISSUE_S`) |
| `bridges/email/smtp.py` | `perform` and `lookup` |
| `bridges/email/stop.py` | `Ends`: a stop shuts a blocked connection down so its thread returns |
| `core/mail.py` | `reply_all`, the message builder, and its encoded size |
| `core/session.py` | A `reply_to` request filled in as reply-all |

The bridge imports only `core.bridge`, `core.intake`, `core.broker`,
`core.settings`, `core.db`, `core.credentials`, `core.notices`, and
`core.tasks`, and nothing from the Telegram bridge. Its tests run against Dovecot and a local SMTP server
(`tests/mailserver.py`) with a real Postgres and no mocks.

## Gaps

- **The sent folder.** `lookup` relies on Gmail filing mail submitted over
  SMTP into the folder flagged `\Sent`. A send in doubt that is never
  filed (a send the server never stored, or a provider that does not file
  sent mail) stays in flight with no outcome, and each outbox wake reads
  Sent Mail for it again over a new IMAP connection.
- **Waits with no timer.** Every SMTP and IMAP command waits as long as the
  server takes, and IDLE waits for new mail, a network change, or its re-issue. A stop ends a
  blocked read by shutting the socket down (`stop.Ends`); the thread
  returns before the connection is closed. Each send and each Sent
  Mail lookup runs as its own task on its own database connection, so a
  server that accepts a connection and then never answers holds only that
  effect until the connection drops or a stop (Tom's task stop or SIGTERM) ends it; the outbox,
  the watch, and every other send go on.
- **Email starts, answers, and steers nothing.** No email record is
  verified, so mail from Tom is recorded and binds as `none`.
- **Approving every send.** As on Telegram, each send to anyone but Tom
  waits for his tap, and the broker has no standing grants.
