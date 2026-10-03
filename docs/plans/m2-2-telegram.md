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
every message in the chats it reads to `intake.receive`, performs
`telegram.send_message` effects the broker releases, and sends operator
notices to Tom's chat. It conforms to the port that task 2.1 builds in
`core/`; the names it assumes from that port are listed under "Port
assumed" so the two plans can be reconciled before either is built.

It serves Mission items 1 and 6 (Tom gives work and taps approvals where
he already is) and the constraint "Bounded authority, metered spending"
(nothing leaves under Valor's name without passing the broker).

## Stakes

`critique_rounds: 1`, `review_rounds: 2`. The bridge sends to real people
under Valor's name and holds the account's session. It changes one kernel
file (`core/settings.py`, new fields), so its merge waits its turn behind
other tasks that change kernel files.

## Port assumed

From 2.1 as [valor-rebuild.md](valor-rebuild.md) describes it and
[bridges/telegram.md](../bridges/telegram.md) specifies it. Where 2.1's
plan names a thing differently, this plan takes 2.1's name.

| Name | What 2.2 relies on |
|---|---|
| `core.intake.Inbound` | The record in telegram.md's field table, with `headers` free for channel facts; no `vote` field and no `kind: vote` (polls are dropped) |
| `core.intake.receive(conn, inbound)` | Appends `message.received` on the `telegram` channel stream and returns after commit; a replay of the same `(channel, chat_id, message_id)` is absorbed by the unique index and returns without error |
| `core.intake.highest_message_id(conn, channel, chat_id)` | The largest `message_id` received in a chat, or None; the gap fill's floor |
| `core.bridge.Bridge`, `ChannelLimits` | The protocol in telegram.md, "The port in code" |
| `core.outbox` | `pending(conn, action_types)`: effects of those types that carry a `release.requested` row, an unused approval, and no intent; `unsent_notices(conn, channel)`: `notice.requested` rows with no `notice.sent`; the notification channel name (assumed `valor_outbox`) the kernel notifies on when it writes either row |
| `release.requested` | The row the kernel writes when Tom's `approve` reply or `core release` asks for a held effect to be performed; the owning bridge performs it |
| `notice.requested`, `notice.sent` | `notice.requested` carries `notice_id`, `text`, and the record it concerns, and names no recipient; `notice.sent` carries `notice_id`, `chat_id`, `message_id` |
| `core.broker.Performer` with awaitable `perform` and `lookup` | 1.4 made the broker's perform awaitable; a performer is registered per process |
| The broker's idempotency key carries the effect id | 2.1 absorbs this; `random_id` is derived from the key, so two identical sends in one task get two `random_id`s |
| `core.broker.reconcile` for any action type, and the restart sweep | The bridge calls the sweep for its own action types on start |
| Binding in `core/` | A reply from Tom's sender id to a notice binds to the record it carries (`question.answered`, `feedback.given`, `approval.granted` plus `release.requested` on exactly `approve`, `task.stopped` on exactly `stop`), each with `via: "telegram"` and `role_played: false`; any other message from Tom starts a task. If 2.1's plan does not carry binding, the lead moves it into one of the two plans before either builds |
| `core.db.connect` as `valor_kernel` through the password file | The bridge is a kernel process with its own launchd job |

## Done, as evidence

### Shown now, with the Telegram emulator and the local test server

The emulator is `tests/telegram_emulator.py`: a local HTTP server, run as
its own process, holding chats, per-chat message id sequences, the
account's own messages, `random_id` duplicate detection, injected flood
waits, disconnects, and a pause point after a send is accepted. The bridge
reaches it through the same narrow wire interface it uses for Telethon
(`bridges/telegram/wire.py`), passed in by the test's child process, so no
production setting selects it. Postgres is the real test database.

- **Receipt is idempotent and lossless.** A message killed between commit
  and acknowledgement lands once after restart; the live handler and the
  gap fill receiving the same message at once write one row; a receive
  that fails (Postgres down) drops the connection, and the gap fill on
  reconnect records the message.
- **Gap fill.** After the bridge was down, every message in each read
  chat above `highest_message_id` is received once, oldest first, across
  more than one page; a chat with no rows backfills nothing.
- **An inbound message from Tom starts a task** through 2.1's kernel, and
  `python -m core status` shows its metered spending (the judge call
  answered by the local judgement upstream).
- **A question reaches Tom and his reply binds.** A `notice.requested`
  goes to the operator chat only, `notice.sent` records its message id,
  and an emulated reply from Tom's sender id records `question.answered`
  with `via: "telegram"`, `role_played: false`.
- **A delivery card's tap releases a held push.** A reply of exactly
  `approve` to the card records `approval.granted` and `release.requested`;
  the kernel releases the push to the task's local bare origin. A reply of
  `Approve.` or `ok approve` leaves it held.
- **A forced crash between intent and outcome does not send twice.** The
  child is killed by its own pid at the emulator's pause point, after the
  send was accepted and before the outcome; on restart the sweep's
  `lookup` finds the message and writes `done`, reconciled, and the
  emulator holds one message. Killed before the send was accepted, the
  effect settles `failed` after `reconcile_after_s` and nothing is sent.
- **A `telegram.send_message` sends verbatim**: plain text with no parse
  mode and no link preview, the reply target honored, the outcome carrying
  `chat_id` and `message_id`; over the limit, a `.txt` file holding the
  same bytes.
- **The inbound record carries the forum topic id** (#2652).

### Shown on Telegram's test servers, with test accounts

Telegram runs test data centres with their own test phone numbers
(`99966XYYYY`, sign-in code fixed by the number). No Valor session is
used. With `VALOR_LIVE=1` and `VALOR_TELEGRAM_TEST_DC=1`,
`tests/test_live_telegram_dc.py` shows, through the real Telethon wire:

- a repeated `random_id` from a user account is refused by Telegram
  rather than delivered twice, and the performer reads that as sent (the
  gap telegram.md names); if Telegram delivers again instead, the test
  says so and `lookup` alone reconciles, as telegram.md states;
- send, `lookup`, gap fill, and media download work against MTProto.

This needs Valor's API id and hash in the kernel key directory (question 4).

### Waiting for Tom's test window, on Valor's real account

As valor-rebuild.md 2.2 states them, in the operator chat:

- an inbound message starts a task with its spending metered and shown;
- a question reaches Tom and his reply binds to it;
- a delivery card's tap releases a held push;
- a forced crash between intent and outcome does not send twice, run by
  `tests/test_live_telegram_window.py`, which only Tom starts;
- the bridge's RSS after a day connected is measured and recorded in
  [machine.md](../machine.md).

## Threat model

What others control: everything inbound. Anyone who can message Valor's
account controls the text, the sender's display name, file names, media
bytes, reply targets, and the ancestors a reply chain fetches. Telegram
(or the emulator) controls what `lookup` and the gap fill read back. A
turn controls the text of the effects it requests and of the questions
and deliveries the kernel renders into notices.

What the bridge must never do with any of it:

- Act on inbound text. It writes facts to `message.received` and decides
  nothing; Tom's identity is his numeric sender id, never a name.
- Build a path from a sender's file name. A download lands at
  `<media dir>/<chat>-<message id><extension>`, the extension cut to
  lowercase letters and digits.
- Send to anyone the broker did not release, or send a notice anywhere
  but the operator chat in settings. A notice row names no recipient, and
  a field on it that does is ignored.
- Change what Tom approved: no parse mode, no link preview, no trimming;
  the oversized path keeps the bytes.
- Conclude `done` from anything but one unclaimed own message matching
  the payload exactly; two matches conclude nothing.
- Let the session reach a turn, a log, or a ledger row. The session file
  and the API id and hash live in the kernel key directory, which every
  sandbox profile denies; nothing prints any part of the hash.

## Per file, from `main`

Read with `git show origin/main:<path>`; nothing is imported from it.
About 700 lines kept, 600 adapted, the rest of `bridge/` (about 23,500
lines) not carried.

| New file | Source on `main` | Kept | What changes |
|---|---|---|---|
| `bridges/telegram/wire.py` | `bridge/telegram_bridge.py` (`main`'s client construction and connect loop), `bridge/telegram_relay.py` (`_send_queued_message`) | Telethon client setup; connect with exponential backoff and jitter; flood wait on connect honored | The only module that imports Telethon. `flood_sleep_threshold=0`, `catch_up=False`, `auto_reconnect=False`: the bridge owns reconnect, so each connect runs the gap fill. No attempt count: the loop backs off up to 256 s and keeps trying. Sends are raw `SendMessageRequest` and `SendMediaRequest` with a given `random_id`, `no_webpage=True`, no parse mode. The session path, API id, and hash come from the kernel key directory. Sentry, liveness, hibernation, and log formatting go |
| `bridges/telegram/lock.py` | `_cleanup_session_locks` in `bridge/telegram_bridge.py` | The rule never to delete the session's `-journal`, `-wal`, or `-shm` files | Killing whatever `lsof` says holds the session goes. The bridge takes an exclusive `flock` on `<session>.lock` for its life; a second process exits with code 3, naming the holder's pid, and signals nothing |
| `bridges/telegram/inbound.py` | the head of `handler` in `bridge/telegram_bridge.py`; `bridge/media.py` (`get_media_type`, `compute_media_timeout`, `download_media`); `_download_media_with_retry`; `bridge/context.py` (`fetch_reply_chain`, `media_descriptor`, `telegram_media_descriptor`) | Media typing, size-scaled timeout with one retry at twice the leash, the descriptor shape, the reply-chain walk (20 hops, cycle stop) | The handler builds one `Inbound` and ends at `intake.receive`, then marks the message read; the four layers of Redis dedup, the stale-replay cursor, `/update`, project lookup, shadow routing, injection screening, and storage go. Text is `message.message`, the raw string, never Telethon's rendered `.text`. Outgoing and service messages are skipped. `headers` carries `topic_id` and `grouped_id`. Ancestors in `thread` carry media descriptors without downloading; their files, if received, are in their own `message.received` rows. Transcription and image description go |
| `bridges/telegram/gap.py` | `bridge/history_fetch.py` | Backward paging that accepts only strictly older ids and stops on a short page | Pages down to `highest_message_id` instead of a date cutoff, with no per-chat ceiling, and hands messages to the same intake path oldest first |
| `bridges/telegram/send.py` | `_send_queued_message`, `_maybe_send_oversized_text_as_file` in `bridge/telegram_relay.py`; `_find_already_sent_poll` | The oversized-as-file path; the scan of the account's own messages in a chat with "two matches adopt nothing" | Becomes the `telegram.send_message` performer (`act`) and the notice sender. One attempt; flood waits, network errors, and refusals raise, and the broker writes the failed outcome. `random_id` is the first 8 bytes of SHA-256 of the key as a signed int64, never zero. `RandomIdDuplicate` is read as sent and answered by `lookup`. The `.txt` file is named from the key's digest, so `lookup` matches it exactly. Voice notes, albums, custom emoji, the `_file_sent` marker, markdown, and dead letters go |
| `bridges/telegram/bridge.py` | the outbox loop in `bridge/telegram_relay.py` (`process_outbox`, `relay_loop`) and the body of `main()` in `bridge/telegram_bridge.py` | Graceful shutdown on SIGTERM | The Redis outbox loop becomes LISTEN on the outbox channel plus a drain on start and on every database reconnect: `broker.release` for each pending effect of its action type, and each unsent notice. Startup runs the sweep for its action types, then connects |
| `bridges/telegram/peer.py` | `utils/peer.py` | `numeric_peer`, `deliverable_telegram_peer` as they are | The module docstring about import cost goes |
| `bridges/telegram/login.py` | `scripts/telegram_login.py` | The interactive code and two-factor flow; the existing-session check | Reads the API id and hash from the kernel key file; asks for the phone number and the password at the prompt (the password through `getpass`) and stores neither; writes the session into the kernel key directory, mode 600. Prints the signed-in name and user id, and no part of the API hash (`main` prints its last four characters). `--test-dc` signs a test account into a session under the test's temporary directory |
| `bridges/telegram/__main__.py` | none | | `run`, `login`, `keys` (copies `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from the vault `.env` into `telegram-keys` in the kernel key directory through `credentials.copy_keys`, printing each name with `written`, `kept`, or `missing`), and `plist` (prints the launchd job) |

## Behaviour in detail

**Chats it reads.** The operator chat (`telegram_operator_chat`) and any
chat ids in `telegram_chats`, default empty. An event from any other chat
is dropped before anything is recorded, and the gap fill visits only these
chats. This is how the bridge keeps to single-machine ownership: it reads
no chat unless listed, and the operator chat is one no other Mac's bridge
owns (question 1).

**Messages from others.** In a read chat, a message from anyone is
recorded with its sender id; the kernel uses only Tom's to start or bind
work, as telegram.md, "Other people", states.

**Lookup.** For a dangling intent, scan the account's own messages in the
target chat, newest first, and stop at the newest one whose message id is
already recorded in an `effect.outcome` or `notice.sent` row for that chat;
everything older was sent before. Among the unclaimed ones, a match is a
text equal to the payload's text with the same reply target, or, for an
oversized send, the document whose name is the key's digest. One match:
`{chat_id, message_id}`. None: `None`. More than one: `broker.Unknown`, so
nothing is concluded and the effect stays in flight for Tom to see.

**Notices.** For each unsent notice, the bridge first runs the same scan
for the notice's text (a crash between send and `notice.sent` leaves it
already on screen) and records `notice.sent` if found; otherwise it sends
with a `random_id` derived from the notice id. A notice that fails stays
unsent and is sent at the next drain after any flood wait has passed:
notices are the approval surface and need no approval, so this is not a
resend of an approved effect.

**Flood waits.** A flood wait anywhere is written as a `telegram.flood_wait`
row (`until`) on the `telegram` channel stream. Before connecting and before
each request the bridge waits out the latest one, so a restart by launchd
mid-wait does not hit Telegram again early. No local file holds it.

**Length.** `ChannelLimits.max_text` is 4,096, measured in UTF-16 code
units, as Telegram counts. Over it, the performer sends a `.txt` file of
the same bytes with the caption "The full message is in the attached
file."

**Where things live.** The session at `telegram.session` and the keys at
`telegram-keys`, both in the kernel key directory (mode 600, directory
700). Media under `telegram_media_dir` (default
`~/Library/Application Support/valor-kernel/telegram-media`). Logs under
`settings.log_dir`. Copying an attachment into a task's workspace, where a
turn can read it, is `core/`'s.

## Settings it adds (`core/settings.py`)

| Field | Env | Default |
|---|---|---|
| `telegram_operator_id` | `VALOR_TELEGRAM_OPERATOR_ID` | none; `run` refuses to start without it, naming the variable |
| `telegram_operator_chat` | `VALOR_TELEGRAM_OPERATOR_CHAT` | none; same |
| `telegram_chats` | `VALOR_TELEGRAM_CHATS` | empty |
| `telegram_media_dir` | `VALOR_TELEGRAM_MEDIA` | as above |
| `telegram_session`, `telegram_keyfile` | none | derived from `pg_passfile`'s directory, like `judgement_keyfile`, so the sandbox deny cannot drift from them |
| `telegram_test_dc` | `VALOR_TELEGRAM_TEST_DC` | off |

`python -m core settings` prints the paths and ids; none is a secret.

## Tech debt absorbed

- **#3550 and #3095**: polls dropped, per Tom's ruling. Telegram.md loses
  "Questions as polls", the `telegram.send_poll` row, `kind: vote`, and the
  `vote` field.
- **#2652**: the inbound record carries the forum topic id: `reply_to_top_id`
  when the message is in a topic, the reply target when it replies to the
  topic's root, and none in a forum's General topic.
- **#3269**: gap fill on every connect, from the ledger; no local cursor.
- **#3589**: every send passes the broker, and notices go only to the
  operator chat.
- `flood_sleep_threshold` set to 0, so Telethon never retries on its own.
- Plain text sends, so what Tom approves is what renders.
- `main`'s login printing the last four characters of the API hash.
- `main`'s session-lock cleanup signalling any process `lsof` names.
- `main`'s local state files (`data/flood-backoff`, `data/last_connected`):
  the flood wait is a ledger row and the gap fill needs no timestamp.
- Telegram.md's "secrets live in Keychain" against machine.md's kernel key
  directory: the docs are made to say the key directory (question 3).

## Left out

- Routing, the drafter, the promise gate, catch-up and reconciler state,
  hibernation, `/update`, reactions, dead letters, polls (as valor-rebuild.md
  lists).
- Edits, deletions, and reactions inbound (not part of the port).
- Files, voice notes, and albums in an outbound payload. The payload is
  `text` and `reply_to`; a file is added when a task needs to send one
  twice (Mission item 5). Only the oversized-text file is sent.
- Transcribing voice notes and describing images.
- Reading chat ownership from `main`'s `projects.json`. Chats are listed
  in settings.
- Inline buttons: a user account cannot send them, so a tap is a reply of
  `approve`.
- Standing grants for any chat (milestone 2's Leaves out).
- A bot account.

## Tests

Real Postgres (the test database, never `valor_rebuild`), the emulator as
its own process, and the bridge in a child process the test starts and
kills by pid. No mocks inside the bridge.

`tests/test_telegram_inbound.py`
- `message.message` with Markdown-looking characters and entities arrives
  verbatim; `.text` is never read.
- Outgoing messages, service messages (joins, pins), and messages from an
  unread chat write nothing.
- A message from someone other than Tom in a read chat is recorded with
  that sender id.
- Topic id: a message in a topic, a reply inside a topic, a message in
  General.
- A sender file name of `../../x.sh` lands inside the media dir as
  `<chat>-<id>.sh`; a name with no extension and a photo with no name.
- A download that times out twice is listed with its reason; a
  non-timeout error is not retried.
- Reply chain: a cycle stops, a deleted ancestor stops the walk, the walk
  stops at 20 hops, ancestors carry descriptors without downloads.
- An album of three photos is three records sharing `grouped_id`.

`tests/test_telegram_gap.py`
- Messages sent while the bridge was down are received once, oldest first,
  across three pages.
- A chat with no `message.received` rows backfills nothing.
- The live handler and the gap fill delivering one message at once write
  one row.
- A receive that fails with Postgres stopped drops the connection; after
  Postgres returns, the reconnect's gap fill records the message.
- A flood wait during the gap fill is waited out and recorded.

`tests/test_telegram_send.py`
- `random_id` is the same for the same key and differs for two effects
  with identical payloads in one task.
- The emulator reporting a duplicate `random_id` yields `done` with the
  first message's id, and one message on screen.
- Sent text carries no parse mode and no link preview; the reply target is
  honored; the outcome carries `chat_id` and `message_id`.
- 4,096 UTF-16 units go as text; 4,097 go as a `.txt` whose SHA-256 equals
  the text's; 2,048 astral-plane emoji (4,096 units) go as text and 2,049
  as a file.
- A flood wait on send writes a failed outcome carrying the seconds, sends
  nothing more, and the next request waits it out.
- `lookup`: a matching message older than the newest claimed one is not
  adopted; two unclaimed matches raise `Unknown` and nothing is written;
  an oversized send is found by its file name.

`tests/test_telegram_outbox.py`
- An approved effect with no `release.requested` is not performed; a
  released one is performed once, whether the drain was woken by NOTIFY or
  by startup.
- A released effect on a stopped task is refused by the broker and nothing
  is sent.
- Effects of other action types (`push_branch`, `merge`, `email.send`) are
  left alone.
- A notice goes to the operator chat even when its row carries a
  `chat_id`; killed between send and `notice.sent`, it is found by the
  scan on restart and not sent again; a notice that hit a flood wait is
  sent at the next drain after the wait.
- A dropped database connection is reconnected and the outbox drained.

`tests/test_telegram_crash.py`
- Killed at the pause point after the emulator accepted a send: restart,
  the sweep writes `done` (reconciled), one message.
- Killed after the intent and before the send: restart, `failed` once the
  intent is older than the settle time (set short in the test), zero
  messages, and no resend.
- Killed after `receive` committed and before the read acknowledgement:
  the replay lands once.

`tests/test_telegram_bridge.py`
- A second bridge process exits with code 3 naming the first's pid; the
  first keeps serving and receives no signal.
- `run` without `telegram_operator_id` or `telegram_operator_chat` exits
  naming the variable.
- `login` and `keys` output contains no 4-character substring of a fake
  API hash; `keys` writes mode 600.
- `peer.py`'s cases from `main`'s tests, kept.

`tests/test_telegram_pipeline.py` (with 2.1's kernel)
- An emulated message from Tom's id in the operator chat starts a task in
  `judge`; `status` shows metered spending.
- A question notice, Tom's emulated reply, `question.answered` with
  `via: "telegram"` and `role_played: false`; the next run resumes.
- An approval card for a held `push_branch`; `Approve.` leaves it held;
  `approve` records `approval.granted` and `release.requested`, and the
  push reaches the local bare origin.
- `stop` in reply to a notice records `task.stopped`, and a released send
  of that task is then refused.

Live: `tests/test_live_telegram_dc.py` (test servers, test accounts) and
`tests/test_live_telegram_window.py` (Valor's account; skipped unless
`VALOR_TELEGRAM_WINDOW=1`, set only by Tom in a window).

## Files it changes

- New: `bridges/telegram/__init__.py`, `__main__.py`, `wire.py`, `lock.py`,
  `inbound.py`, `gap.py`, `send.py`, `bridge.py`, `peer.py`, `login.py`.
- New: `tests/telegram_emulator.py`, `tests/telegram_child.py`, and the
  test files above.
- `core/settings.py`: the fields above.
- `pyproject.toml`, `uv.lock`: `telethon`, pinned.
- `docs/bridges/telegram.md`: status (built), polls removed, secrets in the
  kernel key directory, lookup and notice behaviour as built, the
  conformance tests naming the emulator and the test servers.
- `docs/machine.md`: the Keychain table's Telegram row, the measured RSS
  after the window.
- `docs/tech-stack.md`: Telethon chosen for the Telegram bridge; the
  secrets row.
- `bridges/README.md`, `tests/README.md`, `core/README.md`: entry points
  and settings.

## Rollout

1. Merge on Tom's tap, after any other task changing kernel files that is
   ahead of it.
2. On the build Mac, in the kernel's checkout: `uv sync`, then
   `python -m bridges.telegram keys`.
3. Tom creates the operator group (question 1) and gives its chat id and
   his user id; they go into the launchd job's environment from
   `python -m bridges.telegram plist`, label `com.valor.kernel.telegram`.
4. The emulator suite and, with question 4 answered, the test-server
   suite are green on the merged branch.

The test window, on the build Mac:

5. Disable the running system, in its checkout, by label, never by
   process pattern: `./scripts/valor-service.sh worker-disable`,
   `./scripts/valor-service.sh email-disable`, then for its Telegram
   bridge `launchctl disable gui/$(id -u)/<prefix>.bridge-watchdog`,
   `launchctl disable gui/$(id -u)/<prefix>.bridge`,
   `launchctl disable gui/$(id -u)/<prefix>.update` (its update job
   restarts services), and `./scripts/valor-service.sh stop`.
   `launchctl print gui/$(id -u)/<prefix>.bridge` shows it not running.
6. Tom runs `python -m bridges.telegram login` and types the code and his
   password (question 2).
7. Install and start the job: `launchctl bootstrap gui/$(id -u)` with the
   printed plist.
8. The window's evidence, in order: a message from Tom in the operator
   chat starts a task and `status` shows its spending; the task's question
   reaches him and his reply binds; its delivery card's `approve` releases
   the held push; Tom runs `VALOR_LIVE=1 VALOR_TELEGRAM_WINDOW=1 pytest
   tests/test_live_telegram_window.py`, which kills its own child by pid
   at the pause point and checks one message on screen and a reconciled
   `done`.
9. RSS after a day connected: `ps -o rss= -p <pid>`, the pid from
   `launchctl print gui/$(id -u)/com.valor.kernel.telegram`, recorded in
   machine.md. Under question 5's assumed answer the bridge stays
   connected after step 10 for the day; otherwise the day is its own
   window.
10. Enable the running system: `launchctl enable` for the bridge,
    watchdog, and update labels, `./scripts/valor-service.sh start`,
    `worker-enable` then `worker-start`, `email-enable` then
    `email-start`. Messages in the operator group belong to the new
    system; the running system does not read that group, so it replays
    none of them.
11. The results go into this file's Done section and the task's ledger.

## Decided by default

- **One launchd process per bridge**, separate from the resident kernel,
  so a Telethon fault does not take the kernel down; it connects to
  Postgres as `valor_kernel` like the kernel does.
- **`flock` instead of killing lock holders.** Killing whatever holds the
  session file can kill a process this bridge did not start.
- **The bridge owns reconnect** (`auto_reconnect=False`, `catch_up=False`),
  so every connect runs the gap fill; one recovery path, from the ledger.
- **No connect attempt count.** launchd restarts a process that exits, so a
  count only adds a restart; the backoff stays.
- **Flood waits as a ledger row**, so the bridge's only local state is the
  session and the media directory, as telegram.md states.
- **Outbound payload is text and a reply target.** No task sends files
  yet, and reading a turn-named path is a risk with no need behind it.
- **Link previews off**: what renders is the approved text.
- **Notices are sent again after a failure**; they need no approval, and a
  question Tom never sees costs more than a second attempt.
- **Chats listed in settings**, not read from `projects.json`.
- **No backfill for a chat with no rows**: a first connect does not turn a
  chat's history into tasks.
- **Mark read after commit**, as telegram.md says.
- **Two lookup matches conclude nothing** (`Unknown`), as `main`'s poll
  adoption does.
- **The length limit in UTF-16 code units**, as Telegram counts.
- **The emulator is a local server process**, so a kill of the bridge
  leaves the "server" holding what it accepted, which the crash tests need.

## Questions for Tom

Identity, credential, and intent only. The build proceeds on each assumed
answer.

1. **Which chat is the operator chat?** Assumed: a new group, "Valor
   rebuild", holding only Tom and Valor, listed in no project's config, so
   no running bridge on any Mac reads it and messages there belong to the
   new system alone. The alternative is Tom's DM with Valor, which the
   running system on some Mac reads, so the gap fill would hand the new
   system messages the running one already answered.
2. **Which session does the bridge use?** Assumed: its own login, a new
   authorized device on Valor's account, made by Tom in the window with
   `python -m bridges.telegram login`. Not a copy of the running bridge's
   session file, since two processes must never share one session.
3. **Where do the Telegram API id, hash, and session live?** Assumed: the
   kernel key directory, which every turn's sandbox denies, as machine.md
   says of kernel-held secrets. Telegram.md's "Keychain" is readable by a
   turn through `security`.
4. **May the build use Valor's Telegram API id and hash against Telegram's
   test servers, with test accounts and no Valor session?** Assumed: yes;
   `keys` copies them from the vault into the key directory, printing no
   value. If not, the `random_id` check moves into Tom's window.
5. **May the bridge stay connected outside a window, reading only the
   operator group, for the day-long RSS measurement?** Assumed: yes. It
   reads no chat the running system owns and sends nothing without a tap.
   If not, the measurement is a day-long window of its own.
