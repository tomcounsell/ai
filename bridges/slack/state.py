"""`slack-seen.json`: where each chat's next gap-fill pass starts.

Per chat, `{"ts": "<ts>", "inclusive": <bool>}`: the newest `ts` the last
completed pass read, or the `ts` of a message whose receive is still
running or failed, which the next pass reads again (`inclusive`). The file
is written whole to a temporary file, flushed, moved into place, and the
directory flushed. A file that cannot be read is set aside as
`slack-seen.json.unreadable` and the state starts empty: each chat then
starts at its lowest recorded `ts`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("valor.slack")


def _valid(v) -> bool:
    return isinstance(v, dict) and isinstance(v.get("ts"), str) and isinstance(v.get("inclusive"), bool)


class Seen:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text())
            except ValueError:
                data = None
            if isinstance(data, dict) and all(_valid(v) for v in data.values()):
                self.data = data
            else:
                aside = path.with_name(path.name + ".unreadable")
                os.replace(path, aside)
                log.warning("%s could not be read; set aside as %s", path.name, aside.name)

    def get(self, chat: str) -> tuple[str, bool] | None:
        v = self.data.get(chat)
        return None if v is None else (v["ts"], v["inclusive"])

    def set(self, chat: str, ts: str, inclusive: bool) -> None:
        self.data[chat] = {"ts": ts, "inclusive": inclusive}
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
