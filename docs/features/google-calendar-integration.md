# Work Time Tracking via Google Calendar

Tracking: https://github.com/tomcounsell/ai/issues/20

## Overview

The `valor-calendar` CLI tool logs work sessions as Google Calendar events. Events are **feature-keyed** (all work on one feature coalesces into a single event), **client-facing** (titled with a jargon-stripped feature name, not a technical slug), and **day-bounded** (an event can never span multiple days). Per-project calendars route via `calendar_config.json`, and an offline queue absorbs auth/network failures.

## Usage

```bash
# Manual: the positional argument is the event title verbatim
valor-calendar --project psyoptimal "member-export"

# Hook mode: reads Claude Code hook JSON from stdin (see Automatic Heartbeats)
echo '{"prompt":"...","cwd":"..."}' | valor-calendar --hook --event prompt
echo '{...}' | valor-calendar --hook --event stop
```

## How It Works

Two identities drive each event:

- **Feature key** — a *stable* coalescing key. Everything sharing it (consecutive prompts, parallel subagents, every SDLC stage) rolls into **one** event. Derived, in priority order, from: the git branch (minus scaffolding prefixes like `session/`), a slug-scoped task-list id, or — for ad-hoc trunk work — the project key for the day.
- **Display name** — a *client-facing* feature name shown as the event title. Generated once per feature/day and cached so the title stays stable as the event grows. Technical jargon (`sdlc`, `prompt`, `parallel`, `test`, `issue`, …) is stripped so a non-technical client sees the value, not the plumbing.

Given those, `process_calendar_event`:

1. Maps the project to a Google Calendar ID via `~/Desktop/Valor/calendar_config.json` (allowlist — unmapped projects are skipped, no default fallback).
2. Finds today's event for the **feature key** (cached event ID first, then a title search) — **rejecting any event that did not start today**.
3. If none: creates a new event in the current 20-minute segment, titled with the display name.
4. If one exists and already covers the current segment: no-op.
5. If one exists but is behind: extends it — but never past the end of the day.

### Segment Rounding & Day Bounding

- Start rounds DOWN to the 10-minute boundary: 5:08 → 5:00.
- End is `start + 20 min`, **clamped to 23:59:59** of the same day.
- Extending: event 5:00–5:20 touched at 5:42 → extends to 5:50.
- **No multi-day events**: a block from a prior day is never matched or extended (`_starts_today`), and extension is capped at the day boundary. This is what eliminates the runaway multi-day project-name blocks the old summary-search produced.

### No Noise Events

- **Trivial prompts are gated** before any work: acknowledgements (`thanks`, `continue`, `ok`), bare confirmations, and sub-12-char prompts never create or name an event (`is_trivial_prompt`).
- **No project-name stubs**: an event is only ever titled with a real feature name. Ad-hoc trunk work coalesces under the project-for-the-day key but is *titled* from the seeding prompt (via Haiku), never the bare project key.

### Client-Facing Naming

- **Tracked work** (branch / task-list feature key): the title is a deterministic, jargon-stripped cleanup of the key — no network call.
- **Ad-hoc work**: a Haiku call (`claude-haiku-4-5`, 6s timeout, best-effort) rewrites the seeding prompt into a 2–4 word client-facing feature name, with a jargon denylist applied as a guardrail. Any failure falls back to the deterministic cleaner. The result is cached per feature/day so it costs at most one call.

### Rate Limiting

The `--hook` path skips re-touching a feature's event more than once per 10 minutes (`STAMP_CACHE_PATH`), bounding API churn. The first fire for a feature each day is never limited.

### Offline Queue

On auth/network failure, entries are queued to `data/calendar_queue.jsonl` (feature key + display name + project). On the next successful call, queued entries are replayed (entries >24h old are skipped). Legacy `slug`-only entries still replay.

## Automatic Heartbeats

Time tracking runs automatically in two contexts:

### Claude Code Hook (direct machine work)

Two **thin shell wrappers** forward the Claude Code hook JSON (stdin) to `valor-calendar --hook`; all logic lives in Python:
- `scripts/calendar_prompt_hook.sh` on `UserPromptSubmit` → `--event prompt`
- `scripts/calendar_hook.sh` on `Stop` → `--event stop`

The wrappers run the call **detached** (backgrounded, streams redirected) so calendar logging never delays the user's prompt. Project resolution, feature-key coalescing, client-facing naming, trivial-prompt gating, rate-limiting, and day-bounded event logic all live in `tools/valor_calendar.py`'s `run_hook`.

**Registered globally**, not per-repo: the hooks live in `~/.claude/settings.json` with absolute paths to this repo's scripts, so they fire in *every* project directory — which is how the mapped project repos (cyndra, psyoptimal, …) get tracked even though they have no local `.claude/settings.json`. The ai repo (`valor`) is not calendar-mapped, so it produces no events. (The old duplicate registration in this repo's local `.claude/settings.json` was removed — it only ever fired in the unmapped `valor` repo.)

**Scope is the calendar-mapped-project allowlist**: every local session in a project present in `calendar_config.json` is tracked; unmapped projects are skipped by `get_calendar_id` — there is no `default` fallback.

### Hang Protection (all Google callers)

Every Google HTTP round-trip is bounded by `GWS_HTTP_TIMEOUT` (default 30s; the hook tightens it to 8s). `tools/google_workspace/auth.py` builds the API client on an `httplib2.Http(timeout=…)` transport (`AuthorizedHttp`) and refreshes tokens through a timeout-enforcing `requests` adapter. Without this, a stalled Google connection could hang a hook indefinitely (the httplib2 socket default is infinite; the requests refresh default is 120s) — well past the 15s hook budget.

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
| `tools/valor_calendar.py` | CLI tool, `--hook` mode, feature-key/naming/rate-limit logic, event model, offline queue |
| `tools/google_workspace/__init__.py` | Package init |
| `tools/google_workspace/auth.py` | OAuth module with bounded HTTP transport (`GWS_HTTP_TIMEOUT`) |
| `agent/agent_session_queue.py` | Bridge heartbeat integration |
| `scripts/calendar_prompt_hook.sh` | Thin `UserPromptSubmit` wrapper → `valor-calendar --hook --event prompt` (detached) |
| `scripts/calendar_hook.sh` | Thin `Stop` wrapper → `valor-calendar --hook --event stop` (detached) |
| `~/.claude/settings.json` | Global hook registration (fires in every repo; absolute paths to this repo's scripts) |

Cache/queue files under `data/`: `calendar_event_ids.json` (feature-key:date → event id), `calendar_feature_names.json` (feature-key:date → display name), `calendar_fire_stamps.json` (rate-limit stamps), `calendar_queue.jsonl` (offline queue).

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

Projects not in the config are **skipped** (no event created) — there is no automatic `default` fallback for the hook path. The `default` entry is only used by explicit manual/backdating calls that pass no `--project`.

### OAuth Credentials

- Credentials: `~/Desktop/Valor/google_credentials.json` (from Google Cloud Console)
- Token: `~/Desktop/Valor/google_token.<machine-name>.json` (per-machine, auto-generated on first run)
- Scopes: `https://www.googleapis.com/auth/calendar`

See `docs/features/google-workspace-auth.md` for error handling, `verify_token()`, and `--reauth`/`--check` CLI flags.

## Setup & Validation

- `/setup` command (Step 4) walks through OAuth consent using `valor-calendar --reauth` and validates with `valor-calendar --check`
- `/update` command validates OAuth connectivity, config file, and per-project calendar accessibility
