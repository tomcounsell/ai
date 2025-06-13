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

REACTION SYSTEM:
- You can add emoji reactions to messages by including REACTION:emoji at the end of your response
- Use reactions to acknowledge messages, show approval, or express sentiment
- Examples: REACTION:👍 REACTION:❤️ REACTION:🔥 REACTION:🎉 REACTION:🤔 REACTION:😁
- Available reactions: 👍 👎 ❤️ 🔥 🎉 😁 😮 😢 🤔 👏
- Use reactions appropriately based on message content and context
- Multiple reactions can be used: REACTION:👍 REACTION:🔥

IMPORTANT TOOL USAGE:
- You have access to specialized tools for specific tasks (web search, image generation, image analysis, coding delegation, link saving, notion queries, documentation reading)
- When users request something that matches a tool's capability, YOU MUST use the appropriate tool
- For image requests ("create image", "generate image", "draw", "make picture"), use the create_image tool
- For current information requests, use the search_current_info tool
- For coding tasks, use the delegate_coding_task tool IMMEDIATELY - this tool actually executes code:
  * CRITICAL: Execute first, respond after - call the tool and wait for results
  * Don't make promises ("I'll fix...") - do the work then report completion
  * The tool spawns Claude Code to actually write code, run tests, and commit changes
  * Always specify target_directory and include detailed requirements in specific_instructions
  * Report actual results from the execution, not plans or intentions
- For analyzing shared images, use the analyze_shared_image tool
- For saving/analyzing links, use the save_link_for_later tool
- For searching saved links, use the search_saved_links tool
- For project/task questions, use the query_notion_projects tool
- For documentation questions, request that Claude Code summarize project documentation using MCP development tools
- Always actually use the tools when appropriate - don't just describe what you would do

DEVELOPMENT TOOLS AVAILABLE (via Claude Code MCP):
Note: These tools are now available through Claude Code's MCP integration and should be used for development workflows.
- Test parameter generation for AI testing scenarios
- Local AI model judging for response evaluation  
- Code linting and formatting (ruff, black, mypy)
- Document summarization and analysis
- Image analysis and tagging with multiple AI providers
- Claude Code session management for development workflow continuity

