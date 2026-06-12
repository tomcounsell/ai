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

# PM final-delivery protocol (issue #1058 + D6 hardening from #1129).
#
# When the pipeline reaches a terminal state, the worker runs a dedicated
# "compose final summary" turn and delivers the result directly via send_cb,
# bypassing the nudge loop. See `docs/features/pm-final-delivery.md`.
#
# D6 contract (from plan docs/plans/session-model-routing-fallback.md):
#   - Always Opus on both passes (quality trumps cost for this one call).
#   - 2-pass drafter: Pass 1 drafts, Pass 2 self-reviews / refines.
#   - No-silent-fail: drafter failures log at ERROR + deliver a visible
#     degraded fallback; Pass 2 failures log at WARNING + fall back to
#     Pass 1's draft. The final_text is guaranteed non-empty before send_cb.
#   - Always-finalize: `finalize_session(parent, "completed", ...)` runs in
#     a `finally` block so the PM session reaches terminal state even when
#     the drafter or delivery step misbehaves.
#   - Ollama fallback: deferred to #1137. Until that ships, Anthropic-down
#     manifests as a visible degraded-fallback message + ERROR log.
#
# Both prompts use a PREFIX + concatenation pattern (not `.format()`) so that
# literal ``{`` / ``}`` characters in the embedded context or draft (e.g. a
# dict repr or JSON snippet from a Dev session summary) do not crash the
# prompt construction (ADV-1 fix).
_COMPLETION_PROMPT_PREFIX = (
    "The SDLC pipeline has finished. "
    "This is your final turn. Write a 2-3 sentence summary for the user covering "
    "what was accomplished and any notable outcomes. Do NOT use any special "
    "markers or format instructions — just write the summary directly.\n\n"
    "CONTEXT:\n"
)

_COMPLETION_REVIEW_PROMPT_PREFIX = (
    "Below is a draft final-delivery message for the user. Review it against "
    "these criteria and return a refined version:\n\n"
    "1. SHORT — no wasted words. Cut anything that isn't load-bearing.\n"
    "2. DENSE — maximum information per word. Preserve concrete outcomes.\n"
    "3. THOUGHTFUL — phrase like a colleague writing with care, not a template.\n\n"
    "Return ONLY the refined message. No preamble, no meta-commentary, no "
    "markdown headers. Just the message as it should be sent.\n\n"
    "DRAFT:\n"
)


# Sentinel literal assigned to ``final_text`` before any drafter pass runs.
# Every successful drafter path overwrites it; if the suppression check ever
# sees this value it must early-return (suppressing this string against any
# baseline is not a meaningful signal). See issue #1262 / plan
# ``docs/plans/dedupe-completion-emit.md``.
_DEGRADED_FINAL_TEXT_SENTINEL = "[completion-runner internal error — no final_text assigned]"


def _build_degraded_fallback(summary_context: str) -> str:
    """Compose a visible-but-explicit fallback when the drafter fails.

    Satisfies D6(c) simultaneously: (a) non-empty, (b) visibly loud (operator
    can see this was a degraded delivery), (c) preserves whatever context the
    pipeline did gather. Used when Pass 1 fails, returns empty, or returns
    the ``_HARNESS_NOT_FOUND_PREFIX`` sentinel. See #1137 for the Ollama-
    backed recovery that will eventually replace this fallback.
    """
    context = (summary_context or "").strip()
    if context:
        return f"[drafter unavailable — pipeline completed] {context[:1500]}"
    return "[drafter unavailable — pipeline completed, see session history for details]"


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


def _pipeline_complete_lock_key(parent_id: str) -> str:
    """Redis key for the pipeline-completion CAS lock."""
    return f"pipeline_complete_pending:{parent_id}"


def _interrupted_sent_key(session_id: str) -> str:
    """Redis key for the interrupted-message dedup lock."""
    return f"interrupted-sent:{session_id}"


