"""`python -m ui`: the status page, on 127.0.0.1 at `settings.ui_port`.
Started by hand when Tom opens it; there is no launchd job."""

from aiohttp import web

from core.settings import settings
from ui.app import make_app

if __name__ == "__main__":
    web.run_app(make_app(), host="127.0.0.1", port=settings.ui_port, print=None)
