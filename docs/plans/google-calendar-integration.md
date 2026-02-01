---
status: Ready
appetite: Small (1-2 days)
owner: Valor Engels
created: 2026-01-31
finalized: 2026-01-31
tracking: https://github.com/tomcounsell/ai/issues/20
google_cloud_project: Yudame General
google_cloud_project_id: quickstart-1586433403044
google_project_number: 224102219743
credentials: ~/Desktop/claude_code/google_credentials.json
---

# Work Time Tracking via Google Calendar

## Problem

Valor's work time across projects and chat groups isn't tracked or visible on calendar. When working on a task, there's no automatic time logging showing what was worked on and for how long.

**Current behavior:**
- No automatic time tracking when Valor starts/ends sessions
- Work time not visible on calendar
- Can't see time allocation across projects
- No historical record of work sessions

**Desired outcome:**
- Simple CLI command: `valor-calendar "task-name"` logs work time
- Automatically creates or extends calendar events for current task
- Each project/chat group has its own calendar
- Works offline with queue system when OAuth temporarily broken
- Automatically triggered at session start/end

## Appetite

**Time budget:** Small (1-2 days)

**Team size:** Solo (Valor)

## Solution

### Key Elements

- **CLI Tool**: `valor-calendar` command for simple time tracking
- **Smart Event Management**: Creates new event or extends existing event for same task
- **Per-Project Calendars**: Each repo/chat group maps to its own Google Calendar
- **Offline Queue**: Logs work locally when OAuth broken, syncs when auth restored
- **Auto-Hooks**: Triggered at session start/end automatically

### Flow

**Typical usage**:
Session starts → Bridge calls `valor-calendar "project-name: task-description"` → Tool checks if event exists for this task today → Either creates new event or extends existing → Returns confirmation

**Offline scenario**:
Session starts → `valor-calendar "task"` → OAuth fails → Logs to queue file → Session ends → OAuth restored → Queue processor syncs pending events → Queue cleared

**Example flows**:
1. **New task at 5:08**: `valor-calendar "ai-repo"` → Creates event "ai-repo" from 5:00-5:30 on ai-repo calendar
2. **Same task at 5:18**: `valor-calendar "ai-repo"` → Event already covers 5:00-5:30, no change
3. **Same task at 5:42**: `valor-calendar "ai-repo"` → Extends event to 5:00-6:00 (now covers two 30-min segments)
4. **Different task**: `valor-calendar "yudame-auth"` → Creates new event on yudame calendar

### Technical Approach

**CLI Tool (`tools/valor_calendar.py`):**
- Single command interface: `valor-calendar <session-slug>`
- Session slug: 1-4 words, stable identifier for the session (e.g., "ai-repo", "yudame-auth-bug")
- Maps session slug to calendar ID from config (e.g., "ai-repo" → calendar ID)
- If slug not in config: falls back to primary calendar
- Checks for existing event today with matching summary
- If exists: PATCH event to extend to cover current 30-min segment
- If not exists: POST new event rounded to current 30-min segment
- Time rounding: 5:08-5:18 → event from 5:00-5:30
- Uses `google-auth` and `google-api-python-client`
- Single OAuth token works for all calendars (same Google Workspace)

**Offline Queue (`~/.config/valor/calendar_queue.jsonl`):**
- JSONL file with entries: `{"timestamp": "...", "task": "...", "action": "start"}`
- On auth failure: append to queue, continue gracefully
- Background processor checks queue periodically
- On successful auth: replay queued entries, clear file

**Calendar Mapping (`~/.config/valor/calendar_config.json`):**
```json
{
  "calendars": {
    "ai-repo": "calendar-id-1@group.calendar.google.com",
    "yudame": "calendar-id-2@group.calendar.google.com",
    "default": "primary"
  }
}
```

**Setup Process:**
- Project calendars pre-created in Google Workspace by Tom
- All calendars belong to same Workspace → single OAuth token
- Machine setup workflow updated to prompt for calendar ID per project
- OAuth credentials reused from existing Google Workspace integration

**Session Hooks (Heartbeat):**
- Bridge calls `valor-calendar <session-slug>` at session start
- Same command called periodically during long sessions (heartbeat pattern)
- Each call acts as heartbeat: "I'm still working on this"
- Session slug must be stable and consistent across all calls for same session
- Slug format: 1-4 words, e.g., "ai-repo", "yudame-auth", "telegram-bridge"
- Derived from: repo name or chat group name (simplified, no branch names)
- No separate "start" vs "continue" actions - single command does both

