# Thread tags in a chat session's prompt

A chat session reads a channel as one stream: a Slack thread reply is only
`replying to <ts>`, so the session lost track of which thread it was in and
asked the same questions in a thread and at top level. Each chat message
and each send confirmation it is shown now names its thread by a short tag.
Tags are in the prompt only; nothing people see in Slack or Telegram changes
(Tom, team chat, 2026-10-29 07:44). Answers to the five questions: all
defaults, with `#issue-N` for a link-only root.

## What is built

1. **`intake.thread_tag(m)`**, beside `chat_line`, a pure function of the
   received message (no bridge change, no model call); None on email and
   local. `intake._chat` keeps its result on the session's chat row as
   `thread_tag` (the row keeps no `thread`); `chat_line` reads it there, or
   computes it for a full received message (a new session's history).
   - Not in a thread: `top-level, starts #tag`, slugged from its own text,
     the tag its replies will carry (`top-level` when no word is left).
   - A reply: `in #tag`, slugged from `thread[0]`, the first message.
     Telegram adds `, from <first id>`, since its `replying to` is the
     message answered; Slack's already is the first message. So two
     threads whose first words collide stay apart by id on both channels.
     A Telegram chain stops at a deleted message, so its tag is the oldest
     message left: stable unless the first is deleted. No recorded root
     text: no tag, `replying to` only.
   - A Telegram topic: `in #topic-<id>` for every message in it.
   - `slug`: mention and channel markup dropped, `<url|label>` as the label,
     a GitHub issue or pull link as `issue N` / `pr N`, other links dropped;
     the first three words that are not filler, then the first number.
     A link-only root reads `#issue-3654`.
2. **`chat_line`** adds the marker after the ids: `(message 5, replying to
   1, in #self-wake-3654)`. Reaction lines unchanged.
3. **`session._send_places`** names where each last-turn Slack or Telegram
   send into the session's own chat went, for `_effects_report`: `into #tag`
   for a reply (the tag of the message it answers among the session's
   rows, else of its thread's root, `intake.root_of`), else `into the thread
   of <root>`; `into #topic-<id>`; `top-level, starts #tag` for a post,
   slugged from its text. A send to another chat, a non-send, or an entry
   that lost its request renders as today.
4. **`skills/sdlc/converse.md`**: one sentence: answer in the thread a
   message came from, and ask a thing in one thread only. Wording only.

## Out of scope

Anything shown in Slack or Telegram; fetching Telegram topic titles; a model
or stored thread names; email and the local chat; the board's posting rule
(task 5f2e0af6fdd5).

## Tests (tests/test_thread_tags.py, plus moved assumptions)

A thread's first message and its replies share a tag; top-level and DM
messages; two similar first messages get different tags; colliding first
words share a tag and differ by id (Slack and Telegram); a link-only root;
words before a link; a reply with no recorded root; Telegram chain, topic
(plain and reply share `#topic-<id>`), and a chain whose first message was
deleted; email, local and reaction lines unchanged; a session's prompt
through intake showing two live threads by tag; send confirmations into
either thread, an unknown root, a top-level post, another chat, a
non-send, an entry without its request, and a Slack reply found through
`root_of`. `test_reactions.py` exact lines gain their markers.

## Done

Tom sees a session in the team chat keep two live threads apart by their
tags.
