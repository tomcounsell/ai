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

Unified PydanticAI agent for Valor Engels persona with comprehensive tool integration.
This agent handles both standalone interactions and Telegram chat integration.
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
from tools.link_analysis_tool import extract_urls, search_stored_links, store_link_with_analysis
from tools.search_tool import search_web


class ValorContext(BaseModel):
    """Context for Valor Engels agent interactions.

    This unified context class supports both standalone agent usage and
    Telegram chat integration, providing all necessary context information
    for conversations across different interaction modes.

    Attributes:
        chat_id: Optional unique identifier for the chat session.
        username: Optional username of the person chatting.
        is_group_chat: Whether this is a group chat or direct conversation.
        chat_history: List of previous chat messages for context.
        notion_data: Optional Notion project data for priority questions.
        is_priority_question: Whether this message is asking about work priorities.
    """

    chat_id: int | None = None
    username: str | None = None
    is_group_chat: bool = False
    chat_history: list[dict[str, Any]] = []
    notion_data: str | None = None
    is_priority_question: bool = False


def load_persona() -> str:
    """Load the Valor Engels persona from the persona document.

    Reads the persona.md file from the integrations directory to get the
    complete Valor Engels persona definition for the chat agent.

    Returns:
        str: The complete persona content as a string.

    Raises:
        FileNotFoundError: If the persona.md file cannot be found.
        IOError: If there's an error reading the file.
    """
    persona_file = Path(__file__).parent.parent / "integrations" / "persona.md"
    with open(persona_file) as f:
        return f.read()


# Load persona content for system prompt
PERSONA_CONTENT = load_persona()

# Create the unified Valor Engels agent with comprehensive tool suite
valor_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=ValorContext,
    system_prompt=f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for conversations:
- Keep responses under 200 words for Telegram compatibility when chat_id is provided
- Use the chat history and context provided to maintain conversation continuity
- For priority questions, use Notion data when available to provide specific recommendations
- Match the conversational style (casual vs technical) appropriately
- Remember: you're a human software engineer, not an AI assistant

IMPORTANT TOOL USAGE:
- You have access to specialized tools for specific tasks (web search, image generation, image analysis, coding delegation, link saving, notion queries)
- When users request something that matches a tool's capability, YOU MUST use the appropriate tool
- For image requests ("create image", "generate image", "draw", "make picture"), use the create_image tool
- For current information requests, use the search_current_info tool
- For complex coding tasks, use the delegate_coding_task tool with structured prompts:
  * For complex features: Use PLANNING PHASE template first, then IMPLEMENTATION PHASE template
  * For simple tasks: Use direct task description
  * Always specify target_directory and include detailed requirements in specific_instructions
- For analyzing shared images, use the analyze_shared_image tool
- For saving/analyzing links, use the save_link_for_later tool
- For searching saved links, use the search_saved_links tool
- For project/task questions, use the query_notion_projects tool
- Always actually use the tools when appropriate - don't just describe what you would do

