"""Session watchdog - detect and fix stuck agent sessions.

Monitors active agent sessions for signs of distress:
- Silent sessions (no activity for extended period)
- Looping behavior (repeated identical tool calls)
- Error cascades (high error rate in recent activity)
- Excessively long sessions
- Cumulative per-session token spend crossing a soft threshold (issue #1128)

When issues are detected, the watchdog FIXES them automatically:
- Retries stalled sessions with exponential backoff (up to MAX_STALL_RETRIES)
- Marks stuck sessions as abandoned after retries exhausted
- Creates GitHub issues for problems that can't be auto-fixed
- Notifies human via Telegram after max retries exhausted
- For looping / error-cascade / token-alert conditions (issue #1128):
  AUTOMATICALLY enqueues a steering message via `_inject_watchdog_steer`.
  Per-reason atomic Redis cooldowns (SET NX EX) prevent floods. No longer
  "detected but logged only" — detections now drive actuation.
- For stalled sessions with an originating Telegram message (issue #1313):
  AUTOMATICALLY queues a ⏳ reaction emoji on the user's original message
  via `_apply_stall_reaction`. Atomic `SET NX EX` dedup key per session
  prevents repeats within a stall period. Reset on healthy-state observation
  so re-stalls trigger a fresh reaction.

NO ALERTS ARE SENT for recoverable stalls. Either retry, fix, or create an issue.

**Process topology**: This watchdog runs as a SEPARATE process from the
worker. The `_active_clients` SDK-client registry and its idle-teardown
sweeper (`worker/idle_sweeper.py`) were deleted (plan #2000 Task 2.2 --
the CLI harness spawns a short-lived subprocess per turn and has no
persistent client to go stale).
"""

import asyncio
import json
import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from popoto.exceptions import ModelException

from config.settings import settings
from models.agent_session import AgentSession


