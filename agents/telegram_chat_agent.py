#!/usr/bin/env python3
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic-ai",
#   "openai",
#   "python-dotenv",
#   "anthropic"
# ]
# ///

PydanticAI chat agent for Telegram integration.
Replaces direct Anthropic API calls with structured agent approach.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import tools and converters
from tools.search_tool import search_web
from agents.message_history_converter import integrate_with_existing_telegram_chat


class TelegramChatContext(BaseModel):
    """Context for Telegram chat interactions."""
    chat_id: int
    username: Optional[str] = None
    is_group_chat: bool = False
    chat_history: List[Dict[str, Any]] = []
    notion_data: Optional[str] = None
    is_priority_question: bool = False


def load_persona() -> str:
    """Load the Valor Engels persona from the persona document."""
    persona_file = Path(__file__).parent.parent / "integrations" / "persona.md"
    with open(persona_file, 'r') as f:
        return f.read()


# Load persona content for system prompt
PERSONA_CONTENT = load_persona()

# Create the main Telegram chat agent
telegram_chat_agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
    deps_type=TelegramChatContext,
    system_prompt=f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for Telegram conversations:
- Keep responses under 200 words for Telegram compatibility
- Use the chat history and context provided to maintain conversation continuity
- For priority questions, use Notion data when available to provide specific recommendations
- Match the conversational style (casual vs technical) appropriately
- Remember: you're a human software engineer, not an AI assistant"""
)


@telegram_chat_agent.tool
def search_current_info(ctx: RunContext[TelegramChatContext], query: str) -> str:
    """
    Search for current information on the web using Perplexity AI.
    Use this when you need up-to-date information about:
    - Current events, news, or recent developments
    - Latest technology trends or releases
    - Current market conditions or company information
    - Recent research or publications
    - Any information that might have changed recently

    Args:
        query: The search query to find current information about

    Returns:
        Current information from web search formatted for conversation
    """
    return search_web(query)


async def handle_telegram_message(
    message: str,
    chat_id: int,
    username: Optional[str] = None,
    is_group_chat: bool = False,
    chat_history_obj = None,
    notion_data: Optional[str] = None,
    is_priority_question: bool = False
) -> str:
    """
    Handle a Telegram message using the PydanticAI agent with proper message history.

    Args:
        message: The user's message
        chat_id: Telegram chat ID
        username: Optional username
        is_group_chat: Whether this is a group chat
        chat_history_obj: ChatHistoryManager instance for conversation history
        notion_data: Optional Notion project data for priority questions
        is_priority_question: Whether this is asking about work priorities

    Returns:
        Agent response as string
    """

    # Prepare context
    context = TelegramChatContext(
        chat_id=chat_id,
        username=username,
        is_group_chat=is_group_chat,
        chat_history=[],  # We'll use PydanticAI message history instead
        notion_data=notion_data,
        is_priority_question=is_priority_question
    )

    # Build system prompt with context
    system_prompt = _build_system_prompt(context)

    # Add recent chat context to the message for continuity
    enhanced_message = message
    if chat_history_obj:
        telegram_messages = chat_history_obj.get_context(chat_id)
        if telegram_messages:
            # Include recent context in the message itself
            recent_context = telegram_messages[-3:]  # Last 3 messages
            context_text = "Recent conversation context:\n"
            for msg in recent_context:
                context_text += f"{msg['role']}: {msg['content']}\n"
            enhanced_message = f"{context_text}\nCurrent message: {message}"

    # Run the agent (PydanticAI will manage its own message history)
    result = await telegram_chat_agent.run(enhanced_message, deps=context)

    return result.output


def _build_system_prompt(context: TelegramChatContext) -> str:
    """Build system prompt with context for the agent."""

    # Base persona content
    base_prompt = f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for Telegram conversations:
- Keep responses under 400 words for Telegram compatibility
- For priority questions, use Notion data when available to provide specific recommendations
- Match the conversational style (casual vs technical) appropriately
- Remember: you're a human software engineer, not an AI assistant"""

    # Add priority question context
    if context.is_priority_question and context.notion_data:
        if "Error" not in context.notion_data:
            base_prompt += f"\n\nCurrent project priorities:\n{context.notion_data}"

    # Add environment context
    current_time = datetime.now()
    env_context = f"""

Environment: Telegram chat (chat_id: {context.chat_id})
Time: {current_time.strftime('%Y-%m-%d %H:%M')}
Group chat: {context.is_group_chat}
Priority question: {context.is_priority_question}"""

    return base_prompt + env_context


# Convenience functions for backward compatibility with existing handlers

async def handle_user_priority_question(
    question: str,
    chat_id: int,
    chat_history_obj,
    notion_scout=None,
    username: Optional[str] = None
) -> str:
    """
    Handle user priority questions using PydanticAI agent with message history.
    """

    # Check if there's project context in recent conversation
    context_has_project_info = False
    if chat_history_obj:
        context_messages = chat_history_obj.get_context(chat_id)
        for msg in context_messages[-5:]:
            if any(keyword in msg['content'].lower() for keyword in ['project', 'task', 'working on', 'psyoptimal', 'flextrip']):
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
        is_priority_question=True
    )


async def handle_general_question(
    question: str,
    chat_id: int,
    chat_history_obj,
    username: Optional[str] = None
) -> str:
    """
    Handle general questions using PydanticAI agent with message history.
    """

    return await handle_telegram_message(
        message=question,
        chat_id=chat_id,
        username=username,
        chat_history_obj=chat_history_obj,
        is_priority_question=False
    )


# Test function
if __name__ == "__main__":
    import asyncio

    async def test():
        test_response = await handle_telegram_message(
            message="How's it going?",
            chat_id=12345,
            username="test_user"
        )
        print(f"Test response: {test_response}")

    asyncio.run(test())
