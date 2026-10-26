# Reactions: the classifier picks a handling and a completed reaction, and no 👀 is left hanging

**Ask.** Tom, valor team chat, 2026-10-25 15:16 and 2026-10-26 07:36 UTC:
make reactions "more fun and rarely leave behind hanging 👀"; "the
classifier should choose reactions for both handling and completed in all
cases"; 📝 ("noted") as the backup; a plain yes or acknowledgement is
answered by the reaction alone; bigger work shows it is being worked on.
This supersedes the 2026-10-17 revision of `reactions-activity.md`
("nothing is guessed on arrival") on Tom's later word. Kept from it: 👀 is
placed at once and never skipped, and nothing claims an activity the turn
did not do.

**Premise, checked in `core/reactions.py`.** 👀 hangs because `on_turn`
sets one `shape` per turn on one message (1744 kept 👀, 1745 got 🫡); a
plain-reply turn sets nothing by design; a compaction turn is never
collected and an errored one sets nothing, so a message whose retries run
out keeps 👀; a `none` binding's 👀 is `final`; a steer to a task stopped
before its next turn keeps 👀. Nearly every chat message ends 🫡 because a
chat session's `task.steer`/`task.start` effects all read as `task`.

## What is built

1. **One palette, one table.** `REACTIONS` becomes, per channel, a label
   for each emoji in a palette of about 40 (Telegram glyph, Slack name):
   the old activity emoji (👨‍💻 🔍 🧐 ✍ 📊 🎫 📅 📤 🫡 👌) plus the
   expressive ones the team would enjoy (👍 ❤ 🔥 🎉 💯 🤝 🙏 😁 🤣 🤔 🤯 😢
   🙈 🤷 🏆 ⚡ 🫶 ✅ 🚀 …), and `noted` 📝 and `seen` 👀. Telegram places a
   glyph off its standard list as a Premium custom emoji, as the wire does
   today; Slack its standard name. "Full set" is read as any emoji either
   platform can place, curated to a palette a closed classifier can pick
   from (a PICK question over ~1,900 labels is not askable of either leg).
   Before fixing the palette the builder reads, with Valor's account,
   Telegram's standard list (`GetAvailableReactionsRequest`) and
   `SearchCustomEmojiRequest` for each glyph off it, and drops any glyph
   with neither; the build record lists each glyph's result.
2. **Two judgement sites** in `core/judgement_tasks.py`, both `Kind.PICK`
   over the palette's labels (one rubric each: glyph and when it fits),
   `error_cost` low, quiet (`ledger.QUIET_SITES`). **Floor 0.08 on both
   legs** (about three times the flat 1/40): several emoji fit one message
   and split the probability, and a wrong pick costs a mark, so the top
   label is taken unless the distribution is near flat. `JudgementTask`'s
   floor comment widens to say a PICK floor may sit under 0.5.
   - `reaction.handling`: inputs `message`, `thread`, `bound_as`. "Pick the
     reaction that shows Valor understood what is asked: its mood, or the
     work it plainly asks for. A question is not work."
   - `reaction.completed`: inputs `message`, `thread`, `bound_as`,
     `handling` (the emoji placed), `did` (the turn's activities, the
     existing `effect_did`/`tool_did` reading, `ORDER` kept as its order),
     `replied`, `outcome` (`done`, `errored`, `task ended`, or the binding:
     `none`, `start`, `stop`). `replied` is one of: "a reply to this
     message: <text>", "replied in this chat to message N: <text>", or "no
     text sent". "Pick the reaction that shows what came of it. No text
     sent means Valor agreed or acknowledged (a session answers anything
     else in text, `converse.md`), so pick an affirmative or warm emoji
     then (👍 for a yes, ❤ for thanks), never a doubtful one (🤔 🤷)."
   **Truthfulness** (Tom, 2026-10-17: plain questions got 👨‍💻): each
   activity emoji's rubric (👨‍💻 🔍 🧐 ✍ 📊 🎫 📅 📤 🫡) says it fits only
   when `did` names that activity, and the question says so too. The
   fixed activity map is context in `did` and the rubrics, not a picker.
