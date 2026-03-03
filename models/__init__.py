"""Popoto Redis models for system state management.

Provides fast, queryable Redis models for all persistent data:
- TelegramMessage: incoming/outgoing Telegram messages (source of truth)
- Link: URLs shared in Telegram chats
- Chat: Telegram chat ID to name mapping
- AgentSession: unified agent work lifecycle (replaces RedisJob + SessionLog)
- BridgeEvent: structured bridge events for analytics
- DeadLetter: failed message queue
- ReflectionRun: per-day reflection execution state
- ReflectionIgnore: ignored bug patterns with TTL-based expiry
- LessonLearned: institutional memory from session reflections
"""

from models.agent_session import AgentSession
from models.bridge_event import BridgeEvent
from models.chat import Chat
from models.dead_letter import DeadLetter
from models.link import Link
from models.reflections import LessonLearned, ReflectionIgnore, ReflectionRun
from models.telegram import TelegramMessage

# Backward compatibility alias
SessionLog = AgentSession

__all__ = [
    "AgentSession",
    "SessionLog",
    "DeadLetter",
    "BridgeEvent",
    "ReflectionIgnore",
    "ReflectionRun",
    "LessonLearned",
    "TelegramMessage",
    "Chat",
    "Link",
]
