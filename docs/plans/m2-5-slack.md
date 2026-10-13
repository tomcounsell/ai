---
tracking: none
slug: m2-5-slack
type: build
status: planned
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# 2.5: the Slack bridge

Task 2.5 of [valor-rebuild.md](valor-rebuild.md), milestone 2. Tom's
request (2026-10-12): a comms module like Telegram, for Slack, leaning on
how other agent systems do it. This task builds `bridges/slack/` to the
bridge port that [bridges/telegram.md](../bridges/telegram.md) owns, and
makes the kernel declare the channel.

The design takes its transport and its hard-won Slack behaviour from two
open systems, read at their heads on 2026-10-12, and its contract from
our port:

- **Hermes Agent** (NousResearch, `plugins/platforms/slack/adapter.py`,
  about 7,600 lines): Socket Mode, the catch-all ack, the subtype
  allowlist, and the download host allowlist.
- **OpenClaw** (`extensions/slack/src`): an event recorded durably before
  it is acked, a dedupe key of channel and `ts`, each message handled as
  its own task, writes never retried except on a 429, and a metadata
  marker on every post that a history scan finds after a crash.

Neither system's scale comes with it. Hermes's pairing flows, Block Kit,
streaming edits, status lines and reactions, and OpenClaw's multi-account
relay, are features for many users and many agents. Valor has one identity
and one owner, and the port says a bridge does I/O and nothing else.

2.5 serves one workspace, `yudame.slack.com`. The keys are stored by team
id, so a second workspace is a second entry and a second connection, not
a new design.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. Like 2.4, the task adds a
channel to `core/intake.py`'s sender verification (who counts as Tom) and
to the kernel's declarations (`DECLARED`, `LIMITS`, `owned`). A mistake
there lets something other than Tom start work. The bridge also sends to
real people under Valor's name. The Slack tokens are this channel's
authentication, the part MTProto plays for Telegram. They add no check,
gate, or review step, so no governance grant is asked.

## Done, as evidence

Against the test database, with `tests/slack_emulator.py` running as its
own process (a Socket Mode WebSocket and the Web API methods the bridge
calls):

- **Receive.** A `message` envelope from Tom (`sender_id` one of
  `VALOR_OPERATOR_SLACK_IDS`) in his DM records one `message.received` on
  the `slack` stream, `verified: true`, and `intake.bind` starts a task
  from it under the project that lists the chat, else `valor`. This holds
  with the operator channel set to Telegram: Tom's DM is owned because
  his Slack ids are. The envelope is acked only after `receive` commits.
- **One record per message.** The same message delivered twice (a Slack
  retry, then a gap fill) lands once. An envelope redelivered while the
  first delivery's receive is still running (a slow download) is acked
  and not processed again, and the file is written once.
- **Valor's own posts.** A send produces no `message.received`, live or
  in gap fill: messages whose `user` is the token's own id (from
  `auth.test` at connect) are dropped.
- **First pass.** A chat with no seen entry and no recorded rows, holding
  old messages, records nothing; its seen `ts` is set to its newest.
- **Gap fill.** With the bridge killed by its pid, the emulator queues
  three channel messages and a thread reply. On restart, gap fill records
  all four and records nothing twice. A thread reply to a notice posted
  three days earlier, made while the bridge was down, is recorded on
  restart and binds to that notice.
- **Projects.** A spec listing `slack:T0ABC/C0DEF` is accepted, the chat
  is in `owned("slack")`, and `_project_for` returns that spec for a
  message in it.
- **Send.** A released `slack.send_message` posts once, carrying the
  marker metadata. With the bridge killed between the intent and the
  outcome, `lookup` finds the post on restart and the send settles as
  done, not resent; the same test passes with the emulator dropping
  metadata, through the text fallback. A send of 9,000 characters leaves
  as three posts, and a kill after the first post finishes with the two
  that are missing. A 429 on the second post is waited out for its
  `Retry-After` and the send finishes with three posts.
- **A file post** settles with the `ts` the bounded `files.info` poll
  reads. With the emulator never sharing the file, the outcome is
  `broker.Unknown` and `lookup` finds the share in history.
- **Replies bind.** A notice posted in a thread rooted at Tom's message,
  then Tom's reply in that thread, records `question.answered` with
  `via: "slack"`. With two notices in one thread, Tom's reply binds to
  the newer. A reply of exactly `stop` stops the task; a near miss steers
  and owes the notice.
