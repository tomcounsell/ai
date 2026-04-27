"""Sustainable self-healing reflections for queue governance.

Seven standalone reflection functions, each registered in config/reflections.yaml:
- circuit_health_gate: pause/resume queue and manage hibernation based on Anthropic circuit state
- session_recovery_drip: drip paused_circuit and paused sessions back to pending one at a time
- session_count_throttle: throttle sessions per hour to prevent runaway execution
- failure_loop_detector: deduplicate GitHub issues for repeated error patterns
- sustainability_digest: daily Telegram health summary
- send_hibernation_notification: enqueue a Telegram notification for hibernation entry or wake

All functions are synchronous (run in executor by the reflection scheduler).
All functions catch all exceptions and log — never crash the reflection tick.

Redis key schema:
- {project_key}:sustainability:queue_paused — set when circuit is OPEN/HALF_OPEN (TTL 3600s)
- {project_key}:worker:hibernating — set when worker enters hibernation (TTL 600s)
- {project_key}:recovery:active — set when circuit recovers from queue_paused state (TTL 3600s)
- {project_key}:worker:recovering — set when circuit recovers from hibernation (TTL 3600s)

The {project_key} prefix resolves to ``valor`` in production, sourced from the
``VALOR_PROJECT_KEY`` env var injected into worker/bridge launchd plists by
the install scripts (``scripts/install_worker.sh`` and
``scripts/update/service.py::install_worker``). When unset/empty, the fallback
is ``"valor"`` (matching ``tools.agent_session_scheduler.DEFAULT_PROJECT_KEY``)
to keep readers and AgentSession writers aligned. See issue #1171.
"""

import hashlib
import logging
import os
import subprocess
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


def circuit_health_gate() -> None:
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


def session_recovery_drip() -> None:
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


def session_count_throttle() -> None:
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
        from agent.session_health import _filter_hydrated_sessions
        from models.agent_session import AgentSession

        r = _get_redis()
        project_key = _get_project_key()
        pause_key = f"{project_key}:sustainability:queue_paused"
        seen_key = f"{project_key}:sustainability:seen_fingerprints"

        # Skip during acknowledged API outages
        if r.exists(pause_key):
            logger.info("[failure-loop-detector] Queue paused (API outage) — skipping failure scan")
            return

        from bridge.utc import to_unix_ts

        cutoff = time.time() - (4 * 3600)  # 4 hours ago
        # Phantom guard: drop records whose fields are still Popoto Field descriptors
        # (orphan $IndexF members).
        all_sessions = _filter_hydrated_sessions(AgentSession.query.filter(project_key=project_key))
        failed_sessions = []
        for s in all_sessions:
            if s.status not in ("failed", "abandoned"):
                continue
            ts = to_unix_ts(getattr(s, "completed_at", None))
            if ts is None:
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


def sustainability_digest() -> None:
    """Send a daily health summary to Telegram.

    Checks all health signals locally. If everything is nominal, sends a
    one-line "all clear" directly via valor-telegram (no agent session needed).
    Only spins up a full agent session when there are anomalies worth reporting.

    This reflection is registered with interval 86400s (daily).
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
            # Send a one-liner directly — no agent session needed
            _send_telegram("🩺 Daily health check — all clear, no surprises.")
            logger.info("[system-health-digest] All nominal — sent one-liner")
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
                "Send via valor-telegram to the 'Dev: Valor' chat. "
                "Subject line: ⚠️ Daily Health Digest — anomalies detected."
            )

            AgentSession.create_and_enqueue(
                project_key=project_key,
                message_text=command,
                session_type="dev",
                priority="low",
                extra_context={"digest_type": "sustainability_digest"},
            )
            logger.info(
                "[system-health-digest] Anomalies detected (%s) — enqueued agent session",
                ", ".join(anomalies),
            )
    except Exception:
        logger.exception("[system-health-digest] Unhandled exception — skipping tick")


def _send_telegram(message: str) -> None:
    """Send a message to the 'Dev: Valor' chat via valor-telegram CLI."""
    try:
        result = subprocess.run(
            ["valor-telegram", "send", "--chat", "Dev: Valor", message],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error(
                "[system-health-digest] valor-telegram send failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
    except Exception as e:
        logger.error("[system-health-digest] Failed to send Telegram message: %s", e)
