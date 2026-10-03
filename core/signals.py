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
`.valor/handled/<turn_id>/` once read, so no signal is read twice.
"""

import json
import os
import stat
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
    screens: list[dict[str, Any]] = field(default_factory=list)


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
    signals.screens = read_screens(workspace, turn_id)
    return signals


def read_screens(workspace: str | Path, turn_id: str) -> list[dict[str, Any]]:
    """The files in `.valor/screens/` as `{name, bytes}`, each opened
    relative to a directory descriptor without following links or blocking,
    and recorded only when a regular file with one link; anything else is
    `{name, refused: reason}` and is never read. Each entry is then moved to
    `.valor/handled/<turn_id>/screens/`. Written to be replaced by the shared
    safe-read helper."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    out: list[dict[str, Any]] = []
    fds: list[int] = []
    try:
        parent = os.open(workspace, flags | os.O_DIRECTORY)
        fds.append(parent)
        for part in (DIR, "screens"):
            parent = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            fds.append(parent)
    except OSError:
        return out  # no screens directory, or one that is not a plain directory
    try:
        screens = parent
        valor = fds[-2]
        dest = None
        for name in sorted(os.listdir(screens)):
            entry: dict[str, Any] = {"name": name}
            try:
                st = os.stat(name, dir_fd=screens, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    entry["refused"] = "not a regular file"
                elif st.st_nlink != 1:
                    entry["refused"] = "more than one link"
                else:
                    fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=screens)
                    try:
                        after = os.fstat(fd)
                    finally:
                        os.close(fd)
                    if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
                        raise OSError("changed while read")
                    entry["bytes"] = after.st_size
            except OSError as exc:
                entry["refused"] = exc.strerror or str(exc)
            if dest is None:
                dest = _screens_dest(valor, turn_id, fds)
            try:
                if dest is None:
                    raise OSError("no place to file it")
                os.rename(name, name, src_dir_fd=screens, dst_dir_fd=dest)
            except OSError:
                # A screen that cannot be filed away counts as unreadable and
                # is removed, so no later turn records it again.
                entry = {"name": name, "refused": "could not be moved aside"}
                _remove(screens, name)
            out.append(entry)
    except OSError:
        pass
    finally:
        for fd in fds:
            os.close(fd)
    return out


def _remove(dir_fd: int, name: str) -> None:
    for unlink in (os.unlink, os.rmdir):
        try:
            unlink(name, dir_fd=dir_fd)
            return
        except OSError:
            continue


def _screens_dest(valor: int, turn_id: str, fds: list[int]) -> int | None:
    """`.valor/handled/<turn_id>/screens/` as a descriptor, made step by step
    relative to `.valor` and never through a link."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    parent = valor
    for part in ("handled", turn_id, "screens"):
        try:
            os.mkdir(part, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError:
            return None
        try:
            parent = os.open(part, flags, dir_fd=parent)
        except OSError:
            return None
        fds.append(parent)
    return parent


def _move(path: Path, to: Path) -> None:
    to.parent.mkdir(parents=True, exist_ok=True)
    path.replace(to)
