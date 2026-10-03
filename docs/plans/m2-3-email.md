---
tracking: none
slug: m2-3-email
type: build
status: planned
critique_rounds: 1
review_rounds: 2
governance_grant: open question 17 (Tom, 2026-10-01), the DMARC verified check in core/intake.py
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
people under Valor's name, and decides which mail is verified as Tom's. A
mistake sends a message twice, sends bytes Tom did not approve, or lets a
forged `From` start work. Its kernel changes are small and named: one
guard seed, settings, the DMARC test in `core/intake.py`, the
`email.send` declaration's usage text and settle time, and the reply-all
expansion.

## Done, as evidence

The milestone's Done items for email are four, all on Valor's real
mailbox during a test window. Each is first shown against local IMAP and
SMTP test servers, with a real Postgres and no mocks, so the window only
confirms what the provider does.

### Shown now, against local test servers

The local servers are Dovecot (IMAP, run as the agent's user from a
config written under the test's temporary directory, a self-signed test
CA, a password file of test users, a folder flagged `\Sent`) and a small
SMTP server in `tests/mailserver.py` built on `aiosmtpd` (STARTTLS, AUTH,
a recipient it can be told to refuse, a reading rate it can be told to
hold, a hook after DATA, a delay it can put before the 250 reply, and
every accepted message appended to the sender's `\Sent` folder in
Dovecot, as Gmail files it). Ports are free ones the OS gives each run.

| Done item | Evidence here |
|---|---|
| A mail from Tom passing DMARC starts a task | A message delivered into the Dovecot inbox with the headers Gmail writes (topmost `Authentication-Results: mx.google.com; ... dmarc=pass ... header.from=<Tom's domain>`) and `From` Tom's address lands as one `message.received` row with `verified: true` and starts one task, its metered spending shown in `status`. The forgeries in "Tests" that reach the inbox from an owned address land as one row each with `verified: false` and start nothing |
| A reply-all is held and released once | A task requests `email.send` with `reply_to` naming Tom's message (which had a `Cc`). The held effect's payload names the sender plus every `To` and `Cc` address minus Valor's own, a `Re:` subject, `In-Reply-To`, and the whole `References` chain. `core approve` then `core release` lead the bridge process to send it; the local server receives exactly one copy with the Message-ID derived from the broker key; a second `release` returns the recorded outcome and sends nothing |
| A crash between SMTP and outcome does not double-send | The exactly-one case: the after-DATA hook kills the bridge process by its PID once the message is stored, before the 250 reply; the restarted bridge's sweep finds the message in `\Sent` by its Message-ID, writes `effect.outcome` `done` with `reconciled: true`, and the server holds one copy. The upload case: the test terminates the performing connection's backend (its pid from `pg_stat_activity` where `application_name = 'valor-email-perform'`) while a 9 MB upload is in flight; after restart and settle, the server holds at most one copy, and the outcome is `done` exactly when it holds one |
| A 9 MB attachment sends | A 9 MB file sends through a server reading at 1 MB/s with `smtp_timeout_s` set to 2 s for the test: the deadline, scaled by the encoded length, lets it finish, and the received attachment's sha256 equals the approved one. The same send with the deadline unscaled times out mid-body: the performer raises `broker.Unknown` naming the timeout, no outcome is written, and the next sweep after `settle_after_s` writes `failed`, since the server stored nothing |

### Waits for Tom's test window on Valor's real mailbox

| Done item | Evidence in the window |
|---|---|
| A mail from Tom passing DMARC starts a task | Tom sends a request from his address; its topmost `Authentication-Results` is from `mx.google.com` with `dmarc=pass`; one task starts. A mail from another address of his is not received and stays unseen |
| A reply-all is held and released once | Valor's reply to that mail, which Tom cc'd to the second address 2.1's identity question names, waits for his tap; one tap, one copy in each inbox |
| A crash between SMTP and outcome does not double-send | During a 9 MB reply, the performing connection's backend is terminated by its pid; launchd restarts the bridge, a sweep settles the effect from Gmail's Sent Mail, and Tom's inbox holds at most one copy, with the outcome `done` exactly when it holds one |
| A 9 MB attachment sends | The reply above carries a 9 MB file that arrives intact |

