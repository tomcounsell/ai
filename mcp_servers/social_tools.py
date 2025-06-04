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
from .context_manager import inject_context_for_tool

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Social Tools")

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
    try:
        # Import here to avoid circular imports
        import subprocess
        import os
        
        # Get workspace context if available
        working_dir = "."
        context_info = ""
        
        if chat_id:
            try:
                from utilities.workspace_validator import get_workspace_validator
                validator = get_workspace_validator()
                workspace_dir = validator.get_working_directory(chat_id)
                if workspace_dir:
                    working_dir = workspace_dir
                    workspace_name = validator.get_workspace_for_chat(chat_id)
                    context_info = f"Workspace: {workspace_name}, Directory: {working_dir}"
            except Exception:
                pass  # Continue with default directory
        
        # Build research-focused prompt for Claude Code
        prompt_parts = [
            f"TECHNICAL RESEARCH TASK: {research_topic}",
            "",
            "RESEARCH OBJECTIVES:",
            "- Conduct comprehensive technical analysis and investigation",
            "- Focus on understanding, not modifying files",
            "- Provide detailed findings with code examples and explanations", 
            "- Explore relevant files, documentation, and patterns",
            "- Research best practices and architectural decisions",
            "",
        ]
        
        if focus_areas:
            prompt_parts.extend([
                f"FOCUS AREAS: {focus_areas}",
                "",
            ])
            
        if context_info:
            prompt_parts.extend([
                f"WORKSPACE CONTEXT: {context_info}",
                "",
            ])
        
        prompt_parts.extend([
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
        
        # Execute Claude Code for research
        if working_dir and working_dir != ".":
            command = f'cd "{working_dir}" && claude code "{full_prompt}"'
            shell = True
        else:
            command = ["claude", "code", full_prompt]
            shell = False
        
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=7200,  # 2 hour timeout for research tasks
            shell=shell
        )
        
        return f"🔬 **Technical Research Results**\n\n{process.stdout}"
        
    except subprocess.TimeoutExpired:
        return f"🔬 **Research Timeout**: Technical analysis of '{research_topic}' exceeded 2 hours. Try breaking down into smaller research questions."
        
    except subprocess.CalledProcessError as e:
        return f"🔬 **Research Error**: Technical analysis failed: {e.stderr or 'Unknown error'}"
        
    except Exception as e:
        return f"🔬 **Research Error**: {str(e)}"


if __name__ == "__main__":
    mcp.run()