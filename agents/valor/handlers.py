"""
Telegram message handlers for the Valor Engels agent.

This module contains the message handling functions that integrate the Valor agent
with Telegram chat functionality, including context management and conversation history.
"""

import logging
from .agent import ValorContext, valor_agent

logger = logging.getLogger(__name__)


def _detect_mixed_content(message: str) -> bool:
    """
    Detect if a message contains both text and image components.

    Args:
        message: The message content to analyze

    Returns:
        bool: True if message contains both text and image indicators
    """
    if not message:
        return False

    message_upper = message.upper()

    # Check for explicit mixed content markers
    mixed_content_indicators = [
        "[IMAGE+TEXT]",  # New standardized marker for images
        "[PHOTO + TEXT]",  # Legacy marker for images
        "[DOCUMENT+TEXT]",  # Document with caption
        "[VIDEO+TEXT]",  # Video with caption
        "[VOICE+TEXT]",  # Voice message with caption
        "[AUDIO+TEXT]",  # Audio file with caption
        "MIXED CONTENT MESSAGE",  # New enhanced marker
        "BOTH TEXT AND AN IMAGE",  # Legacy enhanced marker
    ]

    for indicator in mixed_content_indicators:
        if indicator in message_upper:
            return True

    # Check for image analysis with user text patterns
    has_image_indicator = any(
        pattern in message_upper
        for pattern in ["[IMAGE FILE PATH:", "[IMAGE DOWNLOADED TO:", "IMAGE MESSAGE:"]
    )

    has_text_content = any(
        pattern in message_upper for pattern in ["USER'S TEXT:", "TEXT CONTENT", "TEXT MESSAGE:"]
    )

    # If we have both image indicators and text content, it's mixed
    if has_image_indicator and has_text_content:
        return True

    return False


def _build_enhanced_message_context(
    message: str,
    chat_id: int,
    chat_history_obj,
    notion_data: str | None,
    is_priority_question: bool,
    intent_result=None,
) -> str:
    """
    Build enhanced message with context information.

    Args:
        message: Original user message
        chat_id: Chat identifier
        chat_history_obj: Chat history manager
        notion_data: Optional Notion project data
        is_priority_question: Whether this is a priority question
        intent_result: Optional intent classification result

    Returns:
        str: Enhanced message with context
    """
    # Detect mixed content
    has_mixed_content = _detect_mixed_content(message)
    if has_mixed_content:
        logger.info(
            f"🖼️📝 MIXED CONTENT DETECTED: Message contains both text and image for chat {chat_id}"
        )
        logger.debug(
            f"Message preview: {message[:100]}..." if len(message) > 100 else f"Message: {message}"
        )

    context_parts = []

    # Add intent information to context if available
    if intent_result:
        intent_info = f"Detected Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})"
        if intent_result.reasoning:
            intent_info += f"\nReasoning: {intent_result.reasoning}"
        context_parts.append(intent_info)

    # Add recent chat context for continuity (always include if available)
    if chat_history_obj:
        telegram_messages = chat_history_obj.get_context(
            chat_id,
            max_context_messages=8,  # Up to 8 messages total
            max_age_hours=6,  # Only messages from last 6 hours
            always_include_last=2,  # Always include last 2 messages regardless of age
        )
        if telegram_messages:
            context_text = "Recent conversation:\n"
            for msg in telegram_messages:
                context_text += f"{msg['role']}: {msg['content']}\n"
            context_parts.append(context_text)

    # Include Notion data for priority questions (in addition to chat context)
    if is_priority_question and notion_data and "Error" not in notion_data:
        context_parts.append(f"Current project data:\n{notion_data}")

    # Combine all context with the current message
    if context_parts:
        if has_mixed_content:
            return (
                "\n\n".join(context_parts)
                + f"\n\n🖼️📝 CURRENT MESSAGE (MIXED CONTENT - text+image): {message}"
            )
        else:
            return "\n\n".join(context_parts) + f"\n\nCurrent message: {message}"
    else:
        if has_mixed_content:
            return f"🖼️📝 MIXED CONTENT MESSAGE (text+image): {message}"
        else:
            return message


