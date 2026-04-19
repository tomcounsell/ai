"""Periodic health monitoring, no-progress detection, orphan cleanup, and startup recovery."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from agent.session_state import SessionHandle, _active_events, _active_sessions, _active_workers
from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)


def _ts(val):
    """Convert datetime or float to Unix timestamp."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    return None


# Agent session health check constants
AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300  # 5 minutes
AGENT_SESSION_TIMEOUT_DEFAULT = 2700  # 45 minutes for standard sessions
AGENT_SESSION_TIMEOUT_BUILD = (
    9000  # 2.5 hours for build sessions (detected by /do-build in message_text)
)
AGENT_SESSION_HEALTH_MIN_RUNNING = (
    300  # Don't recover sessions running less than 5 min (race condition guard)
)

# === Two-tier no-progress detector constants (issue #1036) ===
# Heartbeat write interval inside `_heartbeat_loop` for the queue-layer
# heartbeat field `last_heartbeat_at`. 60s matches the messenger watchdog
# tick so both Tier 1 heartbeats nominally refresh on the same cadence.
HEARTBEAT_WRITE_INTERVAL = 60
# Freshness window (seconds) for Tier 1 heartbeat fields. A heartbeat whose
# age is strictly less than this window is considered fresh. 90s provides a
# 30s grace margin over the 60s write cadence.
HEARTBEAT_FRESHNESS_WINDOW = 90
# Freshness window (seconds) for the stdout-stale Tier 1 kill signal (#1046)
# and the Tier 2 recent-stdout reprieve gate. A session whose last_stdout_at
# age exceeds this window is flagged by Tier 1 even when both heartbeats are
# fresh. 600s (10 min) accommodates long tool calls while bounding the
# alive-but-silent failure mode. Env-tunable via STDOUT_FRESHNESS_WINDOW_SECS.
STDOUT_FRESHNESS_WINDOW = int(os.environ.get("STDOUT_FRESHNESS_WINDOW_SECS", 600))
# Deadline (seconds) after started_at before a session that has NEVER produced
# stdout is also flagged by Tier 1. Preserves warmup tolerance (#1036) while
# bounding the "silent from the start" case. Env-tunable via
# FIRST_STDOUT_DEADLINE_SECS.
FIRST_STDOUT_DEADLINE = int(os.environ.get("FIRST_STDOUT_DEADLINE_SECS", 300))
# Max health-check kills before a session is finalized as `failed` instead
# of being re-queued to `pending`. Ensures sessions always reach a terminal
# status within ~10 minutes of going non-progressing, avoiding the
# Meta.ttl=30d silent-delete backstop described in spike-2 of issue #1036.
MAX_RECOVERY_ATTEMPTS = 2
# Timeout for awaiting task cancellation during recovery. SDK client cleanup
# propagates near-instantly once CancelledError is raised; 0.25s keeps the
# health-check tick budget tight while still giving the cancellation a
# moment to complete.
TASK_CANCEL_TIMEOUT = 0.25

# Module-level variable set by _has_progress() before returning False to
# attribute the reason for flagging without changing the return type (#1046).
# The health-check loop reads this immediately after _has_progress() returns
# False to emit the tier1_flagged_stdout_stale counter. Reset to "" at the
# top of _has_progress() on every call to avoid stale attribution.
_last_progress_reason: str = ""


