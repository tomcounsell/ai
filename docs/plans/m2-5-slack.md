---
tracking: none
slug: m2-5-slack
type: build
status: draft
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
  about 7,600 lines): Socket Mode, the ping-staleness watchdog, the
  catch-all ack, the subtype allowlist, and the download host allowlist.
- **OpenClaw** (`extensions/slack/src`): an event recorded durably before
  it is acked, a dedupe key of channel and `ts`, writes never retried
  except on a 429, and the one idempotent-send design either system has,
  a metadata marker on every post that a history scan finds after a
  crash.

Neither system's scale comes with it. Hermes's pairing flows, Block Kit,
streaming edits, status lines and reactions, and OpenClaw's multi-account
relay, are features for many users and many agents. Valor has one identity
and one owner, and the port says a bridge does I/O and nothing else.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. Like 2.4, the task adds a
channel to `core/intake.py`'s sender verification (who counts as Tom) and
to the kernel's declarations (`DECLARED`, `LIMITS`, `owned`). A mistake
there lets something other than Tom start work, or a send go somewhere
the kernel did not mean. The bridge also sends to real people under
Valor's name. The Slack tokens are this channel's authentication, the
part MTProto plays for Telegram. They add no check, gate, or review step,
so no governance grant is asked.

## Done, as evidence

Against the test database, with `tests/slack_emulator.py` running as its
own process (a Socket Mode WebSocket and the Web API methods the bridge
calls):

- A `message` envelope from Tom's Slack user id records one
  `message.received` on the `slack` stream, `verified: true`, and
  `intake.bind` starts a task from it under the project that lists the
  chat, else `valor`. The envelope is acked only after `receive` returns.
- The same message delivered twice (a Slack retry, then a gap fill)
  lands once.
- With the bridge killed by its pid while it is down, the emulator queues
  three channel messages and a thread reply. On restart, gap fill records
  all four and records nothing twice.
- A released `slack.send_message` posts once, carrying the marker
  metadata. With the bridge killed between the intent and the outcome,
  `lookup` finds the post by its marker on restart and the send settles
  as done, not resent. A send of 9,000 characters leaves as three posts,
  and a kill after the first post finishes with the two that are missing.
- A reply in a notice's thread binds: `approve` writes `approval.granted`
  with `via: "slack"`, `stop` stops the task, and a near miss steers.
- A 429 on a send's first post is `broker.Failed` with the `Retry-After`
  seconds. A connection cut after the request was written is
  `broker.Unknown`.
- A file whose `url_private` redirects off Slack's hosts is listed as
  skipped, and the token is never sent there.
- The suite and both ruff checks are green.

Live, in a test window on a workspace Valor's account belongs to: Tom
DMs Valor and a task starts; a question reaches him in Slack and his
thread reply binds to it; a forced crash between the intent and the
outcome does not post twice; and the bridge's RSS after a day connected
is measured.

## Threat model

- **What a turn controls:** a send's payload (text, `thread_ts`, files
  in its workspace) and its target. The kernel refuses a target this
  machine's Slack bridge does not own, and it sizes the files. The bridge
  sends exactly the payload.
- **What a message controls:** its own text and files. These are data;
  the judgement layer reads them and the kernel decides what they may do.
  `sender_id` is the Slack user id that Slack's authenticated socket
  asserts, so it is `verified` the way a Telegram user id is. A display
  name is never used to identify anyone.
- **What the kernel must never do:** read turn-owned state to decide who
  Tom is, or let a turn see the tokens. `slack-keys` lives in the kernel
  key directory, mode 600, which every turn's sandbox denies. The token
  goes only to `slack.com`, `*.slack.com` and `*.slack-files.com`, with
  each redirect checked by hand (Hermes, `adapter.py` 6255-6340).

## Decisions for Tom (questions 1 and 2 change the build)

1. **Which identity posts.** Recommended: **Valor's own Slack user
   account, through a user token (`xoxp`)** of a single-workspace app
   that Valor installs. Valor's messages then come from Valor, a member
   like any other, and one identity holds, for the reason telegram.md
   gives for using a user account over a bot. The other choice, a bot
   user (`xoxb`, what Hermes and OpenClaw use), shows an "APP" badge,
   must be invited into each channel, and cannot read a DM between Tom
   and Valor the person.
2. **Which workspaces.** The app must be installed in each workspace it
   reads. In a client's workspace where Valor is a guest, that needs the
   client admin's approval. Recommended: start with one workspace that
   Valor or Tom administers, and add client workspaces, one per project,
   as `chats` entries.
