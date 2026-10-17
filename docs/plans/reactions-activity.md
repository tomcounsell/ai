# Reactions show the activity, use each platform's full set, and are known to the session

**Ask.** Tom, valor team chat, 2026-10-17 13:30 and 13:35 UTC: every message
in DMs and Eng groups gets a reaction; in team groups only when Valor is
named or tagged. On Telegram 👀 means read; a plain reply keeps it; work
replaces it with an emoji for the activity. The classifier stays, labelled
by activity, and the placed reaction is known to the session. "Use the full
emoji set available on each platform. That includes premium reactions on
telegram." His readback (4 points) was accepted.

**Premise, checked.** `intake._react` calls `reactions.on_bound` after
every binding. A team-chat message that wakes the session (every message,
or in a `mentions` chat one naming Valor or replying to his post) binds
`steer` to the session and gets 👀; an unaddressed one binds `heard` and gets
none. Tom's own chat binds `steer` (👀) or `stop` (👌). So steering a chat
session already reacts. The one gap: Tom replying in his DM to a merged or
stopped task binds `none` with that task id and gets no reaction, only a
notice. The judgement (`SHAPE`, `response.shape`) runs in its own serve job,
apart from the session's turn, and its label picks `EMOJI`; Slack maps those
code points to names in `bridges/slack/send.py` `REACTION_NAMES` (a second
mapping). No test covers reactions today. Telegram's standard list has no
🔍, 📅, 🎫 or 📨: the activities that matter need Premium **custom emoji
reactions** (`ReactionCustomEmoji(document_id)`), which this account may
place in DMs and in groups that allow custom reactions.

## What is built

1. **Activities.** `SHAPE`'s labels become, most specific wins: `reply`
   (an answer, acknowledgement, question back or refusal: no new reaction,
   👀 stays), `task` (hand work to a task), `code`, `web_search`, `lookup`
   (code, chat history, ledger, email), `document`, `present` (deck, video,
   explainer), `issue`, `schedule`, `send` (email, a file, a message to
   someone else). `stop` stays unjudged 👌. The question text, the
   `bound_as` input description and `consumer.proceed` change with them:
   the label is the classifier's read of the activity the response will
   take, a prediction, not a record of it.
