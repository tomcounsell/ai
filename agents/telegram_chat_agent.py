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

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

# Load environment variables
load_dotenv()

# Import tools and converters
from tools.claude_code_tool import spawn_claude_session
from tools.image_analysis_tool import analyze_image
from tools.image_generation_tool import generate_image
from tools.search_tool import search_web


class TelegramChatContext(BaseModel):
    """Context for Telegram chat interactions."""

    chat_id: int
    username: str | None = None
    is_group_chat: bool = False
    chat_history: list[dict[str, Any]] = []
    notion_data: str | None = None
    is_priority_question: bool = False


def load_persona() -> str:
    """Load the Valor Engels persona."""
    persona_file = Path(__file__).parent.parent / "integrations" / "persona.md"
    with open(persona_file) as f:
        return f.read()


# Load persona content for system prompt
PERSONA_CONTENT = load_persona()

# Create the main Telegram chat agent with streamlined system prompt
telegram_chat_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=TelegramChatContext,
    system_prompt=PERSONA_CONTENT,
)


@telegram_chat_agent.tool
def search_current_info(ctx: RunContext[TelegramChatContext], query: str) -> str:
    """
    Search the web for current information using Perplexity AI.
    Use when someone asks about recent events, technology updates, or current conditions.

    Examples: "What's new with React?", "Latest news about OpenAI", "Current Python trends"
    """
    return search_web(query)


@telegram_chat_agent.tool
def create_image(
    ctx: RunContext[TelegramChatContext],
    prompt: str,
    style: str = "natural",
    quality: str = "standard",
) -> str:
    """
    Generate an image using DALL-E 3.
    Use when someone asks to create, draw, or visualize something.

    Examples: "Draw a cat", "Create a logo", "Make an image of..."
    """
    image_path = generate_image(prompt=prompt, style=style, quality=quality, save_directory="/tmp")

    if image_path.startswith("🎨") and "error" in image_path.lower():
        return image_path
    else:
        return f"🎨 **Image Generated!**\n\nPrompt: {prompt}\nSaved to: {image_path}\n\nI've created your image! The file is ready for you to view."


@telegram_chat_agent.tool
def analyze_shared_image(
    ctx: RunContext[TelegramChatContext],
    image_path: str,
    question: str = "",
) -> str:
    """
    Analyze images shared in chat using AI vision.
    Use when someone shares a photo and asks about it or wants it described.

    Examples: "What's in this image?", "Read the text", "Describe this photo"
    """
    # Get recent chat context for more relevant analysis
    chat_context = None
    if ctx.deps.chat_history:
        recent_messages = ctx.deps.chat_history[-3:]
        chat_context = " ".join([msg.get("content", "") for msg in recent_messages])

    return analyze_image(
        image_path=image_path, question=question if question else None, context=chat_context
    )


@telegram_chat_agent.tool
def delegate_coding_task(
    ctx: RunContext[TelegramChatContext],
    task_description: str,
    target_directory: str = ".",
    specific_instructions: str = "",
) -> str:
    """
    Handle coding tasks using Claude Code.
    Use for implementation requests: building features, fixing bugs, refactoring code.

    Examples: "Build a login page", "Fix the database error", "Add user authentication"
    """
    try:
        result = spawn_claude_session(
            task_description=task_description,
            target_directory=target_directory,
            specific_instructions=specific_instructions if specific_instructions else None,
        )
        return f"Claude Code session completed successfully:\n\n{result}"
    except Exception as e:
        return f"Error executing Claude Code session: {str(e)}"


async def handle_telegram_message(
    message: str,
    chat_id: int,
    username: str | None = None,
    is_group_chat: bool = False,
    chat_history_obj=None,
    notion_data: str | None = None,
    is_priority_question: bool = False,
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
    result = await telegram_chat_agent.run(enhanced_message, deps=context)

    return result.output


# Removed _build_system_prompt - using streamlined approach with context in user message


# Convenience functions for backward compatibility with existing handlers


async def handle_user_priority_question(
    question: str, chat_id: int, chat_history_obj, notion_scout=None, username: str | None = None
) -> str:
    """
    Handle user priority questions using PydanticAI agent with message history.
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
    """
    Handle general questions using PydanticAI agent with message history.
    """

    return await handle_telegram_message(
        message=question,
        chat_id=chat_id,
        username=username,
        chat_history_obj=chat_history_obj,
        is_priority_question=False,
    )


# Test function
if __name__ == "__main__":
    import asyncio

    async def test():
        test_response = await handle_telegram_message(
            message="How's it going?", chat_id=12345, username="test_user"
        )
        print(f"Test response: {test_response}")

    asyncio.run(test())
