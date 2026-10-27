# Slack bridge

The Slack bridge connects Valor's own Slack user account to one workspace
over Socket Mode, hands each message to `core/` as a record, and posts the
messages the broker releases. It conforms to the bridge port defined in
[telegram.md](telegram.md#the-bridge-port): intake, performers, and the
outbox. This doc covers what is particular to Slack.

**Status.** Built in `bridges/slack/`. `python -m bridges.slack run` runs
it under `bridge.serve`; `keys` copies its tokens, `manifest` prints the
app manifest, and `--plist` prints its launchd job. The last section lists
the modules. It serves the Yudame workspace (`T0B6E8JPE3C`), one bridge per
Mac, each on that Mac's own app. The Cowboy's receives Tom's DM, #general
(`C0B67T44KEF`, a mentions-only team chat for general Yudame business;
Tom, 2026-10-23), and its projects' dev channels; each other Mac's, its
own projects' dev channels.

**Dev channels** (Tom, 2026-10-25: "private dev channels for each
project ... named like x-dev. They are equivalent to Eng: x telegram
groups"). Each is private, made by Valor with Tom invited, listed in its
project's spec, not mentions-only, its room and Slack description its Eng
group's:

| Channel | Project, Mac |
|---|---|
| `#valor-dev` (`C0CBTQ76PRB`) | valor, the Cowboy |
| `#popoto-dev` (`C0C1P5GLBGR`, Tom's own, from 2026-09) | popoto, the Cowboy |
| `#gato-os-dev` (`C0CBYTEST8A`) | gato-os, the Pirate |
| `#mondayflowers-dev` (`C0CBSS3RW03`) | mondayflowers, the Pirate |
| `#counsell-home-dev` (`C0CC2PX7QTW`) | counsell-home, the Pirate |
| `#yudame-dev` (`C0CBX4MBN1L`, "Engineering on the yuda.me site": no Eng group) | yudame, the Pirate |
| `#cuttlefish-dev` (`C0CBHMQBYET`) | cuttlefish, the Captain |
| `#psyoptimal-dev` (`C0CC2PWM8E8`) | psyoptimal, the Captain |
| `#mastery-dev` (`C0CBHMR5FHV`) | mastery, the Captain |
| `#cyndra-dev` (`C0CC2PWDETE`) | cyndra, the Bald |
| `#satsol-dev` (`C0CCTEEG7FA`) | satsol, no Mac yet, so unanswered |

`skills` and `work-vault` have no Eng group and no dev channel.

**Decision boards** (Tom, 2026-10-27: "more like a todo board that can be
reorganized and evolve"; "I won't do work on the board. I'll chat with
you or call you, or leave emoji reactions"). Each dev channel above but
`#satsol-dev` is its project's `board` (projects/README.md): its session
keeps a canvas tab, "Valor Decisions", of the decisions open for Tom on
that project, rewritten whole from the ledger (`core/board.py`,
`skills/sdlc/converse.md`). Tom made `#valor-dev`'s himself; the bridge
writes into it. Tom answers in the channel, or with 👍 on a decision's
post, which the bridge receives (below).

## What it serves

| Mechanism | Serves |
|---|---|
| Valor as a member of client workspaces under its own name, receiving and posting in project channels | Mission item 1: work arrives the way the people around Tom already send it |
| Tom's verified Slack ids start, answer, steer, and stop work, as on Telegram | Mission item 1; Constraint "Reliable stop, recovery, and correction" |
| Every post an `act` effect, with its `ts` and thread root recorded in its result | Constraint "Bounded authority, metered spending"; effect classes [11] |
| Receipt acknowledged only after the ledger commits; sends reconciled by a history scan | Constraint "Reliable stop, recovery, and correction": nothing is lost or sent twice |
| Inbound text and files treated as data, never as authority | Retrieved content can act as instructions [7] |

## The account

**One identity.** The bridge acts as Valor's own Slack user, through a
user token (`xoxp`), not a bot. Each Mac has a small internal app in the
workspace, from `python -m bridges.slack manifest`, signed in as Valor at
api.slack.com: Socket Mode on, installed to Valor's account, with the
workspace admin's approval where the workspace requires it (Yudame's did
not). The app is never distributed: a distributed app outside the
Marketplace gets one request a minute on `conversations.history` and
`conversations.replies`, and an internal app keeps Tier 3, which gap fill
assumes.

**One app per Mac.** A chat is answered only on the Mac its project
names, and Slack splits one Socket Mode app's events between the
connections open on its token, so one token runs on one bridge. Each Mac
runs its own bridge on its own app: "Valor" (`A0C97117R5F`) on the
Cowboy, "Valor Pirate" (`A0CBTPN2T4M`), "Valor Captain"
(`A0CBYSURU8J`), "Valor Bald" (`A0CBTPN8BTP`). All four are Valor's
user, so every app gets every message Valor can see; each bridge receives
only what its Mac's specs list. The other way, the Cowboy receiving for
every Mac, needs a path from one kernel to another that does not exist.

The manifest asks for these user scopes: `channels:history`,
`groups:history`, `im:history`, `mpim:history`, `channels:read`,
`groups:read`, `groups:write` (making the dev channels, inviting to them,
setting their description), `im:read`, `mpim:read`, `im:write`, `chat:write`,
`files:read`, `files:write`, `users:read`, `reactions:read`,
`reactions:write`, `search:read`, `canvases:write`; and these
user events:
`message.channels`, `message.groups`, `message.im`, `message.mpim`,
`reaction_added`. All four apps had `reactions:read`, `canvases:write`
and `reaction_added` added and were reinstalled to Valor's account on
2026-10-27; a reinstall keeps the user token, so no key changed.

**The tokens.** `python -m bridges.slack keys` copies this Mac's pair,
`SLACK_APP_TOKEN_<MACHINE>` and `SLACK_USER_TOKEN_<MACHINE>` (the machine's
name upper-cased, each run of other characters `_`:
`SLACK_APP_TOKEN_VALOR_THE_PIRATE`), from the vault `.env` into
`slack-keys` in the kernel key directory (mode 600), each under the team
id `auth.test` names for the user token (`T0ABC_SLACK_APP_TOKEN`),
printing each name with `written`, `kept`, or `missing` and never a value,
and exits 1 when one is missing. It never reads another Mac's pair, and
no Mac's is unsuffixed. `run` refuses to start with no workspace's tokens,
or with more than one workspace in the file: the bridge serves one.

**Every update makes sure of it** (Tom, 2026-10-25: "makes sure it's
Slack comms is working, the same way it ensures Telegram is connected").
On a Mac whose specs list a Slack chat, the update's setup check
(`core.permissions.slack`) runs `keys`; with none in the vault it reports
`slack: no keys` and starts nothing. A missing `com.valor.kernel.slack`
job is written from `--plist` with the settings of the Mac's Telegram
job, and loaded; a job whose keys changed is reloaded. The update then
names each bridge `connected since` the second its own connection came
up, or `not connected` ([telegram.md](telegram.md#the-outbox),
Connected), in its `rollout.ended` row, its reply in the room and `core
update --wait`.

**The connection.** `slack_sdk`'s aiohttp `SocketModeClient` for events
and `AsyncWebClient` for the Web API, with no Bolt. `auth.test` at connect
gives the team id and the token's own user id. The socket is kept up by
`slack_sdk`'s own session monitor. Reads (history, replies, `users.info`,
`files.info`, `conversations.open`) retry twice on a connection error, a
429, or a 5xx. Writes (`chat.postMessage` and the file upload) are never
retried by the client: a 429 surfaces with its `Retry-After` seconds, and
the send decides (below).

## Receiving

**Who is received.** The same rule as Telegram:

- Tom's DM, listed in `projects/valor.toml` as `slack:<team>/<his id>`, so
  only the Cowboy's bridge takes it; `VALOR_OPERATOR_SLACK_IDS` (a comma
  list of `<team>/<user>`, one per workspace) is who Tom is, on every Mac,
  and owns nothing by itself;
- the operator chat, when `VALOR_OPERATOR_CHANNEL=slack`
  (`VALOR_OPERATOR_CHAT`, uppercased like every Slack id);
- every member's message in a channel a project spec on this machine lists
  as `slack:<team>/<channel>`, and a DM a spec lists as
  `slack:<team>/<user>`.

A DM from anyone else, and a channel no project lists, is not received.
Valor's own posts (the event's `user` is the token's own id) are dropped,
live and in gap fill; so are bot messages, and every subtype but none,
`file_share`, `thread_broadcast`, and `me_message`.

**Tom's reactions.** A `reaction_added` by one of
`VALOR_OPERATOR_SLACK_IDS` on Valor's own post, in a chat this bridge
owns, is a record of kind `reaction`: `message_id` is `<post ts>/<emoji
name>`, `text` the name as Slack writes it (`:+1:`), `reply_to` and
`topic_id` the post's `ts`, so it binds as a reply to the post (in a team
chat, a steer of its session) and the session reads `Tom reacted :+1: to
your post <ts>`. It gets no reaction of its own. It is acked once its
receive commits; a redelivery is the same record. Anyone else's reaction,
and one on someone else's post, is acked and dropped.

**The record.** Slack fills the port's fields this way:

| Field | From |
|---|---|
| `chat_id` | `<team>/<channel>`; for a DM, the other person's `sender_id` |
| `chat_kind` | `dm` for an IM, `group` otherwise |
| `message_id` | The message's `ts` |
| `sender_id` | `<team>/<user>`, the team being the message's `user_team`, else its `team`, else the connected workspace's, uppercased. One function builds it, live, in gap fill, and in send, so a live event and its history copy are one person |
| `sender_name` | `users.info`'s real name, cached in memory |
| `text` | Slack's `text` as stored (entities and wrapped links included), except that a mention of Valor's own user, `<@U..>`, is written `<@U..|valor>`, so the kernel's mention rule sees it in a mentions-only chat (the thread's texts likewise) |
| `reply_to`, `topic_id` | The thread root (`thread_ts`) of a reply; none for a top-level message |
| `thread` | The root and the replies before this one, the last 50 in all, Valor's own posts included, nothing downloaded |
| `attachments` | Each file fetched from `url_private_download` into `inbound_dir/slack/`, named by its sha256, as `{name, mime, bytes, path}`; a file Slack will not give is `{name, mime, bytes, skipped}` with the reason; an `audio/*` file (a clip, a voice memo) also has `transcript` from the voice process, or `transcript_failed` with the reason, as on Telegram ([telegram.md](telegram.md), Voice notes) |

`sender_id` is what Slack's authenticated socket asserts, so a Slack
record is `verified` the way a Telegram user id is.

**Downloads.** The token goes only to `slack.com`, `*.slack.com` and
`*.slack-files.com`. Redirects are followed by hand, each one checked: a
redirect to any other host is listed as skipped ("redirected off Slack's
hosts") and the token is never sent there. A `text/html` answer is the
sign-in page and is listed as skipped ("not signed in"). A file is written
to a temporary name and renamed into place by its sha256.

**Acknowledgement.** Every envelope that is not a wanted message is acked
on arrival, so Slack never turns event delivery off. A wanted message runs
as its own task and is acked once its `intake.receive` commits. Slack
redelivers an envelope not acked within about 3 seconds; a redelivery whose
`(chat, ts)` is still being received in this process waits for that
receive, is acked once it commits, and is received itself if it fails. A
receive that fails is not acked, so Slack retries it.

**Gap fill.** On every new socket session and every tick, for each owned
chat in the connected workspace, the bridge reads `conversations.history`
from the chat's seen `ts`, then `conversations.replies` from that `ts` for
every thread with recent activity: a parent in the history whose
`latest_reply` is newer, the roots of Valor's sends and notices in the
chat, the threads of messages recorded there (both from the last 14 days,
`intake.threads`), and the roots of receives that failed. The receipt
index makes each replay land once.

- A chat's seen `ts` moves only after its history and every listed thread
  have been read.
- It is held at the `ts` of any receive still running or failed, and a
  read from a held `ts` is inclusive.
- A chat with no seen entry starts at its lowest recorded `ts`
  (`intake.lowest_ts`); with no rows either, its seen `ts` is set to its
  newest and nothing is recorded.
- Reads are budgeted per method, 50 calls a minute each for history and
  replies, shared across chats. A spent budget ends the pass, and the next
  tick resumes it where it stopped.
- A pass's place in a chat (its page cursor, its newest `ts`, its thread
  list) moves only once the page it read is recorded. A failed ledger read
  ends the pass, and the next tick reads that page again.
- A read Slack refuses does not stop the pass. A deleted thread root
  (`thread_not_found`) is logged and its thread dropped. A chat whose
  history or DM Slack refuses (`channel_not_found`, `not_in_channel`, when
  Valor was taken out of the channel) is logged, its pass dropped, its
  seen `ts` kept, and the pass goes on to the next chat; the next tick
  tries it again.

**Local state.** `slack-seen.json` in the key directory holds, per chat,
`{"ts", "inclusive"}`, written whole, flushed, and renamed into place. A
file that cannot be read is set aside as `slack-seen.json.unreadable`;
each chat then starts at its lowest recorded `ts`.

## How a message becomes work

The path is the one in [telegram.md](telegram.md#how-a-message-becomes-work).
What differs is threading: a reply in a thread names the root, not the
message it answers. So when `reply_to` matches no sent message, Tom's
thread reply binds to the newest of Valor's posts in that thread older than
the reply (the root included), read from the `sent` entries in the ledger,
with no cap. A notice posted inside a long thread binds however far down
it sits. Bindings record `via: "slack"`.

People also answer in a DM without threading. Tom's Slack DM has a chat
session, as his Telegram DM does
([telegram.md](telegram.md#chat-sessions-one-per-chat-every-message-a-steer)),
so a top-level message there is the session's next prompt, read against
the whole DM, and the session takes it for what it is: an answer to a
question it relayed, or new work; it answers, steers and stops the tasks
that report to it. An exact `stop` there stops those tasks at once
([telegram.md](telegram.md#bound-replies-decided-by-the-kernel)). A
threaded reply to a question a task sent before the DM had a session
still binds to that task.

## Sending

**The performer.** `slack.send_message` is `act`. Its target is
`<team>/<channel>`, or `<team>/<user>` for a DM (opened through
`conversations.open`, cached in memory). Any chat Valor's account can reach
is a target, owned or not. Its payload:

| Payload field | Meaning |
|---|---|
| `text` | Sent as written |
| `reply_to` | A message `ts`; the post goes in that message's thread |
| `files` | Each a path in the task's workspace and its sha256; a voice reply is an `.mp3` from the present skill's `tts.py`, uploaded like any file, which Slack plays in the message |

A target in a workspace other than the connected one is `broker.Failed`
("`<team>` is not a connected workspace"), and nothing is posted.

**The board canvas.** `slack.write_canvas` is `act`, target
`<team>/<channel>`, payload `{"markdown"}`. The bridge reads the
channel's tabs (`conversations.info`) for a canvas titled "Valor
Decisions" (the tab's label, else the canvas's own title, since a tab
someone added keeps an empty label), and replaces its whole content
(`canvases.edit`); with none, it makes one (`conversations.canvases.create`).
Its result is `{canvas_id, url, readers}`, `readers` the names of the
channel's members other than Valor (`conversations.members`), which the
board row shows the session. Slack's refusal is `broker.Failed`; a write
cut after it was sent is `broker.Unknown`, and lookup writes it again (a
whole replace lands the same however often; a canvas made in doubt is
found by its title). A session's write to its own chat owes Tom no report.
Checked live on 2026-10-27 with the reinstalled Cowboy app: a replace of
`#valor-dev`'s canvas and a new canvas in `#popoto-dev`, both private
channels, were accepted.

**Posts.** Text is split by the port's `split_text` at 4,000 characters a
post (Slack's guidance for `chat.postMessage`), at a newline, else a space;
files follow, one post each. Each text post is `chat.postMessage` with
`mrkdwn: false`, `unfurl_links: false`, `unfurl_media: false`, and the
marker metadata `{event_type: "valor_send", event_payload: {key:
sha256(broker key), part, parts}}`. A file goes through
`files.getUploadURLExternal` and `files.completeUploadExternal`, then
`files.info` is read up to five times, two seconds apart, for the share's
`ts`.

**The thread root.** `reply_to` resolves to its thread's root in order:
the ledger (a recorded message's `topic_id`, else its own `ts`; a `sent`
entry's `thread_ts`, else its `ts`); else one `conversations.replies(ts=
reply_to, limit=1)`; else `reply_to` as given, and Slack's own error on
that post is the outcome.

**What a failure means.**

- The first post refused, rate limited, or never sent is `broker.Failed`;
  a rate limit names Slack's `Retry-After` seconds.
- A 429 on a later post is waited out for its seconds, and the send goes
  on.
- A later post Slack refuses (`is_archived`, say) settles done with the
  posts made, as Telegram's does.
- A post in doubt (the connection cut after the request was written, a
  5xx, a later post never sent, a file share `files.info` never shows) is
  `broker.Unknown`, and the outbox's reconcile settles it through
  `lookup`.

**Lookup.** `lookup(action, key, since)` scans the target chat (the thread
when there is a root, else the history) from `since` less five minutes,
with `include_all_metadata`, keeping Valor's own posts not already claimed
by another recorded send or notice (`intake.claimed`). The expected parts
are the split text, then the files. For each part in order:

1. if any kept post carries a marker, the post with this key's marker and
   this part number;
2. otherwise the earliest kept post after the previous part whose text,
   decoded from Slack's stored form, equals the part's, and whose thread
   is the send's. Slack keeps a mention, channel link or special mention
   (`<@U..>`, `<#C..>`, `<!..>`) as written, stores every other `&`, `<`
   and `>` as an entity, wraps a URL as `<url>` (or `<url|label>`), and
   trims the ends. The stored text is decoded: each `<x|label>` to
   `label`, each `<x>` to `x`, the entities back to characters. The
   part's own text is not decoded, so a literal `<this>` or `&amp;` in it
   still matches; its mentions and channel links are unwrapped the same
   way (or every `<...>` in it, when it wrote its links that way too). A
   file part matches on name and size.

All parts found is done. A found prefix means the rest are posted, then
done. A part found after a missing one, or two candidates for one part, is
`Unknown`. Nothing found is None, and the broker writes the send failed.
`lookup` before the bridge has connected is `Unknown`, so the reconcile
`serve` runs before connecting leaves the send in flight for the next wake.
There is no send state file.

**Notices.** Notices to a Slack chat (the operator chat when the operator
channel is Slack, or a binding notice's chat) take the same path, keyed by
the notice id, with `since` the request row's time: a notice already posted
is found and marked sent, not posted again. A notice that cannot be sent is
logged once and tried on the next wake.

**Reactions.** The kernel marks a message it acts on (`core/reactions.py`;
the Telegram port's Reactions section says when): 👀, then the
classifier's handling reaction, then its done. The outbox yields each
`reaction.requested` row as a `ReactionDue`; the bridge adds it by the
Slack name the row carries (`reactions.REACTIONS`, from the one palette:
`eyes`, `thinking_face`, `+1`, `tada`, `mag`, `saluting_face`, `memo` and
the rest; a row with no label, written before rows named their emoji, as
`eyes`) with `reactions.add`, then removes each name the message's earlier
stages placed (`ReactionDue.previous`) that differs from it, so the message
carries one, and a done that repeats its handling keeps it.
`already_reacted` and `no_reaction` count as done. Slack's refusal
(`missing_scope` until the app is reinstalled with `reactions:write`) is
written as `reaction.failed`; a 429 or a lost connection leaves the
reaction for the next wake, as a notice. A reaction below a later one of
its message (a 👀 once that is placed, any other once it is requested) is
closed `superseded` by the outbox and never added.

## Stop and recovery

A stopped task's released sends are refused by the broker, as on every
channel. On SIGTERM an in-flight perform and its outcome finish before
`run` returns; receives still running are stopped unacked, and Slack or the
next gap fill delivers them again. A bridge killed at any point loses
nothing: an unacked message is redelivered or found by gap fill and lands
once; a send killed after its intent is found or finished by `lookup`; a
send killed before its intent is still in the outbox. launchd runs the
bridge as a resident process (`com.valor.kernel.slack`, logs in
`log_dir/slack.log`) and restarts it if it exits; its job carries the
database, machine, and operator settings, `VALOR_OPERATOR_SLACK_IDS`
among them. The kernel's job and the Telegram and email bridges' jobs
carry `VALOR_OPERATOR_SLACK_IDS` too, since a send to Tom's Slack DM is a
message to Tom in whichever process records it.

**Reading history for a turn.** As on Telegram (its port's "Reading
history for a turn"): `core.bridge.reads` on
`127.0.0.1:slack_read_port` (`VALOR_SLACK_READ_PORT`, 8713) answers a
turn's `valor-slack read`. The chat is the port's id (`T…/C…`, or `T…/U…`
for a DM, opened with `conversations.open`). With no `q` it reads
`conversations.history`, `before` passed as `latest` (strictly older).
With `q` it reads `search.messages` for `<q> in:<#C…>` (`in:<@U…>` for a
DM), sorted newest first, and keeps the matches in that channel; a search
pages by number, so `before` does not apply and answers 400.
`search.messages` needs the user scope `search:read`: until the app is
reinstalled with it, a search answers 503 with `missing_scope`, and
history still reads. Each message has `id` (its `ts`), `sent_at`,
`sender_id`, `sender_name` (`users.info`), `out`, `reply_to` (its
`thread_ts`, from the permalink for a search match), `text`, and its first
file's name.

## What the bridge never does

- **Routing, triage, or judgement.** Which chat belongs to which project is
  the specs'; what a message means is `core/`'s.
- **Formatting.** Text is sent verbatim with `mrkdwn: false`; no markdown
  to mrkdwn, no Block Kit. `<!channel>` and `<!here>` are not escaped.
- **Progress of its own.** No streaming or status lines, and no reaction
  the kernel did not request.
- **Edits, deletions, slash commands, or buttons.** Answers and stops bind
  by reply text, as on every channel.
- **Retries of a write.** A post is retried only by waiting out a 429 on a
  later part of a send already under way.

## The implementation

| Module | Holds |
|---|---|
| `bridges/slack/wire.py` | The only module importing `slack_sdk`: the socket, the read and write clients, the IM and name caches, uploads, downloads with the host check, `reactions.add` and `reactions.remove`, and the canvas calls |
| `bridges/slack/inbound.py` | One message, or Tom's reaction, to the `Inbound` fields; `person`, the one function that builds a person's id |
| `bridges/slack/bridge.py` | `SlackBridge`: connect, the live handler and its acks, gap fill, the outbox loop, and SIGTERM |
| `bridges/slack/gap.py` | The per-method budget and a chat's pass in progress |
| `bridges/slack/send.py` | `slack.send_message` and `slack.write_canvas` perform and lookup, the notice send, and reactions |
| `bridges/slack/state.py` | `slack-seen.json` |
| `bridges/slack/kernel.py` | The port gathered into one object, so the bridge imports only `core.bridge`, `core.broker`, `core.db`, `core.intake`, `core.settings`, and `core.credentials` |
| `bridges/slack/__main__.py` | The verbs `run`, `keys`, `manifest`, and `--plist` |

## Gaps

- **Metadata on a user-token post** is unverified until the live window.
  Lookup works either way; the marker only makes the match exact.
- **File posts carry no metadata.** `completeUploadExternal` takes none, so
  a file part is matched by name and size.
- **No server-side dedupe.** Slack has no `random_id`, so a post Slack
  received and never answered is found only by `lookup`.
- **Quiet threads.** A reply, sent while the bridge is down, in a thread
  rooted at someone's top-level message, with no reply in it recorded and
  no post of Valor's in it in the last 14 days, is missed by gap fill;
  live, the socket delivers it.
- **One workspace.** `slack-keys` is keyed by team id, so a second
  workspace is data; its connection is later work.
- **Reactions while the bridge is down** are missed: gap fill reads
  history, and history carries no reaction events. Tom says it in the
  channel instead.
- **A reaction undone** (`reaction_removed`) is not received: a 👍
  passed on stays passed on, and Tom corrects it in the channel. The same
  emoji removed and added again on one post is the record already made.
- **No Mac for SATSOL.** `#satsol-dev` is listed, as Eng: SATSOL is, and
  answered once its spec names a Mac.
