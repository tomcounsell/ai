"""How a turn tells the kernel what happened: files under `.valor/` in the
task's workspace, read when the turn ends.

- `.valor/question.md`: a question for Tom. The task waits for his answer.
- `.valor/advice.md`: a question for the advisor, a fresh session on the
  other vendor's model; the next turn opens with its answer. It counts
  only on a turn that ends no stage (`core/session.py`).
- `.valor/no_question.md`: from a clarify turn, why no question would
  change the result, and the approach.
- `.valor/plan.json`: from a plan turn, the committed plan file and its
  stakes, loop counts, and scope additions.
- `.valor/done.md`: from a build or patch turn, a candidate: a note to Tom
  above a line that is exactly `---`, then what was delivered and how it
  was verified. The kernel's reports to Tom carry the note
  (`core/notices.py`).
- `.valor/stop.md`: from a patch turn, Valor's decision to stop the work
  instead of patching again: a note to Tom saying what was built, what
  did not pass, and why it stops. The task ends (`task.ended`).
- `.valor/compact.md`: from a chat session's turn, a request to compact
  its conversation before its next turn; any text in it is added to the
  compaction's instructions.
- `.valor/wake.md`: from a chat session's turn, a request to be stepped
  again after a delay, watching one post: a delay (`20m`), optionally
  `post: <effect file or message id>`, then a note to itself.
- `.valor/screens/<name>.png|.html`: what `look` kept of a page, recorded
  as evidence (name and size) and never as a signal.
- `.valor/effects/<name>.json`: one request for an effect beyond the
  workspace, `{"action_type", "target", "payload"}`. The kernel passes each
  to the broker, which decides what it may do; one that is not JSON text,
  or that Postgres jsonb refuses to store, is answered as unreadable.

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
TEXT_SIGNALS = ("question", "no_question", "done", "advice", "stop", "compact", "wake")


def kind(value: Any) -> str:
    """A JSON value's type, as an error names it in place of the value."""
    return {dict: "an object", list: "a list", str: "a string", bool: "a boolean", type(None): "null"}.get(
        type(value), "a number"
    )


@dataclass
class Signals:
    question: str | None = None
    no_question: str | None = None
    done: str | None = None
    advice: str | None = None
    stop: str | None = None
    compact: str | None = None
    wake: str | None = None
    plan: dict[str, Any] | None = None
    plan_error: str | None = None
    stakes: dict[str, Any] | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    screens: list[dict[str, Any]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _object(name: str, body: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """`name` read as a JSON object: the object, or one line saying why not."""
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise TypeError("not a JSON object")
        return value, None
    except (ValueError, TypeError, RecursionError) as exc:
        return None, f"{name} is unreadable: {_why(exc)}"


def _stakes(signals: Signals, body: bytes | None, why: str | None) -> None:
    """`stakes.json` as `plan.json` is read: an object, or one line in `unreadable`."""
    if body is None:
        if why:
            signals.unreadable.append(why)
        return
    signals.stakes, error = _object("stakes.json", body)
    if error:
        signals.unreadable.append(error)


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
            signals.plan, signals.plan_error = _object("plan.json", body)
        elif why:
            signals.plan_error = f"plan.json is unreadable: {why}"
        body, why = _take(valor, "stakes.json", valor, turn_id)
        _stakes(signals, body, why)
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
            signals.plan, signals.plan_error = _object("plan.json", body)
        elif why:
            signals.plan_error = f"plan.json is unreadable: {why}"
    if signals.stakes is None and not any("stakes.json" in u for u in signals.unreadable):
        body, why = workspace.read_turn_file(handled, "stakes.json")
        _stakes(signals, body, why)
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
    """The request in `body`, or an `error` saying why it is unreadable:
    not JSON (nested past what Python's parser recurses through included),
    not an object with an action type and a target that are strings, or
    holding a surrogate
    code point. `json.loads` joins an escaped surrogate pair into the one
    character it encodes, so a surrogate left over (a lone escape, or a
    pair split between an escape and raw bytes) is not text: no UTF-8
    encodes it, and the broker and the bridges read strings as text. What
    else Postgres jsonb cannot store is Postgres's to say, when the request
    is recorded (`session.record`)."""
    try:
        request = json.loads(body)
        if not isinstance(request, dict):
            raise TypeError("not a JSON object")
        for key in ("action_type", "target"):
            if not isinstance(request[key], str):
                raise TypeError(f"{key} is {kind(request[key])}, not a string")
        found = {
            "action_type": request["action_type"],
            "target": request["target"],
            "payload": dict(request.get("payload") or {}),
        }
        json.dumps(found, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        entry["error"] = (
            "unreadable request: it holds a surrogate code point outside an escaped pair, which is not text"
        )
        return entry
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        entry["error"] = f"unreadable request: {_why(exc)}"
        return entry
    entry["request"] = found
    return entry


def _why(exc: BaseException) -> str:
    """A parse error as the kernel writes it: its type and message, never
    its repr. A `UnicodeDecodeError`'s repr holds the bytes it was given,
    the turn's file; its message, like `json`'s, names a position."""
    return f"{type(exc).__name__} ({exc})"


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
