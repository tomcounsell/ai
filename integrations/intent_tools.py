"""
Intent-based tool access control for Claude Code CLI.

This module provides intelligent tool filtering and configuration based on 
detected message intent to optimize performance and relevance.
"""

import logging
from typing import List, Dict, Set, Optional, Any
from enum import Enum
from dataclasses import dataclass

from .ollama_intent import MessageIntent, IntentResult

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    """Categories of tools available in the system."""
    
    # Core development tools
    DEVELOPMENT = "development"     # Code editing, file operations, git
    
    # Information and research
    SEARCH = "search"              # Web search, current information
    ANALYSIS = "analysis"          # Link analysis, content analysis
    
    # Creative tools
    CREATIVE = "creative"          # Image generation, artistic creation
    VISION = "vision"              # Image analysis, visual understanding
    
    # Project management
    PROJECT = "project"            # Notion queries, task management
    
    # Communication
    COMMUNICATION = "communication" # Telegram tools, chat management
    
    # System tools
    SYSTEM = "system"              # Health checks, monitoring
    
    # General utilities
    UTILITY = "utility"            # General purpose tools


@dataclass
class ToolAccess:
    """Tool access configuration for an intent."""
    allowed_tools: Set[str]
    priority_tools: Set[str]  # Tools that should be highlighted/preferred
    restricted_tools: Set[str]  # Tools that should be avoided
    max_tools: Optional[int] = None  # Maximum number of tools to enable


