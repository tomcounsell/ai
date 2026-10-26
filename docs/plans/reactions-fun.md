# Reactions: the classifier picks a handling and a done reaction, and no 👀 is left hanging

**Ask.** Tom, valor team chat, 2026-10-25 15:16 and 2026-10-26 07:36 UTC:
"more fun and rarely leave behind hanging 👀"; "the classifier should
choose reactions for both handling and completed in all cases"; 📝 as the
backup; a plain yes is answered by the reaction alone; bigger work shows it
is being worked on. Supersedes `reactions-activity.md`'s "nothing is
guessed on arrival" (2026-10-17) on his later word; kept from it: 👀 at once
and never skipped, and no activity emoji for work not done.

**Premise, checked.** `on_turn` set one reaction per turn on one message; a
plain-reply turn, an errored turn, a `none` binding (`final`) and a steer to
a stopped task kept 👀; chat sessions' `task.steer` effects made most ends 🫡.

## What is built

1. **One palette** (`core/reactions.py` `PALETTE`, about forty labels): the
   activity emoji, expressive ones (👍 ❤ 🎉 🤔 🙈 🦄 …), `noted` 📝, `seen`
   👀; Telegram glyph and Slack name each. Telegram's standard list, as the
   bridge logged it on 2026-10-25, has all but 🔍 🧐 📤 🎫 📊 📅 ✅ 🚀 ⏳ 📝,
   which go as Premium custom emoji (🔍 🧐 📤 found by the search before).
2. **Two `pick` sites**, `reaction.handling` (message, thread, bound_as) and
   `reaction.completed` (also handling, `did`, `replied`, `outcome`), quiet,
   floor 0.08 (several emoji split the probability; a `pick` floor may sit
   under 0.5, said in `JudgementTask`, `decide` and the judgement doc). An
   activity emoji fits only when `did` names it; no text sent means
   agreement, so a doubtful emoji never answers a yes.
3. **Stages** `seen` < `handling` < `stalled` < `done` < `noted` (`RANK`).
   The outbox closes a row `superseded` below a later one: a 👀 once that is
   placed, any other once it is requested and not refused (a slow handling
   behind its done in one batch). `ReactionDue.previous` names the lower
   stages placed; Slack removes each that differs from the new name.
4. **Owed, then judged.** `reactions.owe`, in `record`'s transaction after
   `turn.collected`, writes `reaction.owed` for every 👀 message the turn
   read (the fold's window and its mid-turn steers): `done`, or `stalled`
   for a turn that ended in an error; a fresh or compaction turn owes none;
   `heard` messages have no 👀 and get nothing. `replied` is a reply to this
   message, a reply in its chat to message N, or no text. `on_bound` owes a
   `start` or `none` its done at once; a `stop` gets 👌. The resident kernel
   (`Kernel.react`, jobs apart from task jobs) writes each `pending` item:
   a refused done → 📝 `noted`; an owed done; a stopped task's message (not
   billable) → 📝; an owed stalled; else a handling. An abstain, no port, or
   two failed judgements give 📝. **Rollout:** only a 👀 written with
   `completes` (from this change) is ever due without an owed row, so no
   message bound before it is judged or reacted on again.
5. **The session knows** (`reactions.current`/`shown`, `chat_line`): each
   line says the reaction and why; the one set after a turn read a message
   reaches the next turn ("Your reaction on message N is now ✅: …").
6. **Docs:** reactions docstring, `docs/bridges/telegram.md`, `slack.md`,
   `docs/data.md`, `docs/judgement-layer.md` (shape 13), `core/README.md`,
   `skills/sdlc/converse.md` (send no text to a plain yes; the reaction says
   it; a no or a doubt gets text).

**Out of scope:** the session choosing its emoji; Slack custom emoji;
calibration (the floor stands until rows exist); email and local.

## Tests (`tests/test_reactions.py`)

Palette and sites; the floor (0.3 among forty proceeds, flat abstains); who
gets 👀 (heard: nothing owed); handling pick, abstain, two failures, no
port; three messages one turn, each its own done and `replied`; no text on a
yes-or-no question; errored turn → stalled → done; fresh/compaction owe
none; stop 👌; start owes its done; stopped task → 📝, also mid-judgement;
refused done → 📝 once; pre-change rows left alone; `record` writes owed
rows with `turn.collected` or neither; outbox: 👀 first, a handling behind
its done closed, `previous`; Slack keeps a repeated name; the session's
lines; the kernel's job. Live (rule 5): Tom sees a handling then a done, a
yes answered by a reaction alone, and no 👀 once the turn ends.

**Governance.** None added: no check, gate or refusal; `pending` is a work
queue and 📝 a fallback value.
