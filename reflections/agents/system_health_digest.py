"""reflections/agents/system_health_digest.py — daily health check, exception-only alert.

What it does: Gathers throttle level, queue_paused state, active failure-cluster
    count, circuit states (anthropic/telegram/redis), and failed/abandoned session
    count over the last 24h. If everything is nominal it sends NOTHING. If any
    anomaly is present, enqueues a low-priority eng AgentSession that investigates
    and sends a Telegram digest to the 'Eng: Valor' chat.
Cadence: 86400s (once daily; the all-clear is intentionally suppressed because
    live status is always on the dashboard at localhost:8500).
Failure modes:
    - Circuit health unavailable -> treated as not-OK (an anomaly worth reporting).
    - Failed-session count fails -> set to -1 (unknown), treated as an anomaly.
    - Any unhandled exception -> logger.exception, skip tick (never crash).
Related reflections:
    - circuit_health_gate: source of the queue_paused flag this digest reads.
    - session_count_throttle: source of the throttle_level this digest reads.
    - failure_loop_detector: source of the seen_fingerprints cluster count.
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
    """Daily health check with exception-only Telegram delivery.

    Checks all health signals locally. If everything is nominal, sends
    NOTHING — the daily all-clear is intentional noise; live status is always
    on the dashboard. Only spins up a full agent session to report when there
    are anomalies worth Tom's attention.

    This reflection is registered with interval 86400s (daily) as a
    function-type callable, so the silence-on-healthy contract is deterministic
    rather than relying on an LLM agent to choose to send nothing.
    """
    try:
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        throttle_key = f"{project_key}:sustainability:throttle_level"
        pause_key = f"{project_key}:sustainability:queue_paused"
        fingerprints_key = f"{project_key}:sustainability:seen_fingerprints"

        # Gather state
        throttle_level = (r.get(throttle_key) or b"none").decode()
        queue_paused = bool(r.exists(pause_key))
        failure_cluster_count = r.scard(fingerprints_key) or 0

        # Check circuits
        circuits_ok = True
        try:
            from bridge.health import get_health
            from bridge.resilience import CircuitState

            health = get_health()
            for name in ("anthropic", "telegram", "redis"):
                cb = health.get(name)
                if cb is not None and cb.state != CircuitState.CLOSED:
                    circuits_ok = False
                    break
        except Exception:
            circuits_ok = False

        # Count failed sessions in last 24h
        failed_24h = 0
        try:
            all_sessions = list(AgentSession.query.filter(project_key=project_key))
            cutoff = time.time() - 86400
            for s in all_sessions:
                if s.status not in ("failed", "abandoned"):
                    continue
                completed_at = getattr(s, "completed_at", None)
                if completed_at is None:
                    continue
                ts = (
                    completed_at
                    if isinstance(completed_at, int | float)
                    else getattr(completed_at, "timestamp", lambda: 0)()
                )
                if ts >= cutoff:
                    failed_24h += 1
        except Exception:
            failed_24h = -1  # unknown — treat as anomaly

        # Determine if everything is nominal
        is_nominal = (
            circuits_ok
            and throttle_level == "none"
            and not queue_paused
            and failure_cluster_count == 0
            and failed_24h == 0
        )

        if is_nominal:
            # Exception-only delivery: on a healthy day, send NOTHING. The daily
            # all-clear is intentional noise Tom does not want — live status is
            # always on the dashboard (localhost:8500). Sending nothing is success.
            logger.info("[system-health-digest] All nominal — staying silent (no all-clear ping)")
        else:
            # Something noteworthy — spin up an agent to investigate and report
            anomalies = []
            if not circuits_ok:
                anomalies.append("one or more service circuits are not healthy")
            if throttle_level != "none":
                anomalies.append(f"throttle level is {throttle_level}")
            if queue_paused:
                anomalies.append("queue is paused")
            if failure_cluster_count > 0:
                anomalies.append(f"{failure_cluster_count} active failure cluster(s)")
            if failed_24h > 0:
                anomalies.append(f"{failed_24h} failed/abandoned session(s) in last 24h")
            elif failed_24h < 0:
                anomalies.append("could not count failed sessions")

            command = (
                "You are generating the daily sustainability digest for the Valor AI system. "
                "There are anomalies that need reporting:\n"
                + "\n".join(f"- {a}" for a in anomalies)
                + "\n\n"
                "Investigate each anomaly. Collect details:\n"
                "1. Circuit state per dependency (anthropic, telegram, redis)\n"
                "2. Current throttle level and queue paused status from Redis\n"
                "3. Session counts and failure details from last 24 hours\n"
                "4. Active failure cluster count\n\n"
                "When reporting circuit states, translate as follows"
                " — never output the raw state string:\n"
                "- 'closed' or 'CLOSED' → OK\n"
                "- 'open' or 'OPEN' → DOWN\n"
                "- 'half_open' or 'HALF_OPEN' → RECOVERING\n\n"
                "Format as a concise Telegram message highlighting what's wrong. "
                "Send via valor-telegram to the 'Eng: Valor' chat. "
                "Subject line: ⚠️ Daily Health Digest — anomalies detected."
            )

            AgentSession.create_and_enqueue(
                project_key=project_key,
                message_text=command,
                session_type="eng",
                priority="low",
                extra_context={"digest_type": "sustainability_digest"},
            )
            logger.info(
                "[system-health-digest] Anomalies detected (%s) — enqueued agent session",
                ", ".join(anomalies),
            )
    except Exception:
        logger.exception("[system-health-digest] Unhandled exception — skipping tick")
