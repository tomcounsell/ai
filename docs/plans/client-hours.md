# Client hours on Valor's calendar

Task 2c7b30edba4c. Tom, 2026-11-01 (Slack, Valor team chat): "Let's go ahead
and build that and have it start working from November 1st." Rule 2: the old
system logged Valor's hours to per-client calendars (`tools/valor_calendar.py`,
`calendar-sync` at `old-system`); the rebuild dropped it, and October's hours
were rebuilt by hand. Done when Tom invoices a client from these events at the
end of November (rule 5).

## What is built

1. **`hours` in the record** (`core/record.py`), for one project spec over a
   range of whole Asia/Bangkok days, using the existing `reach` (the
   project's tasks and chat sessions; other projects' tasks never). Input:
   every turn overlapping the range, `[turn.started.at, end]`. `end` is
   `turn.ended.at`, except a turn ended `interrupted` with reason `kernel
   restarted` (`core/serve.py`), stamped at the restart, not the death: it
   ends at the last row carrying its `turn_id` before that `turn.ended`
   (its last `gateway.*` call), or is dropped if there is none. Rules, in
   order: union overlapping turns; join gaps of 20 minutes or less; split
   at Bangkok midnight and keep the days in the range; round start down and
   end up to the 6-minute grid (0.1h); a block under 12 minutes widens its
   end to 12, or its start backwards when its end is a midnight split.
   Each block carries its task ids. `valor-record hours [--since D]
   [--until D] [--json]` (`tools/valor_record.py`) prints the blocks.
2. **Routine `hours`** (`routines/hours/`: `routine.toml`, `runner.py`),
   daily at 02:00, installed on every Mac; ceiling `act`. For each
   `Spec.here()` with `calendar = {id = "...", since = DATE}`, the window
   is whole Bangkok days from max(`since`, today − 7) to now. It lists the
   calendar's events starting in the window that carry the private
   extended property `valor_hours=1`, and for each block of `hours` over
   the window: a tagged event overlapping it is patched to its start and
   end, keeping its title; with none, an event is inserted with the tag.
   A tagged event overlapping no block is deleted. Untagged (hand-made)
   events are never touched. Events have no attendees and are written with
   `sendUpdates=none`, so no Google mail leaves. gws is run by its path,
   `settings.gws` (`VALOR_GWS`, default `/opt/homebrew/bin/gws`), since
   `launchd_path()` does not include it. Each write is a `calendar.written`
   row on the run (project, op, start, end, event id); a failed write is
   `calendar.unwritten`, and the other blocks and projects go on.
3. **Titles, client-readable, written once per block** (Tom approved this):
   a new block's title comes from the `light` seat (`claude-haiku-5-5`),
   one `claude -p` through the kernel's `Gateway` on a grant for the run,
   as `call.Front` does, so it is metered on the run. Input: the block's
   tasks' delivered results (`turn.ended` result text, in Valor's own
   words), not their instructions, which for a chat session open with the
   chat id. Output: under 60 characters, the value delivered, no tooling
   words. Kept on re-runs (patches keep the title). A failed title call
   skips that block's insert, as `calendar.unwritten`; the next run retries.
4. **`Spec.calendar`** (`core/workspace.py`): an optional table, `id` and
   `since`. The public repo names no client and no calendar id.

## Rollout, in this order

1. The merge lands; every Mac updates (`docs/rollout.md`), because
   `Spec.from_dict` refuses an unknown key and an old Mac would drop a spec
   carrying `calendar`, unbinding its chats.
2. Only then: `calendar = {id, since = 2026-11-01}` in the cyndra,
   psyoptimal and mastery specs in `~/Desktop/Valor/projects/` (ids from
   `calendar_config.json`); a new "Mastery" calendar on valor@yuda.me,
   shared with tom@yuda.me (make changes). Each spec loaded with the loader.
3. Plist installed on the Bald (cyndra) and the Captain (psyoptimal,
   mastery); the live check is `launchctl kickstart` of the installed job,
   then a read of each calendar against `valor-record hours` for November.
4. The three work-vault invoice skills' Valor rows read the client's
   calendar on valor@yuda.me, events tagged `valor_hours`, hours =
   duration, in place of the CSV Tom prompts for; committed in the vault.

## Out of scope

October: nothing written, edited or removed on any calendar (Tom: the
invoices are final). SATSOL, Fuse and other projects. Tom's or Charlie's
hours. Rates, rendering, Mercury.

## Tests (real Postgres; a stub `gws` at `VALOR_GWS` keeps events in a
JSON file; titles from the local scripted upstream)

- Hours: overlapping turns count once; a 20-minute gap joins, 21 splits; a
  block across midnight becomes two; 0.1h rounding; the 12-minute floor; a
  23:55–24:00 block widens backwards, not into the next day; a turn ended
  by a kernel restart ends at its last call, and with no call is dropped;
  another project's task and a chat outside the project's chats are
  excluded; a project chat session's turns are included; same rows twice,
  same blocks.
- Routine: first run inserts tagged events with no attendees and
  `sendUpdates=none`; a re-run writes nothing; a block that grew patches
  its event's times and keeps its title; a vanished block deletes only its
  tagged event; a hand-made overlapping event is untouched; a turn spanning
  the window's start yields no second event; nothing before `since`; a
  spec without `calendar`, or on another Mac, is skipped; a chat-session
  block's title comes from its results, not its chat id; a gws failure on
  one project leaves `calendar.unwritten` and the next project is written.
- Read-only, before step 2: `valor-record hours` for October on the Bald
  and the Captain against the hand-built October events' totals, reported
  as numbers; nothing written.

## Docs

`docs/routines.md`, `routines/README.md`, `tools/README.md`,
`docs/performers.md` (Calendar writes as valor@yuda.me, no invites),
`projects/example.toml` (a made-up `calendar` table).

No governance is added: no check, gate or approval step.
