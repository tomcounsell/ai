"""Post-execution lifecycle: session finalization, parent transitions, dev completion
handling, and continuation-PM creation."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)

# PM final-delivery protocol (issue #1058).
#
# When the pipeline reaches a terminal state, the worker runs a dedicated
# "compose final summary" turn and delivers the result directly via send_cb,
# bypassing the nudge loop. See `docs/features/pm-final-delivery.md`.
_COMPLETION_PROMPT = (
    "The SDLC pipeline has finished. Context: {context}\n\n"
    "This is your final turn. Write a 2-3 sentence summary for the user covering "
    "what was accomplished and any notable outcomes. Do NOT use any special "
    "markers or format instructions — just write the summary directly."
)

# Background tasks spawned by `_deliver_pipeline_completion`. Drained by the
# worker shutdown sequence so in-flight completion turns either finish or are
# cancelled cleanly.
_pending_completion_tasks: dict[str, asyncio.Task] = {}


async def _complete_agent_session(session: AgentSession, *, failed: bool = False) -> None:
    """Mark a running session as completed (or failed) and persist to Redis.

    Sessions are retained in Redis with their terminal status so that followup
    messages can revive them. The model's TTL (90 days) handles eventual cleanup.

    Delegates all completion side effects (lifecycle log, auto-tag, branch checkpoint,
    parent finalization, status save) to finalize_session() from the lifecycle module.

    Re-reads the session from Redis before finalizing to capture any stage events
    written during execution (e.g., SDLC pipeline transitions). The re-query is
    intentionally status-filter-free: filtering by status="running" would return an
    empty list if the session transitioned away from "running" (e.g., via a concurrent
    path) before _complete_agent_session fires — causing finalize_session() to operate
    on the stale in-memory object and corrupt the status index (session ends up indexed
    under both the old and new status simultaneously). See issue #825.

    Tie-breaking when multiple records share the same session_id: prefer any record
    currently in "running" status (ensures the live session is finalized), fall back
    to most-recent by created_at only if no running records exist.

    Args:
        session: The AgentSession to complete.
        failed: If True, this session failed (used for parent finalization).
    """
    from models.session_lifecycle import finalize_session

    # Re-read from Redis to capture stage events accumulated during execution.
    # The in-memory object may hold a stale snapshot if _cleanup_stale_sessions
    # ran during the session's lifetime (it does finalize and re-create the record).
    # Querying by session_id (not id) finds the current record regardless of id changes.
    session_id = getattr(session, "session_id", None)
    if session_id:
        try:
            # Re-query without status filter: the session may have transitioned away
            # from "running" (e.g., via a concurrent path) before _complete_agent_session
            # fires. Filtering by status="running" would return an empty list in that
            # scenario, causing finalize_session() to operate on the stale in-memory
            # snapshot and corrupt the status index (session ends up indexed under both
            # the old and new status simultaneously).
            #
            # Tie-breaking: prefer any record currently in "running" status first
            # (ensures the live session is selected), then fall back to most-recent
            # by created_at only if no running records exist.
            fresh_records = list(AgentSession.query.filter(session_id=session_id))
            if fresh_records:
                running = [r for r in fresh_records if getattr(r, "status", None) == "running"]
                if running:
                    if len(running) > 1:
                        # Multiple running records — take most recent by created_at
                        running.sort(key=lambda r: r.created_at or 0, reverse=True)
                        logger.warning(
                            "[lifecycle] Multiple running records for session_id=%s — "
                            "using most recent (id=%s)",
                            session_id,
                            getattr(running[0], "id", "?"),
                        )
                    session = running[0]
                else:
                    if len(fresh_records) > 1:
                        # Multiple non-running records — take most recent by created_at
                        fresh_records.sort(
                            key=lambda r: r.created_at or 0,
                            reverse=True,
                        )
                        logger.warning(
                            "[lifecycle] Multiple records for session_id=%s, none running — "
                            "using most recent (id=%s)",
                            session_id,
                            getattr(fresh_records[0], "id", "?"),
                        )
                    session = fresh_records[0]
        except Exception as exc:
            logger.warning(
                "[lifecycle] Redis re-read failed for session_id=%s, falling back to "
                "in-memory object: %s",
                session_id,
                exc,
            )

    status = "failed" if failed else "completed"
    finalize_session(session, status, reason="agent session completed")


def _transition_parent(parent: AgentSession, new_status: str) -> None:
    """Transition a parent session to a new status.

    Delegates to the lifecycle module for consistent lifecycle handling.
    Uses finalize_session() for terminal statuses and transition_status()
    for non-terminal statuses.
    """
    # NOTE: Imports private _transition_parent from lifecycle module — this is
    # intentional. The function is private in the lifecycle module because it's
    # a specialized parent-transition helper, not a general-purpose API. This
    # wrapper exists to keep the import localized to one place.
    from models.session_lifecycle import (
        _transition_parent as _lifecycle_transition_parent,
    )

    _lifecycle_transition_parent(parent, new_status)


def _diagnose_missing_session(session_id: str) -> dict:
    """Check for session diagnostics when Popoto query fails.

    Uses Popoto-native queries and targeted hash existence checks instead
    of raw r.keys() scanning. Returns a dict with diagnostic info to aid
    debugging why the session was not found by the ORM query.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        result = {}

        # Check if the AgentSession hash key exists directly
        hash_key = f"AgentSession:{session_id}"
        hash_exists = POPOTO_REDIS_DB.exists(hash_key)
        result["hash_exists"] = bool(hash_exists)

        if hash_exists:
            ttl = POPOTO_REDIS_DB.ttl(hash_key)
            result["hash_ttl"] = ttl

        # Try Popoto query with session_id filter
        try:
            matches = list(AgentSession.query.filter(session_id=session_id))
            result["popoto_query_matches"] = len(matches)
        except Exception as qe:
            result["popoto_query_error"] = str(qe)

        # Check if session exists by ID (AutoKeyField lookup)
        try:
            by_id = AgentSession.query.filter(id=session_id)
            result["id_query_matches"] = len(list(by_id))
        except Exception:
            result["id_query_matches"] = 0

        return result
    except Exception as e:
        return {"error": str(e)}