The window also measures the Sent Mail search form that works, Gmail's
filing delay, the 9 MB upload rate, and the bridge's RSS.

## Threat model

Senders control every byte of an inbound message except the headers the
receiving server prepends: `From`, the ids, any `Authentication-Results`
inside, MIME structure, charsets, sizes, and filenames. A turn controls
the `email.send` it requests and every file in its workspace, any time.

What the kernel must never do with any of it:

- Set `verified` on `From` alone. Only the topmost
  `Authentication-Results`, with the configured authserv-id, `dmarc=pass`,
  and `header.from` the domain of a single `From`, verifies; lines below
  it are the sender's.
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
  `Declared` whose `settle_after_s` may be a function of the action
  (D37), and `serve`, which holds `bridge:email:<machine>`, sets `application_name` to `valor-email`
  (`valor-email-perform` on the performing connection), and reconciles
  `broker.dangling` at start and on every `serve_tick_s` wake.
- `Outbox`: iterate, `perform(Release)`, never `broker.release`; email
  ignores `NoticeDue`, since `operator_channel` is Telegram.
- `email.send`: target the `to` list, lowercased, sorted, comma-joined;
  payload `to`, `cc`, `subject`, `body`, `in_reply_to`, `references`,
  `files: [{path, sha256}]`; refused at request time when the encoded
  message exceeds `max_file_bytes`, the protocol limit the reason (15b).
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

### `bridges/email/imap.py`: the poll, adapted from `_poll_imap`

One connection per poll, every `email_poll_s` seconds (30, main's value),
under `imaplib.IMAP4_SSL` with `ssl.create_default_context()` (main
passes no context, so certificates go unverified there) and a socket
timeout of `imap_timeout_s` (30, main's `IMAP_SOCKET_TIMEOUT`): a hung
server ends one poll.

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

A duplicate `receive` reports is marked seen too. A message whose parse, persist, or receive
raises is logged with its UID and stays unseen, and the poll goes on to
the next UID, so one bad message never blocks later mail. A failed login
or connection is one failed poll in the log, and the next poll runs on
schedule; there is no backoff, alert key, or retry state. Main's batch
cap of 20 goes.

### `bridges/email/smtp.py`: the performer

Adapted from `_build_reply_mime` and `_send_smtp` (`bridge/email_bridge.py`)
and `_send_smtp_sync` (`bridge/email_relay.py`).

- **Files.** `perform` reads each payload file once into memory, hashes
  those bytes, and compares with the payload's `sha256` (the broker's
  digest binding, not a new check). A mismatch or a missing file raises
  before SMTP connects, and nothing is sent. The MIME
  is built from the bytes it hashed, so a file changed between Tom's tap
  and the send never leaves.
- **MIME.** `EmailMessage`, a `text/plain` UTF-8 body, files as
  `multipart/mixed` parts. The subject is the payload's, unchanged (main's
  `force_reply_prefix` goes). `References` is the payload's whole chain.
  Headers added: `From` (`email_address`), `To`, `Cc`, `Date`, and
  `Message-ID` = `<valor.<first 32 hex of sha256(key)>@<Valor's domain>>`,
  so a retry of one effect repeats its id and two effects never share one.
