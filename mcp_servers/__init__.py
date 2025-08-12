"""
Model Context Protocol (MCP) servers and connections

This module provides a comprehensive MCP server implementation with:
- Stateless server architecture with context injection
- Multiple specialized server types (social, development, project management, telegram)
- Orchestration and service discovery
- Health monitoring and load balancing
- Inter-server communication
"""

from .base import (
    MCPServer,
    MCPServerFactory,
    MCPRequest,
    MCPResponse,
    MCPError,
    MCPToolCapability,
    MCPServerStatus,
    ContextInjector,
    DefaultContextInjector
)

from .context_manager import (
    MCPContextManager,
    WorkspaceContext,
    UserContext,
    SessionContext,
    SecurityContext,
    EnrichedContext,
    SecurityLevel,
    ContextScope
)

from .social_tools import SocialToolsServer
from .pm_tools import ProjectManagementServer
from .telegram_tools import TelegramToolsServer
from .development_tools import DevelopmentToolsServer

from .orchestrator import (
    MCPOrchestrator,
    ServerRegistration,
    RoutingRule,
    InterServerMessage,
    ServerHealth,
    MessagePriority
)

__all__ = [
    # Base MCP components
    "MCPServer",
    "MCPServerFactory", 
    "MCPRequest",
    "MCPResponse",
    "MCPError",
    "MCPToolCapability",
    "MCPServerStatus",
    "ContextInjector",
    "DefaultContextInjector",
    
    # Context management
    "MCPContextManager",
    "WorkspaceContext",
    "UserContext", 
    "SessionContext",
    "SecurityContext",
    "EnrichedContext",
    "SecurityLevel",
    "ContextScope",
    
    # Server implementations
    "SocialToolsServer",
    "ProjectManagementServer",
    "TelegramToolsServer",
    "DevelopmentToolsServer",
    
    # Orchestration
    "MCPOrchestrator",
    "ServerRegistration",
    "RoutingRule", 
    "InterServerMessage",
    "ServerHealth",
    "MessagePriority"
]