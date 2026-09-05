"""Ancestor-safe PID lookup for the long-lived Python services (#3164).

Why this exists instead of ``pgrep``
------------------------------------

BSD ``pgrep`` — the one macOS ships — documents:

    -a  Include process ancestors in the match list. By default, the calling
        process and its ancestors are excluded.

Every long-lived service in this repo spawns agent sessions as child
processes (``claude -p`` under the bridge and under the worker), and those
sessions routinely run code that asks "is the bridge running?" —
``python -m scripts.update.verify_release``, ``python -m tools.doctor``, the
UI health endpoints. For all of them the service is an *ancestor*, so
``pgrep`` silently reports nothing and a perfectly healthy process reads as
absent. On Valor the Captain that turned a correct, freshly-beaconed bridge
into ``release verify OK @ 45d5d42d4 (bridge unknown, worker matches)``.

Passing ``-a`` is not the fix: on Linux/procps ``-a`` means "print the full
command line", so the flag silently changes meaning off macOS.

``ps`` has no ancestor filter, so a descendant sees its own ancestors. This
module reads the process table with ``ps`` and parses each command line as a
**CPython invocation** — interpreter, then its own ``-m`` module or its own
script position — rather than scanning it for a substring. That is what keeps
``python -m ruff check bridge/telegram_bridge.py`` from being reported as the
bridge: the path is there, but as a program argument, not as what the process
is running.

Not for use in kill paths
-------------------------

``monitoring/bridge_watchdog.py::kill_stale_processes`` deliberately stays on
``pgrep``. There, ancestor exclusion is load-bearing rather than a bug: an
ancestor-safe lookup feeding ``os.kill(pid, 9)`` would let a bridge-descended
caller SIGKILL its own live ancestor bridge.

Where a signalling or restart path does need a PID from here, it must gate on
:func:`is_own_ancestor` first. ``pgrep`` made caller-fratricide unreachable by
accident; with an ancestor-safe lookup it becomes a decision the caller is
responsible for making explicitly.

Known limitation
----------------

``ps -o args=`` returns one string per process, so argv is recovered by
splitting on whitespace. A service whose interpreter or script path contains a
space would not match. No path in this repo's launchd plists contains one.
``-ww`` is passed so the command line is never truncated to terminal width.
"""

from __future__ import annotations

import os
import subprocess

__all__ = ["find_python_service_pids", "is_own_ancestor", "list_processes"]

# Bounded so a wedged `ps` can never hang a watchdog tick or an update run.
_PS_TIMEOUT_SECONDS = 10


