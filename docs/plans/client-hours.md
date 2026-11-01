# Client hours on Valor's calendar

Task 2c7b30edba4c. Tom, 2026-11-01 (Slack, Valor team chat): "Let's go ahead
and build that and have it start working from November 1st." Rule 2: the old
system logged Valor's hours to per-client calendars (`tools/valor_calendar.py`,
`calendar-sync` at `old-system`); the rebuild dropped it, and October's hours
were rebuilt by hand. Done when Tom invoices a client from these events at the
end of November (rule 5).

## What is built

1. **`hours` in the record** (`core/record.py`). For one project spec, using
   the existing `reach` (the project's tasks and chat sessions, tasks of
   other projects never included): every turn's `[turn.started.at,
   turn.ended.at]` (a turn reaped or never ended runs to its `turn.reaped`
   row, else is left out). Rules, in order: union overlapping turns; join
   blocks whose gap is 20 minutes or less; split at Asia/Bangkok midnight;
   round each block's start down and end up to the 6-minute grid (0.1h);
   a block under 12 minutes widens its end to 12. Each block carries its
   tasks' ids and a title: the first line of the longest-running task's
   instruction, cut to 60 characters, plus "(+N more)" when there are
   others. No model call: the instruction is already a plain sentence.
   Exposed as `valor-record hours [--since DATE] [--until DATE] [--json]`
   (`tools/valor_record.py`), which a turn reads for its own project.
2. **Routine `hours`** (`routines/hours/`: `routine.toml`, `runner.py`),
   daily at 02:00, installed on every Mac; ceiling `act`. For each
   `Spec.here()` whose spec has `calendar = {id = "...", since = DATE}`:
   window = the last 7 days, never before `since`. It lists the calendar's
   events in the window carrying the private extended property
   `valor_hours=1`, diffs them against the record's blocks by (start, end):
   a match with a different title is patched, a tagged event matching no
   block is deleted, a missing block is inserted with the tag. Untagged (hand-made) events are never touched. Writes go
   through `gws calendar events …` as valor@yuda.me. Each write is a
   `calendar.written` row on the run (project, op, start, end, event id);
   a gws failure is a `calendar.unwritten` row and the other projects go on.
3. **`Spec.calendar`** (`core/workspace.py`): an optional table, `id` and
   `since`. The public repo names no client and no calendar id.

## Private side (rollout, not in the diff)

- `calendar = {id, since = 2026-11-01}` in the cyndra, psyoptimal and
  mastery specs in `~/Desktop/Valor/projects/`; ids from
  `calendar_config.json`. A new "Mastery" calendar on valor@yuda.me,
  shared with tom@yuda.me (make changes), as Cyndra's and PsyOPTIMAL's are.
- The three work-vault invoice skills' Valor rows read "the client's
  calendar on valor@yuda.me, events tagged `valor_hours`, hours = duration"
  in place of the CSV Tom prompts for; committed in the vault.
- Plist installed on the Bald (cyndra) and the Captain (psyoptimal,
  mastery) per `docs/rollout.md`; one manual run there, then a read of each
  calendar to check November's events against `valor-record hours`.

## Out of scope

October: no events written, edited or removed on any calendar (Tom: the
invoices are final). SATSOL, Fuse and every other project. Tom's or
Charlie's hours. Billing rates, invoice rendering, Mercury.

## Tests (real Postgres; `gws` is a stub executable on `PATH` that keeps
events in a JSON file, as Telegram is a local emulator)

- Hours: two overlapping turns count once; a 20-minute gap joins, 21 splits;
  a block across Bangkok midnight becomes two; rounding to 0.1h and the
  0.2h floor; a reaped turn ends at its reap; a task of another project and
  a chat session outside the project's chats are excluded; a project chat
  session's turns are included; same rows twice give the same blocks.
- Routine: first run inserts tagged events; a re-run writes nothing; a
  changed title patches in place; a block that vanished deletes only its
  tagged event; a hand-made overlapping event is untouched; nothing before
  `since`; a spec without `calendar`, or placed on another Mac, is skipped;
  a gws failure on one project leaves a `calendar.unwritten` row and the
  next project still written.
- Read-only check before rollout: `valor-record hours` for October on the
  Bald and the Captain against the hand-built October events' totals, as
  numbers in the delivery; nothing written.

## Docs

`docs/routines.md` and `routines/README.md` (the `hours` runner),
`tools/README.md` (`hours`), `docs/performers.md` (Google Calendar writes
as valor@yuda.me), `projects/example.toml` (a made-up `calendar` table).

No governance is added: no check, gate or approval step.
