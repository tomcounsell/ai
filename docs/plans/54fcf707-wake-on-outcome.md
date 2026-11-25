# A chat session owing a follow-up on a send wakes when the send lands

Bug fix (hard rule 9): this file is removed in the fix's commit.

## The defect

2026-11-24, the Captain's #mastery-dev session: a hand-on asked for a test
mail and a note back. The turn requested `email.send`, said the note was
"owed once the outcome is back", and ended with no `wake.md`. An effect's
outcome never steps a chat session (`session.py` docstring; `router.ready`
steps on `chat_waiting` or `wake_due` only), so it ran again only when the
next hand-on came at 15:25 UTC, and the note never went.

Two gaps: the guidance (`skills/sdlc/converse.md`, "A wake is yours to
set") never says to set a wake when something is owed after a send; and the
wake can only be timed by a guessed delay, which fires either before the
outcome ("it has not gone out yet", another turn) or late.

## Options weighed

- (a) every outcome wakes an otherwise idle requester: a turn after nearly
  every reply, since a reply is usually the last thing a turn does. Refused.
- (b) only a failed outcome wakes it: the 24 Nov mail was sent, so it
  would not have fired. Does not fix the case.
- (c) guidance only, with today's delay wake: works, but the delay is a
  guess at when the outcome lands.
- Chosen: (c) plus one extension of the existing wake: `wake.md`'s first
  line may be `outcome post: <this turn's effect file>`, due the moment that
  effect has an outcome (or a refusal). Only a turn that asks pays a turn.

## What is built

1. `core/session.py`: `WAKE`/`_wake` accept `outcome` in place of the delay;
   it needs `post:` naming an effect file (`.json`), else the existing
   "not set" error with the form in it. `wakes()` gives such a wake
   `due` = the `at` of the first `effect.outcome` or `effect.refused` row
   for that effect's id, gathered from all rows before the wake loop as
   `sent` and `gone` are, so an outcome written before its turn's
   `turn.collected` (a performer that finishes inline during collection)
   counts; `due` is then at least `set_at`. When the post is already
   `gone` at collection (the entry carries an error, has no `effect_id`,
   or the turn requested no such file), `due` = `set_at`: woken at once
   and told it never went out. Otherwise no `due` yet (`owed` skips a
   `None`). A reply to the post still drops it; a finished turn that was
   given it spends it.
   `_wake_report` says "You asked to be woken once <file> had its outcome",
   then the existing sent/gone wording. `wake_due` is unchanged.
2. `skills/sdlc/converse.md`: one sentence under "A wake is yours to set":
   when you owe something once a send lands (a note back, a check on it),
   write `outcome post: <file>`; a send that owes nothing after sets none.
3. `core/record.py` `waits`: a wake with `due` None shows as due when
   the send lands, not a time.
4. Docs that describe `wake.md`: `core/session.py` and `core/signals.py`
   docstrings, `docs/harnesses.md` row, `docs/bridges/telegram.md` line.

Out of scope: any outcome waking a session that did not ask; a second
timer or scheduler; anything on task (non-chat) sessions, whose outcomes
already open their next prompt; changing what a delay wake does.

## Tests (`tests/test_wake.py`, `tests/test_kernel.py`)

- Replay of 24 Nov through `session.run`: a hand-on steers the chat session;
  its turn requests `email.send` (`mail.json`) and leaves `wake.md`
  `outcome post: mail.json` with a note; before the outcome `wake_due` is
  false; the bridge's `effect.outcome` (done, `sent` with a message id)
  lands; `wake_due` is true; the next turn runs with no new message and its
  prompt opens with `# Your wake-up` naming the outcome and the note; after
  it the wake is spent.
- A send with no wake: the outcome lands, `wake_due` stays false (the cost
  case: no turn added).
- A failed or refused outcome makes it due, reported as "never went out".
- The outcome row ordered before the wake's `turn.collected`: due at once,
  and the next prompt reports it.
- An `outcome post: mail.json` whose entry carries an error, and one naming
  a file the turn did not write: due at once, reported "never went out".
- `waits` lists a not-yet-due outcome wake without a time.
- A reply to the mail before the outcome drops it.
- `outcome` with no post, or with a message id, is "not set" with the error.
- The converse prompt carries the new sentence.

Governance: none. A wake the turn asks for; no check, gate or refusal.
