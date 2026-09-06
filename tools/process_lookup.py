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
responsible for making explicitly — including which way the guard should fail
when the process tree cannot be read, which is what that function's
``on_unreadable`` keyword selects.

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

# The single letters from `_VALUE_OPTIONS` that can appear inside a bundled
# short-flag cluster (`-uWignore`, `-uW ignore`). Their value is the rest of the
# cluster, or the next argv token when the cluster ends at the flag.
_CLUSTER_VALUE_FLAGS = frozenset({"W", "X"})


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
    - ``python -mworker`` → ``("worker", None)`` — CPython accepts the value
      attached to the flag, with no separating space.
    - ``python /abs/path/worker/__main__.py`` → ``(None, "/abs/path/worker/__main__.py")``
    - ``python -c "..."`` and ``python -c"..."`` → ``(None, None)`` — a ``-c``
      payload is source code, and everything after it belongs to the program.
    - ``python`` (REPL) → ``(None, None)``

    Bundled short-flag clusters are walked character by character rather than
    skipped whole, because any letter in a cluster can be a terminator or a
    value option: ``python -um worker``, ``python -uEm platform`` and
    ``python -uc print(1)`` are all legal CPython, and skipping the cluster
    would read ``worker`` as a *script path* instead of a module. Within a
    cluster, ``m`` and ``c`` end option parsing exactly as their standalone
    spellings do, and ``W``/``X`` take the rest of the cluster as their value —
    or the next argv token when the cluster ends at the flag. Long options
    (``--``-prefixed) never enter this path, so ``--check-hash-based-pycs``
    stays a value option rather than being read as an attached ``-c`` payload.
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
        if token.startswith("--"):
            index += 1  # a long option taking no value (`--help`, `--version`)
            continue
        if token.startswith("-"):
            # A bundled short-flag cluster: `-u`, `-EsSu`, `-um`, `-uEm`,
            # `-mworker`, `-uc`, `-uWignore`. Walk the body so a terminator or
            # value option in the LAST position is not skipped with the cluster.
            body = token[1:]
            consumes_next_token = False
            for position, flag in enumerate(body):
                rest = body[position + 1 :]
                if flag == "m":
                    if rest:
                        return rest, None  # `-mworker`, `-umworker`
                    # `-um worker`: the module is the next argv token.
                    return (argv[index + 1] if index + 1 < len(argv) else None), None
                if flag == "c":
                    return None, None  # the rest is source code, if anything
                if flag in _CLUSTER_VALUE_FLAGS:
                    consumes_next_token = not rest  # `-uW ignore` vs `-uWignore`
                    break  # either way the cluster ends at this flag
                # Any other letter is a valueless flag; keep walking the cluster.
            index += 2 if consumes_next_token else 1
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


def is_own_ancestor(pid: int, *, on_unreadable: bool = False) -> bool:
    """True when ``pid`` is this process, or any ancestor of this process.

    The guard for restart/signal paths. An ancestor-safe lookup can now hand a
    caller the PID of the very service that spawned it, so any code that would
    then restart or signal that PID has to check first — otherwise an agent
    session running ``/update`` can kill the worker or bridge it is running
    inside, taking itself down mid-operation. ``pgrep`` used to make that
    unreachable by accident; it is now a decision the caller must make.

    ``on_unreadable`` is the answer returned when the walk is **inconclusive** —
    ``ps`` fails or is unreadable partway up the chain, or the bounded walk is
    exhausted without reaching pid 1 (a cycle in the reported tree). It is NOT
    returned for the legitimate terminal case of walking all the way to pid <= 1
    without a match: that is a real ``False``.

    The default polarity fails **open** (inconclusive → "not an ancestor" →
    proceed), and callers that signal must override it, because the two
    directions are dangerous in opposite ways:

    - ``scripts/update/run.py::_self_heal_stale_worker`` keeps the default.
      There a spurious ``True`` would silently disable a legitimate recovery
      restart, and the downside of proceeding is a restart that was already
      the intended action.
    - ``monitoring/worker_watchdog.py::recover`` and
      ``scripts/update/service.py::stop_email`` pass ``on_unreadable=True``.
      Both then run ``os.kill``, where "unreadable → proceed" means signalling
      a PID that may well be the caller's own ancestor — under fork pressure
      (exactly the condition that wedges a worker and triggers ``recover()``)
      ``ps`` is also the thing most likely to fail. Refusing costs little:
      by the time ``recover()`` holds a PID, ``ps`` has already succeeded once
      — that is where the PID came from — so a later transient failure only
      defers the kill to the next watchdog tick.
    """
    current = os.getpid()
    for _ in range(64):  # bounded: the real chain is a handful of levels deep
        if current <= 1:
            return False  # reached init with no match: a conclusive "no"
        if current == pid:
            return True
        parent = _parent_pid(current)
        if parent is None:
            return on_unreadable  # inconclusive: the chain could not be walked
        current = parent
    return on_unreadable  # inconclusive: 64 levels without reaching init = a cycle


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

    Returns PIDs sorted **ascending**, so a caller taking ``pids[0]`` gets the
    lowest matching PID. That is a PID ordering, not the per-shape ordering the
    old ``pgrep`` probes produced (they tried one launch shape first and only
    then fell back to the other), so a caller whose two selectors can match
    different live processes gets a different answer — see
    ``scripts/update/service.py::get_worker_pid``. Returns ``[]`` when nothing
    matches or the process table cannot be read; never raises.
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
