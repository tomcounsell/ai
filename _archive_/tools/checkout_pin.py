"""Pin a script's imports to the checkout the script lives in (#3141).

``python scripts/<tool>.py`` puts the SCRIPT'S DIRECTORY at ``sys.path[0]``,
nothing more. Every first-party import (``agent``, ``tools``, ``bridge``) then
resolves through the venv's editable-install ``.pth`` entry, which names the
checkout the venv was synced from. Run a worktree's script through the primary
checkout's venv (the ``c`` launcher activates ``~/src/ai/.venv``, so a bare
``python`` inside a worktree IS that venv) and the script operates on
``main``'s code while appearing to run the worktree's. The #3069 lane hit this:
``capture_persona_baseline.py`` reported the regenerated baseline "unchanged"
because it composed the prompt from the wrong checkout.

A script-side ``sys.path.insert(0, REPO_ROOT)`` is not enough. The flush-guard
boot shim (``zzz_redis_flush_guard.pth``) imports ``tools`` during ``site``
processing, before the script's first line runs, so ``sys.modules["tools"]``
is already the primary checkout's package and every later ``tools.*`` import
resolves inside it regardless of ``sys.path``.

This module is that pin, applied at the same moment and one step earlier.
``scripts/update/redis_flush_guard_pth.py`` copies its source into every repo
venv's ``site-packages`` as ``_valor_checkout_pin.py`` next to a one-line
``_valor_checkout_pin.pth`` (``import _valor_checkout_pin;
_valor_checkout_pin.pin()``). The ``_v`` prefix sorts before
``zzz_redis_flush_guard.pth``, so the invoking checkout is at the front of
``sys.path`` before anything imports ``tools``. The mirror is a byte copy of
this file; ``/update`` rewrites it whenever this file changes.

What ``pin()`` does, per interpreter start, in order:

1. Read ``sys.argv[0]``. ``-c``, ``-m``, ``-`` (stdin) and an empty argv leave
   immediately: those forms already put the cwd at ``sys.path[0]`` and need no
   help. This is the whole cost for every service and ``python -m`` CLI.
2. Resolve the script's real path (symlinks followed) and walk up to the
   nearest directory holding ``.git`` (a directory in the primary checkout, a
   file in a linked worktree). That directory must carry a ``pyproject.toml``
   declaring THIS project; any other repository, or no repository, leaves
   ``sys.path`` alone.
3. When that checkout root is already on ``sys.path`` (the script lives in
   the venv's own checkout, or a caller pinned ``PYTHONPATH``), do nothing.
   Otherwise insert it at index 0.

The script's own checkout is the strongest available statement of intent, so
it wins over an ambient ``PYTHONPATH`` naming a different checkout. Console
scripts (``.venv/bin/valor-*``) resolve to the checkout that owns the venv, so
they keep importing the code they were installed from.

Stdlib only, no logging, never raises: this runs inside ``site`` for every
interpreter in the fleet, and a broken pin must degrade to today's behavior
rather than break interpreter startup.
"""

from __future__ import annotations

import os
import sys
import tomllib

PROJECT_NAME = "valor-bridge"

_NO_SCRIPT_ARGV0 = frozenset({"", "-c", "-m", "-"})


def declares_project(checkout_root: str) -> bool:
    """True iff ``checkout_root/pyproject.toml`` names :data:`PROJECT_NAME`."""
    try:
        with open(os.path.join(checkout_root, "pyproject.toml"), "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == PROJECT_NAME


def checkout_root_of(path: str) -> str | None:
    """Nearest ancestor of ``path`` that is a git checkout of this project.

    Walks up from the real path of ``path`` (its directory when it is a file)
    to the first directory containing ``.git`` (file or directory), then
    accepts it only when :func:`declares_project` holds. The walk stops at
    that first repository: a foreign repo nested inside a checkout of this
    project is still a foreign repo.
    """
    current = os.path.realpath(path)
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current if declares_project(current) else None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def pin(argv: list[str] | None = None, path: list[str] | None = None) -> str | None:
    """Put the invoking script's checkout root at ``path[0]`` when it is absent.

    ``argv`` and ``path`` default to ``sys.argv`` and ``sys.path``; both are
    parameters so the decision is testable without touching the interpreter.
    Returns the root inserted, or ``None`` when nothing changed.
    """
    argv = sys.argv if argv is None else argv
    path = sys.path if path is None else path
    try:
        argv0 = argv[0] if argv else ""
        if argv0 in _NO_SCRIPT_ARGV0 or not os.path.exists(argv0):
            return None
        root = checkout_root_of(argv0)
        if root is None:
            return None
        if any(entry and os.path.realpath(entry) == root for entry in path):
            return None
        path.insert(0, root)
        return root
    except Exception:
        return None
