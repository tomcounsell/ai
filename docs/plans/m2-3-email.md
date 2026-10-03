---
tracking: none
slug: m2-3-email
type: build
status: planned
critique_rounds: 1
review_rounds: 2
---

# 2.3 in full: the email bridge, adapted from `main`

Task 2.3 of [valor-rebuild.md](valor-rebuild.md), milestone 2. It builds
`bridges/email/` to the bridge port that task 2.1 puts in `core/`
([bridges/telegram.md](../bridges/telegram.md#the-bridge-port)), to the
contract in [bridges/email.md](../bridges/email.md), from the email code on
`main` (`bridge/email_bridge.py`, `bridge/email_relay.py`), read with
`git show origin/main:<path>` and never imported.

Planned against 2.1 as valor-rebuild.md describes it. Every name taken from
2.1 is listed under "Port assumed"; the build uses whatever names 2.1's
plan settles, and only those.

## Stakes

`critique_rounds: 1`, `review_rounds: 2`. The bridge sends mail to real
people under Valor's name, and decides which mail counts as Tom's. A
mistake sends a message twice, sends one Tom did not approve, or lets a
forged `From` start work. It touches the kernel only in small, named
places (the DMARC identity test, reply-all recipients, one guard seed,
settings, and how long `reconcile` waits for a mail performer).

## Done, as evidence

The milestone's Done items for email are four, all on Valor's real
mailbox during a test window. Each is first shown against local IMAP and
SMTP test servers, with a real Postgres and no mocks, so the window only
confirms what the provider does.

### Shown now, against local test servers

The local servers are Dovecot (IMAP, run as the agent's user from a
config written under the test's temporary directory, a self-signed test
CA, a password file of test users, a `\Sent` special-use folder) and a
small SMTP server in `tests/mailserver.py` built on `aiosmtpd` (STARTTLS,
AUTH, a recipient it can be told to refuse, a reading rate it can be told
to hold, a hook after DATA, and every accepted message appended to the
sender's `\Sent` folder in Dovecot, which is what Gmail does). Ports are
free ones the OS gives each test run.

| Done item | Evidence here |
|---|---|
| A mail from Tom passing DMARC starts a task | A message delivered into the Dovecot inbox with the headers Gmail writes (topmost `Authentication-Results: mx.google.com; ... dmarc=pass ... header.from=<Tom's domain>`) and `From` Tom's address lands as one `message.received` row and starts one task with its metered spending shown in `status`. The same message with each forgery in "Tests" lands as one row and starts nothing |
| A reply-all is held and released once | A task requests `email.send` replying to Tom's message (which had a `Cc`). The held effect's payload names the sender plus every `To` and `Cc` address minus Valor's own, `Re:` subject, `In-Reply-To`, and the whole `References` chain. `core approve` then `core release` lead the bridge process to send it; the local SMTP server receives exactly one copy with the Message-ID derived from the broker key; a second `release` returns the recorded outcome and sends nothing |
| A crash between SMTP and outcome does not double-send | Two ways. The SMTP server's after-DATA hook kills the bridge process by its PID once the message is stored, before the 250 reply. And, with no hook, the test terminates the bridge's Postgres backend (by its pid from `pg_stat_activity`, `application_name = 'valor-email'`) while a 9 MB upload is in flight, so SMTP completes and the outcome write fails. In both, the restarted bridge's sweep finds the message in `\Sent` by its Message-ID, writes `effect.outcome` `done` with `reconciled: true`, and the server holds one copy |
| A 9 MB attachment sends | A 9 MB file sends through a server reading at 1 MB/s with `smtp_timeout_s` set to 2 s for the test: the scaled deadline lets it finish, and the received attachment's sha256 equals the approved one. The same send with the deadline unscaled fails, as #3601 did, with the timeout in the error |

### Waits for Tom's test window on Valor's real mailbox

| Done item | Evidence in the window |
|---|---|
| A mail from Tom passing DMARC starts a task | Tom sends a request from his address; its topmost `Authentication-Results` is from `mx.google.com` with `dmarc=pass`; one task starts. A mail from any other address of his is recorded and starts nothing |
| A reply-all is held and released once | Valor's reply to that mail, which Tom cc'd to a second address of his own, waits for his tap; one tap, one copy in each inbox |
| A crash between SMTP and outcome does not double-send | During a 9 MB reply, the bridge's Postgres backend is terminated by its pid; launchd restarts the bridge, the sweep finds the message in Gmail's Sent Mail, and Tom's inbox holds one copy |
| A 9 MB attachment sends | The reply above carries a 9 MB file that arrives intact |

The window also measures, with no decision attached: that Gmail files
SMTP-submitted mail in Sent Mail and that `UID SEARCH HEADER Message-ID`
finds it (the gap in email.md), how long that takes, and the bridge's RSS
after the window (machine.md estimates 100 MB).

## Threat model

What senders control: every byte of an inbound message except the
headers the receiving server prepends, so `From`, `Message-ID`,
`References`, `In-Reply-To`, any `Authentication-Results` lines inside
the message, MIME structure, charsets, part counts and sizes, and
attachment filenames. What a turn controls: the `email.send` it requests
(body, files from its workspace, and for a new thread the recipients and
subject), and everything in its workspace.

What the kernel must never do with any of it:

- Treat a mail as Tom's on its `From` alone. Only the topmost
  `Authentication-Results` header, carrying the configured authserv-id,
  with `dmarc=pass` for the domain of a single `From` address equal to
  Tom's, makes a mail Tom's. Lines further down are the sender's and are
  never read for identity.
- Take an approval, a stop, or any authority from mail. Mail is data.
- Send anything the broker did not release with Tom's unused approval
  bound to the payload digest, or let a turn choose a reply's recipients:
  `core/` computes reply-all from the received row.
- Read a payload file from a workspace when sending. The file is copied
  into the kernel's file store when the effect is requested, and the
  performer sends those bytes.
- Write an attachment outside the media directory, or decode a part past
  the per-message cap.
- Put the app password in a record, a row, a log line, an exception, or
  `os.environ`.

Accepted: a sender who knew a Message-ID before Tom used it could send
it first and shadow Tom's message at the receipt index. Gmail's ids are
random, so this needs the id before it exists.

## Port assumed

The names 2.3 builds on, as valor-rebuild.md 2.1 and telegram.md
describe them. The lead reconciles them with 2.1's plan.

- `core.intake.Inbound`, with telegram.md's fields (`channel`, `chat_id`,
  `chat_kind`, `message_id`, `sender_id`, `sender_name`, `sent_at`,
  `kind`, `text`, `reply_to`, `thread`, `attachments`, `headers`), and
  `intake.receive(conn, inbound)` returning whether the row is new, with
  the unique index on `(channel, chat_id, message_id)`.
- A read of the highest receipt position per scope, the one Telegram's gap
  fill needs: `intake.highest(conn, channel, scope)` over a `cursor`
  (`scope`, `position`) the record carries. Email uses
  `INBOX:<UIDVALIDITY>` and the UID.
- `core.bridge.Bridge` (`channel`, `limits: ChannelLimits`,
  `performers()`, `run(intake, outbox)`).
- The outbox: a `release.requested` row for a held effect Tom approved,
  a Postgres notification on it, and an iterator over requested effect
  ids of the bridge's action types; the bridge calls `broker.release`
  for each in its own process.
- `broker.Performer` with awaitable `perform` and `lookup` (1.4's
  absorbed item), `broker.Unknown`, and the idempotency key carrying the
  effect id (2.1's absorbed item), so two identical sends in one task are
  two effects with two Message-IDs.
- The restart sweep: `broker.reconcile` for every dangling intent of the
  action types a process registers, run by that process when it starts.
- The operator test per channel: one function in `core/` that says
  whether a received row is Tom's, where 2.3 registers the email case
  (`mail.from_operator`).
- Binding by structure: a row whose `reply_to` equals the `message_id` in
  an `effect.outcome` or `notice.sent` binds to that row's task.
- The reader of a turn's effect requests (`core/signals.py` today), where
  an `email.send` naming `reply_to` is expanded before the broker holds
  it.
- A kernel-owned file store, outside every workspace: `files.keep(path)`
  copies a file at request time and returns its `sha256`, `name`, and
  `size`; `files.path(sha256)` is where the performer reads it. If 2.1
  does not build it, 2.3 builds `core/files.py`, shared with 2.2.
- `settings.media_dir`, the bridges' inbound media directory, which turns
  can read.

## What is built

### `bridges/email/parse.py`: kept and adapted

Pure functions over raw bytes. No disk, no network.

| Function on `main` (`bridge/email_bridge.py`) | Here |
|---|---|
| `_decode_header_value` | Kept |
| `_extract_address`, `_extract_addresses` | Kept. A `From` with more than one address is reported as such, so identity can refuse it |
| `_extract_body` | Adapted: an unknown charset decodes as UTF-8 with replacement instead of raising `LookupError`; HTML-only mail is reduced to text with `html.parser`, dropping `script` and `style` and unescaping entities, in place of the tag regex |
| `_sanitize_attachment_filename`, `_is_attachment_part` | Kept |
| `_extract_attachment_metadata` | Kept, with the caps from settings (`email_max_attachment_bytes`, 25 MiB, and `email_max_attachment_parts`, 50, main's values); each part over a cap or failing to decode is listed as skipped with its reason instead of one `truncated` flag |
| `_attachment_storage_key` | Adapted: the key is the sha256 of the record's `message_id` |
| `_persist_attachments` | Kept, writing under `settings.media_dir/email/<key>/`; the vault mirror goes |
| `parse_email_message` | Adapted: keeps empty-body mail with no attachments; keeps mail with no `From` (sender empty); reads `References` (ids in order), `In-Reply-To`, `Date`, `Subject`, `To`, `Cc`, and every `Authentication-Results` header in order; returns `Inbound` fields |
| `_public_attachment`, `_email_media_type`, `_attachment_descriptors`, `_body_references_attachments`, `_mirror_attachments_to_vault` | Not carried: descriptors are the port's `attachments` field, and guessing from the body that a file is missing is interpretation |

The record, as email.md's table gives it, with one change: a message with
no `Message-ID` gets `sha256:<digest of its raw bytes>`, which survives a
UIDVALIDITY change, where `uid:<UIDVALIDITY>:<UID>` would not. `sent_at`
is the `Date` header, or the server's `INTERNALDATE` when `Date` does not
parse. `headers` carries `subject`, `to`, `cc`, `references`,
`authentication_results` (a list, topmost first), `uid`, and
`uidvalidity`.

### `bridges/email/imap.py`: the poll, adapted from `_poll_imap`

One connection per poll, every `email_poll_s` seconds (30, main's value),
under `imaplib.IMAP4_SSL` with `ssl.create_default_context()` (main passes
no context, so certificates go unverified there).

1. `SELECT INBOX`; read `UIDVALIDITY`.
2. `UID SEARCH` for mail from the senders this machine owns
   (`email_senders`, built by main's `_build_imap_sender_query`, kept) that
   is `UNSEEN SINCE <email_since>`, together with every UID above
   `intake.highest(conn, "email", "INBOX:<UIDVALIDITY>")`. The second part
   receives mail someone opened in another client; the ledger is the
   cursor, and the bridge keeps none.
3. For each UID, oldest first: `UID FETCH (UID INTERNALDATE BODY.PEEK[])`,
   parse, persist attachments, `intake.receive`, and only after it
   returns, `UID STORE +FLAGS.SILENT (\Seen)`.

A message already in the ledger lands nowhere and is marked seen. A
failed login or connection is one failed poll in the log and the next
poll runs on schedule; there is no backoff, alert key, or retry state.
Main's batch cap of 20 goes: every matching message is received, oldest
first.

### `bridges/email/smtp.py`: the performer, adapted from `_build_reply_mime` and `_send_smtp_sync`

`email.send`, `act`, target the first `To` address, payload as email.md
gives it (`to`, `cc`, `subject`, `body`, `in_reply_to`, `references`,
`files`, each file a `sha256`, `name`, and `size` in the file store).

- **MIME** (adapted `_build_reply_mime`): `EmailMessage`, `text/plain`
  UTF-8 body, files as `multipart/mixed` parts read from
  `files.path(sha256)`. The subject is the payload's, unchanged: no `Re:`
  is added here (`force_reply_prefix` goes). `References` is the payload's
  whole chain. Headers added: `From` (`email_address`), `To`, `Cc`,
  `Date`, and `Message-ID` = `<valor.<first 32 hex of sha256(key)>@<Valor's
  domain>>`, so a performed effect always carries the same id.
- **Send** (adapted `_send_smtp_sync`): `smtplib.SMTP` with
  `smtp_timeout_s` (30, main's `smtp_s`), `starttls` with the default
  context, login, then the socket's timeout set to the scaled deadline,
  `smtp_timeout_s + size / smtp_floor_bytes_per_s`, before
  `send_message`. `smtp_floor_bytes_per_s` is 50,000, a quarter of the
  rate #3601 measured (9.0 MB in 45.6 s). One attempt. All recipients
  refused, a refused login, or a refused message raise, and the broker
  records `failed` with the server's reply. Some refused: `done`, with
  `refused` listing each address and its reply. The result carries
  `message_id`, `accepted`, and `refused`.
- **lookup**: one IMAP connection, the folder with the `\Sent`
  special-use flag (`LIST (SPECIAL-USE)`), `UID SEARCH HEADER Message-ID
  <id>`. Found: the result with `message_id`. Not found: `None`. A
  connection or login failure raises `broker.Unknown`.
- **settle_after_s(action)**: twice the scaled deadline for the action's
  size. `broker.reconcile` waits this long, when the performer defines
  it, before reading a missing message as never sent, in place of
  `settings.reconcile_after_s`, which is sized for git. A 25 MB message
  waits about 17 minutes; a text reply about one.
- **limits**: no text length limit; files at most 25 MB together (Gmail's
  limit), stated so `core/` renders within it. The bridge does not refuse
  over it; the server does.

Main's retry counters, dead letters, relay loop, drafter, history cache,
and `react` are not carried.

### `bridges/email/__main__.py`: the process

`python -m bridges.email` connects to Postgres as the kernel role with
`application_name = 'valor-email'`, registers the performer, runs the
restart sweep, then runs the poll loop and the outbox loop together in
one event loop. A lost database connection ends the process with a
nonzero exit and launchd starts it again. `python -m bridges.email
--plist` prints its launchd job (`KeepAlive`, logs under
`settings.log_dir`), as `core backup --plist` does.

### `core/mail.py`: what the kernel decides about mail

Pure functions, no I/O.

- `from_operator(row)`: the DMARC identity test in the threat model,
  registered as the email case of 2.1's operator test.
- `reply_all(row, own)`: `to` is the sender; `cc` is every `To` and `Cc`
  address minus Valor's own and minus the sender, lowercased, in order,
  without repeats; `subject` gains `Re: ` unless it starts with `re:` in
  any case, and an empty one is `Re: (no subject)`; `in_reply_to` is the
  row's `message_id`; `references` is the row's `References` ids plus its
  `message_id`.
- `instruction(row)`: a task started by email takes its subject, a blank
  line, and its body as the instruction.

The effect-request reader expands an `email.send` that names `reply_to`
(a received `message_id`) through `reply_all` before the broker holds
it, so Tom's approval covers the final recipients. An `email.send` that
names no `reply_to` carries its own `to`, `cc`, and `subject`, and Tom
sees them in the card like any held effect. The performer's `usage`
line offers both forms.

### The DMARC check, ledgered

Tom's default answer to open question 17 (A, standing from 2026-10-01)
grants it as an approved check, and valor-rebuild.md says it is ledgered
the way the four pipeline guards are when milestone 2 builds it. So
`core/guards.py` seeds a fifth guard on the `guards` stream, once,
through `migrate`:

| Field | Value |
|---|---|
| `guard_id` | `email.dmarc` |
| `name` | the DMARC test: a mail counts as Tom's only when the receiving server's topmost `Authentication-Results` shows DMARC pass for his address |
| `incident` | On `main`, the email bridge started sessions from any mail whose `From` matched a known sender, with no SPF, DKIM, or DMARC test, and the attacker-controlled `From` reached a shell command a session was told to run (`docs/features/context-recall-advisory.md` on `main`, #2694); no forged mail is on record |
| `mission_items` | `[1]`, and the constraint "Bounded authority, metered spending" in the note |
| `granted_at` | 2026-10-01 |
| `expires` | 2026-12-30 |
| `provenance` | `by: tom`, `via: open question 17, default not objected to, seeded by migrate`, `role_played: false` |

It fires when a mail from Tom's address fails the test; that is read
from the `message.received` row itself, so no new row type is added. If
the expiry sweep deletes it, nothing else tells Tom's mail from others',
and email starts no work; that deletion is Tom's tap like any other.

### Settings and the credential

`core/settings.py` gains: `email_address` (Valor's), `email_operator`
(Tom's address), `email_senders` (default: `email_operator` alone),
`email_since` (a date), `email_authserv_id` (`mx.google.com`),
`imap_host`, `imap_port` (993), `smtp_host`, `smtp_port` (587),
`email_poll_s` (30), `smtp_timeout_s` (30), `smtp_floor_bytes_per_s`
(50,000), `email_max_attachment_bytes`, `email_max_attachment_parts`,
`mail_cafile` (unset; tests point it at their CA), and `media_dir`.

`python -m core mail-keys` copies `IMAP_USER`, `IMAP_PASSWORD`,
`SMTP_USER`, and `SMTP_PASSWORD` from the vault `.env` into `mail-keys`
in the kernel key directory (mode 600), through the same code as
`judgement-keys`, printing each name with `written`, `kept`, or
`missing`, never a value. The bridge reads the file when it starts; a
missing name fails the start, naming it. The password is held in the
IMAP and SMTP config objects only.

## Tech debt absorbed

- #3601: the SMTP timeout bounded the whole upload, so large attachments
  failed; the deadline scales with size. The other three items in #3601
  go with the code not carried.
- #3124 and #2160: moot. Sends are held for Tom and carry exactly what he
  approved; steering is in `core/`.
- Certificates unchecked on IMAP and SMTP on `main` (no SSL context
  passed): both use `ssl.create_default_context()`.
- `_extract_body` raising on an unknown charset; the HTML regex leaving
  script and style text and entities.
- `parse_email_message` dropping empty-body mail and mail with no `From`.
- `\Seen` set before the fetch on `main`, so a crash lost the message: it
  is set after `receive` returns.
- Docs that say the bridge's secrets go in the Keychain: they go in the
  kernel key directory, for the reason machine.md gives (a turn can read
  the login keychain).

## Left out

- Operator notices by email. Telegram is the operator channel; email
  notices wait for settings to name email.
- Approvals and stops by email (email.md: never).
- IMAP IDLE; polling every 30 seconds stands.
- Alerts on a failing login, backoff, and a health key. Telling Tom is
  `core/`'s job, and no incident in this system asks for it yet.
- Subject coalescing, the vault mirror, the Redis history and dead
  letters, the relay's retries, per-sender project routing, the
  customer-service handler, the drafter, and `gws` drafts.
- Standing grants for any thread; every send to anyone but Tom is a tap.
- Folders other than `INBOX`.

## Tests

Real Postgres (`VALOR_TEST_DB`), real Dovecot, the local SMTP server, no
mocks. Tests needing Dovecot or `aiosmtpd` fail naming what is missing.

**Parsing** (`tests/test_email_parse.py`, fixtures in
`tests/fixtures/mail/`):

- Empty body, no attachments: a record with `text` "" and no
  attachments.
- Empty body with one attachment; attachment-only mail.
- A 9 MB attachment: saved, size and sha256 right. A 26 MB one: listed
  as skipped, over the size cap, never decoded. 51 parts: 50 saved, one
  skipped.
- Filenames `../../etc/passwd`, `.`, empty, and two parts both
  `report.pdf`: all land inside the message's directory, distinct.
- RFC 2047 subject and display name; an unknown charset; malformed MIME
  that `email` parses loosely.
- HTML-only mail with `<script>`, `<style>`, and `&amp;`.
- No `Message-ID`: `sha256:` id, the same on two parses. No `From`:
  recorded, sender empty.
- `References` with folded lines and stray text: ids in order; `chat_id`
  the first.
- Unparseable `Date`: `sent_at` from `INTERNALDATE`.

**Identity** (`tests/test_mail_identity.py`), `from_operator` is true
only for the first case:

- Topmost AR from `mx.google.com`, `dmarc=pass`, `header.from` Tom's
  domain, `From` Tom's address in any case.
- The same with `dmarc=fail`, `dmarc=none`, or no `dmarc` result.
- A forged pass line below a real topmost fail.
- A pass line whose authserv-id is anything else, alone or topmost.
- `header.from` another domain; `From` another address at Tom's domain.
- Two `From` addresses; `"tom@... " <other@x>` display-name tricks.
- No `Authentication-Results` at all.

**The bridge** (`tests/test_email_bridge.py`):

- One mail lands as one row, then is `\Seen`; a second poll records
  nothing.
- Receive without the `\Seen` store (the poll's steps run with the store
  skipped, as a crash would leave it), then a full poll: one row, then
  seen.
- UIDVALIDITY change (`doveadm mailbox update --uid-validity`): mail
  received and seen before is not received again; mail received but not
  yet seen lands once after the change, with and without a
  `Message-ID`; the UID cursor restarts under the new scope.
- Mail opened in another client (seen before the poll, above the
  cursor) is received.
- Mail from a sender not in `email_senders` stays unseen and unrecorded.
  Unseen mail before `email_since` is not received.
- A mail from Tom passing the test starts one task; each forgery above,
  delivered the same way, is one row and no task.
- Tom's reply to a mail Valor sent (its `In-Reply-To` is the outcome's
  `message_id`) binds to that task.
- Reply-all: recipients, subject, threading as in `reply_all`; held;
  released once by the bridge process; one copy at the server; second
  release returns the recorded outcome.
- Two identical replies in one task: two effects, two Message-IDs, two
  copies.
- A stopped task's held send: release refused, nothing sent.
- Crash between SMTP and outcome: both ways in Done.
- Crash after the intent, before SMTP connects (the bridge killed by PID
  from a hook on connect): the sweep finds nothing, writes `failed` once
  `settle_after_s` passes (set short in the test), and nothing is sent.
- Lookup with the IMAP server down: `Unknown`, nothing written.
- 9 MB send through the held reading rate: done, attachment byte-equal;
  unscaled, it fails with the timeout in the error.
- One recipient refused: `done` with it in `refused`. All refused:
  `failed`. Wrong password: `failed` with the server's reply.
- A server whose certificate the test CA did not sign: IMAP and SMTP
  both refuse to connect.
- With a known test password, after a run including a refused login, the
  password appears in no ledger row, log line, or exception text.

**Kernel** (`tests/test_guards.py` or beside the seed tests):
`migrate` on a ledger holding the four guards adds `email.dmarc` once;
a second `migrate` adds nothing. `reconcile` uses a performer's
`settle_after_s` when it has one and `settings.reconcile_after_s`
otherwise.

**Window** (`tests/test_live_email.py`, run only with `VALOR_LIVE=1` and
the window's settings): the four Done items and the Sent Mail lookup on
Valor's mailbox, driven by the steps below.

## Files it changes

New:

- `bridges/email/__init__.py`, `__main__.py`, `parse.py`, `imap.py`,
  `smtp.py`
- `core/mail.py`
- `tests/mailserver.py` (Dovecot and SMTP fixtures),
  `tests/test_email_parse.py`, `tests/test_mail_identity.py`,
  `tests/test_email_bridge.py`, `tests/test_live_email.py`,
  `tests/fixtures/mail/*.eml`

Changed:

- `core/settings.py`, `core/guards.py`, `core/broker.py` (`reconcile`
  reads `settle_after_s`), `core/__main__.py` (`mail-keys`),
  `core/credentials.py` (the key copy shared with `judgement-keys`)
- The operator test and the effect-request reader 2.1 names
  (`core/signals.py` today), each to register or call the email case
- `pyproject.toml` (`aiosmtpd` in the dev group)
- Docs: `docs/bridges/email.md` (status, the `sha256:` id, the sender
  filter and `email_since`, the UID cursor, the key directory),
  `docs/machine.md` (the mail credentials row), `docs/tech-stack.md`
  (bridges' secrets; `imaplib` and `smtplib` chosen), `core/README.md`,
  `tests/README.md`, `docs/plans/rebuild-handoff.md` (Dovecot in setup)

`core/settings.py`, `core/broker.py`, and `core/__main__.py` are shared
with 2.1 and 2.2, so 2.3 merges after 2.1 and rebases before its checks.

## Rollout

1. `python -m core backup`.
2. Merge; on the build Mac, pull and `python -m core migrate` (seeds
   `email.dmarc`).
3. `brew install dovecot` on any machine that runs the suite.
4. `python -m core mail-keys`; set `email_address`, `email_operator`,
   and `email_since` (the window's date) in the bridge's launchd
   environment.
5. Install the plist from `python -m bridges.email --plist`, unloaded.

The test window, with Tom:

1. Before it: `_dmarc.yuda.me` publishes a DMARC record (see Questions);
   `dig +short TXT _dmarc.yuda.me` shows it.
2. Disable the email bridge on `main` on the build Mac (`email-disable`)
   and on the Mac whose `projects.json` lists Tom's address (Valor the
   Captain, project cuttlefish today), so no other poller marks his mail
   seen. Load the new bridge.
3. Tom sends a request from his address with a `Cc` to a second address
   of his own: one task starts, its spending shown.
4. Tom sends one mail from another address of his: one row, no task.
5. The task's reply carrying a 9 MB file is held; Tom taps once; during
   the upload, terminate the bridge's Postgres backend by its pid. The
   bridge restarts, the sweep records `done`, and each inbox holds one
   copy.
6. Record Gmail's Sent Mail lookup result and delay, and the bridge's RSS.
7. Unload the new bridge and enable the email bridge on `main` on both
   Macs. Mail in the window belongs to the new system and is not
   replayed.

## Decided by default

- **One process of its own**, `python -m bridges.email` under launchd,
  per email.md, so an IMAP or SMTP stall never holds the kernel.
- **Only senders this machine owns are read** (`email_senders`, default
  Tom's address), with main's IMAP sender query kept. Each of Valor's
  Macs owns its senders, and reading all of `INBOX` would mark seen mail
  another Mac's bridge is waiting for. email.md's "records every
  message" becomes "every message from the senders this machine owns".
- **`email_since`** keeps the backlog of unseen mail on Valor's mailbox
  from arriving as new on the first poll.
- **The UID cursor from the ledger** alongside `UNSEEN`, so mail opened
  in Gmail's web client is still received, and no local state is kept.
- **`sha256:` ids** for mail with no `Message-ID`, stable across a
  UIDVALIDITY change.
- **No batch cap per poll.** Every matching message is received in UID
  order.
- **The mail credential in the kernel key directory**, not the Keychain
  (also asked below, since it is a credential choice).
- **Reconcile waits per performer** (`settle_after_s`), since a large
  upload outlasts the wait sized for git.
- **No re-hash at send.** The file store is kernel-owned and keyed by
  sha256; the performer sends the bytes at that key.
- **The local servers are Dovecot and `aiosmtpd`**, Dovecot because UID
  and UIDVALIDITY behavior is the thing under test, and it runs as the
  agent's user with no root.
- **The DMARC guard's grant date** is 2026-10-01, the date open question
  17's default took effect, so it expires with the four pipeline guards.
- **The task instruction from email** is the subject, a blank line, and
  the body, since requests by email often sit in the subject.

## Questions for Tom

Each carries the answer the build assumes and goes ahead with.

1. **Which addresses count as you?** Assumed: one, `tom@yuda.me`. Mail
   from `tomcounsell.com` or any other address of yours is recorded and
   starts nothing.
2. **`yuda.me` publishes no DMARC record today**, so no mail from that
   address can pass the test, and email cannot start work. Will you
   publish one? Assumed: yes, before the test window;
   `v=DMARC1; p=none` is enough for a pass result, since Google already
   sends your mail with SPF aligned. Until then, mail from you is
   recorded as someone else's.
3. **Where does the mailbox's app password live?** Assumed: the kernel
   key directory (`mail-keys`, copied from the vault `.env` by
   `python -m core mail-keys`), not the Keychain the docs name, because a
   turn can read the login keychain and cannot read the key directory.
4. **Who receives the test window's reply-all?** Assumed: you, at your
   address and one second address of yours on `Cc`; no client or other
   person gets test mail.
