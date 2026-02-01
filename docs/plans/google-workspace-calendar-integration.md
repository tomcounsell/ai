---
appetite: Small (1-2 days)
owner: Valor Engels
created: 2025-01-31
updated: 2025-01-31
---

# Automatic Work Time Tracking via Calendar

## Problem

Tom has no visibility into how much time Valor spends on different projects and tasks. When Valor starts working on something, that time should automatically be logged to a Google Calendar so Tom can see:
- How much time each project/repo is consuming
- When Valor is actively working
- Time distribution across different chat groups/tasks

**Real scenario:**
> Valor starts a session for "ai" repo via Telegram
> → CLI tool auto-creates/extends calendar event: "ai: Session work"
> Tom checks calendar later: sees "ai: Session work" from 2:00-3:30 PM
> Next session starts: CLI extends the event or creates new one if gap is large

Currently:
1. Work sessions happen but aren't tracked anywhere visible
2. Tom has to manually ask Valor for time reports
3. No historical view of what Valor worked on when
4. Can't easily see time distribution across projects

## Appetite

**Small** (1-2 days)

Focus on automatic time tracking, not interactive calendar queries. Simple CLI interface.

## Solution (Breadboarded)

### Core Interface

**Single CLI command:**
```bash
valor-calendar working-on "project-slug"
```

This command:
- Uses session slug as task name (1-4 words, consistent per session)
- **30-minute block alignment**: All events snap to 30-min segments
  - Work from 5:08-5:18 → Event 5:00-5:30
  - Work from 2:45-3:10 → Event 2:30-3:30 (spans two blocks)
- Checks if there's an event for this project covering current 30-min block TODAY
- If exists: extends to cover current block (e.g., 2:00-2:30 → 2:00-3:00 if now is 2:45)
- If doesn't exist: creates new event starting at current 30-min block
- Logs action locally if OAuth is temporarily broken (queued for retry)

### Places & Affordances

**Place 1: Session Heartbeat**
- Bridge calls `valor-calendar working-on "{project_key}"` as heartbeat
- Same call for session start and keepalive (no distinction - just register activity)
- Called every 30 minutes while session is active
- No separate session end hook (event naturally ends when heartbeats stop)

**Place 2: CLI Tool**
- Reads OAuth token from `~/.config/valor/google_credentials.json`
- Single OAuth token works for all project calendars (same Google Workspace)
- Calls Google Calendar API to create/update events
- Each project should have its own dedicated calendar (created by Tom in Google Workspace)
- Calendar IDs stored in project config (e.g., `ACTIVE_PROJECTS["ai"]["calendar_id"]`)
- Falls back to primary calendar if `calendar_id` not set (for convenience, but not recommended)
- If OAuth fails: appends to `~/.valor/calendar_queue.log` with timestamp

**Place 3: Queue Replay**
- On next successful auth: reads queue log and backfills events
- Clears queue after successful backfill

### Key Flows

1. **Heartbeat Call** (start + keepalive are identical):
   ```
   Bridge heartbeat for "ai" at 5:08 PM
   → valor-calendar working-on "ai"
   → Calculate current 30-min block: 5:00 PM
   → Check: Any "ai" event today covering 5:00-5:30?
   → No: Create event (5:00 PM → 5:30 PM)
   → Yes: Extend end time to cover current block
   ```

2. **Next Heartbeat** (30 min later):
   ```
   Bridge heartbeat for "ai" at 5:35 PM (now in 5:30-6:00 block)
   → valor-calendar working-on "ai"
   → Check: Does event cover 5:30-6:00?
   → No: Extend event from 5:00-5:30 → 5:00-6:00
   → Yes: No action needed (already covers block)
   ```

3. **OAuth Broken**:
   ```
   valor-calendar working-on "ai"
   → Google API returns 401
   → Append to queue: 2025-01-31T14:30:00,ai,working-on
   → Continue silently (don't block bridge)
   → Next successful call: replay queue
   ```

4. **Manual Override**:
   ```
   Tom can manually edit calendar events in Google Calendar UI
   CLI tool doesn't sync back - calendar is source of truth for display
   ```

5. **Setup & Test**:
   ```
   New machine setup:
   → System-setup skill prompts for calendar IDs per project
   → Stores in config

   /update command test:
   → valor-calendar test
   → Verifies OAuth token valid
   → Tests connection to each project calendar
   → Reports status (✓ Connected or ✗ Failed with error)
   → ⚠️  Warns if any project missing calendar_id (using primary calendar fallback)
   ```

### Fat Marker Sketch

