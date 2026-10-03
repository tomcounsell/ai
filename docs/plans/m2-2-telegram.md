---
tracking: none
slug: m2-2-telegram
type: build
status: planned
critique_rounds: 1
review_rounds: 2
---

# 2.2: the Telegram bridge, adapted from `main`

Task 2.2 of [valor-rebuild.md](valor-rebuild.md), milestone 2. It builds
`bridges/telegram/`: a Telethon client on Valor's own account that hands
every message in the chats this machine owns to `intake.receive`, performs
the `telegram.send_message` effects the outbox yields, and sends the
notices the outbox yields. It is a bridge on the port in
[m2-1-port.md](m2-1-port.md), which 2.1 builds in `core/`.

It serves Mission items 1 and 6 (Tom gives work and taps approvals where
he already is) and the constraint "Bounded authority, metered spending"
(nothing leaves under Valor's name without passing the broker).

Depends on 2.1's merge and on 1.4d's (`broker.Performers` passed
explicitly, awaitable perform and lookup).

## Stakes

`critique_rounds: 1`, `review_rounds: 2`. The bridge sends to real people
under Valor's name and holds the account's session. It changes no kernel
file.

## Port used

The lead's port decisions, items 1 to 36 and 34a, as 2.1 writes them in
[m2-1-port.md](m2-1-port.md) (2.1's commit 4ce1cede3). What 2.2 uses, by
item:

| Item | What 2.2 does with it |
|---|---|
| 1, 28 | `bridges/telegram/__main__.py run` calls `core.bridge.serve(TelegramBridge())`; the class implements `Bridge` (`channel = "telegram"`, `limits`, `performers()`, `async run(outbox)`) |
| 2 | Every message ends at `intake.receive(conn, inbound)`; `Received.duplicate` means already recorded, and nothing more happens |
| 3, 32 | Gap fill pages with `intake.highest` as a hint and receives only ids `intake.recorded` does not list, checked before any download |
| 4, 15, 16 | Reads only `intake.owned("telegram")` (the operator chat, plus chats a project spec lists for this machine); drops events from any other chat and Valor's own messages |
| 5, 23 | Iterates `Outbox`: a `Release` goes to `outbox.perform(item)`, a `NoticeDue` is sent by the bridge; the outbox reconciles `broker.dangling` on every wake. The bridge runs no loop, LISTEN, drain, or sweep of its own |
| 8, 9 | A notice goes to `item.chat_id`; `outbox.sent(item, sent)` records it, `sent` being `[{channel, chat_id, message_id}]` |
| 11 | Passes Telegram's raw facts; `receive` sets `verified` true for every Telegram record, since Telegram's servers attest the sender id, and bind decides whether the sender is the operator (`sender_id == operator_telegram_id`) |
| 15b, 25 | `ChannelLimits(max_text=4096, max_file_bytes=2_097_152_000)`, Telegram's message length and upload limit; the performer splits text over the limit |
| 17, 18 | `Inbound.topic_id`, `thread: list[dict]`, `headers["grouped_id"]`, attachments `{name, mime, bytes, path}` or `{name, mime, bytes, skipped: reason}`, files under `settings.inbound_dir/telegram/` named by sha256 |
| 19 | Payload `files: [{path, sha256}]`: read once, hashed, raise before sending on a mismatch, send the bytes hashed |
| 22, 24 | `Declared` for `telegram.send_message` is 2.1's; a send in doubt raises `broker.Unknown` |
| 26 | Uses 2.1's settings: `operator_chat`, `inbound_dir`, `machine`, `serve_tick_s`, `pg_passfile` |
| 27 | Single instance is `serve`'s session lock `bridge:telegram:<machine>`; no flock |
| 29 | Connections named `valor-telegram` and `valor-telegram-perform`, by `serve` |
| 30 | Imports `core.bridge`, `core.intake`, `core.broker`, `core.settings`, `core.db`, `core.credentials`, nothing else |
| 31 | `--plist` prints the job for Tom to load |
| 33, 34, 34a | `lookup(action, key, since)`: `since` is the intent's `at`, which `bridge.serve` passes on a `Release` and on reconcile alike. The scan covers own messages dated at or after `since` less the clock margin, skipping `intake.claimed` ids |
| 35 | A notice lookup matches the notice's short id in its text |
| 36 | Flood waits are held in memory for the process |

## Done, as evidence

### Shown now, with the Telegram emulator and the local test server

The emulator is `tests/telegram_emulator.py`, a local HTTP server run as
its own process. It holds chats, own messages, a message id sequence per
supergroup and one shared by private chats and basic groups (so a chat's
ids have gaps), `random_id` duplicate detection, Telegram's trim of
leading and trailing whitespace, injected flood waits, dropped live
updates, a disconnect after accepting a send and before replying, and a
pause point after a send is accepted. The bridge reaches it below
`bridges/telegram/wire.py`, through a stand-in wire the test's child
process passes in; no production setting selects it. Postgres is the
real test database. `wire.py` itself (Telethon's update handling, error
types, and reconnect) is shown only on the test servers below.

- **Receipt is idempotent and lossless.** Killed between commit and the
  read acknowledgement, a message lands once after restart. The live
  handler and the gap fill receiving one message at once write one row.
  A live update Telethon drops (104 dropped, 105 delivered) is recorded
  within one tick.
- **Gap fill.** After the bridge was down, every message in each owned
  chat that `intake.recorded` does not list is received once, oldest
  first, across more than one page; a chat with no rows backfills
  nothing.
- **An inbound message from Tom starts a task** through 2.1's kernel, and
  `python -m core status` shows its metered spending.
- **A question reaches Tom and his reply binds.** A question notice goes to
  the row's chat, `notice.sent` records its message id, and an emulated
  reply from Tom's id records `question.answered` with `via: "telegram"`,
  `role_played: false`.
- **A held push is released by a tap.** A reply of exactly `approve` to
  the held push's effect notice records `approval.granted`, and the kernel
  performs the push to the task's local bare origin, with no
  `release.requested`. `Approve.` binds as a steer and owes the notice
  "not an approval; reply `approve`". `approve` in reply to the delivered
  notice binds as feedback.
- **A forced crash between intent and outcome does not send twice.**
  Killed at the pause point after the emulator accepted a send: on
  restart, the outbox's reconcile finds the message and writes `done`,
  reconciled, and the emulator holds one message. Killed before the send
  was accepted and restarted at once: the effect stays in flight until
  its intent is older than the settle time, then a later tick writes
  `failed`, and nothing is sent.
- **A send in doubt is not a failure.** The emulator accepts a send and
  drops the connection before replying: the effect ends `done` with one
  message on screen.
- **`telegram.send_message` sends verbatim**: plain text with no parse
  mode and no link preview, the reply target and forum topic honored, text
  over 4,096 split into several messages, files sent as documents from
  the bytes hashed.
- **The inbound record carries the forum topic id** (#2652).

### Shown on Telegram's test servers, with test accounts

Telegram runs test data centres with their own phone numbers
(`99966XYYYY`, sign-in code fixed by the number). No Valor session is
used. `tests/test_live_telegram_dc.py` (`VALOR_LIVE=1`,
`VALOR_TELEGRAM_TEST_DC=1`) reads Valor's API id and hash from the kernel
key directory, which every turn's sandbox denies, so the lead session runs
it outside a turn, before the merge. Through the real `wire.py` it shows:

- a repeated `random_id` from a user account is dropped rather than
  delivered twice; if Telegram delivers again, the test says so and
  notices rely on the short-id lookup alone;
- send, split, topic send, file send, `lookup`, gap fill, and media
  download against MTProto;
- `sequential_updates=True` handling, and the mapping of Telethon's
  `FloodWaitError`, `RandomIdDuplicateError`, and a connection lost
  mid-request onto failed, sent, and `broker.Unknown`.

### Waiting for Tom's test window, on Valor's real account

In the operator group, as valor-rebuild.md 2.2 states them:

- an inbound message starts a task with its spending metered and shown;
- a question reaches Tom and his reply binds to it;
- `approve` in reply to a held push's effect notice releases the push,
  which goes to a local bare origin; `approve` in reply to the delivered
  notice is feedback;
- a forced crash between intent and outcome does not send twice, run by
  `tests/test_live_telegram_window.py` with the launchd job booted out;
- the bridge's RSS after a day connected, recorded in
  [machine.md](../machine.md).

## Threat model

What others control: everything inbound. Anyone who can message Valor's
account in an owned chat controls the text, the display name, file names,
media bytes, reply targets, and the ancestors a reply chain fetches.
Telegram (or the emulator) controls what `lookup` and the gap fill read
back. A turn controls the text and files of the sends it requests and of
the questions and deliveries the kernel renders into notices.

What the bridge must never do with any of it:

- Act on inbound text. It writes facts to `message.received` and decides
  nothing; binding is the kernel's, by numeric sender id, never by name.
- Build a path from a sender's file name. Files land under
  `inbound_dir/telegram/` named by their sha256.
- Send to a chat the outbox did not yield, or anything the broker did not
  release; send other bytes than those hashed.
- Change what Tom approved: no parse mode, no link preview, no trimming;
  splitting keeps every character in order.
- Conclude `done` from anything but own messages dated after the intent,
  unclaimed, matching the payload; two matches conclude nothing.
- Let the session reach a turn, a log, or a ledger row. The session and
  the API id and hash live in the kernel key directory, which every
  sandbox profile denies; nothing prints any part of the hash.

Inbound files under `inbound_dir` (default `~/valor-inbound`) are readable
by every turn, as 2.1's sandbox stands; they are data a sender chose to
send to Valor.

## Per file, from `main`

Read with `git show origin/main:<path>`; nothing is imported from it.

| New file | Source on `main` | Kept | What changes |
|---|---|---|---|
| `bridges/telegram/wire.py` | `bridge/telegram_bridge.py` (client construction, connect loop), `bridge/telegram_relay.py` (`_send_queued_message`) | Telethon client setup; connect with exponential backoff to 256 s and jitter; a connect flood wait honored | The only module that imports Telethon. `sequential_updates=True`, `flood_sleep_threshold=0`, `catch_up=False`, `auto_reconnect=False`: the bridge owns reconnect and runs the gap fill on each connect. No attempt count. Sends are raw `SendMessageRequest` and `SendMediaRequest` with a given `random_id`, `no_webpage=True`, no parse mode, `reply_to` with `top_msg_id` for a topic. The session path, API id, and hash come from the kernel key directory. Sentry, liveness, hibernation, the lsof session cleanup and its signals, and the `data/flood-backoff` and `data/last_connected` files go. Never deletes the session's `-journal` file |
| `bridges/telegram/inbound.py` | the head of `handler` in `bridge/telegram_bridge.py`; `bridge/media.py` (`get_media_type`, `compute_media_timeout`, `download_media`); `_download_media_with_retry`; `bridge/context.py` (`fetch_reply_chain`, `media_descriptor`) | Media typing; a size-scaled timeout with one retry at twice the leash; the reply-chain walk (20 hops, cycle stop) | The handler builds one `Inbound` and ends at `intake.receive`, then marks the message read. Redis dedup, the replay cursor, `/update`, project routing, screening, and storage go. Text is `message.message`, never Telethon's rendered `.text`. Outgoing and service messages, and messages from the account itself, are skipped. The timeout is `max(10, 5 + MB)` seconds with no ceiling. Files are named by sha256. A message in a forum topic has `topic_id` set and `reply_to` None unless it replies to a message other than the topic's root. `thread` entries are `{id, text, attachments}`, attachments listed as `skipped: "earlier message"`. Transcription and image description go |
| `bridges/telegram/gap.py` | `bridge/history_fetch.py` | Backward paging that accepts only strictly older ids and stops on a short page | Pages to a floor (below), receives ids `intake.recorded` does not list, oldest first, through the same path as the handler; no per-chat ceiling |
| `bridges/telegram/send.py` | `_send_queued_message` in `bridge/telegram_relay.py`; `_find_already_sent_poll` | The scan of the account's own messages in a chat, newest first; two matches adopt nothing | The `telegram.send_message` perform and lookup, and the notice send. One attempt per message. Text is split, then files are sent as documents. `random_id` per part. Voice notes, albums, custom emoji, markdown, the oversized-as-file path, and dead letters go |
| `bridges/telegram/bridge.py` | the body of `main()` in `bridge/telegram_bridge.py` | Graceful shutdown on SIGTERM | `TelegramBridge`: `run(outbox)` connects, runs the gap fill, registers the handler, and iterates the outbox. On SIGTERM an in-flight perform and its outcome finish before exit |
| `bridges/telegram/peer.py` | `utils/peer.py` | `numeric_peer`, `deliverable_telegram_peer` | The docstring about import cost goes |
| `bridges/telegram/login.py` | `scripts/telegram_login.py` | The code and two-factor flow; the existing-session check | Reads the API id and hash from the key file; asks for the phone number and the password (through `getpass`) and stores neither; writes the session into the kernel key directory, mode 600. Prints the signed-in name and user id, and no part of the API hash (`main` prints its last four characters). `--test-dc` signs a test account into a session under the test's temporary directory |
| `bridges/telegram/__main__.py` | none | | `run`, `login`, `keys` (copies `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from the vault `.env` into `telegram-keys` in the kernel key directory with `credentials.copy_keys`, printing each name with `written`, `kept`, or `missing`), and `--plist` |

The session is `telegram.session` and the keys `telegram-keys`, both in the
directory holding `settings.pg_passfile`, derived in `bridges/telegram/`,
so the sandbox deny on that directory covers them.

## Behaviour in detail

**Sending.** Text is split so each part is at most 4,096 UTF-16 code
units, Telegram's count, breaking at the last newline before the limit,
else the last space, else at the limit; core's check by characters is the
same or stricter. The first part carries the reply target; every part
carries the topic. Then each file is sent as a document named by its
`path`'s base name, from the bytes hashed. Part `n` has `random_id` from
the first 8 bytes of SHA-256 of `key:n`, a signed int64, never zero. The
result is `{"sent": [...]}`, one entry per message, in order.

**What each failure means.**

| What happened | Perform raises | The broker writes |
|---|---|---|
| Telegram answered with an error (flood wait, write forbidden, peer invalid) | that error, with the seconds for a flood | `failed`, after `lookup` finds nothing |
| Not connected; nothing was written to the socket | a connection error | `failed`, after `lookup` |
| The connection was lost after a request was written and before its answer | `broker.Unknown` | nothing; the outbox's reconcile settles it |
| `RandomIdDuplicate` | nothing: perform calls `lookup` and returns its result, or raises `broker.Unknown` if the scan misses | `done`, or nothing |
| Killed mid-perform | | nothing; reconcile settles it |

`random_id` is defence in depth for effects: the broker never performs one
effect twice. It is the main mechanism for notices, which the outbox
yields again until marked sent.

**Lookup** (`lookup(action, key, since)`, `since` the intent's `at` from
`bridge.serve`, on a release and on reconcile alike). Scan the account's own messages
in the target chat, newest first, down to those dated before `since` less
a clock margin of 60 seconds, for skew between this Mac's clock and
Telegram's dates. Skip ids in `intake.claimed`. Compare texts after
Telegram's trim of leading and trailing whitespace, with the same reply
target and topic; a file by its document name and size. Every part found
once: the result. None: None. Any part with two matches, or some parts
found and others not: `broker.Unknown`, so nothing is concluded and the
effect stays in flight for Tom to see.

**Notices.** For each `NoticeDue`, first scan as above from `item.at` for
a message carrying the notice's short id; if found, `outbox.sent`.
Otherwise send to `item.chat_id` with the reply target, `random_id` from
the notice id, and on success `outbox.sent`. On `RandomIdDuplicate` with
no message found by the scan, the notice is not on screen; it is sent
again with `random_id` from the notice id and the attempt count, so Tom
sees it. A failing notice is yielded again on later wakes; its reason is
logged once per notice.

**Gap fill.** Runs on each connect and on every outbox wake, per owned
chat. Each pass pages back from the newest message, receives every id
`intake.recorded` does not list, and stops at a message dated before the
pass's floor. During a connection the floor is the previous pass's start
less the clock margin. The first pass after connecting stops at the lower
of `intake.highest` and that message's date less `serve_tick_s` and the
margin. A chat with no rows starts at the connect time. Media is
downloaded only for ids not recorded.

**Flood waits.** A flood wait on a request is held in memory; later
requests wait it out. A restart forgets it, and Telegram answers the next
request with the remaining wait.

## Tech debt absorbed

- **#3550 and #3095**: polls dropped. Telegram.md loses "Questions as
  polls", the `telegram.send_poll` row, `kind: vote`, and the `vote` field.
- **#2652**: the inbound record carries the forum topic id: in a topic,
  `reply_to_top_id`, or the reply target when it is the topic's root;
  none in General.
- **#3269**: gap fill from the ledger on connect and on every tick; no
  local cursor.
- **#3589**: every send passes the broker or is a notice the kernel wrote.
- `flood_sleep_threshold` set to 0, so Telethon never retries on its own.
- Plain text sends, so what Tom approves is what renders.
- `main`'s login printing the last four characters of the API hash.
- `main`'s session-lock cleanup signalling any process `lsof` names.
- `main`'s 120 s ceiling on the media download timeout, which fails any
  file over about 115 MB on a link slower than about 1 MB/s.
- Telegram.md's "secrets live in Keychain" against machine.md's kernel key
  directory: the docs say the key directory.

## Left out

- Routing, the drafter, the promise gate, catch-up and reconciler state,
  hibernation, `/update`, reactions, dead letters, polls.
- Edits, deletions, and reactions inbound.
- Voice notes, albums, and captions outbound.
- Transcribing voice notes and describing images.
- Inline buttons: a user account cannot send them; a tap is a reply of
  `approve`.
- Standing grants for any chat (milestone 2's Leaves out).
- A bot account.

## Tests

Real Postgres (the test database, never `valor_rebuild`), the emulator as
its own process, and the bridge in `tests/telegram_child.py`, a child
process the test starts and kills by its pid. No mocks inside the bridge.

`tests/test_telegram_inbound.py`
- `message.message` with Markdown-looking characters and entities arrives
  verbatim; `.text` is never read.
- Outgoing messages, service messages, Valor's own messages, and messages
  from an unowned chat write nothing.
- A message from someone other than Tom in an owned chat is recorded with
  that sender id.
- Topic: a message in a topic has `topic_id` and `reply_to` None; a reply
  inside a topic has both; a message in General has neither.
- A sender file name of `../../x.sh` lands as `inbound_dir/telegram/<sha256>`.
- A 500 MB declared size gets a timeout above 500 s; a download that times
  out twice is listed with `skipped` and its reason; a non-timeout error is
  not retried.
- Reply chain: a cycle stops, a deleted ancestor stops the walk, the walk
  stops at 20 hops, ancestors carry `{id, text, attachments}` with nothing
  downloaded.
- An album of three photos is three records sharing `headers["grouped_id"]`.

`tests/test_telegram_gap.py`
- Messages sent while the bridge was down are received once, oldest first,
  across three pages, in a chat whose ids have gaps.
- A chat with no rows backfills nothing.
- The live handler and the gap fill delivering one message at once write
  one row.
- The emulator drops the live update for 104 and delivers 105: 104 is
  recorded within one tick, and 105's media is not downloaded again.
- Killed after 105 was received and before a tick covered a dropped 104:
  the first pass after restart records 104.
- A receive that fails with Postgres stopped drops the connection; after
  Postgres returns, the reconnect's pass records the message.

`tests/test_telegram_send.py`
- `random_id` is the same for the same key and part and differs for two
  effects with identical payloads in one task.
- Sent text carries no parse mode and no link preview; the reply target
  and topic are honored; the result carries one `sent` entry per message.
- 4,096 UTF-16 units go as one message; 4,097 as two whose joined text is
  the payload; 2,049 astral-plane emoji as two; a split falls on a newline
  when there is one.
- A file whose bytes changed after approval raises before anything is
  sent; a file is sent from the bytes hashed.
- A flood wait on send writes `failed` carrying the seconds, and the next
  request waits it out.
- The emulator accepts a send and drops the connection before replying:
  `broker.Unknown`, no outcome, and the next tick's reconcile writes `done`
  with one message on screen.
- `RandomIdDuplicate` on an effect: perform returns the scanned message.
- Lookup: a match dated before `since` less the margin is not adopted; a
  claimed id is skipped; a payload with a trailing newline matches the
  trimmed message; a sent message older than a later claimed one is found;
  two unclaimed matches, or a split send half found, give `broker.Unknown`
  and nothing is written.

`tests/test_telegram_outbox.py`
- A notice goes to the row's `chat_id` and `notice.sent` is recorded
  through `outbox.sent`; killed between send and `sent`, the short-id scan
  finds it on restart and nothing is sent again.
- `RandomIdDuplicate` on a notice with a scan miss: one notice on screen
  afterwards, and `notice.sent` recorded.
- A notice failing on every attempt logs its reason once.
- A released effect on a stopped task is refused, recorded once, and
  nothing is sent.
- Effects of other owners (`push_branch`, `email.send`) are left alone.

`tests/test_telegram_crash.py`
- Killed at the pause point after the emulator accepted a send: restart,
  `done` (reconciled), one message.
- Killed after the intent and before the send, restarted within a second,
  with a settle time of 5 s: the effect is still in flight after the first
  tick, `failed` on a tick after 5 s, zero messages.
- Killed after `receive` committed and before the read acknowledgement:
  the replay lands once.
- SIGTERM during a perform: the message and its outcome are both recorded
  before exit.

`tests/test_telegram_bridge.py`
- A second `run` waits on `serve`'s lock while the first keeps serving,
  and no process receives a signal.
- `login` and `keys` output contains no 4-character substring of a fake
  API hash; `keys` writes mode 600.
- `peer.py`'s cases from `main`'s tests, kept.

`tests/test_telegram_pipeline.py` (with 2.1's kernel)
- An emulated message from Tom in the operator group starts a task;
  `status` shows metered spending.
- A question notice, Tom's emulated reply, `question.answered` with
  `via: "telegram"` and `role_played: false`; the next run resumes.
- A held `push_branch` effect notice: `Approve.` binds as a steer with its
  notice; `approve` records `approval.granted` and the push reaches the
  local bare origin, with no `release.requested`.
- `approve` in reply to the delivered notice binds as feedback.
- `stop` in reply to a notice records `task.stopped`, and a released send
  of that task is then refused.

Live: `tests/test_live_telegram_dc.py` (test servers, run by the lead
session before the merge) and `tests/test_live_telegram_window.py`
(Valor's account; skipped unless `VALOR_TELEGRAM_WINDOW=1`, set only by
Tom in a window). The window test runs `tests/telegram_child.py` around
the real `wire.py`, pausing after `SendMessageRequest` returns, a pause
only the test child has.

## Files it changes

- New: `bridges/telegram/__init__.py`, `__main__.py`, `wire.py`,
  `inbound.py`, `gap.py`, `send.py`, `bridge.py`, `peer.py`, `login.py`.
- New: `tests/telegram_emulator.py`, `tests/telegram_child.py`, and the
  test files above.
- `pyproject.toml`, `uv.lock`: `telethon`, pinned.
- `docs/bridges/telegram.md`: status, polls removed, secrets in the kernel
  key directory, sends, lookup, notices, and gap fill as built, the
  conformance tests naming the emulator and the test servers.
- `docs/machine.md`: the secrets table's Telegram row, and the measured
  RSS after the window.
- `docs/tech-stack.md`: Telethon chosen for the Telegram bridge; the
  secrets row.
- `bridges/README.md`, `tests/README.md`: entry points.

## Rollout

1. On the build Mac, outside a turn: `uv sync`, then
   `python -m bridges.telegram keys`. The lead session runs the emulator
   suite and `tests/test_live_telegram_dc.py`; both are green before the
   merge.
2. Merge on Tom's tap, after 2.1 and 1.4d.
3. The operator group "Valor rebuild" exists, holding Tom and Valor's
   account. Its title contains no group name a `main` project lists (never
   "Eng: Valor ..."), since `main` matches groups by substring. Nobody
   sends `/update` in it, since `main`'s bridges on other Macs run it from
   any group. It is listed in `projects/valor.toml` `chats` with this
   machine as `machine`, and `operator_chat` is its id, as 2.1's rollout
   sets.

The test window, on the build Mac:

4. Disable the running system, in its checkout, by label, never by
   process pattern: `./scripts/valor-service.sh worker-disable`,
   `./scripts/valor-service.sh email-disable`, then
   `launchctl disable gui/$(id -u)/<prefix>.bridge-watchdog`,
   `launchctl disable gui/$(id -u)/<prefix>.bridge`,
   `launchctl disable gui/$(id -u)/<prefix>.update` (it restarts
   services), and `./scripts/valor-service.sh stop`.
   `launchctl print gui/$(id -u)/<prefix>.bridge` shows it not running.
5. Tom runs `python -m bridges.telegram login` and types the code and his
   password. The code arrives as a message from 777000 in Valor's other
   sessions, including `main`'s bridges on other Macs.
6. Load the job `python -m bridges.telegram --plist` prints, label
   `com.valor.kernel.telegram`, with `launchctl bootstrap gui/$(id -u)`.
7. In the operator group: a message from Tom starts a task whose push goes
   to a local bare origin, and `status` shows its spending; its question
   reaches Tom and his reply binds; `approve` in reply to the push's
   effect notice performs the push; `approve` in reply to the delivered
   notice is feedback.
8. `launchctl bootout gui/$(id -u)/com.valor.kernel.telegram`; Tom runs
   `VALOR_LIVE=1 VALOR_TELEGRAM_WINDOW=1 pytest
   tests/test_live_telegram_window.py`, which kills its own child by pid
   at the pause and checks one message on screen and a reconciled `done`;
   then bootstrap the job again.
9. `launchctl bootout gui/$(id -u)/com.valor.kernel.telegram`, so the
   new bridge is live only during the window. Enable the running system:
   `launchctl enable` for the bridge, watchdog, and update labels,
   `./scripts/valor-service.sh start`, `worker-enable` then
   `worker-start`, `email-enable` then `email-start`. `main` does not
   read the operator group, so it replays none of the window's messages.
10. RSS after a day connected is a day-long window of its own: steps 4
    and 6, then after a day `ps -o rss= -p <pid>`, the pid from
    `launchctl print gui/$(id -u)/com.valor.kernel.telegram`, recorded in
    machine.md; then step 9.
11. The results go into this file's Done section and the task's ledger.

## Decided by default

- **The bridge's session is its own login**, a new authorized device on
  Valor's account that Tom signs in during the window, not a copy of the
  running bridge's session: two processes must never share one session.
- **Valor's API id and hash are used against Telegram's test servers**,
  with test accounts and no Valor session. The test servers touch no real
  chat, and the choice is reversible. `keys` copies them from the vault
  into the key directory, printing no value; the lead session runs the
  suite outside a turn.
- **The bridge is live only during test windows**, as valor-rebuild.md
  says of the new bridges, so the day-long RSS measurement is a window of
  its own.
- **Secrets in the kernel key directory**, as machine.md decides for
  kernel-held secrets: the API id, hash, and session. Telegram.md,
  machine.md, and tech-stack.md are made to say so.
- **The operator group** is "Valor rebuild", listed in
  `projects/valor.toml` with `machine` (the lead's answer).
- **The bridge owns reconnect** (`auto_reconnect=False`, `catch_up=False`),
  so every connect runs the gap fill.
- **`sequential_updates=True`**, so one message's failed receive cannot
  commit a later one first.
- **No connect attempt count.** launchd restarts a process that exits; the
  backoff stays.
- **The clock margin is 60 seconds**, for skew between the Mac's clock and
  Telegram's message dates; it widens a scan and stops nothing.
- **Split at a newline or space before the limit**, so a split message
  reads whole.
- **Files after text**, as documents with no caption.
- **Link previews off**: what renders is the approved text.
- **No backfill for a chat with no rows**: a first connect does not turn a
  chat's history into tasks.
- **Mark read after commit**, as telegram.md says.
- **The emulator is a local server process**, so a kill of the bridge
  leaves the server holding what it accepted.

## Questions for Tom

None. The one thing that needs Tom is signing the session in, rollout
step 5.