def _recover_interrupted_agent_sessions_startup() -> int:
    """Reset stale running sessions to pending at startup.

    At startup, running sessions are likely orphaned from the previous process.
    However, sessions that started very recently (within AGENT_SESSION_HEALTH_MIN_RUNNING
    seconds) may have been picked up by a worker that started before this recovery
    function fired. These are skipped to avoid orphaning their SDK subprocesses.

    This uses the same timing guard as _agent_session_health_check() to avoid a race
    where a worker transitions a session to running, then startup recovery resets it
    back to pending -- orphaning the already-spawned SDK subprocess.

    Local CLI sessions (session_id starts with "local") are never re-queued. They are
    marked "abandoned" instead. The worker cannot deliver output for local sessions, so
    re-queuing them would spawn a second harness competing with the interactive CLI.

    Note: The timing guard (AGENT_SESSION_HEALTH_MIN_RUNNING) is the primary defense
    against the hook-reactivation race. Hook reactivation transitions running→running
    (same status), so CAS via finalize_session(expected_status) does NOT protect against
    it — but truly stale sessions (>300s old) predate any active typing activity.

    Status is an IndexedField, so direct mutation and save is safe.
    Returns the number of recovered bridge sessions (local sessions are not counted).
    """
    running_sessions = list(AgentSession.query.filter(status="running"))
    if not running_sessions:
        return 0

    now = time.time()
    cutoff = now - AGENT_SESSION_HEALTH_MIN_RUNNING

    # Filter out recently-started sessions (they are not orphans from a dead process)
    stale_sessions = []
    skipped = 0
    for entry in running_sessions:
        started_ts = _ts(getattr(entry, "started_at", None))
        if started_ts is not None and started_ts > cutoff:
            skipped += 1
            logger.info(
                "[startup-recovery] Skipping recent session %s (started %ds ago, guard=%ds)",
                entry.agent_session_id,
                int(now - started_ts),
                AGENT_SESSION_HEALTH_MIN_RUNNING,
            )
        else:
            stale_sessions.append(entry)

    if skipped:
        logger.info("[startup-recovery] Skipped %d recently-started session(s)", skipped)

    if not stale_sessions:
        return 0

    # Filter out terminal sessions that appear in the running index due to stale
    # IndexedField entries (#1006). These are zombie entries — the session hash
    # says killed/completed/failed but the index set still contains them.
    # Re-promoting these to pending creates an infinite resurrection cycle.
    non_terminal = []
    terminal_skipped = 0
    for entry in stale_sessions:
        actual_status = getattr(entry, "status", None)
        if actual_status in _TERMINAL_STATUSES:
            terminal_skipped += 1
            logger.warning(
                "[startup-recovery] Skipping terminal session %s "
                "(hash status=%s, stale running index entry — zombie #1006)",
                entry.agent_session_id,
                actual_status,
            )
        else:
            non_terminal.append(entry)
    if terminal_skipped:
        logger.warning(
            "[startup-recovery] Skipped %d terminal session(s) with stale running index entries",
            terminal_skipped,
        )
    stale_sessions = non_terminal

    if not stale_sessions:
        return 0

    logger.warning("[startup-recovery] Found %d stale session(s) to process", len(stale_sessions))

    count = 0
    abandoned = 0
    for entry in stale_sessions:
        wk = entry.worker_key
        is_local = entry.session_id.startswith("local")  # session_id is the reliable discriminator

        if is_local:
            # Local CLI sessions cannot be resumed by the bridge worker.
            # Mark abandoned so the originating CLI can reclaim on next turn.
            try:
                from models.session_lifecycle import StatusConflictError, finalize_session

                finalize_session(
                    entry,
                    "abandoned",
                    reason="startup recovery: local session cannot be resumed by worker",
                    skip_auto_tag=True,
                )
                abandoned += 1
                logger.info(
                    "[startup-recovery] Abandoned local session %s (session_id=%s, worker_key=%s)",
                    entry.agent_session_id,
                    entry.session_id,
                    wk,
                )
            except StatusConflictError as e:
                # Another concurrent modification (not hook reactivation — timing guard handles
                # that race). Log at INFO and skip — session is being handled elsewhere.
                logger.info(
                    "[startup-recovery] Status conflict abandoning local session %s, skipping: %s",
                    entry.session_id,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "[startup-recovery] Failed to abandon local session %s, deleting: %s",
                    entry.session_id,
                    e,
                )
                try:
                    entry.delete()
                except Exception:
                    pass
        else:
            logger.warning(
                "[startup-recovery] Recovering interrupted session %s "
                "(session=%s, worker=%s, msg=%.80r...)",
                entry.agent_session_id,
                entry.session_id,
                wk,
                entry.message_text or "",
            )
            try:
                from models.session_lifecycle import update_session

                update_session(
                    entry.session_id,
                    new_status="pending",
                    fields={"priority": "high", "started_at": None},
                    expected_status="running",
                    reason="startup recovery",
                )
                logger.info("[startup-recovery] Recovered session %s", entry.agent_session_id)
                count += 1
            except Exception as e:
                logger.warning(
                    "[startup-recovery] Failed to recover session %s, deleting: %s",
                    entry.session_id,
                    e,
                )
                try:
                    entry.delete()
                except Exception:
                    pass

    logger.warning(
        "[startup-recovery] Recovered %d bridge session(s), abandoned %d local session(s)",
        count,
        abandoned,
    )
    return count


# === Agent Session Health Monitor ===


def _get_agent_session_timeout(session) -> int:
    """Return the timeout in seconds for a session based on its message_text.

    Build sessions (containing '/do-build') get a longer timeout since they
    involve full SDLC cycles. All other sessions get the standard timeout.
    """
    message_text = getattr(session, "message_text", "") or ""
    if "/do-build" in message_text:
        return AGENT_SESSION_TIMEOUT_BUILD
    return AGENT_SESSION_TIMEOUT_DEFAULT


