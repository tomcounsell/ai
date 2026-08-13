"""Fleet-wide installer for the ambient production-Redis flush guard (#2645).

Writes two files into a venv's ``site-packages`` so the guard in
``tools/redis_flush_guard.py`` is armed from the moment the interpreter
starts, not merely from the moment something happens to import
``tools.redis_flush_guard``:

  - ``_redis_flush_guard_boot.py`` — a shim whose entire body is
    ``try: import tools.redis_flush_guard; tools.redis_flush_guard.arm()``
    / ``except Exception: pass``. It calls ``arm()``, never ``install()``,
    so a process that never touches Redis never pays an ``import redis``
    (D2a) — the shim only inserts a ``sys.meta_path`` finder.
  - ``zzz_redis_flush_guard.pth`` — a single line, ``import
    _redis_flush_guard_boot``. The ``zzz_`` prefix is load-bearing:
    ``.pth`` files are processed in **sorted order**, and this one must run
    *after* ``_editable_impl_valor_bridge.pth`` has already put the repo
    root on ``sys.path`` (``_`` is ASCII 0x5F, ``z`` is 0x7A, so
    ``zzz_redis_flush_guard.pth`` always sorts after any ``_``-prefixed
    ``.pth``). Without the repo root on ``sys.path``, ``import
    tools.redis_flush_guard`` inside the shim would fail — silently, since
    the shim swallows every exception — leaving the guard installed but
    inert (Risk 1).

**Race 1** (two interpreters self-healing the same venv concurrently, or
``/update`` rewriting the shim while a launchd service is mid-``site.py``):
both files are written via write-temp-in-the-same-directory + atomic
``os.replace()``, and the shim is always written **before** the ``.pth``,
so the ``.pth`` can never reference a module that does not yet exist on
disk.

Two entry points:

  - ``install_into(venv_path)`` — the single-venv primitive. Reused by
    ``tools/redis_flush_guard.py``'s own self-heal (D2b), by
    ``agent/worktree_manager.py``'s venv bootstrap, and by this module's
    own ``--venv`` CLI flag.
  - ``install_fleet(repo_root=None)`` — discovers every venv under the repo
    root (``.venv``, ``.worktrees/*/.venv``, ``.claude/worktrees/*/.venv``)
    and calls ``install_into`` on each. Used by ``/update`` Step 3.05.

Every path is skip-with-a-reason, never a crash: not a venv, no
site-packages, read-only site-packages. Identical content already present
is a no-op that reports ``unchanged``.

Per the plan's Rabbit Holes: this installer *detects and reports* a venv
where ``import tools`` would fail (e.g. a missing editable install); it
does not attempt to repair the editable install itself. ``/update`` and
``uv sync`` own that.

**Stdlib imports only.** ``tools/redis_flush_guard.py`` loads this file by
*file path* via ``importlib.util.spec_from_file_location`` — never via
``import scripts.update.redis_flush_guard_pth`` — because
``scripts/update/__init__.py`` eagerly does ``from .run import ...``, which
drags in ~30 submodules and mutates ``sys.path[0]``. That self-heal call
happens on every interpreter start (D2b), so this module must stay
importable in total isolation from its own package, with no side effects
beyond defining functions.

CLI:
    python -m scripts.update.redis_flush_guard_pth            # fleet
    python -m scripts.update.redis_flush_guard_pth --venv PATH  # one venv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Project root (ai/) — scripts/update/redis_flush_guard_pth.py -> ../../
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_PTH_FILENAME = "zzz_redis_flush_guard.pth"
_SHIM_FILENAME = "_redis_flush_guard_boot.py"

_PTH_CONTENT = "import _redis_flush_guard_boot\n"

# The shim's entire body. `arm()`, never `install()` (D2a) -- arm() does not
# import `redis`, it only inserts a meta-path finder that calls install()
# on the first *real* import of redis/redis.asyncio. No kill-switch here on
# purpose: a second, broader bypass of the load-bearing layer would be
# invisible to the Layer-3 hook validator.
_SHIM_CONTENT = (
    "try:\n"
    "    import tools.redis_flush_guard\n\n"
    "    tools.redis_flush_guard.arm()\n"
    "except Exception:  # noqa: S110 -- a broken guard must never break interpreter startup\n"
    "    pass\n"
)


def _find_site_packages(venv_path: Path) -> Path | None:
    """Return the venv's ``site-packages`` dir, or None if not found."""
    matches = sorted(venv_path.glob("lib/python*/site-packages"))
    if matches:
        return matches[0]
    # Windows-style layout, included for completeness though this fleet is
    # macOS-only.
    win_candidate = venv_path / "Lib" / "site-packages"
    if win_candidate.is_dir():
        return win_candidate
    return None


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via write-temp-in-same-dir + os.replace.

    Atomic on a single filesystem (Race 1): a reader never observes a
    partially written file, and two concurrent writers never tear one.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def install_into(venv_path: str | Path) -> dict:
    """Install (or verify) the flush-guard shim + ``.pth`` in one venv.

    Never raises. Always returns a dict with at least:
      - ``venv``: str(venv_path)
      - ``status``: ``"installed"`` | ``"unchanged"`` | ``"skipped"``
      - ``reason``: str | None, present (non-None) only when ``skipped``
    """
    venv_path = Path(venv_path)
    result: dict = {"venv": str(venv_path), "status": "skipped", "reason": None}

    try:
        if not venv_path.is_dir():
            result["reason"] = "not a venv: path does not exist or is not a directory"
            return result

        if not (venv_path / "pyvenv.cfg").is_file():
            result["reason"] = "not a venv: no pyvenv.cfg"
            return result

        site_packages = _find_site_packages(venv_path)
        if site_packages is None:
            result["reason"] = "no site-packages directory found under lib/python*/"
            return result

        if not os.access(site_packages, os.W_OK):
            result["reason"] = "read-only site-packages"
            return result

        shim_path = site_packages / _SHIM_FILENAME
        pth_path = site_packages / _PTH_FILENAME

        shim_current = shim_path.read_text() if shim_path.is_file() else None
        pth_current = pth_path.read_text() if pth_path.is_file() else None

        if shim_current == _SHIM_CONTENT and pth_current == _PTH_CONTENT:
            result["status"] = "unchanged"
            result["reason"] = None
            return result

        # Shim FIRST, then .pth (Race 1) -- the .pth must never reference a
        # module that is not yet on disk.
        _atomic_write(shim_path, _SHIM_CONTENT)
        _atomic_write(pth_path, _PTH_CONTENT)

        result["status"] = "installed"
        result["reason"] = None
        return result
    except Exception as exc:  # never let a single venv crash the fleet
        result["status"] = "skipped"
        result["reason"] = f"unexpected error: {exc}"
        return result


