"""reflections/agents/failure_loop_detector.py — file one GitHub issue per failure cluster.

What it does: Scans failed/abandoned sessions from the last 4 hours, groups them
    by error fingerprint (SHA256 of HTTP-status/exception-type + error message).
    For each cluster with >= 3 failures, atomically records the fingerprint in the
    {project_key}:sustainability:seen_fingerprints Redis set (7-day TTL), files a
    GitHub issue via the gh CLI, and marks affected sessions loop_detected=True.
Cadence: 3600s (hourly scan over a 4-hour window catches sustained loops without
    spamming issues; the 7-day dedup set prevents duplicate filings).
Failure modes:
    - Queue paused (acknowledged API outage) -> info log, skip scan.
    - Fingerprint already seen (SADD returns 0) -> debug log, skip issue creation.
    - gh issue create fails -> error log, continue.
    - Marking a session fails -> debug log, continue.
    - Any unhandled exception -> logger.exception, skip tick (never crash).
Related reflections:
    - circuit_health_gate: sets queue_paused, which this reflection honors to
      avoid filing issues during a known outage.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

import hashlib
import logging
import os
import subprocess
import time

from config.settings import settings

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


def _latest_failure_reason(session) -> str:
    """Extract the latest failure reason from session_events lifecycle entries.

    Production code writes failure reasons via ``log_lifecycle_transition()``
    as ``{"event_type": "lifecycle", "text": "{old}→{new}: {reason}"}`` entries
    in ``session.session_events``.  This helper reads that path as a fallback
    when ``extra_context`` and ``failed_reason`` are both empty.

    Parse contract:
    - Split on ``": "`` (two-char separator) with ``maxsplit=1`` to handle
      embedded colons in the reason text (e.g. ``"health check: no progress"``).
    - Return ``parts[1]`` when the split yields exactly 2 parts; return ``""``
      otherwise (no separator → no reason recorded).

    Guard contract:
    - Uses ``isinstance(events, list)`` — NOT ``is None`` — because a bare
      ``MagicMock`` auto-creates a truthy attribute that would pass a None check
      but is not iterable as expected.

    Empty-reason contract:
    - Returns ``""`` for all malformed inputs so callers can treat a falsy return
      as "no reason available" without special-casing.
    """
    events = getattr(session, "session_events", None)
    if not isinstance(events, list):
        return ""
    for item in reversed(events):
        if not isinstance(item, dict):
            continue
        if item.get("event_type") == "lifecycle":
            text = item.get("text", "")
            parts = text.split(": ", 1)
            return parts[1] if len(parts) == 2 else ""
    return ""


def _compute_fingerprint(session) -> str:
    """Compute a short error fingerprint for a session.

    Resolution order for the error message component:
    1. ``extra_context["error_message"]`` / ``extra_context["failed_reason"]``
    2. Top-level ``session.failed_reason``
    3. Latest lifecycle reason from ``session.session_events`` (``"{old}→{new}: {reason}"``)
    4. Falls back to ``""`` → raw becomes ``"unknown:"`` → degenerate hash
       ``06f5940a02173ba1`` (SHA256("unknown:")[:16]).

    The session_events fallback (step 3) means that sessions whose failure
    reason is stored only as a lifecycle transition event — the standard
    production path via ``finalize_session()`` → ``log_lifecycle_transition()``
    — now produce distinct fingerprints per distinct reason rather than all
    collapsing into the same degenerate hash.  The 7-day per-fingerprint Redis
    dedup key ensures one GitHub issue per novel cluster.

    Uses HTTP status code or exception type for the non-message component when
    available; otherwise ``"unknown"``.
    """
    ec = getattr(session, "extra_context", None) or {}
    http_status = ec.get("http_status") or ec.get("status_code")
    exc_type = ec.get("exception_type") or ec.get("error_type")
    error_message = ec.get("error_message") or ec.get("failed_reason") or ""

    # Also try top-level failed_reason on the model
    if not error_message:
        error_message = getattr(session, "failed_reason", "") or ""

    # Fallback: read the latest lifecycle reason from session_events
    if not error_message:
        error_message = _latest_failure_reason(session)

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
            or _latest_failure_reason(sample_session)
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
            timeout=settings.timeouts.git_subprocess_s,
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


def run() -> None:
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
