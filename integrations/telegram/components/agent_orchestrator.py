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
            valor_context["detected_intent"] = plan.intent.intent.value if hasattr(plan.intent, 'intent') and plan.intent.intent else str(plan.intent)

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
        """
        Optimized Valor agent invocation with direct agent access.
        
        This replaces the previous fragmented handler approach with direct agent execution,
        providing better performance, cleaner error handling, and unified context management.
        """
        if not self.valor_agent:
            return AgentResponse(
                content="Agent not available. Please try again later.",
                metadata={"error": "no_agent"},
            )

        try:
            # Import agent and context directly
            from agents.valor.agent import valor_agent, ValorContext
            
            # Convert agent_context dict to proper ValorContext
            valor_ctx = ValorContext(
                chat_id=agent_context.get("chat_id"),
                username=agent_context.get("username"),
                is_group_chat=not context.is_private_chat,
                chat_history=agent_context.get("chat_history", []),
                chat_history_obj=agent_context.get("chat_history_obj"),
                notion_data=agent_context.get("notion_data"),
                is_priority_question=agent_context.get("is_priority_question", False),
                intent_result=plan.intent
            )

            # Build enhanced message with context for agent
            enhanced_message = self._build_enhanced_message(context, plan, agent_context)

            # Apply intent-specific system prompt if available
            original_prompt = None
            if plan.intent and plan.intent != Intent.UNKNOWN:
                original_prompt = await self._apply_intent_optimization(valor_agent, plan.intent, context)

            try:
                # Execute agent with timeout and proper context
                logger.debug(f"⚡ Executing valor_agent.run() with message ({len(enhanced_message)} chars)")
                
                if plan.agent_config.streaming:
                    # Handle streaming mode with real-time token delivery
                    response_text = await self._execute_streaming_agent(valor_agent, enhanced_message, valor_ctx)
                else:
                    # Handle sync mode with timeout
                    result = await asyncio.wait_for(
                        valor_agent.run(enhanced_message, deps=valor_ctx),
                        timeout=self.sync_timeout
                    )
                    response_text = result.output if result.output else "No response generated."

                # Extract tool usage for metadata
                tool_actions = self._extract_tool_actions(result if 'result' in locals() else None)
                
                # Parse response with comprehensive marker detection
                agent_response = self._parse_agent_response(response_text)
                agent_response.metadata.update({
                    "tools_used": tool_actions,
                    "intent": plan.intent.value if plan.intent else None,
                    "context_size": len(enhanced_message),
                    "model": "claude-3-5-sonnet-20241022"
                })

                return agent_response

            finally:
                # Restore original system prompt if modified
                if original_prompt is not None:
                    valor_agent.system_prompt = original_prompt

        except asyncio.TimeoutError:
            logger.error(f"⏱️ Agent execution timeout ({self.sync_timeout}s) for chat {context.chat_id}")
            return AgentResponse(
                content="⏱️ Response timeout. The request is taking longer than expected. Please try again.",
                metadata={"error": "timeout", "timeout_duration": self.sync_timeout}
            )
        except Exception as e:
            logger.error(f"❌ Agent invocation error: {str(e)}", exc_info=True)
            return AgentResponse(
                content=f"❌ I encountered an error processing your message: {str(e)}",
                metadata={"error": str(e), "error_type": type(e).__name__}
            )

    def _build_enhanced_message(
        self, context: MessageContext, plan: ProcessingPlan, agent_context: dict[str, Any]
    ) -> str:
        """Build enhanced message with comprehensive context for the agent."""
        message_parts = []
        
        # Add intent context if available
        if plan.intent and plan.intent != Intent.UNKNOWN:
            intent_info = f"Detected Intent: {plan.intent.value}"
            if hasattr(plan, 'intent_confidence'):
                intent_info += f" (confidence: {plan.intent_confidence:.2f})"
            message_parts.append(intent_info)
        
        # Add chat history context
        if agent_context.get("chat_history"):
            recent_history = agent_context["chat_history"][-5:]  # Last 5 messages
            if recent_history:
                history_text = "Recent conversation:\n"
                for msg in recent_history:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")[:200] + ("..." if len(msg.get("content", "")) > 200 else "")
                    history_text += f"{role}: {content}\n"
                message_parts.append(history_text)
        
        # Add workspace/project context for priority questions
        if agent_context.get("is_priority_question") and agent_context.get("notion_data"):
            notion_data = agent_context["notion_data"]
            if notion_data and "Error" not in notion_data:
                message_parts.append(f"Current project data:\n{notion_data}")
        
        # Add media context if present
        if context.media_info:
            media_desc = f"Media Type: {context.media_info.media_type.value}"
            if hasattr(context.media_info, 'file_path') and context.media_info.file_path:
                media_desc += f"\nFile Path: {context.media_info.file_path}"
            message_parts.append(media_desc)
        
        # Add reply context if present
        if context.reply_context:
            reply_info = f"Replying to: {context.reply_context.get('text', 'Previous message')}"
            if context.reply_context.get('username'):
                reply_info += f" (from @{context.reply_context['username']})"
            message_parts.append(reply_info)
        
        # Combine context with current message
        if message_parts:
            context_block = "\n\n".join(message_parts)
            return f"{context_block}\n\nCurrent message: {context.cleaned_text}"
        else:
            return context.cleaned_text

    async def _apply_intent_optimization(self, agent, intent: Intent, context: MessageContext) -> str | None:
        """Apply intent-specific system prompt optimization."""
        try:
            from integrations.intent_prompts import get_intent_system_prompt
            
            prompt_context = {
                "chat_id": context.chat_id,
                "username": context.username,
                "is_group_chat": not context.is_private_chat,
                "has_image": context.media_info is not None,
                "has_links": any(url in context.cleaned_text.lower() 
                               for url in ["http://", "https://", "www."]),
                "message_length": len(context.cleaned_text)
            }
            
            intent_prompt = get_intent_system_prompt(intent, prompt_context)
            if intent_prompt:
                original_prompt = agent.system_prompt
                agent.system_prompt = intent_prompt
                logger.debug(f"🎯 Applied intent-specific prompt for {intent.value}")
                return original_prompt
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to apply intent optimization: {e}")
        
        return None

    async def _execute_streaming_agent(self, agent, message: str, context) -> str:
        """Execute agent in streaming mode with progress updates."""
        try:
            # Note: PydanticAI doesn't have built-in streaming for agent.run()
            # This is a placeholder for when streaming becomes available
            # For now, fall back to sync execution
            logger.debug("📡 Streaming mode requested but not available, using sync execution")
            result = await agent.run(message, deps=context)
            return result.output if result.output else "No response generated."
            
        except Exception as e:
            logger.error(f"❌ Streaming execution failed: {e}")
            raise

    def _extract_tool_actions(self, result) -> list[str]:
        """Extract tool usage information from agent result."""
        actions = []
        
        if not result or not hasattr(result, "messages"):
            return actions
            
        try:
            # Tool name mapping for user-friendly descriptions
            tool_action_map = {
                "search_current_info": "🔍 Web Search",
                "create_image": "🎨 Image Generation", 
                "analyze_shared_image": "👁️ Image Analysis",
                "save_link_for_later": "🔗 Link Saved",
                "search_saved_links": "📚 Link Search",
                "query_notion_projects": "📋 Project Query",
                "delegate_coding_task": "💻 Development Task",
                "search_telegram_history": "💬 Chat History Search",
                "get_telegram_context_summary": "📝 Context Summary",
                "spawn_valor_session": "🚀 Valor Session",
            }
            
            for message in result.messages:
                if hasattr(message, 'parts'):
                    for part in message.parts:
                        if hasattr(part, 'tool_name') and part.tool_name:
                            action = tool_action_map.get(part.tool_name, f"🔧 {part.tool_name}")
                            if action not in actions:
                                actions.append(action)
                                
        except Exception as e:
            logger.warning(f"⚠️ Could not extract tool actions: {e}")
            
        return actions

    async def _handle_streaming_response(
        self, context: MessageContext, agent_context: dict[str, Any]
    ) -> str:
        """Legacy streaming method - now redirects to new implementation."""
        from agents.valor.agent import valor_agent, ValorContext
        
        valor_ctx = ValorContext(
            chat_id=agent_context.get("chat_id"),
            username=agent_context.get("username"),
            is_group_chat=not context.is_private_chat,
            chat_history=agent_context.get("chat_history", []),
            chat_history_obj=agent_context.get("chat_history_obj"),
            notion_data=agent_context.get("notion_data"),
            is_priority_question=agent_context.get("is_priority_question", False)
        )
        
        return await self._execute_streaming_agent(valor_agent, context.cleaned_text, valor_ctx)

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
