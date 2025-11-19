"""Valor Agent Implementation

This module contains the main ValorAgent class using PydanticAI for advanced
agent capabilities with context management and tool integration.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from .context import ValorContext
from ..context_manager import ContextWindowManager
from ..tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ValorResponse(BaseModel):
    """Response model for Valor agent interactions."""
    
    content: str = Field(..., description="The response content")
    context_updated: bool = Field(default=False, description="Whether context was updated")
    tools_used: List[str] = Field(default_factory=list, description="Tools used in response")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class ValorAgent:
    """
    Advanced AI agent using PydanticAI with comprehensive context management,
    tool integration, and persistent conversation handling.
    """
    
    def __init__(
        self,
        model: str = "openai:gpt-4",
        persona_path: Optional[Path] = None,
        max_context_tokens: int = 100_000,
        debug: bool = False
    ):
        """
        Initialize the Valor agent.
        
        Args:
            model: The model to use for the agent
            persona_path: Path to the persona.md file
            max_context_tokens: Maximum tokens for context window
            debug: Enable debug logging
        """
        self.model = model
        self.debug = debug
        self.max_context_tokens = max_context_tokens
        
        # Set up logging
        if debug:
            logging.getLogger().setLevel(logging.DEBUG)
        
        # Load persona
        self.persona_path = persona_path or (Path(__file__).parent / "persona.md")
        self.system_prompt = self._load_persona()
        
        # Initialize components
        self.context_manager = ContextWindowManager(max_tokens=max_context_tokens)
        self.tool_registry = ToolRegistry()
        
        # Initialize PydanticAI agent
        self._initialize_agent()
        
        # Active contexts
        self._contexts: Dict[str, ValorContext] = {}
        
        logger.info(f"Valor agent initialized with model: {model}")
    
    def _load_persona(self) -> str:
        """Load the system prompt from persona.md file."""
        try:
            if self.persona_path.exists():
                return self.persona_path.read_text(encoding='utf-8')
            else:
                logger.warning(f"Persona file not found at {self.persona_path}, using default")
                return self._get_default_persona()
        except Exception as e:
            logger.error(f"Error loading persona: {e}")
            return self._get_default_persona()
    
    def _get_default_persona(self) -> str:
        """Get default persona if file is not found."""
        return """You are Valor, an advanced AI assistant designed to help with complex tasks.
        
You are knowledgeable, helpful, and precise in your responses. You maintain context
across conversations and can use various tools to assist users effectively.

Your key capabilities:
- Context-aware conversations
- Tool integration and automation
- Complex problem solving
- Code analysis and generation
- Project management assistance

