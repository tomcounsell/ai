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
2. **Two judgement sites** in `core/judgement_tasks.py`, both `Kind.PICK`
   over the palette's labels (one rubric each: glyph and when it fits),
   `error_cost` low, floors 0.55/0.60, quiet (`ledger.QUIET_SITES`):
   - `reaction.handling`: inputs `message`, `thread`, `bound_as`. "Pick the
     reaction that shows Valor understood what is asked: its mood, or the
     work it plainly asks for. A question is not work."
   - `reaction.completed`: inputs `message`, `thread`, `bound_as`,
     `handling` (the emoji placed), `did` (the turn's activities, the
     existing `effect_did`/`tool_did` reading, `ORDER` kept as its order),
     `replied` (the text the turn sent in reply to this message, or "no
     text sent"), `outcome` (`done`, `errored`, `task ended`, `bound
     none`/`start`/`stop` at binding). "Pick the reaction that shows what
     came of it; with no text sent, the reaction is the answer (👍 for a
     yes, ❤ for thanks)."
   The fixed activity map is context (in `did` and the rubrics), not a
   second picker.
3. **Stages.** `reaction.requested` stages, ranked: `seen` 👀 at binding
   (unchanged, instant), `handling`, `stalled` (an errored turn's), `done`.
   The unique index `(received_id, stage)` already allows one of each. The
   outbox (`core/bridge.py` `_reactions`) closes a row `superseded` when a
   higher-ranked row of the same message is already placed (today's rule,
   generalized from `seen`/`shape`), so a slow handling never lands over a
   done, and 👀 is still never skipped.
4. **Owed, then judged, in the resident loop.** `on_turn` writes, for every
   message the turn read (the fold's window and its mid-turn steers, as
   now) with no `done`, a quiet `reaction.owed` row: `received_id`,
   `turn_id`, `stage` (`done`, or `stalled` for an errored turn), `did`,
   `replied`, `outcome`. `on_bound` writes one at binding for `start`
   (handed to a task), `none` and `stop`, which get no handling. `serve`
   regains the shaping jobs removed on 2026-10-17 (`Kernel.shape`, kept
   apart from task jobs): `reactions.pending` lists seen rows with no
   handling and no done (handling due), owed rows with no reaction at their
   stage, and seen rows with no done on a stopped or ended task (owed as
   `task ended`). Each is one judgement on the bound task, then one
   `reaction.requested`; an abstain, both legs failing
   `judgement.UNANSWERED_RUNS` times, or no port writes `noted` 📝. A
   compaction turn is not collected; the turn after it reads the messages.
5. **Removed:** the `shape` stage for new rows, 🫡-at-start, 👌-at-stop,
   `final`, and `on_turn`'s single target. Old rows still read (`shape`
   ranks as `handling`).
6. **The session knows.** `reactions.shown`/`why` give each line, through
   `intake.chat_line`: `reacted 🤔 (handling: you read it as a question to
   think over)`, `reacted 👍 (done: no text sent; you answered with the
   reaction)`. `session.py`'s "Your reaction on message N is now X" line
   covers handling and done set after a turn read the message.
7. **Docs:** `core/reactions.py` docstring, `docs/bridges/telegram.md`,
   `docs/bridges/slack.md`, `docs/data.md` (stages, `reaction.owed`),
   `docs/judgement-layer.md` (two sites), `reactions-activity.md` (a line
   pointing here), and `skills/sdlc/converse.md`: a plain yes or
   acknowledgement gets no text, the completed reaction answers it; the
   reaction on each line is what the kernel set from what you did; asked
   about one, say which and why.

## Out of scope

The session choosing its own emoji; Slack workspace custom emoji;
calibration (no labels yet: floors are set later from rows, as `SHAPE`'s
were); email and local (they do not react).

## Tests (`tests/test_reactions.py`, scripted port)

- Every palette label has a Telegram glyph and a Slack name; both sites'
  labels are exactly the palette (`noted` included).
- Handling: a steer gets 👀 then the picked handling; abstain, failure and
  no port give 📝; a message already done is not judged.
- Completed: a turn reading three messages writes three owed rows and three
  done reactions, each judged with its own `replied`; a plain-ack turn
  passes "no text sent"; an errored turn writes `stalled`, and the next
  done turn's `done` replaces it; a `none`, `start` and `stop` binding each
  end on a done; a steer on a task stopped before its turn ends on a done
  (`task ended`); a turn with `did` of code passes `code`.
- Outbox: handling requested after done is placed is closed superseded;
  👀 is placed before a requested handling; Slack removes the previous name.
- Session: `chat_line` shows the handling and done text; the "is now" line
  appears for a done set after the read; an old `shape` row still shows.
- Existing `test_reactions.py` cases that assert 🫡/👌/`final`/single-target
  are rewritten to the new rule.
- Live (the done criterion, rule 5): in Tom's DM, a request for work shows
  👀, a working handling, then a done; "thanks"/"yes" gets a reaction and no
  text; no message keeps 👀 once its turn ends.

**Governance.** None added: no check, gate or refusal; `pending` is the
same work-queue the shaping jobs had, and 📝 is a fallback value, not a
guard.
