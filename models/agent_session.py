"""AgentSession model - unified lifecycle tracking for agent work.

Single Popoto model with session_type discriminator ("chat" or "dev").
Popoto does not support model inheritance, so ChatSession and DevSession
are distinguished by the session_type field with factory methods and
derived properties providing type-specific behavior.

ChatSession (session_type="chat"): Read-only Agent SDK session, PM persona.
  Owns the Telegram conversation, orchestrates work, spawns DevSessions.
DevSession (session_type="dev"): Full-permission Agent SDK session, Dev persona.
  Does the actual coding work, runs SDLC pipeline stages.

Status lifecycle:
  pending -> running -> active -> dormant -> completed | failed | waiting_for_children
"""

import json as _json
import logging
from datetime import UTC, datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    DictField,
    Field,
    IndexedField,
    IntField,
    KeyField,
    ListField,
    Model,
    SortedField,
)

from config.enums import ClassificationType, SessionType
from models.session_event import SessionEvent

logger = logging.getLogger(__name__)

HISTORY_MAX_ENTRIES = 20
STEERING_QUEUE_MAX = 10  # Max buffered steering messages per session

# SDLC stages in pipeline order
SDLC_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]

# Backward-compatible aliases (import from config.enums for new code)
SESSION_TYPE_CHAT = SessionType.CHAT
SESSION_TYPE_DEV = SessionType.DEV


