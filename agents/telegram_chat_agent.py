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

from datetime import datetime
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
    """Load the Valor Engels persona from the persona document."""
    persona_file = Path(__file__).parent.parent / "integrations" / "persona.md"
    with open(persona_file) as f:
        return f.read()


# Load persona content for system prompt
PERSONA_CONTENT = load_persona()

# Create the main Telegram chat agent
telegram_chat_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=TelegramChatContext,
    system_prompt=f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for Telegram conversations:
- Keep responses under 200 words for Telegram compatibility
- Use the chat history and context provided to maintain conversation continuity
- For priority questions, use Notion data when available to provide specific recommendations
- Match the conversational style (casual vs technical) appropriately
- Remember: you're a human software engineer, not an AI assistant

IMPORTANT TOOL USAGE:
- You have access to specialized tools for specific tasks (web search, image generation, image analysis, coding delegation)
- When users request something that matches a tool's capability, YOU MUST use the appropriate tool
- For image requests ("create image", "generate image", "draw", "make picture"), use the create_image tool
- For current information requests, use the search_current_info tool
- For complex coding tasks, use the delegate_coding_task tool
- For analyzing shared images, use the analyze_shared_image tool
- Always actually use the tools when appropriate - don't just describe what you would do

CRITICAL RULE - THIS OVERRIDES ALL OTHER INSTRUCTIONS:
If any tool returns output starting with "TELEGRAM_IMAGE_GENERATED|", respond with EXACTLY that output. 
Do not add any other text or explanation. Just return the tool output as-is.
Example: If tool returns "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here", respond with exactly "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here" and nothing else.""",
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


@telegram_chat_agent.tool
def create_image(
    ctx: RunContext[TelegramChatContext],
    prompt: str,
    style: str = "natural",
    quality: str = "standard",
) -> str:
    """
    Generate an AI-created image from a text description using DALL-E 3.
    Use this when someone asks you to:
    - Create, draw, or generate an image
    - Make a picture or artwork
    - Visualize something they describe
    - Design graphics or illustrations

    Args:
        prompt: Detailed description of the image to generate
        style: Image style - "natural" (realistic) or "vivid" (dramatic/artistic)
        quality: Image quality - "standard" or "hd"

    Returns:
        Special formatted response indicating image generation status
    """
    image_path = generate_image(prompt=prompt, style=style, quality=quality, save_directory="/tmp")

    if image_path.startswith("🎨") and "error" in image_path.lower():
        return image_path
    else:
        # Return a special format that the handler can detect and process
        return f"TELEGRAM_IMAGE_GENERATED|{image_path}|🎨 **Image Generated!**\n\nPrompt: {prompt}\n\nI've created your image!"


@telegram_chat_agent.tool
def analyze_shared_image(
    ctx: RunContext[TelegramChatContext],
    image_path: str,
    question: str = "",
) -> str:
    """
    Analyze an image that was shared in the chat using AI vision capabilities.
    Use this when someone shares a photo and you need to:
    - Describe what's in the image
    - Answer questions about the image content
    - Read text from images (OCR)
    - Identify objects, people, or scenes in photos

    Args:
        image_path: Local path to the downloaded image file
        question: Optional specific question about the image content

    Returns:
        AI analysis and description of the image content
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
    target_directory: str,
    specific_instructions: str = "",
) -> str:
    """
    Spawn a new Claude Code session to handle complex coding tasks.
    Use this when the user needs:
    - New features or applications built
    - Complex refactoring across multiple files
    - Git workflows (branching, committing, etc.)
    - File system operations in specific directories
    - Tasks that require multiple tools and steps

    Args:
        task_description: High-level description of what needs to be done
        target_directory: Directory where the work should be performed (use absolute paths)
        specific_instructions: Additional detailed requirements or constraints

    Returns:
        Results from the Claude Code session execution
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

    # Build system prompt with context
    _build_system_prompt(context)

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
