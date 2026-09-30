"""Resolve the identity of the ``claude`` process supervising a local SDLC run.

Issue #2714. The detached lease heartbeat
(``tools/sdlc_lease_heartbeat.py``) renews a per-issue lease on behalf of a
``/do-sdlc`` supervisor it cannot see: ``start_new_session=True`` detaches it
from its spawner immediately, and its spawner -- the ephemeral ``sdlc-tool
session-ensure`` CLI -- exits within seconds anyway. The only way the heartbeat
can ever tell that its supervisor died is to be handed that supervisor's
identity *explicitly*, at mint time, by a process that is still inside the
supervisor's own process tree. That handoff is what this module produces.

An identity is the pair ``(pid, create_time)``. The ``create_time`` half is
not optional: a bare pid is recyclable, and a recycled pid would read as a
live supervisor forever (Risk 5). Matching ``create_time`` within ``1e-3`` is
the same guard ``models/session_lifecycle.py::_lock_owner_is_live`` uses, and
it fails toward *detecting* death rather than away from it.

Three tiers, in order:

1. ``CLAUDE_PID`` -- Claude Code exports it into every Bash tool call's
   environment, and it names the TOP-LEVEL ``claude`` process even when the
   call originates from a subagent. That is exactly the right supervisor
   identity for ``/do-sdlc``, whose supervisor loop and every stage subagent
   live inside that one process. Accepted only when psutil can actually
   resolve the pid, so a stale or garbage value degrades rather than lies.
2. **Ancestry walk** -- ``psutil.Process().parents()``, taking the first
   ancestor whose executable basename is ``claude`` (or a ``node`` process
   whose cmdline mentions ``claude``, for the bundled-CLI shape). Keeps the
   feature working if the undocumented env var ever disappears.
3. ``(None, None)`` -- unresolved. The caller spawns the heartbeat without an
   identity and the heartbeat falls back to its shortened unsupervised
   lifetime ceiling.

**The parent pid is never consulted.** A heartbeat's literal parent exits
seconds after spawn, so a healthy heartbeat is reparented to pid 1 almost
immediately; treating that as a death signal would drop a live run's lease on
tick one. This module resolves identity only from the environment and from an
ancestry walk performed while the supervisor is still an ancestor.

Best-effort throughout: every tier is wrapped, and any failure degrades to the
next tier or to ``(None, None)``. Nothing here ever raises.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Identity came from the ``CLAUDE_PID`` environment variable.
SOURCE_ENV = "env"
#: Identity came from the psutil ancestry walk.
SOURCE_ANCESTRY = "ancestry"
#: No supervisor could be identified.
SOURCE_UNRESOLVED = "unresolved"

# How far up the ancestor chain to look for a `claude` process. A Claude Code
# Bash tool call sits 1-3 hops below its `claude` process; the cap only bounds
# a pathological chain.
# GRAIN OF SALT: 12 is PROVISIONAL/TUNABLE -- generously above the observed
# depth, small enough that the walk stays cheap.
_MAX_ANCESTOR_DEPTH = 12


def _self_process():
    """This process as a ``psutil.Process``. Module seam so tests can patch it."""
    import psutil

    return psutil.Process()


def _create_time_for_pid(pid: int) -> float | None:
    """``create_time()`` for a live pid, or ``None`` when it cannot be resolved."""
    from agent.session_health import _psutil_process_for_pid

    proc = _psutil_process_for_pid(pid)
    if proc is None:
        return None
    return float(proc.create_time())


def _executable_basename(proc) -> str:
    """Basename of a process's executable, preferring ``exe()`` over ``name()``.

    ``exe()`` is the precise answer but raises ``AccessDenied`` for processes
    owned by another user; ``name()`` is always readable and is what matches
    for the bare-``claude`` shape. Returns ``""`` when neither is available.
    """
    for getter in ("exe", "name"):
        try:
            value = getattr(proc, getter)()
        except Exception:
            continue
        if value:
            return os.path.basename(str(value).strip())
    return ""


def _is_claude_process(proc) -> bool:
    """True for a ``claude`` executable, or a ``node`` running the bundled CLI.

    Deliberately narrow. The ``node`` arm requires ``claude`` to appear in the
    cmdline, so an unrelated node process in the ancestry never matches.
    """
    base = _executable_basename(proc)
    if base == "claude":
        return True
    if base.startswith("node"):
        try:
            cmdline = proc.cmdline() or []
        except Exception:
            return False
        return any("claude" in str(part) for part in cmdline)
    return False


def _from_env() -> tuple[int | None, float | None]:
    """Tier 1: ``CLAUDE_PID``, accepted only when psutil resolves the pid."""
    raw = os.environ.get("CLAUDE_PID", "").strip()
    if not raw:
        return (None, None)
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return (None, None)
    if pid <= 0:
        return (None, None)
    create_time = _create_time_for_pid(pid)
    if create_time is None:
        return (None, None)
    return (pid, create_time)


def _from_ancestry() -> tuple[int | None, float | None]:
    """Tier 2: the nearest ``claude`` ancestor of this process."""
    for proc in (_self_process().parents() or [])[:_MAX_ANCESTOR_DEPTH]:
        if _is_claude_process(proc):
            return (int(proc.pid), float(proc.create_time()))
    return (None, None)


def resolve_supervisor_identity_detailed() -> tuple[str, int | None, float | None]:
    """``(source, pid, create_time)`` for the supervising ``claude`` process.

    ``source`` is one of :data:`SOURCE_ENV`, :data:`SOURCE_ANCESTRY`, or
    :data:`SOURCE_UNRESOLVED`, and is carried into the heartbeat's startup log
    line so a silent loss of ``CLAUDE_PID`` (an undocumented harness variable)
    shows up as a visible tier downgrade rather than as an unexplained drop to
    the shortened unsupervised lifetime.

    Never raises: any failure in any tier degrades to the next one.
    """
    for source, tier in ((SOURCE_ENV, _from_env), (SOURCE_ANCESTRY, _from_ancestry)):
        try:
            pid, create_time = tier()
        except Exception as e:  # noqa: BLE001 - best-effort; degrade to the next tier
            logger.debug(
                "sdlc_supervisor_identity: %s tier failed (%s: %s) -- degrading",
                source,
                type(e).__name__,
                e,
            )
            continue
        if pid is not None and create_time is not None:
            return (source, pid, create_time)
    return (SOURCE_UNRESOLVED, None, None)


def resolve_supervisor_identity() -> tuple[int | None, float | None]:
    """``(pid, create_time)`` for the supervising ``claude`` process, or ``(None, None)``.

    Source-agnostic form of :func:`resolve_supervisor_identity_detailed`.
    """
    _source, pid, create_time = resolve_supervisor_identity_detailed()
    return (pid, create_time)
