"""
venv-health check (issue #2050): verify the *shared* project `.venv` still has
its dev extras after a lane may have run destructive dependency operations
inside a worktree.

Since issue #2052, SDLC lanes under `.worktrees/{slug}/` get their own eagerly
provisioned `.venv` (see `agent/worktree_manager.provision_worktree_venv`);
the shared repo-root `.venv` still backs the main checkout and any
unprovisioned worktree (e.g. harness-created `.claude/worktrees/{agent}/`
checkouts before their bootstrap). If a `uv sync` ever slips past the
`validate_no_uv_sync_in_worktree.py` PreToolUse guard from one of those
(e.g. via an exotic shell chain the guard's `cd`-prefix parsing misses),
it silently drops every package not in the worktree's lockfile from that
shared environment. This module is the backstop: a cheap presence probe run
at lane exit that turns that kind of corruption into a loud warning instead
of a silent `ModuleNotFoundError` surfacing in some unrelated later command.

Checked extras:
- `pytest`, `xdist` (pytest-xdist) -- module presence via `importlib.util.find_spec`,
  which does not execute the module (cheap, not version-fragile).
- `ruff` -- checked as a **file's existence** (`<venv>/bin/ruff`), not an
  `import ruff`. `ruff`'s Python package layout is version-fragile and not a
  reliable importable module across releases; the CLI binary the whole repo
  actually invokes (`python -m ruff` and the `ruff` binary) is the thing that
  matters, and its file presence in the venv's bin dir is stable to check both
  in-process and from a subprocess.

Usage:
  python -m tools.venv_health          # prints status, exits 1 if anything missing
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REQUIRED_MODULES = ("pytest", "xdist")
_REQUIRED_BINARIES = ("ruff",)


def _venv_bin_dir() -> Path:
    """The `bin/` directory of the venv backing the running interpreter.

    Deliberately does NOT call `.resolve()` on `sys.executable` -- venvs
    commonly symlink `bin/python` to a system/homebrew interpreter, and fully
    resolving that symlink would land outside the venv entirely (e.g.
    `/opt/homebrew/opt/python@3.14/bin/python3.14` instead of
    `<repo>/.venv/bin`). `sys.executable` itself is already the absolute path
    Python was invoked as, so its immediate parent is the venv's bin dir.
    """
    return Path(sys.executable).parent


def check_modules() -> list[str]:
    """Return the subset of `_REQUIRED_MODULES` that are not importable in
    the current environment. Checks each module independently -- one missing
    module does not short-circuit the check for the rest.
    """
    missing = []
    for mod in _REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(mod)
    return missing


def check_binaries() -> list[str]:
    """Return the subset of `_REQUIRED_BINARIES` whose executable is not
    present in the venv's bin directory.
    """
    bin_dir = _venv_bin_dir()
    return [name for name in _REQUIRED_BINARIES if not (bin_dir / name).exists()]


def check_health() -> list[str]:
    """Return the combined list of missing modules and missing binaries.
    Empty list means the environment is healthy.
    """
    return check_modules() + check_binaries()


def main() -> int:
    missing = check_health()
    if missing:
        print(
            f"venv-health: MISSING extras in {sys.exec_prefix}: {', '.join(missing)}. "
            "The shared .venv may have been stripped by a `uv sync` from a "
            "worktree -- see docs/features/uv-sync-worktree-guard.md.",
            file=sys.stderr,
        )
        return 1
    print(f"venv-health: OK ({', '.join(_REQUIRED_MODULES + _REQUIRED_BINARIES)} all present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
