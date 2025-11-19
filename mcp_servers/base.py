"""
Model Context Protocol (MCP) Server Base Implementation

This module provides the foundational MCP server architecture with:
- Stateless server design with context injection
- Tool registration and lifecycle management
- Request/response handling with comprehensive error propagation
- Server factory and orchestration patterns
- Performance monitoring and health checks
"""

import asyncio
import json
import logging
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Type, Union, Callable, Awaitable
from contextlib import asynccontextmanager

from pydantic import BaseModel, Field, validator

from utilities.exceptions import AISystemError as BaseError
from agents.valor.context import ValorContext


class MCPServerStatus(Enum):
    """MCP Server status enumeration."""
    
    INITIALIZING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


class MCPToolCapability(BaseModel):
    """Represents a tool capability exposed by an MCP server."""
    
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    version: str = Field(default="1.0.0", description="Tool version")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Tool parameter schema")
    returns: Dict[str, Any] = Field(default_factory=dict, description="Tool return schema")
    stateless: bool = Field(default=True, description="Whether tool is stateless")
    requires_context: bool = Field(default=True, description="Whether tool requires context injection")
    tags: List[str] = Field(default_factory=list, description="Tool tags for categorization")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class MCPRequest(BaseModel):
    """Standard MCP request structure."""
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique request ID")
    method: str = Field(..., description="Method name to invoke")
    params: Dict[str, Any] = Field(default_factory=dict, description="Method parameters")
    context: Optional[Dict[str, Any]] = Field(None, description="Execution context")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class MCPResponse(BaseModel):
    """Standard MCP response structure."""
    
    id: str = Field(..., description="Request ID this response corresponds to")
    success: bool = Field(default=True, description="Whether request was successful")
    result: Optional[Any] = Field(None, description="Method result if successful")
    error: Optional[Dict[str, Any]] = Field(None, description="Error details if failed")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Response metadata")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_time_ms: Optional[float] = Field(None, description="Execution time in milliseconds")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class MCPError(BaseError):
    """MCP-specific error with structured error information."""
    
    def __init__(
        self,
        message: str,
        error_code: str = "MCP_ERROR",
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = True,
        request_id: Optional[str] = None
    ):
        super().__init__(message, error_code, details, recoverable)
        self.request_id = request_id


