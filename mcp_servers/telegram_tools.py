#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Tools MCP Server

Provides Telegram conversation history search and context tools for Claude Code integration.
This server follows the GOLD STANDARD wrapper pattern by importing functions from 
standalone tools and adding MCP-specific concerns (context injection, validation).

ARCHITECTURE: MCP Wrapper → Standalone Implementation
- search_conversation_history → tools/telegram_history_tool.py
- get_conversation_context → tools/telegram_history_tool.py
- get_recent_history → Unique MCP implementation (UNIQUE)
- list_telegram_dialogs → Unique MCP implementation (UNIQUE)
"""

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Import standalone tool implementations following GOLD STANDARD pattern
from tools.telegram_history_tool import (
    search_telegram_history,
    get_telegram_context_summary
)

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Telegram Tools")


@mcp.tool()
def search_conversation_history(query: str, chat_id: str = "", max_results: int = 5) -> str:
    """Search through Telegram message history for relevant context.
    
    Use this tool when you need to find specific information from previous conversations
    that might not be in the immediate recent context. Searches through message content
    for keywords and returns relevant historical messages.

    Args:
        query: Search terms or keywords to find in message history
        chat_id: Chat ID to search (extracted from CONTEXT_DATA if available)
        max_results: Maximum number of relevant messages to return

    Returns:
        Formatted string of relevant historical messages or "No matches found"
    """
    # Validate inputs (MCP-specific validation)
    if not query or not query.strip():
        return "❌ Search query cannot be empty."
    
    if len(query) > 200:
        return "❌ Search query too long (max 200 characters)."
    
    if not chat_id:
        return "❌ No chat ID provided for history search. Ensure CONTEXT_DATA includes CHAT_ID."
    
    if max_results < 1 or max_results > 50:
        return "❌ max_results must be between 1 and 50."

    try:
        # Import chat history here to avoid import errors if not available
        from integrations.telegram.chat_history import ChatHistoryManager
        
        # Initialize chat history manager
        chat_history_obj = ChatHistoryManager()
        
        if not chat_history_obj:
            return "❌ Chat history system not available"
        
        # Convert chat_id to int if it's a string (MCP-specific handling)
        try:
            chat_id_int = int(chat_id)
        except ValueError:
            return f"❌ Invalid chat ID format: {chat_id}"
        
        # Call standalone implementation following GOLD STANDARD pattern
        result = search_telegram_history(query, chat_history_obj, chat_id_int, max_results)
        
        # Add MCP-specific formatting for enhanced user experience
        if result.startswith("No messages found"):
            return f"📂 {result} for chat {chat_id}"
        elif result.startswith("Found"):
            return f"📂 **{result.replace('Found', 'Found in chat ' + chat_id + ':')}**"
        else:
            return f"📂 **Search Results for '{query}' in chat {chat_id}:**\n\n{result}"
        
    except ImportError:
        return "❌ Telegram chat history system not available - missing integrations"
    except Exception as e:
        return f"❌ Error searching message history: {str(e)}"


@mcp.tool()
def get_conversation_context(chat_id: str = "", hours_back: int = 24) -> str:
    """Get a summary of recent conversation context for understanding the flow.
    
    Use this when you need to understand the broader conversation context beyond just
    the last few messages. Provides extended context from recent hours to help with
    conversation continuity and reference understanding.

    Args:
        chat_id: Chat ID to get context for (extracted from CONTEXT_DATA if available)
        hours_back: How many hours back to summarize

    Returns:
        Formatted summary of recent conversation or "No recent activity"
    """
    # MCP-specific validation
    if not chat_id:
        return "❌ No chat ID provided for context retrieval. Ensure CONTEXT_DATA includes CHAT_ID."

    try:
        # Import chat history here to avoid import errors if not available
        from integrations.telegram.chat_history import ChatHistoryManager
        
        # Initialize chat history manager
        chat_history_obj = ChatHistoryManager()
        
        if not chat_history_obj:
            return "❌ Chat history system not available"
        
        # Convert chat_id to int if it's a string (MCP-specific handling)
        try:
            chat_id_int = int(chat_id)
        except ValueError:
            return f"❌ Invalid chat ID format: {chat_id}"
        
        # Call standalone implementation following GOLD STANDARD pattern
        result = get_telegram_context_summary(chat_history_obj, chat_id_int, hours_back)
        
        # Add MCP-specific formatting for enhanced user experience
        if result.startswith("No conversation activity"):
            return f"📭 {result} for chat {chat_id}"
        elif result.startswith("Conversation summary"):
            # Enhanced formatting with chat context
            enhanced_result = f"💬 **Conversation Context Summary**\n"
            enhanced_result += f"📅 Last {hours_back} hours | 🔗 Chat {chat_id}\n\n"
            enhanced_result += result.replace("Conversation summary", "Summary")
            return enhanced_result
        else:
            return f"💬 **Context for chat {chat_id}:**\n\n{result}"
        
    except ImportError:
        return "❌ Telegram chat history system not available - missing integrations"
    except Exception as e:
        return f"❌ Error getting conversation summary: {str(e)}"


@mcp.tool()
def get_recent_history(chat_id: str = "", max_messages: int = 10) -> str:
    """Get the most recent messages from a conversation for immediate context.
    
    Use this tool to quickly get recent conversation history when you need to understand
    what was discussed recently but it's not in the immediate context window.

    Args:
        chat_id: Chat ID to get history for (extracted from CONTEXT_DATA if available)
        max_messages: Maximum number of recent messages to retrieve

    Returns:
        Formatted list of recent messages or "No recent messages"
    """
    if not chat_id:
        return "❌ No chat ID provided for recent history. Ensure CONTEXT_DATA includes CHAT_ID."

    try:
        # Import chat history here to avoid import errors if not available
        from integrations.telegram.chat_history import ChatHistoryManager
        
        # Initialize chat history manager
        chat_history_obj = ChatHistoryManager()
        
        if not chat_history_obj:
            return "❌ Chat history system not available"
        
        # Convert chat_id to int if it's a string
        try:
            chat_id_int = int(chat_id)
        except ValueError:
            return f"❌ Invalid chat ID format: {chat_id}"
        
        # Get recent messages
        recent_messages = chat_history_obj.get_context(
            chat_id=chat_id_int,
            max_context_messages=max_messages,
            max_age_hours=24,  # Last 24 hours
            always_include_last=max_messages
        )
        
        if not recent_messages:
            return f"📭 No recent messages found for chat {chat_id}"
        
        # Format recent messages
        result = f"📱 **Recent Messages** (Chat {chat_id}):\n\n"
        
        for i, msg in enumerate(recent_messages, 1):
            timestamp = msg.get('timestamp', 'Unknown time')
            role = msg.get('role', 'unknown')
            content = msg.get('content', '').strip()
            
            # Show full content for recent messages
            result += f"{i}. **{role}** ({timestamp}):\n   {content}\n\n"
            
        return result.strip()
        
    except ImportError:
        return "❌ Telegram chat history system not available - missing integrations"
    except Exception as e:
        return f"❌ Error getting recent history: {str(e)}"


@mcp.tool()
def list_telegram_dialogs() -> str:
    """List all active Telegram groups and DMs with their details.
    
    Use this tool to get an overview of all available Telegram conversations,
    including groups, channels, and direct messages. Useful for understanding
    which chats are available and their basic information like chat IDs, titles,
    member counts, and unread message counts.

    Returns:
        Formatted string listing all groups and DMs with their details,
        or error message if operation fails
    """
    try:
        # Import required modules
        import asyncio
        from integrations.telegram.client import TelegramClient
        from integrations.telegram.utils import format_dialogs_list, list_telegram_dialogs_safe
        
        async def get_dialogs():
            """Async helper to get dialogs."""
            # Create a temporary client instance for listing dialogs
            # Note: This assumes the client can be initialized without starting
            client = TelegramClient()
            
            # Check if there's an existing client session file
            import os
            session_file = os.path.join(client.workdir, "ai_project_bot.session")
            if not os.path.exists(session_file):
                return None, "❌ No active Telegram session found. Please authenticate first using scripts/telegram_login.sh"
            
            # Initialize the client
            if not await client.initialize():
                return None, "❌ Failed to initialize Telegram client"
            
            try:
                # Get dialogs safely
                dialogs_data, error = await list_telegram_dialogs_safe(client)
                return dialogs_data, error
            finally:
                # Always stop the client after getting dialogs
                await client.stop()
        
        # Run the async function
        try:
            dialogs_data, error = asyncio.run(get_dialogs())
        except RuntimeError:
            # If we're already in an event loop, use current loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule as a task in the current loop
                import asyncio
                task = loop.create_task(get_dialogs())
                dialogs_data, error = task.result() if task.done() else (None, "❌ Failed to retrieve dialogs in current event loop")
            else:
                dialogs_data, error = loop.run_until_complete(get_dialogs())
        
        if error:
            return error
        
        if not dialogs_data:
            return "❌ No dialogs data received"
        
        # Format and return the results
        formatted_result = format_dialogs_list(dialogs_data)
        return formatted_result
        
    except ImportError as e:
        return f"❌ Telegram integration not available: Missing required modules ({e})"
    except Exception as e:
        return f"❌ Error listing Telegram dialogs: {str(e)}"


if __name__ == "__main__":
    mcp.run()