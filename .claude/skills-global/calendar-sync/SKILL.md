---
name: calendar-sync
description: Reconstructs a day's work from git commit history in the current repo, groups it into time-blocked calendar events (merged by feature/goal, minimum 20 minutes each), and writes them to that repo's mapped Google Calendar — creating new events or updating existing overlapping ones so reruns stay idempotent. Replaces the old hook-based time-tracking system. Use when the user wants to log/sync their day's work to their calendar, review what they did today, or backfill a prior day. Triggered by '/calendar-sync', 'sync my calendar', 'log today's work to calendar', 'daily lookback', 'what did I work on today'.
allowed-tools:
  - Bash(git log:*)
  - Bash(git rev-parse:*)
  - Bash(cat:*)
  - Bash(gws:*)
  - Read
  - ToolSearch
  - mcp__claude-in-chrome__tabs_context_mcp
  - mcp__claude-in-chrome__tabs_create_mcp
  - mcp__claude-in-chrome__navigate
  - mcp__claude-in-chrome__computer
  - mcp__claude-in-chrome__find
  - mcp__claude-in-chrome__get_page_text
  - mcp__claude-in-chrome__browser_batch
argument-hint: "[date or date range, default: today]"
context: fork
---

# Calendar Sync

Turns a repo's git commit history for a given day into time-blocked events on
that repo's own Google Calendar. Fully autonomous — no approval gate, full
CRUD freedom on the target calendar once it's correctly resolved.

## Inputs
- `$ARGUMENTS`: Optional date or date range to sync (e.g. "yesterday", "2026-07-10", "2026-07-08 to 2026-07-10"). Defaults to today if omitted.

## Goal
Every distinct chunk of work done in the repo during the target date range has
exactly one correctly-timed, correctly-titled, >=20-minute event on the repo's
mapped calendar — with no duplicates and no events left on the wrong (personal
primary) calendar.

## Steps

### 1. Resolve scope and date range
Determine the repo root (`git rev-parse --show-toplevel` from cwd) and parse
`$ARGUMENTS` into a concrete start/end timestamp range, defaulting to today
00:00–23:59 in the user's local timezone.

**Success criteria**: repo root path and a concrete `[start, end]` timestamp range.

### 2. Resolve the target calendar (do not default to primary)
Read `~/Desktop/Valor/projects.json`, match the repo root against each
project's `working_directory` to find the project slug (e.g.
`cyndra-consulting` → `cyndra`). Read `~/Desktop/Valor/calendar_config.json`'s
`calendars` map and look up that slug to get the Google Calendar ID. Fall
back to the map's `"default"` entry only if no project match is found.

**Rules**:
- Never silently fall back to the personal primary/"Valor Engels" calendar for a repo that has its own mapped calendar — that was the mistake made the first time this process ran manually.
- This mapping data is reused as-is from the existing config; do not read or reimplement the old `.calendar_hook_*` hook logic in that same directory — this skill replaces it.

**Success criteria**: a resolved Google Calendar ID that is not the personal primary calendar unless the project's mapping explicitly says `"primary"`/`"dm"`.

### 3. Gather commit history
Run `git log` scoped to the resolved date range in the repo root (subjects,
bodies, and timestamps — `--since`/`--until`, `--date=format:'%Y-%m-%d %H:%M'`).

**Artifacts**: chronological list of `{timestamp, subject, body}` commits for the range.

**Success criteria**: full commit list for the range captured, nothing missed at the range boundaries.

### 4. Group commits into events
Cluster commits that share a common feature/goal (same issue number, plan
doc, subsystem, or PR) into a single event rather than one event per commit.
Title each event with the feature/end-user goal, not a literal commit
subject; the description summarizes the underlying commits.

**Rules**:
- Every event must be at least 20 minutes long. If a natural cluster's span is shorter, extend it (merge into an adjacent same-feature cluster, or pad up to the 20-minute floor) rather than leaving a sub-20-minute event.
- Events must not overlap.

**Success criteria**: ordered list of `{title, start, end, description}` events, each >=20 minutes, non-overlapping, covering the day's real work.

### 5. Resolve the write tool
Try in order: (a) `gws calendar` if `gws auth status` shows valid credentials — target the calendar ID from step 2 directly; (b) a Calendar MCP tool if one is loaded for this session; (c) browser automation on calendar.google.com as the last resort, using the `render?action=TEMPLATE&text=&dates=&details=` prefill URL.

**Rules**:
- On tier (c), the prefilled event editor defaults to the personal primary calendar — you must switch the calendar dropdown to the resolved project calendar (step 2) before saving every single event.
- On tier (c), do not batch "click Save" immediately followed by "navigate to the next event" in one call — Google Calendar's editor blocks navigation with a "Leave site?" dialog if the save hasn't visually completed. Click Save, screenshot to confirm the URL dropped `/eventedit`, then navigate to the next event.

**Success criteria**: a working write path confirmed (auth valid, or tool present and responsive).

### 6. Deduplicate against existing events
Read events already on the resolved calendar for the date range. For each
proposed event from step 4 that time-overlaps an existing event, update that
existing event's title/time/description in place instead of creating a new
one. Only create net-new events for time slots with nothing existing.

**Success criteria**: no duplicate or overlapping events remain on the calendar after this run, including on reruns for the same day.

### 7. Create/update events
Execute the writes (creates and in-place updates) via the tier resolved in step 5.

**Success criteria**: every event from step 4 exists on the resolved calendar exactly once, correctly timed/titled/described.

### 8. Verify
Re-fetch the calendar's day view (API read or day-view screenshot) for the
resolved calendar and date range.

**Success criteria**: every proposed event is visibly present, non-overlapping, and correctly timed; report a short summary back to the user.

## Notes for the future scheduled version
A later daily cron-scheduled variant of this will also ingest local machine
activity alongside git commits as an input to step 4's grouping. Keep step 3
("gather commit history") as a swappable input-gathering step rather than
hardcoding git as the only source, so that addition slots in cleanly.