def _extract_issue_number(session: Any, agent_session: Any) -> int | None:
    """Extract tracking issue number from session message text or env vars.

    Looks for GitHub issue URL pattern "issues/NNN" in the session message_text
    and in SDLC_TRACKING_ISSUE / SDLC_ISSUE_NUMBER env vars.

    Returns:
        Issue number as int, or None if not found.
    """
    import re as _re

    # Check env vars first (most authoritative)
    for env_key in ("SDLC_TRACKING_ISSUE", "SDLC_ISSUE_NUMBER"):
        val = os.environ.get(env_key)
        if val:
            try:
                return int(val)
            except ValueError:
                pass

    # Search in session message text for "issues/NNN"
    text = ""
    if hasattr(session, "message_text") and session.message_text:
        text = session.message_text
    elif agent_session and hasattr(agent_session, "message_text") and agent_session.message_text:
        text = agent_session.message_text

    if text:
        match = _re.search(r"issues/(\d+)", text)
        if match:
            return int(match.group(1))

    return None


# Maximum continuation PM depth — prevents runaway chains of continuation sessions.
_CONTINUATION_PM_MAX_DEPTH = 3

# Maximum characters of dev-result content to embed into a continuation PM's
# message_text. The prior cap of 500 chars (issue #1109) truncated enriched
# PM-dev payloads after the routing headers (PROJECT/FROM/SESSION_ID/TASK_SCOPE/
# SCOPE ≈ 500 chars), leaving the dev session with gutted instructions. Raised
# to 10_000 so full task content is preserved across the PM→dev handoff.
# Large enough to cover realistic dev-result payloads while still providing
# a defensive upper bound against unbounded message_text growth in Redis.
_DEV_RESULT_PREVIEW_MAX_CHARS = 10_000


