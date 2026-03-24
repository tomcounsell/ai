# Web UI Infrastructure

Localhost web application providing observability dashboards for the Valor AI system.

## Overview

The web UI runs as a standalone FastAPI server on `localhost:8500`, serving HTML dashboards with HTMX for interactivity. It provides read-only visibility into system state without requiring SSH or manual inspection.

## Quick Start

```bash
# Start the UI server
python -m ui.app

# Or with a custom port
UI_PORT=8600 python -m ui.app
```

Then open `http://localhost:8500/` in a browser.

## Architecture

```
Browser -> FastAPI (localhost:8500)
             |-> Jinja2 Templates (server-side HTML)
             |-> HTMX (client-side interactivity, CDN)
             |-> Popoto Models (read-only Redis queries)
```

### Key Design Decisions

- **FastAPI + Jinja2 + HTMX**: Server-side rendered HTML with HTMX for drill-down and polling. No JavaScript framework, no npm, no build step.
- **Sync route handlers**: All route handlers use `def` (not `async def`) so FastAPI runs them in a threadpool, avoiding event loop blocking from Popoto's synchronous Redis calls.
- **Read-only**: The UI only reads data. No write operations, no action buttons, no mutations.
- **Localhost only**: Binds to `127.0.0.1`, never `0.0.0.0`. No authentication needed.
- **Dark theme**: Terminal-aesthetic CSS with information-dense layouts and monospace typography.

## Directory Structure

```
ui/
  __init__.py
  __main__.py          # python -m ui.app entrypoint
  app.py               # FastAPI app factory, Jinja2 config, filters
  routers/
    __init__.py
    reflections.py     # Reflections dashboard routes
    sdlc.py            # SDLC observer routes
  data/
    __init__.py
    reflections.py     # Data access for reflection state
    sdlc.py            # Data access + Pydantic serializers
  templates/
    base.html          # Shared layout: nav, HTMX, CSS
    index.html         # Dashboard listing
    error.html         # Error page
    reflections/       # Reflections dashboard templates
    sdlc/              # SDLC observer templates
  static/
    style.css          # Dark theme CSS
```

## Available Dashboards

| Dashboard | URL | Description |
|-----------|-----|-------------|
| Root | `/` | Dashboard listing with descriptions |
| [Reflections](reflections-dashboard.md) | `/reflections/` | Scheduled task execution and history |
| [SDLC Observer](sdlc-observer.md) | `/sdlc/` | Development pipeline tracking |

## Adding a New Dashboard

1. Create a data layer in `ui/data/your_dashboard.py`
2. Create a router in `ui/routers/your_dashboard.py`
3. Create templates in `ui/templates/your_dashboard/`
4. Mount the router in `ui/app.py`:
   ```python
   from ui.routers.your_dashboard import router
   app.include_router(router, prefix="/your-dashboard")
   ```
5. Add a card to `ui/templates/index.html`

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `UI_PORT` | `8500` | Port to bind the server on |

## Dependencies

- `fastapi` - Web framework
- `uvicorn[standard]` - ASGI server
- `jinja2` - Template engine
- HTMX loaded from CDN (no local install)

## Related

- [Reflections Dashboard](reflections-dashboard.md) - Reflection execution monitoring
- [SDLC Observer](sdlc-observer.md) - Pipeline stage tracking
