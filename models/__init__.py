"""Popoto Redis models for system state management.

Provides fast, queryable Redis models for all persistent data:
- TelegramMessage: incoming/outgoing Telegram messages (source of truth)
- Link: URLs shared in Telegram chats
- Chat: Telegram chat ID to name mapping
- SessionLog: agent session lifecycle (replaces AgentSession)
- BridgeEvent: structured bridge events for analytics
- DeadLetter: failed message queue
"""

from models.bridge_event import BridgeEvent
from models.chat import Chat
from models.dead_letter import DeadLetter
from models.link import Link
from models.session_log import SessionLog
from models.telegram import TelegramMessage

__all__ = [
    "DeadLetter",
    "BridgeEvent",
    "TelegramMessage",
    "Chat",
    "Link",
    "SessionLog",
]