3. **Slack as Tom's operator channel?** Assumed: no. Tom's verified Slack
   messages start work and bind replies, the same as on Telegram. Notices
   that concern no Slack message stay on the operator channel set in
   settings, Telegram today. `VALOR_OPERATOR_CHANNEL=slack` is supported
   for when Tom wants it.

## What is built

### `core/` (the port's side), about 150 lines

- `bridge.py`: `LIMITS["slack"]` (4,000 characters per post, Slack's
  stated guidance for `chat.postMessage`, which truncates past 40,000;
  1 GB per file). `DECLARED["slack.send_message"]`, with `_refuse_slack`:
  the chat is owned, the text has the right shape, and the post is not
  empty. `split_text` learns `chars` units.
- `intake.py`: `VERIFY["slack"]`, `"slack"` in `REPLIES`, `_WHERE`,
  `owned("slack")`, and `_from_operator` against
  `settings.operator_slack_id`.
- `notices.py`: `_to_tom` knows a send to Tom's Slack DM.
- `settings.py`: `VALOR_OPERATOR_SLACK_ID`, and `slack` as an operator
  channel.
- `workspace.py`: project `chats` accept `slack:<team id>/<channel id>`.

### `bridges/slack/`, about 1,300 lines (Telegram's is about 2,500)

| Module | Does | Taken from |
|---|---|---|
| `wire.py` | The only module importing `slack_sdk`: `SocketModeClient` (aiohttp) and `AsyncWebClient`, with no Bolt. Writes retry 0 times and a 429 surfaces its `Retry-After`; reads retry on 429 and 5xx, bounded. A watchdog reconnects when pong is stale for more than 4 ping intervals, since `is_connected()` can be true on a dead session | Hermes 1325-1350; OpenClaw `client-options.ts` 32-35 |
| `inbound.py` | One `message` event to an `Inbound`. `chat_id` is `<team>/<channel>`, `message_id` is `ts`, `chat_kind` is `dm` for an IM and `group` otherwise, and `topic_id` is the `thread_ts` of a threaded reply. `reply_to` is the thread root. `thread` holds the root and the replies before this one (`conversations.replies`, capped at 50). Files are fetched from `url_private_download` by sha256 into `inbound_dir/slack/`; a `text/html` answer is listed as skipped ("not signed in"). The subtype allowlist is none, `file_share`, `thread_broadcast`, and `me_message`; edits, deletions, joins, and bot messages are not part of the port | Hermes 4513-4517, 6255-6340 |
| `bridge.py` | `run`: on every envelope type, ack, so Slack never disables event delivery. A `message` envelope is acked only after `intake.receive` commits. If `receive` fails, it is not acked, Slack retries it, and gap fill takes it. Also the outbox loop and SIGTERM handling, as Telegram does them | Hermes's catch-all ack; OpenClaw `ingress.ts` 166-184 |
| `gap.py` | On every connect and tick, for each owned chat, `conversations.history` from the newest `ts` the last pass saw. For each parent whose `latest_reply` is newer than that, `conversations.replies`. Everything goes through the idempotent intake. Pages are budgeted per tick (Tier 3, about 50 calls a minute); the pass resumes where it stopped | Ours; Hermes and OpenClaw only catch up lazily |
| `send.py` | `slack.send_message` perform and lookup, and notices. Each post is `chat.postMessage` with `mrkdwn: false`, `unfurl_links: false`, `unfurl_media: false`, `thread_ts` from the payload, and metadata `{event_type: "valor_send", event_payload: {key: sha256(broker key), part, parts}}`. Files go through `files.getUploadURLExternal` and `completeUploadExternal`. `lookup` scans history (or replies, for a thread) above the `ts` recorded before the first post, with `include_all_metadata`, keeping Valor's own posts. A full match is done. Some parts on screen means the rest are sent, then done. Two matches for one part is `Unknown` | OpenClaw `send.ts` 494-830 |
| `state.py` | `slack-seen.json` (the newest `ts` per chat) and `slack-sends.json` (the newest `ts` before each send in flight), each written whole, flushed, and renamed into place, as Telegram's are | Ours |
| `kernel.py`, `__main__.py` | The port as one object. `run`, `keys` (copies `SLACK_APP_TOKEN` and `SLACK_USER_TOKEN` from the vault into `slack-keys`), `manifest` (prints the app manifest to paste into api.slack.com), and `--plist` | Hermes `slack_cli.py` 36-75 |

