"""Session tracking and management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class Session:
    """Represents a user session."""
    session_id: str
    user_id: str
    chat_id: str
    created_at: datetime
    last_activity: datetime
    message_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_minutes(self) -> float:
        """Get session duration in minutes."""
        return (self.last_activity - self.created_at).total_seconds() / 60

    @property
    def is_active(self) -> bool:
        """Check if session is considered active (activity in last hour)."""
        return (datetime.now() - self.last_activity).total_seconds() < 3600

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "message_count": self.message_count,
            "duration_minutes": self.duration_minutes,
            "is_active": self.is_active,
            "metadata": self.metadata,
        }


@dataclass
class SessionMetrics:
    """Metrics about sessions."""
    total_sessions: int
    active_sessions: int
    average_duration_minutes: float
    total_messages: int
    peak_concurrent: int
    sessions_last_hour: int


class SessionTracker:
    """Track and manage user sessions."""

    def __init__(self):
        """Initialize the session tracker."""
        self._sessions: dict[str, Session] = {}
        self._peak_concurrent = 0

    def create_session(self, user_id: str, chat_id: str, metadata: dict[str, Any] | None = None) -> Session:
        """Create a new session.

        Args:
            user_id: The user identifier.
            chat_id: The chat identifier.
            metadata: Optional additional metadata.

        Returns:
            The created Session.
        """
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        now = datetime.now()

        session = Session(
            session_id=session_id,
            user_id=user_id,
            chat_id=chat_id,
            created_at=now,
            last_activity=now,
            metadata=metadata or {},
        )

        self._sessions[session_id] = session

        # Update peak concurrent
        active_count = len(self.get_active_sessions())
        self._peak_concurrent = max(self._peak_concurrent, active_count)

        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The Session if found, None otherwise.
        """
        return self._sessions.get(session_id)

    def get_session_by_chat(self, chat_id: str) -> Session | None:
        """Get the most recent session for a chat.

        Args:
            chat_id: The chat identifier.

        Returns:
            The most recent Session for the chat, or None.
        """
        chat_sessions = [s for s in self._sessions.values() if s.chat_id == chat_id]
        if not chat_sessions:
            return None
        return max(chat_sessions, key=lambda s: s.last_activity)

    def update_activity(self, session_id: str) -> bool:
        """Update the last activity time for a session.

        Args:
            session_id: The session identifier.

        Returns:
            True if updated, False if session not found.
        """
        session = self._sessions.get(session_id)
        if session:
            session.last_activity = datetime.now()
            session.message_count += 1
            return True
        return False

    def end_session(self, session_id: str) -> bool:
        """End and remove a session.

        Args:
            session_id: The session identifier.

        Returns:
            True if ended, False if session not found.
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_active_sessions(self) -> list[Session]:
        """Get all currently active sessions.

        Returns:
            List of active sessions.
        """
        return [s for s in self._sessions.values() if s.is_active]

    def get_all_sessions(self) -> list[Session]:
        """Get all tracked sessions.

        Returns:
            List of all sessions.
        """
        return list(self._sessions.values())

    def cleanup_stale_sessions(self, max_age_hours: int = 24) -> int:
        """Remove sessions older than max_age_hours without activity.

        Args:
            max_age_hours: Maximum age in hours before cleanup.

        Returns:
            Number of sessions cleaned up.
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        stale_ids = [
            sid for sid, session in self._sessions.items()
            if session.last_activity < cutoff
        ]

        for sid in stale_ids:
            del self._sessions[sid]

        return len(stale_ids)

    def get_session_metrics(self) -> SessionMetrics:
        """Get metrics about current sessions.

        Returns:
            SessionMetrics with current statistics.
        """
        all_sessions = list(self._sessions.values())
        active_sessions = self.get_active_sessions()

        if all_sessions:
            total_duration = sum(s.duration_minutes for s in all_sessions)
            avg_duration = total_duration / len(all_sessions)
            total_messages = sum(s.message_count for s in all_sessions)
        else:
            avg_duration = 0.0
            total_messages = 0

        hour_ago = datetime.now() - timedelta(hours=1)
        sessions_last_hour = sum(
            1 for s in all_sessions
            if s.created_at >= hour_ago
        )

        return SessionMetrics(
            total_sessions=len(all_sessions),
            active_sessions=len(active_sessions),
            average_duration_minutes=avg_duration,
            total_messages=total_messages,
            peak_concurrent=self._peak_concurrent,
            sessions_last_hour=sessions_last_hour,
        )

    def get_user_sessions(self, user_id: str) -> list[Session]:
        """Get all sessions for a specific user.

        Args:
            user_id: The user identifier.

        Returns:
            List of sessions for the user.
        """
        return [s for s in self._sessions.values() if s.user_id == user_id]
