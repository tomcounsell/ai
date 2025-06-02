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
from tools.valor_delegation_tool import spawn_valor_session
from tools.image_analysis_tool import analyze_image
from tools.image_generation_tool import generate_image
from tools.link_analysis_tool import extract_urls, search_stored_links, store_link_with_analysis
from tools.notion_tool import query_psyoptimal_workspace
from tools.search_tool import search_web
from tools.telegram_history_tool import search_telegram_history, get_telegram_context_summary


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
        intent_result: Optional intent classification result for message optimization.
    """

    chat_id: int | None = None
    username: str | None = None
    is_group_chat: bool = False
    chat_history: list[dict[str, Any]] = []
    chat_history_obj: Any = None  # ChatHistoryManager instance for search tools
    notion_data: str | None = None
    is_priority_question: bool = False
    intent_result: Any = None  # IntentResult instance for message optimization


def load_persona() -> str:
    """Load the Valor Engels persona from the persona document.

    Reads the persona.md file from the agents/valor directory to get the
    complete Valor Engels persona definition for the chat agent.

    Returns:
        str: The complete persona content as a string.

    Raises:
        FileNotFoundError: If the persona.md file cannot be found.
        IOError: If there's an error reading the file.
    """
    persona_file = Path(__file__).parent / "persona.md"
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

DEVELOPMENT TOOLS AVAILABLE (via Claude Code MCP):
Note: These tools are now available through Claude Code's MCP integration and should be used for development workflows.
- Test parameter generation for AI testing scenarios
- Local AI model judging for response evaluation  
- Code linting and formatting (ruff, black, mypy)
- Document summarization and analysis
- Image analysis and tagging with multiple AI providers

CRITICAL RULE - THIS OVERRIDES ALL OTHER INSTRUCTIONS:
If any tool returns output starting with "TELEGRAM_IMAGE_GENERATED|", respond with EXACTLY that output.
Do not add any other text or explanation. Just return the tool output as-is.
Example: If tool returns "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here", respond with exactly "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here" and nothing else.""",
)