def _has_progress(entry: AgentSession) -> bool:
    """Return True iff the session shows any signal that real work has begun.

    Tier 1 signals (dual heartbeat, issue #1036) — checked first:
    - ``last_heartbeat_at``: queue-layer heartbeat, written every 60s by
      ``_heartbeat_loop`` inside ``_execute_agent_session``.
    - ``last_sdk_heartbeat_at``: messenger-sourced heartbeat, written by
      ``BackgroundTask._watchdog`` via the ``on_heartbeat_tick`` callback.

    Semantics: **OR** — if EITHER heartbeat is fresher than
    ``HEARTBEAT_FRESHNESS_WINDOW`` (90s), the session has progress. This
    tolerates single-writer failures (e.g. queue heartbeat loop wedged while
    the messenger watchdog keeps ticking), minimizing false-positives
    (killing a working session). The kill trigger in the health check
    requires BOTH heartbeats to be stale before even evaluating Tier 2.

    Tier 1 extension — stdout-stale kill signal (#1046):
    Even when both heartbeats are fresh, a session whose ``last_stdout_at``
    is stale beyond ``STDOUT_FRESHNESS_WINDOW`` (600s) is flagged by Tier 1.
    This catches the alive-but-silent failure mode where a ``claude -p``
    subprocess keeps emitting heartbeats but produces no stdout for 10+ min.

    For sessions that have never produced stdout (``last_stdout_at is None``),
    ``FIRST_STDOUT_DEADLINE`` (300s) applies: if ``started_at`` is older than
    the deadline, Tier 1 flags the session. This preserves warmup tolerance
    (#1036) while bounding the "silent from the start" case.

    When a live-but-silent subprocess is flagged by Tier 1, Tier 2 gate (c)
    "alive" will reprieve it — the subprocess is still running. The session
    remains monitored and is only killed once the subprocess eventually goes
    non-alive or the absolute timeout fires. This is intentional: an alive
    subprocess should not be killed prematurely; the reprieve loop bounds kill
    latency to ``STDOUT_FRESHNESS_WINDOW + one health-check tick`` after the
    process goes non-alive.

    The reason for the Tier 1 flag is stored in the module-level
    ``_last_progress_reason`` variable (set before returning False) so the
    health-check loop can emit a distinct counter without changing the return
    type. The variable is reset to "" at the top of every call.

    Own-progress signals (original behavior, preserved):
    - ``claude_session_uuid`` — populated on SDK authentication.
    - ``log_path`` — written on the first log entry.
    - ``turn_count`` — incremented per turn completion.

    Any one of the three is sufficient evidence that the session is in flight.

    Additionally, a session with active children (e.g. a PM session that has
    spawned a dev child) is considered to have progress even if it has no
    own-progress signals. The child lookup uses ``get_children()`` which queries
    via the Popoto ``parent_agent_session_id`` index. ``get_children()`` already
    returns ``[]`` on failure with a WARNING log, so no outer try/except is
    needed.

    Used by ``_agent_session_health_check`` to distinguish stuck slugless dev
    sessions (worker_alive via a co-running PM, but no progress) from healthy
    long-warmup BUILD sessions. See issues #944, #963, #1036, and #1046.
    """
    global _last_progress_reason
    _last_progress_reason = ""

    # Tier 1: dual-heartbeat OR check (#1036). Fresh on either signal → progress.
    now_utc = datetime.now(tz=UTC)
    any_heartbeat_fresh = False
    for hb_attr in ("last_heartbeat_at", "last_sdk_heartbeat_at"):
        hb = getattr(entry, hb_attr, None)
        if isinstance(hb, datetime):
            hb_aware = hb if hb.tzinfo else hb.replace(tzinfo=UTC)
            age_s = (now_utc - hb_aware).total_seconds()
            if age_s < HEARTBEAT_FRESHNESS_WINDOW:
                any_heartbeat_fresh = True
                break

    if any_heartbeat_fresh:
        # Tier 1 extension: stdout-stale kill signal (#1046).
        # Even with fresh heartbeats, flag if stdout is stale or overdue.
        lso = getattr(entry, "last_stdout_at", None)
        if isinstance(lso, datetime):
            lso_aware = lso if lso.tzinfo else lso.replace(tzinfo=UTC)
            if (now_utc - lso_aware).total_seconds() >= STDOUT_FRESHNESS_WINDOW:
                _last_progress_reason = "stdout_stale"
                return False  # stdout stale; Tier 1 flags despite fresh heartbeats
        elif lso is None:
            # No stdout yet — apply FIRST_STDOUT_DEADLINE relative to started_at.
            started = getattr(entry, "started_at", None)
            if started is not None:
                started_aware = started if started.tzinfo else started.replace(tzinfo=UTC)
                if (now_utc - started_aware).total_seconds() >= FIRST_STDOUT_DEADLINE:
                    _last_progress_reason = "first_stdout_deadline"
                    return False  # never produced stdout within deadline; flag
        return True

    # Own-progress fields (original behavior, preserves #944 / #963 invariants).
    if (entry.turn_count or 0) > 0:
        return True
    if bool((entry.log_path or "").strip()):
        return True
    if bool(entry.claude_session_uuid):
        return True
    # Child-progress check: a PM session with active children is not stuck
    # get_children() queries via Popoto parent_agent_session_id index (not string session_id)
    # and already returns [] on failure with a WARNING log — no outer try/except needed
    children = entry.get_children()
    if any(c.status not in _TERMINAL_STATUSES for c in children):
        return True
    return False


def _tier2_reprieve_signal(
    handle: "SessionHandle | None",
    entry: AgentSession,
) -> str | None:
    """Evaluate Tier 2 activity-positive reprieve gates (issue #1036).

    Called by the health check after Tier 1 has flagged a session as "both
    heartbeats stale". Any single positive signal reprieves the kill.

    Gates (order matters for telemetry — the first passing gate is returned):
      (c) "alive"    — ``psutil.Process(pid).status()`` is not one of
                       {zombie, dead, stopped}. Proves the SDK subprocess
                       still exists and is not a zombie.
      (d) "children" — ``psutil.Process(pid).children()`` is non-empty.
                       Stronger signal than (c): tool-subprocess execution is
                       actively happening right now. Returned in preference
                       to "alive" so metrics highlight this case.
      (e) "stdout"   — ``entry.last_stdout_at`` is within
                       ``STDOUT_FRESHNESS_WINDOW``. Proves the SDK recently
                       emitted stdout (stream-json event).

    Returns the name of the first passing gate ("children", "alive", or
    "stdout"), or ``None`` if every gate fails.

    Failure handling:
      * ``handle is None`` → (c)(d) skipped; fall through to (e).
      * ``handle.pid is None`` → (c)(d) skipped; fall through to (e).
      * ``psutil.NoSuchProcess`` / ``psutil.AccessDenied`` / ``ImportError``
        → (c)(d) skipped silently; fall through to (e).

    This helper NEVER raises. A genuinely dead session where every gate
    fails is preferable to crashing the health-check loop.
    """
    pid = handle.pid if handle is not None else None
    if pid is not None:
        try:
            import psutil

            proc = psutil.Process(pid)
            status = proc.status()
            if status not in (
                psutil.STATUS_ZOMBIE,
                psutil.STATUS_DEAD,
                psutil.STATUS_STOPPED,
            ):
                # Prefer "children" when present — stronger signal.
                if proc.children():
                    return "children"
                return "alive"
        except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError):
            pass
        except Exception as e:
            # Defensive: never crash the health check from a psutil edge case.
            logger.debug("[session-health] psutil probe failed for pid=%s: %s", pid, e)

    # (e) recent stdout — works even when pid is unknown.
    lso = getattr(entry, "last_stdout_at", None)
    if isinstance(lso, datetime):
        lso_aware = lso if lso.tzinfo else lso.replace(tzinfo=UTC)
        age = (datetime.now(tz=UTC) - lso_aware).total_seconds()
        if age < STDOUT_FRESHNESS_WINDOW:
            return "stdout"
    return None


