"""How a turn tells the kernel what happened: files under `.valor/` in the
task's workspace, read when the turn ends.

- `.valor/question.md`: a question for Tom. The task waits for his answer.
- `.valor/done.md`: the work is delivered; the text says what and how it was
  verified.
- `.valor/effects/<name>.json`: one request for an effect beyond the
  workspace, `{"action_type", "target", "payload"}`. The kernel passes each
  to the broker, which decides what it may do.

Files rather than a line of final output, because writing a file is a
deliberate tool call that survives whatever prose follows it, and a turn
killed mid-way leaves whatever it wrote readable. Each file is moved to
`.valor/handled/<turn_id>/` once read, so no signal is read twice.

`PROTOCOL` is the text every workspace turn's Brief carries, and `CLARIFY`
the extra section a task in the `clarify` mode carries.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DIR = ".valor"

PROTOCOL = """# How this task reaches Tom

Tom is not watching this session. He reads only what you leave in three
places under `.valor/` in your workspace, which the kernel collects when your
turn ends. `.valor/` is ignored by git.

- To ask Tom a question, write it to `.valor/question.md` and end your turn.
  For reversible decisions: inspect, infer, prototype, show. Ask only when
  the answer materially changes the outcome or the authority required. Your
  next turn opens with his answer, in this same session.
- When the work is finished and ready for Tom to use, write `.valor/done.md`:
  what you delivered, how you verified it, and anything he should know. Then
  end your turn.
- An effect beyond the workspace is a request, one JSON file per request in
  `.valor/effects/<name>.json`, of the form
  `{"action_type": "...", "target": "...", "payload": {...}}`. The kernel
  performs it or holds it for Tom's approval. Available here:
  `push_branch`, target the branch name, payload `{"head_sha": "<full sha>"}`,
  pushes that commit to the branch on `origin` once Tom approves. Pushing
  any other way is not available to you.

A turn that ends with neither a question nor `done.md` is resumed with
"Continue." """

# The `clarify` arm's opening, carried in the Brief of a task started with
# `--mode clarify`. It is the experiment's data, not a rule the kernel
# checks: nothing reads the first turn for compliance.
CLARIFY = """# Mode: clarify

This task opens with a clarifying turn. In your first turn, only inspect:
read the code and whatever else in the workspace you need, and change no
file. End that turn by writing `.valor/question.md` holding:

1. The questions for Tom whose answers would materially change what you
   build or the authority it needs, numbered, each with the answer you will
   assume if he leaves it open. Ask nothing you can settle by reading the
   code.
2. The approach you intend to take, in a few lines.

If you have no such questions, say so in `question.md` and still state your
approach. Tom's reply opens your next turn; build from there."""


@dataclass
class Signals:
    question: str | None = None
    done: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)


def collect(workspace: str | Path, turn_id: str) -> Signals:
    """Read and move aside everything the turn left. An effect file that is
    not a JSON object comes back with an `error` and no request."""
    root = Path(workspace) / DIR
    handled = root / "handled" / turn_id
    signals = Signals()
    for name in ("question", "done"):
        path = root / f"{name}.md"
        if path.is_file():
            setattr(signals, name, path.read_text().strip() or f"(empty {name}.md)")
            _move(path, handled / path.name)
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
