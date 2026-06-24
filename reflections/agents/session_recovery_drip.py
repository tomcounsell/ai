"""reflections/agents/session_recovery_drip.py — drip paused sessions back to pending.

What it does: When recovery:active OR worker:recovering is set in Redis, moves
    one session per tick back to pending — paused_circuit sessions first (FIFO by
    created_at), then paused sessions (FIFO). Clears both recovery flags when both
    queues are empty.
Cadence: 30s (rate-limits resume to at most one session per 30s so a recovered
    worker does not stampede the queue).
Failure modes:
    - Neither recovery flag set -> debug log, no-op.
    - transition_status fails for a candidate -> warning log, continue (the next
      tick retries).
    - Any unhandled exception -> logger.exception, skip tick (never crash).
Related reflections:
    - circuit_health_gate: sets the recovery:active / worker:recovering flags this
      reflection consumes when the Anthropic circuit closes.
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


def run() -> None:
    """Drip one paused session back to pending per tick.

    Replaces recovery_drip (sustainability.py) and session_resume_drip (hibernation.py).

    Only active when recovery:active OR worker:recovering flag is set in Redis.
    Drips paused_circuit sessions first (FIFO), then paused sessions (FIFO).
    Clears BOTH recovery:active AND worker:recovering when both queues are empty.

    Rate: called every 30s by the scheduler → at most 1 session per 30s.
    Never raises — all exceptions are caught and logged.
    """
    try:
        from agent.session_health import _filter_hydrated_sessions
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        r = _get_redis()
        project_key = _get_project_key()
        recovery_key = f"{project_key}:recovery:active"
        rec_key = f"{project_key}:worker:recovering"

        if not r.exists(recovery_key) and not r.exists(rec_key):
            logger.debug("[session-recovery-drip] neither recovery flag set — no-op")
            return

        # Phantom guard: drop records whose fields are still Popoto Field descriptors
        # (orphan $IndexF members).
        paused_circuit = _filter_hydrated_sessions(
            AgentSession.query.filter(project_key=project_key, status="paused_circuit")
        )
        paused = _filter_hydrated_sessions(
            AgentSession.query.filter(project_key=project_key, status="paused")
        )

        if not paused_circuit and not paused:
            r.delete(recovery_key)
            r.delete(rec_key)
            logger.info(
                "[session-recovery-drip] both queues empty — clearing recovery:active"
                " and worker:recovering flags"
            )
            return

        # Pop oldest session (FIFO by created_at) — paused_circuit has priority
        def _ts(session):
            from bridge.utc import to_unix_ts

            return to_unix_ts(getattr(session, "created_at", None)) or 0.0

        if paused_circuit:
            paused_circuit.sort(key=_ts)
            candidate = paused_circuit[0]
            drip_reason = "session-recovery-drip: API circuit recovered"
        else:
            paused.sort(key=_ts)
            candidate = paused[0]
            drip_reason = "session-recovery-drip: worker recovered"

        remaining = len(paused_circuit) + len(paused) - 1

        try:
            transition_status(
                candidate,
                "pending",
                reason=drip_reason,
            )
            logger.info(
                "[session-recovery-drip] Dripped session %s → pending (%d remaining paused)",
                getattr(candidate, "session_id", "?"),
                remaining,
            )
        except Exception as e:
            logger.warning(
                "[session-recovery-drip] Could not transition session %s to pending: %s",
                getattr(candidate, "session_id", "?"),
                e,
            )
    except Exception:
        logger.exception("[session-recovery-drip] Unhandled exception — skipping tick")
