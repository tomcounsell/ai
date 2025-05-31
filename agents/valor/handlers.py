"""
Telegram message handlers for the Valor Engels agent.

This module contains the message handling functions that integrate the Valor agent
with Telegram chat functionality, including context management and conversation history.
"""

from .agent import ValorContext, valor_agent


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
    has_image_indicator = any(pattern in message_upper for pattern in [
        "[IMAGE FILE PATH:",
        "[IMAGE DOWNLOADED TO:",
        "IMAGE MESSAGE:"
    ])
    
    has_text_content = any(pattern in message_upper for pattern in [
        "USER'S TEXT:",
        "TEXT CONTENT",
        "TEXT MESSAGE:"
    ])
    
    # If we have both image indicators and text content, it's mixed
    if has_image_indicator and has_text_content:
        return True
        
    return False



async def handle_telegram_message(
    message: str,
    chat_id: int,
    username: str | None = None,
    is_group_chat: bool = False,
    chat_history_obj=None,
    notion_data: str | None = None,
    is_priority_question: bool = False,
) -> str:
    """Handle a Telegram message using the PydanticAI agent with proper message history.

    This is the main entry point for processing Telegram messages through the
    Valor Engels AI agent. It manages conversation context, integrates chat history,
    and orchestrates responses using the agent's available tools.

    The function:
    1. Creates a ValorContext with the provided information
    2. Builds an enhanced message with recent conversation context
    3. Processes the message through the PydanticAI agent
    4. Returns the agent's response for sending back to Telegram

    Args:
        message: The user's message text to process.
        chat_id: Unique Telegram chat identifier.
        username: Optional Telegram username of the sender.
        is_group_chat: Whether this message is from a group chat.
        chat_history_obj: ChatHistoryManager instance for conversation history.
        notion_data: Optional Notion project data for priority questions.
        is_priority_question: Whether this is asking about work priorities.

    Returns:
        str: The agent's response message ready for sending to Telegram.

    Example:
        >>> response = await handle_telegram_message(
        ...     "What's the weather like?", 12345, "user123"
        ... )
        >>> type(response)
        <class 'str'>
    """

    # Prepare context
    context = ValorContext(
        chat_id=chat_id,
        username=username,
        is_group_chat=is_group_chat,
        chat_history=[],  # Legacy field, now handled by message_history
        chat_history_obj=chat_history_obj,  # Pass the history manager for search tools
        notion_data=notion_data,
        is_priority_question=is_priority_question,
    )

    # Add contextual information to the user message if needed
    enhanced_message = message
    
    # Detect if this message contains both text and image components
    has_mixed_content = _detect_mixed_content(message)
    if has_mixed_content:
        print(f"🖼️📝 MIXED CONTENT DETECTED: Message contains both text and image for chat {chat_id}")
        print(f"Message preview: {message[:100]}..." if len(message) > 100 else f"Message: {message}")

    # Build enhanced message with context - always include chat history when available
    context_parts = []
    
    # Add recent chat context for continuity (always include if available)
    if chat_history_obj:
        telegram_messages = chat_history_obj.get_context(
            chat_id, 
            max_context_messages=8,  # Up to 8 messages total
            max_age_hours=6,         # Only messages from last 6 hours
            always_include_last=2    # Always include last 2 messages regardless of age
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
            enhanced_message = "\n\n".join(context_parts) + f"\n\n🖼️📝 CURRENT MESSAGE (MIXED CONTENT - text+image): {message}"
        else:
            enhanced_message = "\n\n".join(context_parts) + f"\n\nCurrent message: {message}"
    else:
        if has_mixed_content:
            enhanced_message = f"🖼️📝 MIXED CONTENT MESSAGE (text+image): {message}"
        else:
            enhanced_message = message

    # Run the agent
    result = await valor_agent.run(enhanced_message, deps=context)

    return result.output


# Convenience functions for backward compatibility with existing handlers


async def handle_user_priority_question(
    question: str, chat_id: int, chat_history_obj, notion_scout=None, username: str | None = None
) -> str:
    """Handle user priority questions using PydanticAI agent with message history.

    This function provides backward compatibility for the previous handler system
    while routing priority questions through the new PydanticAI agent. It checks
    for project context and optionally integrates Notion data.

    Args:
        question: The user's priority-related question.
        chat_id: Telegram chat identifier.
        chat_history_obj: ChatHistoryManager instance for context.
        notion_scout: Optional NotionScout instance for project data.
        username: Optional Telegram username.

    Returns:
        str: Agent response addressing the priority question.
    """

    # Check if there's project context in recent conversation
    context_has_project_info = False
    if chat_history_obj:
        context_messages = chat_history_obj.get_context(chat_id)
        for msg in context_messages[-5:]:
            if any(
                keyword in msg["content"].lower()
                for keyword in ["project", "task", "working on", "psyoptimal", "flextrip"]
            ):
                context_has_project_info = True
                break

    # Get Notion data if needed and available
    notion_data = None
    if notion_scout and not context_has_project_info:
        # Note: Notion scout calls would need to be converted to async
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
    """Handle general questions using PydanticAI agent with message history.

    This function provides backward compatibility for the previous handler system
    while routing general questions through the new PydanticAI agent. It's used
    for non-priority conversations and casual interactions.

    Args:
        question: The user's general question or message.
        chat_id: Telegram chat identifier.
        chat_history_obj: ChatHistoryManager instance for context.
        username: Optional Telegram username.

    Returns:
        str: Agent response to the general question.
    """

    return await handle_telegram_message(
        message=question,
        chat_id=chat_id,
        username=username,
        chat_history_obj=chat_history_obj,
        is_priority_question=False,
    )
