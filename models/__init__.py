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
"""

from models.agent_session import AgentSession
from models.bridge_event import BridgeEvent
from models.chat import Chat
from models.cyclic_episode import CyclicEpisode
from models.dead_letter import DeadLetter
from models.link import Link
from models.procedural_pattern import ProceduralPattern
from models.reflections import ReflectionIgnore, ReflectionRun
from models.telegram import TelegramMessage

# Backward compatibility alias
SessionLog = AgentSession

__all__ = [
    "AgentSession",
    "SessionLog",
    "CyclicEpisode",
    "ProceduralPattern",
    "DeadLetter",
    "BridgeEvent",
    "ReflectionIgnore",
    "ReflectionRun",
    "TelegramMessage",
    "Chat",
    "Link",
]