- **Binding notices.** A binding notice about Tom's message in a client
  channel goes to the operator channel and chat, not into the client
  channel. The same holds for a Telegram project group; in Tom's own DM
  or the operator chat it goes in reply, as before.
- **Failures.** A 429 on a send's first post is `broker.Failed` with the
  `Retry-After` seconds. A connection cut after the request was written
  is `broker.Unknown`.
- **Downloads.** A file whose `url_private` redirects off Slack's hosts
  is listed as skipped, and the token is never sent there.
- The suite and both ruff checks are green.

Live, in a test window on `yudame.slack.com`, with
`VALOR_OPERATOR_CHANNEL=slack` for the window: `test_live_slack.py`
first, which says whether metadata is kept on a user-token post, so the
window knows which lookup branch runs; a message from a non-whitelisted
DM is not received, and one in an owned channel from any member is
recorded; Tom DMs Valor and a task starts; a question reaches him in
Slack and his thread reply binds to it; a forced crash between the intent
and the outcome does not post twice; and the bridge's RSS after a day
connected is measured.

## Threat model

- **What a turn controls:** a send's payload (text, `thread_ts`, files
  in its workspace) and its target. Any chat Valor's account can reach is
  a target, owned or not: the boundary is the account's access
  ([business.md](../business.md), Business item 8), as for Telegram. A
  reply in a chat the bridge does not receive is not read. The kernel
  sizes the files, and the bridge sends exactly the payload.
- **What a message controls:** its own text and files. These are data;
  the judgement layer reads them and the kernel decides what they may do.
  `sender_id` is `<user_team>/<user>` as Slack's authenticated socket
  asserts it, so it is `verified` the way a Telegram user id is. A
  display name is never used to identify anyone.
- **What the kernel must never do:** read turn-owned state to decide who
  Tom is, or let a turn see the tokens. `slack-keys` lives in the kernel
  key directory, mode 600, which every turn's sandbox denies. The token
  goes only to `slack.com`, `*.slack.com` and `*.slack-files.com`, with
  each redirect checked by hand (Hermes, `adapter.py` 6255-6340). A
  binding notice never carries kernel text into a chat other than Tom's
  own.

## Tom's answers (2026-10-13)

- **Purpose: a teammate in client workspaces.** Valor is a member of
  clients' Slack workspaces under its own name, reading project channels
  and posting there. So the identity is **Valor's own user account
  through a user token (`xoxp`)**, not a bot: one identity, as on
  Telegram. Each workspace creates a small internal app once, with its
  admin's approval.
- **Who may talk to Valor: the same as Telegram.** Some people are
  whitelisted for DMs, each against a project. Otherwise, membership of a
  channel is the whitelist for chat in that channel. In the port's terms:
  - a project's `chats` lists `slack:<team id>/<channel id>` for each
    channel it owns, and the bridge records every member's message there;
  - a whitelisted DM is `slack:<user team>/<user id>` in that project's
    `chats`. A DM's `chat_id` is the other person's `sender_id`, as a
    Telegram DM's is the other person's user id, and the bridge maps the
    IM channel to it;
  - Tom's DMs are owned through `VALOR_OPERATOR_SLACK_IDS`, whatever the
    operator channel;
  - a DM from anyone else, and any channel no project lists, is not
    received.

  What a non-Tom message may then do is the kernel's, and it is the same
  for every channel: today it is recorded as context. Widening that
  belongs to gap G1 in [cutover-data.md](cutover-data.md), and Slack
  follows whatever G1 decides for Telegram, with no Slack-specific rule.
- **First workspace: `yudame.slack.com`.** Valor is already signed in
  there on the desktop app. The live test windows run there.
- **Notices** stay on the operator channel in settings (Telegram).
  `VALOR_OPERATOR_CHANNEL=slack` is supported but not set outside the
  live window. (Decided by default: Tom chose the teammate purpose, not
  "another line to Tom".)

Before the live window, signed in as Valor, at api.slack.com: create the
app inside `yudame` from `python -m bridges.slack manifest`, turn on
Socket Mode, install it to Valor's account, and put `SLACK_APP_TOKEN` and
`SLACK_USER_TOKEN` in the vault `.env`. If the workspace requires app
approval, the yudame admin approves the install.

## What is built

### `core/` (the port's side), about 200 lines