CRITICAL RULE - THIS OVERRIDES ALL OTHER INSTRUCTIONS:
If any tool returns output starting with "TELEGRAM_IMAGE_GENERATED|", respond with EXACTLY that output.
Do not add any other text or explanation. Just return the tool output as-is.
Example: If tool returns "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here", respond with exactly "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here" and nothing else.""",
)


@valor_agent.tool
def search_current_info(ctx: RunContext[ValorContext], query: str) -> str:
    """Search for current information on the web using Perplexity AI.

    This tool enables the Valor agent to access up-to-date information from
    the web when answering questions about current events, trends, or recent
    developments that may not be in the agent's training data.

    Use this when you need up-to-date information about:
    - Current events, news, or recent developments
    - Latest technology trends or releases
    - Current market conditions or company information
    - Recent research or publications
    - Any information that might have changed recently

    Args:
        ctx: The runtime context containing conversation information.
        query: The search query to find current information about.

    Returns:
        str: Current information from web search formatted for conversation.

    Example:
        >>> search_current_info(ctx, "Python 3.12 new features")
        '🔍 **Python 3.12 new features**\n\nPython 3.12 includes...'
    """
    return search_web(query)


@valor_agent.tool
def create_image(
    ctx: RunContext[ValorContext],
    prompt: str,
    style: str = "natural",
    quality: str = "standard",
) -> str:
    """Generate an AI-created image from a text description using DALL-E 3.

    This tool creates custom images based on user descriptions and returns them
    in a format that can be sent through Telegram. The generated images are
    saved locally and the path is returned for transmission.

    Use this when someone asks you to:
    - Create, draw, or generate an image
    - Make a picture or artwork
    - Visualize something they describe
    - Design graphics or illustrations

    Args:
        ctx: The runtime context containing chat information.
        prompt: Detailed description of the image to generate.
        style: Image style - "natural" (realistic) or "vivid" (dramatic/artistic).
        quality: Image quality - "standard" or "hd".

    Returns:
        str: Special formatted response starting with "TELEGRAM_IMAGE_GENERATED|"
             followed by the image path and caption, or error message if failed.

    Example:
        >>> create_image(ctx, "a cat wearing a wizard hat", "vivid", "hd")
        'TELEGRAM_IMAGE_GENERATED|/tmp/generated_cat_wizard.png|🎨 **Image Generated!**...'
    """
    image_path = generate_image(prompt=prompt, style=style, quality=quality, save_directory="/tmp")

    if image_path.startswith("🎨") and "error" in image_path.lower():
        return image_path
    else:
        # Return a special format that the handler can detect and process
        return f"TELEGRAM_IMAGE_GENERATED|{image_path}|🎨 **Image Generated!**\n\nPrompt: {prompt}\n\nI've created your image!"


@valor_agent.tool
def analyze_shared_image(
    ctx: RunContext[ValorContext],
    image_path: str,
    question: str = "",
) -> str:
    """Analyze an image that was shared in the chat using AI vision capabilities.

    This tool processes images shared in Telegram chats and provides AI-powered
    analysis using vision-capable models. It can understand image content and
    answer specific questions about what's shown.

    Use this when someone shares a photo and you need to:
    - Describe what's in the image
    - Answer questions about the image content
    - Read text from images (OCR)
    - Identify objects, people, or scenes in photos

    Args:
        ctx: The runtime context containing chat information and history.
        image_path: Local path to the downloaded image file.
        question: Optional specific question about the image content.

    Returns:
        str: AI analysis and description of the image content, formatted
             for conversation display.

    Example:
        >>> analyze_shared_image(ctx, "/tmp/photo.jpg", "What's in this image?")
        '👁️ **Image Analysis**\n\nI can see a sunset over mountains...'
    """
    # Get recent chat context for more relevant analysis
    chat_context = None
    if ctx.deps.chat_history:
        recent_messages = ctx.deps.chat_history[-3:]
        chat_context = " ".join([msg.get("content", "") for msg in recent_messages])

    return analyze_image(
        image_path=image_path, question=question if question else None, context=chat_context
    )


@valor_agent.tool
def delegate_coding_task(
    ctx: RunContext[ValorContext],
    task_description: str,
    target_directory: str = ".",
    specific_instructions: str = "",
) -> str:
    """Spawn a new Claude Code session to handle complex coding tasks.

    This tool creates a new Claude Code session with specialized coding capabilities
    to handle complex development tasks that require multiple steps, file operations,
    or git workflows. It's designed for tasks beyond simple conversation.

    Use this when the user needs:
    - New features or applications built
    - Complex refactoring across multiple files
    - Git workflows (branching, committing, etc.)
    - File system operations in specific directories
    - Tasks that require multiple tools and steps

    For complex features, use these structured prompt templates:

    PLANNING PHASE TEMPLATE:
    "Investigate and create a detailed implementation plan for: [feature/change name]

    Process:
    1. Analyze the codebase to understand current architecture and patterns
    2. Research requirements and identify dependencies
    3. Design the implementation approach with consideration for:
       - Existing code patterns and conventions
       - Required tests and test scenarios
       - Potential edge cases and error handling
       - Integration points and side effects
    4. Create comprehensive plan with step-by-step implementation details
    5. Save complete reasoning, analysis, and plan to /docs/plan/[name].md

    Include in the plan document:
    - Requirements analysis
    - Current state assessment
    - Proposed solution architecture
    - Detailed implementation steps
    - Test scenarios and coverage requirements
    - Potential risks and mitigation strategies
    - Success criteria

    Feature/Change: [your specific request here]"

    IMPLEMENTATION PHASE TEMPLATE:
    "Implement the plan documented in /docs/plan/[name].md using TDD approach:

    Process:
    1. Read and understand the complete plan from /docs/plan/[name].md
    2. Create todos based on the planned implementation steps
    3. Follow strict TDD: write tests → verify they fail → implement → make tests pass → refactor → commit
    4. If you discover issues requiring plan adjustments, update /docs/plan/[name].md first
    5. Mark todos complete in real-time as you progress
    6. Achieve 100% test coverage before marking feature complete

    Execute step-by-step with live progress updates.

    Plan file: /docs/plan/[name].md"

    Args:
        ctx: The runtime context containing conversation information.
        task_description: High-level description of what needs to be done.
        target_directory: Directory where the work should be performed (use absolute paths).
        specific_instructions: Additional detailed requirements or constraints.

    Returns:
        str: Results from the Claude Code session execution, including any
             files created, modified, or error messages if the session failed.

    Example:
        >>> delegate_coding_task(ctx, "Create a CLI tool", "/tmp", "Use Python")
        'Claude Code session completed successfully:\n\nCreated new CLI application...'
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