## Rabbit Holes & Risks

### Risk 1: OAuth Credentials Not Provided
**Impact:** Can't authenticate, tool doesn't work
**Mitigation:** Document prerequisites clearly. Offline queue ensures work isn't lost if auth breaks temporarily.

### Risk 2: Event Overlap Detection
**Impact:** Creating multiple events for same task if detection logic is wrong
**Mitigation:** Match on event summary (exact string match) and date. Search today's events only. Simple and fast.

### Risk 6: 30-Minute Segment Logic
**Impact:** Complex edge cases in rounding and extending events
**Mitigation:**
- Round start time DOWN to nearest 30-min boundary (5:08 → 5:00)
- Round end time UP to nearest 30-min boundary (5:18 → 5:30)
- When extending: ensure end time covers current 30-min segment
- Example: Event 5:00-5:30, called at 5:42 → extend to 6:00

### Risk 3: Calendar ID Mapping Complexity
**Impact:** Hard-coding calendar IDs is fragile, breaks when calendars change
**Mitigation:** Store mapping in config file. Provide setup script to list available calendars and create config.

### Risk 4: Queue Replay Edge Cases
**Impact:** Replaying stale queue entries creates wrong events
**Mitigation:** Timestamp queue entries. Skip entries older than 24 hours during replay. Log skipped entries for audit.

### Risk 5: Session Hook Integration
**Impact:** Bridge might not have task context at session start/end
**Mitigation:** Make task name optional parameter. If not provided, use generic "work session" or skip. Don't block bridge on missing context.

## No-Gos (Out of Scope)

- **Not building full calendar management** - Just time tracking, no event browsing/editing/deleting
- **Not implementing smart categorization** - User provides task name, tool logs it as-is
- **Not building analytics/reports** - Just creates events, no dashboards or summaries
- **Not handling calendar permissions** - Assumes calendars already exist and are writable
- **Not supporting non-Google calendars** - Google Calendar only
- **Not implementing retroactive time tracking** - Only tracks current work, no backdating

## Implementation Notes

**Update Command Enhancement:**
- Add calendar connection test to `/update` command
- Test sequence:
  1. Check if OAuth token exists
  2. Attempt to list calendars (validates token and permissions)
  3. Check if `~/.config/valor/calendar_config.json` exists
  4. Verify each project from `ACTIVE_PROJECTS` has custom calendar mapping (not "primary")
  5. Verify mapped calendars are accessible
  6. Report status:
     - ✓ Connected: All projects have custom calendars and are accessible
     - ✗ Auth failed: OAuth token invalid or missing
     - ⚠ Missing calendars: Some projects lack custom calendar config
     - ⚠ Inaccessible: Calendars configured but not accessible

**System Setup Integration:**
- Update `.claude/commands/setup.md` to include calendar config step
- Add after Step 2 (Environment File): "Calendar Configuration"
- Prompt for calendar IDs per active project
- Store in `~/.config/valor/calendar_config.json`
- Format: `{"calendars": {"project-slug": "calendar-id@group.calendar.google.com"}}`

---

## Success Criteria

- [ ] `valor-calendar "ai-repo"` at 5:08 creates event from 5:00-5:30
- [ ] Calling `valor-calendar "ai-repo"` at 5:18 keeps event at 5:00-5:30 (already covered)
- [ ] Calling `valor-calendar "ai-repo"` at 5:42 extends event to 5:00-6:00
- [ ] Different session slugs create separate events
- [ ] Events appear on correct project-specific calendar based on slug mapping
- [ ] Single OAuth token works for all calendars (same Workspace)
- [ ] OAuth failures queue events locally and sync when auth restored
- [ ] Queue replay skips stale entries (>24 hours old)
- [ ] Bridge automatically calls `valor-calendar` at session start with stable slug
- [ ] Bridge calls `valor-calendar` periodically as heartbeat (same command, no flags)
- [ ] Tool runs in <500ms for typical operations
- [ ] `/update` command tests calendar OAuth and connection
- [ ] `/update` validates each active project has custom calendar config (not "primary")
- [ ] `/update` reports detailed status: connected, auth failed, missing config, or inaccessible
- [ ] System setup command includes calendar configuration step
