"""Popoto Redis models for system state management.

Provides fast, queryable Redis models for all persistent data:
- TelegramMessage: incoming/outgoing Telegram messages (source of truth)
- Link: URLs shared in Telegram chats
- Chat: Telegram chat ID to name mapping
- AgentSession: unified agent work lifecycle (replaces RedisJob + SessionLog)
- BridgeEvent: structured bridge events for analytics
- DeadLetter: failed message queue
- Reflection: per-reflection scheduler state (unified recurring task tracking)
- ReflectionRun: per-day reflection execution state
- ReflectionIgnore: ignored bug patterns with TTL-based expiry
- DedupRecord: per-chat message deduplication tracking
- SeenIssue: GitHub issue poller seen-issue tracking
- ObserverTelemetry: observer agent telemetry counters and events
"""

from models.agent_session import AgentSession
from models.bridge_event import BridgeEvent
from models.chat import Chat
from models.dead_letter import DeadLetter
from models.dedup import DedupRecord
from models.link import Link
from models.reflection import Reflection
from models.reflections import ReflectionIgnore, ReflectionRun
from models.seen_issue import SeenIssue
from models.telegram import TelegramMessage
from models.telemetry import ObserverTelemetry

# Backward compatibility alias
SessionLog = AgentSession

__all__ = [
    "AgentSession",
    "SessionLog",
    "DedupRecord",
    "DeadLetter",
    "BridgeEvent",
    "ObserverTelemetry",
    "Reflection",
    "ReflectionIgnore",
    "ReflectionRun",
    "SeenIssue",
    "TelegramMessage",
    "Chat",
    "Link",
]