```
[Session Start] → valor-calendar working-on "ai"
                        ↓
                  [Check OAuth token]
                        ↓
                   [Valid?] ───No──→ [Log to queue] → [Continue]
                        ↓ Yes
                  [Query today's events for "ai"]
                        ↓
          [Found recent event?] ───Yes──→ [Extend end time]
                        ↓ No
                  [Create new event]
                        ↓
                  [Return success]
```

**What we're NOT specifying:**
- Exact event naming format (can iterate)
- Whether to use separate calendars or one calendar with colors
- Exact "recent" threshold (2 hours? 4 hours?)
- Whether to track sub-tasks or just project-level

## Rabbit Holes (Risks & Unknowns)

- [ ] **OAuth token refresh**: Google tokens expire after 1 hour (or 7 days for offline). Does `google-auth` handle refresh automatically, or do we need to implement? Decision: Use `google-auth-oauthlib` with `credentials.refresh()` - handles it for us.

- [ ] **Calendar management**: Tom creates separate calendar per project in Google Workspace (e.g., "Valor: ai", "Valor: yudame-web"). Calendar IDs stored in project config. Tool falls back to primary calendar if `calendar_id` not configured, but `/update` should detect and warn about missing per-project calendar config.

- [ ] **Event merging logic**: All events snap to 30-min blocks. Work from 2:00-3:00 creates event 2:00-3:00. Work from 3:15-4:00 extends to 3:00-4:00 (merges with previous). Gap detection not needed - block alignment handles merging naturally.

- [ ] **Queue replay conflicts**: If OAuth is broken for 6 hours, queue has 12 entries for "ai". Do we create 12 separate events, or merge into one 6-hour block? Decision: Merge consecutive entries for same project into single block.

- [ ] **Timezone handling**: Calendar API uses UTC, need to convert to Tom's timezone (EST/EDT). Risk: DST transitions. Mitigation: Use `pytz` for proper timezone conversion.

- [ ] **Rate limiting**: Google Calendar API has rate limits (requests per second). If bridge restarts 10 times in a minute, could we hit limits? Decision: Add 1-second delay between API calls, track last call time.

- [ ] **Session end detection**: No explicit session end - events naturally end when heartbeats stop. If no heartbeat for 1 hour, event stays at last extended time. Simple and reliable.

- [ ] **Calendar connection testing**: Add `valor-calendar test` command for `/update` to verify OAuth and calendar access. Should test each project calendar and report status. **Critical**: Must detect and warn if any project is using primary calendar instead of dedicated per-project calendar. Risk: If config has stale calendar IDs, tests will fail. Mitigation: Clear error messages with calendar ID shown.

## No-Gos (Out of Scope)

- **Not building interactive calendar queries** - No "what's on my calendar?" or "schedule a meeting" commands
- **Not syncing other people's calendars** - Only Valor's work time, not Tom's meetings
- **Not doing time analytics** - Just raw calendar events, no reports or dashboards (Google Calendar has built-in views)
- **Not tracking sub-tasks** - Session slug only (e.g., "ai" not "ai: telegram_bridge.py")
- **Not creating calendars programmatically** - Tom creates project calendars manually in Google Workspace
- **Not building a UI** - CLI only, view in Google Calendar web/app
- **Not handling multiple users** - Valor only, not a multi-agent system
- **Not doing smart categorization** - Manual project names, not auto-detecting from git repos
- **Ignoring cost tracking** - Time only, not API costs or compute hours
- **Not creating separate "start" vs "keepalive" commands** - Same heartbeat call for both (simpler)

## Open Questions

### Assumptions Made (Now Resolved)

1. ✅ **Separate calendars per project** - Tom creates calendars in Google Workspace, stores calendar IDs in config
2. ✅ **30-minute block alignment** - Events snap to :00 and :30 (5:08 work → 5:00-5:30 event)
3. ✅ **Heartbeat model** - Session start = keepalive (same call, just register activity every 30 min)
4. ✅ **Simple slug naming** - Session slug (1-4 words), consistent across calls for same session
5. ✅ **Single OAuth token** - Works for all project calendars (same Google Workspace)
6. ✅ **Setup via system-setup skill** - Calendar IDs configured during new machine setup workflow
7. ✅ **Test via /update** - Add `valor-calendar test` to verify OAuth and calendar connections

### Remaining Assumptions

1. **Assumed project-level tracking** - Event title is just slug (e.g., "ai"), not task details. Is this correct?

2. **Assumed no retroactive logging** - Only tracks forward from implementation. Don't backfill historical sessions. Correct?

### Questions Needing Input

