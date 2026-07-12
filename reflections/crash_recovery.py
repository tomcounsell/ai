"""
reflections/crash_recovery.py — Crash-signature extraction and auto-resume reflection.

Callable contract: no arguments, returns:
  {"status": "ok"|"error", "findings": [...], "summary": str}

Periodic reflection that:
1. Scans recently-terminal sessions (RESUMABLE_STATUSES) for unprocessed signatures
2. Extracts and upserts crash signatures into the library
3. Attributes outcomes for already-resumed sessions (crash_outcome_attributed idempotency)
4. In propose mode (default): logs proposals only, no resume
5. In auto mode (FEATURES__CRASH_AUTORESUME_ENABLED=1): resumes eligible sessions with safety gates:
   - machine-ownership gate: only the machine that owns the session's project
     (projects.<key>.machine == computer_name()) resumes it; others propose (Gap 3b)
   - deterministic floor (Gap 3a): a confirmed-dead clean-kill-to-`failed`
     signature is permitted a bounded first retry ahead of statistical warm-up
     (settings.features.crash_autoresume_deterministic_floor_attempts; default 1, 0 disables)
   - per-session attempt cap (settings.features.crash_autoresume_max_attempts, default 3)
   - global per-run budget (settings.features.crash_autoresume_run_budget, default 5)
   - determinism guardrail: NON_RESUMABLE_DETERMINISTIC sessions are escalated, not resumed

The enable flag and all four thresholds are read from the pydantic settings
object (config.settings.settings.features) at RUN TIME inside
run_crash_recovery() — env prefix FEATURES__. The lookback window
(CRASH_AUTORESUME_LOOKBACK_HOURS) has no settings field and is still env-read
at run time.

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
from pathlib import Path

logger = logging.getLogger("reflections.crash_recovery")


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


def _is_transient_clean_kill_to_failed(events: list[dict]) -> bool:
    """Return True if the terminal transition is a confirmed-dead clean kill to ``failed``.

    This is the known-transient tool-wedge shape the deterministic floor acts on:
    the worker detected a wedged tool call, killed the subprocess with a confirmed
    death, and finalized the session ``failed``. Derived **inline** here from the
    last ``status_transition`` in the ``events`` list (critique C4) rather than
    threading a ``transient_kind`` field through the shared ``CrashSignatureKey``
    dataclass. Mirrors the ``kill.confirmed_dead`` / ``to`` read shown in
    ``agent/crash_signature.py::_normalize_status_transition``.

    Fail-soft: any malformed event → False (no floor, fall to statistical gating).
    """
    last_transition: dict | None = None
    for evt in events:
        if evt.get("type") == "status_transition":
            last_transition = evt
    if last_transition is None:
        return False
    try:
        data = last_transition.get("data") or {}
        to_status = data.get("to") or last_transition.get("to")
        if to_status != "failed":
            return False
        kill_info = data.get("kill") or last_transition.get("kill")
        if not isinstance(kill_info, dict):
            return False
        return str(kill_info.get("confirmed_dead", "")).lower() == "true"
    except Exception as exc:  # noqa: BLE001
        logger.debug("_is_transient_clean_kill_to_failed swallowed exception: %r", exc)
        return False


def _machine_owns_project(project_key: str | None) -> bool:
    """Return True if THIS machine owns ``project_key`` per ``projects.json``.

    Single-machine invariant (Gap 3b): auto-resume acts on a session only when
    ``projects.<project_key>.machine == computer_name()``. Making ownership
    structural (rather than relying on the operator setting the env flag on
    exactly one box) means exactly one machine resumes a given session even if
    the flag is on fleet-wide.

    Fail-soft: an unresolvable / unknown / missing ``project_key`` → False
    (treated as not-owned, so the reflection falls to propose-only — the safe
    default). Any lookup error is swallowed and returns False.
    """
    if not project_key:
        return False
    try:
        from config.machine import get_machine_name
        from tools.reflection_machine_filter import _load_project_machines

        projects_path = Path(__file__).resolve().parent.parent / "config" / "projects.json"
        owners = _load_project_machines(projects_path)
        owner = owners.get(project_key)
        if not owner:
            return False
        return owner == get_machine_name().strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.debug("_machine_owns_project swallowed exception: %r", exc)
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

    # Read enable flag + thresholds from the pydantic settings object at RUN
    # TIME (not import time). The documented config surface is the
    # FEATURES__CRASH_AUTORESUME_* env prefix -> settings.features.*; reading
    # them here is the single source of truth. The lookback window has no
    # settings field, so it is still env-read (run-time) below.
    from config.settings import settings

    auto_enabled = bool(settings.features.crash_autoresume_enabled)
    min_occurrences = int(settings.features.crash_autoresume_min_occurrences)
    min_success_ratio = float(settings.features.crash_autoresume_min_success_ratio)
    max_auto_attempts = int(settings.features.crash_autoresume_max_attempts)
    run_budget = int(settings.features.crash_autoresume_run_budget)
    floor_attempts = int(settings.features.crash_autoresume_deterministic_floor_attempts)
    lookback_hours = float(os.environ.get("CRASH_AUTORESUME_LOOKBACK_HOURS", "2.0"))

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
    run_budget_remaining = run_budget

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
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

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
                    "session=%s signature=%s class=%s",
                    session_id,
                    sig.human_form,
                    sig.signature_class,
                )
                escalated += 1
                sig_record.escalated = True
                sig_record.save()
                findings.append(f"escalated: session={session_id} sig={sig.human_form}")
                continue

            # Per-session attempt count (read once; drives both the deterministic
            # floor eligibility check and the max-attempts cap below).
            session_attempts = getattr(session, "auto_resume_attempts", None)
            try:
                attempt_count = int(session_attempts or 0)
            except (TypeError, ValueError):
                attempt_count = 0

            # Deterministic first-retry floor (Gap 3a): a confirmed-dead
            # clean-kill-to-`failed` signature (the known-transient tool-wedge
            # shape) is permitted a bounded first retry ahead of statistical
            # warm-up, so a cold library still self-heals the exact current
            # failure mode. The transient shape is derived INLINE from the
            # terminal status_transition in `events` (critique C4 — no
            # `transient_kind` field threaded through the shared CrashSignatureKey
            # dataclass). Bounded by the attempt cap + run budget below; setting
            # crash_autoresume_deterministic_floor_attempts to 0 disables it and
            # restores pure statistical gating.
            floor_eligible = (
                floor_attempts > 0
                and attempt_count < floor_attempts
                and _is_transient_clean_kill_to_failed(events)
            )

            # Confidence gate (Risk 1 mitigation / core safety requirement):
            # never auto-resume a signature that has not earned statistical
            # confidence UNLESS the deterministic floor permits it. A signature
            # must clear MIN_OCCURRENCES and the per-strategy MIN_SUCCESS_RATIO
            # before the policy promotes it to auto-eligible. Until then this is
            # propose-mode: we observed the pattern but lack the confidence to
            # act on it. The strategy name "auto_resume" matches
            # record_outcome/policy_confidence usage above and in
            # models/crash_signature.py.
            statistically_eligible = sig_record.is_auto_eligible(
                strategy="auto_resume",
                min_occurrences=min_occurrences,
                min_success_ratio=min_success_ratio,
            )
            if not statistically_eligible and not floor_eligible:
                proposed += 1
                logger.info(
                    "propose-mode: signature=%s not yet auto-eligible "
                    "(occurrences=%d < %d or confidence=%.2f < %.2f) and no "
                    "deterministic floor — observed but not resuming session=%s",
                    sig.human_form,
                    sig_record.occurrence_count_int,
                    min_occurrences,
                    sig_record.policy_confidence("auto_resume"),
                    min_success_ratio,
                    session_id,
                )
                findings.append(
                    f"proposed: session={session_id} sig={sig.human_form} (below-confidence)"
                )
                continue

            # Resume-eligible path (statistical eligibility OR deterministic floor)
            if auto_enabled and run_budget_remaining > 0:
                # Machine-ownership gate (Gap 3b): exactly one machine resumes a
                # given session. Even with FEATURES__CRASH_AUTORESUME_ENABLED on
                # fleet-wide, only the machine that owns the session's project
                # (projects.<key>.machine == computer_name()) acts; every other
                # machine falls to propose. Unowned / unknown project_key →
                # not-owned (safe: propose only). This makes the single-machine
                # invariant structural rather than relying on the operator
                # setting the flag on exactly one box.
                if not _machine_owns_project(getattr(session, "project_key", None)):
                    proposed += 1
                    logger.info(
                        "propose-mode: this machine does not own project=%s for "
                        "session=%s — not resuming (single-machine invariant)",
                        getattr(session, "project_key", None),
                        session_id,
                    )
                    findings.append(
                        f"proposed: session={session_id} sig={sig.human_form} (not-owner)"
                    )
                    continue

                # Check per-session attempt cap
                if attempt_count >= max_auto_attempts:
                    logger.warning(
                        "max auto-resume attempts (%d) reached for session %s "
                        "(signature %s); leaving terminal for human",
                        max_auto_attempts,
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
                        "auto-resumed session=%s signature=%s attempt=%d floor=%s",
                        session_id,
                        sig.human_form,
                        attempt_count + 1,
                        floor_eligible and not statistically_eligible,
                    )
                    findings.append(
                        f"auto-resumed: session={session_id} sig={sig.human_form} "
                        f"attempt={attempt_count + 1}"
                    )
                else:
                    # Failure-path convergence (critique C1): a failed resume must
                    # still consume an attempt. auto_resume_attempts was only
                    # advanced on the success path, so a persistently-failing
                    # resume_session (missing UUID → refusal; transition raises)
                    # re-satisfied `attempt_count < floor` (0 < 1) every 300s tick
                    # forever — the floor's boundedness was a lie on this path.
                    # Advance the counter here too, mirroring the success-path
                    # lines above, so it converges to the max-attempts guard.
                    fresh_session.auto_resume_attempts = str(attempt_count + 1)
                    fresh_session.save()
                    logger.warning(
                        "auto-resume failed: session=%s error=%s (attempt %d consumed)",
                        session_id,
                        result.error,
                        attempt_count + 1,
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