CRITICAL RULE - THIS OVERRIDES ALL OTHER INSTRUCTIONS:
If any tool returns output starting with "TELEGRAM_IMAGE_GENERATED|", respond with EXACTLY that output.
Do not add any other text or explanation. Just return the tool output as-is.
Example: If tool returns "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here", respond with exactly "TELEGRAM_IMAGE_GENERATED|/path/image.png|Caption here" and nothing else.""",
)


def _execute_with_session_management(
    prompt: str,
    working_directory: str,
    existing_session,
    chat_id: str,
    username: str,
    tool_name: str,
    task_description: str,
    specific_instructions: str = None
) -> str:
    """Execute Claude Code with session management capabilities.
    
    This helper function handles session continuity, command building, and session storage
    for both delegate_coding_task and technical_analysis tools.
    
    Args:
        prompt: The prompt/task description to send to Claude Code
        working_directory: Directory to run Claude Code in
        existing_session: Previous session to continue (if any)
        chat_id: Telegram chat ID for session tracking
        username: Username for session tracking
        tool_name: Name of the calling tool
        task_description: Original task description
        specific_instructions: Additional instructions (for coding tasks)
        
    Returns:
        Claude Code output with session information
    """
    import subprocess
    import os
    from utilities.claude_code_session_manager import ClaudeCodeSessionManager
    
    # Get workspace-specific prime content if this is a new session
    prime_content = ""
    if not existing_session:
        prime_content = ClaudeCodeSessionManager.load_workspace_prime_content(working_directory)
        if not prime_content:
            # Fallback to generic project context if no workspace-specific prime found
            try:
                from mcp_servers.development_tools import get_project_context
                prime_content = get_project_context(chat_id)
            except Exception:
                pass
    
    # Build comprehensive prompt with project priming for new sessions
    prompt_parts = [
        f"TASK: {task_description}",
        "",
        f"WORKING DIRECTORY: {working_directory}",
        ""
    ]
    
    # Add workspace-specific prime content for new sessions
    if prime_content and not existing_session:
        prompt_parts.extend([
            "WORKSPACE PRIME CONTEXT (/prime equivalent):",
            prime_content,
            "",
            "---",
            ""
        ])
    
    if specific_instructions:
        prompt_parts.extend([f"SPECIFIC INSTRUCTIONS: {specific_instructions}", ""])
    
    if existing_session:
        prompt_parts.extend([
            f"CONTINUING SESSION: {existing_session.session_id[:8]}...",
            f"Previous work: {existing_session.initial_task}",
            f"Tasks completed: {existing_session.task_count}",
            ""
        ])
        
        # Update session activity
        ClaudeCodeSessionManager.update_session_activity(
            existing_session.session_id, 
            task_description
        )
    
    if tool_name == "delegate_coding_task":
        prompt_parts.extend([
            "REQUIREMENTS:",
            "- Follow existing code patterns and conventions",
            "- Ensure all changes are properly tested if tests exist",
            "- Use appropriate git workflow (branch, commit, etc.)",
            "- Provide clear commit messages",
            "- Handle errors gracefully",
            "",
            "Execute this task autonomously and report results."
        ])
    else:  # technical_analysis
        prompt_parts.extend([
            "RESEARCH OBJECTIVES:",
            "- Conduct comprehensive technical analysis and investigation",
            "- Focus on understanding, not modifying files",
            "- Provide detailed findings with code examples and explanations",
            "- Explore relevant files, documentation, and patterns",
            "- Research best practices and architectural decisions",
            "",
            "RESEARCH GUIDELINES:",
            "- Use Read, Glob, Grep, and other analysis tools extensively",
            "- Do NOT edit, write, or modify any files",
            "- Focus on understanding and explaining what exists",
            "- Provide code examples and architectural insights",
            "- Research industry standards and best practices",
            "- Explain your findings clearly with technical depth",
            "",
            "Conduct this technical research thoroughly and provide comprehensive analysis."
        ])
    
    full_prompt = "\\n".join(prompt_parts)
    
    # Build Claude Code command with session management
    if existing_session:
        command = ClaudeCodeSessionManager.build_session_command(
            full_prompt, 
            session_id=existing_session.session_id,
            should_continue=True
        )
    else:
        command = ClaudeCodeSessionManager.build_session_command(full_prompt)
    
    # Execute Claude Code
    if working_directory and working_directory != ".":
        command = f'cd "{working_directory}" && {command}'
        shell = True
    else:
        shell = False
    
    timeout = 7200 if tool_name == "technical_analysis" else 3600  # 2h for research, 1h for coding
    
    try:
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell
        )
        
        # Extract and store session ID from output
        session_id = ClaudeCodeSessionManager.extract_session_id_from_output(process.stdout)
        
        if session_id and not existing_session:
            # Store new session
            ClaudeCodeSessionManager.store_session(
                session_id=session_id,
                chat_id=chat_id,
                username=username,
                tool_name=tool_name,
                working_directory=working_directory,
                task_description=task_description,
                metadata={"specific_instructions": specific_instructions} if specific_instructions else None
            )
            print(f"📋 Created new Claude Code session: {session_id[:8]}...")
        elif existing_session:
            print(f"📋 Continued session: {existing_session.session_id[:8]}...")
        
        # Format response with tool-specific prefix
        prefix = "🔬 **Technical Research Results**" if tool_name == "technical_analysis" else "⚙️ **Development Task Results**"
        return f"{prefix}\\n\\n{process.stdout}"
        
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(command, timeout, f"Claude Code execution timed out after {timeout} seconds")
    except subprocess.CalledProcessError as e:
        error_msg = f"Claude Code failed with exit code {e.returncode}\\n"
        if e.stdout:
            error_msg += f"STDOUT: {e.stdout}\\n"
        if e.stderr:
            error_msg += f"STDERR: {e.stderr}"
        raise subprocess.CalledProcessError(e.returncode, command, error_msg)


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
    """Execute development tasks autonomously using Claude Code sessions.

    This tool spawns a new Claude Code session to actually execute coding tasks rather than 
    just providing guidance. It performs real work including writing code, running tests, 
    making git commits, and providing detailed results.

    The tool executes tasks including:
    - Writing and modifying code files
    - Running tests and fixing failures
    - Git operations (commit, branch management)
    - Dependency management and builds
    - Code refactoring and optimization

    Use this for ANY development work that needs to be actually completed:
    - "Fix the login bug" - Will actually fix the bug and commit changes
    - "Add a dark mode feature" - Will implement the feature with tests
    - "Refactor the API endpoints" - Will perform refactoring with validation
    - "Update dependencies and run tests" - Will update deps and ensure tests pass
    - "Implement user authentication" - Will build complete auth system

    The tool operates in the correct workspace directory and follows project conventions.
    For Telegram groups, working directory context is automatically determined.

    Args:
        ctx: The runtime context containing chat information.
        task_description: What needs to be built, fixed, or implemented.
        target_directory: (Optional) Specific directory context. If empty and chat_id provided, uses workspace directory.
        specific_instructions: (Optional) Any additional constraints or preferences.

    Returns:
        str: Detailed execution results including changes made, tests run, and git status.

    Example:
        >>> delegate_coding_task(ctx, "Fix the authentication bug")
        '✅ **Task Completed Successfully**\\n\\nTask: Fix authentication bug...'
    """
    import time
    start_time = time.time()
    
    try:
        # Use unified workspace resolution
        from utilities.workspace_validator import WorkspaceResolver
        from utilities.swe_error_recovery import SWEErrorRecovery
        from utilities.claude_code_session_manager import ClaudeCodeSessionManager
        
        # Get chat context for session management
        chat_id = str(ctx.deps.chat_id) if ctx.deps.chat_id else None
        username = ctx.deps.username
        
        working_dir, context_desc = WorkspaceResolver.resolve_working_directory(
            chat_id=chat_id,
            username=username,
            is_group_chat=ctx.deps.is_group_chat,
            target_directory=target_directory
        )
        
        print(f"📁 Workspace resolved: {context_desc}")
        print(f"🎯 Working directory: {working_dir}")
        
        # Check for recent session to continue
        recent_session = ClaudeCodeSessionManager.find_recent_session(
            chat_id=chat_id,
            username=username,
            tool_name="delegate_coding_task",
            working_directory=working_dir,
            hours_back=2  # Look for sessions in last 2 hours
        )
        
        # Execute with session management
        result = _execute_with_session_management(
            prompt=task_description,
            working_directory=working_dir,
            existing_session=recent_session,
            chat_id=chat_id,
            username=username,
            tool_name="delegate_coding_task",
            task_description=task_description,
            specific_instructions=specific_instructions
        )
        
        return result
        
    except Exception as e:
        # Use intelligent error recovery
        error_message = str(e)
        execution_time = time.time() - start_time
        recovery_response = SWEErrorRecovery.format_recovery_response(
            tool_name="delegate_coding_task",
            task_description=task_description,
            error_message=error_message,
            working_directory=working_dir if 'working_dir' in locals() else ".",
            execution_time=execution_time
        )
        return recovery_response


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
    Typical response time is under 100ms for database queries.

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
    """Query the workspace associated with this chat for tasks, status, and priorities.

    This tool searches through the appropriate Notion database based on the chat's 
    workspace mapping to answer questions about tasks, project status, priorities, 
    and development work using AI-powered analysis of the database content.

    WORKSPACE ISOLATION: This tool automatically determines the correct workspace
    based on the chat context and enforces strict access controls.

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
        str: AI-generated analysis of the authorized workspace database with specific task details.

    Example:
        >>> query_notion_projects(ctx, "What tasks are ready for dev?")
        '🎯 **Project Status**\n\nFound 3 tasks ready for development...'
    """
    try:
        # Use workspace-aware MCP tool with proper access validation
        # Revolutionary living project context - replaced query_notion_projects
        from mcp_servers.pm_tools import get_development_context as mcp_get_context
        from utilities.workspace_validator import get_workspace_validator
        
        # Get chat context
        chat_id = str(ctx.deps.chat_id) if ctx.deps.chat_id else ""
        
        # Determine workspace for this chat
        if not chat_id:
            return "❌ Unable to determine workspace: No chat context available"
        
        validator = get_workspace_validator()
        workspace_name = validator.get_workspace_for_chat(chat_id)
        
        if not workspace_name:
            return "❌ Access denied: This chat is not mapped to any workspace"
        
        # Get living project context for the workspace
        result = mcp_get_context(workspace_name, chat_id)
        return result
    except Exception as e:
        error_str = str(e)
        
        # Handle specific error types with user-friendly messages
        if "Connection" in error_str or "connection" in error_str:
            return "❌ Connection error: Cannot reach Notion API. Check internet connection."
        elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
            return "❌ Timeout error: Notion query took too long. Try a simpler question."
        elif "NOTION_API_KEY" in error_str or "api key" in error_str.lower():
            return "❌ Authentication error: Check NOTION_API_KEY configuration."
        elif "ANTHROPIC_API_KEY" in error_str or "anthropic" in error_str.lower():
            return "❌ AI Analysis error: Check ANTHROPIC_API_KEY configuration."
        elif "Unknown workspace" in error_str:
            return f"❌ Workspace error: {error_str}"
        else:
            # Log unexpected errors for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Unexpected notion query error: {error_str}")
            return f"❌ Error querying PsyOPTIMAL workspace: {error_str}\n\nPlease ensure your Notion API integration is properly configured."


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
    - The conversation seems to reference earlier topics (e.g., "like we discussed before")
    - You want to see the full recent conversation flow to maintain continuity
    - Understanding the broader context would help provide better assistance
    - User asks about recent conversation history ("what were we talking about earlier?")
    - You need to understand the evolution of a discussion over several hours

    Args:
        ctx: The runtime context containing chat information and history manager.
        hours_back: How many hours of conversation history to summarize (1-168, default 24).

    Returns:
        str: Formatted conversation summary with timestamps and roles,
             or "No recent activity" if no messages found.

    Example:
        >>> get_conversation_context(ctx, 12)
        'Conversation summary (last 12 hours, 8 messages):
         1. user: Started discussing the new project requirements...
         2. assistant: I can help you break down those requirements...'
    """
    # Validate parameters
    if hours_back < 1 or hours_back > 168:  # 1 hour to 1 week
        return "❌ hours_back must be between 1 and 168 hours (1 week)"
    
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
