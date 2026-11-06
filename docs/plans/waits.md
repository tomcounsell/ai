# What matters most, and who it waits on

**Ask.** Tom, valor team chat on Slack, 2026-11-06 05:08 UTC, on the two
items the PM-heuristics task (bb17eeec8) left out: an answer "on any given
day if someone asks what the most important thing is ... read from the
Google sheet priority queue, and understanding dependencies"; waits
"computed if not tracked directly. a reminder to yourself to pick up a
stalled chat, should wake you up to use a tool or process that can identify
what/who is the blocker and with time measurement, judge whether to wait,
nudge, continue without someone"; "the real work for a PM is managing
expectations and keeping track of peoples contributions to prioritized
goals. much of that is being wise about timing and communication".
Answers (via the chat session, all defaults): Tom's waits in his working
hours (Mon-Fri 09:00-18:00 Bangkok) plus wall-clock, anyone else
wall-clock and "time zone unknown"; the asking chat's own project only;
the session names who and what it blocks in its wake note; with no Sheet,
someone waiting on Valor first, then dated commitments, then what
unblocks most.

**Premise, checked.** The ledger already holds both waits: a task's open
question (`question.asked`, unanswered, task in `waiting`; `core/board.py`
reads the same) and a chat session's own posts awaiting a reply
(`session.wakes`: post, set-at, due, note). The Sheet's id lives in room
text, and the kernel does not read Sheets, so the Blocked tab is read from
the turn (`sheet.py`). Tom's zone is in `persona/identity.toml`; nobody
else's is recorded.

## What is built

1. **`valor-record waits`** (`core/record.py` verb `waits`,
   `tools/valor_record.py`). For the asking task's project (`reach`),
   oldest first, every open wait:
   - *question*: the waiting task, its request (what it blocks), the
     question, on whom (the chat session it reports to, by chat and room),
     asked-at;
   - *post*: each chat session's pending wake with a post: chat, room, the
     post, set-at, and the note (who and what it blocks, as the session
     wrote it).
   Each shows wall-clock waited and hours of Tom's working time waited
   (zone from `persona.identity`). The record does not say who a wait is
   on beyond the chat and the note, so the line prints both; the guidance
   says use Tom's hours when it is on Tom, and wall-clock with "time zone
   unknown" for anyone else unless the room's notes give their zone.
   A SELECT plus `session.wakes`; nothing stored.
2. **`sheet.py blocked SHEET [--today]`**: the Blocked tab's open rows,
   each with days since `asked on` and whether `chase on` has come. Reuses
   `values`/`rows`; `due` stays as it is.
3. **The wake-up.** `session._wake_report` ends with one line: run
   `valor-record waits` (and in a product room `sheet.py blocked`) before
   choosing wait, nudge, go ahead on a two-way door, or hand to the next
   best person, by "Nothing stalls on one person"; and say in the chat
   what was decided. `skills/sdlc/converse.md` wake section: one line that
   a wake note on a question names who it waits on and what it blocks.
4. **Persona (`persona/conduct.md`, mirrored in `docs/persona.md`).** Run
   the project's opening line takes Tom's framing: the work is managing
   expectations and keeping track of people's contributions to the
   priorities, with good timing and communication. One new item, **What
   matters most, any day**: answer from the ranked queue (the product
   Sheet where there is one; else the open tasks: someone waiting on Valor
   first, then dated commitments, then what unblocks most), naming the
   pick, its blocker, who, how long (`valor-record waits`), and what you
   did about it. No routine.
5. Docs: `tools/README.md`, `core/README.md` verb lists; the product-sheet
   SKILL.md names `blocked`.

**Out of scope.** A per-person time-zone list; waits across projects;
inferring the person from post text; any check, gate or approval: nothing
here blocks, refuses or approves (no governance, no grant needed).

## Tests

- `tests/test_record.py`: `waits` lists an open question with its task's
  request and asked-at; an answered question drops out; a task not in
  `waiting` drops out; a session's pending wake with a post appears with
  its note, a replied-to one does not; another project's waits stay out;
  the CLI prints a line per wait and `--json`.
- Working hours: a wait from Friday 17:00 to Monday 10:00 Bangkok counts
  2h; one inside a weekday counts its span; wall-clock alongside.
- `tests/test_product_sheet.py`: `blocked` lists open rows with days
  waited, leaves cleared rows out, flags a due chase.
- `tests/test_wake.py`: the wake-up prompt carries the `valor-record
  waits` line.
- Persona tests updated where they assert the moved text; full suite.

**Stakes.** Prompt text plus a read-only verb; a mistake shows a wrong
age or misses a wait, undone by a commit.
