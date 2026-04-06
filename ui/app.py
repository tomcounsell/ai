"""FastAPI application factory for the unified web UI.

Mounts sub-routers for each dashboard, configures Jinja2 templating,
and serves static files. Binds to localhost only (127.0.0.1).

Start with: python -m ui.app
"""

import datetime
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bridge.utc import utc_now

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def _filter_format_timestamp(ts: float | None) -> str:
    """Jinja2 filter: format Unix timestamp to humanized relative time."""
    if ts is None:
        return "-"
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).astimezone()
    now = utc_now().astimezone()
    diff = now - dt

    if diff.total_seconds() < 0:
        return dt.strftime("%H:%M")
    if diff.total_seconds() < 60:
        return "just now"
    if diff.total_seconds() < 3600:
        mins = int(diff.total_seconds() / 60)
        return f"{mins}m ago"
    if diff.total_seconds() < 86400 and dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.date() == (now - datetime.timedelta(days=1)).date():
        return f"yesterday {dt.strftime('%H:%M')}"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def _filter_format_duration(seconds: float | None) -> str:
    """Jinja2 filter: format seconds to compact integer duration."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return "<1m"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{int(seconds / 3600)}h"


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
        from ui.data.machine import get_machine_name, get_machine_projects
        from ui.data.reflections import get_all_reflections
        from ui.data.sdlc import get_all_sessions

        sessions = get_all_sessions()
        reflections = get_all_reflections()
        machine_projects = get_machine_projects()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sessions": sessions,
                "reflections": reflections,
                "machine_name": get_machine_name(),
                "machine_projects": machine_projects,
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

    @app.get("/session/{agent_session_id}/modal-content", response_class=HTMLResponse)
    def session_modal_content(request: Request, agent_session_id: str):
        """HTMX partial: session detail content for modal."""
        from ui.data.sdlc import get_pipeline_detail

        pipeline = get_pipeline_detail(agent_session_id)
        return templates.TemplateResponse(
            request,
            "_partials/session_modal_content.html",
            {"pipeline": pipeline},
        )

    def _get_bridge_health() -> dict:
        """Check bridge health from last_connected file freshness."""
        import time

        last_connected_file = Path(__file__).parent.parent / "data" / "last_connected"
        try:
            if last_connected_file.exists():
                mtime = last_connected_file.stat().st_mtime
                age_s = round(time.time() - mtime)
                # Bridge writes last_connected every ~5min in heartbeat loop
                if age_s < 360:
                    return {"status": "ok", "age_s": age_s}
                elif age_s < 600:
                    return {"status": "running", "age_s": age_s}
                else:
                    return {"status": "error", "age_s": age_s}
        except OSError:
            pass
        return {"status": "error", "age_s": None}

    def _session_to_json(s) -> dict:
        """Serialize a PipelineProgress to JSON dict for the dashboard API."""
        result = {
            "agent_session_id": s.agent_session_id,
            "session_id": s.session_id,
            "display_name": s.display_name,
            "session_type": s.session_type,
            "status": s.status,
            "project_key": s.project_key,
            "project_name": s.project_name,
            "slug": s.slug,
            "branch_name": s.branch_name,
            "current_stage": s.current_stage,
            "stages": [{"name": st.name, "status": st.status} for st in s.stages],
            "created_at": s.created_at,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "updated_at": s.updated_at,
            "duration": s.duration,
            "issue_url": s.issue_url,
            "pr_url": s.pr_url,
            "message_text": s.message_text,
            "parent_agent_session_id": s.parent_agent_session_id,
            "context_summary": s.context_summary,
            "expectations": s.expectations,
            "turn_count": s.turn_count,
            "tool_call_count": s.tool_call_count,
            "watchdog_unhealthy": s.watchdog_unhealthy,
            "priority": s.priority,
            "classification_type": s.classification_type,
            "is_stale": s.is_stale,
            "children": [_session_to_json(c) for c in s.children],
            "events": [
                {
                    "role": e.role,
                    "text": e.text,
                    "timestamp": e.timestamp,
                }
                for e in s.events
            ],
        }
        return result

    @app.get("/dashboard.json")
    def dashboard_json():
        """Full dashboard state as JSON for programmatic consumption."""
        from fastapi.responses import JSONResponse

        from ui.data.machine import get_machine_name, get_machine_projects
        from ui.data.reflections import get_all_reflections
        from ui.data.sdlc import get_all_sessions

        bridge = _get_bridge_health()
        sessions = get_all_sessions()
        reflections = get_all_reflections()

        return JSONResponse(
            {
                "health": {
                    "webserver": "ok",
                    "bridge": bridge["status"],
                    "bridge_last_seen_s": bridge["age_s"],
                },
                "sessions": [_session_to_json(s) for s in sessions],
                "reflections": reflections,
                "machine": {
                    "name": get_machine_name(),
                    "projects": get_machine_projects(),
                },
            }
        )

    @app.get("/health")
    def health_status():
        """Health JSON endpoint for programmatic access."""
        from fastapi.responses import JSONResponse

        bridge = _get_bridge_health()
        return JSONResponse(
            {
                "webserver": "ok",
                "bridge": bridge["status"],
                "bridge_last_seen_s": bridge["age_s"],
            }
        )

    @app.get("/_partials/health/", response_class=HTMLResponse)
    def partial_health(request: Request):
        """HTMX partial: health indicator badges."""
        bridge = _get_bridge_health()
        bridge_label = "bridge: ok"
        if bridge["status"] == "running":
            bridge_label = f"bridge: slow ({bridge['age_s']}s)"
        elif bridge["status"] == "error":
            bridge_label = "bridge: down"
        return HTMLResponse(
            f'<span class="badge badge-{bridge["status"]}">{bridge_label}</span>'
            f'<span class="badge badge-ok">web: ok</span>'
        )

    # Exception handler for Redis connection failures
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Render a user-friendly error page instead of a 500 traceback."""
        return templates.TemplateResponse(
            request, "error.html", {"error": str(exc)}, status_code=500
        )

    # Startup probe: log session count for index staleness detection
    @app.on_event("startup")
    def _log_session_count():
        try:
            from models.agent_session import AgentSession

            count = len(AgentSession.query.all())
            logger.info(f"Dashboard startup: {count} AgentSession records found in Redis")
            if count == 0:
                logger.warning(
                    "Dashboard startup: zero sessions found. "
                    "Popoto indexes may be stale after restart."
                )
        except Exception as e:
            logger.warning(f"Dashboard startup: failed to query sessions: {e}")

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