async def _agent_session_health_check() -> None:
    """Health check for worker-managed sessions (running and pending).

    Other non-terminal statuses (active, dormant, paused, paused_circuit) are
    monitored by the bridge-hosted watchdog in monitoring/session_watchdog.py.
    See RECOVERY_OWNERSHIP in models/session_lifecycle.py for the full coverage map.

    Scans both 'running' and 'pending' sessions:

    For RUNNING sessions:
    1. If worker is dead/missing AND running > AGENT_SESSION_HEALTH_MIN_RUNNING: recover.
    2. If worker appears alive but running > AGENT_SESSION_HEALTH_MIN_RUNNING AND
       the session has no progress signal (``turn_count``, ``log_path``,
       ``claude_session_uuid`` all empty): recover. Slugless dev sessions share
       ``worker_key`` with co-running PM sessions, so ``worker_alive`` alone
       does not prove the dev session is being handled (#944).
    3. If exceeded timeout: recover regardless of worker state.
    4. Legacy sessions without started_at and no worker: recover.

    For PENDING sessions:
    5. If no live worker for session.chat_id AND pending > AGENT_SESSION_HEALTH_MIN_RUNNING:
       start a worker. This replaces the old _recover_stalled_pending mechanism.

    **Delivery guard (#918):** Before recovering a running session to pending,
    the health check inspects ``response_delivered_at``. If the field is set,
    the session already delivered its final response to Telegram — re-queuing
    would cause a duplicate reply. Instead, the session is finalized as
    ``completed`` via ``finalize_session()``. This prevents the crash-recover
    loop that previously produced 6+ duplicate messages per session.

    Recovery resets status to 'pending' via direct mutation and save.
    Status is an IndexedField, so no delete-and-recreate is needed.
    Only sessions whose worker is confirmed dead are touched.
    """
    now = time.time()
    checked = 0
    recovered = 0
    workers_started = 0

    # === Check RUNNING sessions_list ===
    running_sessions = list(AgentSession.query.filter(status="running"))
    for entry in running_sessions:
        checked += 1

        # Terminal-status guard (#1006): skip sessions whose hash status is
        # terminal but still appear in the running index due to stale
        # IndexedField entries. Without this, killed/completed sessions get
        # re-promoted to pending in an infinite resurrection cycle.
        actual_status = getattr(entry, "status", None)
        if actual_status in _TERMINAL_STATUSES:
            logger.warning(
                "[session-health] Skipping terminal session %s "
                "(hash status=%s, stale running index entry — zombie #1006)",
                entry.agent_session_id,
                actual_status,
            )
            continue

        try:
            worker_key = entry.worker_key
            worker = _active_workers.get(worker_key)
            worker_alive = worker is not None and not worker.done()

            started_ts = _ts(getattr(entry, "started_at", None))
            running_seconds = (now - started_ts) if started_ts else None

            should_recover = False
            reason = ""

            if not worker_alive:
                if started_ts is None:
                    should_recover = True
                    reason = "worker dead/missing, no started_at (legacy session)"
                elif (
                    running_seconds is not None
                    and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING
                ):
                    should_recover = True
                    reason = (
                        f"worker dead/missing, running for "
                        f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard)"
                    )
                else:
                    logger.debug(
                        "[session-health] Skipping session %s - worker dead but "
                        "running only %ss (under %ss guard)",
                        entry.agent_session_id,
                        int(running_seconds) if running_seconds else "?",
                        AGENT_SESSION_HEALTH_MIN_RUNNING,
                    )
            # Project-keyed dev sessions share worker_key with PM; without a
            # progress signal, worker_alive alone doesn't prove the dev session
            # is being handled (#944).
            elif (
                running_seconds is not None
                and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING
                and not _has_progress(entry)
            ):
                should_recover = True
                reason = (
                    f"worker alive but no progress signal, running for "
                    f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard, "
                    f"turn_count={entry.turn_count}, log_path={entry.log_path!r}, "
                    f"claude_session_uuid={entry.claude_session_uuid!r})"
                )
            elif started_ts is not None:
                timeout = _get_agent_session_timeout(entry)
                if running_seconds is not None and running_seconds > timeout:
                    should_recover = True
                    reason = f"exceeded timeout ({int(running_seconds)}s > {timeout}s)"

            if should_recover:
                # Classify the recovery reason up front — referenced below to
                # gate Tier 1/Tier 2 reprieve logic to no_progress recoveries
                # only (#1039 review). worker_dead and timeout kinds must
                # NOT pass through Tier 2: a dead worker cannot be reprieved
                # by an "active children" signal, and a timed-out session
                # with active children should still be killed — otherwise
                # the timeout cap becomes unenforceable.
                if "no progress signal" in reason:
                    _reason_kind = "no_progress"
                elif "exceeded timeout" in reason:
                    _reason_kind = "timeout"
                else:
                    _reason_kind = "worker_dead"

                # O1: observability counter — increment a project-scoped Redis
                # counter for dashboards. Failure must never block recovery.
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB as _R

                    _R.incr(f"{entry.project_key}:session-health:recoveries:{_reason_kind}")
                except Exception as _counter_err:
                    logger.debug(
                        "[session-health] recovery counter increment failed (non-fatal): %s",
                        _counter_err,
                    )

                # Guard: if response was already delivered, finalize instead
                # of recovering to pending (prevents duplicate delivery, #918)
                if getattr(entry, "response_delivered_at", None) is not None:
                    try:
                        from models.session_lifecycle import finalize_session

                        logger.info(
                            "[session-health] Session %s already delivered response at %s, "
                            "finalizing instead of recovering",
                            entry.agent_session_id,
                            entry.response_delivered_at,
                        )
                        finalize_session(
                            entry,
                            "completed",
                            reason="health check: already delivered",
                        )
                        recovered += 1
                    except Exception as e:
                        logger.error(
                            "[session-health] Failed to finalize already-delivered session %s: %s",
                            entry.agent_session_id,
                            e,
                        )
                    continue

                # === Two-tier no-progress detector (#1036) ===
                # Tier 1/Tier 2 reprieve logic applies ONLY to no_progress
                # recoveries. worker_dead and timeout recoveries skip reprieve
                # and fall through to the kill path below (#1039 review):
                #   * worker_dead: no worker to deliver future progress.
                #   * timeout: Tier 2 activity signals must NOT defeat the
                #     absolute timeout cap; an actively-running session that
                #     exceeds its configured timeout should still be killed.
                handle = _active_sessions.get(entry.agent_session_id)
                if handle is None:
                    logger.debug(
                        "[session-health] No registry handle for %s; "
                        "Tier 2 will use stdout gate only",
                        entry.agent_session_id,
                    )
                if _reason_kind == "no_progress":
                    try:
                        from popoto.redis_db import POPOTO_REDIS_DB as _MR

                        _MR.incr(f"{entry.project_key}:session-health:tier1_flagged_total")
                    except Exception as _m_err:
                        logger.debug("[session-health] tier1_flagged counter failed: %s", _m_err)

                    # Emit stdout-stale counter when the flag came from stdout,
                    # not from heartbeat staleness (#1046). _last_progress_reason
                    # is set by _has_progress() before returning False.
                    if _last_progress_reason in ("stdout_stale", "first_stdout_deadline"):
                        try:
                            from popoto.redis_db import POPOTO_REDIS_DB as _MR

                            _MR.incr(
                                f"{entry.project_key}:session-health:tier1_flagged_stdout_stale"
                            )
                        except Exception as _m_err:
                            logger.debug(
                                "[session-health] tier1_flagged_stdout_stale counter failed: %s",
                                _m_err,
                            )

                    reprieve = _tier2_reprieve_signal(handle, entry)
                    if reprieve is not None:
                        # Activity-positive: do NOT kill, do NOT increment recovery_attempts.
                        try:
                            from popoto.redis_db import POPOTO_REDIS_DB as _MR

                            _MR.incr(
                                f"{entry.project_key}:session-health:tier2_reprieve_total:{reprieve}"
                            )
                        except Exception as _m_err:
                            logger.debug(
                                "[session-health] tier2_reprieve counter failed: %s", _m_err
                            )
                        try:
                            entry.reprieve_count = (entry.reprieve_count or 0) + 1
                            entry.save(update_fields=["reprieve_count"])
                        except Exception as _rc_err:
                            logger.debug("[session-health] reprieve_count save failed: %s", _rc_err)
                        # Escalate log level after 3 reprieves to alert operators
                        # that a session may be alive-but-silent indefinitely (#1046 C2).
                        log_fn = logger.warning if (entry.reprieve_count or 0) >= 3 else logger.info
                        log_fn(
                            "[session-health] Tier 2 reprieve (%s) for session %s — "
                            "skipping kill (reprieve_count=%s)",
                            reprieve,
                            entry.agent_session_id,
                            entry.reprieve_count,
                        )
                        continue

                # All Tier 2 gates failed. Respect kill-switch.
                if os.environ.get("DISABLE_PROGRESS_KILL") == "1":
                    logger.warning(
                        "[session-health] Would kill session %s (DISABLE_PROGRESS_KILL=1): %s",
                        entry.agent_session_id,
                        reason,
                    )
                    continue

                is_local = worker_key.startswith("local")
                logger.warning(
                    "[session-health] Recovering stuck session %s "
                    "(chat=%s, session=%s, local=%s): %s",
                    entry.agent_session_id,
                    worker_key,
                    entry.session_id,
                    is_local,
                    reason,
                )

                # Cancel the in-flight session task if we have a handle and
                # the task reference has been populated. `handle.task` is None
                # between `_execute_agent_session` entry and
                # `BackgroundTask.run()` completion — during that setup window
                # there is nothing session-scoped to cancel (the worker-loop
                # task is off limits; plan spike-1, #1039 review). Cancelling
                # the populated `task._task` terminates the SDK subprocess via
                # CancelledError propagation, preventing orphan heartbeats.
                if handle is not None and handle.task is not None and not handle.task.done():
                    handle.task.cancel()
                    try:
                        await asyncio.wait_for(handle.task, timeout=TASK_CANCEL_TIMEOUT)
                    except (TimeoutError, asyncio.CancelledError):
                        pass
                    except Exception as _c_err:
                        logger.debug(
                            "[session-health] task cancel await raised %s for session %s",
                            _c_err,
                            entry.agent_session_id,
                        )
                    logger.info(
                        "[session-health] Cancelled orphan task for session %s",
                        entry.agent_session_id,
                    )

                from models.session_lifecycle import (
                    StatusConflictError,
                    finalize_session,
                    transition_status,
                )

                # Bump recovery_attempts counter only on actual kill (#1036).
                entry.recovery_attempts = (entry.recovery_attempts or 0) + 1
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB as _MR

                    _MR.incr(f"{entry.project_key}:session-health:kill_total")
                except Exception as _m_err:
                    logger.debug("[session-health] kill counter failed: %s", _m_err)

                try:
                    if is_local:
                        # Local CLI sessions have no bridge worker to resume them --
                        # mark abandoned. Tier 2 reprieves already had a chance above,
                        # so if we reach here the local session is genuinely wedged.
                        finalize_session(
                            entry,
                            "abandoned",
                            reason=(
                                f"health check: local session stuck "
                                f"(chat={worker_key}, attempts={entry.recovery_attempts})"
                            ),
                            skip_auto_tag=True,
                        )
                        logger.info(
                            "[session-health] Marked local session %s as abandoned "
                            "(chat=%s, attempts=%s)",
                            entry.agent_session_id,
                            worker_key,
                            entry.recovery_attempts,
                        )
                    elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                        # Exhausted retries: finalize as `failed` so the session
                        # reaches a terminal status and is auditable via
                        # valor-session status. Prevents the Meta.ttl silent-delete
                        # backstop from eating non-terminal records (spike-2).
                        finalize_session(
                            entry,
                            "failed",
                            reason=(
                                f"health check: {entry.recovery_attempts} recovery "
                                f"attempts, never progressed"
                            ),
                        )
                        logger.warning(
                            "[session-health] Finalized session %s as failed after "
                            "%s recovery attempts",
                            entry.agent_session_id,
                            entry.recovery_attempts,
                        )
                    else:
                        # Apply companion fields directly to the already-loaded entry,
                        # then transition via transition_status() which has its own CAS
                        # re-read. Save recovery_attempts along the way.
                        entry.priority = "high"
                        entry.started_at = None
                        try:
                            entry.save(update_fields=["recovery_attempts"])
                        except Exception as _ra_err:
                            logger.debug(
                                "[session-health] recovery_attempts save failed: %s",
                                _ra_err,
                            )
                        transition_status(
                            entry,
                            "pending",
                            reason=(
                                f"health check: recovered stuck session "
                                f"(chat={worker_key}, attempt {entry.recovery_attempts})"
                            ),
                        )
                        logger.info(
                            "[session-health] Recovered session %s (chat=%s, attempt %s)",
                            entry.agent_session_id,
                            worker_key,
                            entry.recovery_attempts,
                        )
                        from agent.agent_session_queue import _ensure_worker  # noqa: PLC0415

                        _ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)
                        # Wake up an already-running idle worker — _ensure_worker returns
                        # early if the worker exists, so the event is never set and the
                        # recovered pending session would stall until a new notify arrives.
                        event = _active_events.get(worker_key)
                        if event is not None:
                            event.set()
                except StatusConflictError as _sc_err:
                    logger.warning(
                        "[session-health] StatusConflictError during recovery of %s: %s",
                        entry.agent_session_id,
                        _sc_err,
                    )
                recovered += 1
        except Exception:
            logger.exception(
                "[session-health] Error processing session %s",
                getattr(entry, "agent_session_id", "unknown"),
            )

    # === Check PENDING sessions_list ===
    pending_sessions = list(AgentSession.query.filter(status="pending"))
    for entry in pending_sessions:
        checked += 1
        try:
            worker_key = entry.worker_key
            worker = _active_workers.get(worker_key)
            worker_alive = worker is not None and not worker.done()

            if worker_alive:
                # Worker exists — nudge its event in case it missed the original
                # notify (e.g. startup-recovery race: session put to pending before
                # the worker loop subscribed to its event).
                event = _active_events.get(worker_key)
                if event is not None:
                    event.set()
                continue

            # No live worker — check age threshold before starting one
            created_ts = _ts(getattr(entry, "created_at", None))
            if created_ts is None:
                continue
            pending_seconds = now - created_ts
            if pending_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING:
                if worker_key.startswith("local"):
                    # Local CLI sessions can't be resumed by bridge workers
                    logger.info(
                        "[session-health] Marking orphaned local pending session %s "
                        "as abandoned (chat=%s, pending %.0fs)",
                        entry.agent_session_id,
                        worker_key,
                        pending_seconds,
                    )
                    from models.session_lifecycle import finalize_session

                    finalize_session(
                        entry,
                        "abandoned",
                        reason=f"health check: orphaned local pending session (chat={worker_key})",
                        skip_auto_tag=True,
                    )
                else:
                    logger.info(
                        "[session-health] Starting worker for orphaned pending "
                        "session %s (chat=%s, pending %.0fs)",
                        entry.agent_session_id,
                        worker_key,
                        pending_seconds,
                    )
                    _ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)
                workers_started += 1
        except Exception:
            logger.exception(
                "[session-health] Error processing pending session %s",
                getattr(entry, "agent_session_id", "unknown"),
            )

    if checked > 0:
        logger.info(
            "[session-health] Health check: %d checked, %d recovered, %d workers started",
            checked,
            recovered,
            workers_started,
        )


