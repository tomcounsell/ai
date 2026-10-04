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
  to the broker, which decides what it may do; one holding a NUL character,
  an unpaired surrogate, or a NaN or infinite number is answered as
  unreadable, since the ledger cannot store it.

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
import math
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


def recollect(workspace_dir: str | Path, turn_id: str) -> Signals:
    """Read again what a turn left, for a turn that ended but was never
    collected: first whatever is still in `.valor/` (a move the kill cut
    short), filed away and read as `collect` does, then what
    `.valor/handled/<turn_id>/` already holds, through the same walk. A file
    in both places is read once, as the one filed last: the copy from
    `.valor/`, which replaced the filed one, so what is read is what is
    kept. Screens are recorded the same way."""
    signals = collect(workspace_dir, turn_id)
    try:
        root = os.open(workspace_dir, workspace.DIR_FLAGS)
    except OSError:
        return signals
    try:
        handled, why = workspace.open_turn_dir(root, f"{DIR}/handled/{turn_id}")
    finally:
        os.close(root)
    if handled is None:
        if why:
            signals.unreadable.append(why)
        return signals
    try:
        _filed(signals, handled)
    finally:
        os.close(handled)
    return signals


def _filed(signals: Signals, handled: int) -> None:
    """Fill in from `handled/<turn_id>/` every signal `collect` did not
    read: each read in place, through `read_turn_file`."""
    for name in TEXT_SIGNALS:
        if getattr(signals, name) is None:
            body, why = workspace.read_turn_file(handled, f"{name}.md")
            if body is not None:
                setattr(signals, name, body.decode(errors="replace").strip() or f"(empty {name}.md)")
            elif why:
                signals.unreadable.append(why)
    if signals.plan is None and signals.plan_error is None:
        body, why = workspace.read_turn_file(handled, "plan.json")
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
    seen = {e["file"] for e in signals.effects}
    effects, why = workspace.open_turn_dir(handled, "effects")
    if effects is not None:
        try:
            for name in sorted(n for n in os.listdir(effects) if n.endswith(".json") and n not in seen):
                body, why = workspace.read_turn_file(effects, name)
                entry: dict[str, Any] = {"file": name}
                if body is None:
                    entry["error"] = (
                        f"unreadable request: {why or name + ' was listed and gone when it was read'}"
                    )
                    signals.effects.append(entry)
                else:
                    signals.effects.append(_request(entry, body))
        finally:
            os.close(effects)
    elif why:
        signals.unreadable.append(why)
    signals.effects.sort(key=lambda e: e["file"])
    seen = {e["name"] for e in signals.screens}
    screens, why = workspace.open_turn_dir(handled, "screens")
    if screens is not None:
        try:
            for name in sorted(n for n in os.listdir(screens) if n not in seen):
                fd, st, why = workspace.open_plain_file(screens, name)
                if fd is None:
                    signals.screens.append({"name": name, "refused": why or f"{name} was listed and gone"})
                    continue
                os.close(fd)
                signals.screens.append({"name": name, "bytes": st.st_size})
        finally:
            os.close(screens)
    elif why:
        signals.unreadable.append(why)
    signals.screens.sort(key=lambda e: e["name"])


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
            signals.effects.append(_request(entry, body))
    finally:
        os.close(effects)


def _request(entry: dict[str, Any], body: bytes) -> dict[str, Any]:
    """The request in `body`, or an `error` saying why it is unreadable. A
    request holding what Postgres jsonb cannot store (a NUL character, an
    unpaired surrogate, or a NaN or infinite number, all of which
    `json.loads` accepts) is unreadable: every row an effect gets holds its
    payload whole, and `turn.collected` the request, so it is answered here
    and never reaches the broker."""
    try:
        request = json.loads(body)
        if not isinstance(request, dict):
            raise TypeError("not a JSON object")
        found = {
            "action_type": str(request["action_type"]),
            "target": str(request["target"]),
            "payload": dict(request.get("payload") or {}),
        }
    except (ValueError, KeyError, TypeError) as exc:
        entry["error"] = f"unreadable request: {exc!r}"
        return entry
    why = _unstorable(found)
    if why:
        entry["error"] = (
            f"unreadable request: it holds {why}, which the ledger's JSON (Postgres jsonb) cannot store"
        )
    else:
        entry["request"] = found
    return entry


def _unstorable(value: Any) -> str | None:
    """What in `value` Postgres jsonb refuses, or None. A surrogate pair is
    stored as the one character it encodes; a surrogate alone is refused."""
    if isinstance(value, float) and not math.isfinite(value):
        return "a NaN or infinite number"
    if isinstance(value, str):
        if "\x00" in value:
            return "a NUL character"
        try:
            value.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
        except UnicodeDecodeError:
            return "an unpaired surrogate"
        return None
    if isinstance(value, dict):
        value = [part for pair in value.items() for part in pair]
    if isinstance(value, list):
        return next(filter(None, map(_unstorable, value)), None)
    return None


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
