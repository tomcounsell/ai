"""Popoto Redis models for system state management.

Provides fast, queryable Redis models for all persistent data:
- TelegramMessage: incoming/outgoing Telegram messages (source of truth)
- Link: URLs shared in Telegram chats
- Chat: Telegram chat ID to name mapping
- AgentSession: unified agent work lifecycle (replaces RedisJob + SessionLog)
- BridgeEvent: structured bridge events for analytics
- DeadLetter: failed message queue
- DaydreamRun: per-day daydream execution state
- DaydreamIgnore: ignored bug patterns with TTL-based expiry
- LessonLearned: institutional memory from session reflections
"""

from models.agent_session import AgentSession
from models.bridge_event import BridgeEvent
from models.chat import Chat
from models.daydream import DaydreamIgnore, DaydreamRun, LessonLearned
from models.dead_letter import DeadLetter
from models.link import Link
from models.telegram import TelegramMessage

# Backward compatibility alias
SessionLog = AgentSession

__all__ = [
    "AgentSession",
    "SessionLog",
    "DeadLetter",
    "BridgeEvent",
    "DaydreamIgnore",
    "DaydreamRun",
    "LessonLearned",
    "TelegramMessage",
    "Chat",
    "Link",
]
