"""reflections/housekeeping/redis_ttl_cleanup.py — Remove expired Redis records.

What it does: Calls cleanup_expired/cleanup_old on TelegramMessage, Link, Chat,
    AgentSession (90-day), BridgeEvent (7-day), and ReflectionIgnore (deletes records).
Cadence: 86400s (daily) (bounds Redis growth without thrashing)
Failure modes:
    - any cleanup raises -> caught, status="error" with the exception in summary
Related reflections:
    - redis_quality_audit: reads the same models this cleanup prunes
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Run TTL cleanup on all Redis models to remove expired records.

    Cleans up: TelegramMessage, Link, Chat, AgentSession (90-day),
    BridgeEvent (7-day), ReflectionIgnore (expired).
    """
    findings = []

    try:
        from models.agent_session import AgentSession
        from models.bridge_event import BridgeEvent
        from models.chat import Chat
        from models.link import Link
        from models.reflections import ReflectionIgnore
        from models.telegram import TelegramMessage

        msg_deleted = TelegramMessage.cleanup_expired(max_age_days=90)
        link_deleted = Link.cleanup_expired(max_age_days=90)
        chat_deleted = Chat.cleanup_expired(max_age_days=90)
        session_deleted = AgentSession.cleanup_expired(max_age_days=90)
        event_deleted = BridgeEvent.cleanup_old(max_age_seconds=7 * 86400)
        ignore_deleted = ReflectionIgnore.cleanup_expired()

        total = (
            msg_deleted
            + link_deleted
            + chat_deleted
            + session_deleted
            + event_deleted
            + ignore_deleted
        )
        summary = (
            f"Redis cleanup: {total} expired records removed "
            f"(msgs={msg_deleted}, links={link_deleted}, "
            f"chats={chat_deleted}, sessions={session_deleted}, "
            f"events={event_deleted}, "
            f"ignores={ignore_deleted})"
        )
        logger.info(summary)
        findings.append(summary)

    except Exception as e:
        logger.warning(f"Redis TTL cleanup failed (non-fatal): {e}")
        return {"status": "error", "findings": [], "summary": f"Redis cleanup error: {e}"}

    return {"status": "ok", "findings": findings, "summary": findings[0] if findings else "ok"}
