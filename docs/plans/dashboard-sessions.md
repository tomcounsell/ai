---
tracking: none
slug: dashboard-sessions
type: feature
status: built (2026-10-20); Tom uses it as a Chrome app; the done bar waits on /live seen while a task runs
critique_rounds: 0
review_rounds: 0
---

# The status page lists sessions first, shows what is alive, then routines

Tom's ask (2026-10-20): "listing sessions and each's meta data was the most
important feature. that would be tasks now and perhaps a tree of running
kernels/subagents to see what is alive. followed by listing reflections (now
called routines)." The old dashboard (`_archive_/docs/features/dashboard.md`,
Jobs table, Liveness Signals) is the reference. A capability on Tom's ask
(hard rule 2); it adds no check, gate, limit or review step.

## What is wrong today

`/` prints all 145 tasks flat, each with its whole instruction and the
attention counts as JSON, oldest root and newest child mixed. 66 tasks are
children (the team chat alone has 15) and nothing nests them. Nothing says
which task needs Tom, which has a turn running, or what is alive.

## Change

- **Tasks (`/`).** Three groups: *Needs you* (each unsettled task with an
  unanswered question or a failed step, flat), *Active* (open, not settled), *Recent*
  (settled roots, the newest 15; `?all=1` lists every one, as the old
  page's settled-Job cap did). A child is listed under its root in a
  collapsed tbody, opened by a ▶ count toggle (CSS only). Each row: project, the instruction's first line
  (whole text on the task page), kind (chat and its channel, routine name,
  or task), harness and model, state with a `turn running` mark, started,
  last row as an age, the tree's metered spending, and only the non-zero
  attention counts and the open questions.
  `tasks.index` gains the Brief's metadata (`project`, `chat`, `routine`,
  `harness_name`, `model`, `created_at`), `open_questions`, `turn_open` and
  `failed_step`.
- **Live (`/live`).** `runs.live(conn)`, a read model: the kernel's and
  each bridge's lock connection (`pg_stat_activity`: `valor-kernel@<commit>`,
  `valor-<channel>@<commit>`) with when it connected; each turn with a
  `turn.started` and no `turn.ended`, with its task, state or fresh stage,
  harness, model, start, and its live processes (`_turn_processes`, the
  reaper's own finder); under each, its session's subagents
  (`<session>/subagents/agent-*.meta.json`: type, description, depth,
  finished), found under the task's `state/work` or `checks/*` config dir.
  Below, the newest turns that ended, with outcome and duration.
  The page reloads itself every 5 seconds while no modal is open.
- **Routines.** The same content; `need` as text, times as local
  `MM-DD HH:MM`. Each routine shows its newest 5 runs; older ones collapse
  under a toggle.
- **Attention.** Newest first; what was said and the answer clipped to
  three lines (the row opens the task's modal).
- **Shared.** Times render local and short; ages as `3m`, `2h`, `4d`. The
  old dashboard's dark theme and header (Tom, 2026-10-20): the title, page
  buttons, and Services from `rollout.services` (kernel and each installed
  bridge, green with uptime, red when not connected). A click on a task row
  opens a modal of its metadata (`/task/ID/modal`: details, timing,
  questions asked, instruction, children, turns), as the old session modal
  did. Ids are never printed; rows carry them as `data-task` (Tom,
  2026-10-20). One script, `ui/static/app.js`, opens the modal and reloads.

## Tests

`tests/test_ui.py`, against the suite's database: a child nests under its
root; a task with an unanswered question is under *Needs you*; a settled
root past the newest 15 shows only with `?all=1`; an open turn and a
subagent meta file show on `/live`; the modal shows a task's metadata and
names a child by `data-task`, not text; every page escapes a planted `<script>`.

## Done

Tom, 2026-10-20: "looking better", saved as a Chrome app. Not yet seen:
`/live` while a task runs.

Tom has opened `/` and `/live` while a task runs and said what he sees.
