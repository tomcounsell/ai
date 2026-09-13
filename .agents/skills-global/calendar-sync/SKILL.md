---
name: calendar-sync
description: "Reconstruct a day of repository work from git commits and sync inferred work blocks to its mapped Google Calendar."
---

# Calendar Sync

Resolve the repo, date range, and user's timezone. In Valor, match `working_directory` in `~/Desktop/Valor/projects.json`, then resolve that project in `~/Desktop/Valor/calendar_config.json`. Never silently choose the personal primary calendar; use a default only when the mapping explicitly authorizes it.
Collect the complete chronological commit history within timezone-aware boundaries. Group by feature or goal, not by commit. Construct non-overlapping blocks of at least 20 minutes, merging related clusters when needed. Label durations as inferred from commits rather than measured work time. A request merely to review today's work does not authorize calendar writes.
Read existing events on the resolved calendar. Match previously generated work-log events by a stable repo/date/cluster identity stored in their descriptions or supported private metadata. Time overlap alone is not identity: preserve unrelated meetings and manual events. Update matching events, create missing ones, and avoid duplicate writes on reruns.
Use authenticated `gws calendar`, a Calendar connector, or browser UI. With UI, verify the selected calendar before each save and wait for save completion before navigating. Re-fetch the day and report created/updated counts and any unresolved conflicts.
