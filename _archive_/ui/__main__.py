"""Allow running the UI server with: python -m ui.app"""

import os

import uvicorn

port = int(os.environ.get("UI_PORT", "8500"))
uvicorn.run(
    "ui.app:create_app",
    factory=True,
    host="127.0.0.1",
    port=port,
    reload=False,
)
