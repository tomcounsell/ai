---
tracking: none
slug: m2-3-email
type: build
status: delivered-not-passed (record in m2-3-email-record.md)
critique_rounds: 1
review_rounds: 2
governance_grant: none (open question 17, the DMARC check, is parked: aec2bff7f stays unmerged)
---

# 2.3 in full: the email bridge, adapted from `main`

Task 2.3 of [valor-rebuild.md](valor-rebuild.md), milestone 2. It builds
`bridges/email/` to the bridge port in
[m2-1-port.md](m2-1-port.md), to the contract
in [bridges/email.md](../bridges/email.md), from the email code on `main`
(`bridge/email_bridge.py`, `bridge/email_relay.py`), read with
`git show origin/main:<path>` and never imported.

## Stakes

`critique_rounds: 1`, `review_rounds: 2`. The bridge sends mail to real
people under Valor's name. A mistake sends a message twice or sends bytes
Tom did not approve. Its kernel changes are small and named: settings, the
`email.send` declaration's usage text and size function, the reply-all
expansion, `broker.Failed` in `core/broker.py`, and the `core/bridge.py`
additions in the record. The DMARC test and its guard are parked (open
question 17) and are not part of this delivery.

## Done, as evidence

The milestone's Done items for email are four, all on Valor's real
mailbox during a test window. Each is first shown against local IMAP and
SMTP test servers, with a real Postgres and no mocks, so the window only
confirms what the provider does.

### Shown now, against local test servers

