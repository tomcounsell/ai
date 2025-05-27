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
    """
    Merge Telegram chat history with PydanticAI agent conversation history.
    
    This function combines:
    1. Existing Telegram chat history (from ChatHistoryManager)
    2. PydanticAI agent's internal conversation history
    
    Args:
        telegram_chat_history_obj: The existing ChatHistoryManager instance
        chat_id: Chat ID to get history for
        pydantic_agent_history: PydanticAI agent's internal conversation history
        max_context_messages: Maximum number of context messages to include
        deduplicate: Whether to remove duplicate messages
        
    Returns:
        Merged message list in chronological order as PydanticAI ModelMessage objects
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
    """
    Remove duplicate messages based on content and role.
    Keeps the most recent occurrence of any duplicate.
    
    Args:
        messages: List of messages sorted by timestamp
        
    Returns:
        Deduplicated list of messages
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
    """
    Legacy helper function for backward compatibility.
    Use merge_telegram_with_pydantic_history for new implementations.

    Args:
        telegram_chat_history_obj: The existing ChatHistoryManager instance
        chat_id: Chat ID to get history for
        system_prompt: System prompt for the conversation (unused for compatibility)
        max_context_messages: Maximum number of context messages to include

    Returns:
        PydanticAI ModelMessage objects for message_history parameter
    """
    return merge_telegram_with_pydantic_history(
        telegram_chat_history_obj=telegram_chat_history_obj,
        chat_id=chat_id,
        pydantic_agent_history=None,
        max_context_messages=max_context_messages,
        deduplicate=True
    )
