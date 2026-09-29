---
name: calendar-sync
description: "Log a day's git work to the repo's Google Calendar as time blocks. Triggered by 'sync my calendar', 'log today's work to calendar', 'daily lookback', 'what did I work on today'."
allowed-tools:
  - Bash(git log:*)
  - Bash(git rev-parse:*)
  - Bash(cat:*)
  - Bash(gws:*)
  - Read
  - ToolSearch
  - mcp__claude_ai_Google_Calendar__*
  - mcp__byob__*
argument-hint: "[date or date range, default: today]"
context: fork
model: sonnet
effort: medium
---

# Calendar Sync

Turn the current repo's git commits for a date range (`$ARGUMENTS`, e.g. "yesterday",
"2026-07-10", "2026-07-08 to 2026-07-10"; default today, local timezone) into
time-blocked events on that repo's mapped Google Calendar, then report a short summary.

**Done when** every distinct chunk of work in the range has exactly one event on the
resolved calendar: titled by the feature or end-user goal (not a commit subject), a
description summarizing the underlying commits, at least 20 minutes long, not
overlapping this skill's other events, and a re-fetch of the calendar confirms it.
Reruns for the same range must update, not duplicate.

## Constraints

- **Only touch events this skill created.** Every event it writes carries the marker
  line `[calendar-sync:<repo-dir-name>]` at the end of its description. Update or delete
  an existing event only when its description carries that marker for this repo. Never
  modify, move, or delete any other event, even when it overlaps a proposed block: real
  meetings live on these calendars. A proposed block that overlaps an unmarked event is
  still created alongside it.
- On a rerun, marked events in the range that match a proposed block are updated in
  place; marked events that no proposed block covers any more are deleted. Everything
  else is left alone. No approval gate is needed within those bounds.
- **Resolve the calendar; never default to primary.** Match the repo root
  (`git rev-parse --show-toplevel`) against each project's `working_directory` in
  `~/Desktop/Valor/projects.json` to get the project slug, then look that slug up in the
  `calendars` map of `~/Desktop/Valor/calendar_config.json`. Use the map's `"default"`
  entry only when no project matches. The personal primary calendar is a valid target
  only when the mapping says `"primary"`/`"dm"`. Use only `calendar_config.json` from that
  directory; the `.calendar_hook_*` files beside it belong to a retired hook system.
- Group commits that share an issue number, plan doc, subsystem, or PR into one event.
  Extend or merge a cluster shorter than 20 minutes rather than leaving a short event.

## Write path

Use the first that works: `gws calendar` (when `gws auth status` shows valid
credentials), then the Google Calendar MCP tools, then browser automation on
calendar.google.com via the `render?action=TEMPLATE&text=&dates=&details=` prefill URL.

Browser tier gotchas: the prefilled editor defaults to the personal primary calendar, so
switch the calendar dropdown to the resolved calendar before saving every event. Don't
batch "click Save" with navigating to the next event: the editor raises a "Leave site?"
dialog if the save hasn't completed. Save, confirm the URL dropped `/eventedit`, then
move on.
