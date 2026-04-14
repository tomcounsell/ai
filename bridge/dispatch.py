"""Centralized dispatch wrapper for Telegram-originating session enqueues.

Every Telegram-originating session enqueue in the live handler goes through
``dispatch_telegram_session``, which enqueues and then records dedup atomically
from the caller's perspective. This removes the distributed per-call-site
contract that previously required every early-return branch in
``bridge/telegram_bridge.py::handler`` to remember a manual
``record_message_processed`` call.

Non-enqueue branches (steering an existing session, finalizing a dormant
session) call :func:`record_telegram_message_handled` to record dedup without
enqueuing -- the reconciler treats the message as handled and skips it on its
next 3-minute scan.

Recovery paths (``bridge/catchup.py``, ``bridge/reconciler.py``) intentionally
do NOT use this wrapper. They know they are writing to dedup and keep their
explicit two-step pairing so future maintainers can see that these paths are
different from the live handler's dispatch.
"""

from __future__ import annotations

import logging

from agent.agent_session_queue import enqueue_agent_session
from bridge.dedup import record_message_processed
from config.enums import SessionType

logger = logging.getLogger(__name__)


async def dispatch_telegram_session(
    *,
    project_key: str,
    session_id: str,
    working_dir: str,
    message_text: str,
    sender_name: str,
    chat_id: str,
    telegram_message_id: int,
    chat_title: str | None = None,
    priority: str = "normal",
    revival_context: str | None = None,
    sender_id: int | None = None,
    slug: str | None = None,
    task_list_id: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
    correlation_id: str | None = None,
    scheduled_at: float | None = None,
    parent_agent_session_id: str | None = None,
    telegram_message_key: str | None = None,
    session_type: str = SessionType.PM,
    scheduling_depth: int = 0,
    project_config: dict | None = None,
    extra_context_overrides: dict | None = None,
) -> int:
    """Enqueue a Telegram-originating session and record dedup.

    Signature mirrors :func:`agent.agent_session_queue.enqueue_agent_session`
    exactly so the live handler can pass through its existing kwargs.

    Ordering matters: ``enqueue_agent_session`` must complete successfully
    before ``record_message_processed`` runs. If the enqueue raises, we do
    NOT record dedup -- the reconciler will pick the message up on its next
    scan, which is the correct recovery behavior.

    This wrapper does NOT catch exceptions from ``enqueue_agent_session``.
    A failed enqueue propagates so the caller can log/handle, and leaves
    dedup unrecorded so the reconciler can retry.

    Returns:
        The queue depth returned by ``enqueue_agent_session``.
    """
    depth = await enqueue_agent_session(
        project_key=project_key,
        session_id=session_id,
        working_dir=working_dir,
        message_text=message_text,
        sender_name=sender_name,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        chat_title=chat_title,
        priority=priority,
        revival_context=revival_context,
        sender_id=sender_id,
        slug=slug,
        task_list_id=task_list_id,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        parent_agent_session_id=parent_agent_session_id,
        telegram_message_key=telegram_message_key,
        session_type=session_type,
        scheduling_depth=scheduling_depth,
        project_config=project_config,
        extra_context_overrides=extra_context_overrides,
    )
    await record_message_processed(chat_id, telegram_message_id)
    return depth


async def record_telegram_message_handled(chat_id, telegram_message_id: int) -> None:
    """Record dedup for a message handled WITHOUT enqueuing a new session.

    Use this for the non-enqueue branches of the live handler: intake
    interjection (steer existing session), intake acknowledgment (finalize
    dormant session), and the in-memory coalescing guard (merge into a
    pending session). These branches "handled" the message -- the reconciler
    must skip it on its next scan -- but they did not produce a new
    ``AgentSession``.

    This is a semantic sibling of :func:`dispatch_telegram_session`. Keeping
    it as a distinct function earns its weight by emitting a ``logger.debug``
    line with a grep-able signature that distinguishes steered/finalized
    outcomes from the enqueue path. The underlying
    :func:`bridge.dedup.record_message_processed` already swallows exceptions
    and logs at warning level, so this function never raises either.
    """
    logger.debug(
        "telegram message handled without enqueue: chat=%s msg=%s",
        chat_id,
        telegram_message_id,
    )
    await record_message_processed(chat_id, telegram_message_id)
