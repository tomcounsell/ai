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
module reads the process table with ``ps`` and matches the **tokenised**
command line rather than a substring, which also removes the ``ps | grep``
family of false positives: a shell whose argv merely mentions
``telegram_bridge.py`` is never mistaken for the bridge.

Not for use in kill paths
-------------------------

``monitoring/bridge_watchdog.py::kill_stale_processes`` deliberately stays on
``pgrep``. There, ancestor exclusion is load-bearing rather than a bug: an
ancestor-safe lookup feeding ``os.kill(pid, 9)`` would let a bridge-descended
caller SIGKILL its own live ancestor bridge. Do not wire this module into any
signalling path.

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

__all__ = ["find_python_service_pids", "list_processes"]

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


def _runs_module(argv: list[str], module: str) -> bool:
    """True when argv carries an adjacent ``-m <module>`` pair.

    Token equality, not prefix: ``python -m workerfoo`` does not satisfy
    ``module="worker"``.
    """
    for index in range(1, len(argv) - 1):
        if argv[index] == "-m" and argv[index + 1] == module:
            return True
    return False


def _interpreter_argv(argv: list[str]) -> list[str]:
    """Argv truncated at ``-c``, i.e. the part Python itself interprets.

    ``python -c <source> [args...]`` hands everything from ``<source>`` onward
    to the program: the source is not a path and the trailing args are not
    interpreter options. Scanning them is how a ``ps | grep``-style probe
    mistakes ``python -c "print('bridge/telegram_bridge.py')"`` for the bridge.
    Truncating once, here, keeps the module and script matchers consistent —
    neither can be fooled by a ``-c`` payload.
    """
    try:
        return argv[: argv.index("-c")]
    except ValueError:
        return argv


def _runs_script(argv: list[str], script_suffix: str) -> bool:
    """True when some argument is a path equal to or ending in ``script_suffix``.

    ``argv[0]`` is excluded so the interpreter path itself can never match —
    load-bearing, because the framework interpreter path ends in path segments
    a suffix probe could otherwise match.
    """
    for token in argv[1:]:
        if token == script_suffix or token.endswith("/" + script_suffix):
            return True
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
        scan = _interpreter_argv(argv)
        if module is not None and _runs_module(scan, module):
            pids.append(pid)
            continue
        if script_suffix is not None and _runs_script(scan, script_suffix):
            pids.append(pid)
    return sorted(pids)
