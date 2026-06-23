"""reflections/agents/circuit_health_gate.py — manage Anthropic circuit flags.

What it does: Reads the Anthropic circuit breaker state via bridge.health. On
    CLOSED, clears the queue_paused and worker:hibernating Redis flags and, if
    either was set, sets recovery:active and worker:recovering (TTL 3600s each)
    and enqueues a "waking" Telegram notification session. On OPEN/HALF_OPEN,
    renews queue_paused (TTL 3600s) and worker:hibernating (TTL 600s).
Cadence: 60s (circuit state must be tracked near-real-time so the queue
    pauses/resumes promptly on outages).
Failure modes:
    - Anthropic circuit not registered (cold-start/test) -> debug log, skip tick.
    - Wake-notification enqueue fails -> error log, continue (best-effort).
    - Any unhandled exception -> logger.exception, skip tick (never crash).
Related reflections:
    - session_recovery_drip: consumes the recovery:active / worker:recovering
      flags this reflection sets, dripping paused sessions back to pending.
    - system_health_digest: reports queue_paused state as an anomaly.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

import logging
import os

logger = logging.getLogger(__name__)


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


def run() -> None:
    """Check Anthropic circuit state and manage all circuit-related flags atomically.

    Replaces api_health_gate (sustainability.py) and worker_health_gate (hibernation.py).

    - OPEN or HALF_OPEN → renew queue_paused (TTL 3600s) AND worker:hibernating (TTL 600s)
    - CLOSED → delete both flags; if either was set, set recovery:active AND worker:recovering
      (TTL 3600s each) and call send_hibernation_notification("waking")

    Logs at WARNING on first transition to hibernated/paused state.
    Guards against cold-start / test environments where circuit is not registered.
    Never raises — all exceptions are caught and logged.
    """
    try:
        from bridge.health import get_health
        from bridge.resilience import CircuitState

        r = _get_redis()
        project_key = _get_project_key()
        pause_key = f"{project_key}:sustainability:queue_paused"
        hib_key = f"{project_key}:worker:hibernating"
        recovery_key = f"{project_key}:recovery:active"
        rec_key = f"{project_key}:worker:recovering"

        cb = get_health().get("anthropic")
        if cb is None:
            logger.debug("[circuit-health-gate] Anthropic circuit not registered — skipping")
            return

        if cb.state == CircuitState.CLOSED:
            # Circuit closed — clear both flags atomically
            was_paused = r.exists(pause_key)
            was_hibernating = r.exists(hib_key)
            r.delete(pause_key)
            r.delete(hib_key)
            was_either_flag_set = was_paused or was_hibernating
            if was_either_flag_set:
                logger.info(
                    "[circuit-health-gate] Anthropic circuit CLOSED — queue unpaused,"
                    " hibernation cleared, starting recovery drip"
                )
                r.set(recovery_key, "1", ex=3600)
                r.set(rec_key, "1", ex=3600)
                # Enqueue wake notification (best-effort)
                try:
                    send_hibernation_notification("waking", project_key=project_key)
                except Exception as _notif_err:
                    logger.error(
                        "[circuit-health-gate] Failed to enqueue wake notification: %s", _notif_err
                    )
            else:
                logger.debug(
                    "[circuit-health-gate] Anthropic circuit CLOSED — queue was not paused,"
                    " worker was not hibernating"
                )
        else:
            # OPEN or HALF_OPEN — renew both flags
            was_paused = r.exists(pause_key)
            was_hibernating = r.exists(hib_key)
            r.set(pause_key, "1", ex=3600)
            r.set(hib_key, "1", ex=600)
            if not was_paused and not was_hibernating:
                logger.warning(
                    "[circuit-health-gate] Queue paused, worker hibernating — Anthropic circuit %s",
                    cb.state.value.upper(),
                )
            else:
                logger.debug(
                    "[circuit-health-gate] Queue remains paused, worker remains hibernating"
                    " — Anthropic circuit %s",
                    cb.state.value.upper(),
                )
    except Exception:
        logger.exception("[circuit-health-gate] Unhandled exception — skipping tick")
