"""Compatibility surface for self-healing reflections relocated to reflections/agents/.

The five self-healing reflection callables that used to live here now each have
their own self-contained module under ``reflections/agents/`` (one file per
reflection — see issue #1028). The reflections registry (config/reflections.yaml,
vault) still references the historical dotted paths below, so each reflection is
re-exported here under its original name and the scheduler's importlib resolution
keeps working with no registry edit:

- ``agent.sustainability.circuit_health_gate``    → reflections.agents.circuit_health_gate.run
- ``agent.sustainability.session_recovery_drip``  → reflections.agents.session_recovery_drip.run
- ``agent.sustainability.session_count_throttle`` → reflections.agents.session_count_throttle.run
- ``agent.sustainability.failure_loop_detector``  → reflections.agents.failure_loop_detector.run
- ``agent.sustainability.sustainability_digest``  → reflections.agents.system_health_digest.run

New code should import the reflection directly from its per-reflection module.

``send_hibernation_notification`` is NOT a reflection — it is a helper imported
directly by ``agent/agent_session_queue.py`` (the circuit-health hibernation
path). It stays defined here, along with the ``_get_project_key`` / ``_get_redis``
helpers that several tests and the relocated reflections rely on as the canonical
project-key resolver.
"""

import logging
import os

# Re-exports so config/reflections.yaml's historical callable paths still resolve.
from reflections.agents.circuit_health_gate import run as circuit_health_gate
from reflections.agents.failure_loop_detector import run as failure_loop_detector
from reflections.agents.session_count_throttle import run as session_count_throttle
from reflections.agents.session_recovery_drip import run as session_recovery_drip
from reflections.agents.system_health_digest import run as sustainability_digest

logger = logging.getLogger(__name__)

__all__ = [
    "circuit_health_gate",
    "session_recovery_drip",
    "session_count_throttle",
    "failure_loop_detector",
    "sustainability_digest",
    "send_hibernation_notification",
    "_get_project_key",
    "_get_redis",
]


def _get_project_key() -> str:
    """Return the project-scoped Redis key prefix.

    Sources VALOR_PROJECT_KEY from env (injected by worker/bridge plist
    generators). Empty or whitespace-only values fall back to ``"valor"`` so a
    misconfigured ``VALOR_PROJECT_KEY=`` line in ``.env`` does not produce a
    bare ``:sustainability:queue_paused`` key (issue #1171).
    """
    v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return v or "valor"


def _get_redis():
    """Return the shared Popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def send_hibernation_notification(event: str, project_key: str | None = None) -> None:
    """Enqueue a Telegram notification session for hibernation entry or wake.

    Absorbed from the former hibernation module (send_hibernation_notification).

    Args:
        event: Either "hibernating" or "waking".
        project_key: Project key for the notification session. Defaults to env var.

    Enqueues a lightweight agent session with a pre-composed notification message.
    Wrapped in try/except — never raises.
    """
    try:
        from models.agent_session import AgentSession

        pk = project_key or _get_project_key()

        if event == "hibernating":
            # Count paused sessions for context
            try:
                paused = list(AgentSession.query.filter(project_key=pk, status="paused"))
                count = len(paused)
            except Exception:
                count = 0
            command = (
                f"Send a Telegram message to the 'Eng: Valor' chat with this exact text:\n"
                f"Worker hibernating: Anthropic circuit open. "
                f"{count} session(s) paused. Will resume automatically when circuit closes."
            )
        elif event == "waking":
            try:
                paused = list(AgentSession.query.filter(project_key=pk, status="paused"))
                count = len(paused)
            except Exception:
                count = 0
            command = (
                f"Send a Telegram message to the 'Eng: Valor' chat with this exact text:\n"
                f"Worker waking: Anthropic circuit closed. "
                f"Beginning drip resume — {count} session(s) queued to restore."
            )
        else:
            logger.warning(
                "[circuit-health-gate] Unknown event type %r — skipping notification", event
            )
            return

        notification_session = AgentSession(
            session_type="teammate",
            project_key=pk,
            command=command,
        )
        notification_session.save()
        logger.info(
            "[circuit-health-gate] Enqueued %s notification session %s",
            event,
            getattr(notification_session, "agent_session_id", "?"),
        )
    except Exception as e:
        logger.error("[circuit-health-gate] Failed to enqueue %s notification: %s", event, e)
