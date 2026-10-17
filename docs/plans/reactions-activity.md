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
   someone else). `stop` stays unjudged 👌. The question text and
   `consumer` say the label is the activity Valor's response will take.
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
   name it is given.
3. **Telegram sends custom emoji.** `Wire.react` takes the optional id and
   sends `ReactionCustomEmoji`, else `ReactionEmoji`. At connect the wire
   logs the available reactions (`GetAvailableReactionsRequest`, premium
   flagged) beside the Premium line. The builder reads each Eng and team
   group's `available_reactions` with the account; only if one refuses
   custom reactions does the Telegram entry get a standard fallback used on
   `REACTION_INVALID`, and the delivery names the chat that needed it.
4. **Coverage gap closed.** `on_bound` also reacts (👀, judged) for a
   message bound `none` with a task id, so Tom's reply to a finished task
   in his DM is marked read.
5. **Known to the session.** `chat_line` takes the message's current
   reaction: the newest placed `reaction.requested` for its `received_id`,
   with its label, e.g. `(message 123, you reacted 🔍 web_search)`, or
   `you reacted 👀 read` (`reply` when the judgement answered so).
   `chat_prompt` reads it from the session's own rows; the history block
   and `_steering` read it by `received_id`. A shape reaction placed after
   the turn that read its message reaches the next turn as a line, `- You
   changed your reaction on message 123 to 🔍 (web_search)`, the way a
   `heard` row does (quiet, it wakes nothing).
6. **Docs.** `core/reactions.py` docstring, `docs/bridges/telegram.md` and
   `slack.md` Reactions sections, `docs/judgement-layer.md` labels,
   `skills/sdlc/converse.md` (your reaction is on the message line; asked
   about it, say which and why).

## Out of scope

Deriving the emoji from the tools a turn actually calls (the readback keeps
the classifier); Slack workspace custom emoji; reactions in
`valor-telegram read` output; email and local (they do not react).

## Tests (`tests/test_reactions.py`, kernel DB fixtures)

- Coverage: Tom's DM message, an Eng-group message, a `mentions` group
  message naming Valor, and one replying to his post each get 👀; an
  unaddressed `mentions` group message gets none; a DM reply to a merged
  task gets 👀; email and local get none.
- Shape: a scripted port picking `web_search` writes the shape reaction with
  the Telegram glyph and custom id, and the Slack name on Slack; `reply`
  writes none and is not asked again; abstain leaves 👀; stop gives 👌.
- Every label of `SHAPE` has an entry for `telegram` and `slack`, and no
  entry names a label `SHAPE` lacks.
- Outbox: a `seen` superseded by a shape is closed, not sent; the
  Telegram sender passes the custom id to the wire (fake wire); Slack adds
  the name and removes `eyes`.
- Session: `chat_line` shows the placed reaction and label; a shape placed
  after the turn's `steer_since` appears as the changed-reaction line in
  the next `chat_prompt`; a message with none shows no reaction text.
- Live: in Tom's DM, a request that leads to work shows 👀 then its
  activity emoji; a plain question keeps 👀; asked "why that emoji?",
  Valor names it and the activity.

**Governance.** None added: no check, gate or refusal; the fallback in 3 is
built only on evidence of a chat that refuses custom reactions.
