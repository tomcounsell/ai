"""FastAPI application factory for the unified web UI.

Mounts sub-routers for each dashboard, configures Jinja2 templating,
and serves static files. Binds to localhost only (127.0.0.1).

Start with: python -m ui.app
"""

import datetime
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bridge.utc import utc_now

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def _filter_format_timestamp(ts: float | None) -> str:
    """Jinja2 filter: format Unix timestamp to humanized relative time."""
    if ts is None:
        return "-"
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)
    now = utc_now()
    diff = now - dt

    if diff.total_seconds() < 0:
        return dt.strftime("%H:%M")
    if diff.total_seconds() < 60:
        return "just now"
    if diff.total_seconds() < 3600:
        mins = int(diff.total_seconds() / 60)
        return f"{mins}m ago"
    if diff.total_seconds() < 86400 and dt.date() == now.date():
        return f"today {dt.strftime('%H:%M')}"
    if dt.date() == (now - datetime.timedelta(days=1)).date():
        return f"yesterday {dt.strftime('%H:%M')}"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def _filter_format_duration(seconds: float | None) -> str:
    """Jinja2 filter: format seconds to human-readable duration."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _filter_format_interval(seconds: int | None) -> str:
    """Jinja2 filter: format interval in seconds to label."""
    if not seconds:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _filter_format_relative(seconds: float | None) -> str:
    """Jinja2 filter: format seconds as relative time."""
    if seconds is None:
        return "-"
    abs_secs = abs(seconds)
    if abs_secs < 60:
        label = f"{abs_secs:.0f}s"
    elif abs_secs < 3600:
        label = f"{abs_secs / 60:.0f}m"
    elif abs_secs < 86400:
        label = f"{abs_secs / 3600:.1f}h"
    else:
        label = f"{abs_secs / 86400:.1f}d"
    if seconds < 0:
        return f"{label} overdue"
    return f"in {label}"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app with all routers mounted.
    """
    app = FastAPI(
        title="Valor System Dashboard",
        docs_url=None,
        redoc_url=None,
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Configure templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Register Jinja2 filters for template use
    templates.env.filters["format_timestamp"] = _filter_format_timestamp
    templates.env.filters["format_duration"] = _filter_format_duration
    templates.env.filters["format_interval_filter"] = _filter_format_interval
    templates.env.filters["format_relative"] = _filter_format_relative

    # Store templates in app state for access by routers
    app.state.templates = templates

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """Root route: single-page dashboard with all system state."""
        from ui.data.reflections import get_active_ignores, get_all_reflections, get_schedule
        from ui.data.sdlc import get_all_sessions

        sessions = get_all_sessions()
        reflections = get_all_reflections()
        schedule = get_schedule()
        ignores = get_active_ignores()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sessions": sessions,
                "reflections": reflections,
                "schedule": schedule,
                "ignores": ignores,
            },
        )

    @app.get("/_partials/sessions/", response_class=HTMLResponse)
    def partial_sessions_table(request: Request):
        """HTMX partial: refreshable sessions table."""
        from ui.data.sdlc import get_all_sessions

        sessions = get_all_sessions()
        return templates.TemplateResponse(
            request,
            "_partials/sessions_table.html",
            {"sessions": sessions},
        )

    # Exception handler for Redis connection failures
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Render a user-friendly error page instead of a 500 traceback."""
        return templates.TemplateResponse(
            request, "error.html", {"error": str(exc)}, status_code=500
        )

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("UI_PORT", "8500"))
    uvicorn.run(
        "ui.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        reload=False,
    )
