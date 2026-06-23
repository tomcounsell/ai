"""reflections/audits/redis_quality_audit.py — Audit Redis data quality.

What it does: Reads Link/Chat/AgentSession/TelegramMessage records to surface
    unsummarized links, dead channels, error patterns in session transcripts,
    and per-chat message volume (read-only; no writes).
Cadence: 86400s (daily) (surfaces drift in data hygiene without churn)
Failure modes:
    - any model query raises -> caught, appended as a finding, status stays "ok"
Related reflections:
    - redis_ttl_cleanup: removes the expired records this audit reads
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Run Redis data quality checks: unsummarized links, dead channels, error patterns."""
    findings: list[str] = []

    try:
        import time as _time

        from models.agent_session import AgentSession
        from models.chat import Chat
        from models.link import Link
        from models.telegram import TelegramMessage

        week_ago = _time.time() - (7 * 86400)
        month_ago = _time.time() - (30 * 86400)

        # 1. Unsummarized links
        all_links = Link.query.all()
        unsummarized = [
            link
            for link in all_links
            if link.timestamp and link.timestamp > week_ago and not link.ai_summary
        ]
        if unsummarized:
            findings.append(f"{len(unsummarized)} links shared in last 7 days have no AI summary")
            for link in unsummarized[:5]:
                findings.append(
                    f"  Unsummarized: {link.url} (chat={link.chat_id}, status={link.status})"
                )

        # 2. Dead channels
        all_chats = Chat.query.all()
        dead_chats = [chat for chat in all_chats if chat.updated_at and chat.updated_at < month_ago]
        if dead_chats:
            findings.append(f"{len(dead_chats)} chat(s) with no activity in 30+ days")
            for chat in dead_chats[:5]:
                _ua = chat.updated_at
                if isinstance(_ua, datetime):
                    _ua = _ua.timestamp() if _ua.tzinfo else _ua.replace(tzinfo=UTC).timestamp()
                days_inactive = int((_time.time() - (_ua or 0)) / 86400)
                findings.append(
                    f"  Inactive: {chat.chat_name} ({days_inactive} days, type={chat.chat_type})"
                )

        # 3. Error patterns in recent session transcripts
        recent_cutoff = _time.time() - (7 * 86400)
        all_sessions = AgentSession.query.all()
        recent_sessions = [
            s
            for s in all_sessions
            if (
                lambda sa: (
                    sa is not None
                    and (sa.timestamp() if isinstance(sa, datetime) else float(sa)) > recent_cutoff
                )
            )(s.started_at)
        ]

        error_keywords: dict[str, int] = {}
        for session in recent_sessions:
            if not session.log_path:
                continue
            log_path = Path(session.log_path)
            if not log_path.exists():
                continue
            try:
                content = log_path.read_text(errors="replace")
                for keyword in [
                    "ImportError",
                    "ModuleNotFoundError",
                    "ConnectionError",
                    "TimeoutError",
                    "PermissionError",
                    "FileNotFoundError",
                    "KeyError",
                    "AttributeError",
                ]:
                    count = content.count(keyword)
                    if count > 0:
                        error_keywords[keyword] = error_keywords.get(keyword, 0) + count
            except OSError:
                continue

        if error_keywords:
            sorted_errors = sorted(error_keywords.items(), key=lambda x: x[1], reverse=True)
            findings.append(
                f"Error patterns across {len(recent_sessions)} recent session transcripts:"
            )
            for keyword, count in sorted_errors[:5]:
                findings.append(f"  {keyword}: {count} occurrences")

        # 4. Message volume per chat
        all_messages = TelegramMessage.query.all()[:10000]
        recent_messages = [m for m in all_messages if m.timestamp and m.timestamp > week_ago]
        chat_volumes: dict[str, int] = {}
        for msg in recent_messages:
            chat_id = msg.chat_id or "unknown"
            chat_volumes[chat_id] = chat_volumes.get(chat_id, 0) + 1

        if chat_volumes:
            sorted_chats = sorted(chat_volumes.items(), key=lambda x: x[1], reverse=True)
            findings.append(
                f"Message volume (last 7 days): {len(recent_messages)} messages "
                f"across {len(chat_volumes)} chats"
            )
            for chat_id, count in sorted_chats[:3]:
                chat_name = chat_id
                chat_records = Chat.query.filter(chat_id=chat_id)
                if chat_records:
                    chat_name = chat_records[0].chat_name or chat_id
                findings.append(f"  {chat_name}: {count} messages")

    except Exception as e:
        logger.warning(f"Redis data quality check failed (non-fatal): {e}")
        findings.append(f"Data quality check error: {e}")

    summary = f"Data quality: {len(findings)} finding(s)"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
