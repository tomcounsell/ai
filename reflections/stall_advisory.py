"""
reflections/stall_advisory.py — Stalled-session advisory + recovery reflection.

Callable contract: accepts optional params dict, returns:
  {"status": "ok"|"warn"|"error", "findings": [...], "summary": str}

Periodic reflection that:
1. Queries sessions in _RUNNING_PROBE_STATUSES (running/active/paused/paused_circuit)
2. For each session, calls classify_session_stall() with its telemetry events
3. Collects suspect and stalled verdicts into findings
4. For each STALLED finding with an actionable reason, runs the action-mode
   recovery ladder (_maybe_recover): observe across ticks, then once gates pass
   kill the session + re-enqueue via valor-catchup. Actuation is unconditional
   (issue #1855) — the consecutive-observation counter and run/per-session kill
   budgets are the safety mechanism, not a dry-run flag.
5. Optionally sends a Telegram alert when findings are present and
   params["stall_advisory_telegram_enabled"] is True
6. Returns status="warn" when any suspect/stalled sessions found, "ok" otherwise

Per-session exception isolation: classification errors are logged at debug and
skipped — the reflection always completes and returns a summary.

Design constraints:
  - The advisory path stays zero-write; recovery mutations are gated by the
    consecutive-observation counter and the run/per-session kill budgets
    (FEATURES__STALL_RECOVERY_RUN_BUDGET=0 is the no-deploy break-glass to
    disable actuation entirely).
  - Fail-soft: per-session exceptions and all recovery work are swallowed and
    logged. Recovery never changes the return contract keys (status/findings/
    summary) and never raises.
  - The stall-recovery:* Redis counters are plain keys (NOT Popoto-managed), so
    raw r.get/r.incr/r.delete/r.expire are permitted here (mirrors
    read_project_health_counters / session-recovery-drip).
  - Telegram alert is error-only (no all-clear spam).
"""

from __future__ import annotations

import logging
import subprocess

from config.settings import settings

logger = logging.getLogger("reflections.stall_advisory")


# Stalled reasons that the action-mode is allowed to act on. Other stalled
# reasons (e.g. kill_transition) are observed but never killed by this path.
_ACTIONABLE_STALL_REASONS = frozenset({"never_started", "idle_gap_exceeded_stall"})

# TTL on the cross-tick consecutive-observation counter (~2x the 300s reflection
# cadence) so the count decays if a session stops being reported as stalled.
_CONSEC_KEY_TTL_SECS = 700

# TTL on the per-session kill-attempt budget counter (~24h) so a long-lived
# wedge does not exhaust its budget forever from an old incident.
_BUDGET_KEY_TTL_SECS = 86400


# ---------------------------------------------------------------------------
# Module-local Redis / project-key helpers
# ---------------------------------------------------------------------------


def _get_redis():
    """Return the shared Popoto Redis connection (plain-key access)."""
    from agent.sustainability import _get_redis as _su_get_redis

    return _su_get_redis()