The local servers are Dovecot (IMAP, run as the agent's user from a
config written under the test's temporary directory, a password file of
test users, a folder flagged `\Sent`), reached over TLS through a small
terminator whose certificate a test CA signs, with a second terminator
whose certificate it did not sign; and a small asyncio SMTP server in
`tests/mailserver.py` (STARTTLS, AUTH, a recipient it can be told to refuse, a reading rate it can be told to
hold, a delay before each read of the body, hooks on connect and after
DATA, a delay it can put before the 250 reply, and
every accepted message appended to the sender's `\Sent` folder in
Dovecot, as Gmail files it). Ports come from `VALOR_TEST_PORTS` (a
build's assigned range, such as `6521-6529`) when set, else the OS.

| Done item | Evidence here |
|---|---|
| Parked (open question 17): a mail from Tom passing DMARC starts a task | Not built. A message from Tom's address delivered into the Dovecot inbox lands as one `message.received` row with `verified: false` and starts nothing, whatever its `Authentication-Results` say |
| A reply-all is held and released once | A task requests `email.send` with `reply_to` naming Tom's message (which had a `Cc`). The held effect's payload names the sender plus every `To` and `Cc` address minus Valor's own, a `Re:` subject, `In-Reply-To`, and the whole `References` chain. `core approve` then `core release` lead the bridge process to send it; the local server receives exactly one copy with the Message-ID derived from the broker key; a second `release` returns the recorded outcome and sends nothing |
| A crash between SMTP and outcome does not double-send | The bridge runs as a process of its own (`tests/email_child.py`) and the test kills it by its PID (SIGKILL). Killed once the message is stored, before the 250 reply: reconcile finds the message in `\Sent` by its Message-ID, writes `effect.outcome` `done` with `reconciled: true`, and the server holds one copy. Killed mid-body, or before the server's greeting: the server holds nothing and Sent Mail does not show it, so the effect stays in flight with no outcome (reconcile writes nothing). Each outcome is written once no process performs the send, with no wait |
| A 9 MB attachment sends | A 9 MB file sends through a server reading at 4 MB/s with no timer on any write, and the received attachment's sha256 equals the approved one. A server that stops reading mid-body: a stop shuts the connection down before the end of data line has gone, the send thread returns, and the server holds nothing (RFC 5321 4.1.1.4) |

### Waits for Tom's test window on Valor's real mailbox

| Done item | Evidence in the window |
|---|---|
| Parked (open question 17): a mail from Tom passing DMARC starts a task | Not tested in the window. A mail from Tom's address is received, recorded unverified, and starts nothing; a mail from another address of his is not received and stays unseen |
| A reply-all is held and released once | Valor's reply to that mail, which Tom cc'd to the second address 2.1's identity question names, waits for his tap; one tap, one copy in each inbox |
| A crash between SMTP and outcome does not double-send | During a 9 MB reply, the performing connection's backend is terminated by its pid; launchd restarts the bridge, a sweep settles the effect from Gmail's Sent Mail, and Tom's inbox holds at most one copy, with the outcome `done` exactly when it holds one |
| A 9 MB attachment sends | The reply above carries a 9 MB file that arrives intact |

The window also measures the Sent Mail search form that works, Gmail's
filing delay, the 9 MB upload rate, and the bridge's RSS.

## Threat model

Senders control every byte of a message except the headers the receiving
server prepends: `From`, ids, inner `Authentication-Results`, MIME,
charsets, sizes, filenames. A turn controls its `email.send` and its
workspace files, any time.

What the kernel must never do with any of it:

- Set `verified` on `From` alone. No email record is verified: the DMARC
  test that would verify one is parked (open question 17).
- Take an approval or a stop from mail. Mail is data, and steering.
- Send anything the broker did not release with Tom's unused approval
  bound to the payload digest. Tom's tap is on the full card, recipients
  included; reply-all computes those recipients in `core/` so a turn
  need not, but a turn can name its own, and Tom sees them.
- Send file bytes other than those whose sha256 Tom approved.
- Write an attachment outside `inbound_dir/email/`.
- Put the app password in a record, a row, a log line, an exception, or
  `os.environ`.

Accepted: a sender who knew a Message-ID before Tom used it could send it
first and shadow Tom's message at the receipt index. Gmail's ids are
random, so this needs the id before it exists.

## Port used

From [m2-1-port.md](m2-1-port.md), which wins where this plan differs:

- `core.intake`, module functions: async `receive(conn, inbound) ->
  Received`, async `recorded(conn, channel, chat_id, ids)`, and
  `owns(channel, id)`, `owned(channel)`; ownership on `sender_id`, the
  operator's addresses (`operator_email`, a tuple) owned by definition.
- The email record: `chat_id` is the thread root (the first `References`
  id, else the message's own `Message-ID`); `sender_id` is the `From`
  address lowercased; `message_id` is the `Message-ID` header;
  `reply_to` is `In-Reply-To`; `thread` is `list[dict]`, one `{"id"}` per
  `References` id, oldest first; `headers` is `dict[str, str |
  list[str]]` and carries the raw headers; `attachments` entries are
  `{name, mime, bytes, path}` or `{name, mime, bytes, skipped}`; `text`
  is the subject, a blank line, then the body, and that is the
  instruction. `verified` is set by `receive` in the row's payload, from
  the raw `From` and `Authentication-Results` headers.
- `core.bridge`: `Bridge` (`performers()` returning `(perform,
  lookup)`, `run(outbox)`), `ChannelLimits` (`int | None` fields),
  `Declared` (email's declares no settle time of its own), and
  `serve`, which holds `bridge:email:<machine>`, sets
  `application_name` to `valor-email` (`valor-email-perform` on the
  performing connection), and reconciles `broker.dangling` at start and
  on every `serve_tick_s` wake. Limits live in `core/bridge.py`, one
  entry per channel (D15c); email's carries a size function.
- `Outbox`: iterate, `perform(Release)`, never `broker.release`; email
  ignores `NoticeDue`, since `operator_channel` is Telegram.
- `email.send`: target the `to` list, lowercased, sorted, comma-joined;
  payload `to`, `cc`, `subject`, `body`, `in_reply_to`, `references`,
  `files: [{path, sha256}]`; refused at request time when the channel
  entry's size function, applied to the whole message, exceeds
  `max_file_bytes`, the protocol limit the reason (D15b, D15c).
- Results carrying `sent: [{channel, chat_id, message_id}]`; binding on
  `(channel, chat_id, message_id)`. Email binds only `answer`, `steer`,
  and `start`; an `In-Reply-To` matching no sent message is not a reply;
  a non-reply from the operator starts a task under the project listing
  the chat, otherwise under `valor` (decision 15).
- `lookup(action, key, since)`, `since` the intent's `at`, passed by
  `serve` on release and reconcile alike (D34a). `broker.Unknown` from
  either call: no outcome, the intent left for `reconcile`. Imports and
  settings names per port decisions 26 and 30.

## What is built

### `bridges/email/parse.py`: kept and adapted

Pure functions over raw bytes, plus the attachment write. No network.

| Function on `main` (`bridge/email_bridge.py`) | Here |
|---|---|
| `_decode_header_value` | Kept |
| `_extract_address`, `_extract_addresses` | Kept. A `From` with more than one address is reported as such, so `verified` can be false for it |
| `_extract_body` | Adapted: an unknown charset decodes as UTF-8 with replacement instead of raising `LookupError`; HTML-only mail is reduced to text with `html.parser`, dropping `script` and `style` and unescaping entities, in place of the tag regex |
| `_sanitize_attachment_filename`, `_is_attachment_part` | Kept |
| `_extract_attachment_metadata` | Adapted: no size or part-count cap (see "Left out"); a part that fails to decode is an entry with `skipped` and its reason, instead of a `truncated` flag |
| `_attachment_storage_key` | Adapted: the sha256 of the part's bytes, which names the file |
| `_persist_attachments` | Adapted: writes each part under `settings.inbound_dir/email/` named by its sha256 and keeping the sanitized extension; the vault mirror goes |
| `parse_email_message` | Adapted: keeps empty-body mail with no attachments; keeps mail with no `From` (sender empty, so never verified); reads `References`, `In-Reply-To`, `Date`, `Subject`, `To`, `Cc`, and the `Authentication-Results` headers in order; returns an `Inbound` with raw headers |
| `_public_attachment`, `_email_media_type`, `_attachment_descriptors`, `_body_references_attachments`, `_mirror_attachments_to_vault` | Not carried: descriptors are the port's `Attachment`, and guessing from the body that a file is missing is interpretation |

The record beyond the port's mapping: a message with no `Message-ID` gets
`sha256:<digest of its raw bytes>`, which survives a UIDVALIDITY change,
and is its own thread root. `sent_at` is the `Date` header, or the
server's `INTERNALDATE` when `Date` does not parse. `headers` carries
`subject` and `date` as strings, `from` and `authentication_results` as
lists of the raw header values as received, topmost first, `to` and `cc`
as address lists, and `uid` and `uidvalidity`.

### `bridges/email/imap.py`: the watch, adapted from `_poll_imap`

One held connection under `imaplib.IMAP4_SSL` with
`ssl.create_default_context()` (main passes no context, so certificates go
unverified there) and no socket timeout. On connecting, and after each
wait, the steps below run; then the connection waits in IDLE (RFC 2177)
until the server's `EXISTS` reports new mail, so mail arrives as an event.
RFC 3501 section 5.4 lets a server log out a client idle for 30 minutes,
so IDLE is ended and issued again every 29 minutes (`IDLE_REISSUE_S`, RFC
2177's advice), the only read bound while idling.

1. `SELECT INBOX`; read `UIDVALIDITY`.
2. `UID SEARCH UNSEEN SINCE <email_since>` with an `OR` tree of `FROM`
   terms over `intake.owned("email")` (main's `_build_imap_sender_query`,
   kept). `FROM` is a substring search, so each fetched message is kept
   only when `intake.owns("email", sender)` is true; others are left
   unseen.
3. Fetch the headers of every match (`BODY.PEEK[HEADER.FIELDS
   (MESSAGE-ID FROM REFERENCES)]`), and ask `intake.recorded(conn,
   "email", chat_id, ids)` per thread root which are already received.
   Those are marked `\Seen` without a body fetch, so a crash between
   receive and `\Seen` never downloads a 9 MB message twice.
4. For each other UID, oldest first: `UID FETCH (UID INTERNALDATE
   BODY.PEEK[])`, parse, persist attachments, `intake.receive(conn,
   inbound)`, and only after it returns, `UID STORE +FLAGS.SILENT
   (\Seen)`.

A duplicate is marked seen too. A message whose parse, persist, or
receive raises is logged with its UID and stays unseen, and the search goes
on, so one bad message never blocks later mail. A failed login, a failed
or dropped connection, is one line in the log, and the watch connects
again on the outbox's next wake (`Bridge.tick()`); there is no backoff,
alert key, or retry state. Main's batch cap of 20 goes.

### `bridges/email/smtp.py`: the performer

Adapted from `_build_reply_mime` and `_send_smtp` (`bridge/email_bridge.py`)
and `_send_smtp_sync` (`bridge/email_relay.py`).

- **Files.** `perform` reads each payload file once, hashes those bytes,
  and compares with the payload's `sha256` (the broker's digest binding,
  not a new check). A mismatch or a missing file raises before SMTP
  connects. The MIME is built from the bytes it hashed, so a file changed
  after Tom's tap never leaves.
- **MIME.** `EmailMessage`, a `text/plain` UTF-8 body, files as
  `multipart/mixed` parts. The subject is the payload's, unchanged (main's
  `force_reply_prefix` goes). `References` is the payload's whole chain.
  Headers added: `From` (`email_address`), `To`, `Cc`, `Date`, and
  `Message-ID` = `<valor.<first 32 hex of sha256(key)>@<Valor's domain>>`,
  so a retry of one effect repeats its id and two effects never share one.
- **Send.** `smtplib.SMTP`, `starttls` with the default context, login,
  `MAIL FROM` with `SIZE`, `RCPT` for each address, `DATA`, the
  dot-stuffed body, then the end of data line `.` on its own, sent apart.
  One attempt. No command has a timer: each waits until the server
  answers or a stop ends it, and the body goes in one `sendall`. A stop
  shuts the connection's socket down from the stopping side
  (`bridges/email/stop.py`, `Ends`), so the blocked thread returns; the
  same holds for every IMAP command, IDLE included. After the 250, `QUIT`
  is sent and its reply not awaited.
- **Refused or in doubt.** The server accepts a message only on the end
  of data line, with a 250, or refuses it (RFC 5321 section 4.1.1.4). So
  a refused login, every recipient refused, a refusal at `MAIL` (an
  over-`SIZE` message included), `RCPT`, or the reply to `DATA`'s start,
  and any error or failed write (EPIPE) before the end of data
  line has gone in full, raise a definite refusal (`broker.Failed`), and
  the effect is `failed` with no lookup. After it, a 4xx or 5xx reply is
  also definite (section 4.2.1). Anything else that ends the send (no
  reply because the connection closed, a garbled reply `smtplib` reads as code
  -1, a reply line too long to read, another code) raises
  `broker.Unknown`, naming the exception's `__context__`; the server may
  have stored the message (RFC 5321 section 6.1), so this is never
  `failed` on its own. Some
  recipients refused: `done`, with `refused` listing each address and its
  reply.
- **Result.** `message_id`, `accepted`, `refused`, and one `sent` entry
  whose `chat_id` is the thread root (the first `references` id, else the
  own Message-ID), so a reply from any recipient binds.
- **lookup.** One IMAP connection; plain `LIST "" "*"`, and the folder
  whose flags include `\Sent`; `UID SEARCH SINCE <the date of since,
  less one day> HEADER Message-ID <id>`, or, when the server advertises
  `X-GM-EXT-1`, `UID SEARCH X-GM-RAW "rfc822msgid:<id> after:<that
  date>"`; the day covers the clock margin and IMAP's date-only `SINCE`.
  The key-derived Message-ID needs no scan, so `intake.claimed` is not
  consulted. Found: the result rebuilt with `sent`. Not found, or a
  connection or login failure, raises `broker.Unknown`.
- **Settle time.** None. A 250 settles `done` in `perform`, and a
  definite refusal settles `failed`; neither reads Sent Mail. Only a send
  in doubt, or one whose process died, is settled from Sent Mail:
  reconcile runs on each outbox wake once the effect's performing lock is
  free, reads Sent Mail by Message-ID, and writes `done` when it is
  there. Gmail copies a message sent through SMTP into Sent Mail ("Choose
  your IMAP email client settings for Gmail", Gmail Help, answer 78892);
  no document gives the time that filing takes, so a miss is `Unknown`
  (the port's contract: the intent stays in flight and a later wake
  settles it), never `failed`, and no wait is added.
- **limits.** Email's entry in `core/bridge.py`'s channel table:
  `max_text` `None`; `max_message_bytes` 25,000,000, Gmail's limit on the
  whole encoded message ("Gmail sending limits in Google Workspace",
  Admin Help: maximum email size 25 MB); and the size function
  `message_bytes(action, sizes)`, which the request-time refusal calls
  with the sizes of the files the kernel measured unread in the task's
  workspace. It returns the length of the message `core/mail.py` builds
  from the payload with attachments of those sizes. The performer
  builds with the same function, which the bridge reaches through
  `core.bridge` (D30), so the two lengths are equal by construction. The window records
  the `SIZE` smtp.gmail.com advertises, which replaces 25,000,000 if it
  differs. `MAIL FROM` carries `SIZE`, so an oversize message is refused
  before the body, a definite `failed`.

Main's retries, dead letters, relay loop, drafter, history, and `react`
are not carried.

### `bridges/email/__init__.py` and `__main__.py`

`EmailBridge`: `channel = "email"`, `limits`, `performers()` returning
`{"email.send": (perform, lookup)}`, `tick()`, which lets a failed watch
reconnect, and `run(outbox)`: the watch on its own connection beside each `Release` and each
Sent Mail lookup, every one a task of its own on its own database connection,
one at a time per effect. Blocking IMAP and SMTP calls run in a worker thread
through `stop.in_thread` (`bridges/email/stop.py`), which a stop cancels by
shutting the connection down and waiting for the thread to return, each call on a thread of its own.
Tom's task stop reaches the SMTP call inside `EmailBridge.perform`, after the broker has written the
intent, and a Sent Mail lookup that is running (`EmailBridge.until_stopped` listens on `valor_stop` and
cancels it; only `perform` also reads an earlier `task.stopped` row; a listener that fails is not a stop: the call runs on, and the next outbox wake listens again and reads whether the task was stopped meanwhile), so a release for a stopped task is still
recorded refused by the broker. SIGTERM cancels the whole run (`bridges.email.serve`), so every blocked call is
ended. A stopped call writes no outcome and no notice: the effect stays in flight, and a later wake's lookup
settles it or, on a miss, sends `send_in_doubt`. `__main__` has three
verbs: `run` (`asyncio.run(serve(EmailBridge()))`), `keys` (the
credential copy below), and `--plist`, which prints the launchd job
(`KeepAlive`, logs under `settings.log_dir`) for Tom to load, as
`core backup --plist` does.

### `core/intake.py`: the DMARC test (parked, open question 17)

Not built. 2.1's `receive` sets `verified` false for every email record,
and the bridge never sets it. The raw `authentication_results` values are
recorded in `headers` and read by nothing.

### `core/mail.py`: reply-all and the message

`email_message(payload, blobs, message_id, sender, date)` builds the
MIME (below); `email_encoded_bytes(payload, sizes)` is the length of that
message built from the payload with attachments of the given sizes, with
an id and date of fixed width.

`reply_all(row, own)`, pure: `to` is the sender; `cc` is every `To` and
`Cc` address minus Valor's own and minus the sender, lowercased, in
order, without repeats; `subject` gains `Re: ` unless it starts with
`re:` in any case, and an empty one is `Re: (no subject)`; `in_reply_to`
is the row's `message_id`; `references` is the ids in the row's
`thread` plus its `message_id`. `core/session.py`'s request collection
calls it for an `email.send` that names `reply_to` (a received `received_id` or
`message_id`), merges in the turn's `body` and `files`, drops `reply_to`,
and passes the result to `request`, so Tom's approval covers the final
recipients. `email.send`'s usage text in `core/bridge.py` offers both
forms.

### The DMARC check (parked, open question 17)

Tom's answer: "yuda.me email is managed by google workspace. you decide".
Decided: not built, not ledgered, no guard seeded. The governance paragraph
needs an incident and no spoofed mail has arrived. The parked commit
(aec2bff7f, `m2-3-dmarc-parked`) is not part of this delivery; if spoofed
mail from Tom's address arrives, that is the incident and the commit comes
back for a grant.

### Settings and the credential

`core/settings.py` gains: `email_address` (Valor's), `email_since` (a
date), `imap_host`, `imap_port`
(993), `smtp_host`, `smtp_port` (587), and `mail_cafile` (unset; tests
point it at their CA). There is no poll interval and no SMTP or IMAP timeout. Tom's address is 2.1's `operator_email`.

`python -m bridges.email keys` copies `IMAP_USER`, `IMAP_PASSWORD`,
`SMTP_USER`, and `SMTP_PASSWORD` from the vault `.env` into `mail-keys`
in the kernel key directory (mode 600) through `credentials.copy_keys`,
printing each name with `written`, `kept`, or `missing`, never a value.
`credentials.read_key` takes the owning command, so its errors name
`python -m bridges.email keys` or `judgement-keys`. The bridge reads the
file at start; a missing name fails the start, naming it. The password is held in the
IMAP and SMTP config objects only.

## Left out

- Mail someone opens in Valor's webmail before the watch searches is not
  received. Marking it unread brings it in after the next wait.
- Mail from senders this machine does not own is never received, so
  replies to Valor's mail from anyone else are not recorded as thread
  context. email.md says so.
- Inbound attachment caps (main's 25 MiB and 50 parts): Gmail bounds
  inbound size, and a kernel cap has no incident.
- Operator notices, approvals, and stops by email; alerts,
  backoff, and a health key; subject coalescing, the vault mirror, the
  Redis history and dead letters, the relay's retries, per-sender
  project routing, the customer-service handler, the drafter, `gws`
  drafts, standing grants, and folders other than `INBOX`.

## Tests

Real Postgres (`VALOR_TEST_DB`), real Dovecot, the local SMTP server, no
mocks. Tests needing Dovecot or `openssl` fail naming what is missing.

**Parsing** (`tests/test_email_parse.py`, fixtures in
`tests/fixtures/mail/`):

- Empty body, no attachments: a record, `text` the subject and a blank
  line. Empty body with one attachment; attachment-only; no subject.
- A 9 MB attachment: saved, size and sha256 right. A 30 MB one and 60
  parts: all saved.
- Filenames `../../etc/passwd`, `.`, empty, and two different parts
  both `report.pdf`: all land in `inbound_dir/email/`, named by sha256,
  distinct, with the sanitized name in `name`.
- RFC 2047 subject and display name; an unknown charset; malformed MIME
  that `email` parses loosely; a part that fails to decode, listed in
  `skipped`.
- HTML-only mail with `<script>`, `<style>`, and `&amp;`.
- No `Message-ID`: `sha256:` id, the same on two parses. No `From`:
  recorded, sender empty, not verified.
- `References` with folded lines and stray text: `thread` in order,
  `chat_id` the first id; none: `chat_id` the own `Message-ID`.
- Unparseable `Date`: `sent_at` from `INTERNALDATE`.

**Identity**: a message with any `Authentication-Results` (a pass line,
a forged pass line below a fail, none at all) and `From` an owned address
lands as one row with `verified: false`, its raw `authentication_results`
in `headers`; no test of the DMARC function exists, since none is built.

**The bridge** (`tests/test_email_bridge.py`):

- One mail lands as one row, then is `\Seen`; a second search records
  nothing. Receive without the `\Seen` store (as a crash would leave
  it), then a full search: one row, no body fetch (`intake.recorded`),
  then seen.
- UIDVALIDITY change (`doveadm mailbox update --uid-validity`): mail
  received and seen before is not received again; mail received but not
  yet seen lands once after the change, with and without a `Message-ID`.
- Mail from an owned address with any `Authentication-Results`, forged
  or passing: one row, `verified: false`, no task.
- Mail from a sender not owned stays unseen and unrecorded, including
  `xtom@yuda.me`, which `FROM` matches as a substring. Unseen mail
  before `email_since` is not received.
- A message whose persist raises (the inbound directory made read-only
  for that one key): logged, left unseen, and the next message in the
  same search is received.
- Mail delivered while the watch is in IDLE is received; IDLE returns on
  `EXISTS`; a connection dropped under IDLE ends it; a watch that cannot
  connect connects on the next tick.
- A mail from Tom passing the test, in no chat a spec lists, starts one
  task under `valor`. Tom's reply to a mail Valor sent binds to that task
  as a steer, through the sent entry's thread root; a reply whose text is
  exactly `stop` or `approve` also binds as a steer.
- Reply-all: recipients, subject, threading as `reply_all` gives them;
  held; released once by the bridge process; one copy at the server; a
  second release returns the recorded outcome.
- Two identical replies in one task: two effects, ids, and copies.
- A file swapped in the workspace after approval, before release:
  nothing sent, the outcome `failed` or `effect.refused`, never `done`.
  A stopped task's held send: release refused, nothing sent.
- Crash between SMTP and outcome, the bridge process killed by PID:
  after the message is stored, `done` with `reconciled: true`, one copy;
  before the server took the body, nothing stored and the effect still
  in flight (Sent Mail does not hold it, which is `Unknown`).
- The connection closed after the message is stored and before the 250:
  the performer raises `Unknown`, and the lookup finds the message.
- Crash after the intent, before the server's greeting (a port that
  accepts and never greets): in flight, and nothing is sent.
- Lookup with the IMAP server down: `Unknown`, nothing written.
- 9 MB send through the held reading rate: done, attachment byte-equal.
  A server that stops reading mid-body: a definite refusal before the
  end of data line, nothing stored. A server that hangs up after `354`
  (EPIPE before the end of data line): a definite refusal, no lookup.
  After the end of data line: `554` is a definite refusal; a garbled
  reply, a `251`, or a line too long is `Unknown`.
- `message_bytes(action, sizes)` equals the length of the message the performer
  serializes, for no files, one, and three of odd sizes; a request just
  over 25,000,000 encoded is refused, one of exactly 25,000,000 is held.
- One recipient refused: `done` with it in `refused`. All refused:
  `failed`. Wrong password: `failed` with the server's reply.
- A server whose certificate the test CA did not sign: IMAP and SMTP
  both refuse to connect.
- With a known test password, after a run including a refused login, the
  password appears in no ledger row, log line, or exception text.

**Kernel**: `reply_all` cases: Valor's address in `To`, the sender also in `Cc`, repeats, `RE:`
subjects, an empty `References`.

**Window** (`tests/test_live_email.py`, run only with `VALOR_LIVE=1` and
the window's settings): the four Done items and the Sent Mail lookup on
Valor's mailbox, driven by the steps below.

## Files it changes

New:

- `bridges/email/__init__.py`, `__main__.py`, `config.py`, `parse.py`,
  `imap.py`, `smtp.py`
- `core/mail.py`; `tests/mailserver.py` (Dovecot and SMTP fixtures);
  `tests/test_` `email_parse`, `email_smtp`, `email_imap`,
  `mail`, `email_bridge`, and `live_email` (`.py`);
  `tests/fixtures/mail/*.eml`

Changed:

- `core/settings.py`, `core/credentials.py` (the key copy shared with
  `judgement-keys`, the owning command in errors)
- `core/session.py` (the reply-all call), and `core/bridge.py` (email's
  limits entry and `email.send`'s usage), all 2.1's; 2.1's fake bridges sit
  in `tests/bridges.py`
- Docs: `docs/bridges/email.md` (status, `verified` set in `core/`, the
  `sha256:` id, owned senders and `email_since`, no inbound caps, the
  key directory), `docs/machine.md` (the mail credentials row),
  `docs/tech-stack.md` (bridges' secrets; `imaplib` and `smtplib`
  chosen), `core/README.md`, `tests/README.md`,
  `docs/plans/rebuild-handoff.md` (Dovecot in setup)

`core/settings.py`, `core/intake.py`, `core/session.py`, and
`core/bridge.py` are shared with 2.1 and 2.2, so 2.3 merges after 2.1
and rebases before its checks.

## Rollout

The install steps, Tom's steps before the window, and the test window are in
[m2-3-email-rollout.md](m2-3-email-rollout.md).

## Questions for Tom

None. Tom's addresses and the cc'd second one are 2.1's identity
question; the password's place is machine.md's; no DMARC step is Tom's, since the check is parked.

## The record

What is decided by default, the critique round, and the build are
recorded in [m2-3-email-record.md](m2-3-email-record.md), with the tech debt this plan absorbs.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild-feedback.md, Tom's feedback of 2026-10-03).

Scope: the Delivery in m2-3-email-record.md, findings 1, 3, 4 and 5, and finding 2 as sends and Sent Mail lookups each run as their own task off the outbox loop.

Timers, Tom: "No timer; stop ends it". No value is set for EHLO, STARTTLS, AUTH, or any IMAP command; a hung connection holds only its own effect until a stop.

Open question 17, DMARC, Tom: "yuda.me email is managed by google workspace. you decide". Decided: refused for now. The governance paragraph needs an incident and none has happened (no spoofed mail). Commit aec2bff7f stays parked, unmerged; email starts, answers, and steers nothing, and Valor still sends by email. Tom has no DMARC or DKIM step before the window, and the window tests sends only. If spoofed mail from Tom's address ever arrives, that is the incident, and the commit comes back for a grant.
