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

Routes are declared **inline in `ui/app.py`** inside `create_app()`. There is no
`ui/routers/` package; the dashboard is small enough that a router layer added
indirection without adding structure.

1. Create a data layer in `ui/data/your_dashboard.py`. Synchronous `def`
   handlers, not `async def` — Popoto uses synchronous Redis calls and FastAPI
   runs sync handlers in a threadpool. Import models inside the function, not at
   module scope, so an import cycle in the model layer cannot take the UI down.
2. Create templates in `ui/templates/your_dashboard/`.
3. Add the route inside `create_app()` in `ui/app.py`, returning
   `templates.TemplateResponse`:
   ```python
   @app.get("/_partials/your_dashboard/", response_class=HTMLResponse)
   def partial_your_dashboard(request: Request):
       from ui.data.your_dashboard import get_summary

       return templates.TemplateResponse(
           request, "your_dashboard/summary.html", {"summary": get_summary()}
       )
   ```
4. Add a card to `ui/templates/index.html` that pulls the partial with HTMX:
   ```html
   <div hx-get="/_partials/your_dashboard/" hx-trigger="revealed, every 60s"
        hx-swap="innerHTML"></div>
   ```

The `/_partials/` prefix is the convention for HTMX fragments: they return HTML
fragments rather than whole pages, and are never linked directly.

## Lifecycle

The web UI runs as a standalone process, independent of the bridge. The `/update` skill auto-restarts it if it was running when an update is deployed (`scripts/update/service.py` — `is_webui_running()`, `restart_webui()`). If the UI is not running at update time, it is left stopped.

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