def _get_project_key() -> str:
    """Return the project-scoped Redis key prefix (e.g. ``valor``)."""
    from agent.sustainability import _get_project_key as _su_get_project_key

    return _su_get_project_key()


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
            summary:  Human-readable summary string (extended with recovery
                      counts when any kills/dry-runs/catchup-failures occur).
    """
    params = params or {}
    telegram_enabled = bool(params.get("stall_advisory_telegram_enabled", False))

    findings: list[dict] = []

    try:
        from agent.session_health import _is_ledger
        from agent.session_stall_classifier import (
            _RUNNING_PROBE_STATUSES,
            classify_session_stall,
        )
        from agent.session_telemetry import read_session_timeline
        from config.settings import settings
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

    # Skip terminal-status sessions (concurrent transition) AND non-executable
    # ledgers. `sdlc-local-{N}` pipeline anchors are `is_ledger=True` and by
    # design never spawn an SDK subprocess, so `classify_session_stall` returns
    # `never_started` for them — an actionable reason that would kill the ledger
    # and orphan its issue lock, deadlocking the SDLC router
    # (`ISSUE_LOCKED / orphaned_lock`). Mirror the ledger skip the health loop
    # already performs (#2042); this is the stall-path half of that guard.
    active_sessions = [
        s for s in probe_sessions if s.status not in TERMINAL_STATUSES and not _is_ledger(s)
    ]

    # Best-effort recovery context. If Redis or the project key is unavailable
    # we skip recovery entirely and preserve advisory-only behaviour.
    r = None
    project_key = None
    try:
        r = _get_redis()
        project_key = _get_project_key()
    except Exception as exc:
        logger.debug("stall_advisory: recovery context unavailable: %r", exc)

    run_state = {"killed": 0, "catchup_failed": 0}

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
            # Action-mode: only stalled findings are ever actioned, and only
            # when a Redis/project-key context is available. Fully fail-soft.
            if r is not None and project_key is not None:
                _maybe_recover(session, verdict, settings, r, project_key, run_state)
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
            # Reset the consec counter — a single slow-but-live turn must not
            # accumulate toward a kill.
            _reset_consec(r, project_key, session_id)
        else:
            healthy_count += 1
            logger.debug(
                "stall_advisory: healthy session=%s reason=%s",
                session_id,
                verdict.reason,
            )
            _reset_consec(r, project_key, session_id)

    # Build summary
    summary = (
        f"{total} running session(s): "
        f"{stalled_count} stalled, {suspect_count} suspect, {healthy_count} healthy"
    )
    if run_state["killed"] or run_state["catchup_failed"]:
        summary += (
            f"; recovery: {run_state['killed']} killed, "
            f"{run_state['catchup_failed']} catchup-failed"
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
# Action-mode recovery
# ---------------------------------------------------------------------------


def _maybe_recover(session, verdict, settings, r, project_key, run_state) -> str:
    """Run the stall-recovery gate ladder for a single stalled finding.

    Returns a short outcome string used for summary accounting:
      observed, killed, killed_catchup_failed, skipped_run_budget,
      skipped_session_budget, skipped_not_actionable, skipped_terminal, error.

    Gate ladder (issue #1768, always-on since #1855):
      1. reason not actionable                  -> skipped_not_actionable
      2. increment cross-tick consec counter (TTL ~2x cadence)
      3. consec < N                             -> observed
      4. run kill budget K exhausted            -> skipped_run_budget (set
         stall_recovery_run_budget=0 as the no-deploy break-glass to disable
         actuation entirely)
      5. per-session kill budget exhausted       -> skipped_session_budget
      6. re-read session status (Race 1); terminal -> skipped_terminal (reset consec)
         else kill via _kill_agent_session, then valor-catchup, then audit event.

    The whole body is wrapped so a recovery error never crashes the reflection.
    """
    try:
        from models.session_lifecycle import TERMINAL_STATUSES

        session_id = getattr(session, "session_id", None) or getattr(
            session, "agent_session_id", "?"
        )
        reason = getattr(verdict, "reason", None)

        # 1. Actionable-reason filter — do NOT touch any counter for ineligible
        #    stalled reasons (e.g. kill_transition).
        if reason not in _ACTIONABLE_STALL_REASONS:
            return "skipped_not_actionable"

        feat = settings.features

        # 2. Cross-tick consecutive-observation counter (atomic INCR + TTL).
        consec_key = f"{project_key}:stall-recovery:consec:{session_id}"
        consec = int(r.incr(consec_key))
        r.expire(consec_key, _CONSEC_KEY_TTL_SECS)

        # 3. Not enough consecutive observations yet — observe and return.
        if consec < feat.stall_recovery_consecutive_observations:
            logger.info(
                "[stall-recovery] observation %d/%d session=%s reason=%s",
                consec,
                feat.stall_recovery_consecutive_observations,
                session_id,
                reason,
            )
            return "observed"

        # 4. Per-run kill budget (K).
        if run_state["killed"] >= feat.stall_recovery_run_budget:
            logger.info(
                "[stall-recovery] run budget exhausted, skip session=%s reason=%s",
                session_id,
                reason,
            )
            return "skipped_run_budget"

        # 5. Per-session kill budget.
        budget_key = f"{project_key}:stall-recovery:budget:{session_id}"
        raw_budget = r.get(budget_key)
        try:
            session_budget = int(raw_budget) if raw_budget is not None else 0
        except (ValueError, TypeError):
            session_budget = 0
        if session_budget >= feat.stall_recovery_per_session_budget:
            logger.info(
                "[stall-recovery] per-session budget exhausted, skip session=%s reason=%s",
                session_id,
                reason,
            )
            return "skipped_session_budget"

        # 6. Enforce. Re-read session status to guard Race 1 (worker may have
        #    finalized the session between classification and kill).
        from models.agent_session import AgentSession

        fresh_status = None
        try:
            fresh = AgentSession.query.filter(session_id=session_id).first()
            if fresh is not None:
                fresh_status = fresh.status
        except Exception as exc:
            logger.debug(
                "[stall-recovery] could not re-read session %s status: %r",
                session_id,
                exc,
            )
            fresh = session

        if fresh_status is not None and fresh_status in TERMINAL_STATUSES:
            logger.info(
                "[stall-recovery] session=%s already terminal (%s), skip + reset",
                session_id,
                fresh_status,
            )
            _reset_consec(r, project_key, session_id)
            return "skipped_terminal"

        kill_target = fresh if fresh is not None else session

        # Kill the session (terminate PID + finalize to "killed"). Fail-soft.
        from tools.agent_session_scheduler import _kill_agent_session

        try:
            _kill_agent_session(kill_target)
        except Exception as exc:
            logger.warning(
                "[stall-recovery] kill failed for session=%s reason=%s: %r",
                session_id,
                reason,
                exc,
            )
            return "error"

        # Kill succeeded: charge the per-session budget and the per-run counter.
        try:
            r.incr(budget_key)
            r.expire(budget_key, _BUDGET_KEY_TTL_SECS)
        except Exception as exc:
            logger.debug("[stall-recovery] budget incr failed for %s: %r", session_id, exc)
        run_state["killed"] += 1

        # Re-enqueue genuinely-unanswered human messages via valor-catchup.
        # Mirror _send_alert's subprocess error handling. Catchup failure is
        # logged + counted but never fatal — the wedged session is already dead.
        catchup_ok = False
        try:
            proc = subprocess.run(
                ["valor-catchup"],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.subprocess_default_s,
                check=False,
            )
            catchup_ok = proc.returncode == 0
        except FileNotFoundError:
            logger.warning("[stall-recovery] valor-catchup not on PATH; skipping re-enqueue")
        except subprocess.TimeoutExpired:
            logger.warning("[stall-recovery] valor-catchup timed out")
        except Exception as exc:
            logger.warning("[stall-recovery] valor-catchup failed: %s", exc)

        if not catchup_ok:
            run_state["catchup_failed"] += 1

        _emit_recovery_event(
            session,
            verdict_reason=reason,
            killed=True,
            catchup_invoked=True,
            catchup_ok=catchup_ok,
            dry_run=False,
        )

        logger.warning(
            "[stall-recovery] killed+recovered session=%s reason=%s catchup_ok=%s",
            session_id,
            reason,
            catchup_ok,
        )
        return "killed" if catchup_ok else "killed_catchup_failed"
    except Exception as exc:
        logger.warning("[stall-recovery] _maybe_recover swallowed exception: %r", exc)
        return "error"


def _emit_recovery_event(
    session,
    *,
    verdict_reason,
    killed: bool,
    catchup_invoked: bool,
    catchup_ok: bool,
    dry_run: bool,
) -> None:
    """Append a typed ``stall_recovery_action`` session-event for the dashboard
    feed so a kill-succeeds-but-catchup-fails outcome is durably visible, not
    merely a WARNING log line that scrolls away. Fail-soft (never raises)."""
    try:
        from agent.session_runner.adapter import (
            _append_session_event,
            _now_iso,
        )

        verb = "WOULD kill+recover (dry-run)" if dry_run else "kill+recover"
        _append_session_event(
            session,
            {
                "type": "stall_recovery_action",
                "event_type": "stall_recovery_action",
                "text": f"[stall-recovery] {verb} reason={verdict_reason}",
                "verdict_reason": verdict_reason,
                "killed": killed,
                "catchup_invoked": catchup_invoked,
                "catchup_ok": catchup_ok,
                "dry_run": dry_run,
                "ts": _now_iso(),
            },
        )
    except Exception as exc:
        logger.debug("[stall-recovery] could not emit recovery event: %r", exc)


def _reset_consec(r, project_key, session_id) -> None:
    """Best-effort delete of the consecutive-observation counter for a session
    that recovered (classified healthy/suspect or already terminal)."""
    if r is None or project_key is None:
        return
    try:
        r.delete(f"{project_key}:stall-recovery:consec:{session_id}")
    except Exception as exc:
        logger.debug("[stall-recovery] consec reset failed for %s: %r", session_id, exc)


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
            timeout=settings.timeouts.subprocess_default_s,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("stall_advisory: valor-telegram not on PATH; skipping alert")
    except subprocess.TimeoutExpired:
        logger.warning("stall_advisory: valor-telegram timed out")
    except Exception as exc:
        logger.warning("stall_advisory: valor-telegram failed: %s", exc)
