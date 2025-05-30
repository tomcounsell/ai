#!/usr/bin/env python3
"""
UnifiedValorClaudeAgent - Phase 2 Implementation

This module implements the unified agent system that seamlessly combines Valor's
conversational persona with Claude Code's development capabilities through MCP integration.

The UnifiedValorClaudeAgent eliminates the delegation model and provides a single,
unified interface for both conversation and development tasks.
"""

import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Union

from dotenv import load_dotenv
from pydantic import BaseModel

# Load environment variables
load_dotenv()


class UnifiedContext(BaseModel):
    """Unified context for Valor-Claude integration.
    
    This context class supports all interaction modes and provides the necessary
    information for both conversational and development tasks.
    
    Attributes:
        chat_id: Unique identifier for the chat session.
        username: Username of the person interacting.
        is_group_chat: Whether this is a group chat or direct conversation.
        chat_history: List of previous chat messages for context.
        chat_history_obj: Chat history manager instance for search tools.
        notion_data: Optional Notion project data for priority questions.
        is_priority_question: Whether this message is asking about work priorities.
        working_directory: Current working directory for development tasks.
        project_context: Additional project-specific context.
    """
    
    chat_id: Optional[int] = None
    username: Optional[str] = None
    is_group_chat: bool = False
    chat_history: list[dict[str, Any]] = []
    chat_history_obj: Any = None
    notion_data: Optional[str] = None
    is_priority_question: bool = False
    working_directory: str = "/Users/valorengels/src/ai"
    project_context: Optional[str] = None


