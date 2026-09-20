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

**The router's read is cache-only (#3410).** ``agent/llm/router.py::resolve``
runs on the message hot path, where a ``gh`` shell-out on a miss would blow a
3 s budget (plan spike-4). So the router calls :func:`is_eligible`, which pins
``valor`` ``True`` in code ahead of any cache read, answers every other key
from :func:`peek_open_source` (a pure cache read), and treats a miss as
ineligible for that call while scheduling one background refresh per key
through :func:`_schedule_refresh` (a running loop is required; without one,
the synchronous ``tools/doctor`` path, the miss is the answer and nothing is
scheduled). A process warms the cache for its project list at startup with
:func:`schedule_warm_cache`, which holds the task in :data:`_BACKGROUND_TASKS`
for its whole life and is never awaited ahead of the process's connect step.
The blocking :func:`is_open_source` keeps its ``gh`` shell-out for the
improvement tooling, the refresh and the warm-up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
from collections.abc import Iterable

from bridge.routing import load_config

logger = logging.getLogger(__name__)

#: How long a determinate answer stays good, in seconds.
DEFAULT_TTL_SECONDS = 900

#: The one visibility value that means "any provider may see this".
_PUBLIC = "PUBLIC"

#: The project key pinned eligible in code (Tom, plan answer 4): this repo's
#: own rooms never wait on a cache and never fail closed.
VALOR_PROJECT_KEY = "valor"

#: project_key -> (answer, monotonic expiry). Process-local by design.
_CACHE: dict[str, tuple[bool, float]] = {}

#: Guards ``_REFRESHING``: ``is_eligible`` runs on the loop thread while the
#: refresh's done-callback and the executor thread touch the set too.
_LOCK = threading.Lock()

#: Keys with a background refresh in flight (Race 2: one per key, never a storm).
_REFRESHING: set[str] = set()

#: Strong references to every warm-up task: asyncio holds tasks weakly, so a
#: bare ``create_task`` can be collected mid-run (ruff's select has no RUF006
#: to flag it). ``schedule_warm_cache`` adds and the done-callback discards.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


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
    passed to ``gh`` positionally. ``valor`` is pinned True ahead of the cache.
    """
    if project_key == VALOR_PROJECT_KEY:
        return True
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


def peek_open_source(project_key: str) -> bool | None:
    """The cached answer for ``project_key``, or ``None`` on a miss.

    A pure cache read: never shells out, never schedules, never raises. An
    expired entry is a miss.
    """
    cached = _CACHE.get(project_key)
    if cached is None or cached[1] <= time.monotonic():
        return None
    return cached[0]


def _schedule_refresh(project_key: str) -> bool:
    """Run ``is_open_source`` once in the background for a missed key.

    Returns True when a refresh was scheduled. Needs a running loop (the
    check comes first, so a synchronous caller such as ``tools/doctor`` gets
    False and no thread); holds at most one refresh per key (Race 2) under
    :data:`_LOCK`; the executor's done-callback releases the key whatever
    the outcome, so a failed refresh can be retried on the next miss.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with _LOCK:
        if project_key in _REFRESHING:
            return False
        _REFRESHING.add(project_key)

    def _done(future: asyncio.Future) -> None:
        with _LOCK:
            _REFRESHING.discard(project_key)
        if not future.cancelled() and future.exception() is not None:
            logger.warning(
                "improvement eligibility: refresh for %r failed: %s",
                project_key,
                future.exception(),
            )

    # The executor's work queue holds the job; the loop future is bound to it
    # through the callback chain, so nothing here needs a stronger reference.
    loop.run_in_executor(None, is_open_source, project_key).add_done_callback(_done)
    return True


def is_eligible(project_key: str | None) -> bool:
    """The router's hot-path read: eligible now, from what this process knows.

    ``valor`` is True before any cache read. ``None`` is False. Every other
    key is :func:`peek_open_source`; a miss is False for this call and
    schedules one background refresh (when a loop is running) so a burst's
    later calls see the answer. Fails closed on every uncertainty (§7).
    """
    if project_key == VALOR_PROJECT_KEY:
        return True
    if not project_key:
        return False
    answer = peek_open_source(project_key)
    if answer is None:
        _schedule_refresh(project_key)
        return False
    return answer


async def warm_cache(keys: Iterable[str]) -> None:
    """Resolve every key once in the executor so later hot-path reads hit.

    ``keys`` is a process's project list (a handful of entries from
    ``projects.json``), never a dynamic collection, so one unbounded
    ``gather`` over it is fine. ``return_exceptions=True`` keeps one key's
    failure from hiding the others; the closing INFO line is the operator's
    evidence the warm-up finished (``grep "eligibility warm-up done"``).
    A ``CancelledError`` (process shutdown) propagates untouched.
    """
    unique = list(dict.fromkeys(keys))
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *(loop.run_in_executor(None, is_open_source, key) for key in unique),
        return_exceptions=True,
    )
    public = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if isinstance(r, BaseException))
    for key, result in zip(unique, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("improvement eligibility: warm-up for %r failed: %s", key, result)
    logger.info("eligibility warm-up done keys=%d public=%d failed=%d", len(unique), public, failed)


def schedule_warm_cache(keys: Iterable[str]) -> asyncio.Task:
    """Start :func:`warm_cache` on the running loop and hold the task.

    The one way a process starts the warm-up: the task lives in
    :data:`_BACKGROUND_TASKS` until its done-callback discards it, so it
    cannot be garbage-collected mid-run. Callers never await it ahead of
    their connect step; ``is_open_source`` shells ``gh repo view`` with a
    10 s timeout per key. Raises ``RuntimeError`` outside a running loop.
    """
    task = asyncio.get_running_loop().create_task(warm_cache(list(keys)))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task