def _create_continuation_pm(
    *,
    parent: Any,
    agent_session: Any,
    issue_number: int | None,
    stage: str | None,
    outcome: str,
    result_preview: str,
) -> None:
    """Create a continuation PM session when the parent PM is terminal.

    Called by _handle_dev_session_completion when steer_session() fails because
    the parent PM has already finalized. The continuation PM carries the stage
    result and issue context so the pipeline can resume.

    Uses Redis SETNX deduplication to prevent duplicate continuation PMs when
    multiple dev sessions complete simultaneously for the same parent (Race
    Condition 2 from the plan).

    Stores continuation_depth directly on the AgentSession (O(1) — does NOT
    walk the parent chain, which is fragile under TTL expiry).

    Args:
        parent: The terminal parent PM AgentSession.
        agent_session: The completed dev AgentSession.
        issue_number: The GitHub issue number (may be None).
        stage: The SDLC stage that just completed (may be None).
        outcome: "success" or "fail".
        result_preview: Truncated dev session result, capped at
            ``_DEV_RESULT_PREVIEW_MAX_CHARS`` (10_000) by the caller.
            Preserves full enriched PM→dev payloads; the prior 500-char cap
            silently truncated task content after the routing headers
            (see issue #1109).
    """
    try:
        from models.agent_session import AgentSession as _AgentSession

        # --- Depth cap (CONCERN 4) ---
        parent_depth = 0
        try:
            parent_depth = int(getattr(parent, "continuation_depth", 0) or 0)
        except (TypeError, ValueError):
            parent_depth = 0

        if parent_depth >= _CONTINUATION_PM_MAX_DEPTH:
            logger.error(
                f"[continuation-pm-blocked] Continuation depth {parent_depth} >= "
                f"{_CONTINUATION_PM_MAX_DEPTH} for parent {getattr(parent, 'session_id', '?')}. "
                f"Refusing to create another continuation PM."
            )
            return

        # --- Redis SETNX deduplication (Race Condition 2) ---
        parent_id = getattr(parent, "agent_session_id", None) or getattr(parent, "id", "unknown")
        dedup_key = f"continuation-pm:{parent_id}"
        try:
            from popoto.redis_db import POPOTO_REDIS_DB

            acquired = POPOTO_REDIS_DB.set(dedup_key, "1", nx=True, ex=300)
            if not acquired:
                logger.info(
                    f"[harness] Continuation PM already created for parent {parent_id} "
                    f"(dedup key exists), skipping."
                )
                return
        except Exception as redis_err:
            # If Redis dedup fails, proceed anyway — duplicate is better than none.
            logger.warning(f"[harness] Continuation PM dedup check failed: {redis_err}")

        # --- Build the continuation message ---
        issue_ref = f"issue #{issue_number}" if issue_number else "unknown issue"
        stage_ref = stage or "unknown"
        message = (
            f"CONTINUATION: The previous PM session for {issue_ref} has completed, "
            f"but stage {stage_ref} just finished with outcome: {outcome}.\n\n"
            f"Result preview:\n{result_preview}\n\n"
            f"Resume the SDLC pipeline for {issue_ref}. Assess the current state "
            f"and dispatch the next stage.\n\n"
            f"IMPORTANT: Check for open PRs. If a PR exists and is unmerged, "
            f"the next stage is MERGE (/do-merge). Do NOT signal pipeline completion "
            f"until the PR is merged or closed."
        )
        if issue_number:
            message += f"\n\nTracking: https://github.com/tomcounsell/ai/issues/{issue_number}"

        # --- Create the continuation PM session ---
        new_depth = parent_depth + 1
        continuation = _AgentSession.create(
            session_type="pm",
            project_key=getattr(parent, "project_key", "valor"),
            status="pending",
            chat_id=getattr(parent, "chat_id", None),
            message_text=message,
            parent_agent_session_id=parent_id,
            continuation_depth=new_depth,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        # Copy project_config from parent if available
        try:
            pc = getattr(parent, "project_config", None)
            if pc:
                continuation.project_config = pc
                continuation.save(update_fields=["project_config", "updated_at"])
        except Exception:
            pass

        # --- Metrics ---
        try:
            from popoto.redis_db import POPOTO_REDIS_DB

            POPOTO_REDIS_DB.incr("metrics:continuation_pm_created")
            daily_key = f"metrics:continuation_pm_created:{datetime.now(tz=UTC).date()}"
            POPOTO_REDIS_DB.incr(daily_key)
            POPOTO_REDIS_DB.expire(daily_key, 604800)  # 7 days
        except Exception:
            pass  # Metrics are best-effort

        # --- Structured log (CONCERN 3) ---
        logger.warning(
            f"[continuation-pm-created] parent_id={parent_id} "
            f"issue_number={issue_number} stage={stage_ref} "
            f"continuation_depth={new_depth} "
            f"new_session_id={getattr(continuation, 'session_id', '?')}"
        )

    except Exception as e:
        logger.error(f"[harness] _create_continuation_pm failed: {e}", exc_info=True)


def _pipeline_complete_lock_key(parent_id: str) -> str:
    """Redis key for the pipeline-completion CAS lock."""
    return f"pipeline_complete_pending:{parent_id}"


def _interrupted_sent_key(session_id: str) -> str:
    """Redis key for the interrupted-message dedup lock."""
    return f"interrupted-sent:{session_id}"


async def _deliver_pipeline_completion(
    parent: AgentSession,
    summary_context: str,
    send_cb: Callable[..., Awaitable[Any]] | None,
    chat_id: str | None,
    telegram_message_id: int | None,
) -> None:
    """Compose and deliver the PM session's final summary to the user.

    Plan #1058. Issued when `is_pipeline_complete()` returns True; owns the
    final delivery end-to-end so the PM never re-enters the nudge loop for
    its terminal turn.

    Contract:
      * Sole caller that transitions the parent to ``"completed"`` on the
        success path. Other paths (health-check, ``_finalize_parent_sync``)
        defer via the Redis advisory lock below.
      * Idempotent via Redis SETNX on
        ``pipeline_complete_pending:{parent_id}`` (60s TTL). Secondary
        invocations log at INFO and return.
      * CancelledError-safe: on shutdown, best-effort deliver an
        "I was interrupted" line (dedup'd on ``interrupted-sent:{session_id}``)
        then re-raise to preserve asyncio semantics.
      * All other exceptions are caught and logged; the session finalization
        path still runs so the parent never lingers ``"running"`` indefinitely.

    Args:
        parent: The PM AgentSession whose pipeline has completed.
        summary_context: Outcome text — used verbatim as the fallback if the
            harness returns empty/error.
        send_cb: Transport send callback (from
            ``agent_session_queue._resolve_callbacks``). None is tolerated —
            the runner logs and finalizes the session without delivery.
        chat_id: Target chat id (usually ``parent.chat_id``).
        telegram_message_id: Reply-to message id, optional.
    """
    parent_id = getattr(parent, "agent_session_id", None) or getattr(parent, "id", None)
    session_id = getattr(parent, "session_id", None)
    if not parent_id:
        logger.warning("[completion-runner] Missing parent_id; skipping")
        return

    # CAS lock — Race 1 (runner vs. _finalize_parent_sync) and Race 2
    # (concurrent invocations via _handle_dev_session_completion +
    # _agent_session_hierarchy_health_check). Pattern mirrors the
    # continuation-pm:{parent_id} lock above.
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

        acquired = POPOTO_REDIS_DB.set(_pipeline_complete_lock_key(parent_id), "1", nx=True, ex=60)
        if not acquired:
            logger.info(
                "[completion-runner] pipeline_complete_pending lock held for %s — "
                "another runner owns delivery",
                parent_id,
            )
            return
    except Exception as redis_err:
        # If Redis is unavailable, proceed. Duplicate delivery is preferable
        # to silence on a genuine completion.
        logger.warning(
            "[completion-runner] Redis lock unavailable (%s); proceeding without dedup",
            redis_err,
        )

    # Resolve working_dir from project_config / projects.json for the harness.
    working_dir = _resolve_working_dir_for_parent(parent)

    # Resolve the PM's prior Claude Code UUID (B1 fix). `None` is OK — the
    # harness falls back to `full_context_message` via its no-UUID path.
    try:
        from agent.sdk_client import _get_prior_session_uuid  # noqa: PLC0415

        pm_uuid = _get_prior_session_uuid(session_id) if session_id else None
    except Exception as uuid_err:
        logger.warning("[completion-runner] UUID lookup failed: %s", uuid_err)
        pm_uuid = None

    prompt = _COMPLETION_PROMPT.format(context=summary_context[:3000])

    try:
        try:
            from agent.sdk_client import get_response_via_harness  # noqa: PLC0415

            raw = await get_response_via_harness(
                message=prompt,
                working_dir=working_dir,
                prior_uuid=pm_uuid,
                session_id=session_id,
                full_context_message=prompt,
            )
            final_text = (raw or "").strip()
        except Exception as harness_err:
            logger.warning(
                "[completion-runner] Harness failed (%s) — delivering fallback summary",
                harness_err,
            )
            final_text = ""

        if not final_text:
            final_text = summary_context.strip() or (
                "The pipeline has completed. See session history for details."
            )

        # Deliver.
        if send_cb is not None and chat_id:
            try:
                await send_cb(chat_id, final_text, telegram_message_id, parent)
                logger.info(
                    "[completion-runner] Delivered final summary for %s (%d chars)",
                    parent_id,
                    len(final_text),
                )
            except Exception as send_err:
                logger.error(
                    "[completion-runner] send_cb failed for %s: %s",
                    parent_id,
                    send_err,
                )
        else:
            logger.warning(
                "[completion-runner] No send_cb or chat_id for %s; skipping delivery",
                parent_id,
            )

        # Stamp response_delivered_at and transition to completed. Runner owns
        # this transition (Race 1 / Race 4 mitigation).
        try:
            parent.response_delivered_at = datetime.now(UTC)
            parent.save(update_fields=["response_delivered_at", "updated_at"])
        except Exception as stamp_err:
            logger.warning(
                "[completion-runner] Failed to stamp response_delivered_at: %s",
                stamp_err,
            )

        try:
            from models.session_lifecycle import finalize_session  # noqa: PLC0415

            finalize_session(
                parent, "completed", reason="pipeline complete: final summary delivered"
            )
        except Exception as finalize_err:
            logger.error(
                "[completion-runner] finalize_session(completed) failed for %s: %s",
                parent_id,
                finalize_err,
            )

    except asyncio.CancelledError:
        # Shutdown during runner. Best-effort "interrupted" message with
        # flap-dedup (Risk 6), then re-raise to preserve asyncio semantics.
        if send_cb is not None and chat_id and session_id:
            try:
                await _send_interrupted_message(
                    send_cb, chat_id, telegram_message_id, parent, session_id
                )
            except Exception as int_err:  # pragma: no cover - best-effort
                logger.warning("[completion-runner] interrupted send failed: %s", int_err)
        raise


def _resolve_working_dir_for_parent(parent: AgentSession) -> str:
    """Pick a working directory for the completion-turn harness invocation."""
    try:
        pc = getattr(parent, "project_config", None) or {}
        wd = pc.get("working_directory") if isinstance(pc, dict) else None
        if wd:
            return str(wd)
    except Exception:
        pass
    try:
        from bridge.routing import load_config as _load_projects_config  # noqa: PLC0415

        project_key = getattr(parent, "project_key", None)
        projects = _load_projects_config().get("projects", {})
        cfg = projects.get(project_key, {}) if project_key else {}
        wd = cfg.get("working_directory")
        if wd:
            return str(wd)
    except Exception:
        pass
    return os.getcwd()


async def _send_interrupted_message(
    send_cb: Callable[..., Awaitable[Any]],
    chat_id: str,
    telegram_message_id: int | None,
    parent: AgentSession,
    session_id: str,
) -> None:
    """Best-effort 'I was interrupted' delivery with Risk-6 flap-dedup."""
    should_send = True
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

        acquired = POPOTO_REDIS_DB.set(_interrupted_sent_key(session_id), "1", nx=True, ex=120)
        should_send = bool(acquired)
        if not should_send:
            logger.info(
                "[completion-runner] interrupted-sent dedup held for %s; skipping",
                session_id,
            )
    except Exception as lock_err:
        logger.debug(
            "[completion-runner] interrupted-sent lock unavailable (%s); sending anyway",
            lock_err,
        )

    if not should_send:
        return

    msg = "I was interrupted and will resume automatically. No action needed."
    try:
        await asyncio.wait_for(send_cb(chat_id, msg, telegram_message_id, parent), timeout=2.0)
    except (TimeoutError, Exception) as send_err:
        logger.warning("[completion-runner] interrupted send failed/timed out: %s", send_err)


def schedule_pipeline_completion(
    parent: AgentSession,
    summary_context: str,
    send_cb: Callable[..., Awaitable[Any]] | None,
    chat_id: str | None,
    telegram_message_id: int | None,
) -> asyncio.Task | None:
    """Fire-and-forget scheduler for `_deliver_pipeline_completion`.

    Tracks the task in `_pending_completion_tasks` keyed by parent id so
    worker shutdown can drain it. Deduplicates in-process scheduling so two
    callers in the same worker don't both create tasks (the Redis CAS in
    the runner handles cross-process dedup separately).
    """
    parent_id = getattr(parent, "agent_session_id", None) or getattr(parent, "id", None)
    if not parent_id:
        return None
    existing = _pending_completion_tasks.get(parent_id)
    if existing is not None and not existing.done():
        logger.info(
            "[completion-runner] Completion task already in-flight for %s; skipping duplicate",
            parent_id,
        )
        return existing

    async def _wrapper() -> None:
        try:
            await _deliver_pipeline_completion(
                parent, summary_context, send_cb, chat_id, telegram_message_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - runner wraps its own errors
            logger.error("[completion-runner] Unhandled error for %s: %s", parent_id, e)

    task = asyncio.create_task(_wrapper(), name=f"pipeline_completion:{parent_id}")
    _pending_completion_tasks[parent_id] = task
    task.add_done_callback(lambda t: _pending_completion_tasks.pop(parent_id, None))
    return task


async def drain_pending_completions(timeout: float = 15.0) -> None:
    """Drain in-flight completion-turn tasks during worker shutdown.

    Allocates more time than extraction drain (15s vs 5s) because the
    harness call itself is the whole point of the runner — see Open Question #4
    in the plan. If the timeout fires, CancelledError propagates into the
    runner's handler, which best-effort delivers the interrupted message.
    """
    if not _pending_completion_tasks:
        return
    pending = list(_pending_completion_tasks.values())
    logger.info(
        "[completion-runner] Draining %d pending completion task(s) (timeout=%.1fs)",
        len(pending),
        timeout,
    )
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        task.cancel()
    if still_pending:
        logger.warning(
            "[completion-runner] Cancelled %d completion task(s) past drain timeout",
            len(still_pending),
        )


async def _handle_dev_session_completion(
    session: Any,
    agent_session: Any,
    result: str,
) -> None:
    """Handle SDLC post-completion for dev role sessions run via CLI harness.

    Called after complete_transcript() (which calls _finalize_parent_sync()) has
    already run. This ordering invariant is critical: by the time this function
    executes, _finalize_parent_sync has already transitioned the PM parent to its
    terminal status. The re-check guard below will therefore correctly detect a
    terminal PM and create a continuation PM rather than treating the orphaned
    steering message as successful delivery.

    Must be called after complete_transcript() — NOT before. Calling before
    complete_transcript() causes the race described in issue #987: steer is
    accepted, then _finalize_parent_sync runs and orphans the steering message.

    Classifies the outcome, updates PipelineStateMachine, posts a stage comment
    to the tracking issue, and steers the parent PM session with the completion
    status. If steering fails (parent already terminal), creates a continuation
    PM session to carry the pipeline forward.

    All operations are wrapped in try/except -- failures never crash the worker.

    Args:
        session: The lightweight session record (from _send_callbacks key).
            Always populated from the queue entry — reliable even when
            agent_session is None.
        agent_session: The AgentSession popoto model instance (may be None
            if the status="running" filter raced with a status transition).
        result: Final result text from the harness.
    """
    try:
        # Get parent PM session.
        # Path B fallback: when agent_session is None (status="running" filter
        # returned nothing due to a race with health-check recovery or fast
        # finalization), fall back to session.parent_agent_session_id. The outer
        # `session` param is populated from the queue entry at enqueue time and
        # is reliable regardless of the agent_session lookup result.
        parent_id = getattr(agent_session, "parent_agent_session_id", None) or getattr(
            session, "parent_agent_session_id", None
        )
        if not parent_id:
            logger.debug(
                "[harness] No parent_agent_session_id on dev session or session object, "
                "skipping PM steering"
            )
            return

        from models.agent_session import AgentSession as ParentAgentSession

        parent = ParentAgentSession.get_by_id(parent_id)
        if not parent:
            logger.warning(f"[harness] Parent session {parent_id} not found, skipping PM steering")
            return

        # Classify outcome and update pipeline state
        try:
            from agent.pipeline_state import PipelineStateMachine

            psm = PipelineStateMachine(parent)
            current_stage = psm.current_stage()
            outcome = psm.classify_outcome(current_stage, None, result)
            if outcome == "success":
                psm.complete_stage(current_stage)
            else:
                psm.fail_stage(current_stage)
            logger.info(
                f"[harness] Dev session completion: outcome={outcome}, stage={current_stage}"
            )
        except Exception as psm_err:
            logger.warning(f"[harness] PipelineStateMachine update failed (non-fatal): {psm_err}")
            current_stage = None

        # Set retain_for_resume=True only on BUILD-stage dev sessions so the PM can
        # hard-PATCH resume them via `valor-session resume --id <id>`. BUILD is the only
        # stage where retaining the session transcript is meaningful — it holds the builder
        # reasoning context. The 30-day Meta.ttl acts as the backstop;
        # `valor-session release --pr <N>` clears it on merge.
        try:
            if agent_session and current_stage == "BUILD":
                agent_session.retain_for_resume = True
                agent_session.save(update_fields=["retain_for_resume", "updated_at"])
                logger.info(
                    f"[harness] Set retain_for_resume=True on dev session "
                    f"{getattr(agent_session, 'session_id', '?')} (stage={current_stage})"
                )
        except Exception as retain_err:
            logger.warning(f"[harness] retain_for_resume update failed (non-fatal): {retain_err}")

        if current_stage is None:
            outcome = "success" if result and len(result) > 10 else "fail"

        # Post stage comment to GitHub issue
        issue_number = None
        try:
            issue_number = _extract_issue_number(session, agent_session)
            if issue_number and current_stage:
                from utils.issue_comments import post_stage_comment

                success = post_stage_comment(
                    issue_number=issue_number,
                    stage=current_stage,
                    outcome=outcome,
                )
                if success:
                    logger.info(
                        f"[harness] Posted stage comment: {current_stage} on issue #{issue_number}"
                    )
                else:
                    logger.warning(
                        f"[harness] Failed to post stage comment on issue #{issue_number}"
                    )
        except Exception as comment_err:
            logger.warning(f"[harness] Stage comment posting failed (non-fatal): {comment_err}")

        # ------------------------------------------------------------------
        # PM final-delivery protocol (issue #1058).
        # If the pipeline is complete per `is_pipeline_complete()`, spawn the
        # completion-turn runner and RETURN before issuing a continuation
        # steer. The runner owns final delivery and the parent transition to
        # `"completed"`.
        # ------------------------------------------------------------------
        try:
            from agent.pipeline_complete import (  # noqa: PLC0415
                _check_pr_open,
                is_pipeline_complete,
            )

            psm_states: dict[str, str] = {}
            try:
                # `psm` may be unbound if the earlier try/except failed.
                psm_states = dict(psm.states)  # type: ignore[name-defined]
            except Exception:
                psm_states = {}

            # Call-site gating (Risk 5 / C6): _check_pr_open only for
            # DOCS-completed-MERGE-not-completed corner case. For MERGE-success
            # or non-terminal stages, skip the subprocess.
            pr_open: bool | None = None
            if (
                psm_states.get("DOCS") == "completed"
                and psm_states.get("MERGE") != "completed"
                and issue_number
            ):
                pr_open = _check_pr_open(issue_number)

            is_complete, reason = is_pipeline_complete(psm_states, outcome, pr_open=pr_open)
        except Exception as predicate_err:
            logger.warning(
                "[harness] Pipeline-complete predicate failed (non-fatal): %s", predicate_err
            )
            is_complete = False
            reason = "predicate_error"

        if is_complete:
            # Build a summary context from outcome — used both as the
            # harness prompt context and the fallback on harness failure.
            result_preview = result[:_DEV_RESULT_PREVIEW_MAX_CHARS] if result else "(no result)"
            summary_context = (
                f"Stage {current_stage or 'UNKNOWN'} completed with outcome={outcome} "
                f"(reason={reason}). Result preview: {result_preview}"
            )
            from agent.agent_session_queue import _resolve_callbacks  # noqa: PLC0415

            transport = getattr(parent, "transport", None) or None
            send_cb, _react_cb = _resolve_callbacks(getattr(parent, "project_key", None), transport)
            chat_id = getattr(parent, "chat_id", None)
            telegram_message_id = getattr(parent, "telegram_message_id", None)

            logger.info(
                "[harness] Pipeline complete for parent %s (reason=%s) — invoking "
                "completion-turn runner",
                parent_id,
                reason,
            )
            schedule_pipeline_completion(
                parent, summary_context, send_cb, chat_id, telegram_message_id
            )
            return  # runner owns final delivery + parent transition

        # Steer parent PM session with pipeline state update.
        # Check the return value — if steering fails (parent already terminal),
        # create a continuation PM to carry the pipeline forward.
        try:
            result_preview = result[:_DEV_RESULT_PREVIEW_MAX_CHARS] if result else "(no result)"
            steering_msg = (
                f"Dev session completed. Stage: {current_stage or 'unknown'}. "
                f"Outcome: {outcome}. Result preview: {result_preview}\n\n"
                f"IMPORTANT: If an open PR exists for this issue, the pipeline is NOT complete. "
                f"You MUST invoke /sdlc to dispatch /do-merge before signaling pipeline completion."
            )
            from agent.session_executor import steer_session as _steer_session  # noqa: PLC0415

            steer_result = _steer_session(parent.session_id, steering_msg)
            if steer_result.get("success"):
                # CONCERN 1 guard: steer was accepted, but parent may finalize
                # before processing the message (race with _finalize_parent_sync).
                # Re-check parent status to detect this race.
                try:
                    refreshed_parent = ParentAgentSession.get_by_id(parent_id)
                    refreshed_status = getattr(refreshed_parent, "status", None)
                    if refreshed_parent and refreshed_status in _TERMINAL_STATUSES:
                        logger.warning(
                            f"[harness] Steer accepted but parent {parent.session_id} finalized "
                            f"before processing (race with _finalize_parent_sync) — "
                            f"creating continuation PM"
                        )
                        _create_continuation_pm(
                            parent=refreshed_parent,
                            agent_session=agent_session,
                            issue_number=issue_number,
                            stage=current_stage,
                            outcome=outcome,
                            result_preview=result_preview,
                        )
                    else:
                        logger.info(f"[harness] Steered parent PM session {parent.session_id}")
                        # Immediately re-enqueue parent so it picks up the
                        # steering message without waiting for the periodic
                        # hierarchy health check.  Issue #1004.
                        if refreshed_status == "waiting_for_children":
                            try:
                                from models.session_lifecycle import (
                                    transition_status as _ts,
                                )

                                _ts(
                                    refreshed_parent,
                                    "pending",
                                    reason="child completed, steering injected",
                                )
                                logger.info(
                                    f"[harness] Re-enqueued parent {parent.session_id} "
                                    f"from waiting_for_children to pending"
                                )
                            except Exception as re_enqueue_err:
                                logger.warning(
                                    f"[harness] Failed to re-enqueue parent: {re_enqueue_err}"
                                )
                except Exception:
                    # If refresh fails, assume steer worked
                    logger.info(f"[harness] Steered parent PM session {parent.session_id}")
            else:
                logger.warning(
                    f"[harness] Steering rejected for parent {parent.session_id}: "
                    f"{steer_result.get('error')} — creating continuation PM"
                )
                _create_continuation_pm(
                    parent=parent,
                    agent_session=agent_session,
                    issue_number=issue_number,
                    stage=current_stage,
                    outcome=outcome,
                    result_preview=result_preview,
                )
        except Exception as steer_err:
            logger.warning(
                f"[harness] PM session steering failed (non-fatal): {steer_err} "
                f"— creating continuation PM"
            )
            try:
                result_preview = result[:_DEV_RESULT_PREVIEW_MAX_CHARS] if result else "(no result)"
                _create_continuation_pm(
                    parent=parent,
                    agent_session=agent_session,
                    issue_number=issue_number,
                    stage=current_stage,
                    outcome=outcome,
                    result_preview=result_preview,
                )
            except Exception as cont_err:
                logger.error(f"[harness] Continuation PM creation also failed: {cont_err}")

    except Exception as e:
        logger.warning(f"[harness] _handle_dev_session_completion failed (non-fatal): {e}")
