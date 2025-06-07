#!/usr/bin/env python3
"""
Social Tools MCP Server

Provides web search, image generation, and link analysis tools for Claude Code integration.
This server follows the GOLD STANDARD wrapper pattern by importing functions from 
standalone tools and adding MCP-specific concerns (context injection, validation).

ARCHITECTURE: MCP Wrapper → Standalone Implementation
- search_current_info → tools/search_tool.py
- create_image → tools/image_generation_tool.py  
- analyze_shared_image → tools/image_analysis_tool.py
- save_link → tools/link_analysis_tool.py
- search_links → tools/link_analysis_tool.py
- technical_analysis → Unique Claude Code delegation approach
"""

import os
from typing import Dict, Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Import standalone tool implementations following GOLD STANDARD pattern
from tools.search_tool import search_web
from tools.image_generation_tool import generate_image
from tools.image_analysis_tool import analyze_image as analyze_image_impl
from tools.link_analysis_tool import (
    store_link_with_analysis,
    search_stored_links
)
from tools.voice_transcription_tool import transcribe_audio_file

# Import context manager for MCP context injection
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from context_manager import inject_context_for_tool

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Social Tools")

# Default emoji mappings for each MCP tool
# Using only valid Telegram reaction emojis from our validated set of 72
MCP_TOOL_EMOJIS = {
    "search_current_info": "🗿",      # moai - stone face, based, solid info
    "create_image": "🎉",             # party popper - let's gooo, celebration mode
    "analyze_shared_image": "🤩",     # star eyes - shook, amazing, mind blown, obsessed
    "save_link": "🍾",               # champagne - we poppin bottles, saved successfully
    "search_links": "🔥",            # fire - that's fire, lit search results
    "transcribe_voice_message": "✍", # writing hand - taking notes, documenting
    "technical_analysis": "🤓",      # nerd - big brain time, technical deep dive
    "manage_claude_code_sessions": "👨‍💻", # technologist - coding time, tech management
    "show_workspace_prime_content": "💯"  # 100 - facts, complete info, real talk
}

# Reserved status emojis for system-wide use
# These should be used consistently across all tools for status indicators
STATUS_EMOJIS = {
    "done": "🫡",      # saluting - yes chief, copy that, respect, task completed
    "error": "🥴",     # woozy - drunk thoughts, confused, lost the plot, error state
    "read_receipt": "👀"  # eyes - I see you, watching this, acknowledged/seen
}

@mcp.tool()
def search_current_info(query: str, max_results: int = 3) -> str:
    """Search the web and return AI-synthesized answers using Perplexity.
    
    Use this tool when you need current web information or recent news about any topic.
    Provides AI-synthesized answers based on current web content.

    Args:
        query: The search query to execute
        max_results: Maximum number of results (not used with Perplexity, kept for compatibility)

    Returns:
        AI-synthesized answer based on current web information
    """
    try:
        # Call standalone implementation following GOLD STANDARD pattern
        return search_web(query, max_results)
    except Exception as e:
        return f"🔍 Search error: {str(e)}"


@mcp.tool()
def create_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "natural",
    chat_id: str = ""
) -> str:
    """Generate an image using DALL-E 3 and save it locally.
    
    Use this tool when you need to create custom images from text descriptions.
    Generated images are saved locally and can be shared in conversations.

    Args:
        prompt: Text description of the image to generate
        size: Image size - "1024x1024", "1792x1024", or "1024x1792"
        quality: Image quality - "standard" or "hd"
        style: Image style - "natural" (realistic) or "vivid" (dramatic/artistic)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)

    Returns:
        Path to the generated image file or error message
    """
    try:
        # Inject context if not provided
        chat_id, _ = inject_context_for_tool(chat_id, "")
        
        # Call standalone implementation following GOLD STANDARD pattern
        image_path = generate_image(prompt, size, quality, style, save_directory=None)
        
        # Handle error cases (standalone function returns error messages starting with emoji)
        if image_path.startswith("🎨"):
            return image_path
            
        # Format response for Telegram if chat_id provided (MCP-specific feature)
        if chat_id:
            return f"TELEGRAM_IMAGE_GENERATED|{image_path}|{chat_id}"
        else:
            return image_path
            
    except Exception as e:
        return f"🎨 Image generation error: {str(e)}"