async def _agent_session_hierarchy_health_check() -> None:
    """Check for orphaned children and stuck parents in session hierarchy.

    1. Orphaned children: child's parent_agent_session_id points to a non-existent session.
       Action: clear the parent_agent_session_id field (child completes normally).
    2. Stuck parents: status is waiting_for_children but all children are terminal.
       Action: finalize the parent (transition to completed/failed).
    """
    orphans_fixed = 0
    stuck_fixed = 0

    # Check for orphaned children
    try:
        all_sessions = list(AgentSession.query.all())
        # Guard against corrupt/phantom records whose fields are still Popoto Field
        # descriptors rather than hydrated values — those would crash set-building
        # and recreate.
        hydrated = [s for s in all_sessions if isinstance(s.agent_session_id, str)]
        children_with_parent = [s for s in hydrated if isinstance(s.parent_agent_session_id, str)]
        parent_ids = {s.agent_session_id for s in hydrated}

        for child in children_with_parent:
            if child.parent_agent_session_id not in parent_ids:
                try:
                    logger.warning(
                        "[session-health] Orphaned child %s: parent %s no longer exists — "
                        "clearing parent_agent_session_id",
                        child.agent_session_id,
                        child.parent_agent_session_id,
                    )
                    # Delete-and-recreate required: parent_agent_session_id is a KeyField,
                    # so mutating it directly would corrupt the index.
                    from agent.agent_session_queue import (
                        _extract_agent_session_fields,  # noqa: PLC0415
                    )

                    fields = _extract_agent_session_fields(child)
                    child.delete()
                    fields["parent_agent_session_id"] = None
                    AgentSession.create(**fields)
                    orphans_fixed += 1
                except Exception as inner:
                    logger.error(
                        "[session-health] Orphan repair failed for %s: %s",
                        getattr(child, "agent_session_id", "?"),
                        inner,
                        exc_info=True,
                    )
    except Exception as e:
        logger.error("[session-health] Orphan detection failed: %s", e, exc_info=True)

    # Check for stuck parents
    try:
        waiting_parents = list(AgentSession.query.filter(status="waiting_for_children"))
        for parent in waiting_parents:
            children = parent.get_children()
            if not children:
                # No children but waiting — auto-complete
                logger.warning(
                    "[session-health] Stuck parent %s has no children — auto-completing",
                    parent.agent_session_id,
                )
                from agent.session_completion import _transition_parent  # noqa: PLC0415

                _transition_parent(parent, "completed")
                stuck_fixed += 1
                continue

            terminal_statuses = _TERMINAL_STATUSES
            non_terminal = [c for c in children if c.status not in terminal_statuses]
            if not non_terminal:
                # All children terminal but parent still waiting
                any_failed = any(c.status == "failed" for c in children)
                new_status = "failed" if any_failed else "completed"
                logger.warning(
                    "[session-health] Stuck parent %s: all %d children terminal — "
                    "re-enqueuing for final summary (target=%s)",
                    parent.agent_session_id,
                    len(children),
                    new_status,
                )
                if new_status == "completed":
                    # Re-enqueue parent so PM can compose and deliver a final summary.
                    # The PM must include [PIPELINE_COMPLETE] to break out of nudge loop.
                    n_ok = sum(1 for c in children if c.status == "completed")
                    n_fail = sum(1 for c in children if c.status == "failed")
                    child_lines = "\n".join(
                        f"  - {getattr(c, 'agent_session_id', '?')}: {c.status}" for c in children
                    )
                    from agent.output_router import PIPELINE_COMPLETE_MARKER
                    from agent.steering import push_steering_message
                    from models.session_lifecycle import transition_status

                    steering_msg = (
                        f"All {len(children)} child pipeline sessions have completed "
                        f"({n_ok} succeeded, {n_fail} failed).\n"
                        f"Children:\n{child_lines}\n\n"
                        f"Compose a final summary for the user covering what was accomplished "
                        f"across all child pipelines. "
                        f"End your response with the literal text `{PIPELINE_COMPLETE_MARKER}` "
                        f"so it is delivered to the user instead of nudged."
                    )
                    push_steering_message(parent.session_id, steering_msg, sender="worker")
                    transition_status(
                        parent, "pending", reason="children completed, sending final summary"
                    )
                else:
                    # Failed parent: finalize immediately (no point composing a summary)
                    from agent.session_completion import _transition_parent  # noqa: PLC0415

                    _transition_parent(parent, new_status)
                stuck_fixed += 1
    except Exception as e:
        logger.error("[session-health] Stuck parent detection failed: %s", e, exc_info=True)

    if orphans_fixed or stuck_fixed:
        logger.info(
            "[session-health] Hierarchy check: %d orphan(s) fixed, %d stuck parent(s) fixed",
            orphans_fixed,
            stuck_fixed,
        )