3. **Stages.** `reaction.requested` stages, ranked: `seen` 👀 at binding
   (instant, unchanged), `handling`, `stalled` (an errored turn's),
   `done`. The unique index `(received_id, stage)` already allows one of
   each. The outbox (`core/bridge.py` `_reactions`) closes a row
   `superseded` when a higher-ranked row of the same message is already
   placed (today's rule, generalized), so a slow handling never lands over
   a done and 👀 is never skipped. `ReactionDue` gains `previous`: the
   names of the message's placed lower-stage rows. Slack's sender adds the
   new name, then removes each `previous` name that differs from it (today
   it removes only `eyes`), so 👍 then 👍 keeps 👍. Telegram replaces the
   list as now.
4. **Owed, then judged, in the resident loop.** Only a message with a
   `seen` row gets reactions, so a `heard` message in a `mentions` chat
   (Tom, 2026-10-17) never does. For each such message the turn read (the
   fold's window and its mid-turn steers, as now) with no `done`,
   `on_turn` builds a quiet `reaction.owed` row: `received_id`, `turn_id`,
   `stage` (`done`, or `stalled` for an errored turn), `did`, `replied`,
   `outcome`. It reads `did` from the transcript before `record`'s
   collection transaction and appends the rows inside it, after
   `turn.collected` (or its reduced or bare form), so a crash leaves both
   or neither and `_recollect` redoes both. The window is computed there,
   from the fold over the rows including `turn.collected` and
   `turn.ended`: seen rows after `steer_since` as it stood before the
   turn began, up to the turn's prompt row (`turn_rows[turn_id]`), plus
   its taken steers. A turn that spent them (`steer_since` moved to its
   prompt row) owes `done`; one that did not (errored) owes `stalled` on
   the same window, which the next turn's `done` replaces. `on_bound` writes 👀 and an
   owed `done` at binding for `start` (handed to a task), `none` and
   `stop` (👌 at once goes; a stop now gets 👀 then its done). `serve`
   regains the shaping jobs removed on 2026-10-17 (`Kernel.shape`, apart
   from task jobs). `reactions.pending` lists: owed rows with no reaction
   at their stage; seen rows with no done on a stopped or ended task (owed
   as `task ended`); and handling due, a seen row with no handling, no
   done and no owed row. A message with both due is judged once, for its
   done. Each item is one judgement metered on the bound task, then one
   `reaction.requested`; abstain, no port, or both legs failing
   `judgement.UNANSWERED_RUNS` times writes `noted` 📝. A compaction turn
   is not collected; the turn after it reads the messages.
   **Stopped tasks** cannot be billed (`spending.open_call` raises
   `tasks.TaskStopped`). An item whose task is stopped is not judged: it
   gets its done at once with a fixed label, 👌 for the `stop` binding and
   📝 otherwise; a `TaskStopped` raised mid-judgement does the same. Once
   written, `pending` no longer lists it.
   **Refused reactions.** A `reaction.failed` on a `done` or `stalled`
   row is listed by `pending` and gets a `noted` stage row (ranked
   highest), 📝, unjudged. A refused 📝 writes nothing more: the message
   keeps what it shows, which the delivery names per chat if it happens.
5. **Removed:** the `shape` stage for new rows, 🫡-at-start, 👌-at-stop,
   `final`, and `on_turn`'s single target. Old rows still read (`shape`
   ranks as `handling`). `noted` ranks above `done`.
6. **The session knows.** `reactions.shown`/`why` give each line, through
   `intake.chat_line`: `reacted 🤔 (handling: you read it as a question to
   think over)`, `reacted 👍 (done: no text sent; you answered with the
   reaction)`, or `(done: you replied in this chat to message N)`.
   `session.py`'s "Your reaction on message N is now X" line covers
   handling and done set after a turn read the message.
7. **Docs:** `core/reactions.py` docstring, `docs/bridges/telegram.md`,
   `docs/bridges/slack.md`, `docs/data.md` (stages, `reaction.owed`,
   `previous`), `docs/judgement-layer.md` (two sites, their floor),
   `reactions-activity.md` (a line pointing here), and
   `skills/sdlc/converse.md`: a plain yes or acknowledgement gets no text,
   the completed reaction answers it; a no, a doubt, or anything with
   content gets text; the reaction on each line is what
   the kernel set from what you did; asked about one, say which and why.

## Out of scope

The session choosing its own emoji; Slack workspace custom emoji;
calibration against human labels (the floor above stands until rows exist); email and local (they do not react).

## Tests (`tests/test_reactions.py`, scripted port)

- Palette: every label has a Telegram glyph and a Slack name; both sites'
  labels are exactly the palette (`noted` included).
- Floor: a top label at 0.3 among 40 is placed; a flat distribution gives 📝.
- Handling: a steer gets 👀 then the picked handling; abstain, failure and
  no port give 📝; a message with a done or an owed row is not judged for
  handling.
- Completed: a turn reading three messages writes three owed rows and three
  done reactions; a two-message turn with one send replying to the first
  passes "a reply to this message" for it and "replied in this chat to
  message N" for the other; a no-send turn passes "no text sent"; an
  errored turn writes `stalled`, then the next done turn's `done` replaces
  it; `start`, `none` and `stop` each get exactly seen then done; a steer
  on a task stopped before its turn ends on a done (`task ended`); a
  `heard` message gets no owed row and no reaction; a turn with empty
  `did` sends the completed rubric stating the activity emoji need the
  activity, and `did` of code passes `code`.
- Errored turn: a turn that errors after reading two steers writes
  `stalled` for both and nothing for a steer bound after its prompt row;
  the next done turn writes `done` for all three.
- Stopped: a `stop` ends on a placed 👌 done and a steer to a task stopped
  before its turn on a placed 📝 done, with no judgement row and nothing
  listed on the next `pending` pass; a port raising `TaskStopped`
  mid-judgement gives the same.
- No text: a no-send turn on "can we ship tomorrow?" is judged with
  `replied` "no text sent" and the rubric's agreement rule in the request.
- Refused: a `reaction.failed` on a done gives a placed 📝 `noted` row; a
  refused 📝 writes nothing further.
- Crash: `record` killed after its transaction leaves `turn.collected` and
  the owed rows together; killed before, `_recollect` writes both.
- Outbox: a handling requested after the done is placed is closed
  superseded; 👀 is placed before a requested handling; Slack removes a
  differing handling name after done, and keeps the name when handling and
  done are both `thumbsup`.
- Session: `chat_line` shows the handling and done text; the "is now" line
  appears for a done set after the read; an old `shape` row still shows.
- Existing cases asserting 🫡/👌/`final`/single-target are rewritten to
  the new rule.
- Live (the done criterion, rule 5): in Tom's DM, a request for work shows
  👀, a working handling, then a done; "thanks"/"yes" gets a reaction and no
  text; no message keeps 👀 once its turn ends.

**Governance.** None added: no check, gate or refusal; `pending` is the
same work-queue the shaping jobs had, and 📝 is a fallback value, not a
guard.
