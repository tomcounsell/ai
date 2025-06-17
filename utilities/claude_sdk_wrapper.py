"""
Claude Code SDK Wrapper
Replaces subprocess-based CLI integration with native Python SDK.
Massive code reduction while maintaining interface compatibility.
"""

import asyncio
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Any
import logging

try:
    from claude_code_sdk import query, ClaudeCodeOptions, Message
    from claude_code_sdk.exceptions import CLINotFoundError, CLIConnectionError, ProcessError, CLIJSONDecodeError
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    # Fallback types for development
    class Message:
        pass
    class ClaudeCodeOptions:
        pass

logger = logging.getLogger(__name__)


class PermissionMode(Enum):
    """Structured permission modes for ClaudeCodeOptions."""
    ACCEPT_EDITS = "acceptEdits"
    READ_ONLY = "readOnly" 
    INTERACTIVE = "interactive"


class AllowedTool(Enum):
    """Structured allowed tools for ClaudeCodeOptions."""
    BASH = "bash"
    EDITOR = "editor"
    BROWSER = "browser"
    FILE_READER = "str_replace_editor"
    COMPUTER = "computer"


@dataclass
class ClaudeTaskOptions:
    """Structured configuration for Claude Code tasks."""
    max_turns: int = 10
    working_directory: Optional[str] = None
    system_prompt: Optional[str] = None
    permission_mode: PermissionMode = PermissionMode.ACCEPT_EDITS
    allowed_tools: List[AllowedTool] = None
    timeout_minutes: int = 5
    
    def __post_init__(self):
        if self.allowed_tools is None:
            self.allowed_tools = [
                AllowedTool.BASH,
                AllowedTool.EDITOR,
                AllowedTool.FILE_READER
            ]


class ClaudeCodeSDK:
    """
    SDK wrapper replacing subprocess-based CLI integration.
    
    MASSIVE CODE REDUCTION:
    - Eliminates utilities/claude_code_session_manager.py (200 lines)
    - Eliminates utilities/swe_error_recovery.py (180 lines)  
    - Simplifies tools/valor_delegation_tool.py (400 → 100 lines)
    """
    
    def __init__(self):
        if not SDK_AVAILABLE:
            raise ImportError("claude-code-sdk not installed. Run: pip install claude-code-sdk")
            
        self.active_conversations: Dict[str, List[Message]] = {}
        
    async def execute_task(
        self,
        prompt: str,
        options: ClaudeTaskOptions = None,
        chat_id: str = None
    ) -> AsyncIterator[str]:
        """
        Execute Claude Code task with SDK streaming.
        
        Replaces complex subprocess management with simple async iteration.
        """
        if options is None:
            options = ClaudeTaskOptions()
            
        try:
            claude_options = self._build_claude_options(options)
            
            async for message in query(prompt, claude_options):
                # Stream real-time updates
                if hasattr(message, 'content') and message.content:
                    yield message.content
                    
                # Store for conversation continuity
                if chat_id:
                    if chat_id not in self.active_conversations:
                        self.active_conversations[chat_id] = []
                    self.active_conversations[chat_id].append(message)
                    
        except CLINotFoundError:
            yield "❌ Claude Code CLI not installed. Please run: npm install -g @anthropic-ai/claude-code"
        except CLIConnectionError:
            yield "🔌 Connection to Claude Code failed. Please check your setup."
        except ProcessError as e:
            yield f"⚠️ Execution error: {str(e)}"
        except CLIJSONDecodeError as e:
            yield f"📄 Communication error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected SDK error: {str(e)}")
            yield f"❌ Unexpected error: {str(e)}"
    
    async def execute_task_sync(
        self,
        prompt: str,
        options: ClaudeTaskOptions = None,
        chat_id: str = None
    ) -> str:
        """
        Execute task and return complete result.
        
        Maintains compatibility with existing sync interfaces.
        """
        result_parts = []
        async for chunk in self.execute_task(prompt, options, chat_id):
            result_parts.append(chunk)
        return "".join(result_parts)
    
    def _build_claude_options(self, options: ClaudeTaskOptions) -> ClaudeCodeOptions:
        """Build ClaudeCodeOptions from our structured configuration."""
        # Use kwargs to build options
        kwargs = {
            "max_turns": options.max_turns,
        }
        
        if options.working_directory:
            kwargs["cwd"] = Path(options.working_directory)
            
        if options.system_prompt:
            kwargs["system_prompt"] = options.system_prompt
            
        # Only add permission_mode and allowed_tools if SDK supports them
        try:
            kwargs["permission_mode"] = options.permission_mode.value
            kwargs["allowed_tools"] = [tool.value for tool in options.allowed_tools]
        except Exception:
            # Fallback if SDK doesn't support these options yet
            pass
            
        return ClaudeCodeOptions(**kwargs)
    
    def clear_conversation(self, chat_id: str):
        """Clear conversation history for chat."""
        if chat_id in self.active_conversations:
            del self.active_conversations[chat_id]
    
    def get_conversation_summary(self, chat_id: str) -> str:
        """Get summary of conversation for chat."""
        if chat_id not in self.active_conversations:
            return "No active conversation"
            
        messages = self.active_conversations[chat_id]
        return f"Conversation with {len(messages)} messages"


# Global SDK instance
_sdk_instance = None

def get_claude_sdk() -> ClaudeCodeSDK:
    """Get global SDK instance."""
    global _sdk_instance
    if _sdk_instance is None:
        _sdk_instance = ClaudeCodeSDK()
    return _sdk_instance


# Compatibility functions for existing code
async def execute_claude_task(
    prompt: str,
    working_directory: str = None,
    max_turns: int = 10,
    chat_id: str = None
) -> AsyncIterator[str]:
    """
    Compatibility function replacing subprocess calls.
    
    REPLACES:
    - subprocess.run(["claude", "--print", prompt], ...)
    - Complex session management
    - Manual error handling
    """
    options = ClaudeTaskOptions(
        max_turns=max_turns,
        working_directory=working_directory
    )
    
    sdk = get_claude_sdk()
    async for chunk in sdk.execute_task(prompt, options, chat_id):
        yield chunk


async def execute_claude_task_sync(
    prompt: str,
    working_directory: str = None,
    max_turns: int = 10,
    chat_id: str = None
) -> str:
    """Sync version for compatibility."""
    result_parts = []
    async for chunk in execute_claude_task(prompt, working_directory, max_turns, chat_id):
        result_parts.append(chunk)
    return "".join(result_parts)


def anyio_run_sync(coro):
    """Helper to run async code in sync contexts."""
    import anyio
    return anyio.run(coro)