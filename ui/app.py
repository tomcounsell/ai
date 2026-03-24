"""FastAPI application factory for the unified web UI.

Mounts sub-routers for each dashboard, configures Jinja2 templating,
and serves static files. Binds to localhost only (127.0.0.1).

Start with: python -m ui.app
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


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
        return templates.TemplateResponse(
            "index.html",
            {"request": request},
        )

    # Exception handler for Redis connection failures
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Render a user-friendly error page instead of a 500 traceback."""
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(exc)},
            status_code=500,
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
