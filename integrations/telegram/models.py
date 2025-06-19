"""
Unified data models for Telegram message processing.

These models provide a consistent structure for message handling across
all components of the refactored system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Using pyrogram, not python-telegram-bot
from typing import Any, Any as TelegramMessage


class MessageType(Enum):
    """Types of messages we can process."""

    TEXT = "text"
    PHOTO = "photo"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    STICKER = "sticker"
    COMMAND = "command"
    UNKNOWN = "unknown"


class ProcessingPriority(Enum):
    """Message processing priority levels."""

    CRITICAL = "critical"  # System messages, errors
    HIGH = "high"  # User questions, mentions
    MEDIUM = "medium"  # Regular chat messages
    LOW = "low"  # Background processing


class ResponseFormat(Enum):
    """How to format the response."""

    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
    MEDIA = "media"
    REACTION = "reaction"


class Intent(Enum):
    """Detected message intents."""

    QUESTION = "question"
    COMMAND = "command"
    FEEDBACK = "feedback"
    TASK = "task"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"


@dataclass
class MediaInfo:
    """Information about media attachments."""

    media_type: MessageType
    file_id: str
    file_unique_id: str
    file_size: int | None = None
    mime_type: str | None = None
    file_name: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    thumbnail_file_id: str | None = None
    file_path: str | None = None


@dataclass
class MessageContext:
    """Unified context object for all message processing."""

    message: TelegramMessage
    chat_id: int
    username: str
    workspace: str | None
    working_directory: str | None
    is_dev_group: bool
    is_mention: bool
    cleaned_text: str
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    reply_context: dict[str, Any] | None = None
    media_info: MediaInfo | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_private_chat(self) -> bool:
        """Check if this is a private chat."""
        return self.chat_id > 0

    @property
    def requires_response(self) -> bool:
        """Determine if message requires a response."""
        return self.is_dev_group or self.is_mention or self.is_private_chat


@dataclass
class AgentConfig:
    """Configuration for agent processing."""

    model: str = "claude-3-5-sonnet-20241022"
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt_additions: list[str] = field(default_factory=list)
    tools_enabled: list[str] = field(default_factory=list)
    streaming: bool = True


@dataclass
class ProcessingPlan:
    """Strategy for processing this message."""

    message_type: MessageType
    requires_agent: bool
    intent: Intent | None = None
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    response_format: ResponseFormat = ResponseFormat.TEXT
    priority: ProcessingPriority = ProcessingPriority.MEDIUM
    special_handlers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaAttachment:
    """Media attachment in agent response."""

    file_path: str
    media_type: str
    caption: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """Unified response from agent processing."""

    content: str
    media_attachments: list[MediaAttachment] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    processing_time: float | None = None
    model_used: str | None = None
    tokens_used: int | None = None

    @property
    def has_media(self) -> bool:
        """Check if response includes media."""
        return len(self.media_attachments) > 0

    @property
    def is_async_promise(self) -> bool:
        """Check if this is an async promise response."""
        return self.content.startswith("ASYNC_PROMISE|")


@dataclass
class AccessResult:
    """Security validation result."""

    allowed: bool
    reason: str | None = None
    rate_limit_remaining: int | None = None
    requires_verification: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    """Result of message delivery attempt."""

    success: bool
    message_id: int | None = None
    error: str | None = None
    retry_after: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingResult:
    """Overall result of message processing."""

    success: bool
    summary: str
    context: MessageContext | None = None
    response: AgentResponse | None = None
    delivery: DeliveryResult | None = None
    error: str | None = None
    processing_time: float | None = None

    @classmethod
    def denied(cls, reason: str) -> "ProcessingResult":
        """Create a denied result."""
        return cls(success=False, summary=f"Access denied: {reason}", error=reason)

    @classmethod
    def failed(cls, error: str, context: MessageContext | None = None) -> "ProcessingResult":
        """Create a failed result."""
        return cls(
            success=False, summary=f"Processing failed: {error}", context=context, error=error
        )

    @classmethod
    def succeeded(
        cls,
        summary: str,
        context: MessageContext,
        response: AgentResponse,
        delivery: DeliveryResult,
    ) -> "ProcessingResult":
        """Create a successful result."""
        return cls(
            success=True, summary=summary, context=context, response=response, delivery=delivery
        )