@mcp.tool()
def analyze_shared_image(
    image_path: str,
    question: str = "",
    chat_id: str = ""
) -> str:
    """Analyze an image using AI vision capabilities.
    
    Use this tool to analyze images and answer questions about visual content.
    Supports OCR, object recognition, and scene analysis using GPT-4o vision.

    Args:
        image_path: Path to the image file to analyze
        question: Optional specific question about the image content
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)

    Returns:
        AI analysis of the image content formatted for conversation
    """
    try:
        # Call standalone implementation following GOLD STANDARD pattern
        # Map parameters: question (optional), chat_id as context (optional)
        context = chat_id if chat_id else None
        question_param = question if question and question.strip() else None
        
        return analyze_image_impl(image_path, question_param, context)
        
    except Exception as e:
        return f"👁️ Image analysis error: {str(e)}"



@mcp.tool()
def save_link(url: str, chat_id: str = "", username: str = "") -> str:
    """Save a link with AI-generated analysis to the knowledge base.
    
    Use this tool when a user shares a URL that should be saved for future reference.
    Automatically analyzes the content and stores structured metadata.

    Args:
        url: The URL to analyze and save
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)
        username: Username for context (extracted from CONTEXT_DATA if available)

    Returns:
        Success message with analysis summary or error message
    """
    try:
        # Inject context if not provided
        chat_id, username = inject_context_for_tool(chat_id, username)
        
        # Call standalone implementation following GOLD STANDARD pattern
        # Convert chat_id to int if provided, handle optional parameters
        chat_id_int = int(chat_id) if chat_id and chat_id.isdigit() else None
        username_param = username if username else None
        
        # Call standalone function - returns bool
        success = store_link_with_analysis(url, chat_id_int, None, username_param)
        
        if success:
            # Parse URL for domain to create user-friendly response
            parsed = urlparse(url)
            domain = parsed.netloc or "Unknown"
            return f"🔗 **Link Saved**: {domain}\n\n✅ Successfully stored with AI analysis"
        else:
            return f"❌ Failed to save link: {url}"
            
    except Exception as e:
        return f"🔗 Link save error: {str(e)}"


@mcp.tool()
def search_links(query: str, chat_id: str = "", limit: int = 10) -> str:
    """Search through previously saved links by domain, content, or timestamp.
    
    Use this tool to find links that were previously saved to the knowledge base.
    Searches through domains, URLs, and timestamps.

    Args:
        query: Search query (domain name, URL content, or date pattern)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)
        limit: Maximum number of results to return

    Returns:
        Formatted list of matching links or message indicating no matches
    """
    try:
        # Inject context if not provided
        chat_id, _ = inject_context_for_tool(chat_id, "")
        
        # Call standalone implementation following GOLD STANDARD pattern
        # Convert chat_id to int if provided, handle optional parameters
        chat_id_int = int(chat_id) if chat_id and chat_id.isdigit() else None
        
        # Call standalone function - returns formatted string
        return search_stored_links(query, chat_id_int, limit)
        
    except Exception as e:
        return f"📂 Link search error: {str(e)}"


