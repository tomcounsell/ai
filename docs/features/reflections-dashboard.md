# Reflections Dashboard

Web dashboard for monitoring the reflection scheduler's execution, history, and ignore patterns.

## Overview

The reflections dashboard at `/reflections/` provides visibility into all registered reflections declared in `config/reflections.yaml`, their execution state from Redis, and historical run data.

## Views

### Overview (`/reflections/`)
- Grid of all registered reflections with live status from Redis
- Each row shows: name, description, status badge, last run time, duration, run count, interval, next due
- Failed runs display error messages inline
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
- Full detail for a single run including error text and log content
- Log viewer with lazy-load via HTMX partial endpoint

### Ignore Patterns (`/reflections/ignores/`)
- Active ignore patterns with pattern string, reason, creation date, expiry, and time remaining
- Sorted by expiry (soonest first)

## Data Sources

- **Registry**: `config/reflections.yaml` - names, descriptions, intervals, execution types
- **State**: `Reflection` Popoto model in Redis - last_run, next_due, status, run_count, error
- **History**: `Reflection.run_history` ListField - capped at 200 entries per reflection
- **Ignores**: `ReflectionIgnore` Popoto model in Redis - active patterns with TTL

## Model Extension

The `Reflection` model was extended with a `run_history` ListField that stores serialized run dicts:

```python
{
    "timestamp": 1711000000.0,  # Unix timestamp
    "status": "success",         # success | error
    "duration": 1.5,             # seconds
    "error": None,               # error message or None
}
```

`mark_completed()` internally appends to `run_history` on each call. The list is capped at 200 entries (oldest trimmed). The method signature is unchanged -- existing callers require no modification.

## HTMX Endpoints

| Endpoint | Trigger | Purpose |
|----------|---------|---------|
| `/_partials/status-grid/` | `every 10s` | Refresh overview grid |
| `/_partials/log/{name}/{index}/` | Click | Load log content lazily |

## Related

- [Web UI Infrastructure](web-ui.md) - Shared infrastructure
- [Reflections](reflections.md) - The reflection scheduler itself
