"""AI Agents and Agent Management System

This package provides comprehensive AI agent capabilities with PydanticAI integration,
context management, tool registry, and advanced conversation handling.
"""

# Import base data classes first
from .valor.context import ValorContext, MessageEntry
# Then context manager (depends on context)
from .context_manager import ContextWindowManager, CompressionStrategy, TokenEstimator
# Then agent (depends on context_manager)
from .valor.agent import ValorAgent
# Then tool registry (independent)
from .tool_registry import (
    ToolRegistry,
    ToolMetadata,
    ToolParameter,
    ToolExecution,
    tool_registry_decorator,
    discover_tools
)

__all__ = [
    # Agent implementations
    "ValorAgent",
    "ValorContext",
    "MessageEntry",
    
    # Context management
    "ContextWindowManager",
    "CompressionStrategy", 
    "TokenEstimator",
    
    # Tool system
    "ToolRegistry",
    "ToolMetadata",
    "ToolParameter",
    "ToolExecution",
    "tool_registry_decorator",
    "discover_tools",
]