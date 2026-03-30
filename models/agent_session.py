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
import time

from config.enums import ChatMode, ClassificationType, SessionType
from popoto import (
    AutoKeyField,
    Field,
    IntField,
    KeyField,
    ListField,
    Model,
    SortedField,
)

logger = logging.getLogger(__name__)

MSG_MAX_CHARS = 20_000
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
    Factory methods create_chat() and create_dev() enforce field contracts.

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
    status = KeyField(default="pending")

    # === Queue fields (from RedisJob) ===
    priority = Field(default="normal")  # urgent | high | normal | low
    scheduled_after = Field(type=float, null=True)  # UTC timestamp; _pop_job() skips if > now()
    scheduling_depth = Field(type=int, default=0)  # Self-scheduling chain depth (cap at 3)
    created_at = SortedField(type=float, partition_by="project_key")
    working_dir = Field()
    message_text = Field(max_length=MSG_MAX_CHARS)
    sender_name = Field(null=True)
    sender_id = Field(type=int, null=True)
    chat_id = KeyField(null=True)
    telegram_message_id = Field(type=int, null=True)
    chat_title = Field(null=True)
    revival_context = Field(null=True, max_length=MSG_MAX_CHARS)
    work_item_slug = Field(null=True)
    task_list_id = Field(null=True)
    classification_type = Field(null=True)  # Actively used by is_sdlc, session_tags, job_scheduler
    auto_continue_count = Field(type=int, default=0)
    started_at = Field(type=float, null=True)  # Cannot be SortedField because it starts as None

    # === Cross-reference to TelegramMessage ===
    telegram_message_key = Field(
        null=True
    )  # msg_id of the TelegramMessage that triggered this session (Popoto key)

    # === Session fields (from SessionLog) ===
    last_activity = Field(type=float, null=True)
    completed_at = Field(type=float, null=True)

    turn_count = IntField(default=0)
    tool_call_count = IntField(default=0)
    log_path = Field(null=True, max_length=1000)
    summary = Field(null=True, max_length=50_000)
    branch_name = Field(null=True)
    tags = ListField(null=True)
    classification_confidence = Field(type=float, null=True)  # Paired with classification_type

    # === New fields ===
    history = ListField(null=True)  # Append-only lifecycle events
    issue_url = Field(null=True)
    plan_url = Field(null=True)
    pr_url = Field(null=True)

    # === Pipeline state machine ===
    # JSON-serialized dict of stage -> status managed by PipelineStateMachine.
    # Replaces the [stage] history entry parsing approach.
    stage_states = Field(null=True)

    # === Claude Code identity mapping ===
    # Stores the Claude Code session UUID (from transcript filename) so that
    # continuation sessions can resume the correct transcript instead of falling
    # back to the most recent session file on disk. See issue #374 Bug 1.
    claude_session_uuid = Field(null=True)

    # === Tracing ===
    correlation_id = Field(null=True)  # End-to-end request tracing ID

    # === Stall retry fields ===
    retry_count = Field(type=int, default=0)  # Stall retry attempt count
    last_stall_reason = Field(null=True)  # Diagnostic context from last stall

    # === Watchdog fields ===
    watchdog_unhealthy = Field(null=True)  # Reason string when flagged unhealthy, None when healthy

    # === Session mode (replaces qa_mode) ===
    # Stores ChatMode enum value ("qa" or None) to indicate Q&A routing.
    # Replaces the boolean qa_mode field with a string-typed enum field.
    session_mode = Field(null=True)

    # Legacy field kept for backward compatibility with in-flight Redis sessions.
    # New code should use session_mode instead. Will be removed in a future cleanup.
    _qa_mode_legacy = Field(type=bool, null=True)

    # === Semantic routing fields ===
    context_summary = Field(null=True, max_length=200)  # What this session is about
    expectations = Field(null=True, max_length=500)  # What the agent needs from the human

    # === Steering fields ===
    # Buffered human replies during active pipelines
    queued_steering_messages = ListField(null=True)

    # === ChatSession delivery field ===
    result_text = Field(null=True, max_length=MSG_MAX_CHARS)  # What was delivered to Telegram

    # === PM self-messaging ===
    # Telegram message IDs sent by the PM via send_telegram tool during this session.
    # Populated by the bridge relay after each successful Telethon send.
    # When non-empty, the summarizer is bypassed (PM authored its own messages).
    pm_sent_message_ids = ListField(null=True)

    # === DevSession fields (null when session_type="chat") ===
    parent_chat_session_id = KeyField(null=True)  # Logical FK -> ChatSession
    slug = Field(null=True)  # Derives branch, plan path, worktree
    artifacts = Field(null=True)  # JSON: {issue_url, plan_url, pr_url, ...}

    # === Job hierarchy fields ===
    # Links child jobs to their parent for job decomposition (issue #359).
    # When set, this job is a child of the referenced parent job.
    # Parent tracks aggregate progress via get_children() / get_completion_progress().
    parent_job_id = KeyField(null=True)

    # === Job dependency fields ===
    # stable_job_id: UUID set once at creation, never changes on delete-and-recreate.
    # job_id (AutoKeyField) changes on status transitions; stable_job_id does not.
    # This is the dependency reference key. Nullable for pre-existing jobs.
    stable_job_id = KeyField(null=True)
    # depends_on: list of stable_job_id values this job must wait for.
    # Only jobs whose dependencies are all in terminal state become eligible.
    depends_on = ListField(null=True)
    # commit_sha: HEAD commit SHA recorded at pause for checkpoint/restore.
    commit_sha = Field(null=True)

    # === Compatibility ===

    @property
    def id(self) -> str | None:
        """Alias for job_id. Provides a cleaner name for the primary key.

        Cannot rename job_id directly because AutoKeyField generates Redis
        keys from the field name -- renaming would make existing records
        inaccessible.
        """
        return self.job_id

    @property
    def sender(self) -> str | None:
        """Alias for sender_name (SessionLog used 'sender')."""
        return self.sender_name

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
    def qa_mode(self) -> bool:
        """Backward-compatible property: True when session is in Q&A mode.

        Reads session_mode first, falls back to the legacy ``qa_mode`` Redis
        hash field for pre-migration sessions.  The legacy Popoto field was
        renamed to ``_qa_mode_legacy`` to avoid colliding with this property,
        but old sessions still store the value under the original ``qa_mode``
        key.  We read both to cover all cases.
        """
        if self.session_mode == ChatMode.QA:
            return True
        # Check the renamed Popoto field first (new sessions written post-migration)
        if self._qa_mode_legacy:
            return True
        # Fallback: read the original "qa_mode" hash field from Redis directly,
        # since Popoto only knows about "_qa_mode_legacy" and cannot see the old key.
        try:
            from popoto.redis_db import POPOTO_REDIS_DB

            raw = POPOTO_REDIS_DB.hget(str(self.db_key), "qa_mode")
            if raw is not None:
                # Popoto encodes booleans; True is b'\xc1', False is b'\xc0'
                return raw == b"\xc1"
        except Exception:
            pass
        return False

    @qa_mode.setter
    def qa_mode(self, value: bool) -> None:
        """Backward-compatible setter: writes to session_mode."""
        self.session_mode = ChatMode.QA if value else None

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
        s = self.slug or self.work_item_slug
        return f"session/{s}" if s else self.branch_name

    @property
    def plan_path(self) -> str | None:
        """Derive plan path from slug if available."""
        s = self.slug or self.work_item_slug
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
        """Create a ChatSession (PM persona, read-only orchestrator).

        ChatSessions are created by the bridge handler when a message arrives.
        They own the Telegram conversation and orchestrate DevSessions.

        Wired into bridge handler via enqueue_job(session_type=...).
        """
        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_CHAT,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            telegram_message_id=telegram_message_id,
            message_text=message_text,
            sender_name=sender_name,
            sender_id=sender_id,
            chat_title=chat_title,
            telegram_message_key=telegram_message_key,
            created_at=time.time(),
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
        """Create an AgentSession for a local Claude Code CLI session.

        Local sessions have no parent ChatSession, no Telegram context,
        and no triggering message. They are created by Claude Code hooks
        (UserPromptSubmit) to provide dashboard observability for CLI work.

        The session_id should use the format ``local-{claude_session_id}``
        to avoid collisions with Telegram-originated session IDs.

        Args:
            session_id: Unique session identifier (format: local-{uuid}).
            project_key: Project partition key for Redis queries.
            working_dir: Absolute path to the working directory.
            **kwargs: Additional AgentSession fields to set.
        """
        now = time.time()
        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_DEV,
            project_key=project_key,
            working_dir=working_dir,
            created_at=now,
            started_at=now,
            last_activity=now,
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
        """Create a DevSession (Dev persona, full permissions).

        DevSessions are created exclusively by ChatSessions during orchestration.
        They do the actual coding work and run SDLC pipeline stages.

        Wired into bridge handler via enqueue_job(session_type=...).
        """
        stages_json = _json.dumps(stage_states) if isinstance(stage_states, dict) else stage_states
        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_DEV,
            project_key=project_key,
            working_dir=working_dir,
            parent_chat_session_id=parent_chat_session_id,
            message_text=message_text,
            slug=slug,
            stage_states=stages_json,
            created_at=time.time(),
            **kwargs,
        )
        session.save()
        return session

    def get_parent_chat_session(self) -> "AgentSession | None":
        """Return the parent ChatSession if this is a DevSession.

        Returns None if parent_chat_session_id is not set or parent not found.
        """
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
        """Return all DevSessions spawned by this ChatSession.

        Returns an empty list if no DevSessions exist or this is not a ChatSession.
        """
        if not self.is_chat:
            return []
        try:
            return list(AgentSession.query.filter(parent_chat_session_id=self.job_id))
        except Exception as e:
            logger.warning(f"Failed to query dev sessions for chat {self.job_id}: {e}")
            return []

    # === PM self-messaging helpers ===

    def record_pm_message(self, msg_id: int) -> None:
        """Record a Telegram message ID sent by the PM via the send_telegram tool.

        Called by the bridge relay after successfully sending a PM-authored message.
        The list is checked by the summarizer bypass to skip rewriting PM output.

        Args:
            msg_id: The Telegram message ID returned by Telethon after send.
        """
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
        """Check whether the PM sent any self-authored messages during this session.

        Returns True if pm_sent_message_ids is a non-empty list.
        Used by the summarizer bypass in bridge/response.py.
        """
        ids = self.pm_sent_message_ids
        return isinstance(ids, list) and len(ids) > 0

    # === History helpers ===

    def get_history_list(self) -> list:
        """Safely get history as a Python list."""
        h = self.history
        if isinstance(h, list):
            return h
        return []

    # Keep private alias for internal callers
    _get_history_list = get_history_list

    def append_history(self, role: str, text: str) -> None:
        """Append a lifecycle event to history, capped at HISTORY_MAX_ENTRIES.

        Args:
            role: Event type (user, classify, stage, summary, system)
            text: Event description
        """
        logger.debug(f"append_history({role!r}, {text!r}) on session {self.session_id}")
        entry = f"[{role}] {text}"
        current = self._get_history_list()
        current.append(entry)
        if len(current) > HISTORY_MAX_ENTRIES:
            dropped = len(current) - HISTORY_MAX_ENTRIES
            # Warn when history entries are silently lost so operators can
            # diagnose long-running sessions without reproducing the issue.
            logger.warning(
                f"Session {self.session_id} history truncated from "
                f"{len(current)} to {HISTORY_MAX_ENTRIES}, "
                f"{dropped} oldest entries lost"
            )
            current = current[-HISTORY_MAX_ENTRIES:]
        self.history = current
        try:
            self.save()
        except Exception as e:
            logger.warning(
                f"append_history save failed for session {self.session_id} "
                f"(role={role!r}, history_len={len(current)}): {e}"
            )

    def set_link(self, kind: str, url: str) -> None:
        """Set a tracked link on this session.

        Args:
            kind: Link type - 'issue', 'plan', or 'pr'
            url: The URL to store
        """
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
        """Log a structured lifecycle transition and update session state.

        Emits a structured log line and appends to history.

        Args:
            new_status: The status being transitioned to
            context: Brief description of why the transition happened
        """
        old_status = self.status or "none"
        now = time.time()

        # Calculate duration from session start
        prev_time = self.started_at or self.created_at
        duration = now - prev_time if prev_time else 0

        # Structured log entry
        logger.info(
            f"LIFECYCLE session={self.session_id} transition={old_status}\u2192{new_status} "
            f"job_id={self.job_id} project={self.project_key} "
            f"duration_in_prev_state={duration:.1f}s" + (f' context="{context}"' if context else "")
        )

        # Append to history
        self.append_history(
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
        """Return SDLC stage completion status via PipelineStateMachine.

        Returns:
            Dict mapping stage name to status string.
        """
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.get_display_progress()

    # === Stage-aware auto-continue helpers ===

    @property
    def is_sdlc(self) -> bool:
        """Whether this session is an SDLC pipeline job.

        Two checks:
        1. stage_states has any non-pending/non-ready stage
        2. classification_type == "sdlc" for freshly-classified sessions
        """
        # Primary: check stage_states for any active/completed/failed stage
        stages = self._get_stage_states_dict()
        if stages and any(v not in ("pending", "ready") for v in stages.values()):
            return True

        # Secondary: classification_type for freshly-classified sessions
        if self.classification_type == ClassificationType.SDLC:
            return True

        return False

    def has_remaining_stages(self) -> bool:
        """Check if any SDLC stages are not yet completed.

        Uses PipelineStateMachine to determine remaining stages.

        Returns True if pipeline progression should continue.
        Returns False when the pipeline is complete (MERGE reached or
        no graph transitions remain).

        Used by stage-aware auto-continue to decide whether to keep
        going (stages remain) or consult the classifier (all done).
        """
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.has_remaining_stages()

    def has_failed_stage(self) -> bool:
        """Check if any SDLC stage has failed.

        Uses PipelineStateMachine to check stage_states. Failed stages
        are a hard stop signal -- the output should be delivered to the
        user immediately rather than auto-continued.
        """
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(self)
        return sm.has_failed_stage()

    # === Queued steering message helpers ===

    def push_steering_message(self, text: str) -> None:
        """Buffer a human reply for the ChatSession to read during active pipelines.

        The bridge intake classifier populates this when a human replies
        while the pipeline is running. The ChatSession reads and clears it.
        Bounded at STEERING_QUEUE_MAX entries; oldest dropped on overflow.

        Args:
            text: The human's message text to buffer.
        """
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
        """Pop all buffered steering messages, clearing the queue.

        Returns the list of buffered message texts and resets the field to empty.
        The ChatSession calls this to incorporate human replies into its orchestration.

        Returns:
            List of message text strings, or empty list if none buffered.
        """
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

    # === Job hierarchy helpers ===

    def get_parent(self) -> "AgentSession | None":
        """Return the parent AgentSession if this is a child job.

        Returns None if parent_job_id is not set or parent not found.
        """
        if not self.parent_job_id:
            return None
        try:
            parent = AgentSession.query.get(self.parent_job_id)
            return parent
        except Exception:
            logger.warning(f"Parent job {self.parent_job_id} not found for child {self.job_id}")
            return None

    def get_children(self) -> list["AgentSession"]:
        """Return all child AgentSessions linked to this job via parent_job_id.

        Returns an empty list if no children exist.
        """
        try:
            return list(AgentSession.query.filter(parent_job_id=self.job_id))
        except Exception as e:
            logger.warning(f"Failed to query children for job {self.job_id}: {e}")
            return []

    def get_completion_progress(self) -> tuple[int, int, int]:
        """Compute aggregate completion status of child jobs.

        Returns:
            (completed_count, total_count, failed_count) tuple.
            All zeros if no children exist.
        """
        children = self.get_children()
        total = len(children)
        completed = sum(1 for c in children if c.status == "completed")
        failed = sum(1 for c in children if c.status == "failed")
        return completed, total, failed

    # === Cleanup ===

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete AgentSession Redis metadata older than max_age_days.

        Transcript .txt files are NOT deleted.
        Returns count deleted.
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_sessions = cls.query.all()
        deleted = 0
        for session in all_sessions:
            started = session.started_at or session.created_at
            if started and started < cutoff:
                session.delete()
                deleted += 1
        return deleted
