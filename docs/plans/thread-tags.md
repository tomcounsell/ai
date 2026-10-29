# Thread tags in a chat session's prompt

A chat session reads a channel as one stream: a Slack thread reply is only
`replying to <ts>`, so the session lost track of which thread it was in and
asked the same questions in a thread and at top level. Each chat message
and each send confirmation it is shown now names its thread by a short tag.
Tags are in the prompt only; nothing people see in Slack or Telegram changes
(Tom, team chat, 2026-10-29 07:44). Answers to the five questions: all
defaults, with `#issue-N` for a link-only root.

## What is built

1. **`intake.thread_tag(m)`**, beside `chat_line` in `core/intake.py`, from
   what the bridges already record; no new state, no bridge change, no
   model call. Only for `channel` slack or telegram; else None (email, local
   unchanged).
   - Not in a thread (no `reply_to`, no `topic_id`): `top-level`.
   - In a thread: a slug of the root message's text. The root is
     `thread[0]` (Slack's `thread` puts the root first; Telegram's chain is
     root-first). Slug: Valor's mention markup dropped, `<url|label>` read
     as the label, other URLs dropped except a GitHub issue or pull link,
     which gives `issue-N` / `pr-N`; lowercase words, filler words dropped,
     the first three kept, then the root's first number (an issue or PR
     number) if not already among them; `#` and hyphens. The root's own
     words come first; a link-only root reads `#issue-3654`.
   - A Telegram topic message with no reply chain: `#topic-<topic_id>`.
   - A thread whose root text is not recorded, or slugs to nothing: None,
     and the line keeps only its `replying to <id>`.
2. **`chat_line`** adds `, in #tag` or `, top-level` after the ids it
   already shows: `Tom (message 5, replying to 1, in #self-wake-3654): ...`.
   `replying to` stays, so two roots whose words give the same tag are
   still told apart by root id. Reaction lines are unchanged. Every caller
   (`chat_prompt`, `mid_turn`, the new-session history) gets it.
3. **`session._effects_report`** names where each `slack.send_message` /
   `telegram.send_message` went: `- reply.json (effect 12): done, into
   #self-wake-3654`, or `..., top-level`. The tag comes from the message the
   send's `reply_to` names, found among the session's own chat rows, else
   by its root (`intake.root_of`) among them; not found, `into the thread of
   <root id>`. A Telegram send with only `topic_id` reads `into #topic-<id>`.
   `next_prompt` passes the rows (and the connection for `root_of`).
4. **`skills/sdlc/converse.md`**: one sentence by the reply instructions:
   each line carries its thread's tag or `top-level`; answer in the thread
   the message came from, with `reply_to` the message you answer. Wording
   only.

## Out of scope

Anything shown in Slack or Telegram; fetching Telegram topic titles; a model
or stored thread names; email and the local chat; the board's posting rule
(task 5f2e0af6fdd5).

## Tests (tests/test_thread_tags.py, plus moved assumptions)

- Slack: the root and two replies of one thread get the same tag; a
  top-level message reads `top-level`; a DM message not in a thread reads
  `top-level`.
- Two live threads in one channel get different tags; two similar roots
  ("the self-wake plan", "the self-wake questions") get different tags; two
  roots with the same first words share a tag and their lines still differ
  by `replying to <root>` (the documented collision behaviour).
- A root that is only a GitHub issue link gives `#issue-N`; a root with
  words and a link keeps its words; Valor's own post as the root (mention
  markup) slugs from its words.
- A reply whose thread has no recorded root text gets no tag and keeps
  `replying to`.
- Telegram: a reply chain tagged from its first message; a topic message
  with no chain `#topic-<id>`; a plain group message `top-level`.
- Email and local messages render exactly as before; a reaction line
  unchanged.
- `chat_prompt` shows the tags on its lines.
- `_effects_report`: a send into a thread names that thread's tag; a reply
  to a message mid-thread names the root's tag; a top-level send says
  `top-level`; an unknown root gives `into the thread of <id>`; a non-chat
  effect (push_branch) unchanged.
- Existing exact-line asserts (`test_reactions.py`, `test_board.py`,
  `test_voice.py`, chat history tests) updated where a slack/telegram
  message now gains its marker.

## Done

Tom sees a session in the team chat keep two live threads apart by their
tags.
