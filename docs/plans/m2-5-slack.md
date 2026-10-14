---
tracking: none
slug: m2-5-slack
type: build
status: merged
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
  until the first receive commits, and the file is written once. If the
  first receive fails, the redelivery is processed and the message lands
  once. A DM whose live event carries only `team` and whose history copy
  carries neither `team` nor `user_team` gives one `chat_id` and one
  `sender_id`: one record, one start.
- **Valor's own posts.** A send produces no `message.received`, live or
  in gap fill: messages whose `user` is the token's own id (from
  `auth.test` at connect) are dropped.
- **First pass.** A chat with no seen entry and no recorded rows, holding
  old messages, records nothing; its seen `ts` is set to its newest.
- **Gap fill.** With the bridge killed by its pid, the emulator queues
  three channel messages and a thread reply. On restart, gap fill records
  all four and records nothing twice. A thread reply to a notice posted
  three days earlier, made while the bridge was down, is recorded on
  restart and binds to that notice. With the page budget spent between
  a chat's history read and its replies reads, the next tick still
  records the reply.
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
  `Retry-After` and the send finishes with three posts. A send whose
  second post Slack refuses (`is_archived`) settles done with one `sent`
  entry. The text fallback finds a post containing a URL, an `&`, and a
  trailing newline, which the emulator stores the way Slack does. A
  target in a workspace other than the connected one is `broker.Failed`
  with "not a connected workspace".
- **A file post** settles with the `ts` the bounded `files.info` poll
  reads. With the emulator never sharing the file, the outcome is
  `broker.Unknown` and `lookup` finds the share in history.
- **Replies bind.** A notice posted in a thread rooted at Tom's message,
  then Tom's reply in that thread, records `question.answered` with
  `via: "slack"`. With two notices in one thread, Tom's reply binds to
  the newer. In a 60-reply thread whose notice is reply 58, Tom's reply
  60 binds as the answer. A reply of exactly `stop` stops the task; a near miss steers
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
window knows which lookup branch runs, and shows how Slack stored a part
with a URL, an `&`, and a trailing newline; a message from a non-whitelisted
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
  is not empty. `split_text` already reads `LIMITS[channel]` and counts
  `chars`, so the limit is all it needs.
- `intake.py`:
  - `"slack"` in the table: verified, replies, threaded.
  - `owned("slack")` always adds each of `settings.operator_slack_ids`,
    as `owned("email")` adds Tom's addresses, then `settings.operator_chat`
    when the operator channel is Slack, then the project chats.
  - `_from_operator`: a verified Slack record whose `sender_id` is in
    `operator_slack_ids`.
  - `_replied`: for a threaded channel, when `reply_to` matches no sent
    row, the newest `sent` entry in the record's chat whose `thread_ts`
    equals the record's `topic_id` and whose `message_id` sorts below the
    record's (C collation, as `lowest_ts`) binds instead. Ledger only,
    with no cap; `thread` is not read.
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
  operator channel, whose `VALOR_OPERATOR_CHAT` is `<team>/<channel>`, or
  `<team>/<user>` for Tom's DM.
- `workspace.py`: project `chats` accept `slack:<team>/<id>`, through
  the table's shape and case rule, in place of the Telegram and email
  special case.

### `bridges/slack/`, about 1,400 lines (Telegram's is about 2,500)