# ── Mid-session-send-aware completion suppression (issue #1262) ─────────────
#
# The completion runner emits a final summary at session-end. When a sub-skill
# (e.g. `/do-docs`, `/sdlc`) has already posted a `valor-telegram send` mid-
# session, that send lands in `parent.chat_message_log` (Path B) but NOT in
# `parent.recent_sent_drafts` (Path A only). Without this suppression layer,
# the completion runner re-emits a reformatted version of the same content.
#
# The three helpers below implement a hybrid: a bigram-Jaccard pre-check
# (low-threshold call, see plan §"Two-tier verdict via low-threshold call")
# escalates to a Haiku judge in the borderline band [0.55, 0.75). Final cutoff
# above 0.75 suppresses without LLM cost. All paths are fail-open — a buggy
# suppression check must NEVER block a legitimate completion delivery.
#
# Plan: docs/plans/dedupe-completion-emit.md

# Low-band edge passed to ``should_suppress`` so verdict.jaccard is always
# populated when the score is meaningful. The high-band cutoff is read from
# env at call time (default ``0.75``) and enforced in the caller.
_COMPLETION_SUPPRESSION_LOW_CUTOFF = 0.55


def _build_completion_baseline(
    parent: AgentSession,
    *,
    window_seconds: int | None = None,
    max_entries: int = 5,
) -> list[dict]:
    """Adapt ``parent.chat_message_log`` outbound entries to the
    ``should_suppress`` ``recent_sent_drafts`` shape.

    Returns ``[{ts, text, artifacts}, ...]``; empty list if no qualifying
    entries. Filters to ``direction == "out"`` and entries inside
    ``window_seconds`` (defaults to ``REDUNDANCY_WINDOW_SECONDS``). Computes
    ``artifacts`` via ``bridge.message_drafter.extract_artifacts(content)``.

    Fail-open: any exception → returns ``[]`` (the suppression check is then
    a no-op and delivery proceeds normally).

    The baseline source is ``chat_message_log`` (NOT ``recent_sent_drafts``)
    because Path B (`valor-telegram send` mid-session) only writes to the
    former. See plan §Solution > Technical Approach.
    """
    try:
        import time as _t

        from bridge.message_drafter import extract_artifacts
        from bridge.redundancy_filter import REDUNDANCY_WINDOW_SECONDS

        effective_window = (
            int(REDUNDANCY_WINDOW_SECONDS) if window_seconds is None else int(window_seconds)
        )
        now = _t.time()
        entries = getattr(parent, "chat_message_log", None) or []
        out: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("direction") != "out":
                continue
            ts = entry.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            if (now - ts) > effective_window:
                continue
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            try:
                artifacts = extract_artifacts(content) or {}
            except Exception:
                artifacts = {}
            out.append({"ts": ts, "text": content, "artifacts": artifacts})
        return out[-max_entries:]
    except Exception:
        return []


async def _await_outbox_drained(
    parent: AgentSession,
    *,
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.1,
) -> bool:
    """Wait for the parent session's outbox queue to be empty (best effort).

    Bounds the read-after-write race between ``cmd_send`` (returns immediately
    after ``r.rpush``) and the relay drain loop (``_append_outbound_chat_log``
    runs after the underlying Telegram send succeeds). The publisher and
    consumer run in different processes; there is no cross-process ordering
    guarantee.

    Uses the synchronous ``redis.Redis.from_url(...)`` client (matching the
    pattern in ``agent/output_handler.py`` and ``bridge/telegram_relay.py``;
    the codebase has no async-redis usage) wrapped via ``asyncio.to_thread``
    so the event loop is not blocked by the sub-millisecond ``LLEN`` call.

    Returns:
        ``True`` if the outbox drained inside ``timeout_seconds``, ``False``
        on timeout. Fail-open: returns ``True`` on any exception so a Redis
        outage cannot block delivery.
    """
    try:
        import asyncio as _asyncio
        import os
        import time as _time

        import redis  # sync client — codebase has no async-redis usage

        session_id = getattr(parent, "session_id", None)
        if not session_id:
            return True

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        deadline = _time.time() + timeout_seconds
        queue_key = f"telegram:outbox:{session_id}"
        while _time.time() < deadline:
            length = await _asyncio.to_thread(r.llen, queue_key)
            if length == 0:
                return True
            await _asyncio.sleep(poll_interval)
        return False
    except Exception:
        return True  # fail-open — never block delivery on monitoring