- `channels.py` (new, imports nothing from `core`): the per-channel
  table, one row per channel, read by `bridge.py`, `intake.py`,
  `notices.py`, and `workspace.py`. A row holds: whether a record is
  verified; whether Tom replies there (so `stop` binds); whether replies
  are threaded; the `chats` entry shape and case rule (Telegram as
  written, email lowercased, Slack uppercased, which is how Slack writes
  ids); and where Tom replies, for the email near miss.
- `bridge.py`: `LIMITS["slack"]` (4,000 characters per post, Slack's
  stated guidance for `chat.postMessage`, which truncates past 40,000;
  1 GB per file). `DECLARED["slack.send_message"]`, with `_refuse_slack`
  matching `_refuse_telegram`: the text has the right shape, and the post
  is not empty. `split_text` learns `chars` units.
- `intake.py`:
  - `"slack"` in the table: verified, replies, threaded.
  - `owned("slack")` always adds each of `settings.operator_slack_ids`,
    as `owned("email")` adds Tom's addresses, then the project chats.
  - `_from_operator`: a verified Slack record whose `sender_id` is in
    `operator_slack_ids`.
  - `_replied`: for a threaded channel, when `reply_to` matches no sent
    row, the newest id in the record's `thread` that is in a sent row
    binds instead. Ledger only.
  - `lowest_ts`: the smallest Slack `ts` recorded for a chat, compared
    as text with C collation (a `ts` is ten digits, a dot, and six digits,
    so text order is time order). `lowest` keeps its integer meaning for
    Telegram.
  - `_notice`: a binding notice goes in reply only in Tom's own chat (his
    DM on that channel, or the operator chat); otherwise it goes to the
    operator channel and chat. This holds for every channel.
- `notices.py`: `_to_tom` counts a `slack.send_message` to one of
  `operator_slack_ids`, or to the operator chat when the operator channel
  is Slack.
