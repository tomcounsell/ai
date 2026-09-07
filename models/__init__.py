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
- ImprovementCharter: immutable versions of the improvement controller's charter
- ImprovementEvidence: durable observations the improvement loop reasons from
- ImprovementModelRevision: revisions of the system's model of itself
- ImprovementCase: weaknesses the system has decided to pursue
- ImprovementInvestigation: bounded acts of finding something out
- ImprovementExperiment: candidate changes under frozen, preregistered contracts
- ImprovementEvaluation: paired, blinded measurements of a candidate
- ImprovementRelease: qualified candidates proposed for human review
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
from models.improvement_case import ImprovementCase  # noqa: E402
from models.improvement_charter import ImprovementCharter  # noqa: E402
from models.improvement_evaluation import ImprovementEvaluation  # noqa: E402
from models.improvement_evidence import ImprovementEvidence  # noqa: E402
from models.improvement_experiment import ImprovementExperiment  # noqa: E402
from models.improvement_investigation import ImprovementInvestigation  # noqa: E402
from models.improvement_model_revision import ImprovementModelRevision  # noqa: E402
from models.improvement_release import ImprovementRelease  # noqa: E402
from models.job import Job  # noqa: E402
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
    "ImprovementCase",
    "ImprovementCharter",
    "ImprovementEvaluation",
    "ImprovementEvidence",
    "ImprovementExperiment",
    "ImprovementInvestigation",
    "ImprovementModelRevision",
    "ImprovementRelease",
    "Job",
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