- **Send.** `smtplib.SMTP` with `smtp_timeout_s` (30, main's `smtp_s`),
  `starttls` with the default context, login, then the socket's timeout
  set to the deadline, `smtp_timeout_s + encoded_bytes /
  smtp_floor_bytes_per_s`, before `send_message`. `encoded_bytes` is the
  length of the serialized message, base64 included, the unit #3601
  measured in. One attempt.
- **Refused or in doubt.** A refused login, every recipient refused, or a
  refusal of the message at `MAIL` (an over-`SIZE` message included),
  `RCPT`, or the reply to `DATA`'s start raise with the server's reply,
  and the effect is `failed`. Once the body has started to go, anything
  that ends the send without a final reply raises `broker.Unknown` and
  the effect stays open for `reconcile`: the deadline firing mid-body, a
  lost connection, or a timeout waiting for the reply after the body.
  The server may have stored the message, so none of these is `failed`.
  smtplib reports a timeout as `SMTPServerDisconnected`; the raised
  `Unknown` includes the exception's `__context__`, so the timeout is
  named. Some recipients refused: `done`, with `refused` listing each
  address and its reply.
- **Result.** `message_id`, `accepted`, `refused`, and one `sent` entry
  whose `chat_id` is the thread root (the first `references` id, else the
  own Message-ID), so a reply from any recipient binds.
- **lookup.** One IMAP connection; plain `LIST "" "*"`, and the folder
  whose flags include `\Sent`; `UID SEARCH SINCE <the date of since,
  less one day> HEADER Message-ID <id>`, or, when the server advertises
  `X-GM-EXT-1`, `UID SEARCH X-GM-RAW "rfc822msgid:<id> after:<that
  date>"`; the day covers the clock margin and IMAP's date-only `SINCE`.
  The key-derived Message-ID needs no scan, so `intake.claimed` is not
  consulted. Found: the result rebuilt with
  `sent`. Not found: `None`. A connection or login failure raises
  `broker.Unknown`.
- **settle_after_s**, on `email.send`'s `Declared` in `core/bridge.py`,
  a function of the action (D37): twice the action's deadline, from
  `email_encoded_bytes`, plus `email_sent_settle_s`, the time Gmail takes
  to file a sent message. A text reply waits about 3 minutes, a 25 MB
  message about 20. `email_sent_settle_s` is 120 until the window
  measures it. The wait only delays reading a missing message as never
  sent; it refuses nothing.
- **limits.** `max_text` is `None`; `max_file_bytes` is 25,000,000,
  Gmail's limit on the whole encoded message ("Gmail sending limits in
  Google Workspace", Admin Help: maximum email size 25 MB), compared
  with `email_encoded_bytes(payload)` in `core/bridge.py` (body, MIME
  headers, files base64 at 76 characters a line plus CRLF), which the
  request-time refusal and `settle_after_s` both use. The window records
  the `SIZE` smtp.gmail.com advertises, which replaces 25,000,000 if it
  differs. `MAIL FROM` carries `SIZE`, so an oversize message is refused
  before the body, a definite `failed`.

`smtp_floor_bytes_per_s` is 50,000, a provisional value below #3601's
measured rate (9.0 MB in 45.6 s); the window's measured 9 MB rate
replaces it. A lower floor only lengthens the deadline, so it never
refuses a send.

Main's retry counters, dead letters, relay loop, drafter, history cache,
and `react` are not carried.

### `bridges/email/__init__.py` and `__main__.py`

`EmailBridge` with `channel = "email"`, `limits`, `performers()`
returning `{"email.send": (perform, lookup)}`, and `run(outbox)`, which
runs the poll (`intake`'s functions on its own connection) beside `async
for item in outbox: await outbox.perform(item)` for each `Release`. The blocking IMAP and
SMTP calls run in a thread (`asyncio.to_thread`). `__main__` has three
verbs: `run` (`asyncio.run(bridge.serve(EmailBridge()))`), `keys` (the
credential copy below), and `--plist`, which prints the launchd job
(`KeepAlive`, logs under `settings.log_dir`) for Tom to load, as
`core backup --plist` does.

### `core/intake.py`: the DMARC test

`dmarc_verified(headers, authserv_id)`, pure, the test in the threat
model: true only when `headers["from"]` is one header holding one
address, the first entry of `headers["authentication_results"]` has
authserv-id `email_authserv_id`, a `dmarc=pass` result, and
`header.from` equal to that address's domain, compared without case. `receive` calls it for an
email record and sets `verified`; the bridge never sets it.

### `core/mail.py`: reply-all

`reply_all(row, own)`, pure: `to` is the sender; `cc` is every `To` and
`Cc` address minus Valor's own and minus the sender, lowercased, in
order, without repeats; `subject` gains `Re: ` unless it starts with
`re:` in any case, and an empty one is `Re: (no subject)`; `in_reply_to`
is the row's `message_id`; `references` is the ids in the row's
`thread` plus its `message_id`. `core/session.py`'s request collection calls it for an
`email.send` that names `reply_to` (a received `received_id` or
`message_id`), merges in the turn's `body` and `files`, drops `reply_to`,
and passes the result to `request`, so Tom's approval covers the final
recipients. `email.send`'s usage text in `core/bridge.py` offers both
forms.

### The DMARC check, ledgered

The `verified` test is governance-shaped: it decides whose mail carries
Tom's authority. Tom's default answer to open question 17 (A, standing
from 2026-10-01) grants it as an approved check, so the 2.3 Brief carries
`governance_grant` citing that answer, and the merge tap is Tom's
approval for this instance. The code is `intake.dmarc_verified` in
`core/intake.py` (port decision 11). valor-rebuild.md says it is ledgered
the way the four pipeline guards are, so `core/guards.py` seeds a fifth
guard on the `guards` stream, once, through `migrate`:

| Field | Value |
|---|---|
| `guard_id` | `email.dmarc` (`GUARD_DMARC` in `core/guards.py`: nothing in `core/machine.py` fires it) |
| `name` | the DMARC test: an email record is verified only when the receiving server's topmost `Authentication-Results` shows DMARC pass for its single `From` address's domain |
| `incident` | the risk, stated as it stands: a spoofed `From` reaching the kernel as Tom and carrying his authority. main's email bridge routes on an unauthenticated `From`, with no SPF, DKIM, or DMARC test (`docs/features/context-recall-advisory.md` on `main`, line 82), and #2694's review found that address reaching a shell interpolation, mitigated by quoting. There is no record of a spoofed mail having arrived yet |
| `mission_items` | `[6]`: without it, open question 17's option B asks Tom for a Telegram confirmation on every emailed request |
| `source` | `docs/plans/rebuild-open-questions.md, 17; docs/bridges/email.md, Who counts as Tom` |
| `granted_at` | 2026-10-01 |
| `expires` | 2026-12-30 |
| `note` | serves the constraint "Bounded authority, metered spending": a forged `From` starts nothing |
| `provenance` | `by: tom`, `via: open question 17, default A standing, seeded by migrate`, `role_played: false` |

`seeded_payload` takes the guard's own `note` and `provenance.via` when
the guard carries them, and the pipeline values otherwise. It fires when
a record from `operator_email` has `verified: false`, read from the
`message.received` row, so no new row type is added. Once DMARC is
published it fires only on a forgery or a misconfiguration, so the
expiry sweep will likely offer to delete it; deleted, no email record is
verified and email starts nothing. That deletion is Tom's tap, and the
window notes tell him so.

### Settings and the credential

`core/settings.py` gains: `email_address` (Valor's), `email_since` (a
date), `email_authserv_id` (`mx.google.com`), `imap_host`, `imap_port`
(993), `smtp_host`, `smtp_port` (587), `email_poll_s` (30),
`imap_timeout_s` (30), `smtp_timeout_s` (30), `smtp_floor_bytes_per_s`
(50,000), `email_sent_settle_s` (120), and `mail_cafile` (unset; tests
point it at their CA). Tom's address is 2.1's `operator_email`.

`python -m bridges.email keys` copies `IMAP_USER`, `IMAP_PASSWORD`,
`SMTP_USER`, and `SMTP_PASSWORD` from the vault `.env` into `mail-keys`
in the kernel key directory (mode 600), through the copy in
`core/credentials.py` that `judgement-keys` uses, printing each name with
`written`, `kept`, or `missing`, never a value. `credentials.read_key`
takes the command that owns the file, so its errors name
`python -m bridges.email keys` or `judgement-keys` as fits. The bridge reads the file when it starts; a
missing name fails the start, naming it. The password is held in the
IMAP and SMTP config objects only.

## Tech debt absorbed

- #3601: the SMTP timeout bounds the whole upload on `main`, so large
  attachments fail; the deadline scales with the encoded size. The other
  three items in #3601 go with the code not carried.
- #3124 and #2160: moot. Sends are held for Tom and carry exactly what he
  approved; steering is in `core/`.
- On `main`: unverified certificates, `_extract_body` raising on an
  unknown charset, the HTML regex, dropped empty-body and no-`From`
  mail, and `\Seen` set before the fetch; each is fixed above.
- Docs placing the bridges' secrets in the Keychain: they go in the
  kernel key directory, for the reason machine.md gives.

## Left out

- Mail someone opens in Valor's webmail before a poll is not received.
  Marking it unread brings it in on the next poll.
- Mail from senders this machine does not own is never received, so
  replies to Valor's mail from anyone else are not recorded as thread
  context. email.md says so.
- Inbound attachment caps (main's 25 MiB and 50 parts): Gmail bounds
  inbound size, and a kernel cap has no incident.
- Operator notices, approvals, and stops by email; IMAP IDLE; alerts on
  a failing login, backoff, and a health key. Subject coalescing, the vault mirror, the Redis history and dead
  letters, the relay's retries, per-sender project routing, the
  customer-service handler, the drafter, `gws` drafts, standing grants,
  and folders other than `INBOX`.

## Tests

Real Postgres (`VALOR_TEST_DB`), real Dovecot, the local SMTP server, no
mocks. Tests needing Dovecot or `aiosmtpd` fail naming what is missing.

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

**Identity** (`tests/test_intake_dmarc.py`), `intake.dmarc_verified` is true only
for the first case:

- Topmost AR from `mx.google.com`, `dmarc=pass`, `header.from` the From
  domain, a single `From` in any case.
- The same with `dmarc=fail`, `dmarc=none`, or no `dmarc` result.
- A forged pass line below a real topmost fail.
- A pass line whose authserv-id is anything else, alone or topmost.
- `header.from` another domain than the `From`.
- Two `From` addresses; display-name tricks such as
  `"tom@yuda.me" <other@x>`.
- No `Authentication-Results` at all.

**The bridge** (`tests/test_email_bridge.py`):

- One mail lands as one row, then is `\Seen`; a second poll records
  nothing. Receive without the `\Seen` store (as a crash would leave
  it), then a full poll: one row, no body fetch (`intake.recorded`),
  then seen.
- UIDVALIDITY change (`doveadm mailbox update --uid-validity`): mail
  received and seen before is not received again; mail received but not
  yet seen lands once after the change, with and without a `Message-ID`.
- Mail from an owned address with a forged or failing
  `Authentication-Results` (each identity case above delivered into the
  inbox): one row, `verified: false`, no task, and the guard's firing
  readable from that row.
- Mail from a sender not owned stays unseen and unrecorded, including
  `xtom@yuda.me`, which `FROM` matches as a substring. Unseen mail
  before `email_since` is not received.
- A message whose persist raises (the inbound directory made read-only
  for that one key): logged, left unseen, and the next message in the
  same poll is received.
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
- Crash between SMTP and outcome: both cases in Done. The upload case
  asserts the invariant (at most one copy; `done` exactly when one).
- The 250 reply delayed past the deadline, the message stored: the
  performer raises `Unknown`, no outcome is written, and the next sweep
  after `settle_after_s` writes `done` with `reconciled: true`, never
  `failed`.
- Crash after the intent, before SMTP connects (killed by PID from a hook
  on connect): a sweep after `settle_after_s` (set short in the test)
  writes `failed`, and nothing is sent.
- Lookup with the IMAP server down: `Unknown`, nothing written.
- 9 MB send through the held reading rate: done, attachment byte-equal.
  Unscaled, the deadline fires mid-body: `Unknown` naming the timeout, no
  outcome at once, `failed` from the sweep after `settle_after_s`.
- `email_encoded_bytes` equals the length of the message the performer
  serializes, for no files, one, and three of odd sizes; a request just
  over 25,000,000 encoded is refused, one just under is held.
- One recipient refused: `done` with it in `refused`. All refused:
  `failed`. Wrong password: `failed` with the server's reply.
- A server whose certificate the test CA did not sign: IMAP and SMTP
  both refuse to connect.
- With a known test password, after a run including a refused login, the
  password appears in no ledger row, log line, or exception text.

**Kernel**: `migrate` on a ledger holding the four guards adds
`email.dmarc` once, with its own `note`, `via`, and `source`; a second
`migrate` adds nothing; the four keep the pipeline values. `reply_all`
cases: Valor's address in `To`, the sender also in `Cc`, repeats, `RE:`
subjects, an empty `References`.

**Window** (`tests/test_live_email.py`, run only with `VALOR_LIVE=1` and
the window's settings): the four Done items and the Sent Mail lookup on
Valor's mailbox, driven by the steps below.

## Files it changes

New:

- `bridges/email/__init__.py`, `__main__.py`, `parse.py`, `imap.py`,
  `smtp.py`
- `core/mail.py`
- `tests/mailserver.py` (Dovecot and SMTP fixtures),
  `tests/test_email_parse.py`, `tests/test_intake_dmarc.py`,
  `tests/test_email_bridge.py`, `tests/test_live_email.py`,
  `tests/fixtures/mail/*.eml`

Changed:

- `core/settings.py`, `core/guards.py` (the fifth guard and per-guard
  `note` and `via`), `core/credentials.py` (the key copy shared with
  `judgement-keys`, the owning command in errors)
- `core/intake.py` (`dmarc_verified`, called at receive),
  `core/session.py` (the reply-all call), and `core/bridge.py`
  (`email.send`'s usage text and `settle_after_s`), all 2.1's
- `pyproject.toml` (`aiosmtpd` in the dev group)
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

1. `python -m core backup`. Merge; on the build Mac, pull and
   `python -m core migrate` (seeds `email.dmarc`).
2. `brew install dovecot` on any machine that runs the suite.
3. `python -m bridges.email keys`; set `email_address` and `email_since`
   (the window's date) in the bridge's launchd environment, and confirm
   `operator_email` is set.
4. Install the plist from `python -m bridges.email --plist`, unloaded.

Before the window, Tom's steps (the builder never logs in to the
mailbox):

1. Tom opens "Show original" on one mail he sent to Valor's address and
   reports its topmost `Authentication-Results` line. Both addresses are
   on one Google Workspace domain, so this shows whether such mail
   carries a `dmarc=` result at all. If it carries none even after step
   2, how email proves Tom is an identity question for him, and the
   window waits.
2. Tom publishes a DMARC record for `yuda.me` (none exists today; for
   example `v=DMARC1; p=none`), and the Google DKIM key from the Admin
   console as `google._domainkey.yuda.me`, a second aligned path besides
   SPF. `dig +short TXT _dmarc.yuda.me` shows the record.

The test window, with Tom:

1. Disable the email bridge on `main` on the build Mac (`email-disable`)
   and on the Mac whose `projects.json` lists Tom's address (Valor the
   Captain, project cuttlefish today), so no other poller marks his mail
   seen. Load the new bridge.
2. Tom sends a request from his address with a `Cc` to his second
   address: one task starts, its spending shown.
3. Tom sends one mail from another address of his: it is not received
   and stays unseen.
4. The task's reply carrying a 9 MB file is held; Tom taps once; during
   the upload, terminate the `valor-email-perform` backend by its pid.
   The bridge restarts, a sweep settles the effect, and each inbox holds
   at most one copy, with `done` exactly when it holds one.
5. Record the Sent Mail lookup form that works, Gmail's filing delay
   (it replaces `email_sent_settle_s`), the 9 MB upload rate (it replaces
   `smtp_floor_bytes_per_s`), and the bridge's RSS. Note for Tom that the
   `email.dmarc` guard rarely fires and its expiry tap decides whether
   email can start work.
6. Unload the new bridge and enable the email bridge on `main` on both
   Macs. Mail in the window belongs to the new system and is not
   replayed; mail between windows stays with `main` because
   `email_since` moves to each window's date.

## Decided by default

- **One process of its own**, `python -m bridges.email` under launchd,
  so an IMAP or SMTP stall never holds the kernel.
- **Only owned senders are read**, through `owns`, so no Mac marks seen
  mail another Mac's bridge waits for. Receive scope, not a gate.
- **`email_since`** keeps the backlog of unseen mail from arriving as new
  on the first poll, and marks the boundary with `main`'s bridge.
- **No UID cursor**, so a UIDVALIDITY change or a gap between windows
  replays nothing.
- **The mail credential in the kernel key directory**, as machine.md
  settles for kernel-held secrets.
- **The local servers are Dovecot and `aiosmtpd`**, Dovecot because UID
  and UIDVALIDITY behavior is the thing under test, and it runs as the
  agent's user with no root.

## Questions for Tom

None in 2.3. Which addresses count as Tom, and which second address of
his is cc'd in the window, go to 2.1's identity question. Where the app
password lives is settled by machine.md. The DMARC record and the
`Authentication-Results` report are Tom's rollout steps above.

## Critique round 1 (of 1): revise, rounds spent

| Finding | Handled |
|---|---|
| 1. Port does not match 2.1 | "Port used" written to the lead's port decisions: `core/bridge.py`; `verified` set by `core/intake.py` from raw headers; `chat_id` the thread root, ownership and start on `sender_id`; `thread` dicts; `inbound_dir`; `operator_email`; `sent` in results; binding on `(channel, chat_id, message_id)` |
| 2. No file store; check-then-read race | Store dropped; `perform` reads, hashes, compares, and sends the same bytes; swap test added |
| 3. UID cursor replays old mail | Cursor dropped; `UNSEEN SINCE` per owned sender only; webmail-opened mail under Left out |
| 4. In-doubt sends written `failed` | The performer raises `broker.Unknown` once the body has started; delayed-250 test; the broker side is port decision 24 |
| 5. No later reconcile | Port decisions 22 and 23; `settle_after_s` includes `email_sent_settle_s`, measured in the window |
| 6. Sender filter and `email_since` | Filter built from `owns` and `owned`; contradictions fixed (other addresses not received; forgeries tested from owned addresses); `xtom@` test; cost under Left out |
| 7. Guard row | Incident restated; `mission_items` `[6]`; `source` added; per-guard `note` and `via` overrides; `GUARD_DMARC` in `guards.py`; expiry note in the window |
| 8. DMARC on intra-domain mail | Tom's pre-window step 1; DKIM key in step 2 |
| 9. Which backend to terminate | `valor-email-perform`; invariant asserted; exactly-one shown by the after-DATA hook |
| 10. `LIST (SPECIAL-USE)` on Gmail | Plain `LIST`; `X-GM-RAW` when `X-GM-EXT-1`; the window records which works |
| 11. Size arithmetic | Inbound caps dropped; `max_file_bytes` is Gmail's cited 25 MB encoded limit, compared with the encoded size; deadline on encoded length; a mid-body timeout is `Unknown` with `__context__` named |
| 12. Poison message | Logged, left unseen, poll continues; test added |
| 13. Recipient claim | Threat model reworded: Tom's tap on the full card is the control |
| 14. Premise slips | Both SMTP sources named; `read_key` names the owning command; imports per port decision 30 |
| 15. Limits | Floor rate provisional, replaced by the window's measurement; IMAP socket timeout added from main's value |
