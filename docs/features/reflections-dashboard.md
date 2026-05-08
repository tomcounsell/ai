# Reflections Dashboard

Web dashboard for monitoring the reflection scheduler's execution, history, and ignore patterns.

## Overview

The reflections dashboard at `/reflections/` provides visibility into all registered reflections declared in `config/reflections.yaml`, their execution state from Redis, and historical run data.

## Views

### Overview (`/reflections/`)
- Grid of all registered reflections with live status from Redis
- Each row shows: status dot (green/red/off), name + description, timing column with priority icon, last run timestamp, arrow to next due timestamp, and duration between them
- Failed runs display error messages inline
- Rows with `failure_count_consecutive > 0` surface the consecutive failure count
- Rows with `paused_until > now` are visually marked as paused
- `output_sink` is exposed per row for at-a-glance delivery configuration
- `cost_usd_total` is available for cost tracking on agent-type reflections
- Auto-refreshes via HTMX every 10 seconds
- Links to run history for reflections with historical data

### Schedule (`/reflections/schedule/`)
- All reflections sorted by next-due timestamp (soonest first)
- Shows overdue reflections highlighted in red
- Displays relative time until next execution

### Run History (`/reflections/{name}/history/`)
- Paginated list of historical runs for a specific reflection (newest first)
- Each run shows: index, status, timestamp, duration, error preview
- Links to detail view for each run

### Run Detail (`/reflections/{name}/history/{index}/`)
- Full detail for a single run including error text, output summary, cost, token counts, and per-project breakdown
- `delivery_error` is surfaced when an output sink delivery (e.g., Telegram send) failed
- Log viewer with lazy-load via HTMX partial endpoint

### Ignore Patterns (`/reflections/ignores/`)
- Active ignore patterns with pattern string, reason, creation date, expiry, and time remaining
- Sorted by expiry (soonest first)

## Data Sources

- **Registry**: `config/reflections.yaml` — names, descriptions, schedules, execution types
- **State**: `Reflection` Popoto model in Redis — `ran_at`, `last_status`, `run_count`, `last_error`, `failure_count_consecutive`, `paused_until`, `cost_usd_total`, `output_sink`
- **History**: `ReflectionRun` Popoto model in Redis (30-day TTL) — one row per completed run, queried via `ReflectionRun.query.filter(name=<name>)`
- **Ignores**: `ReflectionIgnore` Popoto model in Redis — active patterns with TTL

`next_due` is not stored; the data layer computes it via `compute_next_due(schedule, ran_at)` (or the legacy `ran_at + interval` path for registry entries that still carry an `interval` field).

## Dashboard Row Fields

Each dashboard entry (`_build_entry` in `ui/data/reflections.py`) exposes:

| Field | Source | Notes |
|-------|--------|-------|
| `last_run` | `Reflection.ran_at` | Unix timestamp |
| `next_due` | Computed | Not stored |
| `run_count` | `Reflection.run_count` | |
| `last_status` | `Reflection.last_status` | `pending`, `running`, `success`, `error`, `skipped`, `stale_running` |
| `last_error` | `Reflection.last_error` | |
| `last_duration` | `Reflection.last_duration` | Seconds |
| `failure_count_consecutive` | `Reflection.failure_count_consecutive` | |
| `paused_until` | `Reflection.paused_until` | Unix timestamp; 0.0 means not paused |
| `cost_usd_total` | `Reflection.cost_usd_total` | Rolling total |
| `output_sink` | `Reflection.output_sink` | `log_only`, `dashboard_only`, `memory:N`, `telegram:<chat>` |
| `has_history` | `ReflectionRun.query.filter(name=...)[:1]` | Boolean; True if any run rows exist |

## ReflectionRun Shape

Run history is queried from `ReflectionRun` rows, not from an embedded list on the `Reflection` record. The data layer maps `ReflectionRun` fields to the display shape:

```python
{
    "timestamp": float,       # Unix epoch of run completion
    "status": str,            # "success" | "error" | "stale_running"
    "duration": float,        # duration_ms / 1000.0
    "error": str | None,
    "output_summary": str | None,
    "delivery_error": str | None,   # Non-None when output sink delivery failed
    "cost_usd": float,
    "tokens_input": int,
    "tokens_output": int,
    "projects": list[dict],   # Per-project breakdown for audit reflections
    "index": int,
}
```

Per-project rows (when `projects` is non-empty) render an indented sub-table with status badge, `[slug]` tag, duration, and error cell.

## HTMX Endpoints

| Endpoint | Trigger | Purpose |
|----------|---------|---------|
| `/_partials/status-grid/` | `every 10s` | Refresh overview grid |
| `/_partials/log/{name}/{index}/` | Click | Load log content lazily |

## Related

- [Web UI Infrastructure](web-ui.md) — Shared infrastructure
- [Reflections](reflections.md) — The reflection scheduler, model fields, output sinks, failure tracking, and MCP surface