async def handle_telegram_message(
    message: str,
    chat_id: int,
    username: str | None = None,
    is_group_chat: bool = False,
    chat_history_obj=None,
    notion_data: str | None = None,
    is_priority_question: bool = False,
    intent_result=None,
) -> str:
    """Handle a Telegram message using the PydanticAI agent with proper message history.

    This is the unified entry point for processing Telegram messages through the
    Valor Engels AI agent. It manages conversation context, integrates chat history,
    and orchestrates responses using the agent's available tools.

    The function:
    1. Creates a ValorContext with the provided information
    2. Builds an enhanced message with recent conversation context
    3. Processes the message through the PydanticAI agent with optional intent optimization
    4. Returns the agent's response for sending back to Telegram

    Args:
        message: The user's message text to process.
        chat_id: Unique Telegram chat identifier.
        username: Optional Telegram username of the sender.
        is_group_chat: Whether this message is from a group chat.
        chat_history_obj: ChatHistoryManager instance for conversation history.
        notion_data: Optional Notion project data for priority questions.
        is_priority_question: Whether this is asking about work priorities.
        intent_result: Optional IntentResult from intent classification for optimization.

    Returns:
        str: The agent's response message ready for sending to Telegram.

    Example:
        >>> response = await handle_telegram_message(
        ...     "What's the weather like?", 12345, "user123"
        ... )
        >>> type(response)
        <class 'str'>
    """

    # Log intent information if available
    if intent_result:
        logger.info(f"🔧 INTENT-AWARE HANDLER START for chat {chat_id}")
        logger.debug(f"   Intent: {intent_result.intent.value}")
        logger.debug(f"   Confidence: {intent_result.confidence:.2f}")
    else:
        logger.info(f"🤖 STANDARD HANDLER START for chat {chat_id}")

    # Prepare context
    context = ValorContext(
        chat_id=chat_id,
        username=username,
        is_group_chat=is_group_chat,
        chat_history=[],  # Legacy field, now handled by message_history
        chat_history_obj=chat_history_obj,  # Pass the history manager for search tools
        notion_data=notion_data,
        is_priority_question=is_priority_question,
        intent_result=intent_result,  # Add intent information to context
    )

    # Build enhanced message with context
    enhanced_message = _build_enhanced_message_context(
        message, chat_id, chat_history_obj, notion_data, is_priority_question, intent_result
    )

    # Handle intent-specific system prompt if available
    intent_system_prompt = None
    if intent_result:
        try:
            logger.debug("📦 Importing intent-specific modules...")
            from integrations.intent_prompts import get_intent_system_prompt

            logger.debug("✅ Intent modules imported successfully")

            logger.debug("🎯 Generating intent-specific system prompt...")
            prompt_context = {
                "chat_id": chat_id,
                "username": username,
                "is_group_chat": is_group_chat,
                "has_image": "[Image" in message or "image file path:" in message.lower(),
                "has_links": any(url in message.lower() for url in ["http://", "https://", "www."]),
            }
            intent_system_prompt = get_intent_system_prompt(intent_result, prompt_context)
            logger.debug(f"🎯 Using intent-specific system prompt for {intent_result.intent.value}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load intent-specific prompt: {e}")
            intent_system_prompt = None

    # Run the agent with error handling and optional intent optimization
    original_system_prompt = None
    try:
        # Handle intent-specific system prompt if available
        if intent_result and intent_system_prompt:
            logger.debug("🚀 Starting Valor agent execution with intent-specific prompt...")
            original_system_prompt = valor_agent.system_prompt
            valor_agent.system_prompt = intent_system_prompt
        else:
            logger.debug("🚀 Starting Valor agent execution with default prompt...")

        logger.debug(f"⏳ Executing agent.run() with enhanced message ({len(enhanced_message)} chars)...")
        result = await valor_agent.run(enhanced_message, deps=context)
        logger.debug("✅ Agent execution completed successfully")

        if not result or not hasattr(result, "output") or not result.output:
            logger.warning(f"⚠️ Agent returned empty result for chat {chat_id}")
            return "I processed your message but didn't generate a response. Please try again."

        # Extract tool usage information
        actions_taken = []
        try:
            # Check if result has tool call information
            if hasattr(result, "messages") and result.messages:
                for msg in result.messages:
                    if hasattr(msg, "parts"):
                        for part in msg.parts:
                            if hasattr(part, "tool_name"):
                                # Map tool names to user-friendly action descriptions
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
                                    "read_project_documentation": "📖 Documentation Read",
                                }
                                action_name = tool_action_map.get(
                                    part.tool_name, f"🔧 {part.tool_name}"
                                )
                                if action_name not in actions_taken:
                                    actions_taken.append(action_name)
                                logger.debug(f"🔧 Tool used: {part.tool_name}")
        except Exception as e:
            logger.warning(f"⚠️ Could not extract tool usage information: {e}")

        # Add action summary to the beginning of the response if actions were taken
        output = result.output
        if actions_taken and not output.startswith("TELEGRAM_IMAGE_GENERATED|"):
            action_summary = "Actions: " + ", ".join(actions_taken) + "\n\n"
            output = action_summary + output

        logger.debug(f"📤 Agent response length: {len(output)} chars")
        return output

    except Exception as e:
        error_msg = f"Error running valor_agent: {str(e)}"
        logger.error(f"❌ {error_msg}")
        import traceback

        logger.error("Exception traceback:", exc_info=True)
        return f"I encountered an error processing your message: {str(e)}"

    finally:
        # Restore original system prompt if it was modified
        if original_system_prompt is not None:
            valor_agent.system_prompt = original_system_prompt


# Backward compatibility alias - now points to the unified handler
handle_telegram_message_with_intent = handle_telegram_message


# Backward compatibility functions - simplified wrappers around unified handler


async def handle_user_priority_question(
    question: str, chat_id: int, chat_history_obj, notion_scout=None, username: str | None = None
) -> str:
    """Handle user priority questions - wrapper for backward compatibility."""
    # notion_scout parameter kept for backward compatibility but no longer used
    # Notion functionality now handled through MCP pm_tools server
    notion_data = None
    context_messages = chat_history_obj.get_context(chat_id)
    context_has_project_info = any(
        keyword in msg["content"].lower()
        for msg in context_messages[-5:]
        for keyword in ["project", "task", "working on", "status", "priority", "dev", "development"]
    )
    if not context_has_project_info:
        notion_data = "Notion data unavailable in current implementation"

    return await handle_telegram_message(
        message=question,
        chat_id=chat_id,
        username=username,
        chat_history_obj=chat_history_obj,
        notion_data=notion_data,
        is_priority_question=True,
    )


async def handle_general_question(
    question: str, chat_id: int, chat_history_obj, username: str | None = None
) -> str:
    """Handle general questions - wrapper for backward compatibility."""
    return await handle_telegram_message(
        message=question,
        chat_id=chat_id,
        username=username,
        chat_history_obj=chat_history_obj,
        is_priority_question=False,
    )