| Module | Does | Taken from |
|---|---|---|
| `wire.py` | The only module importing `slack_sdk`: `SocketModeClient` (aiohttp) and `AsyncWebClient`, with no Bolt. Writes retry 0 times and a 429 surfaces its `Retry-After`; reads retry on 429 and 5xx, bounded. Reconnection is `slack_sdk`'s own session monitor. `auth.test` at connect gives the team id and the token's own user id. `conversations.open` maps a `<team>/<user>` to its IM channel, cached in memory | OpenClaw `client-options.ts` 32-35 |
| `inbound.py` | One `message` event to an `Inbound`, or nothing when its `user` is the token's own (the rule applies to the event itself; `thread` keeps Valor's posts). One function builds a person's id, used live, in gap fill, and in send: the team is the message's `user_team`, else its `team`, else the connected team from `auth.test`. `chat_id` is `<team>/<channel>`, or for an IM the other person's id. `sender_id` is the sender's id. `message_id` is `ts`, `chat_kind` is `dm` for an IM and `group` otherwise, `topic_id` is the `thread_ts` of a threaded reply, and `reply_to` is the thread root. `thread` holds the root and the replies before this one (`conversations.replies`), the last 50, as context for the turn. Files are fetched from `url_private_download` to a temp file, then renamed into `inbound_dir/slack/` by sha256; a `text/html` answer is listed as skipped ("not signed in"). The subtype allowlist is none, `file_share`, `thread_broadcast`, and `me_message`; edits, deletions, joins, and bot messages are not part of the port | Hermes 4513-4517, 6255-6340 |
| `bridge.py` | `run`: every non-message envelope is acked on arrival, so Slack never disables event delivery. A `message` envelope runs as its own task and is acked once its `intake.receive` commits. A redelivered envelope whose `(chat, ts)` is already in flight in this process waits for the first receive: it is acked once that commits, and processed itself if that fails. If `receive` fails, the envelope is not acked, so Slack retries it; seen stays at it, and its thread root is held in memory on the next pass's thread list, so gap fill takes it once Slack's retries run out. Also the outbox loop and SIGTERM handling, as Telegram does them | OpenClaw `ingress.ts` 166-184 |
| `gap.py` | On every connect and tick, for each owned chat: `conversations.history` from the chat's seen `ts`; then `conversations.replies(oldest=seen)` for each thread with recent activity, which is any parent in that page whose `latest_reply` is newer than seen, the roots of Valor's sends and notices in the chat (from `sent` rows), and the `topic_id`s of messages recorded in the chat, both from the last 14 days. A chat with no seen entry starts from `intake.lowest_ts`; with no rows either, its seen is set to its newest `ts` and nothing is recorded (Telegram's first pass, `bridge.py` 265-273). A chat's seen advances only after its history pages and every thread on its list for the pass have been read. Seen is held at the `ts` of any receive still in flight, and a read from a held `ts` uses `inclusive=true`. Pages are budgeted per tick at Tier 3, per method (about 50 calls a minute each for history and replies); the pass resumes where it stopped | Ours; Hermes and OpenClaw only catch up lazily |
| `send.py` | `slack.send_message` perform and lookup, and notices. Each post is `chat.postMessage` with `mrkdwn: false`, `unfurl_links: false`, `unfurl_media: false`, `thread_ts`, and metadata `{event_type: "valor_send", event_payload: {key: sha256(broker key), part, parts}}`. A target whose team is not the connected one is `broker.Failed` ("not a connected workspace"), a can't-perform fact like `channel_not_found`. A DM target is opened through the cached `conversations.open`. `thread_ts` is the root, resolved in order: a recorded message's `topic_id`, else its own `ts`; a `sent` row's `thread_ts`, else its `ts`; else one `conversations.replies(ts=reply_to, limit=1)` for the root; else `reply_to` as given, and Slack's own error on that post is the outcome. A 429 on a later part is waited out for its `Retry-After` and the send continues. A definite Slack refusal of a later part, or a file of the send gone, settles done with the parts posted, as Telegram's `_finish` does. Files go through `files.getUploadURLExternal` and `completeUploadExternal`, then `files.info` is polled, five times two seconds apart, for the share's `ts`; none is `Unknown`. Each `sent` entry is `{channel, chat_id, message_id, thread_ts}` with a DM's `chat_id` as `<team>/<user>`. Lookup is below | OpenClaw `send.ts` 494-830 |
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
2. else the earliest kept post after the previous part's `ts` whose
   normalised text equals the part's and whose `thread_ts` equals the
   payload's root; a file part matches on name and size. Both sides are
   normalised the same way: `&amp;`, `&lt;` and `&gt;` decoded,
   `<url|label>` turned into `label` and `<url>` into `url`, and the ends
   stripped.

All parts found is done with their `ts`. A prefix found means the rest are
sent, then done; if Slack definitely refuses the rest, done with the
prefix. A part found after a missing one, or two candidates for
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

- The email
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
  `mrkdwn: false`. Slack still stores `&`, `<` and `>` as entities, wraps
  URLs as `<...>`, and trims the ends, so lookup normalises both sides
  before comparing. If persona wants Slack formatting, persona
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
  after 3 seconds, and stores text the way Slack does (entities, wrapped
  URLs, trimmed ends), plus `auth.test`, `conversations.open`,
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
  so the live window knows which lookup branch it runs. A second post
  carries a URL, an `&`, and a trailing newline; the test reports its
  stored form and that the text fallback matches it.

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

## Patch round 1

Review round 1 (of 2) asked for changes; the docs check and the test
check each left one note. Each was taken, each with a test that fails
without its fix:

1. A read Slack refused (`thread_not_found` for a deleted root,
   `channel_not_found` for a channel Valor was taken out of) ended the
   whole gap-fill pass on every tick, so no later chat was ever filled.
   A refused thread read is logged and that thread dropped; a refused
   chat read drops that chat's pass, keeps its seen `ts`, and the pass
   goes on. Tests: a deleted root, and a removed channel, in the operator
   chat; the client channel's missed message is still recorded.
2. A pass moved its page cursor, newest `ts` and history-done flag before
   the page was recorded, so a failed ledger read lost the page. They now
   move only after the page is recorded and the thread list read. Tests:
   the bridge child fails one `recorded` (two-message pages) or one
   `threads` read; nothing is lost.
3. Lookup's text match missed a post holding `<@U..>`, `<#C..>` or
   `<!..>`, which Slack keeps as written. The requested text has those
   sequences unwrapped as the stored text has (or every `<...>`, when it
   wrote its links that way). The emulator keeps them; the no-metadata
   lookup test sends a mention and a channel link.
4. A lowercase `VALOR_OPERATOR_CHAT` left the Slack operator chat
   unowned. Settings name it as its channel names chats, uppercase on
   Slack.
5. Docs check: `VALOR_OPERATOR_SLACK_IDS` reached only the Slack job, but
   `notices._to_tom` reads it in whichever process records a send. The
   kernel's, email's and Telegram's jobs carry it, and `MACHINE_ONLY`
   drops it; a test reads all four plists.
6. Test check: `owned("email")` gained the operator chat when the operator
   channel is email. That was not intended: email is never the operator
   channel. Restored to Tom's addresses and project threads, with a test.

Suite (`VALOR_TEST_DB=valor_rebuild_test_25build`,
`VALOR_TEST_PORTS=7200-7399`): 2015 passed, 112 skipped, 4 failed, 65
errors, against base 1963 passed, 4 failed, 65 errors. The 65 errors and
the mailserver failure are dovecot, the backup and credential-push
failures are this Mac's, as at base; the fourth, the Telegram SIGTERM
test (five own posts where one was expected, a test this round does not
touch), passed in 5 runs alone and 2 under load beside the Telegram and
Slack tests, as base's fourth (a Telegram lock test) did. `ruff check`
passes; `ruff format` flags only the three base files it flags at base.

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

### Round 2 (of 2): revise

Both rounds are spent, so these findings ride into the build, as the
lead decided:

1. A DM's team came from `user_team`, which Slack does not always send,
   so a live event and its gap-fill copy could differ. One function takes
   `user_team`, else `team`, else the connected team, live, in gap fill
   and in send; a test dedupes the two copies of one DM.
2. Seen could pass thread replies not yet read, and `oldest` is
   exclusive. Seen advances only after every listed thread is read; a
   held `ts` is read with `inclusive=true`; a test spends the budget
   between the two reads.
3. The `_replied` fallback read `thread`, capped at its oldest end. It
   reads each `sent` entry's `thread_ts` from the ledger instead, with no
   cap; `thread` keeps the last 50. A 60-reply test covers it.
4. Slack rewrites more than three entities. Both sides of the text match
   are normalised (entities, `<url>` and `<url|label>`, trimmed ends); the
   emulator stores text as Slack does; the live test posts a URL.
5. A definite refusal of a later part left the send `failed` with part 1
   live. It settles done with the parts found, as Telegram does.
6. The operator chat was not owned when the operator channel is Slack.
   `owned("slack")` adds it.
7. Acking a redelivery while the first was in flight could lose a
   message if the first failed. The redelivery waits for the first
   receive, is acked when it commits, and is processed if it fails; the
   failed message's thread root joins the next gap fill pass.
8. A target in another workspace, and an unresolvable `reply_to`, had no
   stated behaviour. The first is `broker.Failed` at perform; the
   resolution order of `reply_to` is written out.

Minor: the `split_text` lines were false (it already counts `chars` per
`LIMITS`); struck.

## Checks

Round 1, on 337a9ab10 (base cb3f53159):

- **Test:** pass. Base: 4 failed, 1963 passed, 65 errors. Head: 3 failed, 2002 passed, 65 errors. The 3 shared failures and the 65 errors need dovecot, Apple `container`, or this Mac's backup plist environment; the extra base failure is the flaky Telegram lock test. The check added 5 tests (tests/test_channel_gaps.py): the local binding notice in reply, a Telegram topic reply, Slack thread binding scoped to its own chat, and two fallback cases.
- **Review:** changes, three findings: a refused read halted gap fill for every chat; the page cursor advanced before the page was recorded; the lookup fallback missed `<@U>`, `<#C>` and `<!here>`. A minor one: `VALOR_OPERATOR_CHAT` case. Governance: no.
- **Docs:** updated (f35881b98). It found `VALOR_OPERATOR_SLACK_IDS` missing from the kernel's and the other bridges' environment lists and from `MACHINE_ONLY`; patch round 1 adds it.

Round 2, after patch round 1, on 41264e84e (base db190e4e3):

- **Test:** pass. Full suite: 3 failed, 2016 passed, 112 skipped, 65 errors, all the known machine failures. The Telegram SIGTERM test passed 5 of 5 at base and at head under `-n 4`. The check added 9 tests of the text match (tests/test_slack_text.py).
- **Review:** pass, round 2 of 2. Governance: no. It broke the refused-read fix on purpose and saw its test fail. Five notes, none blocking; the largest is that gap fill can re-read history already seen, which spends rate limit but loses nothing.
- **Docs:** no_change.

The lead checked the governance paragraph against CLAUDE.md byte for byte in bridges/README.md and core/README.md. The diff adds no "cori" and no em or en dashes. `ruff format --check` names three files outside the diff, as at base.

## Merged

The lead's decision: merge. Every check passed on 41264e84e; the head adds only the test check's commit. Valor-cori-rebuild was fast-forwarded to it.

Rollout on this Mac: `uv sync` in the rollout checkout for `slack_sdk`, after a `pg_dump -Fc` of `valor_rebuild` to `~/valor-backups/` (no backup disk is set). No Slack job is loaded: the app, Socket Mode, the install to Valor's account and the two tokens are Tom's, set out in Tom's answers. The first live fact is whether `chat.postMessage` keeps `metadata` on a user-token post; if not, lookup matches on text and `thread_ts` alone, which the tests already cover.

## Live window (2026-10-14)

Done in Valor's browser session on api.slack.com, on Tom's pointer to the yudame app pages:

- The app "Valor" (A0C97117R5F) in Yudame (T0B6E8JPE3C), from `python -m bridges.slack manifest`. Socket Mode is on from the manifest.
- The app-level token `valor-socket`, scope `connections:write`.
- Installed to Valor's own account (U0B6C6J735L, `valor`) with the manifest's user scopes.
- Both tokens went from Slack's Copy buttons straight into `~/Desktop/Valor/.env` as `SLACK_APP_TOKEN` and `SLACK_USER_TOKEN`, never printed. `python -m bridges.slack keys` wrote `T0B6E8JPE3C_SLACK_APP_TOKEN` and `T0B6E8JPE3C_SLACK_USER_TOKEN`.

The first live fact: `tests/test_live_slack.py` passed on Valor's DM with itself (D0B6HSN666Q). Slack keeps `metadata` on a user-token post and returns it with `include_all_metadata`, so lookup finds a sent part by its marker; the text match stays the path for file parts. The stored text wraps the URL in `<...>` and writes `&` as `&amp;`; `same` matches it.

Decided by default:

- The live test runs in Valor's DM with itself: no one else sees the posts, and it needs only `im:write` and `im:history`, which the app has.
- Tom named the 1Password vault Yudame Secrets as a place for these secrets. The `valor-local` service account can read it but is refused a write (`Permission (101)`), so the tokens are in the vault `.env` only for now. Once that account can write there, they go in as `VALOR_SLACK_APP_TOKEN` and `VALOR_SLACK_USER_TOKEN`, following the vault's names for the other Slack app.

No Slack job is loaded yet; that waits for the kernel's live run on this Mac (judgement keys, `claude-token`, the valor-demo items and `VALOR_BACKUP_DIR` are not set up here).

The bridge on the Pirate (2026-10-14): `com.valor.kernel.slack` is loaded from `python -m bridges.slack --plist` with `VALOR_OPERATOR_SLACK_IDS=T0B6E8JPE3C/U0B78H17XQ8` (Tom in Yudame) and `VALOR_PGPORT=5433`, against `valor_rebuild`. It connected as U0B6C6J735L. One notice under the ledger id `slack-live` (`notices.request`, channel `slack`, chat `T0B6E8JPE3C/U0B78H17XQ8`) was posted to Tom's DM by the bridge's outbox: `notice.sent` with ts `1791977339.180769`, read back from the DM with its `valor_send` marker and its `[n:f2e99069d583]` tag. The operator channel stays `telegram`; moving it to Slack is Tom's call. The kernel process is not running on the Pirate, so a DM from Tom is recorded and waits for it.