async def _dependency_health_check() -> None:
    """No-op: dependency tracking was removed in issue #609."""
    pass


def _write_worker_heartbeat() -> None:
    """Write worker heartbeat file so the dashboard can show worker status."""
    heartbeat_file = Path(__file__).parent.parent / "data" / "last_worker_connected"
    try:
        heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat_file.with_suffix(".tmp")
        tmp.write_text(datetime.now(UTC).isoformat())
        os.replace(tmp, heartbeat_file)
    except OSError:
        pass


async def _agent_session_health_loop() -> None:
    """Periodically check running sessions for liveness and timeout."""
    logger.info(
        "[session-health] Agent session health monitor started (interval=%ds)",
        AGENT_SESSION_HEALTH_CHECK_INTERVAL,
    )
    while True:
        try:
            _write_worker_heartbeat()
            await _agent_session_health_check()
            await _agent_session_hierarchy_health_check()
            await _dependency_health_check()
        except Exception as e:
            logger.error("[session-health] Error in health check: %s", e, exc_info=True)
        await asyncio.sleep(AGENT_SESSION_HEALTH_CHECK_INTERVAL)


def format_duration(seconds) -> str:
    """Format seconds into human-readable duration."""
    if seconds is None:
        return "N/A"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_mins = minutes % 60
    return f"{hours}h{remaining_mins}m"