def _to_timestamp(val) -> float | None:
    """Convert a datetime or float to a Unix timestamp.

    Naive datetimes are assumed to represent UTC (matching how Popoto
    SortedField stores them). This prevents local-time interpretation
    on machines running in non-UTC timezones, which would otherwise
    inflate durations by the UTC offset and trigger false LIFECYCLE_STALL
    events for newly-created sessions.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    return None


logger = logging.getLogger(__name__)

# Watchdog configuration constants
WATCHDOG_INTERVAL = 300  # 5 minutes in seconds
SILENCE_THRESHOLD = 600  # 10 minutes
LOOP_THRESHOLD = 5  # identical tool calls to trigger
ERROR_CASCADE_THRESHOLD = 5  # errors in last 20 calls
ERROR_CASCADE_WINDOW = 20
DURATION_THRESHOLD = 7200  # 2 hours
ABANDON_THRESHOLD = 1800  # 30 minutes silent = auto-abandon

# Stall detection thresholds (seconds)
STALL_THRESHOLD_PENDING = 300  # 5 minutes
STALL_THRESHOLD_RUNNING = 2700  # 45 minutes
# STALL_TIMEOUT_SECONDS env var overrides the default active session stall threshold
STALL_THRESHOLD_ACTIVE = int(os.environ.get("STALL_TIMEOUT_SECONDS", 600))  # 10 min default

STALL_THRESHOLDS = {
    "pending": STALL_THRESHOLD_PENDING,
    "running": STALL_THRESHOLD_RUNNING,
    "active": STALL_THRESHOLD_ACTIVE,
}

# === Loop-break + token-alert steering (issue #1128) ===
# Soft-threshold on cumulative tokens (sum of input + output) for the
# token-alert steer. Default 5,000,000 ≈ $75 at Sonnet rates. Taken from
# AgentSession.total_input_tokens + total_output_tokens (written by
# agent/sdk_client.py::accumulate_session_tokens — this file only READS).
TOKEN_ALERT_THRESHOLD = int(os.environ.get("WATCHDOG_TOKEN_ALERT_THRESHOLD", "5000000"))
# Cooldown TTL for the token-alert steer (per session). 3600s = one alert
# per hour per session — generous enough for a human to respond between
# repeats, aggressive enough to avoid silent runaway.
TOKEN_ALERT_COOLDOWN = int(os.environ.get("WATCHDOG_TOKEN_ALERT_COOLDOWN", "3600"))
# Cooldown TTL for repetition + error-cascade steers (per session per
# reason). 900s = 3 watchdog ticks; gives the agent room to respond to a
# steer before the next one fires.
STEER_COOLDOWN = int(os.environ.get("WATCHDOG_STEER_COOLDOWN", "900"))

# === User-visible stall alert (issue #1313) ===
# Reaction emoji queued on the originating Telegram message when a session
# is observed stalled. ⏳ chosen for "stalled / waiting too long"; visually
# distinct from existing bridge reactions (👀 received, 🔥 drafting, 👍 done).
STALL_REACTION_EMOJI = "⏳"
# Dedup TTL: the reaction is queued at most once per session per stall
# period. 1 day is well over any realistic stall lifetime; the key is also
# explicitly DELETEd when the session is observed in a healthy state, so
# this TTL is just a safety bound for orphaned keys.
STALL_REACTION_DEDUP_TTL = 86400
# OUTBOX TTL: matches `agent/output_handler.py::OutputHandler.OUTBOX_TTL`
# (3600s) — the bridge's reaction relay drains the same key with this TTL
# applied via EXPIRE on each rpush.
STALL_REACTION_OUTBOX_TTL = 3600


# === Fix #5 (#1821): out-of-domain worker liveness + slot recovery ===
#
# The bridge process reads the worker's Redis-published loop beacon + lease
# snapshot (published by agent/session_health.py) and drives a restart-free,
# targeted slot reclamation via a Redis reclaim-request that the worker's
# on-loop reap pass drains. This watchdog NEVER kills the worker — all process
# recovery stays with the dead-man's-switch + monitoring/worker_watchdog.py.
#
# Config location (not a defect): raw os.environ.get() at module scope, matching
# the sibling #1815/#1820 threshold constants. Values mirror the worker-side
# constants in agent/session_health.py (each process reads env independently).
WORKER_LOOP_BEACON_KEY_PREFIX = "worker:loop_beacon:"
WORKER_SLOT_LEASES_KEY_PREFIX = "worker:slot:leases:"
WORKER_SLOT_RECLAIM_REQUESTS_KEY_PREFIX = "worker:slot:reclaim_requests:"
WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX = "worker:slot:reclaim_dedup:"
WORKER_WATCHDOG_ACTIONS_KEY_PREFIX = "worker:watchdog:actions:"
# Beacon-freshness threshold: a beacon whose wall_ts is older than this (or a
# missing beacon) reads as loop_wedged. Wall-clock ONLY — never the advisory
# monotonic loop_beacon_age_s (Risk 1). Default 90s (matches the #1815 deadman).
# Single-sourced in agent/session_health.py alongside the beacon publisher and
# the shared worker_loop_beacon_fresh() reader — imported here, never re-read
# from the environment in two places (#1312 extraction).
from agent.session_health import (  # noqa: E402
    BRIDGE_WORKER_BEACON_STALE_S,
    worker_loop_beacon_fresh,
)

# Master gate for the reclaim-request TRIGGER. Default ON — detection/logging
# always runs; only the reclaim-request push is gated. Falsy → detect/log only.
# (Uses the _env_flag_enabled helper below at call time.)
BRIDGE_SLOT_RECLAIM_ENABLED_VAR = "BRIDGE_SLOT_RECLAIM_ENABLED"
# Race 4: cap the reclaim-request list so a multi-owner leak burst cannot grow
# it unboundedly. Mirrors the worker-side default.
RECLAIM_REQUESTS_MAX = int(os.environ.get("RECLAIM_REQUESTS_MAX", "256"))
WORKER_WATCHDOG_ACTIONS_MAX = 256
# Per-owner reclaim-request dedup TTL (a few bridge ticks) so we do not re-push
# the same owner every tick while the worker drains it. Cleared on a healthy tick.
RECLAIM_REQUEST_DEDUP_TTL = 900
# TTL for the reclaim-request list + action log so a dead worker's backlog expires.
WORKER_SLOT_KEY_TTL_SECONDS = 900


# Transcript liveness: if transcript.txt was modified within this many minutes,
# the session is considered alive (doing sub-agent work) even if updated_at
# in Redis is stale. See issue #360.
TRANSCRIPT_STALE_THRESHOLD_MIN = 15

# Default logs/sessions directory for transcript liveness checks
_PROJECT_DIR = Path(__file__).parent.parent
_DEFAULT_LOGS_DIR = _PROJECT_DIR / "logs" / "sessions"


def _check_transcript_liveness(
    session_id: str,
    logs_dir: Path | None = None,
) -> tuple[bool, float]:
    """Check if a session's transcript file has been recently modified.

    Uses os.path.getmtime() on logs/sessions/{session_id}/transcript.txt
    to determine if the session is actively doing work (e.g., sub-agent calls)
    even when the Redis updated_at field hasn't been updated.

    Args:
        session_id: The session ID to check.
        logs_dir: Override for the logs/sessions directory (used in tests).

    Returns:
        Tuple of (is_stale, stale_minutes):
        - is_stale: True if transcript is older than TRANSCRIPT_STALE_THRESHOLD_MIN
          or doesn't exist.
        - stale_minutes: How many minutes since the transcript was last modified.
          Returns float('inf') if the file doesn't exist.
    """
    base_dir = logs_dir if logs_dir is not None else _DEFAULT_LOGS_DIR
    transcript_path = base_dir / session_id / "transcript.txt"

    if not transcript_path.exists():
        return (True, float("inf"))

    try:
        mtime = os.path.getmtime(transcript_path)
        age_seconds = time.time() - mtime
        stale_minutes = age_seconds / 60.0
        is_stale = stale_minutes >= TRANSCRIPT_STALE_THRESHOLD_MIN
        return (is_stale, stale_minutes)
    except OSError:
        # File disappeared between exists() check and getmtime()
        return (True, float("inf"))


async def watchdog_loop(telegram_client=None) -> None:
    """Run the watchdog monitoring loop indefinitely.

    Args:
        telegram_client: Telegram client (kept for API compatibility, not used for alerts)

    This function never returns - it runs forever checking sessions
    at regular intervals. All exceptions are caught and logged to
    prevent the watchdog from crashing.
    """
    logger.info("[watchdog] Session watchdog started (interval=%ds)", WATCHDOG_INTERVAL)

    while True:
        try:
            await check_all_sessions()
        except Exception as e:
            logger.error("[watchdog] Error in watchdog loop: %s", e, exc_info=True)

        try:
            check_stalled_sessions()
        except Exception as e:
            logger.error("[watchdog] Error in stall check: %s", e, exc_info=True)

        try:
            check_worker_liveness_and_slots()
        except Exception as e:
            logger.error("[watchdog] Error in worker liveness/slot check: %s", e, exc_info=True)

        await asyncio.sleep(WATCHDOG_INTERVAL)


async def check_all_sessions() -> None:
    """Check all active sessions for health issues and fix them.

    Queries all active sessions, assesses their health, and takes action:
    - Stuck/silent sessions: marked as abandoned
    - Unfixable issues: GitHub issue created

    NO ALERTS ARE SENT. Either fix it or create an issue.
    """
    try:
        active_sessions = list(AgentSession.query.filter(status="active"))
    except Exception as e:
        logger.error("[watchdog] Failed to query active sessions: %s", e)
        return

    healthy_count = 0
    fixed_count = 0

    for session in active_sessions:
        try:
            assessment = assess_session_health(session)

            if assessment["healthy"]:
                healthy_count += 1
            else:
                # Fix the problem instead of alerting
                fixed = await fix_unhealthy_session(session, assessment)
                if fixed:
                    fixed_count += 1
                    logger.info(
                        "[watchdog] Fixed session %s: %s",
                        session.session_id,
                        ", ".join(assessment["issues"]),
                    )
        except ModelException as e:
            # CRASH GUARD: Stale sessions left behind by SDK crashes can have
            # duplicate Redis keys or corrupted state. When the watchdog tries
            # to save/update them, popoto raises ModelException (e.g. unique
            # constraint violations). We catch all ModelException variants and
            # mark the session as failed to prevent the watchdog from looping
            # on it every cycle. See nudge loop related guards.
            try:
                from models.session_lifecycle import finalize_session

                # Capture exception details so the reflections system can produce
                # actionable bug reports instead of "empty error summary" issues.
                session.summary = f"Watchdog: {type(e).__name__}: {e}"[:500]
                finalize_session(
                    session,
                    "failed",
                    reason=f"watchdog: stale session ({type(e).__name__})",
                    skip_auto_tag=True,
                    skip_checkpoint=True,
                )
                logger.warning(
                    "[watchdog] Marked stale session %s as failed (%s)",
                    session.session_id,
                    e,
                )
            except Exception as finalize_err:
                logger.warning(
                    "[watchdog] Failed to finalize stale session %s: %s",
                    session.session_id,
                    finalize_err,
                )
        except Exception as e:
            logger.error(
                "[watchdog] Error handling session %s: %s",
                session.session_id,
                e,
                exc_info=True,
            )

    if fixed_count > 0:
        logger.info(
            "[watchdog] Checked %d active sessions: %d healthy, %d fixed",
            len(active_sessions),
            healthy_count,
            fixed_count,
        )
    else:
        logger.debug(
            "[watchdog] Checked %d active sessions: all healthy",
            len(active_sessions),
        )


def check_stalled_sessions() -> list[dict]:
    """Check for sessions that appear stalled based on status-specific thresholds.

    Queries all sessions with status in (pending, running, active) and checks
    how long they've been in that state. Uses started_at (falling back
    to created_at) as the reference timestamp.

    For active sessions, also checks updated_at -- if updated_at is recent
    (within the active threshold), the session is not considered stalled.

    Thresholds:
        - pending > 300s (5 min) = stalled
        - running > 2700s (45 min) = stalled
        - active with no recent updated_at > 600s (10 min) = stalled

    Returns:
        List of dicts for stalled sessions, each containing:
        session_id, status, duration, threshold, project_key, last_history.
    """
    stalled: list[dict] = []
    now = time.time()

    for status_val in ("pending", "running", "active"):
        try:
            sessions = list(AgentSession.query.filter(status=status_val))
        except Exception as e:
            logger.error(
                "[watchdog] Failed to query %s sessions for stall check: %s",
                status_val,
                e,
            )
            continue

        threshold = STALL_THRESHOLDS[status_val]

        for session in sessions:
            try:
                session_id = session.session_id or session.agent_session_id or "unknown"

                # Determine reference timestamp based on status
                ref_time = (
                    _to_timestamp(session.started_at) or _to_timestamp(session.created_at) or now
                )

                # For active sessions, use updated_at as reference. (The
                # in-memory sdk_client activity tracker this used to
                # cross-check was SDK-loop-only -- populated exclusively by
                # the now-deleted ValorAgent query loop, never by the CLI
                # harness path -- so it was already a permanent no-op for
                # every CLI-harness production session before its removal;
                # plan #2000 Task 2.2.)
                if status_val == "active":
                    updated_at_ts = _to_timestamp(session.updated_at)

                    if updated_at_ts is not None:
                        # If updated_at is recent, session is not stalled
                        activity_age = now - updated_at_ts
                        if activity_age < threshold:
                            continue
                        # Use updated_at as the reference for duration
                        ref_time = updated_at_ts

                    # Transcript liveness check (issue #360): even if updated_at
                    # is stale, the session may be alive doing sub-agent work.
                    # Check transcript.txt mtime before declaring stalled.
                    transcript_stale, transcript_age_min = _check_transcript_liveness(session_id)
                    if not transcript_stale:
                        logger.debug(
                            "[watchdog] Session %s has fresh transcript "
                            "(%.1f min old), skipping stall detection",
                            session_id,
                            transcript_age_min,
                        )
                        continue

                duration = now - ref_time

                if duration > threshold:
                    # Get last history entry for diagnostic context
                    last_history = "no history"
                    try:
                        if hasattr(session, "_get_history_list"):
                            history = session._get_history_list()
                            if history:
                                last_history = str(history[-1])[:120]
                    except Exception:  # noqa: S110 -- best-effort diagnostic context
                        pass

                    stalled_info = {
                        "session_id": session_id,
                        "status": status_val,
                        "duration": duration,
                        "threshold": threshold,
                        "project_key": getattr(session, "project_key", "?"),
                        "last_history": last_history,
                    }
                    stalled.append(stalled_info)

                    logger.warning(
                        "LIFECYCLE_STALL session=%s status=%s duration=%.0fs "
                        "threshold=%ds project=%s last_history=%s",
                        session_id,
                        status_val,
                        duration,
                        threshold,
                        getattr(session, "project_key", "?"),
                        last_history,
                    )

                    # Issue #1313: queue a user-visible ⏳ reaction on the
                    # originating Telegram message. Idempotent within the
                    # stall period; cleared below when the session recovers.
                    _apply_stall_reaction(session)
                else:
                    # Session observed in a healthy (non-stall) state. Clear
                    # the dedup key so a future re-stall triggers a fresh
                    # reaction. Cheap no-op when the key doesn't exist.
                    _clear_stall_reaction_dedup(session_id)
            except Exception as e:
                logger.error("[watchdog] Error checking session for stall: %s", e)

    if stalled:
        logger.warning(
            "[watchdog] %d stalled session(s) detected: %s",
            len(stalled),
            ", ".join(s["session_id"] for s in stalled),
        )

    return stalled


def _env_flag_enabled(var_name: str, default: bool = True) -> bool:
    """Return True unless the env var is explicitly set to a falsy string.

    Watchdog-hardening feature gates (issue #1128). Falsy (case-insensitive):
    "0", "false", "no". Anything else — including unset — means enabled.
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def _inject_watchdog_steer(
    session_id: str,
    reason: str,
    message: str,
    cooldown_seconds: int = STEER_COOLDOWN,
) -> bool:
    """Enqueue a watchdog-authored steering message, guarded by a per-reason cooldown.

    This is the single actuator for all three watchdog steering triggers
    introduced in issue #1128:

      * ``repetition`` — `detect_repetition` fired on the recent tool-call log.
      * ``error_cascade`` — `detect_error_cascade` fired on the recent log.
      * ``token_alert`` — cumulative tokens crossed `TOKEN_ALERT_THRESHOLD`.

    Each reason gets its OWN cooldown key
    (``watchdog:steer_cooldown:<reason>:<session_id>``) so a repetition
    steer does NOT suppress a parallel error-cascade or token-alert steer.

    Cooldown is enforced with a single atomic Redis ``SET NX EX`` — truthy
    return means the slot was open and we may proceed; falsy means the key
    exists and we back off. This is the full contract — never sequence a
    separate GET then SET, which would race under concurrent watchdog ticks.

    Delivery: `agent/steering.py::push_steering_message` with
    ``sender="watchdog"`` (mandatory — downstream consumers distinguish
    watchdog steers from human steers by this tag). The message is drained
    at the next tool-call boundary by the existing PostToolUse hook and
    the PM session's turn-boundary drain; it does NOT interrupt mid-tool
    execution. Operators should expect a one-tool-call delay between
    detection and correction.

    Feature gate: ``WATCHDOG_AUTO_STEER_ENABLED`` (default on). When
    disabled, the detection is still logged at WARNING upstream but no
    steer is pushed.

    Args:
        session_id: The AgentSession.session_id to steer. Must be the
            bridge/Telegram session id (not agent_session_id).
        reason: One of ``"repetition"``, ``"error_cascade"``,
            ``"token_alert"``. Used as part of the cooldown key.
        message: The steering text to push. Composed by the caller so the
            wording can vary per trigger reason.
        cooldown_seconds: Cooldown TTL in seconds. Callers pass
            ``STEER_COOLDOWN`` for loop-break reasons and
            ``TOKEN_ALERT_COOLDOWN`` for token alerts.

    Returns:
        True when a steer was pushed. False when the cooldown slot was
        closed, the feature flag was off, or any exception was caught
        (fail-quiet so the watchdog loop never crashes on steer errors).
    """
    if not _env_flag_enabled("WATCHDOG_AUTO_STEER_ENABLED"):
        logger.debug(
            "[watchdog] auto-steer disabled via env; skipping %s steer for %s",
            reason,
            session_id,
        )
        return False

    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        cooldown_key = f"watchdog:steer_cooldown:{reason}:{session_id}"
        # Atomic set-if-not-exists with TTL — single Redis command,
        # eliminates the read-then-write race entirely.
        cooldown_slot_open = POPOTO_REDIS_DB.set(
            cooldown_key,
            "1",
            nx=True,
            ex=cooldown_seconds,
        )
        if not cooldown_slot_open:
            logger.debug(
                "[watchdog] %s steer for %s suppressed — cooldown active",
                reason,
                session_id,
            )
            return False

        from agent.steering import push_steering_message

        push_steering_message(session_id, message, sender="watchdog")
        logger.warning(
            "[watchdog] Loop-break steer injected for %s: reason=%s",
            session_id,
            reason,
        )
        return True
    except Exception as e:
        logger.warning(
            "[watchdog] Failed to inject %s steer for %s: %s",
            reason,
            session_id,
            e,
        )
        return False


def _apply_stall_reaction(session: AgentSession) -> bool:
    """Queue a user-visible ⏳ reaction on the originating Telegram message.

    Issue #1313: when `check_stalled_sessions` observes a session past its
    stall threshold, this helper writes a reaction payload to
    ``telegram:outbox:{session_id}`` so the bridge relay's existing
    `_send_queued_reaction` drain delivers the emoji on the user's
    original message. The existing ``LIFECYCLE_STALL`` warning log is
    preserved unchanged — this is an *additional* user-visible channel,
    not a replacement.

    Idempotency: a single atomic ``SET NX EX`` on
    ``watchdog:stall_reaction_applied:{session_id}`` (TTL =
    ``STALL_REACTION_DEDUP_TTL``) ensures exactly one reaction per
    session per stall period. The key is DELETEd elsewhere when the
    session is observed in a healthy state so re-stalls trigger a fresh
    reaction.

    Skip conditions (return ``False``, no Redis writes, no warning):
      * Feature flag ``WATCHDOG_STALL_REACTION_ENABLED`` is set falsy.
      * Session has no ``chat_id`` (e.g. local sessions, no Telegram origin).
      * Session has no ``telegram_message_id`` (originating message not
        captured — typical for non-Telegram session creators).
      * Session has no resolvable ``session_id`` / ``agent_session_id``.
      * Dedup key already exists for this stall period.

    Schema parity: the inlined payload literal MUST stay byte-for-byte
    identical to ``agent/output_handler.OutputHandler._build_reaction_payload``
    so the bridge relay accepts both writers' messages from the same outbox.
    The unit test ``test_payload_matches_build_reaction_payload`` enforces
    this. We do NOT import `_build_reaction_payload` directly because that
    module is async-handler code and the import path risks cycles; the test
    is the only mechanical defense against schema drift.

    Args:
        session: The AgentSession observed as stalled. Must expose
            ``session_id`` (or ``agent_session_id``), ``chat_id``, and
            ``telegram_message_id``.

    Returns:
        True when a reaction payload was queued. False on any skip
        condition or any caught exception (fail-quiet so the watchdog
        loop never crashes on reaction errors).
    """
    if not _env_flag_enabled("WATCHDOG_STALL_REACTION_ENABLED"):
        return False

    try:
        chat_id = getattr(session, "chat_id", None)
        msg_id = getattr(session, "telegram_message_id", None)
        session_id = getattr(session, "session_id", None) or getattr(
            session, "agent_session_id", None
        )
        if not (chat_id and msg_id and session_id):
            return False

        from popoto.redis_db import POPOTO_REDIS_DB

        dedup_key = f"watchdog:stall_reaction_applied:{session_id}"
        slot_open = POPOTO_REDIS_DB.set(
            dedup_key,
            "1",
            nx=True,
            ex=STALL_REACTION_DEDUP_TTL,
        )
        if not slot_open:
            return False

        payload = {
            "type": "reaction",
            "chat_id": str(chat_id),
            "reply_to": int(msg_id),
            "emoji": STALL_REACTION_EMOJI,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        queue_key = f"telegram:outbox:{session_id}"
        POPOTO_REDIS_DB.rpush(queue_key, json.dumps(payload))
        POPOTO_REDIS_DB.expire(queue_key, STALL_REACTION_OUTBOX_TTL)
        logger.warning(
            "[watchdog] Stall reaction queued for %s (chat=%s msg=%s emoji=%s)",
            session_id,
            chat_id,
            msg_id,
            STALL_REACTION_EMOJI,
        )
        return True
    except Exception as e:
        logger.warning(
            "[watchdog] Failed to queue stall reaction for %s: %s",
            getattr(session, "session_id", "?"),
            e,
        )
        return False


def _clear_stall_reaction_dedup(session_id: str) -> None:
    """Delete the stall-reaction dedup key so re-stalls trigger a fresh reaction.

    Called from the iteration loop in `check_stalled_sessions` whenever a
    session is observed in a healthy (non-stall) state. Fail-quiet: any
    Redis exception is swallowed; orphaned keys age out via
    ``STALL_REACTION_DEDUP_TTL``.
    """
    if not session_id:
        return
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(f"watchdog:stall_reaction_applied:{session_id}")
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(
            "[watchdog] Failed to clear stall reaction dedup for %s: %s",
            session_id,
            e,
        )


def check_worker_liveness_and_slots() -> None:
    """Out-of-domain worker liveness + slot recovery (Fix #5, #1821).

    Runs in the BRIDGE process — a different failure domain from the worker loop
    it polices — so it can drive recovery even when the worker event loop is
    synchronously frozen. Reads two Redis keys the worker publishes:

      * ``worker:loop_beacon:{host}`` — wall-clock freshness beacon.
      * ``worker:slot:leases:{host}`` — the current lease snapshot.

    Behaviour each tick:

      1. **Beacon missing / stale wall_ts** → the worker is not publishing (process
         down or wedged). Record a ``loop_wedged`` action + increment
         ``loop_wedged_detected``, log that we are DEFERRING the kill, and return
         with NO kill action. Process recovery belongs to the dead-man's-switch +
         ``worker_watchdog.py`` — this function NEVER sends any process signal, never
         invokes launch tooling, and never writes the critical worker-recovery key
         (No-Gos).
      2. **Beacon fresh but unarmed** (loop has not ticked yet) → never treated as
         wedged; nothing to reclaim; return.
      3. **Beacon fresh + terminal-owner lease** (leak under a live loop), gated on
         ``BRIDGE_SLOT_RECLAIM_ENABLED`` (default on) → push each terminal owner onto
         ``worker:slot:reclaim_requests:{host}`` (per-owner ``SET NX`` dedup, LTRIM
         cap for Race 4) and append a capped action-log entry. The actual
         ``registry.reclaim()`` runs on the worker loop when it drains the request
         (loop-affinity physics); the bridge only issues the TRIGGER.
      4. **Beacon fresh + no terminal-owner leak** → healthy; clear the per-owner
         dedup markers so a future re-leak re-triggers.

    Freshness is keyed ONLY on the wall-clock ``wall_ts`` — never the advisory
    monotonic ``loop_beacon_age_s`` (Risk 1). Fully fail-quiet: a malformed beacon
    JSON or any Redis error logs and returns; nothing propagates into
    ``watchdog_loop``.
    """
    host = socket.gethostname()
    try:
        from popoto.redis_db import POPOTO_REDIS_DB
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[watchdog] worker liveness check: redis unavailable: %s", e)
        return

    beacon_key = f"{WORKER_LOOP_BEACON_KEY_PREFIX}{host}"
    now = time.time()

    # --- Read + parse the beacon (fail-quiet on malformed JSON) ---
    try:
        raw_beacon = POPOTO_REDIS_DB.get(beacon_key)
    except Exception as e:
        logger.warning("[watchdog] worker liveness check: beacon read failed: %s", e)
        return

    if raw_beacon is None:
        _record_loop_wedged(
            POPOTO_REDIS_DB, host, now, "beacon missing (worker down or TTL expired)"
        )
        return

    try:
        if isinstance(raw_beacon, bytes):
            raw_beacon = raw_beacon.decode("utf-8", "replace")
        beacon = json.loads(raw_beacon)
        wall_ts = float(beacon["wall_ts"])
    except Exception as e:
        # Malformed beacon → treat as "no usable beacon" but do not crash.
        logger.warning("[watchdog] worker liveness check: malformed beacon JSON: %s", e)
        _record_loop_wedged(POPOTO_REDIS_DB, host, now, "beacon JSON malformed")
        return

    # Freshness keyed ONLY on wall_ts (Risk 1 — never the monotonic advisory
    # age). The boolean fresh/stale decision is delegated to the shared
    # worker_loop_beacon_fresh() reader (#1312) so there is exactly one freshness
    # definition; the missing/malformed branches above stay local because they
    # need distinct loop_wedged detail strings + the parsed `armed` field below.
    if not worker_loop_beacon_fresh(host):
        _record_loop_wedged(
            POPOTO_REDIS_DB,
            host,
            now,
            f"beacon stale (wall_ts age={now - wall_ts:.0f}s > {BRIDGE_WORKER_BEACON_STALE_S}s)",
        )
        return

    # Fresh beacon. An unarmed beacon (loop not yet ticked) is never wedged and
    # has nothing to reclaim (the reap drain will not run until the loop ticks).
    if not beacon.get("armed", False):
        logger.debug("[watchdog] worker liveness check: beacon fresh but unarmed — skipping")
        return

    # --- Read the lease snapshot ---
    try:
        raw_leases = POPOTO_REDIS_DB.get(f"{WORKER_SLOT_LEASES_KEY_PREFIX}{host}")
    except Exception as e:
        logger.warning("[watchdog] worker liveness check: lease snapshot read failed: %s", e)
        return

    owners: list[str] = []
    if raw_leases is not None:
        try:
            if isinstance(raw_leases, bytes):
                raw_leases = raw_leases.decode("utf-8", "replace")
            leases = json.loads(raw_leases)
            owners = [
                o["owner_session_id"] for o in leases.get("owners", []) if o.get("owner_session_id")
            ]
        except Exception as e:
            logger.warning("[watchdog] worker liveness check: malformed lease snapshot: %s", e)
            return

    # Identify terminal-owner leases. A None/error status read is "unknown → skip"
    # (mirrors the worker drain's #1868 posture — the bridge only requests reclaim
    # for owners it can positively confirm terminal).
    terminal_owners: list[str] = []
    for owner in owners:
        try:
            row = AgentSession.get_by_id(owner)
        except Exception:  # noqa: S112 -- unknown owner status: skip (#1868)
            continue
        if row is not None and getattr(row, "status", None) in _terminal_statuses():
            terminal_owners.append(owner)

    if not terminal_owners:
        # Healthy tick — clear per-owner dedup markers so a future re-leak retriggers.
        _clear_reclaim_dedup(POPOTO_REDIS_DB, host)
        return

    if not _env_flag_enabled(BRIDGE_SLOT_RECLAIM_ENABLED_VAR):
        # Detection/logging only — no reclaim-request pushed (kill-switch off).
        logger.info(
            "[watchdog] worker liveness check: %d terminal-owner lease(s) observed but "
            "%s is disabled — detection only, no reclaim-request pushed.",
            len(terminal_owners),
            BRIDGE_SLOT_RECLAIM_ENABLED_VAR,
        )
        return

    _push_reclaim_requests(POPOTO_REDIS_DB, host, terminal_owners, now)


def _terminal_statuses() -> frozenset:
    """Return the canonical terminal-status set (imported lazily to avoid cycles)."""
    from models.session_lifecycle import TERMINAL_STATUSES

    return TERMINAL_STATUSES


def _record_loop_wedged(redis_client, host: str, now: float, detail: str) -> None:
    """Record a ``loop_wedged`` detection and DEFER the kill (Fix #5, #1821).

    Appends a capped action-log entry + increments
    ``{host}:worker-watchdog:loop_wedged_detected``. Takes NO kill action — the
    dead-man's-switch + ``worker_watchdog.py`` own process recovery. Fail-quiet.
    """
    logger.warning(
        "[watchdog] loop_wedged detected (%s) — DEFERRING kill to the dead-man's-switch / "
        "worker_watchdog.py (this watchdog never kills).",
        detail,
    )
    try:
        _append_watchdog_action(
            redis_client,
            host,
            {"action": "loop_wedged", "ts": now, "detail": detail, "deferring_kill": True},
        )
    except Exception as e:
        logger.debug("[watchdog] loop_wedged action-log append failed: %s", e)
    try:
        redis_client.incr(f"{host}:worker-watchdog:loop_wedged_detected")
    except Exception as e:
        logger.debug("[watchdog] loop_wedged_detected counter increment failed: %s", e)


def _push_reclaim_requests(redis_client, host: str, terminal_owners: list[str], now: float) -> None:
    """Push reclaim-requests for terminal-owner leases (Fix #5 TRIGGER, #1821).

    Non-blocking (concern #4): batched into at most TWO Redis round-trips
    regardless of the owner count — a per-owner ``SET NX`` dedup pipeline, then a
    single push/trim/action-log pipeline — so a multi-owner leak burst can never
    serialize N × socket_timeout blocking calls on the single bridge event loop.

    Race 4: after LPUSH the list is LTRIM'd to ``RECLAIM_REQUESTS_MAX`` and given a
    TTL, so the list stays bounded and a dead worker's backlog expires. The worker
    drain re-reads owner status fresh and ``registry.reclaim()`` is idempotent, so a
    dropped-then-re-requested owner is harmless. Fail-quiet.
    """
    reclaim_key = f"{WORKER_SLOT_RECLAIM_REQUESTS_KEY_PREFIX}{host}"
    actions_key = f"{WORKER_WATCHDOG_ACTIONS_KEY_PREFIX}{host}"
    try:
        # Round-trip 1: per-owner dedup markers (SET NX). Only owners whose marker
        # was newly set are pushed — prevents re-pushing the same owner every tick.
        dedup_pipe = redis_client.pipeline()
        for owner in terminal_owners:
            dedup_pipe.set(
                f"{WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX}{host}:{owner}",
                "1",
                nx=True,
                ex=RECLAIM_REQUEST_DEDUP_TTL,
            )
        nx_results = dedup_pipe.execute()
        new_owners = [
            owner for owner, was_set in zip(terminal_owners, nx_results, strict=False) if was_set
        ]
        if not new_owners:
            return

        # Round-trip 2: push + trim + TTL + capped action log, all in one pipeline.
        push_pipe = redis_client.pipeline()
        for owner in new_owners:
            push_pipe.lpush(reclaim_key, owner)
        push_pipe.ltrim(reclaim_key, 0, RECLAIM_REQUESTS_MAX - 1)
        push_pipe.expire(reclaim_key, WORKER_SLOT_KEY_TTL_SECONDS)
        for owner in new_owners:
            push_pipe.lpush(
                actions_key,
                json.dumps({"action": "reclaim_requested", "ts": now, "owner": owner}),
            )
        push_pipe.ltrim(actions_key, 0, WORKER_WATCHDOG_ACTIONS_MAX - 1)
        push_pipe.expire(actions_key, WORKER_SLOT_KEY_TTL_SECONDS)
        push_pipe.execute()

        logger.warning(
            "[watchdog] pushed %d reclaim-request(s) for terminal-owner lease(s): %s "
            "(worker on-loop drain performs the actual reclaim).",
            len(new_owners),
            ", ".join(new_owners),
        )
    except Exception as e:
        logger.warning("[watchdog] reclaim-request push failed (non-fatal): %s", e)


def _clear_reclaim_dedup(redis_client, host: str) -> None:
    """Clear per-owner reclaim-request dedup markers on a healthy tick (Fix #5).

    So a future re-leak of a previously-requested owner re-triggers a fresh
    reclaim-request. Enumerates via a non-blocking ``scan_iter`` and deletes in
    bounded batches (the old ``KEYS`` scan blocked the Redis event loop at
    scale). Fail-quiet; orphaned markers also age out via their TTL. These are
    plain watchdog marker keys (prefix ``WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX``),
    not Popoto-managed model keys, so raw ``scan_iter``/``delete`` is permitted.
    """
    try:
        pattern = f"{WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX}{host}:*"
        batch: list = []
        for key in redis_client.scan_iter(match=pattern, count=100):
            batch.append(key)
            if len(batch) >= 500:
                redis_client.delete(*batch)
                batch = []
        if batch:
            redis_client.delete(*batch)
    except Exception as e:
        logger.debug("[watchdog] reclaim-dedup clear failed (non-fatal): %s", e)


def _append_watchdog_action(redis_client, host: str, entry: dict) -> None:
    """Append a capped entry to ``worker:watchdog:actions:{host}`` (Fix #5).

    Capped LPUSH + LTRIM (newest first, bounded) + TTL. Fail-quiet.
    """
    actions_key = f"{WORKER_WATCHDOG_ACTIONS_KEY_PREFIX}{host}"
    redis_client.lpush(actions_key, json.dumps(entry))
    redis_client.ltrim(actions_key, 0, WORKER_WATCHDOG_ACTIONS_MAX - 1)
    redis_client.expire(actions_key, WORKER_SLOT_KEY_TTL_SECONDS)


def assess_session_health(session: AgentSession) -> dict[str, Any]:
    """Assess the health of a single session.

    Args:
        session: The AgentSession to assess

    Returns:
        Dict with:
        - healthy: bool indicating if session is healthy
        - issues: list of issue descriptions
        - severity: "warning" or "critical"

    Checks for:
    - Silence (no activity for too long)
    - Duration (session running too long)
    - Looping (repeated identical tool calls)
    - Error cascade (high error rate)
    """
    issues = []
    now = time.time()

    # Check for silence
    updated_ts = _to_timestamp(session.updated_at)
    silence_duration = (now - updated_ts) if updated_ts else 0
    if silence_duration > SILENCE_THRESHOLD:
        issues.append(f"Silent for {int(silence_duration / 60)} minutes")

    # Check for excessive duration
    started_ts = _to_timestamp(session.started_at)
    session_duration = (now - started_ts) if started_ts else 0
    if session_duration > DURATION_THRESHOLD:
        issues.append(f"Running for {int(session_duration / 3600)} hours")

    # Check for looping and error cascades using tool call history
    try:
        tool_calls = read_recent_tool_calls(session.session_id)

        if tool_calls:
            # Check for repetition
            is_looping, repeated_tool, count = detect_repetition(tool_calls)
            if is_looping:
                issues.append(f"Looping: {repeated_tool} called {count} times consecutively")
                # Actuate: push a loop-break steering message (issue #1128).
                # The cooldown key includes reason='repetition', so a parallel
                # error-cascade or token-alert steer is not suppressed.
                if repeated_tool:
                    _inject_watchdog_steer(
                        session.session_id,
                        "repetition",
                        (
                            f"Stop and re-check the task — you appear to be "
                            f"repeating the same tool call ({repeated_tool}) "
                            f"{count} times. Summarize what you've tried, "
                            "then try a different approach."
                        ),
                        cooldown_seconds=STEER_COOLDOWN,
                    )

            # Check for error cascade
            is_cascading, error_count = detect_error_cascade(tool_calls)
            if is_cascading:
                issues.append(
                    f"Error cascade: {error_count} errors in last {ERROR_CASCADE_WINDOW} calls"
                )
                # Actuate: push an error-cascade steer with an independent
                # cooldown key (issue #1128).
                _inject_watchdog_steer(
                    session.session_id,
                    "error_cascade",
                    (
                        f"Stop — you've hit {error_count} errors in the last "
                        f"{ERROR_CASCADE_WINDOW} operations. Summarize the "
                        "failure pattern and pause for human input rather "
                        "than continuing blind."
                    ),
                    cooldown_seconds=STEER_COOLDOWN,
                )
    except Exception as e:
        logger.debug(
            "[watchdog] Could not analyze tool calls for session %s: %s",
            session.session_id,
            e,
        )

    # Token-spend alert (issue #1128). Watchdog READS
    # `AgentSession.total_input_tokens + total_output_tokens` only — it
    # never writes those fields (writers are the SDK ResultMessage handler
    # and `get_response_via_harness`, both worker-process). Only triggers
    # for `running` sessions so a completed session that happened to rack
    # up tokens doesn't get a steer sent into an empty queue.
    try:
        status_val = getattr(session, "status", None)
        if status_val == "running":
            in_tokens = int(getattr(session, "total_input_tokens", 0) or 0)
            out_tokens = int(getattr(session, "total_output_tokens", 0) or 0)
            total_tokens = in_tokens + out_tokens
            if total_tokens >= TOKEN_ALERT_THRESHOLD:
                issues.append(
                    f"Token budget: {total_tokens:,} tokens, "
                    f"${float(getattr(session, 'total_cost_usd', 0.0) or 0.0):.2f}"
                )
                cost_usd = float(getattr(session, "total_cost_usd", 0.0) or 0.0)
                _inject_watchdog_steer(
                    session.session_id,
                    "token_alert",
                    (
                        f"Token budget exceeded: ${cost_usd:.2f} / "
                        f"{total_tokens:,} tokens spent this session. Stop "
                        "and summarize what you've done."
                    ),
                    cooldown_seconds=TOKEN_ALERT_COOLDOWN,
                )
    except Exception as e:
        logger.debug(
            "[watchdog] Token alert check failed for %s: %s",
            session.session_id,
            e,
        )

    # Determine overall health and severity
    healthy = len(issues) == 0
    severity = "critical" if len(issues) > 1 else "warning"

    return {"healthy": healthy, "issues": issues, "severity": severity}


def read_recent_tool_calls(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Read the most recent tool calls from a session's log.

    Args:
        session_id: The session ID to read logs for
        limit: Maximum number of recent lines to read

    Returns:
        List of tool call dicts (parsed JSON), or empty list on error

    Gracefully handles missing files, corrupted JSON, etc.
    """
    project_dir = Path(__file__).parent.parent
    log_file = project_dir / "logs" / "sessions" / session_id / "tool_use.jsonl"

    if not log_file.exists():
        return []

    try:
        # Read last N lines efficiently
        with open(log_file) as f:
            lines = f.readlines()

        # Take last 'limit' lines
        recent_lines = lines[-limit:] if len(lines) > limit else lines

        # Parse each line as JSON
        tool_calls = []
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
            try:
                tool_calls.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip corrupted lines
                continue

        return tool_calls
    except Exception as e:
        logger.debug("[watchdog] Error reading tool calls for %s: %s", session_id, e)
        return []


def detect_repetition(
    tool_calls: list[dict[str, Any]], threshold: int = LOOP_THRESHOLD
) -> tuple[bool, str | None, int]:
    """Detect if the session is stuck in a loop of repeated tool calls.

    Args:
        tool_calls: List of tool call event dicts
        threshold: Number of consecutive identical calls to trigger

    Returns:
        Tuple of (is_looping, repeated_tool_name, count)

    Creates fingerprints from (tool_name, sorted input items) and counts
    consecutive identical fingerprints.
    """
    # Filter to only pre_tool_use events (these have tool_input)
    pre_events = [tc for tc in tool_calls if tc.get("event") == "pre_tool_use"]

    if len(pre_events) < threshold:
        return (False, None, 0)

    # Create fingerprints
    fingerprints = []
    for event in pre_events:
        tool_name = event.get("tool_name", "unknown")
        tool_input = event.get("tool_input", {})

        # Create a hashable fingerprint from tool name and sorted input
        if isinstance(tool_input, dict):
            input_items = tuple(sorted(tool_input.items()))
        else:
            input_items = (str(tool_input),)

        fingerprints.append((tool_name, input_items))

    # Count consecutive identical fingerprints from the end
    if not fingerprints:
        return (False, None, 0)

    last_fingerprint = fingerprints[-1]
    consecutive_count = 1

    for i in range(len(fingerprints) - 2, -1, -1):
        if fingerprints[i] == last_fingerprint:
            consecutive_count += 1
        else:
            break

    is_looping = consecutive_count >= threshold
    repeated_tool = last_fingerprint[0] if is_looping else None

    return (is_looping, repeated_tool, consecutive_count)


def detect_error_cascade(
    tool_calls: list[dict[str, Any]],
    threshold: int = ERROR_CASCADE_THRESHOLD,
    window: int = ERROR_CASCADE_WINDOW,
) -> tuple[bool, int]:
    """Detect if the session is experiencing an error cascade.

    Args:
        tool_calls: List of tool call event dicts
        threshold: Number of errors to trigger cascade detection
        window: Number of recent calls to examine

    Returns:
        Tuple of (is_cascading, error_count)

    Looks at post_tool_use events and counts those with error indicators
    in their output.
    """
    # Filter to only post_tool_use events (these have tool_output)
    post_events = [tc for tc in tool_calls if tc.get("event") == "post_tool_use"]

    # Take last 'window' events
    recent_events = post_events[-window:] if len(post_events) > window else post_events

    if not recent_events:
        return (False, 0)

    # Count events with error indicators
    error_count = 0
    error_indicators = [
        "error",
        "exception",
        "failed",
        "traceback",
        "fatal",
        "cannot",
        "not found",
        "permission denied",
    ]

    for event in recent_events:
        output = event.get("tool_output_preview", "").lower()

        # Check if any error indicator is in the output
        if any(indicator in output for indicator in error_indicators):
            error_count += 1

    is_cascading = error_count >= threshold

    return (is_cascading, error_count)


def _safe_abandon_session(session: AgentSession, reason: str) -> bool:
    """Safely mark a session as abandoned, handling duplicate key errors.

    The watchdog frequently encounters ModelException (duplicate key / unique
    constraint violations) when saving sessions that were concurrently modified
    or cleaned up by another process. This helper wraps the save() call to
    catch those errors locally instead of letting them propagate to the
    watchdog loop where they spam the error log.

    Args:
        session: The session to abandon
        reason: Human-readable reason for the abandonment

    Returns:
        True if the session was successfully saved, False if the save failed
        (session may have been deleted by another process).
    """
    try:
        from models.session_lifecycle import finalize_session

        finalize_session(
            session,
            "abandoned",
            reason=reason,
            skip_auto_tag=True,
            skip_checkpoint=True,
        )
        return True
    except ModelException as e:
        # Session was likely deleted or modified by another process between
        # our read and this save. Log at WARNING (not ERROR) since this is
        # a known race condition, not a bug.
        logger.warning(
            "[watchdog] Could not save session %s (duplicate key / stale): %s",
            session.session_id,
            e,
        )
        return False


async def fix_unhealthy_session(session: AgentSession, assessment: dict[str, Any]) -> bool:
    """Fix an unhealthy session by abandoning it.

    Recovery of jobs with dead workers is handled by the unified health check
    in agent/agent_session_queue.py (_agent_session_health_check). The session watchdog only
    handles session-level health (silence, looping, error cascades).

    Args:
        session: The session with health issues
        assessment: Health assessment dict with issues and severity

    Returns:
        True if the session was fixed (abandoned), False otherwise

    Strategy:
    - Silent sessions (>30 min): abandon
    - Long-running sessions (>2 hours): abandon
    - Looping/error cascades: abandon, create issue if critical
    """
    issues = assessment["issues"]
    severity = assessment["severity"]
    now = time.time()

    # Calculate silence duration
    updated_ts = _to_timestamp(session.updated_at)
    silence_duration = (now - updated_ts) if updated_ts else 0

    # Most common case: session is stuck/silent
    if silence_duration > ABANDON_THRESHOLD:
        reason = f"silent for {int(silence_duration / 60)}min"
        _safe_abandon_session(session, f"watchdog: {reason}")
        logger.info(
            "[watchdog] Abandoned stuck session %s (%s)",
            session.session_id,
            reason,
        )
        return True

    # Long-running session
    started_ts = _to_timestamp(session.started_at)
    session_duration = (now - started_ts) if started_ts else 0
    if session_duration > DURATION_THRESHOLD:
        reason = f"running for {int(session_duration / 3600)}h"
        _safe_abandon_session(session, f"watchdog: {reason}")
        logger.info(
            "[watchdog] Abandoned long session %s (%s)",
            session.session_id,
            reason,
        )
        return True

    # Critical issues (looping, error cascades) - abandon and maybe create issue
    if severity == "critical":
        _safe_abandon_session(
            session,
            f"watchdog: critical issues: {', '.join(issues)}",
        )

        # Create GitHub issue for investigation
        try:
            await create_session_issue(session, issues)
        except Exception as e:
            logger.error("[watchdog] Failed to create issue: %s", e)

        return True

    # Warning-level issues - just abandon
    _safe_abandon_session(
        session,
        f"watchdog: {', '.join(issues)}",
    )
    return True


async def create_session_issue(session: AgentSession, issues: list[str]) -> None:
    """Create a GitHub issue for a session that couldn't be auto-fixed.

    Args:
        session: The problematic session
        issues: List of issue descriptions
    """
    import subprocess

    project_dir = Path(__file__).parent.parent
    issues_formatted = "\n".join(f"- {issue}" for issue in issues)

    title = f"[Watchdog] Session {session.session_id[:8]} had critical issues"
    body = f"""## Session Details

- **Session ID**: `{session.session_id}`
- **Project**: {session.project_key}
- **Chat ID**: {session.chat_id}
- **Tool calls**: {session.tool_call_count}

## Issues Detected

{issues_formatted}

## Action Taken

Session was automatically marked as abandoned by the watchdog.

---
*Auto-generated by session watchdog*
"""

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                "tomcounsell/ai",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "bug",
                "--label",
                "watchdog",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
        if result.returncode == 0:
            logger.info("[watchdog] Created issue: %s", result.stdout.strip())
        else:
            logger.error("[watchdog] Failed to create issue: %s", result.stderr)
    except Exception as e:
        logger.error("[watchdog] Error creating issue: %s", e)