class ClaudeCodeSession:
    """Manages Claude Code sessions with streaming support."""
    
    def __init__(self, working_directory: str = "/Users/valorengels/src/ai"):
        self.working_directory = working_directory
        self.session_id = f"unified_session_{int(time.time())}"
        self.active_process: Optional[subprocess.Popen] = None
        
    def _build_claude_command(self, message: str, mcp_servers: list[str]) -> list[str]:
        """Build the Claude Code command with MCP server configuration."""
        cmd = [
            "claude",
            "--mcp", ",".join(mcp_servers),
            "--working-directory", self.working_directory,
            "--session-id", self.session_id,
            "--stream"
        ]
        return cmd
    
    async def stream_response(self, message: str, mcp_servers: list[str]) -> AsyncIterator[str]:
        """Stream Claude Code responses with MCP tool integration."""
        cmd = self._build_claude_command(message, mcp_servers)
        
        try:
            # Create temporary file for input
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(message)
                input_file = f.name
            
            # Start Claude Code process with streaming
            process = subprocess.Popen(
                cmd + ["-f", input_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.working_directory
            )
            
            self.active_process = process
            
            # Stream output
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    yield output.strip()
                    
            # Wait for completion
            process.wait()
            
            # Clean up
            os.unlink(input_file)
            self.active_process = None
            
        except Exception as e:
            yield f"Error in Claude Code session: {str(e)}"
    
    def terminate(self):
        """Terminate active Claude Code session."""
        if self.active_process:
            self.active_process.terminate()
            self.active_process = None


class TelegramStreamHandler:
    """Handles real-time streaming responses to Telegram with smart formatting."""
    
    def __init__(self):
        self.active_messages: Dict[str, Dict[str, Any]] = {}
        self.update_throttle = 2.0  # Prevent rate limiting
        
    async def send_update(self, chat_id: int, content: str):
        """Stream content updates to Telegram with intelligent batching."""
        message_key = f"{chat_id}_current"
        current_time = time.time()
        
        # Accumulate content for batching
        if message_key not in self.active_messages:
            self.active_messages[message_key] = {
                'content': content,
                'last_update': 0,
                'message_id': None,
                'buffer': []
            }
        else:
            self.active_messages[message_key]['buffer'].append(content)
        
        message_data = self.active_messages[message_key]
        
        # Smart update timing to avoid rate limits
        if current_time - message_data['last_update'] >= self.update_throttle:
            # Flush buffer to content
            if message_data['buffer']:
                message_data['content'] += ''.join(message_data['buffer'])
                message_data['buffer'] = []
            
            # Here you would implement actual Telegram message sending
            # This is a placeholder for the actual Telegram API integration
            print(f"[TELEGRAM UPDATE] Chat {chat_id}: {message_data['content'][:100]}...")
            
            message_data['last_update'] = current_time
    
    async def process_special_responses(self, response_chunk: str, chat_id: int):
        """Handle special response types during streaming."""
        
        # Image generation detection
        if 'TELEGRAM_IMAGE_GENERATED|' in response_chunk:
            await self._handle_image_response(response_chunk, chat_id)
        
        # Progress indicators
        if any(indicator in response_chunk.lower() for indicator in 
               ['analyzing', 'creating', 'testing', 'committing']):
            await self._add_progress_reaction(chat_id)
        
        # Completion indicators
        if any(indicator in response_chunk.lower() for indicator in 
               ['completed', 'finished', 'done', 'success']):
            await self._add_completion_reaction(chat_id)
    
    async def _handle_image_response(self, response_chunk: str, chat_id: int):
        """Handle image generation responses."""
        # Extract image path from response
        if '|' in response_chunk:
            parts = response_chunk.split('|')
            if len(parts) >= 2:
                image_path = parts[1]
                print(f"[TELEGRAM IMAGE] Sending image {image_path} to chat {chat_id}")
    
    async def _add_progress_reaction(self, chat_id: int):
        """Add progress reaction to message."""
        print(f"[TELEGRAM REACTION] Adding progress reaction to chat {chat_id}")
    
    async def _add_completion_reaction(self, chat_id: int):
        """Add completion reaction to message."""
        print(f"[TELEGRAM REACTION] Adding completion reaction to chat {chat_id}")


class ConversationContextManager:
    """Manages conversation context and history across interactions."""
    
    def __init__(self):
        self.context_store: Dict[int, Dict[str, Any]] = {}
    
    def get_context(self, chat_id: int) -> Dict[str, Any]:
        """Get stored context for a chat."""
        return self.context_store.get(chat_id, {})
    
    def update_context(self, chat_id: int, context_data: Dict[str, Any]):
        """Update stored context for a chat."""
        if chat_id not in self.context_store:
            self.context_store[chat_id] = {}
        self.context_store[chat_id].update(context_data)
    
    def get_notion_data(self, chat_id: int) -> Optional[str]:
        """Get Notion data for priority questions."""
        context = self.get_context(chat_id)
        return context.get('notion_data')


class UnifiedValorClaudeAgent:
    """
    The unified agent that seamlessly combines Valor's conversational persona
    with Claude Code's development capabilities through MCP integration.
    
    This class eliminates the delegation model and provides a single interface
    for both chat and code execution with real-time streaming feedback.
    """
    
    def __init__(self, working_directory: str = "/Users/valorengels/src/ai"):
        """Initialize the unified agent system.
        
        Args:
            working_directory: Default working directory for development tasks.
        """
        self.working_directory = working_directory
        self.claude_session = ClaudeCodeSession(working_directory)
        self.telegram_streamer = TelegramStreamHandler()
        self.context_manager = ConversationContextManager()
        self.mcp_servers = ['social-tools', 'notion-tools', 'telegram-tools']
        
        # Load Valor persona
        self.valor_persona = self._load_valor_persona()
        
    def _load_valor_persona(self) -> str:
        """Load the Valor Engels persona from the persona document."""
        try:
            persona_file = Path(__file__).parent / "valor" / "persona.md"
            with open(persona_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error loading Valor persona: {str(e)}"
    
    def _build_unified_system_prompt(self) -> str:
        """Build the unified system prompt combining Valor persona with development capabilities."""
        
        development_integration = """
CRITICAL INTEGRATION GUIDELINES:

You are Valor Engels in a unified conversational development environment. You have seamless access to:

🔧 DEVELOPMENT CAPABILITIES:
- Read, write, and modify files in any directory
- Run tests, commit changes, and push to GitHub
- Explore codebases and understand project structures
- Create implementation plans and execute them step-by-step

🌐 SOCIAL & SEARCH TOOLS (via MCP):
- search_current_info: Get up-to-date web information
- create_image: Generate images with DALL-E 3
- save_link/search_links: Manage link collection with AI analysis
- query_notion_projects: Access project data and tasks

💬 CONVERSATION TOOLS (via MCP):
- search_conversation_history: Find specific topics in chat history
- get_conversation_context: Extended conversation summaries

SEAMLESS OPERATION RULES:
1. NEVER ask "Should I...?" or "What directory?" - just do what makes sense
2. NEVER separate "chat" from "coding" - they're one fluid experience
3. ALWAYS provide real-time progress updates during development work
4. Use tools naturally within conversation flow without explicit "switching modes"
5. For any development request, start working immediately with progress updates
6. For casual conversation, respond naturally while tools remain available

CONTEXT USAGE:
- Extract chat_id, username, and other context from CONTEXT_DATA when using tools
- Use recent conversation history to understand references and continuity
- Leverage project data for informed development decisions

You are not two separate systems - you are one unified conversational development environment.
"""
        
        return f"{self.valor_persona}\n\n{development_integration}"
    
    def _inject_context(self, message: str, context: UnifiedContext) -> str:
        """Inject Telegram context that MCP tools need."""
        
        context_vars = []
        
        # Essential context for tools
        if context.chat_id:
            context_vars.append(f"CHAT_ID={context.chat_id}")
        
        if context.username:
            context_vars.append(f"USERNAME={context.username}")
        
        # Recent conversation for context tools
        if context.chat_history:
            recent = context.chat_history[-5:]
            history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent])
            context_vars.append(f"RECENT_HISTORY:\n{history_text}")
        
        # Notion data if available (group-specific or priority questions)
        if context.notion_data:
            context_vars.append(f"PROJECT_DATA:\n{context.notion_data}")
        
        # Project context
        if context.project_context:
            context_vars.append(f"PROJECT_CONTEXT:\n{context.project_context}")
        
        if context_vars:
            context_block = "\n".join(context_vars)
            enhanced_message = f"""CONTEXT_DATA:
{context_block}

SYSTEM_PROMPT:
{self._build_unified_system_prompt()}

When using tools that need chat_id, username, or context data, extract it from CONTEXT_DATA above.

USER_REQUEST: {message}"""
        else:
            enhanced_message = f"""SYSTEM_PROMPT:
{self._build_unified_system_prompt()}

USER_REQUEST: {message}"""
        
        return enhanced_message
    
    async def handle_message(self, message: str, context: Optional[UnifiedContext] = None) -> AsyncIterator[str]:
        """
        Process any message through unified system with real-time streaming.
        
        This is the main entry point for the unified agent. It processes messages
        and streams responses directly, handling both conversational and development
        tasks seamlessly.
        
        Args:
            message: User message to process.
            context: Optional context about the conversation.
            
        Yields:
            str: Streaming response chunks from the unified system.
        """
        if context is None:
            context = UnifiedContext()
        
        # Build context-enhanced prompt
        enhanced_message = self._inject_context(message, context)
        
        # Stream responses from Claude Code with MCP tools
        async for response_chunk in self.claude_session.stream_response(enhanced_message, self.mcp_servers):
            # Handle special responses
            if context.chat_id:
                await self.telegram_streamer.process_special_responses(response_chunk, context.chat_id)
                await self.telegram_streamer.send_update(context.chat_id, response_chunk)
            
            yield response_chunk
    
    async def handle_telegram_message(self, message: str, chat_id: int, context: Dict[str, Any]) -> AsyncIterator[str]:
        """
        Process Telegram messages through the unified system.
        
        This method specifically handles Telegram integration with the unified agent,
        providing streaming responses and special Telegram features.
        
        Args:
            message: Telegram message content.
            chat_id: Telegram chat ID.
            context: Additional context from Telegram.
            
        Yields:
            str: Streaming response chunks formatted for Telegram.
        """
        # Build unified context
        unified_context = UnifiedContext(
            chat_id=chat_id,
            username=context.get('username'),
            is_group_chat=context.get('is_group_chat', False),
            chat_history=context.get('chat_history', []),
            chat_history_obj=context.get('chat_history_obj'),
            notion_data=context.get('notion_data'),
            is_priority_question=context.get('is_priority_question', False),
            project_context=context.get('project_context')
        )
        
        # Process through unified system
        async for response_chunk in self.handle_message(message, unified_context):
            yield response_chunk
    
    def terminate_session(self):
        """Terminate the current Claude Code session."""
        self.claude_session.terminate()
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get information about the current session."""
        return {
            'session_id': self.claude_session.session_id,
            'working_directory': self.working_directory,
            'mcp_servers': self.mcp_servers,
            'active_process': self.claude_session.active_process is not None
        }


# Utility functions for testing and integration

async def create_unified_agent(working_directory: str = "/Users/valorengels/src/ai") -> UnifiedValorClaudeAgent:
    """Create and initialize a unified agent instance."""
    return UnifiedValorClaudeAgent(working_directory=working_directory)


async def test_unified_agent():
    """Test the unified agent with sample interactions."""
    agent = await create_unified_agent()
    
    test_context = UnifiedContext(
        chat_id=12345,
        username="test_user",
        working_directory="/Users/valorengels/src/ai"
    )
    
    # Test conversational interaction
    print("Testing conversational interaction...")
    async for response in agent.handle_message("Hello, how are you today?", test_context):
        print(f"Response: {response}")
    
    # Test development interaction
    print("\nTesting development interaction...")
    async for response in agent.handle_message("Can you check the current git status?", test_context):
        print(f"Response: {response}")
    
    # Test tool integration
    print("\nTesting tool integration...")
    async for response in agent.handle_message("Search for latest Python 3.12 features", test_context):
        print(f"Response: {response}")
    
    agent.terminate_session()


if __name__ == "__main__":
    asyncio.run(test_unified_agent())