**The manifest.** Socket Mode on. User-token scopes: `channels:history`,
`groups:history`, `im:history`, `mpim:history`, `channels:read`,
`groups:read`, `im:read`, `mpim:read`, `chat:write`, `files:read`,
`files:write`, `users:read`. User events: `message.channels`,
`message.groups`, `message.im`, `message.mpim`. A bot token, if Tom
chooses one, swaps the user scopes for the same bot scopes and adds
`app_mentions:read`.

### Docs

`docs/bridges/slack.md`, in the shape of `email.md`: only what is
particular to Slack. Updates to `bridges/README.md`, the port table in
`telegram.md`, and `machine.md`'s RAM line.

## Tech debt absorbed

- `split_text` is Telegram-only today (`core/bridge.py`), and the email
  and local `chats` are special-cased in `workspace.py` and `intake.py`.
  Adding a fourth channel moves the per-channel facts (verify, the owned
  ids, `_WHERE`, and the shape of a `chats` entry) into one table in
  `core/bridge.py`, instead of a fourth `if channel ==` in each place.

## Left out

- **Converting markdown to mrkdwn.** Text is sent verbatim with
  `mrkdwn: false`, so what the broker records is what renders, as with
  Telegram's plain text. If persona wants Slack formatting, persona
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
  interactive buttons.** Approve and stop bind by reply text, as on every
  channel.
- **The Events API over HTTP.** It needs a public URL and a signing
  secret. Socket Mode needs only an outbound connection, which suits a
  Mac behind NAT.
- **Pairing and allowlists.** Only Tom's verified id starts work. Anyone
  else's message is thread context, the port's rule for other people.
- Distributing the app to more than one workspace (OAuth install flow).
  Each workspace's token is pasted once into the vault.

## Tests

- `tests/slack_emulator.py`: a Socket Mode server (`apps.connections.open`
  hands out a `ws://127.0.0.1` URL), plus `chat.postMessage` (storing
  metadata), `conversations.history` and `conversations.replies` (with
  paging and `include_all_metadata`), the two-step upload, and
  `url_private` with redirect and `text/html` modes. It answers 429 on
  request, and drops a request after reading it.
- `tests/test_slack_bridge.py`: the Done items above, with the bridge as
  its own process and killed by its pid, against a real Postgres.
- `tests/test_live_slack.py`, skipped without `slack-keys`: a post to a
  test channel in Valor's workspace carries metadata, and a history scan
  with `include_all_metadata` returns it. That is the one live fact the
  design rests on (Gaps).

## Gaps

- **Metadata on a user-token post.** OpenClaw posts metadata with a bot
  token. Whether `chat.postMessage` keeps `metadata` when called with a
  user token, and returns it to `include_all_metadata`, is unverified;
  `test_live_slack.py` checks it first. If it is not kept, `lookup`
  falls back to Telegram's design: the account's own posts above the
  recorded `ts`, matched on full text and `thread_ts`.
- **File posts carry no metadata.** `completeUploadExternal` takes no
  `metadata`, so a file part is matched by name and size, as on Telegram.
- **No server-side dedupe.** Slack has no `random_id`, so a post Slack
  received and never answered is found only by `lookup`. The broker never
  performs one effect twice, so this matters only after a crash.
- **Gap fill on a busy workspace** is bounded by Tier 3 limits. A chat
  the pass has not reached is still filled on a later tick, because
  history is read from the seen `ts`, not from the clock.

## Questions for Tom

Questions 1 to 3 are under "Decisions for Tom" above. Also:

4. A Slack account for Valor, and the workspace for test windows: only
   Tom can create or invite these.

## Decided by default

- Socket Mode over the Events API.
- `slack_sdk` without Bolt. Bolt's listener framework is routing, and
  routing is `core/`'s.
- `chat_id` is `<team>/<channel>`, so two workspaces cannot collide, and
  `message_id` is Slack's `ts`, unique within a channel.
- A thread reply's `reply_to` is the thread root, and `topic_id` carries
  `thread_ts` (as a forum topic does on Telegram). A notice is posted in
  the thread of the message it concerns, so Tom's thread reply binds to
  it.
- 4,000 characters per post, split at a newline, else a space.
- The metadata marker is the sha256 of the broker key with no HMAC: other
  apps can read it, but it names nothing they could use.
