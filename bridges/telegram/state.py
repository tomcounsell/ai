"""The bridge's own state, in JSON files beside the session.

Two files: `telegram-seen.json` (the newest message id each chat's last
gap-fill pass saw) and `telegram-sends.json` (for each send in flight,
the newest message id in its chat before its first message). Each is
written whole to a temporary file, flushed to disk, moved into place, and
the directory flushed, so a file is never half written and a write that
returned survives a power loss. With no path the state lives in memory.

A file that cannot be read (empty, cut short, not JSON) is set aside as
`<name>.unreadable` and the state starts empty with `lost` true; the
caller rebuilds what it needs from the ledger or from Telegram.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("valor.telegram")


class State:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, Any] = {}
        self.lost = False
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text())
            except ValueError:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
                data = None
            if isinstance(data, dict):
                self.data = data
            else:
                self.lost = True
                aside = path.with_name(path.name + ".unreadable")
                os.replace(path, aside)
                log.warning("%s could not be read; set aside as %s", path.name, aside.name)

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def names(self) -> list[str]:
        return list(self.data)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._write()

    def drop(self, keys: list[str]) -> None:
        gone = [k for k in keys if k in self.data]
        for k in gone:
            del self.data[k]
        if gone:
            self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w") as f:
            f.write(json.dumps(self.data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
