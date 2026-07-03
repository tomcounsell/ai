"""Canonical project_key resolver for Memory write paths.

This module provides a single ``resolve_project_key()`` function that every
Memory writer callsite uses.  Having one implementation means fixes and new
resolution hints (new projects.json fields, additional env vars) propagate to
all writers automatically.

Priority chain
--------------
1. ``project_key`` kwarg — explicit override; return immediately.
2. ``env`` mapping lookup for ``"VALOR_PROJECT_KEY"`` — machine-scoped env var.
3. ``projects.json`` ``working_directory`` prefix match against ``cwd``.
4. ``None`` — unresolvable; the caller *must* skip the ``Memory.safe_save`` call.

Intentional omissions
---------------------
* **No ``DEFAULT_PROJECT_KEY`` fallback** — ``"default"`` means "misconfigured",
  not "unknown but safe".  Writers that receive ``None`` should skip the write and
  log a ``WARNING`` so the gap is visible in production logs.
* **No ``Path(cwd).name`` (directory-basename) fallback** — basename produces
  keys like ``"ai"`` that are not declared projects.  The previous
  ``memory_bridge._get_project_key`` included this step; the unified helper drops
  it to keep the resolver honest.

Read paths (recall, search, stats) are **not** in scope for this module.  They
may continue to use ``DEFAULT_PROJECT_KEY`` as a non-None default because a bad
key on a read produces an empty result set, not a corrupted write.

Circular-import safety
----------------------
This module imports only stdlib (``json``, ``os``, ``pathlib``) and
``config.memory_defaults``.  It never imports from ``agent/``, ``bridge/``,
``models/``, or ``tools/``.  Callers from all of those packages can safely
import from here without creating a cycle.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy-loaded cache: list of (expanded_working_directory, project_key) pairs
# sorted by path-length descending so the most-specific match wins.
_projects_cache: list[tuple[str, str]] | None = None
_projects_cache_path: str | None = None


def _load_projects(projects_path: Path | None = None) -> list[tuple[str, str]]:
    """Load and cache ``(working_directory, project_key)`` pairs from projects.json.

    Returns an empty list if the file is missing or unreadable — callers fall
    through to the ``None`` return rather than raising.
    """
    global _projects_cache, _projects_cache_path

    if projects_path is not None:
        resolved = str(projects_path)
    else:
        try:
            from config.settings import vault

            resolved = str(vault.projects_path)
        except Exception:
            resolved = str(Path.home() / "Desktop" / "Valor" / "projects.json")

    # Cache hit (same path)
    if _projects_cache is not None and _projects_cache_path == resolved:
        return _projects_cache

    pairs: list[tuple[str, str]] = []
    try:
        p = Path(resolved)
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            home = str(Path.home())
            for key, proj in data.get("projects", {}).items():
                wd = proj.get("working_directory", "")
                if wd:
                    wd = wd.replace("~", home)
                    wd = os.path.normpath(wd)
                    pairs.append((wd, key))
            # Longest match first (most-specific project wins)
            pairs.sort(key=lambda x: len(x[0]), reverse=True)
    except Exception as exc:
        logger.debug("project_key_resolver: could not load %s: %s", resolved, exc)

    _projects_cache = pairs
    _projects_cache_path = resolved
    return pairs


def resolve_project_key(
    cwd: str | None = None,
    env: dict | None = None,
    projects_path: Path | None = None,
    project_key: str | None = None,
) -> str | None:
    """Resolve a project_key for a Memory write, or return ``None`` if unresolvable.

    Args:
        cwd: Working directory to match against ``projects.json``
            ``working_directory`` entries.  Pass ``None`` when no filesystem
            context is available (e.g., SDK PostToolUse hooks without cwd).
        env: Environment mapping to look up ``VALOR_PROJECT_KEY``.  Defaults to
            ``os.environ`` when ``None``.
        projects_path: Override path to ``projects.json``.  Defaults to
            ``vault.projects_path`` (configurable via ``VALOR_VAULT_DIR``;
            base default ``~/Desktop/Valor/projects.json``).
        project_key: Explicit override.  If non-empty, returned immediately
            without consulting ``env`` or ``projects.json``.

    Returns:
        A non-empty project key string, or ``None`` if resolution failed.
        Writers **must** skip ``Memory.safe_save`` when this returns ``None``
        and log a ``WARNING`` so the skip is observable in production.

    Resolution priority:
        1. ``project_key`` kwarg (non-empty string)
        2. ``VALOR_PROJECT_KEY`` in ``env`` (or ``os.environ``)
        3. ``projects.json`` ``working_directory`` prefix match against ``cwd``
        4. ``None``
    """
    # 1. Explicit override
    if project_key:
        return project_key

    # 2. Environment variable
    mapping = env if env is not None else os.environ
    env_key = mapping.get("VALOR_PROJECT_KEY", "").strip()
    if env_key:
        return env_key

    # 3. projects.json cwd-match
    if cwd:
        cwd_normalized = os.path.normpath(cwd)
        pairs = _load_projects(projects_path)
        for wd, key in pairs:
            if cwd_normalized.startswith(wd):
                return key

    # 4. Unresolvable
    return None
