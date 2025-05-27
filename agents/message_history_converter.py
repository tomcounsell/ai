"""
Comprehensive message history integration for Telegram chat with PydanticAI.
Handles merging of Telegram chat history with PydanticAI conversation context.
"""

from typing import Any
from datetime import datetime
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart


def merge_telegram_with_pydantic_history(
    telegram_chat_history_obj,
    chat_id: int,
    pydantic_agent_history: list[dict[str, Any]] = None,
    max_context_messages: int = 10,
    deduplicate: bool = True
) -> list[ModelRequest | ModelResponse]:
    """Merge Telegram chat history with PydanticAI agent conversation history.
    
    This function combines existing Telegram chat history from ChatHistoryManager
    with PydanticAI agent's internal conversation history, providing a unified
    conversation context for agent interactions.
    
    The merging process:
    1. Extracts Telegram messages from ChatHistoryManager
    2. Combines with PydanticAI agent's internal history
    3. Sorts chronologically by timestamp
    4. Optionally removes duplicates
    5. Converts to PydanticAI ModelMessage objects
    
    Args:
        telegram_chat_history_obj: The existing ChatHistoryManager instance containing
            Telegram conversation data. Can be None if no history exists.
        chat_id: Telegram chat ID to retrieve history for.
        pydantic_agent_history: Optional list of PydanticAI agent's internal 
            conversation history. Each dict should contain 'role', 'content', 
            and optionally 'timestamp'. Defaults to None.
        max_context_messages: Maximum number of context messages to include 
            in the final merged history. Defaults to 10.
        deduplicate: Whether to remove duplicate messages based on content 
            and role. Defaults to True.
        
    Returns:
        list[ModelRequest | ModelResponse]: Merged message list in chronological 
            order as PydanticAI ModelMessage objects ready for agent consumption.
            
    Examples:
        >>> history_manager = ChatHistoryManager()
        >>> merged = merge_telegram_with_pydantic_history(
        ...     telegram_chat_history_obj=history_manager,
        ...     chat_id=12345,
        ...     max_context_messages=5
        ... )
        >>> len(merged) <= 5
        True
    """
    merged_messages = []
    
    # Get Telegram chat history directly from raw storage to preserve timestamps
    telegram_messages = []
    if telegram_chat_history_obj:
        # Handle real ChatHistoryManager with chat_histories attribute
        if hasattr(telegram_chat_history_obj, 'chat_histories') and chat_id in telegram_chat_history_obj.chat_histories:
            telegram_messages = telegram_chat_history_obj.chat_histories[chat_id]
        # Handle mock objects or other implementations that use get_context
        elif hasattr(telegram_chat_history_obj, 'get_context'):
            telegram_messages = telegram_chat_history_obj.get_context(chat_id) or []
    
    # Convert Telegram messages to standard format with timestamps
    for msg in telegram_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", 0)
        
        if role in ["user", "assistant"] and content:
            merged_messages.append({
                "role": role,
                "content": content,
                "timestamp": timestamp,
                "source": "telegram"
            })
    
    # Add PydanticAI agent history if provided
    if pydantic_agent_history:
        for i, msg in enumerate(pydantic_agent_history):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role in ["user", "assistant"] and content:
                # Use provided timestamp, or interpolate based on position if not provided
                if "timestamp" in msg:
                    timestamp = msg["timestamp"]
                else:
                    # For messages without timestamps, interpolate based on position
                    # This ensures proper chronological ordering when mixed with Telegram messages
                    current_time = datetime.now().timestamp()
                    # Place in sequence based on index, slightly before current time
                    timestamp = current_time - (len(pydantic_agent_history) - i)
                
                merged_messages.append({
                    "role": role,
                    "content": content,
                    "timestamp": timestamp,
                    "source": "pydantic"
                })
    
    # Sort by timestamp to maintain chronological order
    merged_messages.sort(key=lambda x: x.get("timestamp", 0))
    
    # Remove duplicates if requested
    if deduplicate:
        merged_messages = _remove_duplicate_messages(merged_messages)
    
    # Limit to recent messages
    if len(merged_messages) > max_context_messages:
        merged_messages = merged_messages[-max_context_messages:]
    
    # Convert to format expected by PydanticAI (ModelMessage objects)
    final_messages = []
    for msg in merged_messages:
        content = msg["content"]
        # Skip messages with empty content
        if not content or not content.strip():
            continue
            
        if msg["role"] == "user":
            final_messages.append(ModelRequest(parts=[TextPart(content)]))
        elif msg["role"] == "assistant":
            final_messages.append(ModelResponse(parts=[TextPart(content)]))
    
    return final_messages


def _remove_duplicate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate messages based on content and role.
    
    This function identifies and removes duplicate messages while preserving
    the most recent occurrence of each unique message. Duplicates are determined
    by matching both role and content (after stripping whitespace).
    
    Args:
        messages: List of message dictionaries sorted by timestamp. Each message
            should contain 'role', 'content', and 'timestamp' keys.
        
    Returns:
        list[dict[str, Any]]: Deduplicated list of messages in chronological order,
            with only the most recent occurrence of each unique message preserved.
            
    Examples:
        >>> messages = [
        ...     {'role': 'user', 'content': 'Hello', 'timestamp': 1},
        ...     {'role': 'user', 'content': 'Hello', 'timestamp': 2}
        ... ]
        >>> result = _remove_duplicate_messages(messages)
        >>> len(result)
        1
        >>> result[0]['timestamp']
        2
    """
    seen = set()
    deduplicated = []
    
    # Process in reverse order to keep most recent duplicates
    for msg in reversed(messages):
        # Create a key for duplicate detection
        key = (msg["role"], msg["content"].strip())
        
        if key not in seen:
            seen.add(key)
            deduplicated.append(msg)
    
    # Restore chronological order
    return list(reversed(deduplicated))


def integrate_with_existing_telegram_chat(
    telegram_chat_history_obj, chat_id: int, system_prompt: str = None, max_context_messages: int = 6
) -> list[ModelRequest | ModelResponse]:
    """Legacy helper function for backward compatibility with existing code.
    
    This function provides backward compatibility for code that was written
    before the merge_telegram_with_pydantic_history function was introduced.
    New implementations should use merge_telegram_with_pydantic_history directly.
    
    Args:
        telegram_chat_history_obj: The existing ChatHistoryManager instance
            containing Telegram conversation data.
        chat_id: Telegram chat ID to retrieve history for.
        system_prompt: System prompt for the conversation. This parameter
            is kept for compatibility but is unused in the current implementation.
        max_context_messages: Maximum number of context messages to include
            in the returned history. Defaults to 6.

    Returns:
        list[ModelRequest | ModelResponse]: PydanticAI ModelMessage objects
            suitable for use as the message_history parameter in agent calls.
            
    Note:
        This is a legacy function. Use merge_telegram_with_pydantic_history()
        for new implementations as it provides more comprehensive functionality.
        
    Examples:
        >>> history_manager = ChatHistoryManager()
        >>> messages = integrate_with_existing_telegram_chat(
        ...     telegram_chat_history_obj=history_manager,
        ...     chat_id=12345
        ... )
    """
    return merge_telegram_with_pydantic_history(
        telegram_chat_history_obj=telegram_chat_history_obj,
        chat_id=chat_id,
        pydantic_agent_history=None,
        max_context_messages=max_context_messages,
        deduplicate=True
    )