def cleanup_corrupted_agent_sessions() -> int:
    """Delete AgentSession records with corrupted data that prevent .save().

    Detects sessions where the ID field has an invalid length (e.g., 60 chars
    instead of the expected 32 for uuid4), or where .save() raises ModelException.
    These records jam the health check and startup recovery loops with repeated
    errors because they can't be transitioned or finalized through normal ORM ops.

    After deleting corrupted records, rebuilds AgentSession indexes to clean
    orphaned $IndexF/$KeyF/$SortF entries pointing to deleted objects.

    Called by the reflection scheduler as the 'agent-session-cleanup' reflection.
    Also safe to call from startup recovery or the update script.

    Returns the number of corrupted sessions deleted.
    """
    from popoto.exceptions import ModelException

    cleaned = 0
    all_sessions = list(AgentSession.query.all())

    for session in all_sessions:
        session_id_str = str(getattr(session, "id", "") or "")
        is_corrupt = False

        # Check 1: ID length validation (uuid4 strategy expects 32 chars)
        if session_id_str and len(session_id_str) != 32:
            logger.warning(
                "[agent-session-cleanup] Corrupted session detected: id=%s "
                "(length %d, expected 32), session_id=%s",
                session_id_str[:20],
                len(session_id_str),
                getattr(session, "session_id", "?"),
            )
            is_corrupt = True

        # Check 2: Try a no-op save to detect other validation failures
        if not is_corrupt:
            try:
                session.save()
            except (ModelException, Exception) as e:
                if "invalid" in str(e).lower() or "validation" in str(e).lower():
                    logger.warning(
                        "[agent-session-cleanup] Unsaveable session detected: "
                        "id=%s, session_id=%s, error=%s",
                        session_id_str[:20],
                        getattr(session, "session_id", "?"),
                        e,
                    )
                    is_corrupt = True

        if is_corrupt:
            try:
                session.delete()
                cleaned += 1
            except Exception as del_err:
                logger.warning(
                    "[agent-session-cleanup] ORM delete failed for %s, "
                    "attempting direct Redis cleanup: %s",
                    session_id_str[:20],
                    del_err,
                )
                # Fallback: direct Redis key deletion
                try:
                    import redis as _redis

                    r = _redis.Redis()
                    pattern = f"*{session_id_str}*"
                    for key in r.scan_iter(match=pattern):
                        r.delete(key)
                    cleaned += 1
                except Exception as redis_err:
                    logger.error(
                        "[agent-session-cleanup] Direct Redis cleanup failed for %s: %s",
                        session_id_str[:20],
                        redis_err,
                    )

    # Rebuild indexes to clean any remaining orphaned references
    if cleaned > 0:
        try:
            AgentSession.rebuild_indexes()
            logger.info(
                "[agent-session-cleanup] Rebuilt AgentSession indexes after "
                "cleaning %d corrupted session(s)",
                cleaned,
            )
        except Exception as idx_err:
            logger.warning("[agent-session-cleanup] Index rebuild failed: %s", idx_err)
    else:
        logger.debug("[agent-session-cleanup] No corrupted sessions found")

    return cleaned


