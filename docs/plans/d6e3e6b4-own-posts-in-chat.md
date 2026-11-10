# A chat session sees Valor's own posts in its chat, whichever session sent them

Tom, valor team chat, 2026-11-10 08:01 UTC: a project channel's session
moved a file for a teammate into his DM; when he replied there, the DM's
session said it had never sent it.

## What happened (from the ledgers; no client content here)

- **The send.** The Captain, task a19e56bd743d (a project channel's chat
  session, Slack workspace T0B6E8JPE3C), event 57837 at 07:50:15 UTC:
  `slack.send_message` to a person's user id, three posts (outcome 57841).
- **The reply.** The Pirate, which owns that workspace's DMs, received the
  teammate's DM at 07:54:05 (event 34737) and steered it to its open DM
  session 972b30c11cd7. That session told him it had not sent the file and
  wrote to Tom that something it did not run had posted from its account.
- **Why it saw nothing.** A session reads only what reaches its chat as a
  received message (`intake._chat_start`, `session.chat_prompt`,
  `mid_turn`); the Slack bridge drops Valor's own posts (`inbound.wanted`),
  and the send's record (`effect.outcome` `sent`) is in the Captain's
  ledger, which the Pirate never sees. The thread context the bridge
  fetches (`inbound.thread`) is used only for the thread's tag, and a
  top-level DM message gets none. Only Slack holds every post, so only the
  bridge can say what Valor posted there.

## Fix (one mechanism, no new check)

1. `bridges/slack/inbound.py` `build`: each received message carries
   `posted`, Valor's own posts in its conversation since the last message
   from anyone else, oldest first, at most `POSTED` (10). For a reply,
   picked from the thread page `thread` already reads (no new call); for a
   top-level message, one `conversations.history` page ending at it
   (`latest=ts`, limit `POSTED`). Each: `id`, `sent_at`, `text` (`said`),
   file names (nothing downloaded). Valor's post is `user == wire.user_id`,
   the test `wanted` uses.
   - **A failed read does not fail the receive.** `wire._call` turns a 429
     into `RateLimited` (Slack's Retry-After, no retry) and a 5xx or a drop
     into `NotConnected`; `posted` is context, like a file, so as
     `attachment` does for a file, the message is recorded with `posted`
     empty and `posted_failed` naming why (rendered as one line). No retake,
     so a persistent 429 cannot loop or hold inbound up.
   - **Rate.** `conversations.history` is Tier 3 (50+/min) for Valor's own
     internal workspace apps; the new 1/min limit applies only to
     commercially distributed non-Marketplace apps. The gap fill already
     reads history per owned chat on the same budget.
2. `core/intake.py`: `Inbound` gains `posted: list[dict] =
   field(default_factory=list)` (`{id, sent_at, text, files}`, oldest
   first; Slack only) and `posted_failed: str | None = None`; both join
   `_chat`'s `keep` tuple so `message.steered`/`message.heard` carry them.
   There, `posted` drops each post whose ts this ledger records as a send
   by the receiving session itself (`effect.outcome` `sent` `message_id`
   under that task id): it already has its own replies. What is left is
   what another session, or another Mac, posted.
3. `chat_line` renders `posted` under the message, as the `replying_to`
   post is: "before this, Valor posted here (another session of yours, or
   another Mac):", each post quoted with its time and files. Every read of
   a chat message (`_chat_start` from `message.received`, `chat_prompt`
   and `mid_turn` from the steered/heard rows) goes through `chat_line`.
4. Guidance, one place, `persona/conduct.md` "In a team chat", Tom's rule
   as given: work that starts in a room stays in that room; a file for the
   people in a room is sent in that room's thread, since the room is who
   may see it; a DM is only for something meant for one person alone.

## Out of scope

Telegram (no incident; its bridge could fill `posted` the same way later).
Recording Valor's posts as received messages (they would bind and wake
sessions). Sharing ledgers between Macs. Any check on DM sends.

## Tests

- Slack inbound (fake wire): a top-level DM message after two Valor posts
  (one with a file) and an earlier person's message carries exactly the
  two, oldest first, file named; a thread reply carries Valor's thread
  posts after the person's last one; none before it, none; more than
  `POSTED`, the newest; a history read raising `RateLimited` records the
  message with `posted` empty and `posted_failed` set.
- End to end (Slack `Inbound` received, bound and steered into an open DM
  session, then `chat_prompt`): a post another session sent shows under the
  message; the session's own earlier reply (its `effect.outcome` in the
  ledger) is not repeated. `_chat_start` of a new session shows the post.
- `chat_line` with no `posted` is unchanged (existing tests hold).
- The rendered chat-session persona carries the room rule.

Then the full suite, merge, `core update --wait` on the four Macs, heads
confirmed.
