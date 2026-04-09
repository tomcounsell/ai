"""Worker hibernation reflections and helpers.

Two standalone reflection functions, each registered in config/reflections.yaml:
- worker_health_gate: check Anthropic circuit; renew/clear hibernation flag
- session_resume_drip: drip one paused session back to pending per tick

One helper (not a reflection):
- send_hibernation_notification: enqueue a Telegram notification session

All functions are synchronous (run in executor by the reflection scheduler).
All functions catch all exceptions and log — never crash the reflection tick.

Redis key schema:
- {project_key}:worker:hibernating — set when worker enters hibernation (TTL 600s)
- {project_key}:worker:recovering — set when circuit recovers (TTL 3600s)

Relationship to sustainability.py (#773):
- paused_circuit status / queue_paused key: managed by api_health_gate / recovery_drip
- paused status / worker:hibernating key: managed by this module
Both flags block _pop_agent_session() with independent OR logic.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _get_project_key() -> str:
    """Return the project-scoped Redis key prefix."""
    return os.environ.get("VALOR_PROJECT_KEY", "default")


def _get_redis():
    """Return the shared Popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def worker_health_gate() -> None:
    """Check Anthropic circuit state and manage the worker hibernation flag.

    Called every 60s by the reflection scheduler.

    - OPEN or HALF_OPEN → renew worker:hibernating flag (TTL 600s)
    - CLOSED → delete worker:hibernating, write worker:recovering (TTL 3600s),
      enqueue wake notification (on first transition out of hibernation)

    Guards against cold-start / test environments where circuit is not registered.
    Never raises — all exceptions are caught and logged.
    """
    try:
        from bridge.health import get_health
        from bridge.resilience import CircuitState

        r = _get_redis()
        project_key = _get_project_key()
        hib_key = f"{project_key}:worker:hibernating"
        rec_key = f"{project_key}:worker:recovering"

        cb = get_health().get("anthropic")
        if cb is None:
            logger.debug("[worker-health-gate] Anthropic circuit not registered — skipping")
            return

        if cb.state == CircuitState.CLOSED:
            # Circuit closed — clear hibernation flag, signal drip resume
            was_hibernating = r.exists(hib_key)
            r.delete(hib_key)
            if was_hibernating:
                logger.info(
                    "[worker-health-gate] Anthropic circuit CLOSED — hibernation cleared,"
                    " starting drip resume"
                )
                r.set(rec_key, "1", ex=3600)
                # Enqueue wake notification (best-effort)
                try:
                    send_hibernation_notification("waking", project_key=project_key)
                except Exception as _notif_err:
                    logger.error(
                        "[worker-health-gate] Failed to enqueue wake notification: %s", _notif_err
                    )
            else:
                logger.debug(
                    "[worker-health-gate] Anthropic circuit CLOSED — worker was not hibernating"
                )
        else:
            # OPEN or HALF_OPEN — renew hibernation flag
            was_hibernating = r.exists(hib_key)
            r.set(hib_key, "1", ex=600)
            if not was_hibernating:
                logger.warning(
                    "[worker-health-gate] Worker hibernating — Anthropic circuit %s",
                    cb.state.value.upper(),
                )
            else:
                logger.debug(
                    "[worker-health-gate] Worker remains hibernating — Anthropic circuit %s",
                    cb.state.value.upper(),
                )
    except Exception:
        logger.exception("[worker-health-gate] Unhandled exception — skipping tick")


def session_resume_drip() -> None:
    """Drip one paused session back to pending per tick.

    Called every 30s by the reflection scheduler.

    Only active when {project_key}:worker:recovering flag is set in Redis.
    Clears the recovering flag when the paused queue is empty.
    Rate: called every 30s → at most 1 session re-queued per 30s (~2/min).

    Never raises — all exceptions are caught and logged.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        r = _get_redis()
        project_key = _get_project_key()
        rec_key = f"{project_key}:worker:recovering"

        if not r.exists(rec_key):
            logger.debug("[session-resume-drip] worker:recovering flag not set — no-op")
            return

        paused = list(AgentSession.query.filter(project_key=project_key, status="paused"))

        if not paused:
            r.delete(rec_key)
            logger.info(
                "[session-resume-drip] paused queue empty — clearing worker:recovering flag"
            )
            return

        # Pop oldest session (FIFO by created_at)
        def _ts(session):
            ca = getattr(session, "created_at", None)
            if ca is None:
                return 0.0
            if isinstance(ca, int | float):
                return float(ca)
            try:
                return ca.timestamp()
            except Exception:
                return 0.0

        paused.sort(key=_ts)
        candidate = paused[0]

        try:
            transition_status(
                candidate,
                "pending",
                reason="session-resume-drip: worker recovered",
            )
            logger.info(
                "[session-resume-drip] Dripped session %s → pending (%d remaining paused)",
                getattr(candidate, "session_id", "?"),
                len(paused) - 1,
            )
        except Exception as e:
            logger.warning(
                "[session-resume-drip] Could not transition session %s to pending: %s",
                getattr(candidate, "session_id", "?"),
                e,
            )
    except Exception:
        logger.exception("[session-resume-drip] Unhandled exception — skipping tick")


def send_hibernation_notification(event: str, project_key: str | None = None) -> None:
    """Enqueue a Telegram notification session for hibernation entry or wake.

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
                f"Send a Telegram message to the 'Dev: Valor' chat with this exact text:\n"
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
                f"Send a Telegram message to the 'Dev: Valor' chat with this exact text:\n"
                f"Worker waking: Anthropic circuit closed. "
                f"Beginning drip resume — {count} session(s) queued to restore."
            )
        else:
            logger.warning("[hibernation] Unknown event type %r — skipping notification", event)
            return

        notification_session = AgentSession(
            role="teammate",
            session_type="teammate",
            project_key=pk,
            command=command,
        )
        notification_session.save()
        logger.info(
            "[hibernation] Enqueued %s notification session %s",
            event,
            getattr(notification_session, "agent_session_id", "?"),
        )
    except Exception as e:
        logger.error("[hibernation] Failed to enqueue %s notification: %s", event, e)
