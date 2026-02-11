"""AgentSession model - track agent session lifecycle in Redis."""

from popoto import Field, IntField, KeyField, Model, SortedField, UniqueKeyField

MSG_MAX_CHARS = 20_000


class AgentSession(Model):
    """Tracks active/dormant/completed agent sessions with queryable state.

    Replaces implicit session tracking scattered across branch names,
    in-memory dicts, and process state JSONs.
    """

    session_id = UniqueKeyField()
    project_key = KeyField()
    status = KeyField(default="active")  # active, dormant, completed, failed
    chat_id = Field()
    sender = Field()
    started_at = SortedField(type=float, sort_by="project_key")
    last_activity = SortedField(type=float)
    tool_call_count = IntField(default=0)
    branch_name = Field(null=True)
    work_item_slug = Field(null=True)  # Slug for tier 2 named work items
    message_text = Field(max_length=MSG_MAX_CHARS, null=True)
    classification_type = Field(null=True)  # bug, feature, or chore
    classification_confidence = Field(type=float, null=True)  # 0.0-1.0
