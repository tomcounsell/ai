"""How a turn tells the kernel what happened: files under `.valor/` in the
task's workspace, read when the turn ends.

- `.valor/question.md`: a question for Tom. The task waits for his answer.
- `.valor/no_question.md`: from a clarify turn, why no question would
  change the result, and the approach.
- `.valor/plan.json`: from a plan turn, the committed plan file and its
  stakes, loop counts, and scope additions.
- `.valor/done.md`: from a build or patch turn, a candidate: what was
  delivered and how it was verified.
- `.valor/screens/<name>.png|.html`: what `look` kept of a page, recorded
  as evidence (name and size) and never as a signal.
- `.valor/effects/<name>.json`: one request for an effect beyond the
  workspace, `{"action_type", "target", "payload"}`. The kernel passes each
  to the broker, which decides what it may do.

Which signal counts in which state is `core/session.py`'s. The text a turn
reads about this channel is `skills/sdlc/channel.md`, rendered into its
Brief by `core/tasks.py`.

Files rather than a line of final output, because writing a file is a
deliberate tool call that survives whatever prose follows it, and a turn
killed mid-way leaves whatever it wrote readable. Each file is moved to
`.valor/handled/<turn_id>/` and read there, so no signal is read twice.

The turn controls everything under its workspace, so every lookup goes
through `core.workspace`'s descriptor walk: no link is followed, no FIFO
blocks, and a file is read only when it is a regular file with one link.
Anything else is recorded in `unreadable` with a reason, never its
contents. An entry that cannot be moved is removed unread.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import workspace

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
    screens: list[dict[str, Any]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def collect(workspace_dir: str | Path, turn_id: str) -> Signals:
    """Move aside, then read, everything the turn left. An effect file that
    is not a JSON object comes back with an `error` and no request; so does
    a `plan.json` that is not one. A text signal that cannot be read counts
    as absent, with its reason in `unreadable`."""
    signals = Signals()
    try:
        root = os.open(workspace_dir, workspace.DIR_FLAGS)
    except FileNotFoundError:
        return signals
    except OSError as exc:
        signals.unreadable.append(f"the workspace is not a plain directory ({exc.strerror})")
        return signals
    try:
        valor, why = workspace.open_turn_dir(root, DIR)
    finally:
        os.close(root)
    if valor is None:
        if why:
            signals.unreadable.append(why)
        return signals
    try:
        for name in TEXT_SIGNALS:
            body, why = _take(valor, f"{name}.md", valor, turn_id)
            if body is not None:
                setattr(signals, name, body.decode(errors="replace").strip() or f"(empty {name}.md)")
            elif why:
                signals.unreadable.append(why)
        body, why = _take(valor, "plan.json", valor, turn_id)
        if body is not None:
            try:
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise TypeError("not a JSON object")
                signals.plan = value
            except (ValueError, TypeError) as exc:
                signals.plan_error = f"plan.json is unreadable: {exc!r}"
        elif why:
            signals.plan_error = f"plan.json is unreadable: {why}"
        _effects(signals, valor, turn_id)
        signals.screens = _screens(signals, valor, turn_id)
    finally:
        os.close(valor)
    return signals


def _effects(signals: Signals, valor: int, turn_id: str) -> None:
    effects, why = workspace.open_turn_dir(valor, "effects")
    if effects is None:
        if why:
            signals.unreadable.append(why)
        return
    try:
        for name in sorted(n for n in os.listdir(effects) if n.endswith(".json")):
            entry: dict[str, Any] = {"file": name}
            body, why = _take(effects, name, valor, turn_id, ("effects",))
            if body is None:
                entry["error"] = (
                    f"unreadable request: {why or name + ' was listed and gone when it was moved'}"
                )
                signals.effects.append(entry)
                continue
            try:
                request = json.loads(body)
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
    finally:
        os.close(effects)


def _screens(signals: Signals, valor: int, turn_id: str) -> list[dict[str, Any]]:
    """The files in `.valor/screens/` as `{name, bytes}`, each filed away
    into `handled/<turn_id>/screens/` first and sized there from `fstat`,
    never read. Anything but a regular file with one link is `{name,
    refused}`; so is an entry that cannot be filed away, which is removed
    unread so no later turn records it again. A sparse screen is sized, not
    refused: nothing reads it."""
    screens, why = workspace.open_turn_dir(valor, "screens")
    if screens is None:
        if why:
            signals.unreadable.append(why)
        return []
    out: list[dict[str, Any]] = []
    try:
        for name in sorted(os.listdir(screens)):
            dest, why = workspace._file_away(screens, name, valor, turn_id, ("screens",))
            if dest is None:
                out.append({"name": name, "refused": why or f"{name} was listed and gone when it was moved"})
                continue
            try:
                fd, st, why = workspace.open_plain_file(dest, name)
            finally:
                os.close(dest)
            if fd is None:
                out.append({"name": name, "refused": why or f"{name} was gone from handled/{turn_id}"})
                continue
            os.close(fd)
            out.append({"name": name, "bytes": st.st_size})
    finally:
        os.close(screens)
    return out


def _take(
    src: int, name: str, valor: int, turn_id: str, sub: tuple[str, ...] = ()
) -> tuple[bytes | None, str | None]:
    """File `name` away into `handled/<turn_id>/<sub...>/`, then read it
    there. (None, None) when there is no such entry."""
    dest, why = workspace._file_away(src, name, valor, turn_id, sub)
    if dest is None:
        return None, why
    try:
        body, why = workspace.read_turn_file(dest, name)
    finally:
        os.close(dest)
    if body is None and why is None:
        why = f"{name} was gone from handled/{turn_id} when it was read"
    return body, why
