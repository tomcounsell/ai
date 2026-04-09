"""Sustainable self-healing reflections for queue governance.

Five standalone reflection functions, each registered in config/reflections.yaml:
- api_health_gate: pause/resume queue based on Anthropic circuit state
- session_count_throttle: throttle sessions per hour to prevent runaway execution
- failure_loop_detector: deduplicate GitHub issues for repeated error patterns
- recovery_drip: drip paused_circuit sessions back to pending one at a time
- sustainability_digest: daily Telegram health summary

All functions are synchronous (run in executor by the reflection scheduler).
All functions catch all exceptions and log — never crash the reflection tick.
"""

import hashlib
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)


def _get_project_key() -> str:
    """Return the project-scoped Redis key prefix."""
    return os.environ.get("VALOR_PROJECT_KEY", "default")


def _get_redis():
    """Return the shared Popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def api_health_gate() -> None:
    """Check Anthropic circuit state and pause/resume the session queue.

    - OPEN or HALF_OPEN → set queue_paused flag (TTL 3600s)
    - CLOSED → clear queue_paused, set recovery:active flag (TTL 3600s)

    Logs at WARNING on first transition to paused state.
    Guards against cold-start / test environments where circuit is not registered.
    """
    try:
        from bridge.health import get_health
        from bridge.resilience import CircuitState

        r = _get_redis()
        project_key = _get_project_key()
        pause_key = f"{project_key}:sustainability:queue_paused"
        recovery_key = f"{project_key}:recovery:active"

        cb = get_health().get("anthropic")
        if cb is None:
            logger.debug("[api-health-gate] Anthropic circuit not registered — skipping")
            return

        if cb.state == CircuitState.CLOSED:
            # Circuit closed — clear pause, signal recovery drip
            was_paused = r.exists(pause_key)
            r.delete(pause_key)
            if was_paused:
                logger.info(
                    "[api-health-gate] Anthropic circuit CLOSED — queue unpaused, starting recovery drip"  # noqa: E501
                )
                r.set(recovery_key, "1", ex=3600)
            else:
                logger.debug("[api-health-gate] Anthropic circuit CLOSED — queue was not paused")
        else:
            # OPEN or HALF_OPEN — pause the queue
            was_paused = r.exists(pause_key)
            r.set(pause_key, "1", ex=3600)
            if not was_paused:
                logger.warning(
                    "[api-health-gate] Queue paused — Anthropic circuit %s", cb.state.value.upper()
                )
            else:
                logger.debug(
                    "[api-health-gate] Queue remains paused — Anthropic circuit %s",
                    cb.state.value.upper(),
                )
    except Exception:
        logger.exception("[api-health-gate] Unhandled exception — skipping tick")


def session_count_throttle() -> None:
    """Count sessions started in the last hour and write throttle level.

    Throttle levels:
    - none: sessions/hr < SUSTAINABILITY_THROTTLE_MODERATE
    - moderate: sessions/hr >= SUSTAINABILITY_THROTTLE_MODERATE (blocks low-priority only)
    - suspended: sessions/hr >= SUSTAINABILITY_THROTTLE_SUSPENDED (blocks normal + low)

    Thresholds are configurable via env vars (defaults: 20 and 40).
    """
    try:
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        throttle_key = f"{project_key}:sustainability:throttle_level"

        moderate_threshold = int(os.environ.get("SUSTAINABILITY_THROTTLE_MODERATE", "20"))
        suspended_threshold = int(os.environ.get("SUSTAINABILITY_THROTTLE_SUSPENDED", "40"))

        cutoff = time.time() - 3600  # 1 hour ago
        all_sessions = list(AgentSession.query.filter(project_key=project_key))
        recent = []
        for s in all_sessions:
            started_at = getattr(s, "started_at", None)
            if started_at is None:
                continue
            if isinstance(started_at, int | float):
                ts = started_at
            else:
                try:
                    ts = started_at.timestamp()
                except Exception:
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


def failure_loop_detector() -> None:
    """Detect repeated error fingerprints and file one GitHub issue per novel cluster.

    Scans failed sessions in the last 4 hours. Groups by error fingerprint.
    For each cluster with >= 3 failures:
    - Checks Redis set for seen fingerprints (SADD, skip if return value 0)
    - Files one GitHub issue via gh CLI
    - Marks affected sessions with loop_detected=True in extra_context

    Skips analysis entirely if the queue is paused (API outage in progress).
    """
    try:
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        pause_key = f"{project_key}:sustainability:queue_paused"
        seen_key = f"{project_key}:sustainability:seen_fingerprints"

        # Skip during acknowledged API outages
        if r.exists(pause_key):
            logger.info("[failure-loop-detector] Queue paused (API outage) — skipping failure scan")
            return

        cutoff = time.time() - (4 * 3600)  # 4 hours ago
        all_sessions = list(AgentSession.query.filter(project_key=project_key))
        failed_sessions = []
        for s in all_sessions:
            if s.status not in ("failed", "abandoned"):
                continue
            completed_at = getattr(s, "completed_at", None)
            if completed_at is None:
                continue
            if isinstance(completed_at, int | float):
                ts = completed_at
            else:
                try:
                    ts = completed_at.timestamp()
                except Exception:
                    continue
            if ts >= cutoff:
                failed_sessions.append(s)

        if not failed_sessions:
            logger.debug("[failure-loop-detector] No failed sessions in last 4 hours")
            return

        # Build fingerprint clusters
        clusters: dict[str, list] = {}
        for s in failed_sessions:
            fingerprint = _compute_fingerprint(s)
            clusters.setdefault(fingerprint, []).append(s)

        for fingerprint, sessions in clusters.items():
            if len(sessions) < 3:
                continue

            # Atomic check-and-set: SADD returns 1 if new, 0 if already existed
            added = r.sadd(seen_key, fingerprint)
            if r.ttl(seen_key) < 0:
                # Set TTL if not already set (7 days)
                r.expire(seen_key, 7 * 86400)

            if added == 0:
                logger.debug(
                    "[failure-loop-detector] Fingerprint %s already seen — skipping issue creation",
                    fingerprint,
                )
                continue

            # File GitHub issue
            session_ids = [getattr(s, "session_id", "?") for s in sessions[:5]]
            _file_github_issue(fingerprint, sessions, session_ids)

            # Mark sessions with loop_detected=True
            for s in sessions:
                try:
                    ec = s.extra_context or {}
                    ec["loop_detected"] = True
                    s.extra_context = ec
                    s.save()
                except Exception as mark_err:
                    logger.debug(
                        "[failure-loop-detector] Could not mark session %s: %s",
                        getattr(s, "session_id", "?"),
                        mark_err,
                    )

        logger.info(
            "[failure-loop-detector] Scanned %d failed sessions, found %d clusters (>= 3 failures)",
            len(failed_sessions),
            sum(1 for sessions in clusters.values() if len(sessions) >= 3),
        )
    except Exception:
        logger.exception("[failure-loop-detector] Unhandled exception — skipping tick")


def _compute_fingerprint(session) -> str:
    """Compute a short error fingerprint for a session.

    Uses HTTP status code if available, otherwise exception class name.
    Falls back to 'unknown' if no error info.
    """
    ec = getattr(session, "extra_context", None) or {}
    http_status = ec.get("http_status") or ec.get("status_code")
    exc_type = ec.get("exception_type") or ec.get("error_type")
    error_message = ec.get("error_message") or ec.get("failed_reason") or ""

    # Also try top-level failed_reason on the model
    if not error_message:
        error_message = getattr(session, "failed_reason", "") or ""

    if http_status is not None:
        http_component = str(http_status)
    elif exc_type:
        http_component = str(exc_type)
    else:
        http_component = "unknown"

    raw = f"{http_component}:{error_message[:80]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _file_github_issue(fingerprint: str, sessions: list, session_ids: list) -> None:
    """File a GitHub issue for a failure loop cluster."""
    try:
        count = len(sessions)
        sample_session = sessions[0]
        ec = getattr(sample_session, "extra_context", None) or {}
        error_message = (
            ec.get("error_message")
            or ec.get("failed_reason")
            or getattr(sample_session, "failed_reason", "")
            or "Unknown error"
        )[:200]

        title = f"[failure-loop] {count}x same error fingerprint: {fingerprint}"
        body = (
            f"## Failure Loop Detected\n\n"
            f"**Fingerprint:** `{fingerprint}`\n"
            f"**Count:** {count} failures in last 4 hours\n"
            f"**Sample error:** {error_message}\n\n"
            f"**Affected session IDs (up to 5):**\n"
            + "\n".join(f"- `{sid}`" for sid in session_ids)
            + "\n\nAuto-filed by `failure-loop-detector` reflection."
        )

        result = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body, "--label", "bug"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.warning(
                "[failure-loop-detector] Filed GitHub issue for fingerprint %s: %s",
                fingerprint,
                result.stdout.strip(),
            )
        else:
            logger.error(
                "[failure-loop-detector] gh issue create failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
    except Exception as e:
        logger.error("[failure-loop-detector] Failed to file GitHub issue: %s", e)


def recovery_drip() -> None:
    """Drip one paused_circuit session back to pending per tick.

    Only active when {project_key}:recovery:active flag is set in Redis.
    Clears the recovery flag when the paused_circuit queue is empty.

    Rate: called every 30s by the scheduler → at most 1 session per 30s.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        r = _get_redis()
        project_key = _get_project_key()
        recovery_key = f"{project_key}:recovery:active"

        if not r.exists(recovery_key):
            logger.debug("[recovery-drip] recovery:active flag not set — no-op")
            return

        paused = list(AgentSession.query.filter(project_key=project_key, status="paused_circuit"))

        if not paused:
            r.delete(recovery_key)
            logger.info(
                "[recovery-drip] paused_circuit queue empty — clearing recovery:active flag"
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
                reason="recovery-drip: API circuit recovered",
            )
            logger.info(
                "[recovery-drip] Dripped session %s → pending (%d remaining paused)",
                getattr(candidate, "session_id", "?"),
                len(paused) - 1,
            )
        except Exception as e:
            logger.warning(
                "[recovery-drip] Could not transition session %s to pending: %s",
                getattr(candidate, "session_id", "?"),
                e,
            )
    except Exception:
        logger.exception("[recovery-drip] Unhandled exception — skipping tick")