2. **One mapping, `core/reactions.py` `REACTIONS`.** Per channel, per label
   (`seen` included): Telegram an emoji glyph plus an optional custom-emoji
   document id; Slack a standard emoji name (`eyes`, `saluting_face`,
   `technologist`, `mag`, `face_with_monocle`, `writing_hand`, `bar_chart`,
   `ticket`, `date`, `outbox_tray`, `ok_hand`). The builder picks the
   Telegram ids with the account: Telegram's own emoji packs, through
   `GetCustomEmojiDocumentsRequest`/sticker sets, one per activity whose
   glyph is not on the standard list, recorded in the mapping. `EMOJI` and
   `REACTION_NAMES` go. `reaction.requested` gains `label` and, on
   Telegram, `custom_emoji_id`; `ReactionDue` carries both. Slack sends the
   name it is given. A row written before the deploy (no `label`, no
   `custom_emoji_id`) takes its label from its stage (`seen` → `seen`,
   a `shape` row by its emoji's old meaning, else `seen`), so an in-flight
   👀 is placed, not dropped.
3. **Telegram sends custom emoji.** `Wire.react` takes the optional id and
   sends `ReactionCustomEmoji`, else `ReactionEmoji`. At connect the wire
   logs the available reactions (`GetAvailableReactionsRequest`, premium
   flagged) beside the Premium line. The builder reads each Eng and team
   group's `available_reactions` with the account; only if one refuses
   custom reactions does the Telegram entry get a standard fallback used on
   `REACTION_INVALID`, and the delivery names the chat that needed it.
4. **Coverage gap closed.** `on_bound` also writes 👀 for a message bound
   `none` with a task id (Tom's DM reply to a merged or stopped task), as a
   `seen` row with `judged: false`, which `pending()` excludes: its only
   response is the kernel's notice, so no activity emoji follows.
5. **Known to the session, truthfully.** The session never chose the
   reaction; the classifier did, in its own serve job. `chat_line` shows
   the message's current reaction as that: the newest placed
   `reaction.requested` for its `received_id`, e.g. `(message 123; reacted
   🔍: the reaction classifier read it as web_search)`; with only 👀,
   `reacted 👀 (read)`, plus `; the classifier read it as a plain reply`
   when the `judgement.answered` row for that `received_id` picked `reply`
   (which writes no reaction row, so it is read from the judgement).
   `chat_prompt` reads both from the session's own rows; the history block
   and `_steering` read them by `received_id`. A shape reaction placed after
   the turn that read its message reaches the next turn as `- The reaction
   classifier set 🔍 (web_search) on message 123`, the way a `heard` row
   does (quiet, it wakes nothing).
6. **Docs.** `core/reactions.py` docstring, `docs/bridges/telegram.md` and
   `slack.md` Reactions sections, `docs/judgement-layer.md` labels,
   `skills/sdlc/converse.md` (the reaction on a message is on its line;
   asked about it, say which emoji, that the classifier set it from its
   read of the message, and correct that read if the work went otherwise;
   never claim an activity you did not do).

## Out of scope

Deriving the emoji from the tools a turn actually calls (the readback keeps
the classifier); Slack workspace custom emoji; reactions in
`valor-telegram read` output; email and local (they do not react).

## Tests (`tests/test_reactions.py`, kernel DB fixtures)

- Coverage: Tom's DM message, an Eng-group message, a `mentions` group
  message naming Valor, and one replying to his post each get 👀; an
  unaddressed `mentions` group message gets none; a DM reply to a merged
  task gets 👀 with `judged: false`, is not in `pending()`, and keeps 👀
  after a serve shape pass; email and local get none.
- Shape: a scripted port picking `web_search` writes the shape reaction with
  the Telegram glyph and custom id, and the Slack name on Slack; `reply`
  writes none and is not asked again; abstain leaves 👀; stop gives 👌.
- Every label of `SHAPE` has an entry for `telegram` and `slack`, and no
  entry names a label `SHAPE` lacks.
- Legacy row: a `reaction.requested` 👀 row with no `label` or
  `custom_emoji_id` is placed as `eyes` on Slack and 👀 on Telegram.
- Outbox: a `seen` superseded by a shape is closed, not sent; the
  Telegram sender passes the custom id to the wire (fake wire); Slack adds
  the name and removes `eyes`.
- Session: `chat_line` shows `reacted 🔍: the reaction classifier read it
  as web_search`; a `reply` judgement shows `reacted 👀 (read); the
  classifier read it as a plain reply`; a shape placed after the turn's
  `steer_since` appears as `The reaction classifier set 🔍 (web_search) on
  message N` in the next `chat_prompt`; a message with none shows no
  reaction text.
- Live: in Tom's DM, a request that leads to work shows 👀 then its
  activity emoji; a plain question keeps 👀; asked "why that emoji?",
  Valor names the emoji and says the classifier read the message as that
  activity, correcting it if the work went otherwise.

**Governance.** None added: no check, gate or refusal; the fallback in 3 is
built only on evidence of a chat that refuses custom reactions.

## Revised after use (Tom, 2026-10-17 14:59-15:01 UTC)

"The instant 👀 no longer come", and the classifier gave plain questions 👨‍💻;
"it only needs to change if some work is done". So: the `response.shape`
judgement and its serve jobs are removed; nothing is guessed on arrival.
`reactions.on_turn`, called from `session.record`, sets the activity from
what the collected turn did (its effects, its transcript's tool calls) on
the message it answered; a plain reply keeps 👀. The outbox never skips a
👀 (only one whose activity is already placed), and on a row's wake yields
before the gap fill runs. Items 1 and 5's classifier wording is superseded.
