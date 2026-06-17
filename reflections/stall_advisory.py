"""
reflections/stall_advisory.py — Stalled-session advisory classifier reflection.

Callable contract: accepts optional params dict, returns:
  {"status": "ok"|"warn", "findings": [...], "summary": str}

Periodic reflection that:
1. Queries sessions in _RUNNING_PROBE_STATUSES (running/active/paused/paused_circuit)
2. For each session, calls classify_session_stall() with its telemetry events
3. Collects suspect and stalled verdicts into findings
4. Optionally sends a Telegram alert when findings are present and
   params["stall_advisory_telegram_enabled"] is True
5. Returns status="warn" when any suspect/stalled sessions found, "ok" otherwise

Per-session exception isolation: classification errors are logged at debug and
skipped — the reflection always completes and returns a summary.

Design constraints:
  - Zero writes: no Redis mutations, no AgentSession field changes, no side effects
    beyond optional Telegram send (when explicitly enabled)
  - Fail-soft: per-session exceptions are swallowed and logged
  - Telegram alert is error-only (no all-clear spam)
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("reflections.stall_advisory")


# ---------------------------------------------------------------------------
# Main reflection callable
# ---------------------------------------------------------------------------


def run_stall_advisory(params: dict | None = None) -> dict:
    """Run the stall-advisory reflection.

    Args:
        params: Optional configuration dict. Recognized keys:
            stall_advisory_telegram_enabled (bool, default False):
                When True and findings are present, send a Telegram alert.

    Returns:
        Dict with keys:
            status:   "ok" if all sessions are healthy, "warn" if any are
                      suspect or stalled.
            findings: List of dicts with session_id, level, reason for each
                      non-healthy session.
            summary:  Human-readable summary string.
    """
    params = params or {}
    telegram_enabled = bool(params.get("stall_advisory_telegram_enabled", False))

    findings: list[dict] = []

    try:
        from agent.session_stall_classifier import (
            _RUNNING_PROBE_STATUSES,
            classify_session_stall,
        )
        from agent.session_telemetry import read_session_timeline
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES
    except ImportError as e:
        return {"status": "error", "findings": [], "summary": f"import error: {e}"}

    # Query sessions in the probe statuses only
    try:
        probe_sessions = list(AgentSession.query.filter(status__in=list(_RUNNING_PROBE_STATUSES)))
    except Exception as e:
        logger.warning("stall_advisory: failed to query running sessions: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"stall-advisory query error: {e}",
        }

    # Skip any sessions that have transitioned to terminal status concurrently
    active_sessions = [s for s in probe_sessions if s.status not in TERMINAL_STATUSES]

    total = len(active_sessions)
    healthy_count = 0
    suspect_count = 0
    stalled_count = 0

    for session in active_sessions:
        session_id = getattr(session, "session_id", None) or getattr(
            session, "agent_session_id", "?"
        )
        try:
            events = read_session_timeline(session_id)
            verdict = classify_session_stall(events, session=session)
        except Exception as exc:
            logger.debug(
                "stall_advisory: classification failed for session %s: %r — skipping",
                session_id,
                exc,
            )
            continue

        if verdict.level == "stalled":
            stalled_count += 1
            finding = {
                "session_id": session_id,
                "level": verdict.level,
                "reason": verdict.reason,
            }
            findings.append(finding)
            logger.warning(
                "[stall-advisory] STALLED session=%s reason=%s signals=%r",
                session_id,
                verdict.reason,
                verdict.signals,
            )
        elif verdict.level == "suspect":
            suspect_count += 1
            finding = {
                "session_id": session_id,
                "level": verdict.level,
                "reason": verdict.reason,
            }
            findings.append(finding)
            logger.warning(
                "[stall-advisory] SUSPECT session=%s reason=%s signals=%r",
                session_id,
                verdict.reason,
                verdict.signals,
            )
        else:
            healthy_count += 1
            logger.debug(
                "stall_advisory: healthy session=%s reason=%s",
                session_id,
                verdict.reason,
            )

    # Build summary
    summary = (
        f"{total} running session(s): "
        f"{stalled_count} stalled, {suspect_count} suspect, {healthy_count} healthy"
    )
    logger.info("stall-advisory run complete: %s", summary)

    # Determine status
    status = "warn" if (stalled_count > 0 or suspect_count > 0) else "ok"

    # Telegram alert: only when enabled AND there are findings (no all-clear spam)
    if telegram_enabled and findings:
        problem_parts = []
        if stalled_count:
            problem_parts.append(f"{stalled_count} stalled")
        if suspect_count:
            problem_parts.append(f"{suspect_count} suspect")
        problem_desc = ", ".join(problem_parts)
        message = f"[stall-advisory] {problem_desc} session(s) detected. {summary}"
        _send_alert(message)

    return {"status": status, "findings": findings, "summary": summary}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert. All failures swallowed and logged."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("stall_advisory: valor-telegram not on PATH; skipping alert")
    except subprocess.TimeoutExpired:
        logger.warning("stall_advisory: valor-telegram timed out")
    except Exception as exc:
        logger.warning("stall_advisory: valor-telegram failed: %s", exc)