@valor_agent.tool
def search_current_info(ctx: RunContext[ValorContext], query: str, max_results: int = 3) -> str:
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

    Common error scenarios:
    - Returns "Search unavailable" if PERPLEXITY_API_KEY is not configured
    - Returns "Search error" for network issues or API problems  
    - Returns "Search timeout" if query takes too long to process

    Args:
        ctx: The runtime context containing conversation information.
        query: The search query to find current information about.
        max_results: Maximum number of results (kept for compatibility, not used by Perplexity).

    Returns:
        str: Current information from web search formatted for conversation, or error message if search fails.

    Example:
        >>> search_current_info(ctx, "Python 3.12 new features")
        '🔍 **Python 3.12 new features**\n\nPython 3.12 includes...'
    """
    # Add input validation
    if not query or not query.strip():
        return "🔍 Search error: Please provide a search query."
    
    if len(query) > 500:
        return "🔍 Search error: Query too long (maximum 500 characters)."
    
    return search_web(query, max_results)


@valor_agent.tool
def create_image(
    ctx: RunContext[ValorContext],
    prompt: str,
    style: str = "natural",
    quality: str = "standard",
    size: str = "1024x1024",
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

    Common error scenarios:
    - Returns "Image generation unavailable" if OPENAI_API_KEY is not configured
    - Returns "Image generation error" for API issues or invalid parameters
    - Returns error for invalid size, style, or quality values

    Args:
        ctx: The runtime context containing chat information.
        prompt: Detailed description of the image to generate.
        style: Image style - "natural" (realistic/photographic) or "vivid" (dramatic/artistic/stylized).
        quality: Image quality - "standard" (faster/cheaper) or "hd" (higher detail/slower).
        size: Image dimensions - "1024x1024" (square), "1792x1024" (landscape), or "1024x1792" (portrait).

    Returns:
        str: Special formatted response starting with "TELEGRAM_IMAGE_GENERATED|"
             followed by the image path and caption, or error message if failed.

    Example:
        >>> create_image(ctx, "a cat wearing a wizard hat", "vivid", "hd", "1024x1024")
        'TELEGRAM_IMAGE_GENERATED|/tmp/generated_cat_wizard.png|🎨 **Image Generated!**...'
    """
    # Add input validation
    if not prompt or not prompt.strip():
        return "🎨 Image generation error: Please provide a description for the image."
    
    if len(prompt) > 1000:
        return "🎨 Image generation error: Description too long (maximum 1000 characters)."
    
    valid_styles = ["natural", "vivid"]
    if style not in valid_styles:
        return f"🎨 Image generation error: Style must be 'natural' or 'vivid'. Got '{style}'."
    
    valid_qualities = ["standard", "hd"]
    if quality not in valid_qualities:
        return f"🎨 Image generation error: Quality must be 'standard' or 'hd'. Got '{quality}'."
    
    valid_sizes = ["1024x1024", "1792x1024", "1024x1792"]
    if size not in valid_sizes:
        return f"🎨 Image generation error: Size must be '1024x1024', '1792x1024', or '1024x1792'. Got '{size}'."
    
    image_path = generate_image(prompt=prompt, style=style, quality=quality, size=size, save_directory="/tmp")

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
    target_directory: str = "",
    specific_instructions: str = "",
) -> str:
    """Provide development guidance and implementation advice for coding tasks.

    This tool provides comprehensive development guidance instead of executing tasks directly.
    It offers detailed implementation approaches, code examples, and best practices to help
    with any software development challenge.

    The tool provides structured guidance including:
    - Step-by-step implementation approaches
    - Relevant code examples and patterns  
    - Testing strategies and best practices
    - Architecture and integration advice

    Use this for ANY development request:
    - "Fix the login bug"
    - "Add a dark mode feature" 
    - "Refactor the API endpoints"
    - "Update dependencies and run tests"
    - "Implement user authentication"

    Simply describe what needs to be done and receive detailed technical guidance.
    For Telegram groups, working directory context is automatically provided.

    Args:
        ctx: The runtime context containing chat information.
        task_description: What needs to be built, fixed, or implemented.
        target_directory: (Optional) Specific directory context. If empty and chat_id provided, uses workspace directory.
        specific_instructions: (Optional) Any additional constraints or preferences.

    Returns:
        str: Comprehensive development guidance with implementation approaches and examples.

    Example:
        >>> delegate_coding_task(ctx, "Fix the authentication bug")
        'Development Guidance Available\\n\\nFor the task: Fix the authentication bug...'
    """
    try:
        # Determine the working directory
        working_dir = target_directory
        
        # If no target directory specified and we have a chat_id, use workspace directory
        if not working_dir and ctx.deps.chat_id:
            from integrations.notion.utils import get_workspace_working_directory, get_dm_working_directory
            
            # Try group workspace directory first
            workspace_dir = get_workspace_working_directory(ctx.deps.chat_id)
            if workspace_dir:
                working_dir = workspace_dir
                print(f"🏢 Using workspace directory for chat {ctx.deps.chat_id}: {working_dir}")
            elif ctx.deps.username and not ctx.deps.is_group_chat:
                # For DMs, use user-specific working directory
                dm_dir = get_dm_working_directory(ctx.deps.username)
                working_dir = dm_dir
                print(f"👤 Using DM working directory for user @{ctx.deps.username}: {working_dir}")
        
        # Fall back to current directory if still not set
        if not working_dir:
            working_dir = "."
            
        result = spawn_valor_session(
            task_description=task_description,
            target_directory=working_dir,
            specific_instructions=specific_instructions if specific_instructions else None,
        )
        return result
    except Exception as e:
        return f"Error providing development guidance: {str(e)}"


@valor_agent.tool
def save_link_for_later(
    ctx: RunContext[ValorContext],
    url: str,
) -> str:
    """Save a link with AI analysis for later reference.

    This tool analyzes and stores URLs shared in chat for future reference.
    It automatically extracts key information like title, main topic, and
    reasons why the content might be valuable. Uses caching to avoid
    re-analyzing previously saved URLs.

    Use this when someone shares a link they want to save, or when you want
    to analyze and store interesting URLs for future reference.

    Args:
        ctx: The runtime context containing chat information.
        url: The URL to analyze and save.

    Returns:
        str: Confirmation message with analysis summary.

    Examples:
        >>> save_link_for_later(ctx, "https://example.com/article")
        '📎 **Link Saved & Analyzed**\n\nTitle: Example Article...'
        
        >>> save_link_for_later(ctx, "Save this: https://github.com/user/repo")
        '📎 **Link saved successfully!**\n\nhttps://github.com/user/repo...'

    Troubleshooting:
        - If analysis fails, the URL is still saved but marked with error status
        - Invalid URLs (missing http/https) will return an error message
        - Previously analyzed URLs are retrieved from cache for faster response
        - If Perplexity API is unavailable, the tool will save URL without analysis
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
    links to find matches based on domain name, URL content, title, or timestamp.

    Use this when someone wants to find links they've shared before or
    when looking for previously saved content on a specific topic.

    Args:
        ctx: The runtime context containing chat information.
        query: Search query (domain name, keyword, or date pattern).
        limit: Maximum number of results to return (default: 10).

    Returns:
        str: Formatted list of matching links with metadata.

    Examples:
        >>> search_saved_links(ctx, "github.com", 5)
        '📂 **Found 3 link(s) matching "github.com":**\n\n• **github.com** (2024-01-15)...'
        
        >>> search_saved_links(ctx, "python tutorial")
        '📂 **Found 2 link(s) matching "python tutorial":**...'
        
        >>> search_saved_links(ctx, "2024-01-15")
        '📂 **Found 5 link(s) matching "2024-01-15":**...'

    Search Tips:
        - Domain searches: "github.com", "stackoverflow.com"
        - Topic searches: "python", "machine learning", "api"
        - Date searches: "2024-01", "2024-01-15"
        - Keyword searches match titles and topics
        - Use specific terms for better results
    """
    return search_stored_links(query, chat_id=ctx.deps.chat_id, limit=limit)


