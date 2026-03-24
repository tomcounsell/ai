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

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def _filter_format_timestamp(ts: float | None) -> str:
    """Jinja2 filter: format Unix timestamp to readable datetime."""
    if ts is None:
        return "-"
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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

    # Register routers
    from ui.routers.reflections import router as reflections_router
    from ui.routers.sdlc import router as sdlc_router

    app.include_router(reflections_router, prefix="/reflections")
    app.include_router(sdlc_router, prefix="/sdlc")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """Root route: dashboard listing page."""
        return templates.TemplateResponse(request, "index.html")

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
