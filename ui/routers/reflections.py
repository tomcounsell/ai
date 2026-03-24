"""Reflections dashboard route handlers.

All route handlers use sync (def) functions rather than async (async def)
so that FastAPI runs them in a threadpool. This avoids blocking the event
loop with Popoto's synchronous Redis calls.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["reflections"])


@router.get("/", response_class=HTMLResponse)
def reflections_overview(request: Request):
    """Overview of all registered reflections with status badges."""
    from ui.data.reflections import get_all_reflections

    templates = request.app.state.templates
    reflections = get_all_reflections()
    return templates.TemplateResponse(
        request, "reflections/overview.html", {"reflections": reflections}
    )


@router.get("/schedule/", response_class=HTMLResponse)
def reflections_schedule(request: Request):
    """Upcoming runs ordered by next-due timestamp."""
    from ui.data.reflections import get_schedule

    templates = request.app.state.templates
    schedule = get_schedule()
    return templates.TemplateResponse(request, "reflections/schedule.html", {"schedule": schedule})


@router.get("/ignores/", response_class=HTMLResponse)
def reflections_ignores(request: Request):
    """Active ignore patterns with expiry information."""
    from ui.data.reflections import get_active_ignores

    templates = request.app.state.templates
    ignores = get_active_ignores()
    return templates.TemplateResponse(request, "reflections/ignores.html", {"ignores": ignores})


@router.get("/{name}/history/", response_class=HTMLResponse)
def reflection_history(request: Request, name: str, page: int = 1):
    """Paginated run history for a specific reflection."""
    from ui.data.reflections import get_run_history

    templates = request.app.state.templates
    history_data = get_run_history(name, page=page)
    return templates.TemplateResponse(
        request,
        "reflections/history.html",
        {
            "name": name,
            "runs": history_data["runs"],
            "page": page,
            "total_pages": history_data["total_pages"],
        },
    )


@router.get("/{name}/history/{run_index}/", response_class=HTMLResponse)
def reflection_detail(request: Request, name: str, run_index: int):
    """Detail view for a single reflection run with log content."""
    from ui.data.reflections import get_run_detail

    templates = request.app.state.templates
    run = get_run_detail(name, run_index)
    return templates.TemplateResponse(
        request, "reflections/detail.html", {"name": name, "run": run}
    )


# HTMX partial endpoints


@router.get("/_partials/status-grid/", response_class=HTMLResponse)
def partial_status_grid(request: Request):
    """HTMX partial: refreshable status grid for all reflections."""
    from ui.data.reflections import get_all_reflections

    templates = request.app.state.templates
    reflections = get_all_reflections()
    return templates.TemplateResponse(
        request, "reflections/_partials/status_grid.html", {"reflections": reflections}
    )


@router.get("/_partials/log/{name}/{run_index}/", response_class=HTMLResponse)
def partial_log_viewer(request: Request, name: str, run_index: int):
    """HTMX partial: log content for a specific run."""
    from ui.data.reflections import get_log_content

    templates = request.app.state.templates
    log_content = get_log_content(name, run_index)
    return templates.TemplateResponse(
        request,
        "reflections/_partials/log_viewer.html",
        {"log_content": log_content, "name": name},
    )
