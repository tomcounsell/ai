"""How a turn tells the kernel what happened: files under `.valor/` in the
task's workspace, read when the turn ends.

- `.valor/question.md`: a question for Tom. The task waits for his answer.
- `.valor/no_question.md`: from a clarify turn, why no question would
  change the result, and the approach.
- `.valor/plan.json`: from a plan turn, the committed plan file and its
  stakes, loop counts, and scope additions.
- `.valor/done.md`: from a build or patch turn, a candidate: what was
  delivered and how it was verified.
- `.valor/effects/<name>.json`: one request for an effect beyond the
  workspace, `{"action_type", "target", "payload"}`. The kernel passes each
  to the broker, which decides what it may do.

Which signal counts in which state is `core/session.py`'s. The text a turn
reads about this channel is `skills/sdlc/channel.md`, rendered into its
Brief by `core/tasks.py`.

Files rather than a line of final output, because writing a file is a
deliberate tool call that survives whatever prose follows it, and a turn
killed mid-way leaves whatever it wrote readable. Each file is moved to
`.valor/handled/<turn_id>/` once read, so no signal is read twice.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DIR = ".valor"
TEXT_SIGNALS = ("question", "no_question", "done")


@dataclass
class Signals:
    question: str | None = None
    no_question: str | None = None
    done: str | None = None
    plan: dict[str, Any] | None = None
    plan_error: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)


def collect(workspace: str | Path, turn_id: str) -> Signals:
    """Read and move aside everything the turn left. An effect file that is
    not a JSON object comes back with an `error` and no request; so does a
    `plan.json` that is not one."""
    root = Path(workspace) / DIR
    handled = root / "handled" / turn_id
    signals = Signals()
    for name in TEXT_SIGNALS:
        path = root / f"{name}.md"
        if path.is_file():
            setattr(signals, name, path.read_text().strip() or f"(empty {name}.md)")
            _move(path, handled / path.name)
    plan = root / "plan.json"
    if plan.is_file():
        try:
            value = json.loads(plan.read_text())
            if not isinstance(value, dict):
                raise TypeError("not a JSON object")
            signals.plan = value
        except (ValueError, TypeError) as exc:
            signals.plan_error = f"plan.json is unreadable: {exc!r}"
        _move(plan, handled / plan.name)
    effects = root / "effects"
    for path in sorted(effects.glob("*.json")) if effects.is_dir() else []:
        entry: dict[str, Any] = {"file": path.name}
        try:
            request = json.loads(path.read_text())
            if not isinstance(request, dict):
                raise TypeError("not a JSON object")
            entry["request"] = {
                "action_type": str(request["action_type"]),
                "target": str(request["target"]),
                "payload": dict(request.get("payload") or {}),
            }
        except (ValueError, KeyError, TypeError) as exc:
            entry["error"] = f"unreadable request: {exc!r}"
        signals.effects.append(entry)
        _move(path, handled / "effects" / path.name)
    return signals


def _move(path: Path, to: Path) -> None:
    to.parent.mkdir(parents=True, exist_ok=True)
    path.replace(to)