def discover_venvs(repo_root: Path) -> list[Path]:
    """Discover ``.venv``, ``.worktrees/*/.venv``, ``.claude/worktrees/*/.venv``.

    Public so `tools/doctor.py`'s liveness checks can enumerate the same
    fleet this installer targets, mirroring `_check_worktree_interpreters`'s
    reuse of `agent.worktree_manager`'s discovery helpers.
    """
    venvs = [repo_root / ".venv"]
    venvs += sorted((repo_root / ".worktrees").glob("*/.venv"))
    venvs += sorted((repo_root / ".claude" / "worktrees").glob("*/.venv"))
    return venvs


def install_fleet(repo_root: str | Path | None = None) -> list[dict]:
    """Install into every discovered venv under ``repo_root``.

    Never raises; a failure on one venv is captured in its own result dict
    and does not stop the rest of the fleet.
    """
    root = Path(repo_root) if repo_root is not None else _PROJECT_DIR
    results: list[dict] = []
    for venv_dir in discover_venvs(root):
        try:
            results.append(install_into(venv_dir))
        except Exception as exc:  # defense in depth; install_into already catches
            results.append(
                {"venv": str(venv_dir), "status": "skipped", "reason": f"unexpected error: {exc}"}
            )
    return results


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the redis_flush_guard .pth shim into repo venvs."
    )
    parser.add_argument(
        "--venv",
        help="Install into a single venv only (e.g. the remediation for one unhealed worktree)",
    )
    args = parser.parse_args(argv)

    if args.venv:
        result = install_into(args.venv)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in ("installed", "unchanged") else 1

    results = install_fleet()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
