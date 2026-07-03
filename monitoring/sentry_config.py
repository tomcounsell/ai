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
    a bridge-only concept). The worker passes ``None`` so worker Sentry events are
    never silently dropped because the bridge happens to be hibernating. This
    helper never imports ``bridge.hibernation``.
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


def configure_sentry(component: str, before_send=None) -> bool:
    """Initialize Sentry for a process ``component`` (e.g. ``"bridge"`` / ``"worker"``).

    Args:
        component: Human-readable process name, used only in log lines.
        before_send: Optional Sentry ``before_send`` hook. The bridge passes its
            hibernation filter; the worker passes ``None``.

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
