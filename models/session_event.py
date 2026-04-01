"""Structured event model for AgentSession lifecycle tracking.

SessionEvent replaces the flat string history entries with structured data.
Each event captures a lifecycle moment (status transition, summary, delivery,
stage change, checkpoint) with typed fields.

Events are serialized as dicts in AgentSession.session_events (a ListField).
Pydantic validates at write time; stored as plain dicts in Redis via msgpack.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Types of session lifecycle events."""

    LIFECYCLE = "lifecycle"  # Status transitions (pending->running, etc.)
    SUMMARY = "summary"  # Session summary text
    DELIVERY = "delivery"  # Result text delivered to user
    STAGE = "stage"  # SDLC stage state change
    CHECKPOINT = "checkpoint"  # Commit SHA checkpoint
    CLASSIFY = "classify"  # Classification result
    SYSTEM = "system"  # System events (errors, warnings)
    USER = "user"  # User input events


class SessionEvent(BaseModel):
    """A single lifecycle event in an AgentSession.

    Stored as a dict in AgentSession.session_events ListField.
    Validated by Pydantic at creation time, stored as plain dict.
    """

    event_type: str  # Accept arbitrary event types for backward compat (EventType enum for standard ones)
    timestamp: float = Field(default_factory=lambda: datetime.now(tz=UTC).timestamp())
    text: str = ""
    data: dict | None = None  # Optional structured payload

    @classmethod
    def lifecycle(cls, transition: str, context: str = "") -> "SessionEvent":
        """Create a lifecycle transition event."""
        return cls(
            event_type=EventType.LIFECYCLE,
            text=f"{transition}" + (f": {context}" if context else ""),
        )

    @classmethod
    def summary(cls, text: str) -> "SessionEvent":
        """Create a summary event."""
        return cls(event_type=EventType.SUMMARY, text=text)

    @classmethod
    def delivery(cls, text: str) -> "SessionEvent":
        """Create a delivery (result_text) event."""
        return cls(event_type=EventType.DELIVERY, text=text)

    @classmethod
    def stage_change(cls, stage: str, status: str, stages_dict: dict | None = None) -> "SessionEvent":
        """Create a stage state change event."""
        return cls(
            event_type=EventType.STAGE,
            text=f"{stage}={status}",
            data={"stages": stages_dict} if stages_dict else None,
        )

    @classmethod
    def checkpoint(cls, commit_sha: str) -> "SessionEvent":
        """Create a commit checkpoint event."""
        return cls(event_type=EventType.CHECKPOINT, text=commit_sha)

    @classmethod
    def classify(cls, classification_type: str, confidence: float | None = None) -> "SessionEvent":
        """Create a classification event."""
        return cls(
            event_type=EventType.CLASSIFY,
            text=classification_type,
            data={"confidence": confidence} if confidence is not None else None,
        )

    @classmethod
    def system(cls, text: str) -> "SessionEvent":
        """Create a system event."""
        return cls(event_type=EventType.SYSTEM, text=text)

    @classmethod
    def user(cls, text: str) -> "SessionEvent":
        """Create a user input event."""
        return cls(event_type=EventType.USER, text=text)
