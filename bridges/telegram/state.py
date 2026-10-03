"""The bridge's own state, in JSON files beside the session.

Two files: `telegram-seen.json` (the newest message id each chat's last
gap-fill pass saw) and `telegram-sends.json` (for each send's broker key,
the chat and the newest message id in it before the send). Each is
written whole to a temporary file and moved into place, so a file is
never half written. With no path the state lives in memory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, Any] = {}
        if path is not None and path.exists():
            self.data = json.loads(path.read_text())

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        if self.path is not None:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data))
            os.replace(tmp, self.path)