- `settings.py`: `VALOR_OPERATOR_SLACK_IDS`, a comma list of
  `<team>/<user>` (Tom's ids, one per workspace), and `slack` as an
  operator channel.
- `workspace.py`: project `chats` accept `slack:<team>/<id>`, through
  the table's shape and case rule, in place of the Telegram and email
  special case.

### `bridges/slack/`, about 1,400 lines (Telegram's is about 2,500)

| Module | Does | Taken from |
|---|---|---|
| `wire.py` | The only module importing `slack_sdk`: `SocketModeClient` (aiohttp) and `AsyncWebClient`, with no Bolt. Writes retry 0 times and a 429 surfaces its `Retry-After`; reads retry on 429 and 5xx, bounded. Reconnection is `slack_sdk`'s own session monitor. `auth.test` at connect gives the team id and the token's own user id. `conversations.open` maps a `<team>/<user>` to its IM channel, cached in memory | OpenClaw `client-options.ts` 32-35 |
| `inbound.py` | One `message` event to an `Inbound`, or nothing when its `user` is the token's own. `chat_id` is `<team>/<channel>`, or for an IM the other person's `<user_team>/<user>`. `sender_id` is `<user_team>/<user>`. `message_id` is `ts`, `chat_kind` is `dm` for an IM and `group` otherwise, `topic_id` is the `thread_ts` of a threaded reply, and `reply_to` is the thread root. `thread` holds the root and the replies before this one (`conversations.replies`, capped at 50). Files are fetched from `url_private_download` to a temp file, then renamed into `inbound_dir/slack/` by sha256; a `text/html` answer is listed as skipped ("not signed in"). The subtype allowlist is none, `file_share`, `thread_broadcast`, and `me_message`; edits, deletions, joins, and bot messages are not part of the port | Hermes 4513-4517, 6255-6340 |
| `bridge.py` | `run`: every envelope type is acked, so Slack never disables event delivery. A `message` envelope runs as its own task and is acked once its `intake.receive` commits. A redelivered envelope whose `(chat, ts)` is already in flight in this process is acked and not processed again. If `receive` fails, the envelope is not acked, and seen stays below it, so gap fill takes it. Also the outbox loop and SIGTERM handling, as Telegram does them | OpenClaw `ingress.ts` 166-184 |
| `gap.py` | On every connect and tick, for each owned chat: `conversations.history` from the chat's seen `ts`; then `conversations.replies(oldest=seen)` for each thread with recent activity, which is any parent in that page whose `latest_reply` is newer than seen, the roots of Valor's sends and notices in the chat (from `sent` rows), and the `topic_id`s of messages recorded in the chat, both from the last 14 days. A chat with no seen entry starts from `intake.lowest_ts`; with no rows either, its seen is set to its newest `ts` and nothing is recorded (Telegram's first pass, `bridge.py` 265-273). Seen is held below any receive still in flight. Pages are budgeted per tick at Tier 3, per method (about 50 calls a minute each for history and replies); the pass resumes where it stopped | Ours; Hermes and OpenClaw only catch up lazily |
| `send.py` | `slack.send_message` perform and lookup, and notices. Each post is `chat.postMessage` with `mrkdwn: false`, `unfurl_links: false`, `unfurl_media: false`, `thread_ts`, and metadata `{event_type: "valor_send", event_payload: {key: sha256(broker key), part, parts}}`. A DM target is opened through the cached `conversations.open`. `thread_ts` is the root: a `reply_to` that is a recorded message resolves through its `topic_id`, and one that is Valor's own post through its `sent` row's `thread_ts`. A 429 on a later part is waited out for its `Retry-After` and the send continues. Files go through `files.getUploadURLExternal` and `completeUploadExternal`, then `files.info` is polled, five times two seconds apart, for the share's `ts`; none is `Unknown`. Each `sent` entry is `{channel, chat_id, message_id, thread_ts}` with a DM's `chat_id` as `<team>/<user>`. Lookup is below | OpenClaw `send.ts` 494-830 |
| `state.py` | `slack-seen.json` (the seen `ts` per chat), written whole, flushed, and renamed into place, as Telegram's is | Ours |
| `kernel.py`, `__main__.py` | The port as one object. `run`, `keys` (copies `SLACK_APP_TOKEN` and `SLACK_USER_TOKEN` from the vault into `slack-keys`, under the team id `auth.test` returns), `manifest` (prints the app manifest to paste into api.slack.com), and `--plist`. `run` connects the one workspace in `slack-keys` and refuses to start with more than one entry, since 2.5 serves one | Hermes `slack_cli.py` 36-75 |

**Lookup.** One path, whether or not Slack keeps metadata on a user-token
post. `lookup(action, key, since)` scans the target chat (`conversations.
replies` for a thread, else `conversations.history`) from `since` less a
five minute skew margin, with `include_all_metadata`, keeping only posts
whose `user` is the token's own. It drops every `ts` already in a `sent`
row of another outcome or notice (`intake.claimed`). The expected parts
are `split_text` of the payload, then the files. For each part in order:

1. if any kept post carries metadata, the post whose marker is this key
   and this part;
2. else the earliest kept post after the previous part's `ts` whose text,
   with `&amp;`, `&lt;` and `&gt;` decoded, equals the part and whose
   `thread_ts` equals the payload's root; a file part matches on name
   and size.

All parts found is done with their `ts`. A prefix found means the rest are
sent, then done. A part found after a missing one, or two candidates for
one part, is `Unknown`. Nothing found is None, as on Telegram. A notice
takes the same path, with `since` its request row's `at`. `since` covers
what a record of the newest `ts` before each send did, so there is no
send state file.

**The manifest.** The app is created inside each workspace from the
manifest and never distributed. A distributed app outside the Marketplace
gets one request a minute and 15 messages a page on
`conversations.history` and `conversations.replies`; an internal app keeps
Tier 3, which the gap fill budget assumes. Socket Mode on. User-token
scopes: `channels:history`, `groups:history`, `im:history`,
`mpim:history`, `channels:read`, `groups:read`, `im:read`, `mpim:read`,
`im:write`, `chat:write`, `files:read`, `files:write`, `users:read`. User
events: `message.channels`, `message.groups`, `message.im`,
`message.mpim`.

### Docs

`docs/bridges/slack.md`, in the shape of `email.md`: only what is
particular to Slack. Updates to `bridges/README.md`, the port table in
`telegram.md`, the binding notice rule in `telegram.md`, and
`machine.md`'s RAM line.

## Tech debt absorbed

- `split_text` is Telegram-only today (`core/bridge.py`), and the email
  and local `chats` are special-cased in `workspace.py` and `intake.py`:
  `workspace.py` accepts only `telegram` and `email` entries and
  lowercases everything but Telegram's. Adding a fourth channel moves the
  per-channel facts (verify, replies, threading, the owned ids, `_WHERE`,
  and the shape and case of a `chats` entry) into one table,
  `core/channels.py`, instead of a fourth `if channel ==` in each place.
- A binding notice about a message in a Telegram project group goes in
  reply in that group today, carrying kernel text (a binding error's
  exception) to everyone there. It moves to the operator chat with the
  same rule as Slack.

## Left out

- **Converting markdown to mrkdwn.** Text is sent verbatim with
  `mrkdwn: false`. Slack still stores `&`, `<` and `>` as entities and
  renders `<...>` sequences as links or mentions, so lookup decodes the
  entities before comparing. If persona wants Slack formatting, persona
  renders it in `core/` before the request.
- **Escaping `<!channel>` and `<!here>`.** Hermes escapes them so a bot
  cannot ping a whole channel. With `mrkdwn: false` and no `parse`,
  Slack still turns `<!here>` into a mention. Refusing it would be a new
  check, and no incident has happened, so it is not added. If one
  happens, it comes back as a guard with a grant.
- **Streaming, status, and reactions:** `chat.startStream`,
  `assistant.threads.setStatus`, and acknowledgement reactions. A bridge
  posts no progress of its own (telegram.md, "What the bridge never
  does").
- **Edits, deletions, reactions, slash commands, Block Kit, and
  interactive buttons.** Answers and stops bind by reply text, as on
  every channel.
- **The Events API over HTTP.** It needs a public URL and a signing
  secret. Socket Mode needs only an outbound connection, which suits a
  Mac behind NAT.
- **Pairing and allowlists.** Only Tom's verified ids start work. Anyone
  else's message is thread context, the port's rule for other people.
- **Distributing the app** (OAuth install flow): a distributed app loses
  Tier 3 on history and replies (see the manifest). Each workspace
  creates its own app from the manifest.
- **A second workspace.** 2.5 serves one; `slack-keys` is keyed by team
  id so a second is data, and its connection is later work.
- **A ping watchdog.** Hermes reconnects when pong is stale because
  `is_connected()` can be true on a dead session in its client. The
  aiohttp `SocketModeClient` runs its own session monitor; a watchdog
  comes back only if a test shows `is_connected()` lying under it.

## Tests

- `tests/slack_emulator.py`: a Socket Mode server (`apps.connections.open`
  hands out a `ws://127.0.0.1` URL) that redelivers an unacked envelope
  after 3 seconds, plus `auth.test`, `conversations.open`,
  `chat.postMessage` (storing metadata, or dropping it on request, and
  echoing the post as an event), `conversations.history` and
  `conversations.replies` (with paging, `oldest`, and
  `include_all_metadata`), the two-step upload with a share delayed or
  withheld on request, `files.info`, and `url_private` with redirect,
  slow, and `text/html` modes. It answers 429 on request, on any call
  number, and drops a request after reading it.
- `tests/test_slack_bridge.py`: the Done items above, with the bridge as
  its own process and killed by its pid, against a real Postgres.
- `tests/test_intake.py` and `tests/test_workspace.py` gain the core
  cases: the thread fallback in `_replied`, `lowest_ts`, the Slack
  operator ids, the binding notice rule for Slack and Telegram, and a
  Slack chat in a spec.
- `tests/test_live_slack.py`, skipped without `slack-keys`: a post to a
  test channel in Valor's workspace, then a history scan with
  `include_all_metadata`. It reports first whether the marker came back,
  so the live window knows which lookup branch it runs.

## Gaps

- **Metadata on a user-token post.** OpenClaw posts metadata with a bot
  token. Whether Slack keeps `metadata` on a user-token post is
  unverified until the live window. Lookup works either way (above); the
  marker only makes the match exact.
- **File posts carry no metadata.** `completeUploadExternal` takes no
  `metadata`, so a file part is matched by name and size, as on Telegram.
- **No server-side dedupe.** Slack has no `random_id`, so a post Slack
  received and never answered is found only by `lookup`. The broker never
  performs one effect twice, so this matters only after a crash.
- **Gap fill on a busy workspace** is bounded by Tier 3 limits. A chat
  the pass has not reached is still filled on a later tick, because
  history is read from the seen `ts`, not from the clock. A reply in a
  thread with no ledger activity in 14 days, sent while the bridge is
  down, is missed; live, the socket delivers it.

## Questions for Tom

None open. See "Tom's answers".

## Decided by default

- Socket Mode over the Events API.
- `slack_sdk` without Bolt. Bolt's listener framework is routing, and
  routing is `core/`'s.
- `chat_id` is `<team>/<channel>` for a channel and `<user_team>/<user>`
  for a DM, so two workspaces cannot collide; `message_id` is Slack's
  `ts`, unique within a channel.
- A thread reply's `reply_to` is the thread root, and `topic_id` carries
  `thread_ts` (as a forum topic does on Telegram). A notice with a
  `reply_to` is posted in that message's thread; one without is posted at
  the top of the chat, and Tom's reply in its thread has the notice as
  its root.
- 4,000 characters per post, split at a newline, else a space.
- The metadata marker is the sha256 of the broker key with no HMAC: other
  apps can read it, but it names nothing they could use.
- The lookup skew margin is five minutes and the thread window 14 days.
- **Starts follow the port's rule, with no Slack-specific one.** A
  message from Tom in an owned chat that replies to nothing Valor sent
  starts a task, on Slack as on Telegram, including a top-level message
  of his in a client channel. Tom said access is "the same as Telegram",
  and changing which of his messages start work is a port change for
  every channel, outside 2.5.

## Critique rounds

### Round 1 (of 2): revise

Findings, in brief, and what changed:

1. A thread reply carries the root as `reply_to`, so it never matched a
   notice posted inside the thread. `reply_to` stays the root; `_replied`
   falls back to the newest id in the record's `thread` that is in a sent
   row, for a threaded channel. Two tests: the reply binds as an answer,
   and with two notices it binds to the newer.
2. The `approve` Done item was stale: sends leave at request. Rewritten:
   an answer, `stop`, and a near miss.
3. Slack user ids are per workspace, and Tom's DM was owned by nothing
   with the operator channel on Telegram. `sender_id` is
   `<user_team>/<user>`; `VALOR_OPERATOR_SLACK_IDS` lists Tom's;
   `_from_operator`, `owned("slack")` and `_to_tom` read it.
4. `workspace.py` refused Slack chats and lowercased their ids. The
   per-channel table carries each channel's shape and case rule; a test
   checks a Slack chat in a spec is owned and found by `_project_for`.
5. Gap fill missed replies in threads older than seen. Each pass reads
   `conversations.replies(oldest=seen)` for threads with ledger activity
   in the last 14 days; a restart test covers a reply to an old notice.
6. `intake.lowest` matched integers only, and a first pass would record
   old history. `lowest_ts` compares `ts` as text; a chat with no entry
   and no rows starts at its newest `ts`; seen is held below receives in
   flight.
7. A distributed app loses Tier 3 on history and replies. The app is
   created inside each workspace and never distributed; the budget is
   Tier 3 per workspace and per method.
8. A user token sees its own posts as events. They are dropped by the
   token's own id, live and in gap fill, with a test.
9. Slack redelivers an envelope unacked after 3 seconds, so a download
   could run twice. Each message runs as its own task, acked once its
   receive commits; a redelivery already in flight is acked and skipped;
   downloads go to a temp file and are renamed.
10. A DM needs its IM channel id for gap fill, lookup and sends. `im:write`
    added, `conversations.open` cached in memory, a DM's `sent` chat id is
    `<team>/<user>`, and `thread_ts` resolves to the root through the
    record's `topic_id`.
11. Binding notices could carry kernel text into a client channel. In
    `_notice`, for every channel, a binding notice goes in reply only in
    Tom's own DM or the operator chat, else to the operator channel.
    Starts keep the port's rule (Decided by default).
12. Multi-part sends hit 429 between parts. A 429 on a later part is
    waited out and the send continues; the first post's 429 stays
    `Failed`.
13. Metadata on a user-token post is unverified. One lookup path works
    either way: marker when present, else decoded text, `thread_ts` and
    part order, from `since` less a margin, skipping claimed `ts`.
    `slack-sends.json` is dropped.
14. A file share's `ts` is not known at once. A bounded `files.info` poll
    reads it, else `Unknown` and lookup.
15. Slack stores entities, so the "what is recorded is what renders"
    claim was false. Dropped; lookup decodes `&amp;`, `&lt;`, `&gt;`.
16. `slack-keys` held one workspace. 2.5 serves one, keyed by team id.
17. The bot token sentence is gone, and the ping watchdog left out unless
    a test shows `is_connected()` lying under `slack_sdk`'s monitor.

Also folded in: a Slack send may target any chat Valor's account can
reach, owned or not, as Telegram's does (Business item 8). `_refuse_slack`
and the threat model say so.
