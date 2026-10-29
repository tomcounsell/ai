# A chat session wakes itself (#3654)

**Ask.** Tom, valor team chat, 2026-10-17: "you should have a natural timer
set whenever you ask a question to wake yourself and decide whether it's
worth waiting for an answer. then you can say something like 'Anything I
missed? if not, i'll go ahead'". Answers given 2026-10-29: a general "wake me
in N"; anything that wakes the session cancels it; the session picks the
delay and the kernel sets no bound; the wake prompt gives Tom's time; chat
sessions only.

**Premise, checked.** A chat turn is a one-shot `claude -p --resume`, so
Claude Code's ScheduleWakeup or CronCreate dies when the turn ends. The
kernel already steps a task on a timer (`router.retry_due`, checked by
`has_step` every `serve_tick_s`, 60s). A wake is one more clause there: no
new timer, process or table.

## What is built

1. **The request.** `.valor/wake.md` becomes a text signal (`core/signals.py`).
   Its first line is a delay: `90s`, `20m`, `2h`, or a bare number of
   minutes. The lines after it are a note to itself. It counts only from a
   chat turn that finished (`_stage_verdict`). From any other turn it goes to
   `errors` as "wake means nothing outside a chat session". An unreadable
   delay is reported in `errors` and ignored. `_collected` records
   `"wake": {"after_s", "note"}` on the turn's `turn.collected`, or None.
2. **When it is due.** `session.wake_due(rows, now)` is true when the
   latest `turn.collected` carries a wake, `now >= its at + after_s`, and no
   `turn.started` and no waking row (`CHAT_WAKES`: a `message.steered` or a
   `child.reported`) comes after it. So any message that wakes the session
   cancels the wake, and so does a report. A heard message does not. A later
   turn replaces the wake or drops it. Because a turn started after it
   ends the wake, a wake turn that fails falls back to `retry_due`'s
   backoff and is never stepped every tick. `router.has_step` becomes
   `not chat or chat_waiting or wake_due or retry_due`.
3. **The prompt.** For a chat, `next_prompt` adds `# Your wake-up` when the
   wake is due: "You asked to be woken 20 minutes after your last turn,
   which ended at 13:40 (Tom's time, UTC+7; 06:40 UTC). Nothing has woken
   you since. Your note: …". The time zone comes from `persona.identity`'s
   `timezone`. Heard messages appear above it, as `chat_prompt` shows them
   now.
4. **The guidance.** `skills/sdlc/converse.md`, item 2: whenever the
   session asks, it leaves `wake.md` with a delay sized to the question
   (about 15–30 minutes in a live exchange, longer when Tom has stepped
   away). On the wake it either keeps waiting (another `wake.md`), or posts
   "Anything I missed? If not, I'll go ahead" and starts the work on its
   stated defaults. Silence is go only for work it would have started
   anyway; when the default is hard to undo, it keeps waiting. The
   signals line ("`stop.md` … `compact.md` …; nothing else counts") names
   `wake.md`. Docstrings in `core/signals.py` and `core/session.py`, and the
   chat-session paragraph of `docs/bridges/telegram.md`, say when a session
   is stepped.

**Out of scope.** Tasks waiting on a question (unchanged; they stay
`waiting`). Any floor or ceiling on the delay. Tom's DM gets the
same mechanism, since it is a chat session, but no separate guidance.

**Governance.** None added. The wake holds, refuses and redirects nothing.
It only adds one more reason the kernel steps a listening session.

## Tests (`tests/test_steering.py`, beside the compaction cases)

- `wake_due`: not due one second before, due at the delay; a later
  `message.steered` cancels it, and so does a `child.reported`; a later
  `message.heard` does not, and the heard message appears in the wake prompt;
  a `turn.started` after it (a failed wake turn) leaves it not due; a newer
  `turn.collected` without a wake drops it, and one with a new wake replaces it.
- Delay parsing: `90s`, `20m`, `2h` and `25` (minutes); garbage goes to `errors`
  with no wake.
- `wake.md` from a non-chat turn goes to `errors`; one from an unfinished
  chat turn is not recorded; one beside `stop.md` does not survive the end.
- `has_step` is true for a listening chat session whose wake is due, and
  false before.
- The prompt names the delay, the end time in UTC+7 and UTC, and the note.
- `converse.md` tells the session to leave `wake.md` after a question and
  carries the go-on-silence rule.
- Full suite under 5 minutes.

**Done when** (unchanged, #3654): in the valor team chat a real session
asks Tom a question, he says nothing, it wakes on its own, posts the
"anything I missed?" line and goes ahead. This needs the merge, so the
delivery names it as open.
