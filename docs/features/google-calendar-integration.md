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

1. Maps the slug to a Google Calendar ID via `~/Desktop/Valor/calendar_config.json`
2. Searches today's events for one matching the slug summary
3. If no match: creates a new event rounded to the current 30-minute segment
4. If match exists and already covers current segment: no-op
5. If match exists but doesn't cover current segment: extends the event

### 30-Minute Rounding

- Start time rounds DOWN: 5:08 → 5:00
- End time rounds UP to segment boundary: 5:08 → 5:30
- Extending: event 5:00-5:30 called at 5:42 → extends to 6:00

### Offline Queue

On auth/network failure, entries are queued to `~/Desktop/Valor/calendar_queue.jsonl`. On next successful call, queued entries are replayed (entries >24h old are skipped).

## Automatic Heartbeats

Time tracking runs automatically in two contexts:

### Claude Code Hook (direct machine work)

A Claude Code hook (`scripts/calendar_hook.sh`) fires on `SessionStart` and `Stop` events. It derives the slug from the working directory name (e.g., `ai`, `psyoptimal`). Rate-limited to one call per 25 minutes via a timestamp file to avoid excessive API calls.

Configured in `.claude/settings.json` — committed to the repo, so it works on all machines.

### Bridge Integration (Telegram sessions)

The bridge session queue (`agent/agent_session_queue.py`) automatically calls `valor-calendar` with the project key:
- Once at session start (session begins)
- Every 25 minutes during long-running jobs (heartbeat)

Calls are fire-and-forget subprocesses — they never block agent work.

## Backdating Events from Commit History

When creating events after-the-fact (e.g. a project calendar was just configured), derive them from `git log` — never create generic project-name events.

### Rules

1. **Slugs must match the work done** — use a kebab-case description of the actual task (e.g. `cms-and-hermes-adoption`, `teams-integration`, `mvp-demo-readiness`), never just the project name (`cyndra`, `valor`).
2. **20-minute minimum, 10-minute increments** — all durations are `20 + N×10` minutes. No 30- or 60-minute fixed blocks.
3. **Cluster commits into sessions** — group commits with < 45-minute gaps as one session. Separate clusters become separate events.
4. **Pad the window** — start ~10–15 min before the first commit; end ~10–15 min after the last. Round both to the nearest 10-minute boundary.
5. **Skip automated commits** — dep bumps, merge commits, and bot-authored commits don't represent manual work time; omit or give them a bare 20-minute block only if surrounded by real work.

### Example slug derivation

| Commit cluster | Good slug | Bad slug |
|---|---|---|
| `fix(cms): pin next`, `fix(cms): turbopack`, `feat(#23): adopt Hermes v0.14.0` | `cms-and-hermes-adoption` | `cyndra` |
| `feat(#41): scaffold Teams integration` + 7 CMS type fixes | `teams-integration` | `cyndra-dev` |
| `Plan revision`, `plan(#23): resolve open questions`, `feat(core): simplify knowledge.yaml` | `mvp-demo-readiness` | `valor` |

### Script pattern

```python
from tools.google_workspace.auth import get_service

service = get_service("calendar", "v3")
events = [
    # (calendar_id, slug, start_iso, end_iso)  — all in local tz, 10-min-aligned
    (CYNDRA_CAL, "cms-and-hermes-adoption", "2026-05-20T17:10:00+07:00", "2026-05-20T18:50:00+07:00"),
    ...
]
for cal_id, slug, start, end in events:
    service.events().insert(calendarId=cal_id, body={
        "summary": slug,
        "start": {"dateTime": start, "timeZone": "Asia/Bangkok"},
        "end":   {"dateTime": end,   "timeZone": "Asia/Bangkok"},
    }).execute()
```

## Files

| File | Purpose |
|------|---------|
| `tools/valor_calendar.py` | CLI tool, event logic, offline queue |
| `tools/google_workspace/__init__.py` | Package init |
| `tools/google_workspace/auth.py` | OAuth module (reusable for future Workspace tools) |
| `agent/agent_session_queue.py` | Bridge heartbeat integration |
| `scripts/calendar_hook.sh` | Claude Code hook script (rate-limited) |
| `.claude/settings.json` | Hook configuration (SessionStart + Stop) |

## Configuration

### Calendar Config (`~/Desktop/Valor/calendar_config.json`)

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

- Credentials: `~/Desktop/Valor/google_credentials.json` (from Google Cloud Console)
- Token: `~/Desktop/Valor/google_token.<machine-name>.json` (per-machine, auto-generated on first run)
- Scopes: `https://www.googleapis.com/auth/calendar`

See `docs/features/google-workspace-auth.md` for error handling, `verify_token()`, and `--reauth`/`--check` CLI flags.

## Setup & Validation

- `/setup` command (Step 4) walks through OAuth consent using `valor-calendar --reauth` and validates with `valor-calendar --check`
- `/update` command validates OAuth connectivity, config file, and per-project calendar accessibility