@mcp.tool()
def transcribe_voice_message(
    file_path: str,
    language: str = "",
    cleanup_file: bool = False,
    chat_id: str = ""
) -> str:
    """Transcribe an audio or voice file to text using OpenAI Whisper API.
    
    Use this tool when you need to convert voice messages, audio recordings, or any
    audio file to text. Supports multiple languages and provides high-quality transcription.

    Args:
        file_path: Path to the audio/voice file to transcribe (supports OGG, MP3, WAV, MP4, etc.)
        language: Optional language code for better accuracy (e.g., "en", "es", "fr", "de")
        cleanup_file: Whether to delete the audio file after transcription (useful for temp files)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)

    Returns:
        Transcribed text from the audio file, or error message if transcription fails
    """
    try:
        # Call standalone implementation following GOLD STANDARD pattern
        language_param = language if language and language.strip() else None
        
        result = transcribe_audio_file(file_path, language_param, cleanup_file)
        
        # Format for chat context if available
        if chat_id and result:
            return f"🎙️ **Voice Transcription**\n\n{result}"
        else:
            return result
            
    except FileNotFoundError:
        return f"🎙️ Audio file not found: {file_path}"
    except Exception as e:
        return f"🎙️ Voice transcription error: {str(e)}"


@mcp.tool()
def technical_analysis(
    research_topic: str, 
    focus_areas: str = "", 
    chat_id: str = ""
) -> str:
    """Perform comprehensive technical research and analysis using Claude Code.
    
    This tool delegates complex technical research tasks to Claude Code, which excels at:
    - Exploring codebases and understanding architectures
    - Analyzing technical documentation and specifications
    - Researching industry best practices and patterns
    - Investigating technologies, frameworks, and tools
    - Comparing different approaches and solutions
    - Reading and analyzing files across projects
    
    Unlike delegate_coding_task which focuses on implementation, this tool is optimized
    for research, analysis, and investigation tasks where you need comprehensive 
    technical insights without modifying files.
    
    Args:
        research_topic: The technical topic or question to research and analyze
        focus_areas: Optional specific areas to focus on (e.g., "performance, security, scalability")
        chat_id: Chat ID for workspace context (extracted from CONTEXT_DATA if available)
    
    Returns:
        Comprehensive technical analysis and research findings
    
    Examples:
        >>> technical_analysis("How does the authentication system work in this codebase?")
        >>> technical_analysis("Compare different image compression approaches", "performance, quality")
        >>> technical_analysis("What are the current API endpoints and their purposes?")
    """
    import time
    start_time = time.time()
    
    try:
        # Import here to avoid circular imports
        import subprocess
        import os
        
        # Use unified workspace resolution and session management
        from utilities.workspace_validator import WorkspaceResolver
        from utilities.claude_code_session_manager import ClaudeCodeSessionManager
        from .context_manager import inject_context_for_tool
        
        # Inject context if not provided
        chat_id, username = inject_context_for_tool(chat_id, "")
        
        working_dir, context_desc = WorkspaceResolver.resolve_working_directory(
            chat_id=chat_id,
            username=username,
            is_group_chat=True,  # Assume group context for technical analysis
            target_directory=""
        )
        
        # Check for recent session to continue
        recent_session = ClaudeCodeSessionManager.find_recent_session(
            chat_id=chat_id,
            username=username,
            tool_name="technical_analysis",
            working_directory=working_dir,
            hours_back=2  # Look for sessions in last 2 hours
        )
        
        # Get workspace-specific prime content if this is a new session
        prime_content = ""
        if not recent_session:
            # Import here to avoid circular imports
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.dirname(__file__)))
            from utilities.claude_code_session_manager import ClaudeCodeSessionManager
            
            prime_content = ClaudeCodeSessionManager.load_workspace_prime_content(working_dir)
            if not prime_content:
                # Fallback to generic project context if no workspace-specific prime found
                try:
                    from mcp_servers.development_tools import get_project_context
                    prime_content = get_project_context(chat_id)
                except Exception:
                    pass
        
        # Build research-focused prompt for Claude Code with session context
        prompt_parts = [
            f"TECHNICAL RESEARCH TASK: {research_topic}",
            "",
            f"WORKING DIRECTORY: {working_dir}",
            ""
        ]
        
        # Add workspace-specific prime content for new sessions
        if prime_content and not recent_session:
            prompt_parts.extend([
                "WORKSPACE PRIME CONTEXT (/prime equivalent):",
                prime_content,
                "",
                "---",
                ""
            ])
        
        if focus_areas:
            prompt_parts.extend([f"FOCUS AREAS: {focus_areas}", ""])
            
        if context_desc:
            prompt_parts.extend([f"WORKSPACE CONTEXT: {context_desc}", ""])
        
        if recent_session:
            prompt_parts.extend([
                f"CONTINUING SESSION: {recent_session.session_id[:8]}...",
                f"Previous research: {recent_session.initial_task}",
                f"Tasks completed: {recent_session.task_count}",
                ""
            ])
            
            # Update session activity
            ClaudeCodeSessionManager.update_session_activity(
                recent_session.session_id, 
                research_topic
            )
        
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
        
        full_prompt = "\n".join(prompt_parts)
        
        # Build Claude Code command with session management
        if recent_session:
            command = ClaudeCodeSessionManager.build_session_command(
                full_prompt, 
                session_id=recent_session.session_id,
                should_continue=True
            )
        else:
            command = ClaudeCodeSessionManager.build_session_command(full_prompt)
        
        # Execute Claude Code for research
        if working_dir and working_dir != ".":
            command = f'cd "{working_dir}" && {command}'
            shell = True
        else:
            shell = False
        
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=7200,  # 2 hour timeout for research tasks
            shell=shell
        )
        
        # Extract and store session ID from output
        session_id = ClaudeCodeSessionManager.extract_session_id_from_output(process.stdout)
        
        if session_id and not recent_session:
            # Store new session
            ClaudeCodeSessionManager.store_session(
                session_id=session_id,
                chat_id=chat_id,
                username=username,
                tool_name="technical_analysis",
                working_directory=working_dir,
                task_description=research_topic,
                metadata={"focus_areas": focus_areas} if focus_areas else None
            )
            print(f"🔬 Created new Claude Code research session: {session_id[:8]}...")
        elif recent_session:
            print(f"🔬 Continued research session: {recent_session.session_id[:8]}...")
        
        return f"🔬 **Technical Research Results**\n\n{process.stdout}"
        
    except subprocess.TimeoutExpired:
        from utilities.swe_error_recovery import SWEErrorRecovery
        execution_time = time.time() - start_time
        return SWEErrorRecovery.format_recovery_response(
            tool_name="technical_analysis",
            task_description=research_topic,
            error_message="Research exceeded 2 hour timeout",
            working_directory=working_dir if 'working_dir' in locals() else ".",
            execution_time=execution_time
        )
        
    except subprocess.CalledProcessError as e:
        from utilities.swe_error_recovery import SWEErrorRecovery
        execution_time = time.time() - start_time
        error_msg = f"Claude Code failed (exit {e.returncode}): {e.stderr or 'Unknown error'}"
        return SWEErrorRecovery.format_recovery_response(
            tool_name="technical_analysis", 
            task_description=research_topic,
            error_message=error_msg,
            working_directory=working_dir if 'working_dir' in locals() else ".",
            execution_time=execution_time
        )
        
    except Exception as e:
        from utilities.swe_error_recovery import SWEErrorRecovery
        execution_time = time.time() - start_time
        return SWEErrorRecovery.format_recovery_response(
            tool_name="technical_analysis",
            task_description=research_topic, 
            error_message=str(e),
            working_directory=working_dir if 'working_dir' in locals() else ".",
            execution_time=execution_time
        )


