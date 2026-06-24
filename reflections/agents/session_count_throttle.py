"""reflections/agents/session_count_throttle.py — throttle sessions per hour.

What it does: Counts sessions started in the last hour and writes a throttle
    level (none/moderate/suspended) to {project_key}:sustainability:throttle_level
    in Redis (TTL 7200s). Thresholds come from SUSTAINABILITY_THROTTLE_MODERATE
    (default 20) and SUSTAINABILITY_THROTTLE_SUSPENDED (default 40).
Cadence: 3600s (hourly window matches the rolling count; the 2hr TTL survives
    across two ticks so the level never expires mid-window).
Failure modes:
    - Any unhandled exception -> logger.exception, skip tick (never crash).
Related reflections:
    - system_health_digest: reads throttle_level and reports a non-"none" level
      as an anomaly.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

import logging
import os
import time

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
    """Count sessions started in the last hour and write throttle level.

    Throttle levels:
    - none: sessions/hr < SUSTAINABILITY_THROTTLE_MODERATE
    - moderate: sessions/hr >= SUSTAINABILITY_THROTTLE_MODERATE (blocks low-priority only)
    - suspended: sessions/hr >= SUSTAINABILITY_THROTTLE_SUSPENDED (blocks normal + low)

    Thresholds are configurable via env vars (defaults: 20 and 40).
    """
    try:
        from agent.session_health import _filter_hydrated_sessions
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        throttle_key = f"{project_key}:sustainability:throttle_level"

        moderate_threshold = int(os.environ.get("SUSTAINABILITY_THROTTLE_MODERATE", "20"))
        suspended_threshold = int(os.environ.get("SUSTAINABILITY_THROTTLE_SUSPENDED", "40"))

        cutoff = time.time() - 3600  # 1 hour ago
        # Phantom guard: drop records whose fields are still Popoto Field descriptors
        # (orphan $IndexF members).
        all_sessions = _filter_hydrated_sessions(AgentSession.query.filter(project_key=project_key))
        from bridge.utc import to_unix_ts

        recent = []
        for s in all_sessions:
            ts = to_unix_ts(getattr(s, "started_at", None))
            if ts is None:
                continue
            if ts >= cutoff:
                recent.append(s)

        count = len(recent)
        if count >= suspended_threshold:
            level = "suspended"
        elif count >= moderate_threshold:
            level = "moderate"
        else:
            level = "none"

        r.set(throttle_key, level, ex=7200)  # TTL 2hr (survive across two hourly ticks)
        logger.info(
            "[session-count-throttle] %d sessions started in last hour → throttle_level=%s",
            count,
            level,
        )
    except Exception:
        logger.exception("[session-count-throttle] Unhandled exception — skipping tick")