def recover_orphaned_agent_sessions_all_projects() -> int:
    """Backward-compat alias for cleanup_corrupted_agent_sessions."""
    return cleanup_corrupted_agent_sessions()


def _cleanup_orphaned_claude_processes() -> int:
    """Kill orphaned Claude Code CLI subprocesses from prior worker/bridge runs.

    On process restart, SDK subprocesses from the old process may still be alive
    because Python only cancels asyncio tasks (not OS processes).
    These zombies block new workers via _ensure_worker's .done() check and
    consume resources.

    Finds all 'claude' processes whose parent is PID 1 (orphaned), then
    kills them with SIGTERM/SIGKILL.

    Returns the number of processes killed.
    """
    logger = logging.getLogger(__name__)
    killed = 0
    current_pid = os.getpid()

    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude_agent_sdk/_bundled/claude"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        pids = result.stdout.strip().split("\n")
        for pid_str in pids:
            try:
                pid = int(pid_str.strip())
                if pid == current_pid:
                    continue

                # Check parent PID — if PPID is 1 (orphaned), it's stale
                ppid_result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if ppid_result.returncode != 0:
                    continue

                ppid = int(ppid_result.stdout.strip())

                # Only kill if truly orphaned (PPID=1, meaning parent died)
                if ppid != 1:
                    continue

                logger.warning(
                    "[cleanup] Killing orphaned Claude subprocess PID %d (PPID=%d)",
                    pid,
                    ppid,
                )
                os.kill(pid, signal.SIGTERM)
                # Wait up to 3 seconds for graceful exit
                for _ in range(6):
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    logger.warning("[cleanup] Force-killing Claude subprocess PID %d", pid)
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                killed += 1

            except (ValueError, ProcessLookupError, PermissionError) as e:
                logger.debug("[cleanup] Could not kill PID %s: %s", pid_str, e)

    except subprocess.TimeoutExpired:
        logger.warning("[cleanup] Timeout scanning for orphaned Claude processes")
    except Exception as e:
        logger.debug("[cleanup] Error scanning for orphaned processes: %s", e)

    return killed
