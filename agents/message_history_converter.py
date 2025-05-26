"""
Simple message history integration for Telegram chat with PydanticAI.
"""

from typing import List, Dict, Any, Optional


def integrate_with_existing_telegram_chat(
    telegram_chat_history_obj,
    chat_id: int,
    system_prompt: str,
    max_context_messages: int = 6
) -> List[Dict[str, str]]:
    """
    Helper function to get recent Telegram chat context for PydanticAI.
    
    Args:
        telegram_chat_history_obj: The existing ChatHistoryManager instance
        chat_id: Chat ID to get history for
        system_prompt: System prompt for the conversation
        max_context_messages: Maximum number of context messages to include
        
    Returns:
        Simple message list for PydanticAI message_history parameter
    """
    # Get telegram chat history
    telegram_messages = telegram_chat_history_obj.get_context(chat_id)
    
    # Convert to simple format that PydanticAI can use
    simple_messages = []
    
    # Take recent messages only
    recent_messages = telegram_messages[-max_context_messages:] if telegram_messages else []
    
    # Convert to simple user/assistant format
    for msg in recent_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role in ["user", "assistant"] and content:
            simple_messages.append({
                "role": role,
                "content": content
            })
    
    return simple_messages