1. **Event naming format**: Should event titles be:
   - Just the slug: "ai"
   - Or with prefix: "Valor: ai"
   - Or descriptive: "ai session"

2. **Queue retry frequency**: If OAuth is broken, retry every CLI call or once per hour?

3. **Config location**: Where should calendar IDs be stored?
   - In `config/projects.json` as `{"ai": {"calendar_id": "xxx@group.calendar.google.com"}}`
   - Or in bridge config as `ACTIVE_PROJECTS["ai"]["calendar_id"]`
   - Or separate file `config/calendar_ids.json`

---

## Success Criteria

- [ ] When Valor starts a session via Telegram, a calendar event is created/extended automatically
- [ ] Events show session slug (e.g., "ai", "yudame-web") and duration
- [ ] Events snap to 30-minute blocks (5:08 work → 5:00-5:30 event)
- [ ] If OAuth fails, events are queued and backfilled later (no data loss)
- [ ] Tom can view Valor's work time in per-project Google Calendars
- [ ] Consecutive sessions automatically merge (5:00-5:30 extends to 5:00-6:00 on heartbeat)
- [ ] CLI tool runs in < 1 second (non-blocking for bridge)
- [ ] Single OAuth token works for all project calendars
- [ ] `/update` command includes `valor-calendar test` and reports OAuth + calendar connection status
- [ ] `/update` warns if any project is missing dedicated calendar (using primary fallback)
- [ ] System-setup skill prompts for calendar IDs during new machine setup

---

## Status

**Planning** - Draft updated based on CLI tool approach, awaiting refinement

---

## Implementation Notes

### Phase 1: Basic CLI tool (Day 1 morning)
- Create `tools/valor_calendar.py` with CLI interface using `argparse`
- Implement 30-minute block alignment logic:
  - Round current time down to nearest :00 or :30
  - Calculate block start and next block end
- Implement `working-on` command: heartbeat handler (no distinction between start/keepalive)
  - Query calendar for events TODAY matching slug
  - If event exists and overlaps current block: extend to cover block
  - If no event: create new event starting at block start
- Implement `test` command: verify OAuth and calendar connections
  - Load OAuth token from `~/.config/valor/google_credentials.json`
  - Load calendar IDs from config
  - Test connection to each project calendar
  - Report status (✓ Connected or ✗ Failed with error message)
  - **Warn if any project missing `calendar_id`** (⚠️ Using primary calendar - recommend setting dedicated calendar)
- Use `google-auth-oauthlib` and `google-api-python-client`
- Queue file at `~/.valor/calendar_queue.log`
- Test manually: `valor-calendar working-on "test"` and `valor-calendar test`

### Phase 2: Bridge integration (Day 1 afternoon)
- Add heartbeat hook to `telegram_bridge.py`: call `valor-calendar working-on "{slug}"` every 30 min
- No distinction between session start and keepalive (same call)
- Test with real Telegram session
- Verify events show up in Google Calendar

### Phase 2.5: /update integration
- Modify `/update` command to call `valor-calendar test`
- Display connection status for OAuth and each project calendar
- **Display warning for any project using primary calendar** (missing `calendar_id` in config)
- Test scenarios:
  - Broken OAuth (delete token)
  - Broken calendar ID (invalid ID in config)
  - Missing calendar ID (should warn but not fail)

### Phase 2.6: Setup command integration
- Update `.claude/commands/setup.md` to include calendar ID configuration in Step 3
- After editing `config/projects.json`, prompt user: "Do you want to track work time in Google Calendar? (y/n)"
- If yes: For each project, prompt for calendar ID (explain how to get from Google Calendar settings)
- Store calendar IDs in config (location TBD based on answer to Question 3)
- Run `valor-calendar test` to verify connection

### Phase 3: Queue and error handling (Day 2 morning)
- Implement queue logging when OAuth fails
- Implement queue replay on successful auth
- Test OAuth failure scenario (delete token, verify queue works)
- Test queue replay (restore token, verify backfill)

### Phase 4: Block alignment edge cases (Day 2 afternoon)
- Test edge cases:
  - Session starts at :29 (should align to :00)
  - Session starts at :31 (should align to :30)
  - Keepalive at :59 (should extend to next block)
  - Work spans midnight (event should not cross day boundary)
- DST transition testing: verify timezone handling
- Test multiple projects: verify calendar_id routing works correctly

### Testing Plan
- Unit tests for event creation, extension, merging
- Integration test with Google Calendar API (staging calendar)
- Manual test with bridge: start session, verify event created
- Failure test: break OAuth, verify queueing works
- Replay test: restore OAuth, verify backfill works