@valor_agent.tool
def save_link_for_later(
    ctx: RunContext[ValorContext],
    url: str,
) -> str:
    """Save a link with AI analysis for later reference.

    This tool analyzes and stores URLs shared in chat for future reference.
    It automatically extracts key information like title, main topic, and
    reasons why the content might be valuable.

    Use this when someone shares a link they want to save, or when you want
    to analyze and store interesting URLs for future reference.

    Args:
        ctx: The runtime context containing chat information.
        url: The URL to analyze and save.

    Returns:
        str: Confirmation message with analysis summary.

    Example:
        >>> save_link_for_later(ctx, "https://example.com/article")
        '📎 **Link Saved & Analyzed**\n\nTitle: Example Article...'
    """
    # Extract URLs if the input contains more than just the URL
    urls = extract_urls(url)
    if urls:
        url = urls[0]  # Use the first URL found

    success = store_link_with_analysis(url)

    if success:
        return f"📎 **Link saved successfully!**\n\n{url}\n\nI've analyzed and stored this link for future reference."
    else:
        return f"❌ **Error saving link**: Could not analyze or store {url}"


@valor_agent.tool
def search_saved_links(
    ctx: RunContext[ValorContext],
    query: str,
    limit: int = 10,
) -> str:
    """Search through previously saved links.

    This tool searches through the collection of previously analyzed and saved
    links to find matches based on domain name, URL content, or timestamp.

    Use this when someone wants to find links they've shared before or
    when looking for previously saved content on a specific topic.

    Args:
        ctx: The runtime context containing chat information.
        query: Search query (domain name, keyword, or date pattern).
        limit: Maximum number of results to return (default: 10).

    Returns:
        str: Formatted list of matching links with metadata.

    Example:
        >>> search_saved_links(ctx, "github.com", 5)
        '📂 **Found 3 link(s) matching "github.com":**\n\n• **github.com** (2024-01-15)...'
    """
    return search_stored_links(query, chat_id=ctx.deps.chat_id, limit=limit)


