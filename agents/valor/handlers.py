"""
Telegram message handlers for the Valor Engels agent.

This module contains the message handling functions that integrate the Valor agent
with Telegram chat functionality, including context management and conversation history.
"""

from .agent import ValorContext, valor_agent


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
        notion_data=notion_data,
        is_priority_question=is_priority_question,
    )

    # Add contextual information to the user message if needed
    enhanced_message = message

    # Include Notion data for priority questions
    if is_priority_question and notion_data and "Error" not in notion_data:
        enhanced_message = (
            f"Context - Current project data:\n{notion_data}\n\nUser question: {message}"
        )

    # Add recent chat context for continuity
    elif chat_history_obj:
        telegram_messages = chat_history_obj.get_context(chat_id)
        if telegram_messages:
            recent_context = telegram_messages[-2:]  # Last 2 messages for context
            if recent_context:
                context_text = "Recent conversation:\n"
                for msg in recent_context:
                    context_text += f"{msg['role']}: {msg['content']}\n"
                enhanced_message = f"{context_text}\n{message}"

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
