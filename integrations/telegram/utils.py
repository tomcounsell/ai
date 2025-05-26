"""Utility functions for Telegram integration."""

import time
from typing import List


MAX_MESSAGE_AGE_SECONDS = 300  # Only respond to messages newer than 5 minutes


def is_message_too_old(message_timestamp: int) -> bool:
    """Check if a message is too old to respond to (for catch-up handling)."""
    current_time = time.time()
    message_age = current_time - message_timestamp
    return message_age > MAX_MESSAGE_AGE_SECONDS


def is_notion_question(text: str) -> bool:
    """Detect if a message is asking about Notion."""
    notion_keywords = [
        'notion', 'task', 'project', 'database', 'milestone', 'status', 
        'priority', 'deadline', 'due', 'todo', 'progress', 'development',
        'psyoptimal', 'flextrip', 'psy', 'flex'
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in notion_keywords)


def is_user_priority_question(text: str) -> bool:
    """Detect if a message is asking about user's work priorities or next tasks."""
    priority_patterns = [
        'what should i work on',
        'what am i working on', 
        'what are you working on',
        'what will you work on',
        'what should you work on',
        'what\'s next',
        'whats next',
        'next priority',
        'next task',
        'upcoming work',
        'work on next',
        'priorities',
        'roadmap'
    ]
    text_lower = text.lower()
    return any(pattern in text_lower for pattern in priority_patterns)


async def generate_catchup_response(missed_messages: List[str], anthropic_client) -> str:
    """Generate a brief response to summarize missed messages."""
    if not missed_messages or not anthropic_client:
        return "Hi! I'm back and ready to help with any questions."
    
    # Get the most recent messages (last 3) for context
    recent_messages = missed_messages[-3:]
    messages_text = "\n".join([f"- {msg}" for msg in recent_messages])
    
    system_prompt = """You are a technical assistant who was temporarily offline. A user sent messages while you were away. Generate a VERY brief (1-2 sentences max) acknowledgment that:
1. Acknowledges you missed their messages
2. Offers to help with their most recent question/topic
3. Is friendly but concise

DO NOT try to answer the questions in detail - just acknowledge and offer to help."""
    
    try:
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=150,
            temperature=0.7,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"I sent these messages while you were offline:\n{messages_text}"}
            ]
        )
        
        return response.content[0].text
        
    except Exception as e:
        return "Hi! I'm back and caught up on your messages. How can I help?"