def list_processes() -> list[tuple[int, list[str]]]:
    """Return ``[(pid, argv_tokens), ...]`` for every visible process.

    Never raises. Returns ``[]`` when the process table cannot be read, so
    callers degrade to their existing "not running" branch rather than to a
    wrong PID.
    """
    try:
        result = subprocess.run(
            ["ps", "-axww", "-o", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
        )
    except Exception:  # swallow-ok: an unreadable process table means "no match"
        return []
    if result.returncode != 0:
        return []

    processes: list[tuple[int, list[str]]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_token, _, command = stripped.partition(" ")
        if not pid_token.isdigit():
            continue
        argv = command.split()
        if not argv:
            continue
        processes.append((int(pid_token), argv))
    return processes


def _is_python_interpreter(argv0: str) -> bool:
    """True when ``argv0`` names a Python interpreter.

    Covers ``python``, ``python3``, ``python3.14`` and the launchd-spawned
    framework binary ``Python`` (capital P) inside
    ``Python.app/Contents/MacOS/`` — the case ``pgrep -fi``'s ``-i`` flag was
    working around.
    """
    return os.path.basename(argv0).lower().startswith("python")


# Interpreter options that consume the FOLLOWING argv token as their value.
# `-c` and `-m` also consume it, but they additionally terminate option parsing,
# so they are handled separately in `_parse_python_invocation`.
_VALUE_OPTIONS = frozenset({"-W", "-X", "--check-hash-based-pycs"})


def _parse_python_invocation(argv: list[str]) -> tuple[str | None, str | None]:
    """Return ``(module, script_path)`` for a ``python ...`` command line.

    Models the CPython CLI grammar — ``python [options] [-c cmd | -m mod |
    script] [args...]`` — rather than scanning the whole argv for a token that
    happens to look right. That distinction is the whole point: a scan of every
    token reports ``python -m ruff check bridge/telegram_bridge.py`` as the
    bridge and ``python -m pytest tests/ -m worker`` as the worker, because the
    path and the ``-m`` pair are present as *program arguments*. Only the
    interpreter's own ``-m`` and its own script position identify what the
    process is actually running.

    Exactly one of the two results is ever non-None:

    - ``python -m worker`` → ``("worker", None)``
    - ``python /abs/path/worker/__main__.py`` → ``(None, "/abs/path/worker/__main__.py")``
    - ``python -c "..."`` → ``(None, None)`` — a ``-c`` payload is source code,
      and everything after it belongs to the program.
    - ``python`` (REPL) → ``(None, None)``
    """
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "-m":
            return (argv[index + 1] if index + 1 < len(argv) else None), None
        if token == "-c":
            return None, None
        if token in _VALUE_OPTIONS:
            index += 2
            continue
        if token == "-":
            return None, None  # stdin
        if token.startswith("-"):
            index += 1  # a bundled short-flag cluster such as `-EsSu`
            continue
        return None, token  # first non-option token is the script path
    return None, None


def _script_matches(script: str | None, script_suffix: str) -> bool:
    """True when ``script`` is a path equal to or ending in ``script_suffix``."""
    if script is None:
        return False
    return script == script_suffix or script.endswith("/" + script_suffix)


def _parent_pid(pid: int) -> int | None:
    """Return the parent PID of ``pid``, or None when it cannot be read."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
        )
    except Exception:  # swallow-ok: an unreadable parent breaks the walk safely
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return int(token) if token.isdigit() else None


def is_own_ancestor(pid: int) -> bool:
    """True when ``pid`` is this process, or any ancestor of this process.

    The guard for restart/signal paths. An ancestor-safe lookup can now hand a
    caller the PID of the very service that spawned it, so any code that would
    then restart or signal that PID has to check first — otherwise an agent
    session running ``/update`` can kill the worker or bridge it is running
    inside, taking itself down mid-operation. ``pgrep`` used to make that
    unreachable by accident; it is now a decision the caller must make.

    Conservative on failure: an unreadable process tree returns False, i.e. no
    suppression, because a spurious True would silently disable a legitimate
    recovery restart. The walk is bounded so a cycle cannot hang it.
    """
    current: int | None = os.getpid()
    for _ in range(64):  # bounded: the real chain is a handful of levels deep
        if current is None or current <= 1:
            return False
        if current == pid:
            return True
        current = _parent_pid(current)
    return False


def find_python_service_pids(
    *,
    module: str | None = None,
    script_suffix: str | None = None,
) -> list[int]:
    """PIDs of running Python processes matching ``module`` or ``script_suffix``.

    A process matches when ``argv[0]`` is a Python interpreter AND at least one
    of the supplied selectors matches its argv. Supplying both selectors is an
    OR — the worker runs as ``python -m worker`` under launchd but as
    ``python .../worker/__main__.py`` when started directly.

    Returns PIDs sorted ascending (callers take the first, matching the
    ordering the previous ``pgrep`` probes relied on). Returns ``[]`` when
    nothing matches or the process table cannot be read; never raises.
    """
    if module is None and script_suffix is None:
        raise ValueError("find_python_service_pids requires module or script_suffix")

    pids: list[int] = []
    for pid, argv in list_processes():
        if not _is_python_interpreter(argv[0]):
            continue
        found_module, found_script = _parse_python_invocation(argv)
        if module is not None and found_module == module:
            pids.append(pid)
            continue
        if script_suffix is not None and _script_matches(found_script, script_suffix):
            pids.append(pid)
    return sorted(pids)
