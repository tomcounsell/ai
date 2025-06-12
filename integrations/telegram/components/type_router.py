"""
TypeRouter: Message type detection and routing to specialized handlers.

Consolidates message type detection logic and determines processing strategy.
"""

import logging
import re

from integrations.telegram.models import (
    MessageContext,
    MessageType,
    Priority,
    ProcessingPlan,
    ResponseFormat,
)

logger = logging.getLogger(__name__)


class TypeRouter:
    """Message type detection and routing to specialized handlers."""

    def __init__(self):
        """Initialize TypeRouter with patterns and configurations."""
        # Command patterns
        self.command_patterns = {
            "/start": "start_command",
            "/help": "help_command",
            "/status": "status_command",
            "/cancel": "cancel_command",
        }

        # URL pattern
        self.url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

        # Code patterns
        self.code_block_pattern = re.compile(r"```[\s\S]*?```")
        self.inline_code_pattern = re.compile(r"`[^`]+`")

        # Question patterns (simple heuristics)
        self.question_indicators = [
            "?",
            "how to",
            "what is",
            "why",
            "when",
            "where",
            "can you",
            "could you",
            "would you",
            "should i",
        ]

    async def route_message(self, context: MessageContext) -> ProcessingPlan:
        """
        Determine processing strategy based on message type and content.

        Analyzes:
        1. Message type (text, media, etc.)
        2. System commands
        3. Special patterns (URLs, code)
        4. Intent requirements
        5. Priority level

        Returns:
            ProcessingPlan with routing strategy
        """
        # Detect message type
        message_type = self._detect_message_type(context)

        # Start with base plan
        plan = ProcessingPlan(
            message_type=message_type,
            requires_agent=True,  # Default to requiring agent
            response_format=ResponseFormat.TEXT,
        )

        # Route based on message type
        if message_type == MessageType.TEXT:
            self._route_text_message(context, plan)
        elif message_type in [MessageType.PHOTO, MessageType.VIDEO]:
            self._route_visual_media(context, plan)
        elif message_type == MessageType.DOCUMENT:
            self._route_document(context, plan)
        elif message_type in [MessageType.AUDIO, MessageType.VOICE]:
            self._route_audio_media(context, plan)
        elif message_type == MessageType.COMMAND:
            self._route_command(context, plan)

        # Set priority based on context
        self._determine_priority(context, plan)

        # Determine if intent classification needed
        if self._requires_intent_classification(context, plan):
            plan.intent = None  # Will be filled by classifier

        logger.debug(
            f"Routed message type={message_type} "
            f"requires_agent={plan.requires_agent} "
            f"priority={plan.priority.value}"
        )

        return plan

    def _detect_message_type(self, context: MessageContext) -> MessageType:
        """Unified message type detection."""
        # Check for command first
        if context.cleaned_text.startswith("/"):
            return MessageType.COMMAND

        # Check media types
        if context.media_info:
            return context.media_info.media_type

        # Default to text if has text content
        if context.cleaned_text:
            return MessageType.TEXT

        return MessageType.UNKNOWN

    def _route_text_message(self, context: MessageContext, plan: ProcessingPlan):
        """Route text message and detect special patterns."""
        text = context.cleaned_text.lower()

        # Detect URLs
        urls = self.url_pattern.findall(context.cleaned_text)
        if urls:
            plan.special_handlers.append("url_handler")
            plan.metadata["urls"] = urls

        # Detect code blocks
        if self.code_block_pattern.search(context.cleaned_text) or self.inline_code_pattern.search(
            context.cleaned_text
        ):
            plan.special_handlers.append("code_handler")
            plan.agent_config.tools_enabled.append("delegate_coding_task")

        # Detect questions
        if any(indicator in text for indicator in self.question_indicators):
            plan.metadata["likely_question"] = True
            plan.agent_config.tools_enabled.append("search_current_info")

    def _route_visual_media(self, context: MessageContext, plan: ProcessingPlan):
        """Route photo/video messages."""
        plan.response_format = ResponseFormat.TEXT
        plan.special_handlers.append("media_download_handler")
        plan.agent_config.tools_enabled.extend(["analyze_shared_image", "detailed_image_analysis"])

        # Check for specific analysis request in caption
        caption = context.message.caption or ""
        if "analyze" in caption.lower() or "what" in caption.lower():
            plan.metadata["analysis_requested"] = True
            plan.priority = Priority.HIGH

    def _route_document(self, context: MessageContext, plan: ProcessingPlan):
        """Route document messages."""
        plan.special_handlers.append("document_handler")

        # Check document type
        if context.media_info and context.media_info.mime_type:
            mime_type = context.media_info.mime_type

            if mime_type.startswith("image/"):
                plan.agent_config.tools_enabled.append("analyze_shared_image")
            elif mime_type == "application/pdf":
                plan.agent_config.tools_enabled.append("summarize_document")
            elif mime_type.startswith("text/") or mime_type in [
                "application/json",
                "application/xml",
                "application/x-yaml",
            ]:
                plan.agent_config.tools_enabled.append("analyze_code_file")

    def _route_audio_media(self, context: MessageContext, plan: ProcessingPlan):
        """Route audio/voice messages."""
        plan.special_handlers.append("audio_transcription_handler")
        plan.metadata["requires_transcription"] = True

        # Voice messages often contain questions
        if context.media_info.media_type == MessageType.VOICE:
            plan.metadata["likely_question"] = True

    def _route_command(self, context: MessageContext, plan: ProcessingPlan):
        """Route command messages."""
        command = context.cleaned_text.split()[0].lower()

        if command in self.command_patterns:
            plan.special_handlers.append(self.command_patterns[command])
            plan.requires_agent = False  # Most commands don't need agent
            plan.response_format = ResponseFormat.MARKDOWN
        else:
            # Unknown command - let agent handle
            plan.metadata["unknown_command"] = command

    def _determine_priority(self, context: MessageContext, plan: ProcessingPlan):
        """Determine message priority based on context."""
        # High priority conditions
        if any(
            [
                context.is_mention and context.is_private_chat,
                plan.message_type == MessageType.COMMAND,
                context.reply_context and context.reply_context.get("is_bot"),
                plan.metadata.get("analysis_requested"),
            ]
        ):
            plan.priority = Priority.HIGH

        # Medium priority (default)
        elif any([context.is_mention, context.is_dev_group, plan.metadata.get("likely_question")]):
            plan.priority = Priority.MEDIUM

        # Low priority
        else:
            plan.priority = Priority.LOW

    def _requires_intent_classification(
        self, context: MessageContext, plan: ProcessingPlan
    ) -> bool:
        """Determine if intent classification is needed."""
        # Skip intent for clear cases
        if any(
            [
                plan.message_type == MessageType.COMMAND,
                plan.message_type in [MessageType.PHOTO, MessageType.VIDEO],
                not plan.requires_agent,
                len(context.cleaned_text) < 10,  # Very short messages
            ]
        ):
            return False

        # Need intent for complex text processing
        return True

    def detect_special_patterns(self, text: str) -> list[str]:
        """Detect special patterns in text that need handling."""
        patterns = []

        if self.url_pattern.search(text):
            patterns.append("url")

        if self.code_block_pattern.search(text) or self.inline_code_pattern.search(text):
            patterns.append("code")

        if any(text.lower().startswith(cmd) for cmd in self.command_patterns):
            patterns.append("command")

        if text.count("\n") > 5 or len(text) > 500:
            patterns.append("long_text")

        return patterns