@mcp.tool()
def manage_claude_code_sessions(
    action: str = "list",
    session_id: str = "",
    chat_id: str = ""
) -> str:
    """Manage Claude Code sessions for the current chat.
    
    This tool allows users to view, continue, or deactivate their Claude Code sessions
    for better workflow continuity and session management.
    
    Actions:
    - "list": Show all active sessions for the current chat
    - "show": Show details for a specific session (requires session_id)
    - "deactivate": Mark a session as inactive (requires session_id)
    - "continue": Get instructions to continue a specific session (requires session_id)
    
    Args:
        action: The action to perform ("list", "show", "deactivate", "continue")
        session_id: Session ID for actions that require it (show, deactivate, continue)
        chat_id: Chat ID for context (extracted from CONTEXT_DATA if available)
    
    Returns:
        Formatted session information or action results
    
    Examples:
        >>> manage_claude_code_sessions("list")
        📋 **Active Claude Code Sessions**...
        
        >>> manage_claude_code_sessions("show", "a1b2c3d4")
        📋 **Session Details**...
    """
    try:
        from utilities.claude_code_session_manager import ClaudeCodeSessionManager
        from .context_manager import inject_context_for_tool
        
        # Inject context if not provided
        chat_id, username = inject_context_for_tool(chat_id, "")
        
        if action == "list":
            sessions = ClaudeCodeSessionManager.get_chat_sessions(
                chat_id=chat_id,
                limit=10,
                active_only=True
            )
            
            if not sessions:
                return "📋 **No Active Sessions**\n\nNo active Claude Code sessions found for this chat."
            
            response_parts = ["📋 **Active Claude Code Sessions**\n"]
            
            for i, session in enumerate(sessions, 1):
                session_summary = ClaudeCodeSessionManager.format_session_summary(session)
                response_parts.append(f"{i}. {session_summary}")
            
            response_parts.extend([
                "",
                "💡 **Usage Tips:**",
                "• Use `manage_claude_code_sessions(\"show\", \"session_id\")` for details",
                "• Sessions automatically continue when you use the same tool in the same directory",
                "• Sessions expire after 24 hours of inactivity"
            ])
            
            return "\n".join(response_parts)
        
        elif action == "show":
            if not session_id:
                return "❌ Session ID required for 'show' action. Use format: manage_claude_code_sessions(\"show\", \"session_id\")"
            
            # Find sessions and filter for the requested one
            sessions = ClaudeCodeSessionManager.get_chat_sessions(chat_id, limit=50, active_only=False)
            target_session = None
            
            for session in sessions:
                if session.session_id.startswith(session_id) or session.session_id == session_id:
                    target_session = session
                    break
            
            if not target_session:
                return f"❌ Session not found: {session_id}"
            
            metadata = target_session.session_metadata
            focus_areas = metadata.get("focus_areas", "") if metadata else ""
            specific_instructions = metadata.get("specific_instructions", "") if metadata else ""
            
            details = [
                f"📋 **Session Details: {target_session.session_id[:8]}...**\n",
                f"**Tool:** {target_session.tool_name}",
                f"**Working Directory:** {target_session.working_directory}",
                f"**Initial Task:** {target_session.initial_task}",
                f"**Tasks Completed:** {target_session.task_count}",
                f"**Created:** {target_session.created_at.strftime('%Y-%m-%d %H:%M')}",
                f"**Last Activity:** {target_session.last_activity.strftime('%Y-%m-%d %H:%M')}",
                f"**Status:** {'🟢 Active' if target_session.is_active else '🔴 Inactive'}"
            ]
            
            if focus_areas:
                details.append(f"**Focus Areas:** {focus_areas}")
            if specific_instructions:
                details.append(f"**Instructions:** {specific_instructions}")
            
            return "\n".join(details)
        
        elif action == "deactivate":
            if not session_id:
                return "❌ Session ID required for 'deactivate' action. Use format: manage_claude_code_sessions(\"deactivate\", \"session_id\")"
            
            # Find the session first to verify ownership
            sessions = ClaudeCodeSessionManager.get_chat_sessions(chat_id, limit=50, active_only=True)
            target_session = None
            
            for session in sessions:
                if session.session_id.startswith(session_id) or session.session_id == session_id:
                    target_session = session
                    break
            
            if not target_session:
                return f"❌ Active session not found: {session_id}"
            
            success = ClaudeCodeSessionManager.deactivate_session(target_session.session_id)
            
            if success:
                return f"✅ **Session Deactivated**\n\nSession {target_session.session_id[:8]}... has been marked as inactive."
            else:
                return f"❌ Failed to deactivate session {session_id}"
        
        elif action == "continue":
            if not session_id:
                return "❌ Session ID required for 'continue' action. Use format: manage_claude_code_sessions(\"continue\", \"session_id\")"
            
            # Find the session
            sessions = ClaudeCodeSessionManager.get_chat_sessions(chat_id, limit=50, active_only=True)
            target_session = None
            
            for session in sessions:
                if session.session_id.startswith(session_id) or session.session_id == session_id:
                    target_session = session
                    break
            
            if not target_session:
                return f"❌ Active session not found: {session_id}"
            
            return f"""🔄 **Continue Session: {target_session.session_id[:8]}...**

**To continue this session**, simply use the same tool (`{target_session.tool_name}`) with a follow-up task in the same workspace.

**Session Context:**
• Tool: {target_session.tool_name}
• Directory: {target_session.working_directory}
• Previous work: {target_session.initial_task}
• Tasks completed: {target_session.task_count}

The system will automatically detect and continue this session when you:
1. Use `{target_session.tool_name}` again
2. In the same working directory: `{target_session.working_directory}`
3. Within the next 2 hours

**Example follow-up:**
"Continue the previous work by adding tests for the new features" """
        
        else:
            return f"❌ Unknown action: {action}. Valid actions: list, show, deactivate, continue"
    
    except Exception as e:
        return f"📋 Session management error: {str(e)}"


