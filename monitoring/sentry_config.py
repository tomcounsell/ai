"""Shared Sentry initialization for the bridge and worker processes (#1877 defect #3).

The bridge historically owned the only ``sentry_sdk.init()`` call. Session
execution happens in the worker, so worker-side exceptions (SDK/tool/lifecycle
crashes) were invisible to Sentry. This module extracts the bridge's init block
into a single ``configure_sentry(component, before_send=None)`` helper that both
processes call at startup.

Design notes:
  * **DSN-gated, verbatim.** If ``SENTRY_DSN`` is unset the helper returns without
    initializing — the same gating the bridge already had. ``release`` (git HEAD),
    ``traces_sample_rate``, and ``environment`` are preserved unchanged.
  * **``before_send`` is a parameter, not hardcoded.** The bridge passes its
    ``_sentry_before_send`` (which drops events while the *bridge* is hibernating —
    a bridge-only concept — and then delegates to :func:`drop_orphan_noise`). The
    worker passes :func:`drop_orphan_noise` directly (issue #1835) so the Popoto
    orphan-index diagnostic it emits in a tight poll loop never floods Sentry. The
    worker deliberately does NOT get the bridge-hibernation filter — this helper
    never imports ``bridge.hibernation``.
  * **Test/CI guard.** ``configure_sentry`` returns early under
    ``PYTEST_CURRENT_TEST`` or ``CI`` so a ``SENTRY_DSN``-present test run never
    reports at all (and never mis-tags ``production``).
  * **Dev-vs-prod environment gating (#1834).** When init does proceed,
    :func:`_resolve_environment` decides the ``environment`` tag: an explicit
    ``SENTRY_ENVIRONMENT`` always wins; otherwise a *designated bridge machine*
    (one that owns >=1 project in ``projects.json``) reports as ``production`` and
    every other machine reports as ``development``. This keeps the production
    Sentry project clean of events from dev/misconfigured machines that start a
    real bridge/worker outside pytest. The machine-ownership check is a
    self-contained copy of the ``projects.json`` + ``scutil`` predicate (it does
    NOT import the ui-layer machine helper — that would invert the layer direction).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Popoto's `Query` logger emits this exact diagnostic at ``error`` level whenever a
# model query hits an orphaned index entry (a Redis SET member pointing at an
# expired/deleted hash). It is captured into Sentry by the default
# ``LoggingIntegration`` and — because the worker polls ``AgentSession.query.all()``
# in a tight loop — floods Sentry with tens of thousands of benign-transient events
# (see issue #1835, Sentry ``VALOR-S``). The orphan churn itself is benign: the
# ``if redis_hash`` guard in ``get_many_objects`` already silently skips ghosts, so
# no stale data is ever returned, and existing cleanup infrastructure
# (``agent-session-cleanup`` reflection, ``ghost_reconcile.py``, worker-startup
# ``clean_indexes()``) keeps the orphan count bounded. This substring is the match
# target for ``drop_orphan_noise``.
_ORPHAN_NOISE_SUBSTRING = "one or more redis keys points to missing objects"


def drop_orphan_noise(event, hint):
    """Sentry ``before_send`` hook that drops Popoto orphan-index diagnostics.

    Popoto logs ``"one or more redis keys points to missing objects. Debug with
    Model.query.keys(clean=True)"`` at ``error`` level on every query that touches a
    transient orphan index entry. These are benign (no stale data is returned) but
    flood Sentry, drowning out real signal (issue #1835). This filter drops any event
    whose logged message contains :data:`_ORPHAN_NOISE_SUBSTRING`.

    ``LoggingIntegration`` encodes a ``logger.error(...)`` call as a ``logentry``
    object, so we check ``logentry.formatted`` (the interpolated string) and
    ``logentry.message`` (the raw template), plus the top-level ``message`` key as a
    fallback for non-``logentry`` event shapes.

    Safety net: any exception in the matching logic passes the event through
    unchanged, so a bug in this filter can never silently suppress a real error.

    Args:
        event: The Sentry event dict about to be sent.
        hint: Sentry's ``before_send`` hint (may be ``None``); unused here.

    Returns:
        ``None`` to drop the event when the orphan substring matches, otherwise the
        ``event`` unchanged.
    """
    try:
        logentry = event.get("logentry") or {}
        candidates = (
            logentry.get("formatted") or "",
            logentry.get("message") or "",
            event.get("message") or "",
        )
        if any(_ORPHAN_NOISE_SUBSTRING in text for text in candidates):
            logger.debug("Sentry event dropped: Popoto orphan-index noise")
            return None
    except Exception:
        # Filter crash must never suppress real errors.
        pass
    return event


def _get_machine_name() -> str:
    """Return the local ComputerName via ``scutil``; ``""`` on any failure.

    Self-contained copy of the ui-layer ``get_machine_name`` — ``monitoring``
    must not import that layer (wrong direction). ``""`` on failure is the
    fail-to-development signal consumed by :func:`_owned_project_key`.
    """
    try:
        result = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return ""


def _owned_project_key(machine: str) -> str | None:
    """Return the first ``projects.json`` key whose ``machine`` field matches
    ``machine`` (case-insensitive), or ``None`` if this host owns no project.

    Mirrors the ui-layer ``get_machine_project_keys`` and the predicate
    ``bridge.config_validation.validate_projects_config`` enforces
    (``proj_cfg.get("machine")``), kept as a self-contained copy to avoid the
    ``monitoring``->``ui`` import inversion. Any failure (missing/unreadable
    file, malformed JSON) returns ``None`` — fail-to-development.
    """
    # Fail-to-development guard (issue #1834, critique concern #1): an unresolved
    # ComputerName must never match a project. Without this, an empty machine
    # name would match any projects.json entry that has an empty `machine` field
    # (`"" == ""`), mis-tagging a dev/misconfigured host as `production` — the
    # exact bug this gate exists to eliminate. A real production bridge machine
    # always resolves a non-empty ComputerName and a readable projects.json (it
    # cannot route messages otherwise), so only dev/misconfigured hosts hit this.
    if not machine:
        return None
    config_path = Path("~/Desktop/Valor/projects.json").expanduser()
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text())
    except Exception:
        return None
    machine_lower = machine.lower()
    for project_key, project in config.get("projects", {}).items():
        if project.get("machine", "").lower() == machine_lower:
            return project_key
    return None


def _is_designated_bridge_machine() -> bool:
    """``True`` iff this machine owns >=1 project in ``projects.json``.

    Any failure resolves to ``False`` (fail-to-development) — see
    :func:`_owned_project_key`.
    """
    return _owned_project_key(_get_machine_name()) is not None


def _resolve_environment() -> str:
    """Resolve the Sentry ``environment`` tag for this process (issue #1834).

    Precedence: an explicit ``SENTRY_ENVIRONMENT`` always wins (preserves the
    existing escape hatch and lets a designated machine be forced to e.g.
    ``staging``); otherwise a designated bridge machine reports as
    ``"production"`` and every other machine reports as ``"development"``.
    """
    explicit = os.getenv("SENTRY_ENVIRONMENT")
    if explicit:
        return explicit
    return "production" if _is_designated_bridge_machine() else "development"


def configure_sentry(component: str, before_send=None) -> bool:
    """Initialize Sentry for a process ``component`` (e.g. ``"bridge"`` / ``"worker"``).

    Args:
        component: Human-readable process name, used only in log lines.
        before_send: Optional Sentry ``before_send`` hook. The bridge passes its
            hibernation filter (which chains to :func:`drop_orphan_noise`); the
            worker passes :func:`drop_orphan_noise` directly (issue #1835).

    Returns:
        ``True`` if ``sentry_sdk.init`` was invoked, ``False`` otherwise (no DSN,
        or the pytest/CI guard tripped).
    """
    # Guard: never initialize (and never mis-tag `production`) under a test run
    # or CI. Runs upstream of environment resolution so `_resolve_environment`
    # never fires under a normal test (issue #1834).
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI"):
        logger.debug("[%s] Sentry init skipped (pytest/CI guard)", component)
        return False

    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk  # noqa: PLC0415

    environment = _resolve_environment()
    # Observability (issue #1834, critique concern #2): make a wrong environment
    # tag diagnosable from the process log without needing Sentry itself.
    machine = _get_machine_name()
    logger.info(
        "[%s] Sentry init: environment=%s (ComputerName=%r, owned_project=%s)",
        component,
        environment,
        machine,
        _owned_project_key(machine) or "none",
    )
    sentry_sdk.init(
        dsn=dsn,
        release=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        traces_sample_rate=0.1,
        environment=environment,
        before_send=before_send,
    )
    logger.info("[%s] Sentry initialized", component)
    return True
