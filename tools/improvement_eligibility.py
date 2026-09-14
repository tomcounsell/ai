"""Charter §7: which projects may be routed to any provider.

Charter §7 draws one line. Work on an open-source codebase carries no model or
provider restriction. Regular client work, and the private context that comes
with it, stays on the Claude and Codex subscriptions.

``is_open_source`` is the guard on that line, and it **fails closed to client**
on every uncertainty. The asymmetry is the whole design: returning True by
mistake routes private client context to a foreign provider, while returning
False by mistake costs one experiment. A missing project, a missing ``github``
block, a missing ``org`` or ``repo``, a non-zero ``gh`` exit, a timeout, an
absent binary, unparseable JSON, and an absent ``visibility`` key all return
False, and each is tested separately.

**The repository is passed positionally.** ``GH_REPO`` is set process-wide by
``agent/sdk_client.py`` and ``gh`` reads it before cwd, so a bare
``gh repo view --json visibility`` would answer about whatever ``GH_REPO``
names, exit 0, and look entirely healthy. The positional argument overrides the
environment. The correct and the incorrect version differ by one argument, so
only a test tells them apart.

**The cache is process-local, not Redis.** A repository's visibility changes on
a scale of months, and a durable cache would cost either a Popoto model (with
its migration, schema-gate entry, and TTL decision) or a key in the improvement
control namespace, which does not exist yet and is not this module's to create.
A module-level dict with a monotonic expiry is the cheapest correct thing, and
a later lane can promote it if cross-process sharing is ever shown to matter.

Only determinate answers are cached. A ``gh`` outage returns False for that
call without pinning False for the rest of the window, so a transient failure
costs one experiment rather than fifteen minutes of them.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time

from bridge.routing import load_config

logger = logging.getLogger(__name__)

#: How long a determinate answer stays good, in seconds.
DEFAULT_TTL_SECONDS = 900

#: The one visibility value that means "any provider may see this".
_PUBLIC = "PUBLIC"

#: project_key -> (answer, monotonic expiry). Process-local by design.
_CACHE: dict[str, tuple[bool, float]] = {}


def _clear_cache() -> None:
    """Drop every cached answer. For tests and for a forced re-resolve."""
    _CACHE.clear()


def _resolve_repository(project_key: str) -> str | None:
    """``org/repo`` for a project, or None when it cannot be resolved."""
    try:
        projects = load_config().get("projects", {})
    except Exception as e:
        logger.warning("improvement eligibility: project config unreadable: %s", e)
        return None

    project = projects.get(project_key)
    if not isinstance(project, dict):
        return None

    github = project.get("github")
    if not isinstance(github, dict):
        return None

    org = github.get("org")
    repo = github.get("repo")
    if not org or not repo:
        return None
    return f"{org}/{repo}"


def is_open_source(project_key: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """True only when this project's repository is provably public.

    Every other outcome is False, including every failure. See the module
    docstring for why the asymmetry is deliberate and why the repository is
    passed to ``gh`` positionally.
    """
    cached = _CACHE.get(project_key)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]

    repository = _resolve_repository(project_key)
    if repository is None:
        logger.debug(
            "improvement eligibility: %r has no resolvable repository; treating as client",
            project_key,
        )
        return False

    try:
        completed = subprocess.run(
            ["gh", "repo", "view", repository, "--json", "visibility"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        # OSError covers a missing `gh` binary (FileNotFoundError is one of its
        # subclasses), so it does not need naming separately.
        logger.warning("improvement eligibility: gh failed for %s: %s", repository, e)
        return False

    if completed.returncode != 0 or not (completed.stdout or "").strip():
        logger.warning(
            "improvement eligibility: gh exited %s for %s; treating as client",
            completed.returncode,
            repository,
        )
        return False

    try:
        visibility = json.loads(completed.stdout)["visibility"]
    except (ValueError, TypeError, KeyError) as e:
        logger.warning("improvement eligibility: gh output unusable for %s: %s", repository, e)
        return False

    answer = str(visibility).strip().upper() == _PUBLIC
    _CACHE[project_key] = (answer, time.monotonic() + ttl_seconds)
    return answer
