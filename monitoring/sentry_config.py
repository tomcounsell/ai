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
    a bridge-only concept — and then delegates to :func:`filter_sentry_noise`). The
    worker passes :func:`filter_sentry_noise` directly. That composite drops Popoto
    orphan-index noise (issue #1835) and transient Redis ``MISCONF`` persistence
    noise (issues #2343-#2352, #2372), and pins known per-model/per-loop logger
    clusters to a stable ``fingerprint`` so one root cause can never fan out into
    many Sentry issues again. The worker deliberately does NOT get the bridge-
    hibernation filter — this helper never imports ``bridge.hibernation``.
  * **Test/CI guard.** ``configure_sentry`` returns early under
    ``PYTEST_CURRENT_TEST`` or ``CI`` so a ``SENTRY_DSN``-present test run never
    reports at all (and never mis-tags ``production``).
  * **Dev-vs-prod environment gating (#1834).** When init does proceed,
    :func:`_resolve_environment` decides the ``environment`` tag: an explicit
    ``SENTRY_ENVIRONMENT`` always wins; otherwise a *designated bridge machine*
    (one that owns >=1 project in ``projects.json``) reports as ``production`` and
    every other machine reports as ``development``. This keeps the production
    Sentry project clean of events from dev/misconfigured machines that start a
    real bridge/worker outside pytest. The machine-ownership check resolves
    through :mod:`config.machine` (the lowest shared layer — importing it is the
    correct layer direction, and it carries the #1834 empty-machine guard).
"""

from __future__ import annotations

import logging
import os
import subprocess

from config.machine import get_machine_name, get_machine_project_keys

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

# Redis returns ``MISCONF Redis is configured to save RDB snapshots, but it's
# currently unable to persist to disk`` whenever it cannot fsync its RDB (disk
# full, permissions, background-save failure). This is a *transient, self-
# recovering ops condition*, not an actionable code bug: any command that writes
# raises it until the operator's disk recovers. Both the ``popoto-cleanup``
# sweep (worker) — which calls ``rebuild_indexes()`` once per model — and the
# session watchdog (bridge) re-hit it every loop iteration, so a single episode
# flooded Sentry with 80-160 events *per model* (issues #2343-#2352, #2372). The
# ``MISCONF`` token is a Redis-specific error prefix that appears only for this
# exact persistence-failure class, so matching it is precise. The condition stays
# visible in process logs; we only keep it out of Sentry's error stream.
_TRANSIENT_INFRA_SUBSTRING = "MISCONF"

# Known logger clusters that interpolate a per-model / per-iteration value into
# their message, defeating Sentry's default grouping and fanning one root cause
# out into many issues. We pin each to a stable ``fingerprint`` so every variant
# collapses into a single Sentry issue; the interpolated detail (model name, etc.)
# stays in the event message. ``(substring, fingerprint)`` pairs.
_FINGERPRINT_CLUSTERS = (
    ("[popoto-cleanup] Error processing", ["popoto-cleanup", "error-processing-model"]),
    (
        "[watchdog] Failed to query active sessions",
        ["watchdog", "failed-to-query-active-sessions"],
    ),
)


def _event_message_candidates(event):
    """Return the interpolated + template message strings for ``event``.

    ``LoggingIntegration`` encodes a ``logger.error(...)`` call as a ``logentry``
    object; we check ``logentry.formatted`` (the interpolated string) and
    ``logentry.message`` (the raw template), plus the top-level ``message`` key as
    a fallback for non-``logentry`` event shapes.
    """
    logentry = event.get("logentry") or {}
    return (
        logentry.get("formatted") or "",
        logentry.get("message") or "",
        event.get("message") or "",
    )


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
        if any(_ORPHAN_NOISE_SUBSTRING in text for text in _event_message_candidates(event)):
            logger.debug("Sentry event dropped: Popoto orphan-index noise")
            return None
    except Exception:  # noqa: S110 -- filter must never suppress events
        # Filter crash must never suppress real errors.
        pass
    return event


def drop_transient_infra_noise(event, hint):
    """Sentry ``before_send`` hook that drops transient Redis-persistence noise.

    Redis emits ``MISCONF Redis is configured to save RDB snapshots, but it's
    currently unable to persist to disk`` on every write attempt while it cannot
    fsync its RDB. The ``popoto-cleanup`` sweep and the session watchdog re-hit
    this each loop, so a single ops episode fans out into 80-160 Sentry events per
    model (issues #2343-#2352, #2372). This is an environment/ops condition, not a
    code bug — it self-recovers when the disk does. This filter drops any event
    whose logged message contains :data:`_TRANSIENT_INFRA_SUBSTRING`; the condition
    stays visible in process logs.

    Safety net: any exception in the matching logic passes the event through
    unchanged, so a bug in this filter can never silently suppress a real error.

    Args:
        event: The Sentry event dict about to be sent.
        hint: Sentry's ``before_send`` hint (may be ``None``); unused here.

    Returns:
        ``None`` to drop the event when the MISCONF marker matches, otherwise the
        ``event`` unchanged.
    """
    try:
        if any(_TRANSIENT_INFRA_SUBSTRING in text for text in _event_message_candidates(event)):
            logger.debug("Sentry event dropped: transient Redis MISCONF/persistence noise")
            return None
    except Exception:  # noqa: S110 -- filter must never suppress events
        # Filter crash must never suppress real errors.
        pass
    return event


def normalize_noisy_fingerprints(event, hint):
    """Pin known per-model / per-iteration logger clusters to a stable fingerprint.

    Some error logs interpolate a varying value (a model name, an exception
    string) directly into their message. Sentry's default grouping keys on that
    message, so one root cause fans out into many issues — e.g. the same Redis
    MISCONF episode produced a separate issue for every Popoto model
    (#2343-#2352). For each cluster in :data:`_FINGERPRINT_CLUSTERS`, this sets an
    explicit ``fingerprint`` so all variants collapse into a single Sentry issue.
    The interpolated detail stays in the event message (and in ``extra``/tags if
    the caller added them).

    Safety net: any exception passes the event through unchanged.

    Args:
        event: The Sentry event dict about to be sent.
        hint: Sentry's ``before_send`` hint (may be ``None``); unused here.

    Returns:
        The ``event``, with ``fingerprint`` set if it matched a known cluster.
    """
    try:
        candidates = _event_message_candidates(event)
        for substring, fingerprint in _FINGERPRINT_CLUSTERS:
            if any(substring in text for text in candidates):
                event["fingerprint"] = list(fingerprint)
                break
    except Exception:  # noqa: S110 -- filter must never suppress events
        # Filter crash must never suppress real errors.
        pass
    return event


def filter_sentry_noise(event, hint):
    """Composite ``before_send`` filter shared by the bridge and worker.

    Applies, in order:
      1. :func:`drop_orphan_noise` — drops Popoto orphan-index churn (#1835).
      2. :func:`drop_transient_infra_noise` — drops transient Redis MISCONF /
         persistence-failure noise (#2343-#2352, #2372).
      3. :func:`normalize_noisy_fingerprints` — collapses known per-model /
         per-loop logger clusters into one Sentry issue so a single root cause can
         never fan out again.

    Returns ``None`` if any drop stage matches, otherwise the (possibly
    fingerprint-annotated) ``event``.
    """
    if drop_orphan_noise(event, hint) is None:
        return None
    if drop_transient_infra_noise(event, hint) is None:
        return None
    return normalize_noisy_fingerprints(event, hint)


def _owned_project_key(machine: str) -> str | None:
    """Return the first ``projects.json`` key this ``machine`` owns, else ``None``.

    Thin first-or-``None`` adapter over :func:`config.machine.get_machine_project_keys`,
    which owns the case-insensitive match, the fail-soft ``[]`` on any read
    failure, and the #1834 empty-machine fail-to-development guard (an unresolved
    ComputerName never matches a ``"machine": ""`` entry).
    """
    keys = get_machine_project_keys(machine)
    return keys[0] if keys else None


def _is_designated_bridge_machine() -> bool:
    """``True`` iff this machine owns >=1 project in ``projects.json``.

    Any failure resolves to ``False`` (fail-to-development) — see
    :func:`_owned_project_key`.
    """
    return _owned_project_key(get_machine_name()) is not None


def _resolve_environment(owned_project_key: str | None) -> str:
    """Resolve the Sentry ``environment`` tag for this process (issue #1834).

    Pure function of the ownership result so the caller can compute the
    ``projects.json`` + ``scutil`` inputs exactly once and reuse them for both
    the tag and the init log line.

    Precedence: an explicit ``SENTRY_ENVIRONMENT`` always wins (preserves the
    existing escape hatch and lets a designated machine be forced to e.g.
    ``staging``); otherwise a machine that owns a project
    (``owned_project_key is not None``) reports as ``"production"`` and every
    other machine reports as ``"development"``.
    """
    explicit = os.getenv("SENTRY_ENVIRONMENT")
    if explicit:
        return explicit
    return "production" if owned_project_key is not None else "development"


def configure_sentry(component: str, before_send=None) -> bool:
    """Initialize Sentry for a process ``component`` (e.g. ``"bridge"`` / ``"worker"``).

    Args:
        component: Human-readable process name, used only in log lines.
        before_send: Optional Sentry ``before_send`` hook. The bridge passes its
            hibernation filter (which chains to :func:`filter_sentry_noise`); the
            worker passes :func:`filter_sentry_noise` directly.

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

    # Resolve the ownership inputs exactly once and reuse them for both the
    # environment tag and the observability log line (no double scutil/file read).
    machine = get_machine_name()
    owned_key = _owned_project_key(machine)
    environment = _resolve_environment(owned_key)
    # Observability (issue #1834, critique concern #2): make a wrong environment
    # tag diagnosable from the process log without needing Sentry itself.
    logger.info(
        "[%s] Sentry init: environment=%s (ComputerName=%r, owned_project=%s)",
        component,
        environment,
        machine,
        owned_key or "none",
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