@valor_agent.tool  
def query_notion_projects(
    ctx: RunContext[ValorContext],
    question: str,
) -> str:
    """Query the PsyOPTIMAL workspace for tasks, status, and priorities.

    This tool searches through the PsyOPTIMAL Notion database to answer questions
    about tasks, project status, priorities, and development work using AI-powered
    analysis of the database content.

    Use this when someone asks about:
    - Project status or progress
    - Task priorities or next steps
    - Development work or milestones
    - Specific project information
    - What tasks need attention
    - Current workload and capacity

    Args:
        ctx: The runtime context containing chat information.
        question: The question about projects or tasks.

    Returns:
        str: AI-generated analysis of PsyOPTIMAL database with specific task details.

    Example:
        >>> query_notion_projects(ctx, "What tasks are ready for dev?")
        '🎯 **PsyOPTIMAL Status**\n\nFound 3 tasks ready for development...'
    """
    try:
        # Query the PsyOPTIMAL workspace using the unified notion tool
        result = query_psyoptimal_workspace(question)
        return result
    except Exception as e:
        return f"❌ Error querying PsyOPTIMAL workspace: {str(e)}\n\nPlease ensure your Notion API integration is properly configured."


@valor_agent.tool
def search_conversation_history(
    ctx: RunContext[ValorContext],
    search_query: str,
    max_results: int = 5,
) -> str:
    """Search through Telegram conversation history for specific information.

    This tool searches through the full message history of the current chat to find
    relevant previous conversations, references, or information that might not be
    in the immediate recent context. Use this when you need to find specific topics,
    links, or discussions that happened earlier.

    Use this when:
    - User references something from a previous conversation ("that link I sent", "what we discussed yesterday")
    - You need to find specific information mentioned before
    - Looking for previous decisions, recommendations, or solutions
    - Finding context about ongoing projects or topics

    Args:
        ctx: The runtime context containing chat information and history manager.
        search_query: Keywords or terms to search for in message history.
        max_results: Maximum number of relevant messages to return (default 5).

    Returns:
        str: Formatted list of relevant historical messages or "No matches found".

    Example:
        >>> search_conversation_history(ctx, "authentication API", 3)
        'Found 2 relevant message(s) for "authentication API":...'
    """
    if not ctx.deps.chat_history_obj or not ctx.deps.chat_id:
        return "No chat history available for search"
    
    return search_telegram_history(
        query=search_query,
        chat_history_obj=ctx.deps.chat_history_obj,
        chat_id=ctx.deps.chat_id,
        max_results=max_results
    )


@valor_agent.tool
def get_conversation_context(
    ctx: RunContext[ValorContext],
    hours_back: int = 24,
) -> str:
    """Get extended conversation context and summary.

    This tool provides a broader view of the recent conversation beyond just
    the last few messages. Use this when you need to understand the overall
    flow and context of the conversation to provide better responses.

    Use this when:
    - You need more context to understand what's being discussed
    - The conversation seems to reference earlier topics
    - You want to see the full recent conversation flow
    - Understanding the broader context would help provide better assistance

    Args:
        ctx: The runtime context containing chat information and history manager.
        hours_back: How many hours of conversation history to summarize (default 24).

    Returns:
        str: Formatted conversation summary or "No recent activity".

    Example:
        >>> get_conversation_context(ctx, 12)
        'Conversation summary (last 12 hours, 8 messages):...'
    """
    if not ctx.deps.chat_history_obj or not ctx.deps.chat_id:
        return "No chat history available"
    
    return get_telegram_context_summary(
        chat_history_obj=ctx.deps.chat_history_obj,
        chat_id=ctx.deps.chat_id,
        hours_back=hours_back
    )


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
        if not result or not hasattr(result, 'output'):
            return "Error: Agent returned invalid result"
        return result.output
    except Exception as e:
        return f"Error processing request: {str(e)}"
