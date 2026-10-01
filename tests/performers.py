"""Two local performers for the broker, used only by the tests.

`WorkspaceWrite` (`propose`) writes a file inside a task's workspace
directory: reversible, since the file can be deleted. `OutboxAppend` (`act`)
appends a message to a local outbox file that stands where a bridge's send
will: once a line is in the outbox it counts as sent. Both look their
idempotency key up on the target, so a dangling intent can be reconciled.
"""

import json
from pathlib import Path


class WorkspaceWrite:
    action_type = "workspace_write"
    effect_class = "propose"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, action) -> Path:
        path = (self.root / action.target).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"{action.target} is outside the workspace")
        return path

    def perform(self, action, key: str) -> dict:
        path = self._path(action)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.payload["text"])
        return {"path": str(path), "bytes": len(action.payload["text"].encode())}

    def lookup(self, action, key: str) -> dict | None:
        path = self._path(action)
        if path.exists() and path.read_text() == action.payload["text"]:
            return {"path": str(path), "bytes": path.stat().st_size}
        return None


class OutboxAppend:
    action_type = "outbox_send"
    effect_class = "act"

    def __init__(self, outbox: Path):
        self.outbox = Path(outbox)

    def perform(self, action, key: str) -> dict:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox.open("a") as f:
            f.write(json.dumps({"key": key, "to": action.target, **action.payload}) + "\n")
        return {"outbox": str(self.outbox), "key": key}

    def lookup(self, action, key: str) -> dict | None:
        if not self.outbox.exists():
            return None
        for line in self.outbox.read_text().splitlines():
            if json.loads(line).get("key") == key:
                return {"outbox": str(self.outbox), "key": key}
        return None
