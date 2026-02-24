"""SessionLog model - tracks agent session lifecycle with transcript file path.

Merges and replaces AgentSession. Adds log_path for transcript files,
tags ListField for categorization, and turn_count for session analytics.
"""

import time

from popoto import (
    Field,
    IntField,
    KeyField,
    ListField,
    Model,
    SortedField,
    UniqueKeyField,
)

MSG_MAX_CHARS = 50_000


class SessionLog(Model):
    """Tracks agent session lifecycle with link to transcript file.

    Replaces AgentSession entirely. All AgentSession fields are carried
    over, plus log_path (path to transcript .txt file), tags (ListField
    for categorization), turn_count, and completed_at.

    Transcript .txt files live at logs/sessions/{session_id}/transcript.txt
    and are kept indefinitely. Redis metadata follows the 3-month TTL.
    """

    session_id = UniqueKeyField()
    project_key = KeyField()
    status = KeyField(default="active")  # active, dormant, completed, failed
    chat_id = KeyField(null=True)
    sender = Field(null=True)
    started_at = SortedField(type=float, partition_by="project_key")
    last_activity = SortedField(type=float)
    completed_at = Field(type=float, null=True)
    turn_count = IntField(default=0)
    tool_call_count = IntField(default=0)
    log_path = Field(null=True, max_length=1000)  # Path to transcript .txt file
    summary = Field(null=True, max_length=MSG_MAX_CHARS)
    branch_name = Field(null=True)
    work_item_slug = Field(null=True)  # Slug for tier 2 named work items
    tags = ListField(null=True)  # e.g. ["pr-review", "compacted", "hotfix"]
    classification_type = Field(null=True)  # bug, feature, or chore
    classification_confidence = Field(type=float, null=True)  # 0.0-1.0

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete SessionLog Redis metadata older than max_age_days.

        Transcript .txt files are NOT deleted — they are kept indefinitely.
        This only removes Redis metadata for old sessions.
        Returns count deleted.
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_sessions = cls.query.all()
        deleted = 0
        for session in all_sessions:
            if session.started_at and session.started_at < cutoff:
                session.delete()
                deleted += 1
        return deleted
