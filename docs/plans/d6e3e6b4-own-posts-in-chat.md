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

1. `bridges/slack/inbound.py`: each received message carries `posted`,
   Valor's own posts in its conversation since the last message from
   anyone else, oldest first, at most `POSTED` (10): for a reply, from the
   thread already fetched (`thread` keeps the raw messages to pick from);
   for a top-level message, one `conversations.history` page ending at the
   message (`latest=ts`, limit `POSTED`). Each: `id`, `sent_at`, `text`
   (`said`), file names (nothing downloaded). Valor's post is
   `user == wire.user_id`, the same test `wanted` uses. A failed history
   read raises as a file failure would not: the receive fails and is
   retaken, as for `thread`.
2. `core/intake.py` `chat_line`: a message with `posted` renders those
   posts under it, as the `replying_to` post is, labelled as Valor's own
   posts in this chat before the message, sent by any of its sessions.
   Every place a chat message is read (`_chat_start` history,
   `chat_prompt`, `mid_turn`) goes through `chat_line`, so all see them.
3. Guidance, one place, `persona/conduct.md` "In a team chat", Tom's rule
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
  two posts, oldest first, file named; a reply in a thread carries Valor's
  thread posts after the person's last one; a message with no Valor post
  before it carries none; more than `POSTED` are cut to the newest.
- `chat_line`: renders the posts under the message; no `posted`, output
  unchanged (existing tests hold).
- A new DM session's opening history (`_chat_start`) and a steered
  message's `chat_prompt` both show a post another session sent.
- The rendered chat-session persona carries the room rule.

Then the full suite, merge, `core update --wait` on the four Macs, heads
confirmed.