class IntentToolManager:
    """Manages tool access based on detected message intent."""
    
    def __init__(self):
        """Initialize the intent-based tool manager."""
        
        # Define all available tools by category
        self.tools_by_category = {
            ToolCategory.DEVELOPMENT: {
                "bash", "edit", "multiedit", "write", "read", "glob", "grep",
                "ls", "task", "notebook_read", "notebook_edit"
            },
            ToolCategory.SEARCH: {
                "web_search", "perplexity_search", "search_current_info"
            },
            ToolCategory.ANALYSIS: {
                "web_fetch", "link_analysis", "save_link_for_later", "search_saved_links"
            },
            ToolCategory.CREATIVE: {
                "create_image", "image_generation", "dalle_generate"
            },
            ToolCategory.VISION: {
                "analyze_shared_image", "vision_analysis", "image_analysis"
            },
            ToolCategory.PROJECT: {
                "query_notion_projects", "notion_search", "project_status"
            },
            ToolCategory.COMMUNICATION: {
                "telegram_history", "chat_context", "message_search"
            },
            ToolCategory.SYSTEM: {
                "health_check", "system_status", "ping"
            },
            ToolCategory.UTILITY: {
                "todo_read", "todo_write", "file_operations"
            }
        }
        
        # Define tool access patterns for each intent
        self.intent_tool_access = {
            MessageIntent.CASUAL_CHAT: ToolAccess(
                allowed_tools={
                    "web_search", "search_current_info", "telegram_history",
                    "chat_context", "todo_read", "health_check"
                },
                priority_tools={"chat_context", "telegram_history"},
                restricted_tools=self._get_development_tools(),
                max_tools=4
            ),
            
            MessageIntent.QUESTION_ANSWER: ToolAccess(
                allowed_tools={
                    "web_search", "search_current_info", "web_fetch", 
                    "link_analysis", "read", "grep", "glob"
                },
                priority_tools={"web_search", "search_current_info"},
                restricted_tools={"edit", "write", "multiedit", "create_image"},
                max_tools=6
            ),
            
            MessageIntent.PROJECT_QUERY: ToolAccess(
                allowed_tools={
                    "query_notion_projects", "notion_search", "project_status",
                    "telegram_history", "chat_context", "read", "grep", "glob"
                },
                priority_tools={"query_notion_projects", "notion_search"},
                restricted_tools={"create_image", "bash", "edit", "write"},
                max_tools=6
            ),
            
            MessageIntent.DEVELOPMENT_TASK: ToolAccess(
                allowed_tools=self._get_all_development_tools() | {
                    "web_search", "search_current_info", "telegram_history"
                },
                priority_tools={"edit", "multiedit", "write", "read", "bash", "task"},
                restricted_tools={"create_image", "analyze_shared_image"},
                max_tools=12  # Development tasks need more tools
            ),
            
            MessageIntent.IMAGE_GENERATION: ToolAccess(
                allowed_tools={
                    "create_image", "image_generation", "dalle_generate",
                    "telegram_history", "chat_context"
                },
                priority_tools={"create_image", "image_generation"},
                restricted_tools=self._get_development_tools(),
                max_tools=4
            ),
            
            MessageIntent.IMAGE_ANALYSIS: ToolAccess(
                allowed_tools={
                    "analyze_shared_image", "vision_analysis", "image_analysis",
                    "read", "telegram_history", "chat_context"
                },
                priority_tools={"analyze_shared_image", "vision_analysis"},
                restricted_tools={"edit", "write", "bash", "create_image"},
                max_tools=5
            ),
            
            MessageIntent.WEB_SEARCH: ToolAccess(
                allowed_tools={
                    "web_search", "search_current_info", "perplexity_search",
                    "web_fetch", "link_analysis", "telegram_history"
                },
                priority_tools={"web_search", "search_current_info"},
                restricted_tools=self._get_development_tools() | {"create_image"},
                max_tools=6
            ),
            
            MessageIntent.LINK_ANALYSIS: ToolAccess(
                allowed_tools={
                    "web_fetch", "link_analysis", "save_link_for_later",
                    "search_saved_links", "web_search", "telegram_history"
                },
                priority_tools={"web_fetch", "link_analysis"},
                restricted_tools=self._get_development_tools() | {"create_image"},
                max_tools=6
            ),
            
            MessageIntent.SYSTEM_HEALTH: ToolAccess(
                allowed_tools={
                    "health_check", "system_status", "ping", "bash",
                    "read", "ls", "telegram_history"
                },
                priority_tools={"health_check", "system_status"},
                restricted_tools={"create_image", "edit", "write", "web_search"},
                max_tools=6
            ),
            
            MessageIntent.UNCLEAR: ToolAccess(
                allowed_tools=self._get_safe_tools(),
                priority_tools={"chat_context", "telegram_history"},
                restricted_tools={"edit", "write", "bash", "multiedit"},
                max_tools=8
            ),
        }

    def _get_development_tools(self) -> Set[str]:
        """Get all development-related tools."""
        return self.tools_by_category[ToolCategory.DEVELOPMENT].copy()

    def _get_all_development_tools(self) -> Set[str]:
        """Get comprehensive set of development tools."""
        return (self.tools_by_category[ToolCategory.DEVELOPMENT] |
                self.tools_by_category[ToolCategory.UTILITY])

    def _get_safe_tools(self) -> Set[str]:
        """Get tools safe for unclear intents."""
        return (self.tools_by_category[ToolCategory.SEARCH] |
                self.tools_by_category[ToolCategory.ANALYSIS] |
                self.tools_by_category[ToolCategory.COMMUNICATION] |
                self.tools_by_category[ToolCategory.SYSTEM] |
                {"read", "grep", "glob", "ls"})  # Safe read-only tools

    def get_allowed_tools(self, intent_result: IntentResult) -> List[str]:
        """
        Get list of allowed tools for a specific intent.
        
        Args:
            intent_result: Result from intent classification
            
        Returns:
            List[str]: List of tool names allowed for this intent
        """
        tool_access = self.intent_tool_access.get(intent_result.intent)
        if not tool_access:
            # Fallback to unclear intent tools
            tool_access = self.intent_tool_access[MessageIntent.UNCLEAR]
        
        allowed = list(tool_access.allowed_tools)
        
        # Apply max_tools limit if specified
        if tool_access.max_tools and len(allowed) > tool_access.max_tools:
            # Prioritize tools based on priority_tools
            priority = [tool for tool in allowed if tool in tool_access.priority_tools]
            regular = [tool for tool in allowed if tool not in tool_access.priority_tools]
            
            # Take priority tools first, then fill with regular tools
            allowed = priority + regular[:tool_access.max_tools - len(priority)]
        
        logger.debug(f"Intent {intent_result.intent.value} allows {len(allowed)} tools: {allowed}")
        return allowed

    def get_claude_code_config(self, intent_result: IntentResult) -> Dict[str, Any]:
        """
        Generate Claude Code CLI configuration based on intent.
        
        Args:
            intent_result: Result from intent classification
            
        Returns:
            Dict[str, Any]: Configuration for Claude Code CLI
        """
        tool_access = self.intent_tool_access.get(intent_result.intent)
        if not tool_access:
            tool_access = self.intent_tool_access[MessageIntent.UNCLEAR]
        
        allowed_tools = self.get_allowed_tools(intent_result)
        
        config = {
            "allowed_tools": allowed_tools,
            "intent": intent_result.intent.value,
            "confidence": intent_result.confidence,
            "reasoning": intent_result.reasoning,
            "restrictions": {
                "restricted_tools": list(tool_access.restricted_tools),
                "max_tools": tool_access.max_tools,
            },
            "optimization": {
                "priority_tools": list(tool_access.priority_tools),
                "focus_area": self._get_focus_area(intent_result.intent),
            }
        }
        
        return config

    def _get_focus_area(self, intent: MessageIntent) -> str:
        """Get the focus area description for an intent."""
        focus_areas = {
            MessageIntent.CASUAL_CHAT: "conversational interaction",
            MessageIntent.QUESTION_ANSWER: "information retrieval",
            MessageIntent.PROJECT_QUERY: "project management",
            MessageIntent.DEVELOPMENT_TASK: "code development",
            MessageIntent.IMAGE_GENERATION: "creative image generation",
            MessageIntent.IMAGE_ANALYSIS: "visual analysis",
            MessageIntent.WEB_SEARCH: "current information search",
            MessageIntent.LINK_ANALYSIS: "content analysis",
            MessageIntent.SYSTEM_HEALTH: "system monitoring",
            MessageIntent.UNCLEAR: "general assistance",
        }
        return focus_areas.get(intent, "general assistance")

    def should_restrict_tool(self, tool_name: str, intent_result: IntentResult) -> bool:
        """
        Check if a tool should be restricted for a given intent.
        
        Args:
            tool_name: Name of the tool to check
            intent_result: Result from intent classification
            
        Returns:
            bool: True if tool should be restricted
        """
        tool_access = self.intent_tool_access.get(intent_result.intent)
        if not tool_access:
            return False
        
        return tool_name in tool_access.restricted_tools

    def get_tool_priority(self, tool_name: str, intent_result: IntentResult) -> int:
        """
        Get priority score for a tool given an intent (higher = more important).
        
        Args:
            tool_name: Name of the tool
            intent_result: Result from intent classification
            
        Returns:
            int: Priority score (0-100)
        """
        tool_access = self.intent_tool_access.get(intent_result.intent)
        if not tool_access:
            return 50  # Default priority
        
        if tool_name in tool_access.priority_tools:
            return 90
        elif tool_name in tool_access.allowed_tools:
            return 70
        elif tool_name in tool_access.restricted_tools:
            return 10
        else:
            return 50

    def get_intent_summary(self, intent_result: IntentResult) -> str:
        """
        Get a human-readable summary of the intent and tool configuration.
        
        Args:
            intent_result: Result from intent classification
            
        Returns:
            str: Summary text
        """
        tool_access = self.intent_tool_access.get(intent_result.intent)
        if not tool_access:
            return f"Intent: {intent_result.intent.value} (unclear configuration)"
        
        summary = f"""Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})
Reasoning: {intent_result.reasoning}
Allowed tools: {len(tool_access.allowed_tools)}
Priority tools: {', '.join(sorted(tool_access.priority_tools)) if tool_access.priority_tools else 'none'}
Focus area: {self._get_focus_area(intent_result.intent)}"""
        
        return summary


# Singleton instance for use throughout the application
intent_tool_manager = IntentToolManager()


def get_intent_based_tools(intent_result: IntentResult) -> List[str]:
    """
    Convenience function to get allowed tools for an intent.
    
    Args:
        intent_result: Result from intent classification
        
    Returns:
        List[str]: List of allowed tool names
    """
    return intent_tool_manager.get_allowed_tools(intent_result)


def get_claude_code_configuration(intent_result: IntentResult) -> Dict[str, Any]:
    """
    Convenience function to get Claude Code configuration for an intent.
    
    Args:
        intent_result: Result from intent classification
        
    Returns:
        Dict[str, Any]: Configuration dictionary
    """
    return intent_tool_manager.get_claude_code_config(intent_result)