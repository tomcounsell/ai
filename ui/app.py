"""FastAPI application factory for the unified web UI.

Single-page dashboard with HTMX partials for live refresh.
Binds to localhost only (127.0.0.1).

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
        Configured FastAPI app with all routes mounted.
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

    # Store templates in app state for access by partials
    app.state.templates = templates

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """Single-page dashboard with all sections."""
        from ui.data.reflections import get_all_reflections
        from ui.data.sdlc import get_active_pipelines, get_recent_completions

        reflections = get_all_reflections()
        active_pipelines = get_active_pipelines()
        completed_pipelines = get_recent_completions(limit=10)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "reflections": reflections,
                "active_pipelines": active_pipelines,
                "completed_pipelines": completed_pipelines,
            },
        )

    # HTMX partial endpoints

    @app.get("/_partials/reflections-status/", response_class=HTMLResponse)
    def partial_reflections_status(request: Request):
        """HTMX partial: refreshable status grid for all reflections."""
        from ui.data.reflections import get_all_reflections

        reflections = get_all_reflections()
        return templates.TemplateResponse(
            request,
            "reflections/_partials/status_grid.html",
            {"reflections": reflections},
        )

    @app.get("/_partials/sdlc-active/", response_class=HTMLResponse)
    def partial_sdlc_active(request: Request):
        """HTMX partial: refreshable active pipeline cards."""
        from ui.data.sdlc import get_active_pipelines

        active = get_active_pipelines()
        return templates.TemplateResponse(
            request,
            "sdlc/_partials/active_pipelines.html",
            {"active_pipelines": active},
        )

    @app.get("/_partials/sdlc-stage/{job_id}/", response_class=HTMLResponse)
    def partial_sdlc_stage(request: Request, job_id: str):
        """HTMX partial: single pipeline stage indicator."""
        from ui.data.sdlc import get_pipeline_detail

        pipeline = get_pipeline_detail(job_id)
        return templates.TemplateResponse(
            request,
            "sdlc/_partials/stage_indicator.html",
            {"pipeline": pipeline},
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
