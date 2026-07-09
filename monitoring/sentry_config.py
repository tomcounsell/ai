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
  * **Minimal test/CI guard only.** ``configure_sentry`` returns early under
    ``PYTEST_CURRENT_TEST`` or ``CI`` so a ``SENTRY_DSN``-present test run never
    mis-tags ``production``. It deliberately does NOT add a machine/platform gate
    and does NOT own the richer dev-vs-prod environment gating (that is #1834's
    scope, layered on top of this helper later).
"""

from __future__ import annotations

import logging
import os
import subprocess

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
    # Minimal guard: never initialize (and never mis-tag `production`) under a
    # test run or CI. This is intentionally the ONLY environment gate here —
    # #1834's dev-vs-prod gating layers on top later.
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI"):
        logger.debug("[%s] Sentry init skipped (pytest/CI guard)", component)
        return False

    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk  # noqa: PLC0415

    sentry_sdk.init(
        dsn=dsn,
        release=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        traces_sample_rate=0.1,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        before_send=before_send,
    )
    logger.info("[%s] Sentry initialized", component)
    return True