@valor_agent.tool
def query_notion_projects(
    ctx: RunContext[ValorContext],
    question: str,
    project_filter: str = "",
) -> str:
    """Query Notion project databases for tasks, status, and priorities.

    This tool searches through Notion project databases to answer questions
    about tasks, project status, priorities, and development work. It can
    filter by specific projects like PsyOPTIMAL or FlexTrip.

    Use this when someone asks about:
    - Project status or progress
    - Task priorities or next steps
    - Development work or milestones
    - Specific project information

    Args:
        ctx: The runtime context containing chat information.
        question: The question about projects or tasks.
        project_filter: Optional project name to filter results (psy, optimal, flex, trip).

    Returns:
        str: Notion database query results with task and project information.

    Example:
        >>> query_notion_projects(ctx, "What tasks are ready for dev?", "psy")
        '🎯 **Project Status**\n\nFound 3 tasks ready for development...'
    """
    # This integrates with the existing Notion functionality
    # The notion_data will be passed through the enhanced_message in handle_telegram_message
    if ctx.deps.notion_data:
        return f"🎯 **Notion Project Data**\n\n{ctx.deps.notion_data}"
    else:
        return "🔍 I can help with Notion queries, but I need the Notion integration configured. Try asking about 'project status' or 'tasks ready for dev' and I'll search your databases."


async def run_valor_agent(message: str, context: ValorContext | None = None) -> str:
    """Run the Valor agent with a message and optional context.

    This is the main entry point for interacting with the unified Valor
    Engels agent. It processes user messages and returns responses using the
    agent's available tools and persona.

    Args:
        message: User message to process.
        context: Optional context about the conversation.

    Returns:
        str: Agent response as string.

    Raises:
        Exception: If there's an error processing the request.

    Example:
        >>> response = await run_valor_agent("Hello, how are you?")
        >>> type(response)
        <class 'str'>
    """
    if context is None:
        context = ValorContext()

    try:
        result = await valor_agent.run(message, deps=context)
        return result.data
    except Exception as e:
        return f"Error processing request: {str(e)}"


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


# Test function and example usage
if __name__ == "__main__":
    import asyncio
    import sys

    # Install uvloop for better async performance
    if sys.platform != "win32":  # uvloop is not available on Windows
        try:
            import uvloop

            uvloop.install()
            print("🚀 uvloop installed for improved async performance")
        except ImportError:
            print("⚠️  uvloop not available, using default asyncio event loop")

    async def test_valor_agent():
        """Test the unified Valor agent with various types of queries.

        This function runs a series of test cases to validate that the Valor
        agent is working correctly with different types of queries including
        general questions, tool usage, and Telegram integration.

        The test cases cover:
        - Technical advice questions
        - Web search functionality
        - Complex coding task delegation
        - Telegram-style interactions

        Raises:
            Exception: If any test case fails unexpectedly.
        """

        test_cases = [
            # Standalone usage
            "How should I structure a FastAPI project for production?",
            "What are the latest trends in AI development?",
            # Telegram simulation
            (
                "Hey Valor, what's the latest news about Python?",
                {"chat_id": 12345, "username": "test_user"},
            ),
            (
                "Create a simple CLI tool in /tmp using TypeScript",
                {"chat_id": 12345, "username": "test_user"},
            ),
        ]

        print("🤖 Testing Unified Valor Engels Agent with Comprehensive Tools")
        print("=" * 70)

        for i, test_case in enumerate(test_cases, 1):
            if isinstance(test_case, tuple):
                query, context_data = test_case
                context = ValorContext(**context_data)
                print(f"\n{i}. Telegram Query: {query}")
            else:
                query = test_case
                context = ValorContext()
                print(f"\n{i}. Standalone Query: {query}")

            print("-" * 50)

            try:
                response = await run_valor_agent(query, context)
                print(f"Valor: {response}")
            except Exception as e:
                print(f"Error: {e}")

            if i < len(test_cases):
                print("\n" + "=" * 70)

    # Test telegram message handler
    async def test_telegram_integration():
        """Test the Telegram message handling functionality."""

        print("\n🔗 Testing Telegram Integration")
        print("=" * 70)

        response = await handle_telegram_message(
            message="How's it going?", chat_id=12345, username="test_user"
        )
        print(f"Telegram Test Response: {response}")

    # Only run test if executed directly
    try:
        asyncio.run(test_valor_agent())
        asyncio.run(test_telegram_integration())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    except Exception as e:
        print(f"\nTest failed: {e}")