async def _judge_completion_novelty(
    prior_text: str,
    prior_ts: float,
    draft_text: str,
) -> bool:
    """Borderline-band Haiku judge: is ``draft_text`` materially new vs.
    ``prior_text`` (sent at ``prior_ts``)?

    Returns ``True`` to suppress (judge says "restate"), ``False`` to send
    (judge says "new" OR any failure). Fail-open: every exception path
    returns ``False`` so the completion still ships when the judge is down.

    Pattern adapted from ``bridge/read_the_room.py::read_the_room`` —
    ``semaphore_slot`` + ``async with anthropic.AsyncAnthropic(timeout=...)``,
    NO outer ``asyncio.wait_for``. Uses the ``MODEL_FAST`` (Haiku) family
    with a single ``tool_use`` block.
    """
    try:
        import time as _t

        import anthropic

        from agent.anthropic_client import semaphore_slot
        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        # Format relative time delta so the judge can weight stale-vs-fresh
        # context (Risk 1 mitigation: bias toward "new" when the prior is
        # older than ~2 minutes — the user has likely scrolled away).
        try:
            age_secs = max(0, int(_t.time() - float(prior_ts)))
        except Exception:
            age_secs = 0
        if age_secs < 60:
            relative_time = f"{age_secs}s ago"
        elif age_secs < 3600:
            relative_time = f"{age_secs // 60}m ago"
        else:
            relative_time = f"{age_secs // 3600}h ago"

        tool = {
            "name": "completion_novelty_verdict",
            "description": (
                "Decide whether the candidate completion-summary draft restates the "
                "prior message or contains materially-new information for the user."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["restate", "new"],
                        "description": (
                            "'restate' = draft is substantially the same content as "
                            "the prior message; suppress. 'new' = draft adds material "
                            "outcomes the user does not yet have; deliver."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short machine-readable reason string.",
                    },
                },
                "required": ["action", "reason"],
            },
        }

        # Local variable name is `judge_system` (NOT the obvious-looking
        # alternative) because tests/unit/test_session_completion.py forbids
        # that literal kwarg-shaped token anywhere in this file — Risk 4
        # regression guard for the harness drafter calls. This Haiku judge
        # is NOT a harness call (it talks to the Anthropic SDK directly).
        judge_system = (
            "You are a strict deduplication judge for a developer-assistant chat. "
            "A sub-skill already sent a message to the user during this session. "
            "Now the session-completion runner wants to send a final summary. "
            "Your job: decide whether the final summary is materially-new for the "
            "user (deliver) or substantially the same as the prior message (suppress).\n"
            "\n"
            "Bias toward 'new' when the prior message is older than ~2 minutes — "
            "the user has likely scrolled away and benefits from a fresh anchor. "
            "Bias toward 'restate' when the prior message is recent and the draft "
            "is a reformatted version with no new outcomes (no new PR/commit/error/decision)."
        )
        user_payload = (
            f"## Prior message (sent {relative_time})\n{prior_text}\n\n"
            f"## Final-summary draft about to be sent\n{draft_text}\n\n"
            "Return your verdict via the completion_novelty_verdict tool."
        )

        async with semaphore_slot():
            async with anthropic.AsyncAnthropic(
                api_key=get_anthropic_api_key(),
                timeout=3.0,
            ) as client:
                message = await client.messages.create(
                    model=MODEL_FAST,
                    max_tokens=200,
                    system=judge_system,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "completion_novelty_verdict"},
                    messages=[{"role": "user", "content": user_payload}],
                )

        content = getattr(message, "content", None) or []
        for block in content:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "completion_novelty_verdict"
            ):
                payload = getattr(block, "input", None) or {}
                action = payload.get("action")
                if action == "restate":
                    return True
                if action == "new":
                    return False
        # No usable tool_use block → fail-open (deliver).
        return False
    except Exception as judge_err:
        logger.warning(
            "[completion-runner] Haiku novelty judge failed (non-fatal, defaulting to deliver): %s",
            judge_err,
        )
        return False


