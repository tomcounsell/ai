"""AgentSession model - unified lifecycle tracking for agent work.

Merges RedisJob (queue) and SessionLog (transcript) into a single model
that tracks a unit of work from enqueue through completion.

Queue-phase fields: priority, message_text, auto_continue_count, etc.
Session-phase fields: turn_count, tool_call_count, log_path, summary, tags
New fields: history (lifecycle events), issue_url, plan_url, pr_url

Cross-references TelegramMessage via trigger_message_id. Message metadata
(media, URLs, classification) lives on TelegramMessage; this model references
it for enrichment during job execution.

Status lifecycle: pending -> running -> active -> dormant -> completed | failed
"""

import logging
import time

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

# SDLC stages in pipeline order
SDLC_STAGES = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]


class AgentSession(Model):
    """Unified model tracking agent work from enqueue through completion.

    Replaces both RedisJob (agent/job_queue.py) and SessionLog
    (models/session_log.py). All fields from both models are carried
    forward, plus new history and link tracking fields.

    References TelegramMessage via trigger_message_id for message
    metadata (media, URLs, classification). The job worker resolves
    this reference to get enrichment parameters.

    Status values:
        pending  - Queued, waiting for worker
        running  - Worker picked up, agent executing
        active   - Session in progress (transcript tracking)
        dormant  - Paused on open question
        completed - Work finished successfully
        failed   - Work failed
    """

    # === Identity ===
    job_id = AutoKeyField()
    session_id = Field()  # Telegram-derived session identifier (e.g., tg_project_chatid_msgid)
    claude_code_session_id = Field(null=True)  # Claude Code's session identifier (renamed from session_id overlap)
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
    message_id = Field(type=int, null=True)
    chat_title = Field(null=True)
    revival_context = Field(null=True, max_length=MSG_MAX_CHARS)
    workflow_id = Field(null=True)
    work_item_slug = Field(null=True)
    task_list_id = Field(null=True)
    # === Message metadata (deprecated - now lives on TelegramMessage) ===
    # These fields are retained for backward compatibility during migration.
    # New code should read from TelegramMessage via trigger_message_id.
    has_media = Field(type=bool, default=False)
    media_type = Field(null=True)
    youtube_urls = Field(null=True)
    non_youtube_urls = Field(null=True)
    reply_to_msg_id = Field(type=int, null=True)
    chat_id_for_enrichment = Field(null=True)
    classification_type = Field(null=True)
    auto_continue_count = Field(type=int, default=0)
    started_at = SortedField(type=float, partition_by="project_key", null=True)

    # === Cross-reference to TelegramMessage ===
    trigger_message_id = Field(null=True)  # msg_id of the TelegramMessage that triggered this session

    # === Session fields (from SessionLog) ===
    last_activity = Field(type=float, null=True)
    completed_at = Field(type=float, null=True)
    last_transition_at = Field(type=float, null=True)  # Deprecated: derive from history instead
    turn_count = IntField(default=0)
    tool_call_count = IntField(default=0)
    log_path = Field(null=True, max_length=1000)
    summary = Field(null=True, max_length=50_000)
    branch_name = Field(null=True)
    tags = ListField(null=True)
    classification_confidence = Field(type=float, null=True)

    # === New fields ===
    history = ListField(null=True)  # Append-only lifecycle events
    issue_url = Field(null=True)
    plan_url = Field(null=True)
    pr_url = Field(null=True)

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

    # === Semantic routing fields ===
    context_summary = Field(null=True, max_length=200)  # What this session is about
    expectations = Field(null=True, max_length=500)  # What the agent needs from the human

    # === Observer fields ===
    # Buffered human replies during active pipelines
    queued_steering_messages = ListField(null=True)

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

        # Calculate duration in previous state
        prev_time = self.last_transition_at or self.started_at or self.created_at
        duration = now - prev_time if prev_time else 0

        # Structured log entry
        logger.info(
            f"LIFECYCLE session={self.session_id} transition={old_status}\u2192{new_status} "
            f"job_id={self.job_id} project={self.project_key} "
            f"duration_in_prev_state={duration:.1f}s" + (f' context="{context}"' if context else "")
        )

        # Update fields
        self.last_transition_at = now

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
        """Parse history entries to determine SDLC stage completion status.

        Returns:
            Dict mapping stage name to status: 'completed', 'in_progress', 'failed', or 'pending'
        """
        progress = {stage: "pending" for stage in SDLC_STAGES}
        for entry in self._get_history_list():
            if not isinstance(entry, str) or "[stage]" not in entry.lower():
                continue
            entry_upper = entry.upper()
            for stage in SDLC_STAGES:
                if stage in entry_upper:
                    if "FAILED" in entry_upper or "ERROR" in entry_upper:
                        progress[stage] = "failed"
                    elif "COMPLETED" in entry_upper or "☑" in entry:
                        progress[stage] = "completed"
                    elif "IN_PROGRESS" in entry_upper or "▶" in entry:
                        progress[stage] = "in_progress"
        logger.debug(f"get_stage_progress() on session {self.session_id}: {progress}")
        return progress

    # === Stage-aware auto-continue helpers ===

    def is_sdlc_job(self) -> bool:
        """Check if this session is an SDLC pipeline job.

        Returns True if:
        1. The session was classified as "sdlc" at input routing time, OR
        2. The session's history contains at least one [stage] entry

        The classification_type check (added for issue #246) is the primary
        signal — it's set at classification time and cannot be lost. The
        history check is the legacy fallback for sessions that have stage
        entries from session_progress calls.

        Used by the auto-continue logic to choose between stage-aware
        routing (for SDLC jobs) and classifier-based routing (for
        casual/ad-hoc jobs).
        """
        # Primary: classification_type set at input routing time
        if self.classification_type == "sdlc":
            return True
        # Fallback: check for [stage] entries in history
        for entry in self._get_history_list():
            if isinstance(entry, str) and "[stage]" in entry.lower():
                return True
        return False

    def has_remaining_stages(self) -> bool:
        """Check if any SDLC stages are not yet completed.

        Returns True if at least one stage in the pipeline is still
        'pending' or 'in_progress'. Returns False when all stages
        are 'completed' (or 'failed').

        Used by stage-aware auto-continue to decide whether to keep
        going (stages remain) or consult the classifier (all done).
        """
        progress = self.get_stage_progress()
        return any(status in ("pending", "in_progress") for status in progress.values())

    def has_failed_stage(self) -> bool:
        """Check if any SDLC stage has failed.

        Returns True if a [stage] history entry contains FAILED or
        ERROR for any stage. Failed stages are a hard stop signal --
        the output should be delivered to the user immediately rather
        than auto-continued.
        """
        progress = self.get_stage_progress()
        return any(status == "failed" for status in progress.values())

    # === Queued steering message helpers ===

    def push_steering_message(self, text: str) -> None:
        """Buffer a human reply for the Observer to read during active pipelines.

        The bridge intake classifier (#320) populates this when a human replies
        while the pipeline is running. The Observer reads and clears it.

        Args:
            text: The human's message text to buffer.
        """
        current = self.queued_steering_messages
        if not isinstance(current, list):
            current = []
        current.append(text)
        self.queued_steering_messages = current
        try:
            self.save()
        except Exception as e:
            logger.warning(f"Failed to save steering message for session {self.session_id}: {e}")

    def pop_steering_messages(self) -> list[str]:
        """Pop all buffered steering messages, clearing the queue.

        Returns the list of buffered message texts and resets the field to empty.
        The Observer calls this to incorporate human replies into its decision.

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