def sustainability_digest() -> None:
    """Enqueue a Claude agent session to send a daily Telegram health summary.

    The agent session is given a command prompt that includes required output fields:
    - Circuit state per dependency
    - Current throttle level
    - Session count last 24 hours
    - Active failure cluster count

    This reflection is registered with interval 86400s (daily).
    """
    try:
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        throttle_key = f"{project_key}:sustainability:throttle_level"
        pause_key = f"{project_key}:sustainability:queue_paused"

        # Gather state for the digest prompt
        throttle_level = (r.get(throttle_key) or b"none").decode()
        queue_paused = bool(r.exists(pause_key))

        command = (
            "You are generating the daily sustainability digest for the Valor AI system. "
            "Collect and report the following required fields:\n"
            "1. Circuit state per dependency (anthropic, telegram, redis) — use get_health().summary()\n"  # noqa: E501
            "2. Current throttle level — read from Redis key "
            f"'{project_key}:sustainability:throttle_level' (current: {throttle_level})\n"
            "3. Queue paused status — read from Redis key "
            f"'{project_key}:sustainability:queue_paused' (currently: {'paused' if queue_paused else 'active'})\n"  # noqa: E501
            "4. Session count in last 24 hours — query AgentSession records\n"
            "5. Active failure cluster count — count entries in Redis set "
            f"'{project_key}:sustainability:seen_fingerprints'\n\n"
            "Format as a concise Telegram message (3-8 lines). "
            "Send via valor-telegram to the 'Dev: Valor' chat. "
            "Subject line: Daily System Health Digest."
        )

        AgentSession.create_and_enqueue(
            project_key=project_key,
            message_text=command,
            session_type="dev",
            priority="low",
            extra_context={"digest_type": "sustainability_digest"},
        )
        logger.info("[sustainability-digest] Enqueued daily digest session")
    except Exception:
        logger.exception("[sustainability-digest] Unhandled exception — skipping tick")
