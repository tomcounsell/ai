"""SDLC Observer route handlers.

All route handlers use sync (def) functions rather than async (async def)
so that FastAPI runs them in a threadpool. This avoids blocking the event
loop with Popoto's synchronous Redis calls.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["sdlc"])


@router.get("/", response_class=HTMLResponse)
def sdlc_overview(request: Request):
    """Active pipelines with stage indicators."""
    from ui.data.sdlc import get_active_pipelines, get_recent_completions

    templates = request.app.state.templates
    active = get_active_pipelines()
    completed = get_recent_completions(limit=10)
    return templates.TemplateResponse(
        request,
        "sdlc/pipelines.html",
        {"active_pipelines": active, "completed_pipelines": completed},
    )


@router.get("/completed/", response_class=HTMLResponse)
def sdlc_completed(request: Request, page: int = 1):
    """Recent completed pipelines with outcomes."""
    from ui.data.sdlc import get_recent_completions

    templates = request.app.state.templates
    completed = get_recent_completions(limit=25, page=page)
    return templates.TemplateResponse(
        request, "sdlc/completed.html", {"pipelines": completed, "page": page}
    )


# HTMX partial endpoints


@router.get("/_partials/active/", response_class=HTMLResponse)
def partial_active_pipelines(request: Request):
    """HTMX partial: refreshable active pipeline cards."""
    from ui.data.sdlc import get_active_pipelines

    templates = request.app.state.templates
    active = get_active_pipelines()
    return templates.TemplateResponse(
        request, "sdlc/_partials/active_pipelines.html", {"active_pipelines": active}
    )


@router.get("/_partials/stage-indicator/{job_id}/", response_class=HTMLResponse)
def partial_stage_indicator(request: Request, job_id: str):
    """HTMX partial: single pipeline stage indicator."""
    from ui.data.sdlc import get_pipeline_detail

    templates = request.app.state.templates
    pipeline = get_pipeline_detail(job_id)
    return templates.TemplateResponse(
        request, "sdlc/_partials/stage_indicator.html", {"pipeline": pipeline}
    )


# Parameterized route MUST be last to avoid shadowing static routes above
@router.get("/{job_id}/", response_class=HTMLResponse)
def sdlc_detail(request: Request, job_id: str):
    """Pipeline detail with stage transition timeline."""
    from ui.data.sdlc import get_pipeline_detail

    templates = request.app.state.templates
    pipeline = get_pipeline_detail(job_id)
    return templates.TemplateResponse(request, "sdlc/detail.html", {"pipeline": pipeline})
