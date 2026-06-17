"""
reflections/crash_recovery.py — Crash-signature extraction and auto-resume reflection.

Callable contract: no arguments, returns:
  {"status": "ok"|"error", "findings": [...], "summary": str}

Periodic reflection that:
1. Scans recently-terminal sessions (RESUMABLE_STATUSES) for unprocessed signatures
2. Extracts and upserts crash signatures into the library
3. Attributes outcomes for already-resumed sessions (crash_outcome_attributed idempotency)
4. In propose mode (default): logs proposals only, no resume
5. In auto mode (CRASH_AUTORESUME_ENABLED=1): resumes eligible sessions with safety gates:
   - per-session attempt cap (CRASH_AUTORESUME_MAX_ATTEMPTS, default 3)
   - global per-run budget (CRASH_AUTORESUME_RUN_BUDGET, default 5)
   - determinism guardrail: NON_RESUMABLE_DETERMINISTIC sessions are escalated, not resumed

Race 1 mitigation: if the extracted signature is the unclassifiable sentinel OR the trace
has no terminal status_transition event, skip the session and retry next tick. The SOLE
mitigation is this incomplete-retry guard — finalize_session ordering does NOT apply here.

Cross-record attribution write ordering (concern 2): to avoid double-count when a crash
happens between the library write and the session-flag write, write flag-first:
  (1) set crash_outcome_attributed = True on the session first
  (2) then record the outcome in the CrashSignature library
This gives safe under-count rather than dangerous over-count.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("reflections.crash_recovery")

# ---------------------------------------------------------------------------
# Thresholds (all overridable via env vars)
# ---------------------------------------------------------------------------

_MIN_OCCURRENCES = int(os.environ.get("CRASH_AUTORESUME_MIN_OCCURRENCES", "3"))
_MIN_SUCCESS_RATIO = float(os.environ.get("CRASH_AUTORESUME_MIN_SUCCESS_RATIO", "0.7"))
_MAX_AUTO_ATTEMPTS = int(os.environ.get("CRASH_AUTORESUME_MAX_ATTEMPTS", "3"))
_RUN_BUDGET = int(os.environ.get("CRASH_AUTORESUME_RUN_BUDGET", "5"))
_LOOKBACK_HOURS = float(os.environ.get("CRASH_AUTORESUME_LOOKBACK_HOURS", "2.0"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_terminal_status_transition(events: list[dict]) -> bool:
    """Return True if any event is a status_transition to a terminal status."""
    terminal = {"completed", "failed", "killed", "abandoned", "cancelled"}
    for evt in events:
        if evt.get("type") == "status_transition":
            data = evt.get("data") or {}
            to_status = data.get("to") or evt.get("to")
            if to_status in terminal:
                return True
    return False


# ---------------------------------------------------------------------------
# Main reflection callable
# ---------------------------------------------------------------------------


def run_crash_recovery() -> dict:
    """Main crash-recovery reflection callable.

    Returns:
        Dict with keys: status ("ok"|"error"), findings (list of strings), summary (str).
    """
    findings: list[str] = []
    auto_enabled = bool(int(os.environ.get("CRASH_AUTORESUME_ENABLED", "0") or "0"))

    try:
        from agent.crash_signature import NON_RESUMABLE_DETERMINISTIC, extract_signature
        from agent.session_telemetry import read_session_timeline
        from models.agent_session import AgentSession
        from models.crash_signature import CrashSignature
        from models.session_lifecycle import RESUMABLE_STATUSES, TERMINAL_STATUSES
        from tools.valor_session import resume_session
    except ImportError as e:
        return {"status": "error", "findings": [], "summary": f"import error: {e}"}

    try:
        from agent.session_pickup import _truthy
    except ImportError:
        # Fallback _truthy in case of import issues
        def _truthy(value: object) -> bool:  # type: ignore[misc]
            if isinstance(value, bool):
                return value
            if isinstance(value, int | float):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes"}
            return bool(value)

    # Counters for per-run summary
    processed = 0
    signatures_extracted = 0
    proposed = 0
    auto_resumed = 0
    escalated = 0
    re_crashed = 0
    run_budget_remaining = _RUN_BUDGET

    # Warm-up honesty (concern 4): log if library is cold
    try:
        all_sigs = list(CrashSignature.query.filter())
        if not all_sigs:
            logger.info(
                "crash-signature library is cold (no signatures yet) — "
                "warm-up will take several reflection ticks"
            )
    except Exception:
        pass

    # Scan recently-terminal sessions
    cutoff = datetime.now(UTC) - timedelta(hours=_LOOKBACK_HOURS)

    try:
        all_resumable = list(AgentSession.query.filter(status__in=list(RESUMABLE_STATUSES)))
    except Exception as e:
        logger.warning("crash_recovery: failed to query resumable sessions: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"crash-recovery query error: {e}",
        }

    # Filter to recently-updated sessions.
    # Popoto's DatetimeField round-trips values as NAIVE datetimes (tzinfo
    # stripped on read), so normalize to UTC before comparing against the
    # tz-aware cutoff — otherwise every comparison raises TypeError and the
    # session is silently dropped, leaving the reflection a no-op.
    recent = []
    for s in all_resumable:
        try:
            updated = getattr(s, "updated_at", None)
            if updated is None:
                continue
            if getattr(updated, "tzinfo", None) is None:
                updated = updated.replace(tzinfo=UTC)
            if updated > cutoff:
                recent.append(s)
        except Exception as e:
            logger.warning(
                "crash_recovery: skipping session %s — bad updated_at %r: %s",
                getattr(s, "session_id", "?"),
                getattr(s, "updated_at", None),
                e,
            )

    # --- Phase 1: Attribute outcomes for sessions that were resumed ---
    # These sessions have crash_signature set but crash_outcome_attributed not set,
    # AND are now terminal again (completed or re-crashed).
    needs_attribution = [
        s
        for s in recent
        if getattr(s, "crash_signature", None)
        and not _truthy(getattr(s, "crash_outcome_attributed", None))
        and s.status in TERMINAL_STATUSES
    ]

    for session in needs_attribution:
        try:
            outcome = "recovered" if session.status == "completed" else "crashed_again"
            if outcome == "crashed_again":
                re_crashed += 1

            sig_hash = session.crash_signature

            # Flag-first ordering (concern 2): safe under-count over dangerous over-count.
            # Write the attribution flag first; if we crash between these two writes,
            # the library misses one outcome but the session won't be double-counted.
            session.crash_outcome_attributed = True
            session.save()

            # Then update the library record
            sig_record = CrashSignature.get_by_hash(sig_hash)
            if sig_record:
                sig_record.record_outcome("auto_resume", recovered=(outcome == "recovered"))
                logger.info(
                    "attributed outcome=%s to signature=%s for session=%s",
                    outcome,
                    sig_hash,
                    session.session_id,
                )
                findings.append(
                    f"attributed: session={session.session_id} outcome={outcome} sig={sig_hash}"
                )

            processed += 1
        except Exception as e:
            logger.warning(
                "crash_recovery: attribution failed for session %s: %s",
                getattr(session, "session_id", "?"),
                e,
            )

    # --- Phase 2: Extract signatures for freshly-terminal sessions ---
    # These sessions have no crash_signature set and are in RESUMABLE_STATUSES.
    fresh_terminal = [
        s
        for s in recent
        if not getattr(s, "crash_signature", None)
        and s.status in RESUMABLE_STATUSES
        and not _truthy(getattr(s, "crash_outcome_attributed", None))
    ]

    for session in fresh_terminal:
        session_id = getattr(session, "session_id", "?")
        try:
            # Read telemetry events for this session
            events = read_session_timeline(session_id)

            # Race 1 mitigation (concern 1): if no events or no terminal status_transition,
            # the session may still be finalizing — skip and retry next tick.
            # Do NOT claim finalize_session ordering as mitigation — it never touches DB status.
            if not events or not _has_terminal_status_transition(events):
                logger.debug(
                    "crash_recovery: session %s has no terminal status_transition yet — "
                    "skipping, will retry next tick",
                    session_id,
                )
                continue

            # Extract the crash signature
            sig = extract_signature(events, session=session)

            # Skip unclassifiable signatures — retry next tick (Race 1 mitigation)
            if sig.human_form == "unclassifiable":
                logger.debug(
                    "crash_recovery: session %s signature is unclassifiable — "
                    "skipping, will retry next tick",
                    session_id,
                )
                continue

            # Upsert into the library
            sig_record = CrashSignature.get_or_create_by_hash(
                sig.hash,
                human_form=sig.human_form,
                signature_class=sig.signature_class,
                resumable=sig.resumable,
            )
            sig_record.upsert_occurrence(
                session_id,
                terminal_status=session.status,
                has_uuid=bool(getattr(session, "claude_session_uuid", None)),
                project_key=getattr(session, "project_key", None),
            )
            signatures_extracted += 1
            processed += 1

            logger.info(
                "extracted signature=%s class=%s resumable=%s for session=%s",
                sig.human_form,
                sig.signature_class,
                sig.resumable,
                session_id,
            )
            findings.append(
                f"extracted: session={session_id} sig={sig.human_form} class={sig.signature_class}"
            )

            # Determinism guardrail: never auto-resume NON_RESUMABLE_DETERMINISTIC
            if sig.signature_class == NON_RESUMABLE_DETERMINISTIC:
                logger.warning(
                    "[ESCALATE] NON_RESUMABLE_DETERMINISTIC crash detected — "
                    "session=%s signature=%s class=%s captured_frame=%s",
                    session_id,
                    sig.human_form,
                    sig.signature_class,
                    getattr(session, "startup_captured_frame", None),
                )
                escalated += 1
                sig_record.escalated = True
                sig_record.save()
                findings.append(f"escalated: session={session_id} sig={sig.human_form}")
                continue

            # Confidence gate (Risk 1 mitigation / core safety requirement):
            # never auto-resume a signature that has not earned statistical
            # confidence. A signature must clear MIN_OCCURRENCES and the
            # per-strategy MIN_SUCCESS_RATIO before the policy promotes it to
            # auto-eligible. Until then this is propose-mode: we observed the
            # pattern but lack the confidence to act on it. The strategy name
            # "auto_resume" matches record_outcome/policy_confidence usage above
            # and in models/crash_signature.py.
            if not sig_record.is_auto_eligible(
                strategy="auto_resume",
                min_occurrences=_MIN_OCCURRENCES,
                min_success_ratio=_MIN_SUCCESS_RATIO,
            ):
                proposed += 1
                logger.info(
                    "propose-mode: signature=%s not yet auto-eligible "
                    "(occurrences=%d < %d or confidence=%.2f < %.2f) — "
                    "observed but not resuming session=%s",
                    sig.human_form,
                    sig_record.occurrence_count_int,
                    _MIN_OCCURRENCES,
                    sig_record.policy_confidence("auto_resume"),
                    _MIN_SUCCESS_RATIO,
                    session_id,
                )
                findings.append(
                    f"proposed: session={session_id} sig={sig.human_form} (below-confidence)"
                )
                continue

            # Resume-eligible path
            if auto_enabled and run_budget_remaining > 0:
                # Check per-session attempt cap
                session_attempts = getattr(session, "auto_resume_attempts", None)
                try:
                    attempt_count = int(session_attempts or 0)
                except (TypeError, ValueError):
                    attempt_count = 0

                if attempt_count >= _MAX_AUTO_ATTEMPTS:
                    logger.warning(
                        "max auto-resume attempts (%d) reached for session %s "
                        "(signature %s); leaving terminal for human",
                        _MAX_AUTO_ATTEMPTS,
                        session_id,
                        sig.human_form,
                    )
                    findings.append(f"max-attempts-hit: {session_id}")
                    continue

                # Re-read status to prevent race with recovery mechanisms (#1537)
                fresh_sessions = list(AgentSession.query.filter(session_id=session_id))
                if not fresh_sessions:
                    logger.debug(
                        "crash_recovery: session %s disappeared before auto-resume",
                        session_id,
                    )
                    continue
                # Pick newest by created_at if duplicates
                fresh_sessions.sort(key=lambda s: getattr(s, "created_at", 0) or 0, reverse=True)
                fresh_session = fresh_sessions[0]

                if fresh_session.status not in RESUMABLE_STATUSES:
                    logger.debug(
                        "crash_recovery: session %s status changed to %s before auto-resume — "
                        "skipping (already handled)",
                        session_id,
                        fresh_session.status,
                    )
                    continue

                result = resume_session(fresh_session, "continue", source="auto-resume")
                if result.success:
                    # Tag the resumed session with the crash signature for outcome attribution
                    fresh_session.crash_signature = sig.hash
                    fresh_session.auto_resume_attempts = str(attempt_count + 1)
                    fresh_session.save()
                    run_budget_remaining -= 1
                    auto_resumed += 1
                    logger.info(
                        "auto-resumed session=%s signature=%s attempt=%d",
                        session_id,
                        sig.human_form,
                        attempt_count + 1,
                    )
                    findings.append(
                        f"auto-resumed: session={session_id} sig={sig.human_form} "
                        f"attempt={attempt_count + 1}"
                    )
                else:
                    logger.warning(
                        "auto-resume failed: session=%s error=%s",
                        session_id,
                        result.error,
                    )
                    findings.append(f"auto-resume-failed: {session_id} — {result.error}")
            else:
                # Propose mode: log the proposal without acting
                proposed += 1
                logger.info(
                    "propose-mode: would resume session=%s signature=%s (auto_enabled=%s, "
                    "budget_remaining=%d)",
                    session_id,
                    sig.human_form,
                    auto_enabled,
                    run_budget_remaining,
                )
                findings.append(f"proposed: session={session_id} sig={sig.human_form}")

        except Exception as e:
            logger.warning(
                "crash_recovery: processing failed for session %s: %s",
                session_id,
                e,
                exc_info=True,
            )

    # Per-run summary (concern 3 — misfiring signal)
    summary = (
        f"crash-recovery run complete: processed={processed}, "
        f"signatures_extracted={signatures_extracted}, proposed={proposed}, "
        f"auto_resumed={auto_resumed}, escalated={escalated}, re_crashed={re_crashed}"
    )
    logger.info(summary)
    findings.append(summary)

    return {"status": "ok", "findings": findings, "summary": summary}