Always strive to be helpful while being honest about your limitations."""
    
    def _initialize_agent(self) -> None:
        """Initialize the PydanticAI agent with tools and configuration."""
        # Get available tools from registry
        tools = self.tool_registry.get_available_tools()
        
        # Create the PydanticAI agent
        self.agent = Agent(
            model=self.model,
            system_prompt=self.system_prompt,
            result_type=ValorResponse,
            deps_type=ValorContext
        )
        
        # Register tools with the agent
        for tool_name, tool_func in tools.items():
            self.agent.tool(tool_func)
        
        logger.debug(f"Agent initialized with {len(tools)} tools")
    
    async def create_context(
        self,
        chat_id: str,
        user_name: str,
        workspace: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ValorContext:
        """
        Create a new conversation context.
        
        Args:
            chat_id: Unique identifier for the conversation
            user_name: Name of the user
            workspace: Optional workspace identifier
            metadata: Additional session metadata
            
        Returns:
            ValorContext: New context instance
        """
        context = ValorContext(
            chat_id=chat_id,
            user_name=user_name,
            workspace=workspace or "default",
            message_history=[],
            active_tools=[],
            session_metadata=metadata or {}
        )
        
        self._contexts[chat_id] = context
        logger.info(f"Created new context for chat_id: {chat_id}")
        
        return context
    
    def get_context(self, chat_id: str) -> Optional[ValorContext]:
        """Get existing context by chat_id."""
        return self._contexts.get(chat_id)
    
    async def process_message(
        self,
        message: str,
        chat_id: str,
        user_name: Optional[str] = None,
        workspace: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ValorResponse:
        """
        Process a user message with context management.
        
        Args:
            message: User message to process
            chat_id: Conversation identifier
            user_name: User name (for new contexts)
            workspace: Workspace identifier
            metadata: Additional metadata
            
        Returns:
            ValorResponse: Agent response with metadata
        """
        # Get or create context
        context = self.get_context(chat_id)
        if not context:
            if not user_name:
                raise ValueError("user_name required for new conversations")
            context = await self.create_context(
                chat_id=chat_id,
                user_name=user_name,
                workspace=workspace,
                metadata=metadata
            )
        
        try:
            # Add user message to context
            context.add_message("user", message)
            
            # Manage context window
            if self.context_manager.needs_compression(context):
                context = await self.context_manager.compress_context(context)
                logger.debug(f"Context compressed for chat_id: {chat_id}")
            
            # Run the agent
            result = await self.agent.run(message, deps=context)
            
            # Add response to context
            context.add_message("assistant", result.data.content)
            
            # Update context metadata
            response_metadata = {
                "tools_used": result.data.tools_used,
                "context_tokens": self.context_manager.count_tokens(context),
                "timestamp": context.message_history[-1].timestamp.isoformat()
            }
            
            # Create response
            response = ValorResponse(
                content=result.data.content,
                context_updated=True,
                tools_used=result.data.tools_used,
                metadata=response_metadata
            )
            
            logger.debug(f"Processed message for chat_id: {chat_id}")
            return response
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Return error response
            return ValorResponse(
                content=f"I encountered an error while processing your message: {str(e)}",
                context_updated=False,
                tools_used=[],
                metadata={"error": str(e)}
            )
    
    async def get_conversation_summary(self, chat_id: str) -> Optional[str]:
        """Get a summary of the conversation for the given chat_id."""
        context = self.get_context(chat_id)
        if not context:
            return None
        
        return await self.context_manager.get_conversation_summary(context)
    
    def get_active_tools(self, chat_id: str) -> List[str]:
        """Get list of active tools for a conversation."""
        context = self.get_context(chat_id)
        if not context:
            return []
        return context.active_tools
    
    def register_tool(self, tool_func: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Register a new tool with the agent."""
        self.tool_registry.register_tool(tool_func, metadata)
        # Re-initialize agent with new tools
        self._initialize_agent()
        logger.info(f"Registered new tool: {tool_func.__name__}")
    
    def get_context_stats(self, chat_id: str) -> Dict[str, Any]:
        """Get context statistics for a conversation."""
        context = self.get_context(chat_id)
        if not context:
            return {}
        
        return {
            "message_count": len(context.message_history),
            "token_count": self.context_manager.count_tokens(context),
            "active_tools": len(context.active_tools),
            "workspace": context.workspace,
            "created_at": context.created_at.isoformat() if context.message_history else None,
            "last_activity": context.message_history[-1].timestamp.isoformat() if context.message_history else None
        }
    
    async def clear_context(self, chat_id: str) -> bool:
        """Clear conversation context."""
        if chat_id in self._contexts:
            del self._contexts[chat_id]
            logger.info(f"Cleared context for chat_id: {chat_id}")
            return True
        return False
    
    def list_contexts(self) -> List[str]:
        """List all active context IDs."""
        return list(self._contexts.keys())
    
    async def export_context(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Export context data for backup/analysis."""
        context = self.get_context(chat_id)
        if not context:
            return None
        
        return {
            "chat_id": context.chat_id,
            "user_name": context.user_name,
            "workspace": context.workspace,
            "message_history": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat(),
                    "metadata": msg.metadata
                }
                for msg in context.message_history
            ],
            "active_tools": context.active_tools,
            "session_metadata": context.session_metadata,
            "stats": self.get_context_stats(chat_id)
        }
    
    async def import_context(self, context_data: Dict[str, Any]) -> bool:
        """Import context data from backup."""
        try:
            chat_id = context_data["chat_id"]
            
            # Create new context
            context = ValorContext(
                chat_id=chat_id,
                user_name=context_data["user_name"],
                workspace=context_data["workspace"],
                message_history=[],
                active_tools=context_data["active_tools"],
                session_metadata=context_data["session_metadata"]
            )
            
            # Import message history
            for msg_data in context_data["message_history"]:
                context.add_message(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    metadata=msg_data.get("metadata", {})
                )
            
            self._contexts[chat_id] = context
            logger.info(f"Imported context for chat_id: {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error importing context: {e}")
            return False