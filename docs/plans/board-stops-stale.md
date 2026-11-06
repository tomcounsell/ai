# The board chat closes stale tasks, and asks before it does

**Ask.** Tom, valor team chat on Slack, thread #makes-good-project,
2026-11-06 06:57 UTC: "my DM is not a project manager so to the extent it
can create tasks it would need to be capable of canceling or closing them.
Maybe the best flow is to followup to ask if a task is still valid and an
emoji reaction by me is enough to keep it alive or close it out." He
accepted the read-back (👌, 06:58): the board chat may stop any task of its
project wherever it started; a question unanswered 7 days gets one
top-level post there asking whether it is still worth doing, with a
recommendation; 👍 keeps it waiting, 👎 stops it; the oldest such question
(a task started from Tom's DM, open 20 days, moot since Nov 1) goes first.

**Incident.** The board session's `task.stop` of that task was refused,
"task … does not report to this chat" (`tools/chat_tasks.py` `_child`,
which allows only `tasks.reporting`). `task.answer` already reaches any
waiting task of the project through `board.answers`; `task.stop` has no
such reach, so a task started in Tom's DM can be answered from the board
but never closed.

**Premise, checked.** `board.owe` runs on every kernel wake and writes a
board row only when the snapshot's digest changes; nothing in the snapshot
changes as a question ages, so the board session is never told one has
gone stale. `_posts` maps a send's `about` to its `ts`, prefix
`question:` stripped. This loosens a refusal within a project and adds no
check, gate or state: no governance grant needed.

## What is built

1. **`core/board.py`.** `covers(conn, session_id, task_id)`: the session
   is the board session of the task's project (the half of `answers`
   before its WAITING test; `answers` becomes `covers` and WAITING). Each
   open item gains `"stale": asked_at` older than `STALE_DAYS = 7`, so the
   digest changes the wake a question crosses 7 days and the board row is
   written then: no new routine. `text` adds to a stale item either
   `asked whether still worth doing in your post <ts>: 👍 keeps it
   waiting, 👎 stops it` (a send with `"about": "stale:<qid>"`) or
   `unanswered 7+ days: post here asking whether it is still worth doing,
   with your recommendation`.
2. **`tools/chat_tasks.py`.** `TaskStop._child` falls back to
   `board.covers`, as `TaskAnswer` does to `answers`; its payload takes
   `via` (`slack reaction :-1:`), recorded on the stop. Usage text says the
   board chat's session stops any task of its project. Docstring updated.
3. **`skills/sdlc/converse.md`, the decision board.** A new step: for an
   item marked `unanswered 7+ days`, post once at top level, the question
   in a line, how long it has waited, and your recommendation (keep or
   close), with `"about": "stale:<id>"`; it follows the Who-reads rule (a
   DM or client question is one line with no details). Tom's 👍 on that
   post: nothing to the task, canvas notes it kept. 👎: `task.stop` it,
   `"by": "tom"`, `"via": "slack reaction :-1:"`. Neither is a
   `task.answer`. The existing 👍/👎 meaning on question posts is unchanged.

After rollout the waiting DM task is past 7 days, so the first board row
on the board Mac carries it, and the board session posts it with the
recommendation to close.

## Out of scope

Any other chat's reach (Tom's DM stays a place to talk); `task.steer`
from the board; a new routine, timer or tracked state; persona changes
(the board section is where board flow lives); the posting itself (the
board session does it once the change is live).

## Tests (`tests/test_board.py`)

- The board session `task.stop`s another chat's task of its project, idle
  or waiting, and the stop row carries `by`/`via`; another project's task
  is refused; a same-project non-board chat is still refused a task that
  is not its own.
- A question asked 8 days ago is marked `unanswered 7+ days … post here`;
  one asked today is not; backdating a question across 7 days makes `owe`
  write a row (the digest moved); with a `stale:<qid>` post the item names
  that post instead.
- Existing board-text assertions still hold for fresh questions.

Full suite under 5 minutes; then merge and roll out (kernel restart for
`core/` and `tools/`).
