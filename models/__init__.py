"""Popoto Redis models for system state management.

Provides fast, queryable Redis models for all persistent data:
- TelegramMessage: incoming/outgoing Telegram messages (source of truth)
- Link: URLs shared in Telegram chats
- Chat: Telegram chat ID to name mapping
- AgentSession: unified agent work lifecycle
- BridgeEvent: structured bridge events for analytics
- DeadLetter: failed message queue
- Reflection: per-reflection scheduler state (unified recurring task tracking)
- ReflectionIgnore: ignored bug patterns with TTL-based expiry
- DedupRecord: per-chat message deduplication tracking
- Memory: subconscious memory records (human instructions, agent observations)
- TeammateMetrics: teammate mode classification counters and response times
- KnowledgeDocument: knowledge base indexed documents with embeddings
- DocumentChunk: per-chunk embeddings for fine-grained document search
- PRReviewAudit: deduplication tracker for PR review audit findings
"""

# Install the popoto version-floor interlock BEFORE any model class is
# reachable. Every rebuild_indexes() caller in the repo imports a model through
# this package, so this single install covers them all -- including callers that
# do not exist yet. Under a below-floor popoto, rebuild_indexes() deletes every
# index before it discovers it cannot decode, so refusing up front is the only
# point at which failing is free. See config/popoto_floor.py and issue #2536.
from config.popoto_floor import install_rebuild_interlock

install_rebuild_interlock()

from models.agent_session import AgentSession  # noqa: E402
from models.bridge_event import BridgeEvent  # noqa: E402
from models.chat import Chat  # noqa: E402
from models.dead_letter import DeadLetter  # noqa: E402
from models.dedup import DedupRecord  # noqa: E402
from models.document_chunk import DocumentChunk  # noqa: E402
from models.knowledge_document import KnowledgeDocument  # noqa: E402
from models.link import Link  # noqa: E402
from models.memory import Memory  # noqa: E402
from models.pr_review_audit import PRReviewAudit  # noqa: E402
from models.reflection import Reflection  # noqa: E402
from models.reflection_ignore import ReflectionIgnore  # noqa: E402
from models.room import Room  # noqa: E402
from models.teammate_metrics import TeammateMetrics  # noqa: E402
from models.telegram import TelegramMessage  # noqa: E402

# Backward compatibility alias
SessionLog = AgentSession

__all__ = [
    "AgentSession",
    "SessionLog",
    "DedupRecord",
    "DeadLetter",
    "BridgeEvent",
    "DocumentChunk",
    "KnowledgeDocument",
    "PRReviewAudit",
    "Reflection",
    "ReflectionIgnore",
    "Room",
    "TeammateMetrics",
    "TelegramMessage",
    "Chat",
    "Link",
    "Memory",
]