def _queue_completion_suppress_reaction(
    parent: AgentSession,
    chat_id: str,
    reply_to_msg_id: int,
    emoji: str = "👀",
) -> bool:
    """Queue a 👀 reaction on the user's anchor message via the canonical
    outbox path (mirrors :meth:`TelegramRelayOutputHandler._build_reaction_payload`).

    Returns True on success, False on any error (logged at WARNING). The
    completion runner uses the return value only for logging; failures are
    non-fatal — the session still finalizes cleanly.
    """
    try:
        import json
        import os
        import time as _t

        import redis

        session_id = getattr(parent, "session_id", None)
        if not session_id:
            logger.warning(
                "[completion-runner] cannot queue suppress reaction — no session_id on parent"
            )
            return False

        # Mirror of TelegramRelayOutputHandler._build_reaction_payload — keep in sync.
        # (See agent/output_handler.py:789-820. We inline the schema here rather
        # than importing the static method to avoid pulling the entire output-
        # handler module into the completion-runner import graph for one dict.)
        payload = {
            "type": "reaction",
            "chat_id": chat_id,
            "reply_to": int(reply_to_msg_id) if reply_to_msg_id else None,
            "emoji": str(emoji) if emoji is not None else None,
            "session_id": session_id,
            "timestamp": _t.time(),
        }
        queue_key = f"telegram:outbox:{session_id}"
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
        return True
    except Exception as react_err:
        logger.warning(
            "[completion-runner] failed to queue suppress reaction (non-fatal): %s",
            react_err,
        )
        return False


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

    # Terminal-status guard (kill-is-terminal, #1208). Runs BEFORE the CAS lock
    # below so (a) a killed parent never blocks lock acquisition for healthy
    # work on an unrelated session, and (b) no pipeline_complete_pending:{id}
    # Redis key is ever written for a dead session, leaving the lock keyspace
    # clean. ``completed`` parents are explicitly allowed through — the
    # idempotency path at finalize_session handles re-finalize.
    from models.session_lifecycle import TERMINAL_STATUSES  # noqa: PLC0415

    parent_status = getattr(parent, "status", None)
    if parent_status in TERMINAL_STATUSES and parent_status != "completed":
        logger.info(
            "[completion-runner] Skipping pipeline completion for %s — parent terminal (status=%s)",
            parent_id,
            parent_status,
        )
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

    # Mid-session-send-aware context injection (issue #1262). Re-fetch the
    # parent so a stale in-memory copy from earlier in the runner doesn't
    # shadow a fresh chat_log append from a Path B `valor-telegram send`.
    # Pattern matches models/agent_session.py:1407-1410's append-to-log
    # re-fetch defense against the same stale-copy hazard.
    try:
        refreshed = AgentSession.get_by_id(parent.agent_session_id)
        if refreshed is not None:
            parent = refreshed
    except Exception as refetch_err:
        logger.warning(
            "[completion-runner] parent re-fetch before Pass 1 failed (non-fatal): %s",
            refetch_err,
        )

    # Build a chat-log block from outbound entries. Mirrors (does NOT share
    # code with) bridge/message_drafter.py:1262-1276 — the two surfaces are
    # intentionally decoupled per spike-1 / spike-4. Cap at 5 entries to
    # bound prompt growth. Fail-open: any error → empty block.
    chat_log_block = ""
    try:
        baseline_for_prompt = _build_completion_baseline(parent, max_entries=5)
        if baseline_for_prompt:
            lines = [
                f"[out] {entry.get('text', '').strip()}"
                for entry in baseline_for_prompt
                if (entry.get("text") or "").strip()
            ]
            if lines:
                chat_log_block = (
                    "\n\nYou already sent these messages in this thread "
                    "(do not repeat them — only add materially-new context):\n"
                    + "\n".join(lines)
                    + "\n"
                )
    except Exception as chat_log_err:
        logger.warning(
            "[completion-runner] chat-log prompt block build failed (non-fatal): %s",
            chat_log_err,
        )

    # Build the Pass 1 prompt via concat (not .format()) so literal ``{`` / ``}``
    # in summary_context (e.g. JSON snippets, dict reprs) cannot crash us (ADV-1).
    prompt = _COMPLETION_PROMPT_PREFIX + (summary_context or "")[:3000] + chat_log_block

    # D6 v2: 2-pass drafter + no-silent-fail + always-finalize.
    # - Pass 1 uses session_id=None (S-1): do NOT write the drafter's UUID
    #   over the PM's claude_session_uuid. Drafter UUID is discarded.
    # - Pass 2 uses prior_uuid=None, session_id=None (ADV-2): review prompt is
    #   self-contained (Pass 1 draft embedded); resuming the PM session here
    #   would pollute PM history with drafter + review turns.
    # - Both passes pin model="opus" regardless of PM session's model.
    # - Ollama fallback for Anthropic-down path is deferred to #1137; until
    #   then, Pass 1 failure → visible degraded-fallback message.
    # Sentinel init — must be overwritten by every path below (refined text,
    # Pass 1 draft, or degraded fallback). D6(c) "never return empty" — any
    # code path that reaches send_cb with this value is a bug.
    delivery_attempted = False
    final_text: str = _DEGRADED_FINAL_TEXT_SENTINEL
    cancelled = False
    try:
        from agent.session_executor import (  # noqa: PLC0415
            _HARNESS_NOT_FOUND_PREFIX,
        )
    except Exception:
        # Defence in depth: fall back to the known literal if the import
        # fails (e.g. during partial reloads in tests).
        _HARNESS_NOT_FOUND_PREFIX = "Error: CLI harness not found"  # noqa: N806

    try:
        from agent.sdk_client import get_response_via_harness  # noqa: PLC0415

        # --- Pass 1: Draft ---
        draft_text: str = ""
        pass1_failed = False
        pass1_failure_mode = ""
        try:
            raw1 = await get_response_via_harness(
                message=prompt,
                working_dir=working_dir,
                prior_uuid=pm_uuid,
                session_id=None,  # S-1: discard drafter UUID; don't pollute PM record
                full_context_message=prompt,
                model="opus",  # D6(a): always Opus on final-delivery drafter
                # NOTE: When #1137 lands (Ollama credit-exhaust fallback for the
                # harness), this call site is the priority consumer. On
                # Anthropic-down, Ollama will back-fill instead of triggering
                # the degraded-fallback branch below.
            )
            draft_text = (raw1 or "").strip()
            if not draft_text:
                pass1_failed = True
                pass1_failure_mode = "empty"
            elif draft_text.startswith(_HARNESS_NOT_FOUND_PREFIX):
                pass1_failed = True
                pass1_failure_mode = "sentinel"
        except Exception as harness_err:
            pass1_failed = True
            pass1_failure_mode = "exception"
            logger.error(
                "[completion-runner][DEGRADED] Pass 1 failure mode=exception session_id=%s err=%s",
                session_id,
                harness_err,
                exc_info=True,
            )

        if pass1_failed:
            if pass1_failure_mode != "exception":
                logger.error(
                    "[completion-runner][DEGRADED] Pass 1 failure mode=%s session_id=%s",
                    pass1_failure_mode,
                    session_id,
                )
            # Best-effort metric: bump a daily counter so operators can detect
            # a spike in degraded deliveries (e.g. when Anthropic is down).
            try:
                from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

                counter_key = (
                    f"completion_runner:degraded_fallback:daily:"
                    f"{datetime.now(UTC).strftime('%Y%m%d')}"
                )
                POPOTO_REDIS_DB.incr(counter_key)
                POPOTO_REDIS_DB.expire(counter_key, 604800)  # 7-day TTL
            except Exception as metric_err:
                logger.warning(
                    "[completion-runner] Degraded-fallback metric emit failed: %s",
                    metric_err,
                )
            final_text = _build_degraded_fallback(summary_context)
        else:
            # --- Pass 2: Self-Review / Refine ---
            # Embed Pass 1's draft by concatenation (not .format()) so literal
            # {/} in the draft (code snippets, JSON) don't crash us (ADV-1).
            review_prompt = _COMPLETION_REVIEW_PROMPT_PREFIX + draft_text
            refined_text: str = ""
            pass2_failed = False
            pass2_failure_mode = ""
            try:
                raw2 = await get_response_via_harness(
                    message=review_prompt,
                    working_dir=working_dir,
                    prior_uuid=None,  # ADV-2: isolate from PM session history
                    session_id=None,  # ADV-2: no UUID writeback
                    full_context_message=None,
                    model="opus",
                )
                refined_text = (raw2 or "").strip()
                if not refined_text:
                    pass2_failed = True
                    pass2_failure_mode = "empty"
                elif refined_text.startswith(_HARNESS_NOT_FOUND_PREFIX):
                    pass2_failed = True
                    pass2_failure_mode = "sentinel"
            except Exception as refine_err:
                pass2_failed = True
                pass2_failure_mode = "exception"
                logger.warning(
                    "[completion-runner] Pass 2 exception session_id=%s err=%s — "
                    "falling back to Pass 1 draft",
                    session_id,
                    refine_err,
                    exc_info=True,
                )

            if pass2_failed:
                if pass2_failure_mode == "sentinel":
                    logger.error(
                        "[completion-runner] Pass 2 returned _HARNESS_NOT_FOUND_PREFIX "
                        "sentinel session_id=%s — falling back to Pass 1 draft",
                        session_id,
                    )
                elif pass2_failure_mode == "empty":
                    logger.warning(
                        "[completion-runner] Pass 2 empty session_id=%s — "
                        "falling back to Pass 1 draft",
                        session_id,
                    )
                final_text = draft_text
            else:
                final_text = refined_text

        # final_text is guaranteed non-empty at this point (either refined,
        # Pass 1 draft, or degraded fallback). D6(c) "never return empty".

        # --- Mid-session-send-aware suppression (issue #1262) ---
        # Decide whether to skip the auto-emit because a sub-skill already
        # delivered substantively-the-same content via Path B
        # (`valor-telegram send`) earlier in this session. The whole block is
        # try/except'd with fail-open semantics — a buggy suppression check
        # MUST NOT block a legitimate completion delivery.
        suppress_decision = False
        try:
            # Early-return guard: never run suppression on the sentinel
            # (bigram-Jaccard against the literal "[completion-runner internal
            # error ..." is meaningless) or on empty/whitespace text (the
            # downstream send path handles that case).
            if (
                not final_text
                or not final_text.strip()
                or final_text == _DEGRADED_FINAL_TEXT_SENTINEL
            ):
                pass  # skip suppression check; let existing send path handle
            elif send_cb is None or not chat_id:
                pass  # nothing to suppress; the no-send path below logs
            else:
                # Wait for the outbox to drain so any in-flight Path B sends
                # are reflected in chat_message_log. Fail-open on Redis errors.
                await _await_outbox_drained(parent)

                # Re-fetch parent again to capture any chat_log writes that
                # landed during the wait. Same defense as above the prompt.
                try:
                    refreshed_after_wait = AgentSession.get_by_id(parent.agent_session_id)
                    if refreshed_after_wait is not None:
                        parent = refreshed_after_wait
                except Exception as refetch_err:
                    logger.warning(
                        "[completion-runner] parent re-fetch before suppression "
                        "check failed (non-fatal): %s",
                        refetch_err,
                    )

                baseline = _build_completion_baseline(parent)
                if baseline:
                    # High-confidence cutoff (env-tunable). Read once per call.
                    # Names HIGH_CUTOFF / LOW_CUTOFF are deliberate constants
                    # (referenced verbatim in the plan's Verification table).
                    HIGH_CUTOFF = float(  # noqa: N806
                        os.environ.get("DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD", "0.75")
                    )
                    LOW_CUTOFF = _COMPLETION_SUPPRESSION_LOW_CUTOFF  # noqa: N806

                    from bridge.message_drafter import extract_artifacts as _xa
                    from bridge.redundancy_filter import should_suppress as _ss

                    # Call should_suppress with the LOW threshold so
                    # SuppressionVerdict.jaccard is populated for any match
                    # >= 0.55. The HIGH cutoff is enforced by the caller below
                    # (see plan §"Two-tier verdict via low-threshold call").
                    #
                    # session_status=None bypasses the _TERMINAL_STATUSES
                    # exemption in bridge/redundancy_filter.py:161-162. That
                    # exemption is correct for the in-session drafter path
                    # (TelegramRelayOutputHandler.send) where final messages
                    # must always deliver; the completion runner is a
                    # different surface (out-of-band post-session emit) where
                    # dedupe-against-mid-session-sends IS desired. See
                    # docs/plans/dedupe-completion-emit.md and #1262.
                    #
                    # expectations=None: Pass 2 returns plain text, not a
                    # MessageDraft, so there is no expectations concept here.
                    verdict = _ss(
                        final_text,
                        _xa(final_text),
                        baseline,
                        expectations=None,
                        session_status=None,
                        threshold=LOW_CUTOFF,
                    )

                    if verdict.action == "suppress" and verdict.jaccard is None:
                        # Should not happen — suppress branch always populates
                        # jaccard (bridge/redundancy_filter.py:218-224). Defensive.
                        logger.warning(
                            "[completion-runner] suppress verdict missing jaccard; "
                            "defaulting to send for %s",
                            parent_id,
                        )
                    elif verdict.action == "suppress" and verdict.jaccard >= HIGH_CUTOFF:
                        # High-confidence duplicate — suppress without LLM cost.
                        suppress_decision = True
                        logger.info(
                            "[completion-runner] Suppressed final emit for %s "
                            "(jaccard=%.2f, judge=n/a, decision=high_confidence)",
                            parent_id,
                            verdict.jaccard,
                        )
                    elif (
                        verdict.action == "suppress" and LOW_CUTOFF <= verdict.jaccard < HIGH_CUTOFF
                    ):
                        # Borderline — escalate to Haiku judge.
                        idx = verdict.matched_index
                        prior = (
                            baseline[idx]
                            if (idx is not None and 0 <= idx < len(baseline))
                            else None
                        )
                        if prior is None:
                            logger.warning(
                                "[completion-runner] borderline verdict but "
                                "matched_index=%r out of range (baseline len=%d); "
                                "defaulting to send for %s",
                                idx,
                                len(baseline),
                                parent_id,
                            )
                        else:
                            judge_verdict = await _judge_completion_novelty(
                                prior_text=prior["text"],
                                prior_ts=prior["ts"],
                                draft_text=final_text,
                            )
                            if judge_verdict:
                                suppress_decision = True
                                logger.info(
                                    "[completion-runner] Suppressed final emit "
                                    "for %s (jaccard=%.2f, judge=restate, "
                                    "decision=borderline_haiku_restate)",
                                    parent_id,
                                    verdict.jaccard,
                                )
                            else:
                                logger.info(
                                    "[completion-runner] Delivering final emit for %s "
                                    "(jaccard=%.2f, judge=new, decision=borderline_haiku_new)",
                                    parent_id,
                                    verdict.jaccard,
                                )
                    else:
                        # verdict.action == "send" — below LOW cutoff, new
                        # artifact, or other legitimate send reason. Proceed.
                        logger.info(
                            "[completion-runner] Delivering final emit for %s "
                            "(reason=%s, decision=below_low_cutoff_or_other_send_reason)",
                            parent_id,
                            verdict.reason,
                        )
        except Exception as suppress_err:
            logger.warning(
                "[completion-runner] suppression-block crashed (non-fatal, "
                "falling through to deliver): %s",
                suppress_err,
            )
            suppress_decision = False

        # --- Suppress branch: queue 👀 reaction (or silent fall-through) ---
        if suppress_decision:
            if telegram_message_id is not None and chat_id:
                _queue_completion_suppress_reaction(parent, chat_id, int(telegram_message_id))
            else:
                logger.warning(
                    "[completion-runner] suppress decision but no anchor "
                    "message_id; falling silent for %s",
                    parent_id,
                )
            # delivery_attempted stays False so response_delivered_at is NOT
            # stamped (intentional silent suppression — see plan §Risk 5).
        elif send_cb is not None and chat_id:
            # --- Deliver ---
            delivery_attempted = True
            try:
                await send_cb(chat_id, final_text, telegram_message_id, parent)
                logger.info(
                    "[completion-runner] Delivered final summary for %s (%d chars)",
                    parent_id,
                    len(final_text),
                )
            except Exception as send_err:
                # D6(c) v2: send_cb failure stays log-and-continue (no re-raise).
                # Upstream retry ladder does not exist; re-raising would strand
                # the session mid-flight. The "no silent fail" contract is
                # enforced at the drafter layer, not the transport layer.
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
    except asyncio.CancelledError:
        # Shutdown during runner. Best-effort "interrupted" message with
        # flap-dedup (Risk 6), then re-raise to preserve asyncio semantics.
        # Set `cancelled=True` so the finally block below skips both the
        # response_delivered_at stamp (nothing was delivered) and
        # finalize_session (the shutdown path owns that transition).
        cancelled = True
        if send_cb is not None and chat_id and session_id:
            try:
                await _send_interrupted_message(
                    send_cb, chat_id, telegram_message_id, parent, session_id
                )
            except Exception as int_err:  # pragma: no cover - best-effort
                logger.warning("[completion-runner] interrupted send failed: %s", int_err)
        raise
    finally:
        # On cancellation, the except branch above has already emitted an
        # "interrupted" message and is about to re-raise. Skip both stamping
        # and finalization — the shutdown path owns those.
        if not cancelled:
            # ADV-2 gate: only stamp response_delivered_at when we actually
            # tried to deliver. Preserves the existing "time the user
            # received the final message" contract; a no-send_cb path
            # leaves it unset.
            if delivery_attempted:
                try:
                    parent.response_delivered_at = datetime.now(UTC)
                    parent.save(update_fields=["response_delivered_at", "updated_at"])
                except Exception as stamp_err:
                    logger.warning(
                        "[completion-runner] Failed to stamp response_delivered_at: %s",
                        stamp_err,
                    )

            # D6(c) always-finalize: run regardless of drafter / delivery
            # outcome so the PM session reaches a terminal state. Previously
            # this lived inside the main try-block and silently got skipped
            # when an earlier exception escaped.
            #
            # StatusConflictError handling (#1208): The kill-is-terminal guard
            # in finalize_session() rejects terminal->different-terminal flips
            # by default. If the parent was killed mid-pipeline (operator kill
            # racing the runner) the runner-entry guard at the top of this
            # function should already have bailed; if we reach here and STILL
            # see a conflict, log at INFO — this is the expected "guard fired"
            # outcome, not an alarm. Genuine concurrency anomalies fall through
            # to the generic Exception branch and log at ERROR.
            try:
                from models.session_lifecycle import (  # noqa: PLC0415
                    StatusConflictError,
                    finalize_session,
                )

                finalize_session(
                    parent, "completed", reason="pipeline complete: final summary delivered"
                )
            except StatusConflictError as finalize_conflict:
                logger.info(
                    "[completion-runner] Skipping finalize for %s: %s",
                    parent_id,
                    finalize_conflict,
                )
            except Exception as finalize_err:
                logger.error(
                    "[completion-runner] finalize_session(completed) failed for %s: %s",
                    parent_id,
                    finalize_err,
                )


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

    # Terminal-status guard (kill-is-terminal, #1208). Mirrors the guard in
    # _deliver_pipeline_completion; bailing here prevents creating an asyncio
    # task and registering an entry in _pending_completion_tasks for a parent
    # that's already terminal (most commonly: killed). The runner's guard is
    # the load-bearing check; this is purely an early-out optimization.
    from models.session_lifecycle import TERMINAL_STATUSES  # noqa: PLC0415

    parent_status = getattr(parent, "status", None)
    if parent_status in TERMINAL_STATUSES and parent_status != "completed":
        logger.info(
            "[completion-runner] Skipping schedule for %s — parent terminal (status=%s)",
            parent_id,
            parent_status,
        )
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