class MCPServerMetrics(BaseModel):
    """MCP server performance and health metrics."""
    
    total_requests: int = Field(default=0, description="Total requests processed")
    successful_requests: int = Field(default=0, description="Successful requests")
    failed_requests: int = Field(default=0, description="Failed requests")
    average_response_time_ms: float = Field(default=0.0, description="Average response time")
    last_request_time: Optional[datetime] = Field(None, description="Last request timestamp")
    uptime_seconds: float = Field(default=0.0, description="Server uptime in seconds")
    active_connections: int = Field(default=0, description="Active client connections")
    tool_usage_stats: Dict[str, int] = Field(default_factory=dict, description="Tool usage statistics")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ContextInjector(ABC):
    """Abstract base class for context injection strategies."""
    
    @abstractmethod
    async def inject_context(
        self,
        request: MCPRequest,
        server_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Inject context into request processing.
        
        Args:
            request: The MCP request being processed
            server_context: Server-level context information
            
        Returns:
            Dict[str, Any]: Enriched context for request processing
        """
        pass
    
    @abstractmethod
    async def validate_context(self, context: Dict[str, Any]) -> bool:
        """
        Validate context security and integrity.
        
        Args:
            context: Context to validate
            
        Returns:
            bool: True if context is valid and secure
        """
        pass


class DefaultContextInjector(ContextInjector):
    """Default context injector implementation."""
    
    async def inject_context(
        self,
        request: MCPRequest,
        server_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Inject default context information."""
        enriched_context = {
            "request_id": request.id,
            "timestamp": request.timestamp,
            "server_info": server_context.get("server_info", {}),
            "security_context": server_context.get("security_context", {}),
        }
        
        # Merge request context if provided
        if request.context:
            enriched_context.update(request.context)
        
        return enriched_context
    
    async def validate_context(self, context: Dict[str, Any]) -> bool:
        """Basic context validation."""
        # Check for required fields
        required_fields = ["request_id", "timestamp"]
        return all(field in context for field in required_fields)


class MCPServer(ABC):
    """
    Abstract base class for Model Context Protocol (MCP) servers.
    
    Provides enterprise-grade foundation for stateless MCP servers with:
    - Context injection and security validation
    - Tool registration and lifecycle management
    - Comprehensive error handling and monitoring
    - Performance tracking and health checks
    """
    
    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        context_injector: Optional[ContextInjector] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.name = name
        self.version = version
        self.description = description
        self.logger = logger or logging.getLogger(f"mcp.{name}")
        self.context_injector = context_injector or DefaultContextInjector()
        
        # Server state
        self.status = MCPServerStatus.INITIALIZING
        self.start_time = time.time()
        self.metrics = MCPServerMetrics()
        
        # Tool registry
        self._tools: Dict[str, MCPToolCapability] = {}
        self._tool_handlers: Dict[str, Callable] = {}
        
        # Server context for injection
        self._server_context: Dict[str, Any] = {
            "server_info": {
                "name": self.name,
                "version": self.version,
                "description": self.description,
                "start_time": self.start_time,
            },
            "security_context": {},
        }
        
        # Event handlers
        self._request_middlewares: List[Callable] = []
        self._response_middlewares: List[Callable] = []
        
        self.logger.info(f"MCP Server '{self.name}' v{self.version} initialized")
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the MCP server.
        
        This method should be overridden by subclasses to perform
        server-specific initialization tasks.
        """
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """
        Shutdown the MCP server.
        
        This method should be overridden by subclasses to perform
        cleanup and resource deallocation.
        """
        pass
    
    async def start(self) -> None:
        """Start the MCP server."""
        try:
            self.status = MCPServerStatus.INITIALIZING
            self.logger.info(f"Starting MCP server '{self.name}'")
            
            await self.initialize()
            
            self.status = MCPServerStatus.RUNNING
            self.start_time = time.time()
            
            self.logger.info(f"MCP server '{self.name}' started successfully")
            
        except Exception as e:
            self.status = MCPServerStatus.ERROR
            self.logger.error(f"Failed to start MCP server '{self.name}': {str(e)}")
            raise MCPError(
                f"Server startup failed: {str(e)}",
                error_code="SERVER_STARTUP_ERROR",
                details={"server_name": self.name, "error": str(e)},
                recoverable=False
            )
    
    async def stop(self) -> None:
        """Stop the MCP server."""
        try:
            self.status = MCPServerStatus.STOPPING
            self.logger.info(f"Stopping MCP server '{self.name}'")
            
            await self.shutdown()
            
            self.status = MCPServerStatus.STOPPED
            self.logger.info(f"MCP server '{self.name}' stopped successfully")
            
        except Exception as e:
            self.status = MCPServerStatus.ERROR
            self.logger.error(f"Error stopping MCP server '{self.name}': {str(e)}")
            raise MCPError(
                f"Server shutdown failed: {str(e)}",
                error_code="SERVER_SHUTDOWN_ERROR",
                details={"server_name": self.name, "error": str(e)},
                recoverable=False
            )
    
    def register_tool(
        self,
        capability: MCPToolCapability,
        handler: Callable[[MCPRequest, Dict[str, Any]], Awaitable[Any]]
    ) -> None:
        """
        Register a tool capability with its handler.
        
        Args:
            capability: Tool capability definition
            handler: Async function to handle tool execution
        """
        if capability.name in self._tools:
            raise MCPError(
                f"Tool '{capability.name}' is already registered",
                error_code="TOOL_ALREADY_REGISTERED",
                details={"tool_name": capability.name}
            )
        
        self._tools[capability.name] = capability
        self._tool_handlers[capability.name] = handler
        
        self.logger.info(f"Registered tool '{capability.name}' in server '{self.name}'")
    
    def unregister_tool(self, tool_name: str) -> bool:
        """
        Unregister a tool capability.
        
        Args:
            tool_name: Name of tool to unregister
            
        Returns:
            bool: True if tool was unregistered, False if not found
        """
        if tool_name not in self._tools:
            return False
        
        del self._tools[tool_name]
        del self._tool_handlers[tool_name]
        
        self.logger.info(f"Unregistered tool '{tool_name}' from server '{self.name}'")
        return True
    
    def get_capabilities(self) -> List[MCPToolCapability]:
        """Get list of all registered tool capabilities."""
        return list(self._tools.values())
    
    def get_tool_capability(self, tool_name: str) -> Optional[MCPToolCapability]:
        """Get specific tool capability by name."""
        return self._tools.get(tool_name)
    
    async def process_request(self, request: MCPRequest) -> MCPResponse:
        """
        Process an MCP request with comprehensive error handling and monitoring.
        
        Args:
            request: The MCP request to process
            
        Returns:
            MCPResponse: The response to the request
        """
        start_time = time.time()
        response = MCPResponse(id=request.id)
        
        try:
            # Update metrics
            self.metrics.total_requests += 1
            self.metrics.last_request_time = datetime.now(timezone.utc)
            
            # Validate server status
            if self.status != MCPServerStatus.RUNNING:
                raise MCPError(
                    f"Server '{self.name}' is not running (status: {self.status.name})",
                    error_code="SERVER_NOT_RUNNING",
                    details={"server_status": self.status.name},
                    request_id=request.id
                )
            
            # Apply request middlewares
            for middleware in self._request_middlewares:
                request = await middleware(request)
            
            # Inject context
            enriched_context = await self.context_injector.inject_context(
                request, self._server_context
            )
            
            # Validate context security
            if not await self.context_injector.validate_context(enriched_context):
                raise MCPError(
                    "Context validation failed",
                    error_code="CONTEXT_VALIDATION_ERROR",
                    details={"request_id": request.id},
                    request_id=request.id
                )
            
            # Route request to appropriate handler
            if request.method == "list_tools":
                response.result = await self._handle_list_tools(request, enriched_context)
            elif request.method == "call_tool":
                response.result = await self._handle_call_tool(request, enriched_context)
            elif request.method == "get_server_info":
                response.result = await self._handle_get_server_info(request, enriched_context)
            elif request.method == "health_check":
                response.result = await self._handle_health_check(request, enriched_context)
            else:
                # Try custom method handlers
                response.result = await self._handle_custom_method(request, enriched_context)
            
            # Apply response middlewares
            for middleware in self._response_middlewares:
                response = await middleware(response)
            
            # Update success metrics
            self.metrics.successful_requests += 1
            response.success = True
            
        except MCPError as e:
            self.logger.error(f"MCP error in server '{self.name}': {e.message}")
            response.success = False
            response.error = {
                "code": e.error_code,
                "message": e.message,
                "details": e.details,
                "request_id": request.id,
                "recoverable": e.recoverable
            }
            self.metrics.failed_requests += 1
            
        except Exception as e:
            self.logger.error(f"Unexpected error in server '{self.name}': {str(e)}")
            response.success = False
            response.error = {
                "code": "INTERNAL_SERVER_ERROR",
                "message": f"Internal server error: {str(e)}",
                "details": {"traceback": traceback.format_exc()},
                "request_id": request.id,
                "recoverable": False
            }
            self.metrics.failed_requests += 1
        
        finally:
            # Update timing metrics
            execution_time_ms = (time.time() - start_time) * 1000
            response.execution_time_ms = execution_time_ms
            
            # Update average response time
            total_requests = self.metrics.total_requests
            if total_requests > 0:
                current_avg = self.metrics.average_response_time_ms
                self.metrics.average_response_time_ms = (
                    (current_avg * (total_requests - 1) + execution_time_ms) / total_requests
                )
            
            # Update uptime
            self.metrics.uptime_seconds = time.time() - self.start_time
        
        return response
    
    async def _handle_list_tools(
        self, request: MCPRequest, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Handle list_tools method."""
        capabilities = []
        for capability in self._tools.values():
            capabilities.append({
                "name": capability.name,
                "description": capability.description,
                "version": capability.version,
                "parameters": capability.parameters,
                "returns": capability.returns,
                "stateless": capability.stateless,
                "requires_context": capability.requires_context,
                "tags": capability.tags
            })
        return capabilities
    
    async def _handle_call_tool(
        self, request: MCPRequest, context: Dict[str, Any]
    ) -> Any:
        """Handle call_tool method."""
        tool_name = request.params.get("name")
        if not tool_name:
            raise MCPError(
                "Tool name is required",
                error_code="MISSING_TOOL_NAME",
                request_id=request.id
            )
        
        if tool_name not in self._tool_handlers:
            raise MCPError(
                f"Unknown tool: {tool_name}",
                error_code="UNKNOWN_TOOL",
                details={"tool_name": tool_name},
                request_id=request.id
            )
        
        # Update tool usage stats
        self.metrics.tool_usage_stats[tool_name] = (
            self.metrics.tool_usage_stats.get(tool_name, 0) + 1
        )
        
        # Execute tool handler
        handler = self._tool_handlers[tool_name]
        tool_request = MCPRequest(
            id=request.id,
            method=tool_name,
            params=request.params.get("parameters", {}),
            context=context
        )
        
        result = await handler(tool_request, context)
        return result
    
    async def _handle_get_server_info(
        self, request: MCPRequest, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle get_server_info method."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "status": self.status.name,
            "capabilities": len(self._tools),
            "uptime_seconds": time.time() - self.start_time,
            "context_injection": True,
            "stateless": True
        }
    
    async def _handle_health_check(
        self, request: MCPRequest, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle health_check method."""
        health_score = 10.0
        
        # Check error rate
        total_requests = self.metrics.total_requests
        if total_requests > 0:
            error_rate = self.metrics.failed_requests / total_requests
            if error_rate > 0.1:  # More than 10% error rate
                health_score -= min(5.0, error_rate * 20)
        
        # Check response time
        if self.metrics.average_response_time_ms > 1000:  # Slower than 1 second
            health_score -= 2.0
        
        is_healthy = health_score >= 7.0 and self.status == MCPServerStatus.RUNNING
        
        return {
            "healthy": is_healthy,
            "health_score": max(0.0, health_score),
            "status": self.status.name,
            "metrics": self.metrics.dict(),
            "uptime_seconds": time.time() - self.start_time,
            "check_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def _handle_custom_method(
        self, request: MCPRequest, context: Dict[str, Any]
    ) -> Any:
        """Handle custom methods. Override in subclasses."""
        raise MCPError(
            f"Unknown method: {request.method}",
            error_code="UNKNOWN_METHOD",
            details={"method": request.method},
            request_id=request.id
        )
    
    def add_request_middleware(self, middleware: Callable[[MCPRequest], Awaitable[MCPRequest]]) -> None:
        """Add request middleware."""
        self._request_middlewares.append(middleware)
    
    def add_response_middleware(self, middleware: Callable[[MCPResponse], Awaitable[MCPResponse]]) -> None:
        """Add response middleware."""
        self._response_middlewares.append(middleware)
    
    def update_security_context(self, context: Dict[str, Any]) -> None:
        """Update server security context."""
        self._server_context["security_context"].update(context)
    
    @asynccontextmanager
    async def server_lifecycle(self):
        """Context manager for server lifecycle management."""
        try:
            await self.start()
            yield self
        finally:
            await self.stop()


class MCPServerFactory:
    """Factory for creating and managing MCP servers."""
    
    def __init__(self):
        self._server_classes: Dict[str, Type[MCPServer]] = {}
        self._running_servers: Dict[str, MCPServer] = {}
        self.logger = logging.getLogger("mcp.factory")
    
    def register_server_class(self, server_type: str, server_class: Type[MCPServer]) -> None:
        """Register a server class type."""
        self._server_classes[server_type] = server_class
        self.logger.info(f"Registered MCP server class: {server_type}")
    
    async def create_server(
        self,
        server_type: str,
        name: str,
        config: Dict[str, Any] = None
    ) -> MCPServer:
        """
        Create a new MCP server instance.
        
        Args:
            server_type: Type of server to create
            name: Unique name for the server instance
            config: Server configuration
            
        Returns:
            MCPServer: Created server instance
        """
        if server_type not in self._server_classes:
            raise MCPError(
                f"Unknown server type: {server_type}",
                error_code="UNKNOWN_SERVER_TYPE",
                details={"server_type": server_type}
            )
        
        if name in self._running_servers:
            raise MCPError(
                f"Server with name '{name}' already exists",
                error_code="SERVER_NAME_EXISTS",
                details={"server_name": name}
            )
        
        server_class = self._server_classes[server_type]
        config = config or {}
        
        # Create server instance
        server = server_class(name=name, **config)
        
        self.logger.info(f"Created MCP server '{name}' of type '{server_type}'")
        return server
    
    async def start_server(self, server: MCPServer) -> None:
        """Start a server and add it to running servers."""
        await server.start()
        self._running_servers[server.name] = server
        self.logger.info(f"Started MCP server '{server.name}'")
    
    async def stop_server(self, name: str) -> bool:
        """Stop a running server."""
        if name not in self._running_servers:
            return False
        
        server = self._running_servers[name]
        await server.stop()
        del self._running_servers[name]
        
        self.logger.info(f"Stopped MCP server '{name}'")
        return True
    
    async def stop_all_servers(self) -> None:
        """Stop all running servers."""
        server_names = list(self._running_servers.keys())
        for name in server_names:
            await self.stop_server(name)
        
        self.logger.info("Stopped all MCP servers")
    
    def get_running_servers(self) -> Dict[str, MCPServer]:
        """Get all currently running servers."""
        return self._running_servers.copy()
    
    def get_server(self, name: str) -> Optional[MCPServer]:
        """Get a specific running server by name."""
        return self._running_servers.get(name)
    
    async def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        """Perform health check on all running servers."""
        results = {}
        
        for name, server in self._running_servers.items():
            try:
                request = MCPRequest(method="health_check")
                response = await server.process_request(request)
                results[name] = response.result if response.success else response.error
            except Exception as e:
                results[name] = {
                    "healthy": False,
                    "error": str(e),
                    "check_timestamp": datetime.now(timezone.utc).isoformat()
                }
        
        return results


# Export key components
__all__ = [
    "MCPServer",
    "MCPServerStatus",
    "MCPToolCapability", 
    "MCPRequest",
    "MCPResponse",
    "MCPError",
    "MCPServerMetrics",
    "ContextInjector",
    "DefaultContextInjector",
    "MCPServerFactory"
]