class AgentSession(Model):
    """Unified model for all Agent SDK sessions, discriminated by session_type.

    Single Popoto model with a session_type discriminator ("chat" or "dev").

    ChatSession (session_type="chat"):
        Read-only Agent SDK session, PM persona. Owns the Telegram
        conversation, orchestrates work, spawns DevSessions.
    DevSession (session_type="dev"):
        Full-permission Agent SDK session, Dev persona. Does the actual
        coding work, runs SDLC pipeline stages.

    Status values:
        pending  - Queued, waiting for worker
        running  - Worker picked up, agent executing
        active   - Session in progress (transcript tracking)
        dormant  - Paused on open question
        waiting_for_children - Parent job waiting for child jobs to complete
        completed - Work finished successfully
        failed   - Work failed
    """

    # === Identity ===
    job_id = AutoKeyField()
    session_id = Field()  # Telegram-derived session identifier (e.g., tg_project_chatid_msgid)
    session_type = KeyField(null=True)  # "chat" or "dev" — discriminator
    project_key = KeyField()
    status = IndexedField(default="pending")  # Non-key field with secondary index for .filter()

    # === Queue fields ===
    priority = Field(default="normal")  # urgent | high | normal | low
    scheduled_at = DatetimeField(null=True)  # UTC datetime; _pop_job() skips if > now()
    created_at = SortedField(type=datetime, partition_by="project_key")
    started_at = DatetimeField(null=True)  # Cannot be SortedField because it starts as None
    updated_at = DatetimeField(auto_now=True, null=True)  # Renamed from last_activity
    completed_at = DatetimeField(null=True)
    working_dir = Field()

    # === Telegram origin (consolidated) ===
    initial_telegram_message = DictField(null=True)  # {sender_name, sender_id, message_text, ...}

    chat_id = KeyField(null=True)

    # === Extra context (consolidated) ===
    extra_context = DictField(null=True)  # revival_context, classification_type/confidence, etc.

    task_list_id = Field(null=True)
    auto_continue_count = Field(type=int, default=0)

    # === Cross-reference to TelegramMessage ===
    telegram_message_key = Field(
        null=True
    )  # msg_id of the TelegramMessage that triggered this session (Popoto key)

    # === Session fields ===
    turn_count = IntField(default=0)
    tool_call_count = IntField(default=0)
    log_path = Field(null=True)
    branch_name = Field(null=True)
    tags = ListField(null=True)

    # === Structured event log (replaces history, summary, result_text, stage_states) ===
    session_events = ListField(null=True)  # List of SessionEvent dicts

    issue_url = Field(null=True)
    plan_url = Field(null=True)
    pr_url = Field(null=True)

    # === Claude Code identity mapping ===
    claude_session_uuid = Field(null=True)

    # === Tracing ===
    correlation_id = Field(null=True)  # End-to-end request tracing ID

    # === Watchdog fields ===
    watchdog_unhealthy = Field(null=True)  # Reason string when flagged unhealthy, None when healthy

    # === Session mode ===
    session_mode = Field(null=True)

    # === Semantic routing fields ===
    context_summary = Field(null=True)  # What this session is about
    expectations = Field(null=True)  # What the agent needs from the human

    # === Steering fields ===
    queued_steering_messages = ListField(null=True)

    # === ChatSession delivery fields ===
    # Stop-hook review gate: agent's final delivery decision.
    # Set by the stop hook after the agent reviews its draft output.
    # "send" = deliver delivery_text; "react" = emoji only; "silent" = nothing.
    # None = no review gate ran (subagent/programmatic session) -> fall through to summarizer.
    delivery_action = Field(null=True)
    delivery_text = Field(null=True)  # Final message text (for send/edit)
    delivery_emoji = Field(null=True)  # Emoji for react-only path

    # === PM self-messaging ===
    pm_sent_message_ids = ListField(null=True)

    # === DevSession fields (null when session_type="chat") ===
    parent_chat_session_id = KeyField(null=True)  # Logical FK -> ChatSession
    slug = Field(null=True)  # Derives branch, plan path, worktree

    # === Session hierarchy fields ===
    parent_job_id = KeyField(null=True)

    # === Backward-compatible field name mapping ===

    # DatetimeField names that should auto-convert float timestamps
    _DATETIME_FIELDS = {"scheduled_at", "started_at", "updated_at", "completed_at"}

    def __init__(self, **kwargs):
        """Initialize AgentSession with backward-compatible field name support."""
        kwargs = self.__class__._normalize_kwargs(kwargs)
        super().__init__(**kwargs)

    def __setattr__(self, name, value):
        """Auto-convert float timestamps to datetime for DatetimeField fields."""
        if name in self._DATETIME_FIELDS and isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, tz=UTC)
        super().__setattr__(name, value)

    @classmethod
    def _normalize_kwargs(cls, kwargs: dict) -> dict:
        """Map deprecated field names to their new consolidated equivalents.

        This allows callers to pass old field names (message_text, sender_name,
        etc.) and have them automatically mapped into initial_telegram_message,
        extra_context, etc.
        """
        # Extract fields that map to initial_telegram_message
        itm_fields = {}
        for key in (
            "message_text",
            "sender_name",
            "sender_id",
            "telegram_message_id",
            "chat_title",
        ):
            if key in kwargs and "initial_telegram_message" not in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    itm_fields[key] = val
        if itm_fields and "initial_telegram_message" not in kwargs:
            kwargs["initial_telegram_message"] = itm_fields

        # Extract fields that map to extra_context
        ec_fields = {}
        for key in ("revival_context", "classification_type", "classification_confidence"):
            if key in kwargs and "extra_context" not in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    ec_fields[key] = val
        if ec_fields and "extra_context" not in kwargs:
            kwargs["extra_context"] = ec_fields

        # Map deprecated field names
        if "work_item_slug" in kwargs and "slug" not in kwargs:
            kwargs["slug"] = kwargs.pop("work_item_slug")
        elif "work_item_slug" in kwargs:
            kwargs.pop("work_item_slug")

        if "last_activity" in kwargs and "updated_at" not in kwargs:
            kwargs["updated_at"] = kwargs.pop("last_activity")
        elif "last_activity" in kwargs:
            kwargs.pop("last_activity")

        if "scheduled_after" in kwargs and "scheduled_at" not in kwargs:
            val = kwargs.pop("scheduled_after")
            if isinstance(val, (int, float)):
                kwargs["scheduled_at"] = datetime.fromtimestamp(val, tz=UTC)
            else:
                kwargs["scheduled_at"] = val
        elif "scheduled_after" in kwargs:
            kwargs.pop("scheduled_after")

        if "parent_agent_session_id" in kwargs and "parent_job_id" not in kwargs:
            kwargs["parent_job_id"] = kwargs.pop("parent_agent_session_id")
        elif "parent_agent_session_id" in kwargs:
            kwargs.pop("parent_agent_session_id")

        if "agent_session_id" in kwargs:
            kwargs.pop("agent_session_id")  # AutoKeyField, ignore

        # Map old history to session_events
        if "history" in kwargs and "session_events" not in kwargs:
            kwargs["session_events"] = kwargs.pop("history")
        elif "history" in kwargs:
            kwargs.pop("history")

        # Convert stage_states to a session event
        stage_states_val = kwargs.pop("stage_states", None)
        if stage_states_val is not None and "session_events" not in kwargs:
            if isinstance(stage_states_val, str):
                try:
                    stages_dict = _json.loads(stage_states_val)
                except (ValueError, TypeError):
                    stages_dict = None
            elif isinstance(stage_states_val, dict):
                stages_dict = stage_states_val
            else:
                stages_dict = None
            if stages_dict:
                event = SessionEvent.stage_change("bulk", "init", stages_dict)
                kwargs["session_events"] = [event.model_dump()]

        # Convert commit_sha to a session event
        commit_sha_val = kwargs.pop("commit_sha", None)
        if commit_sha_val is not None:
            events = kwargs.get("session_events", []) or []
            event = SessionEvent.checkpoint(commit_sha_val)
            events.append(event.model_dump())
            kwargs["session_events"] = events

        # Convert summary to a session event
        summary_val = kwargs.pop("summary", None)
        if summary_val is not None:
            events = kwargs.get("session_events", []) or []
            event = SessionEvent.summary(summary_val)
            events.append(event.model_dump())
            kwargs["session_events"] = events

        # Remove dead fields silently
        for dead in (
            "depends_on",
            "stable_agent_session_id",
            "scheduling_depth",
            "_qa_mode_legacy",
        ):
            kwargs.pop(dead, None)

        # Ensure created_at has a default (SortedField is not nullable)
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(tz=UTC)

        # Convert float timestamps to datetime
        if "created_at" in kwargs and isinstance(kwargs["created_at"], (int, float)):
            kwargs["created_at"] = datetime.fromtimestamp(kwargs["created_at"], tz=UTC)
        if "started_at" in kwargs and isinstance(kwargs["started_at"], (int, float)):
            kwargs["started_at"] = datetime.fromtimestamp(kwargs["started_at"], tz=UTC)
        if "completed_at" in kwargs and isinstance(kwargs["completed_at"], (int, float)):
            kwargs["completed_at"] = datetime.fromtimestamp(kwargs["completed_at"], tz=UTC)
        if "updated_at" in kwargs and isinstance(kwargs["updated_at"], (int, float)):
            kwargs["updated_at"] = datetime.fromtimestamp(kwargs["updated_at"], tz=UTC)

        return kwargs

    @classmethod
    def create(cls, **kwargs) -> "AgentSession":
        """Create an AgentSession with backward-compatible field name support."""
        kwargs = cls._normalize_kwargs(kwargs)
        return super().create(**kwargs)

    @classmethod
    async def async_create(cls, **kwargs) -> "AgentSession":
        """Create an AgentSession asynchronously with backward-compatible field name support."""
        kwargs = cls._normalize_kwargs(kwargs)
        return await super().async_create(**kwargs)

    # === Backward-compatible property: agent_session_id -> job_id ===

    @property
    def agent_session_id(self) -> str | None:
        """Backward-compatible alias for job_id."""
        return self.job_id

    @agent_session_id.setter
    def agent_session_id(self, value: str) -> None:
        """Backward-compatible setter for job_id."""
        self.job_id = value

    # === Compatibility property aliases ===

    @property
    def id(self) -> str | None:
        """Alias for job_id."""
        return self.job_id

    @property
    def sender_name(self) -> str | None:
        """Extract sender_name from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("sender_name")
        return None

    @sender_name.setter
    def sender_name(self, value: str | None) -> None:
        """Set sender_name in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        itm["sender_name"] = value
        self.initial_telegram_message = itm

    @property
    def sender_id(self) -> int | None:
        """Extract sender_id from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            val = itm.get("sender_id")
            return int(val) if val is not None else None
        return None

    @sender_id.setter
    def sender_id(self, value: int | None) -> None:
        """Set sender_id in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["sender_id"] = value
        elif "sender_id" in itm:
            del itm["sender_id"]
        self.initial_telegram_message = itm

    @property
    def message_text(self) -> str | None:
        """Extract message_text from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("message_text")
        return None

    @message_text.setter
    def message_text(self, value: str) -> None:
        """Set message_text in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        itm["message_text"] = value
        self.initial_telegram_message = itm

    @property
    def telegram_message_id(self) -> int | None:
        """Extract telegram_message_id from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            val = itm.get("telegram_message_id")
            return int(val) if val is not None else None
        return None

    @telegram_message_id.setter
    def telegram_message_id(self, value: int | None) -> None:
        """Set telegram_message_id in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["telegram_message_id"] = value
        elif "telegram_message_id" in itm:
            del itm["telegram_message_id"]
        self.initial_telegram_message = itm

    @property
    def chat_title(self) -> str | None:
        """Extract chat_title from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("chat_title")
        return None

    @chat_title.setter
    def chat_title(self, value: str | None) -> None:
        """Set chat_title in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["chat_title"] = value
        elif "chat_title" in itm:
            del itm["chat_title"]
        self.initial_telegram_message = itm

    @property
    def sender(self) -> str | None:
        """Alias for sender_name (SessionLog used 'sender')."""
        return self.sender_name

    @property
    def revival_context(self) -> str | None:
        """Extract revival_context from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            return ec.get("revival_context")
        return None

    @revival_context.setter
    def revival_context(self, value: str | None) -> None:
        """Set revival_context in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["revival_context"] = value
        elif "revival_context" in ec:
            del ec["revival_context"]
        self.extra_context = ec

    @property
    def classification_type(self) -> str | None:
        """Extract classification_type from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            return ec.get("classification_type")
        return None

    @classification_type.setter
    def classification_type(self, value: str | None) -> None:
        """Set classification_type in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["classification_type"] = value
        elif "classification_type" in ec:
            del ec["classification_type"]
        self.extra_context = ec

    @property
    def classification_confidence(self) -> float | None:
        """Extract classification_confidence from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            val = ec.get("classification_confidence")
            return float(val) if val is not None else None
        return None

    @classification_confidence.setter
    def classification_confidence(self, value: float | None) -> None:
        """Set classification_confidence in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["classification_confidence"] = value
        elif "classification_confidence" in ec:
            del ec["classification_confidence"]
        self.extra_context = ec

    @property
    def work_item_slug(self) -> str | None:
        """Backward-compatible alias for slug."""
        return self.slug

    @property
    def scheduling_depth(self) -> int:
        """Derive scheduling depth by walking parent_job_id chain.

        Returns the depth of the parent chain, capped at 5 for safety.
        """
        depth = 0
        current = self
        max_depth = 5
        while depth < max_depth:
            pid = current.parent_job_id
            if not pid:
                break
            try:
                parent = AgentSession.query.get(pid)
                if parent is None:
                    break
                depth += 1
                current = parent
            except Exception:
                break
        return depth

    # === Derived properties from session_events ===

    @property
    def summary(self) -> str | None:
        """Get the most recent summary from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "summary":
                return event.get("text")
        return None

    @summary.setter
    def summary(self, value: str) -> None:
        """Set summary by appending a summary event."""
        if value:
            self.append_event("summary", value)

    @property
    def result_text(self) -> str | None:
        """Get the most recent delivery text from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "delivery":
                return event.get("text")
        return None

    @result_text.setter
    def result_text(self, value: str) -> None:
        """Set result_text by appending a delivery event."""
        if value:
            self.append_event("delivery", value)

    @property
    def stage_states(self) -> str | None:
        """Get the most recent stage_states from session_events as JSON string."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "stage":
                data = event.get("data")
                if isinstance(data, dict) and "stages" in data:
                    stages = data["stages"]
                    if isinstance(stages, str):
                        return stages
                    return _json.dumps(stages)
        return None

    @stage_states.setter
    def stage_states(self, value) -> None:
        """Set stage_states by appending a stage event."""
        if value is not None:
            if isinstance(value, str):
                try:
                    stages_dict = _json.loads(value)
                except (ValueError, TypeError):
                    stages_dict = None
            elif isinstance(value, dict):
                stages_dict = value
            else:
                stages_dict = None
            if stages_dict:
                event = SessionEvent.stage_change("bulk", "update", stages_dict)
                self._append_event_dict(event.model_dump())

    @property
    def last_commit_sha(self) -> str | None:
        """Get the most recent commit SHA from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "checkpoint":
                return event.get("text")
        return None

    @property
    def commit_sha(self) -> str | None:
        """Backward-compatible alias for last_commit_sha."""
        return self.last_commit_sha

    @commit_sha.setter
    def commit_sha(self, value: str) -> None:
        """Set commit_sha by appending a checkpoint event."""
        if value:
            event = SessionEvent.checkpoint(value)
            self._append_event_dict(event.model_dump())

    # === Session type helpers ===

    @property
    def is_chat(self) -> bool:
        """Whether this is a ChatSession (PM persona, read-only)."""
        return self.session_type == SESSION_TYPE_CHAT

    @property
    def is_dev(self) -> bool:
        """Whether this is a DevSession (Dev persona, full permissions)."""
        return self.session_type == SESSION_TYPE_DEV

    @property
    def current_stage(self) -> str | None:
        """Return the first SDLC stage with status 'in_progress', or None."""
        stages = self._get_stage_states_dict()
        if not stages:
            return None
        for stage in SDLC_STAGES:
            if stages.get(stage) == "in_progress":
                return stage
        return None

    @property
    def derived_branch_name(self) -> str | None:
        """Derive branch name from slug if available."""
        s = self.slug
        return f"session/{s}" if s else self.branch_name

    @property
    def plan_path(self) -> str | None:
        """Derive plan path from slug if available."""
        s = self.slug
        return f"docs/plans/{s}.md" if s else None

    def _get_stage_states_dict(self) -> dict | None:
        """Parse stage_states into a dict, or None."""
        raw = self.stage_states
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return None

    # === Factory methods ===

    @classmethod
    def create_chat(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        chat_id: str,
        telegram_message_id: int,
        message_text: str,
        sender_name: str | None = None,
        sender_id: int | None = None,
        chat_title: str | None = None,
        telegram_message_key: str | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Create a ChatSession (PM persona, read-only orchestrator)."""
        itm = {
            "message_text": message_text,
            "sender_name": sender_name,
            "telegram_message_id": telegram_message_id,
        }
        if sender_id is not None:
            itm["sender_id"] = sender_id
        if chat_title is not None:
            itm["chat_title"] = chat_title

        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_CHAT,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            initial_telegram_message=itm,
            telegram_message_key=telegram_message_key,
            created_at=datetime.now(tz=UTC),
            **kwargs,
        )
        session.save()
        return session

    @classmethod
    def create_local(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        **kwargs,
    ) -> "AgentSession":
        """Create an AgentSession for a local Claude Code CLI session."""
        now = datetime.now(tz=UTC)
        chat_id = kwargs.pop("chat_id", None) or f"local{int(now.timestamp()) % 10000}"
        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_DEV,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            created_at=now,
            started_at=now,
            updated_at=now,
            **kwargs,
        )
        session.save()
        return session

    @classmethod
    def create_dev(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        parent_chat_session_id: str,
        message_text: str,
        slug: str | None = None,
        stage_states: dict | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Create a DevSession (Dev persona, full permissions)."""
        itm = {"message_text": message_text}

        # If stage_states provided, store as an initial event
        initial_events = None
        if isinstance(stage_states, dict):
            event = SessionEvent.stage_change("bulk", "init", stage_states)
            initial_events = [event.model_dump()]

        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_DEV,
            project_key=project_key,
            working_dir=working_dir,
            parent_chat_session_id=parent_chat_session_id,
            initial_telegram_message=itm,
            slug=slug,
            session_events=initial_events,
            created_at=datetime.now(tz=UTC),
            **kwargs,
        )
        session.save()
        return session

    def get_parent_chat_session(self) -> "AgentSession | None":
        """Return the parent ChatSession if this is a DevSession."""
        if not self.parent_chat_session_id:
            return None
        try:
            return AgentSession.query.get(self.parent_chat_session_id)
        except Exception:
            logger.warning(
                f"Parent chat session {self.parent_chat_session_id} not found "
                f"for dev session {self.job_id}"
            )
            return None

    def get_dev_sessions(self) -> list["AgentSession"]:
        """Return all DevSessions spawned by this ChatSession."""
        if not self.is_chat:
            return []
        try:
            return list(AgentSession.query.filter(parent_chat_session_id=self.job_id))
        except Exception as e:
            logger.warning(f"Failed to query dev sessions for chat {self.job_id}: {e}")
            return []

    # === PM self-messaging helpers ===

    def record_pm_message(self, msg_id: int) -> None:
        """Record a Telegram message ID sent by the PM."""
        current = self.pm_sent_message_ids
        if not isinstance(current, list):
            current = []
        current.append(msg_id)
        self.pm_sent_message_ids = current
        try:
            self.save()
        except Exception as e:
            logger.warning(
                f"record_pm_message save failed for session {self.session_id} "
                f"(msg_id={msg_id}): {e}"
            )

    def has_pm_messages(self) -> bool:
        """Check whether the PM sent any self-authored messages during this session."""
        ids = self.pm_sent_message_ids
        return isinstance(ids, list) and len(ids) > 0

    # === Event log helpers ===

    def get_history_list(self) -> list:
        """Get session_events as a list of formatted strings (backward compat)."""
        events = self.session_events
        if not isinstance(events, list):
            return []
        result = []
        for event in events:
            if isinstance(event, dict):
                etype = event.get("event_type", "system")
                text = event.get("text", "")
                result.append(f"[{etype}] {text}")
            elif isinstance(event, str):
                result.append(event)
        return result

    # Keep private alias for internal callers
    _get_history_list = get_history_list

    @property
    def history(self) -> list | None:
        """Backward-compatible alias for session_events."""
        return self.session_events

    @history.setter
    def history(self, value) -> None:
        """Backward-compatible setter for session_events."""
        self.session_events = value

    def append_event(self, event_type: str, text: str, data: dict | None = None) -> None:
        """Append a structured event to session_events, capped at HISTORY_MAX_ENTRIES.

        Args:
            event_type: Event type (lifecycle, summary, delivery, stage, checkpoint, etc.)
            text: Event description
            data: Optional structured payload
        """
        event = SessionEvent(event_type=event_type, text=text, data=data)
        self._append_event_dict(event.model_dump())

    def _append_event_dict(self, event_dict: dict) -> None:
        """Append a raw event dict to session_events, capped at HISTORY_MAX_ENTRIES."""
        current = self.session_events
        if not isinstance(current, list):
            current = []
        current.append(event_dict)
        if len(current) > HISTORY_MAX_ENTRIES:
            dropped = len(current) - HISTORY_MAX_ENTRIES
            logger.warning(
                f"Session {self.session_id} session_events truncated from "
                f"{len(current)} to {HISTORY_MAX_ENTRIES}, "
                f"{dropped} oldest entries lost"
            )
            current = current[-HISTORY_MAX_ENTRIES:]
        self.session_events = current
        try:
            self.save()
        except Exception as e:
            logger.warning(
                f"append_event save failed for session {self.session_id} "
                f"(event_type={event_dict.get('event_type')!r}): {e}"
            )

    def append_history(self, role: str, text: str) -> None:
        """Backward-compatible: append a lifecycle event using append_event."""
        self.append_event(role, text)

    def set_link(self, kind: str, url: str) -> None:
        """Set a tracked link on this session."""
        field_map = {
            "issue": "issue_url",
            "plan": "plan_url",
            "pr": "pr_url",
        }
        field_name = field_map.get(kind)
        if field_name:
            existing = getattr(self, field_name, None)
            action = "update" if existing else "set"
            cid = getattr(self, "correlation_id", None) or "unknown"
            logger.info(
                f"LINK session={self.session_id} correlation={cid} "
                f"type={kind} action={action} url={url}"
            )
            setattr(self, field_name, url)
            try:
                self.save()
            except Exception as e:
                logger.warning(
                    f"set_link save failed for session {self.session_id} "
                    f"(kind={kind!r}, field={field_name}): {e}"
                )

    def log_lifecycle_transition(self, new_status: str, context: str = "") -> None:
        """Log a structured lifecycle transition and append event."""
        old_status = self.status or "none"
        now = datetime.now(tz=UTC)

        # Calculate duration from session start
        prev_time = self.started_at or self.created_at
        if prev_time is not None:
            if isinstance(prev_time, datetime):
                pt = prev_time if prev_time.tzinfo else prev_time.replace(tzinfo=UTC)
                duration = (now - pt).total_seconds()
            elif isinstance(prev_time, int | float):
                duration = now.timestamp() - prev_time
            else:
                duration = 0
        else:
            duration = 0

        logger.info(
            f"LIFECYCLE session={self.session_id} transition={old_status}\u2192{new_status} "
            f"job_id={self.job_id} project={self.project_key} "
            f"duration_in_prev_state={duration:.1f}s" + (f' context="{context}"' if context else "")
        )

        self.append_event(
            "lifecycle",
            f"{old_status}\u2192{new_status}" + (f": {context}" if context else ""),
        )

    def get_links(self) -> dict[str, str]:
        """Return all non-None tracked links."""
        links = {}
        if self.issue_url:
            links["issue"] = self.issue_url
        if self.plan_url:
            links["plan"] = self.plan_url
        if self.pr_url:
            links["pr"] = self.pr_url
        return links

    def get_stage_progress(self) -> dict[str, str]:
        """Return SDLC stage completion status via PipelineStateMachine."""
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.get_display_progress()

    # === Stage-aware auto-continue helpers ===

    @property
    def is_sdlc(self) -> bool:
        """Whether this session is an SDLC pipeline session."""
        stages = self._get_stage_states_dict()
        if stages and any(v not in ("pending", "ready") for v in stages.values()):
            return True
        if self.classification_type == ClassificationType.SDLC:
            return True
        return False

    def has_remaining_stages(self) -> bool:
        """Check if any SDLC stages are not yet completed."""
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.has_remaining_stages()

    def has_failed_stage(self) -> bool:
        """Check if any SDLC stage has failed."""
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.has_failed_stage()

    # === Queued steering message helpers ===

    def push_steering_message(self, text: str) -> None:
        """Buffer a human reply for the ChatSession."""
        current = self.queued_steering_messages
        if not isinstance(current, list):
            current = []
        current.append(text)
        if len(current) > STEERING_QUEUE_MAX:
            dropped = len(current) - STEERING_QUEUE_MAX
            logger.warning(
                f"Steering queue overflow for session {self.session_id}: "
                f"dropping {dropped} oldest message(s)"
            )
            current = current[-STEERING_QUEUE_MAX:]
        self.queued_steering_messages = current
        try:
            self.save()
        except Exception as e:
            logger.warning(f"Failed to save steering message for session {self.session_id}: {e}")

    def pop_steering_messages(self) -> list[str]:
        """Pop all buffered steering messages, clearing the queue."""
        current = self.queued_steering_messages
        if not isinstance(current, list) or not current:
            return []
        messages = list(current)
        self.queued_steering_messages = []
        try:
            self.save()
        except Exception as e:
            logger.warning(f"Failed to clear steering messages for session {self.session_id}: {e}")
        return messages

    # === Session hierarchy helpers ===

    def get_parent(self) -> "AgentSession | None":
        """Return the parent AgentSession if this is a child session."""
        if not self.parent_job_id:
            return None
        try:
            parent = AgentSession.query.get(self.parent_job_id)
            return parent
        except Exception:
            logger.warning(
                f"Parent agent session {self.parent_job_id} not found for child {self.job_id}"
            )
            return None

    def get_children(self) -> list["AgentSession"]:
        """Return all child AgentSessions linked via parent_job_id."""
        try:
            return list(AgentSession.query.filter(parent_job_id=self.job_id))
        except Exception as e:
            logger.warning(f"Failed to query children for agent session {self.job_id}: {e}")
            return []

    def get_completion_progress(self) -> tuple[int, int, int]:
        """Compute aggregate completion status of child sessions."""
        children = self.get_children()
        total = len(children)
        completed = sum(1 for c in children if c.status == "completed")
        failed = sum(1 for c in children if c.status == "failed")
        return completed, total, failed

    # === Cleanup ===

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete AgentSession Redis metadata older than max_age_days."""
        cutoff = datetime.now(tz=UTC).timestamp() - (max_age_days * 86400)
        all_sessions = cls.query.all()
        deleted = 0
        for session in all_sessions:
            started = session.started_at or session.created_at
            if started is None:
                continue
            # Handle both datetime and float timestamps (migration period)
            if isinstance(started, datetime):
                ts = started.timestamp()
            elif isinstance(started, int | float):
                ts = float(started)
            else:
                continue
            if ts < cutoff:
                session.delete()
                deleted += 1
        return deleted
