# Work Time Tracking via Google Calendar

Tracking: https://github.com/tomcounsell/ai/issues/20

## Overview

The `valor-calendar` CLI tool logs work sessions as Google Calendar events with 30-minute segment rounding, per-project calendars, and an offline queue for auth failures.

## Usage

```bash
valor-calendar <session-slug>
```

Examples:
- `valor-calendar "ai-repo"` — logs time on the default (Internal Projects) calendar
- `valor-calendar "psyoptimal"` — logs time on the PsyOPTIMAL calendar
- `valor-calendar "soul-world-bank"` — logs time on the Soul World Bank calendar

## How It Works

1. Maps the slug to a Google Calendar ID via `~/Desktop/claude_code/calendar_config.json`
2. Searches today's events for one matching the slug summary
3. If no match: creates a new event rounded to the current 30-minute segment
4. If match exists and already covers current segment: no-op
5. If match exists but doesn't cover current segment: extends the event

### 30-Minute Rounding

- Start time rounds DOWN: 5:08 → 5:00
- End time rounds UP to segment boundary: 5:08 → 5:30
- Extending: event 5:00-5:30 called at 5:42 → extends to 6:00

### Offline Queue

On auth/network failure, entries are queued to `~/Desktop/claude_code/calendar_queue.jsonl`. On next successful call, queued entries are replayed (entries >24h old are skipped).

## Automatic Heartbeats

Time tracking runs automatically in two contexts:

### Claude Code Hook (direct machine work)

A Claude Code hook (`scripts/calendar_hook.sh`) fires on `SessionStart` and `Stop` events. It derives the slug from the working directory name (e.g., `ai`, `psyoptimal`). Rate-limited to one call per 25 minutes via a timestamp file to avoid excessive API calls.

Configured in `.claude/settings.json` — committed to the repo, so it works on all machines.

### Bridge Integration (Telegram sessions)

The bridge job queue (`agent/job_queue.py`) automatically calls `valor-calendar` with the project key:
- Once at job start (session begins)
- Every 25 minutes during long-running jobs (heartbeat)

Calls are fire-and-forget subprocesses — they never block agent work.

## Files

| File | Purpose |
|------|---------|
| `tools/valor_calendar.py` | CLI tool, event logic, offline queue |
| `tools/google_workspace/__init__.py` | Package init |
| `tools/google_workspace/auth.py` | OAuth module (reusable for future Workspace tools) |
| `agent/job_queue.py` | Bridge heartbeat integration |
| `scripts/calendar_hook.sh` | Claude Code hook script (rate-limited) |
| `.claude/settings.json` | Hook configuration (SessionStart + Stop) |

## Configuration

### Calendar Config (`~/Desktop/claude_code/calendar_config.json`)

```json
{
  "calendars": {
    "default": "calendar-id@group.calendar.google.com",
    "psyoptimal": "calendar-id@group.calendar.google.com",
    "soul-world-bank": "calendar-id@group.calendar.google.com"
  }
}
```

Slugs not in the config fall back to the `default` entry (or `primary` if no default).

### OAuth Credentials

- Credentials: `~/Desktop/claude_code/google_credentials.json` (from Google Cloud Console)
- Token: `~/Desktop/claude_code/google_token.json` (auto-generated on first run)
- Scopes: `https://www.googleapis.com/auth/calendar`

## Setup & Validation

- `/setup` command (Step 3) walks through OAuth consent and calendar config creation
- `/update` command (Step 7) validates OAuth connectivity, config file, and per-project calendar accessibility
