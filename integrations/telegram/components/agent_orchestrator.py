"""
AgentOrchestrator: Single point for agent interaction.

Consolidates all agent routing and processing logic into a unified component.
"""

import asyncio
import logging
import time
from typing import Any

from integrations.telegram.models import (
    AgentResponse,
    Intent,
    MediaAttachment,
    MessageContext,
    ProcessingPlan,
)

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Single point for agent interaction."""

    def __init__(self, valor_agent=None, intent_classifier=None):
        """Initialize with optional agent and classifier."""
        self.valor_agent = valor_agent
        self.intent_classifier = intent_classifier
        self.streaming_timeout = 30  # seconds
        self.sync_timeout = 60  # seconds for sync operations

    async def process_with_agent(
        self, context: MessageContext, plan: ProcessingPlan
    ) -> AgentResponse:
        """
        Unified agent processing with context.

        Handles:
        1. Intent classification if needed
        2. Context preparation
        3. Agent invocation
        4. Response streaming
        5. Error handling

        Returns:
            AgentResponse with processed content
        """
        start_time = time.time()

        try:
            # Classify intent if needed
            if plan.intent is None and self._should_classify_intent(plan):
                plan.intent = await self._classify_intent_if_needed(context)

            # Prepare agent context
            agent_context = self._prepare_agent_context(context, plan)

            # Process with agent
            if plan.requires_agent:
                response = await self._invoke_agent(context, plan, agent_context)
            else:
                # Handle non-agent responses (commands, etc.)
                response = await self._handle_special_response(context, plan)

            # Add metadata
            response.processing_time = time.time() - start_time
            response.model_used = plan.agent_config.model

            return response

        except TimeoutError:
            logger.error(f"Agent processing timeout for chat {context.chat_id}")
            return AgentResponse(
                content="⏱️ Response timeout. Please try again.",
                metadata={"error": "timeout", "processing_time": time.time() - start_time},
            )
        except Exception as e:
            logger.error(f"Agent processing error: {str(e)}", exc_info=True)
            return AgentResponse(
                content=f"❌ Processing error: {str(e)}",
                metadata={"error": str(e), "processing_time": time.time() - start_time},
            )

    def _should_classify_intent(self, plan: ProcessingPlan) -> bool:
        """Check if intent classification is needed."""
        return (
            plan.requires_agent
            and plan.intent is None
            and "intent_classification" not in (plan.metadata.get("skip_features", []))
        )

    async def _classify_intent_if_needed(self, context: MessageContext) -> Intent | None:
        """Lightweight intent classification."""
        if not self.intent_classifier:
            return None

        try:
            # Quick rule-based classification first
            text_lower = context.cleaned_text.lower()

            # Clear command intent
            if text_lower.startswith("/"):
                return Intent.COMMAND

            # Question detection
            if any(indicator in text_lower for indicator in ["?", "what", "how", "why", "when"]):
                return Intent.QUESTION

            # Task detection
            if any(
                word in text_lower for word in ["create", "make", "build", "fix", "update", "add"]
            ):
                return Intent.TASK

            # Feedback detection
            if any(
                word in text_lower for word in ["feedback", "suggestion", "issue", "problem", "bug"]
            ):
                return Intent.FEEDBACK

            # For ambiguous cases, use LLM classifier if available
            if self.intent_classifier and len(context.cleaned_text) > 20:
                return await self.intent_classifier.classify(context.cleaned_text)

            return Intent.CONVERSATION

        except Exception as e:
            logger.error(f"Intent classification error: {str(e)}")
            return Intent.UNKNOWN

    def _prepare_agent_context(
        self, context: MessageContext, plan: ProcessingPlan
    ) -> dict[str, Any]:
        """Convert MessageContext to agent-specific context."""
        # Build Valor context
        valor_context = {
            "chat_id": str(context.chat_id),
            "username": context.username,
            "chat_history": context.chat_history,
            "workspace": context.workspace,
            "working_directory": context.working_directory,
            "is_priority_question": plan.priority.value in ["high", "critical"],
            "message_text": context.cleaned_text,
        }

        # Add intent information
        if plan.intent:
            valor_context["detected_intent"] = plan.intent.value

        # Add reply context
        if context.reply_context:
            valor_context["reply_to"] = {
                "text": context.reply_context.get("text"),
                "username": context.reply_context.get("username"),
                "is_bot": context.reply_context.get("is_bot"),
            }

        # Add media context
        if context.media_info:
            valor_context["media_type"] = context.media_info.media_type.value
            valor_context["media_file_id"] = context.media_info.file_id

        # Add special handlers context
        if plan.special_handlers:
            valor_context["special_handlers"] = plan.special_handlers

        # Add enabled tools
        if plan.agent_config.tools_enabled:
            valor_context["enabled_tools"] = plan.agent_config.tools_enabled

        return valor_context

    async def _invoke_agent(
        self, context: MessageContext, plan: ProcessingPlan, agent_context: dict[str, Any]
    ) -> AgentResponse:
        """Invoke Valor agent with prepared context."""
        if not self.valor_agent:
            return AgentResponse(
                content="Agent not available. Please try again later.",
                metadata={"error": "no_agent"},
            )

        try:
            # Get the agent handler
            from agents.valor.handlers import handle_with_valor

            # Process with appropriate mode
            if plan.agent_config.streaming:
                response_text = await self._handle_streaming_response(context, agent_context)
            else:
                response_text = await asyncio.wait_for(
                    handle_with_valor(message=context.cleaned_text, context=agent_context),
                    timeout=self.sync_timeout,
                )

            # Parse special markers in response
            return self._parse_agent_response(response_text)

        except Exception as e:
            logger.error(f"Agent invocation error: {str(e)}", exc_info=True)
            raise

    async def _handle_streaming_response(
        self, context: MessageContext, agent_context: dict[str, Any]
    ) -> str:
        """Process streaming agent responses."""
        from agents.valor.handlers import handle_with_valor_streaming

        response_parts = []

        async for chunk in handle_with_valor_streaming(
            message=context.cleaned_text, context=agent_context
        ):
            response_parts.append(chunk)

            # Check for timeout
            if len(response_parts) > 100:  # Safety limit
                break

        return "".join(response_parts)

    def _parse_agent_response(self, response_text: str) -> AgentResponse:
        """Parse agent response for special markers and attachments."""
        response = AgentResponse(content=response_text)

        # Check for async promise marker
        if response_text.startswith("ASYNC_PROMISE|"):
            response.metadata["is_async_promise"] = True
            response.content = response_text.split("|", 1)[1]

        # Check for image generation marker
        if "TELEGRAM_IMAGE_GENERATED|" in response_text:
            parts = response_text.split("TELEGRAM_IMAGE_GENERATED|")
            response.content = parts[0].strip()

            # Parse image info
            image_info = parts[1].split("|")
            if len(image_info) >= 2:
                response.media_attachments.append(
                    MediaAttachment(
                        file_path=image_info[0],
                        media_type="image",
                        caption=image_info[1] if len(image_info) > 1 else None,
                    )
                )

        # Check for reaction markers
        reaction_pattern = r"REACTION:(\S+)"
        import re

        matches = re.findall(reaction_pattern, response_text)
        if matches:
            response.reactions.extend(matches)
            # Remove reaction markers from content
            response.content = re.sub(reaction_pattern, "", response.content).strip()

        return response

    async def _handle_special_response(
        self, context: MessageContext, plan: ProcessingPlan
    ) -> AgentResponse:
        """Handle responses that don't require agent processing."""
        # Command responses
        if "start_command" in plan.special_handlers:
            return AgentResponse(
                content=(
                    "👋 **Welcome to Valor AI Assistant!**\n\n"
                    "I'm here to help you with:\n"
                    "• 💬 Natural conversations\n"
                    "• 🔍 Current information search\n"
                    "• 💻 Code assistance\n"
                    "• 🖼️ Image analysis\n"
                    "• 📊 Project management\n\n"
                    "Just send me a message or mention me in a group!"
                ),
                metadata={"command": "start"},
            )

        elif "help_command" in plan.special_handlers:
            return AgentResponse(
                content=(
                    "🤖 **Valor AI Help**\n\n"
                    "**Commands:**\n"
                    "/start - Welcome message\n"
                    "/help - This help message\n"
                    "/status - System status\n\n"
                    "**Features:**\n"
                    "• Send photos for analysis\n"
                    "• Share URLs for summarization\n"
                    "• Ask questions about anything\n"
                    "• Get coding assistance\n\n"
                    "**In Groups:**\n"
                    "Mention me with @valoraibot to get my attention!"
                ),
                metadata={"command": "help"},
            )

        elif "status_command" in plan.special_handlers:
            return AgentResponse(
                content="✅ System operational. All services running normally.",
                metadata={"command": "status"},
            )

        # Default response
        return AgentResponse(
            content="Message received but no handler available.", metadata={"unhandled": True}
        )