@mcp.tool()
def show_workspace_prime_content(
    working_directory: str = "",
    chat_id: str = ""
) -> str:
    """Show the workspace-specific /prime command content that will be used for new Claude Code sessions.
    
    This tool displays the prime.md content from the .claude/commands/ directory of the
    specified workspace, which automatically primes new Claude Code sessions with 
    project-specific context and commands.
    
    Args:
        working_directory: Specific directory to check (optional, will resolve from chat if empty)
        chat_id: Chat ID for workspace context (extracted from CONTEXT_DATA if available)
    
    Returns:
        The prime content that would be used for Claude Code sessions, or info if not found
    
    Examples:
        >>> show_workspace_prime_content()
        📋 **Workspace Prime Content**...
        
        >>> show_workspace_prime_content("/Users/valorengels/src/psyoptimal")
        📋 **PsyOPTIMAL Prime Content**...
    """
    try:
        from utilities.claude_code_session_manager import ClaudeCodeSessionManager
        from utilities.workspace_validator import WorkspaceResolver
        from .context_manager import inject_context_for_tool
        
        # Inject context if not provided
        chat_id, username = inject_context_for_tool(chat_id, "")
        
        # Resolve working directory if not provided
        if not working_directory:
            working_directory, context_desc = WorkspaceResolver.resolve_working_directory(
                chat_id=chat_id,
                username=username,
                is_group_chat=True,
                target_directory=""
            )
        
        # Load prime content
        prime_content = ClaudeCodeSessionManager.load_workspace_prime_content(working_directory)
        
        if prime_content:
            # Extract title from first line if it exists
            lines = prime_content.split('\n')
            title = lines[0].replace('#', '').strip() if lines and lines[0].startswith('#') else "Prime Content"
            
            return f"""📋 **Workspace Prime Content**

**Directory**: {working_directory}
**Prime File**: {working_directory}/.claude/commands/prime.md
**Title**: {title}
**Length**: {len(prime_content)} characters

**Content Preview** (first 500 chars):
```
{prime_content[:500]}{'...' if len(prime_content) > 500 else ''}
```

💡 **Note**: This content is automatically included in new Claude Code sessions for this workspace to provide project-specific context and guidance."""
        
        else:
            return f"""📋 **No Workspace Prime Content Found**

**Directory**: {working_directory}
**Expected Location**: {working_directory}/.claude/commands/prime.md

❌ No prime.md file found in this workspace's .claude/commands/ directory.

💡 **Note**: Without workspace-specific prime content, new Claude Code sessions will use generic project context as fallback."""
    
    except Exception as e:
        return f"📋 Error loading workspace prime content: {str(e)}"


if __name__ == "__main__":
    mcp.run()