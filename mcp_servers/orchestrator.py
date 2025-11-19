"""
MCP Server Orchestrator

This module provides comprehensive orchestration for all MCP servers:
- Server discovery and registration
- Inter-server messaging and communication
- Health monitoring and status management
- Load balancing and failover
- Unified request routing
- Server lifecycle management
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union, Type, Set
import json
import uuid
from enum import Enum, auto

from pydantic import BaseModel, Field

from .base import (
    MCPServer, MCPServerFactory, MCPRequest, MCPResponse, 
    MCPError, MCPServerStatus, MCPToolCapability
)
from .context_manager import MCPContextManager
from .social_tools import SocialToolsServer
from .pm_tools import ProjectManagementServer
from .telegram_tools import TelegramToolsServer
from .development_tools import DevelopmentToolsServer


class ServerHealth(Enum):
    """Server health status enumeration."""
    
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class MessagePriority(Enum):
    """Inter-server message priority levels."""
    
    LOW = auto()
    NORMAL = auto()
    HIGH = auto()
    CRITICAL = auto()


class InterServerMessage(BaseModel):
    """Inter-server communication message."""
    
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique message ID")
    from_server: str = Field(..., description="Source server name")
    to_server: str = Field(..., description="Destination server name")
    message_type: str = Field(..., description="Message type")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Message payload")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL, description="Message priority")
    
    # Timing
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Message expiration time")
    
    # Delivery tracking
    delivered: bool = Field(default=False, description="Whether message was delivered")
    delivery_attempts: int = Field(default=0, description="Number of delivery attempts")
    max_attempts: int = Field(default=3, description="Maximum delivery attempts")
    
    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        }}


class ServerRegistration(BaseModel):
    """Server registration information."""
    
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
            set: lambda v: list(v)
        }
    }
    
    server_name: str = Field(..., description="Server name")
    server_type: str = Field(..., description="Server type/class")
    version: str = Field(..., description="Server version")
    description: str = Field(default="", description="Server description")
    
    # Server instance reference (excluded from serialization)
    server_instance: Optional[MCPServer] = Field(None, description="Server instance reference", exclude=True)
    
    # Capabilities
    capabilities: List[MCPToolCapability] = Field(default_factory=list, description="Server capabilities")
    tags: Set[str] = Field(default_factory=set, description="Server tags")
    
    # Health and status
    health_status: ServerHealth = Field(default=ServerHealth.UNKNOWN, description="Current health status")
    last_health_check: Optional[datetime] = Field(None, description="Last health check time")
    
    # Registration metadata
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Configuration
    config: Dict[str, Any] = Field(default_factory=dict, description="Server configuration")


class RoutingRule(BaseModel):
    """Request routing rule."""
    
    rule_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Rule ID")
    name: str = Field(..., description="Rule name")
    condition: Dict[str, Any] = Field(..., description="Routing condition")
    target_servers: List[str] = Field(..., description="Target server names")
    priority: int = Field(default=100, description="Rule priority (lower = higher priority)")
    enabled: bool = Field(default=True, description="Whether rule is enabled")
    
    # Load balancing
    load_balance_strategy: str = Field(default="round_robin", description="Load balancing strategy")
    failover_enabled: bool = Field(default=True, description="Enable failover to other servers")
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: Optional[datetime] = Field(None, description="Last time rule was used")
    
    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat()
        }}


class MCPOrchestrator:
    """
    Comprehensive MCP server orchestrator providing:
    - Server discovery and registration
    - Health monitoring and management
    - Request routing and load balancing
    - Inter-server communication
    - Unified API gateway
    """
    
    def __init__(
        self,
        name: str = "mcp_orchestrator",
        health_check_interval: int = 30,
        message_processing_interval: int = 1,
        enable_inter_server_messaging: bool = True,
        enable_load_balancing: bool = True,
        logger: Optional[logging.Logger] = None
    ):
        self.name = name
        self.health_check_interval = health_check_interval
        self.message_processing_interval = message_processing_interval
        self.enable_inter_server_messaging = enable_inter_server_messaging
        self.enable_load_balancing = enable_load_balancing
        self.logger = logger or logging.getLogger("mcp.orchestrator")
        
        # Server management
        self.server_factory = MCPServerFactory()
        self._registered_servers: Dict[str, ServerRegistration] = {}
        self._server_load_counters: Dict[str, int] = {}
        
        # Context management
        self.context_manager = MCPContextManager()
        
        # Routing
        self._routing_rules: Dict[str, RoutingRule] = {}
        self._default_routing_enabled = True
        
        # Inter-server messaging
        self._message_queue: List[InterServerMessage] = []
        self._message_handlers: Dict[str, callable] = {}
        
        # Monitoring
        self._health_check_history: Dict[str, List[Dict[str, Any]]] = {}
        self._orchestrator_stats = {
            "requests_routed": 0,
            "messages_processed": 0,
            "health_checks_performed": 0,
            "servers_registered": 0,
            "start_time": time.time()
        }
        
        # Background tasks
        self._health_check_task: Optional[asyncio.Task] = None
        self._message_processor_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Register built-in server types
        self._register_builtin_servers()
        
        self.logger.info("MCP Orchestrator initialized")
    
    async def start(self) -> None:
        """Start the orchestrator and all background tasks."""
        try:
            self._running = True
            
            # Start background tasks
            self._health_check_task = asyncio.create_task(self._health_check_loop())
            if self.enable_inter_server_messaging:
                self._message_processor_task = asyncio.create_task(self._message_processor_loop())
            
            # Set up default routing rules
            await self._setup_default_routing()
            
            self.logger.info("MCP Orchestrator started")
            
        except Exception as e:
            self.logger.error(f"Failed to start MCP Orchestrator: {str(e)}")
            raise MCPError(
                f"Orchestrator startup failed: {str(e)}",
                error_code="ORCHESTRATOR_STARTUP_ERROR",
                recoverable=False
            )
    
    async def stop(self) -> None:
        """Stop the orchestrator and cleanup resources."""
        try:
            self._running = False
            
            # Cancel background tasks
            if self._health_check_task:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass
            
            if self._message_processor_task:
                self._message_processor_task.cancel()
                try:
                    await self._message_processor_task
                except asyncio.CancelledError:
                    pass
            
            # Stop all registered servers
            for server_name in list(self._registered_servers.keys()):
                await self.unregister_server(server_name)
            
            self.logger.info("MCP Orchestrator stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping MCP Orchestrator: {str(e)}")
    
    def _register_builtin_servers(self) -> None:
        """Register built-in server types with the factory."""
        self.server_factory.register_server_class("social_tools", SocialToolsServer)
        self.server_factory.register_server_class("project_management", ProjectManagementServer)
        self.server_factory.register_server_class("telegram_tools", TelegramToolsServer)
        self.server_factory.register_server_class("development_tools", DevelopmentToolsServer)
        
        self.logger.info("Registered built-in MCP server types")
    
    # Server Management
    
    async def register_server(
        self,
        server_name: str,
        server_type: str,
        config: Dict[str, Any] = None,
        auto_start: bool = True
    ) -> ServerRegistration:
        """Register and optionally start an MCP server."""
        if server_name in self._registered_servers:
            raise MCPError(
                f"Server '{server_name}' is already registered",
                error_code="SERVER_ALREADY_REGISTERED",
                details={"server_name": server_name}
            )
        
        try:
            # Create server instance
            server_instance = await self.server_factory.create_server(
                server_type, server_name, config or {}
            )
            
            # Create registration
            registration = ServerRegistration(
                server_name=server_name,
                server_type=server_type,
                version=server_instance.version,
                description=server_instance.description,
                server_instance=server_instance,
                config=config or {}
            )
            
            # Start server if requested
            if auto_start:
                await self.server_factory.start_server(server_instance)
                registration.capabilities = server_instance.get_capabilities()
                registration.health_status = ServerHealth.HEALTHY
                
                # Update server context with orchestrator information
                server_instance.update_security_context({
                    "orchestrator_name": self.name,
                    "registration_time": registration.registered_at.isoformat()
                })
            
            # Register server
            self._registered_servers[server_name] = registration
            self._server_load_counters[server_name] = 0
            self._orchestrator_stats["servers_registered"] += 1
            
            self.logger.info(f"Registered MCP server: {server_name} ({server_type})")
            
            return registration
            
        except Exception as e:
            self.logger.error(f"Failed to register server '{server_name}': {str(e)}")
            raise MCPError(
                f"Server registration failed: {str(e)}",
                error_code="SERVER_REGISTRATION_ERROR",
                details={"server_name": server_name, "server_type": server_type, "error": str(e)}
            )
    
    async def unregister_server(self, server_name: str) -> bool:
        """Unregister and stop an MCP server."""
        if server_name not in self._registered_servers:
            return False
        
        try:
            registration = self._registered_servers[server_name]
            
            # Stop server if it has an instance
            if registration.server_instance:
                await self.server_factory.stop_server(server_name)
            
            # Remove from registrations
            del self._registered_servers[server_name]
            self._server_load_counters.pop(server_name, None)
            
            # Clean up health check history
            self._health_check_history.pop(server_name, None)
            
            self.logger.info(f"Unregistered MCP server: {server_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to unregister server '{server_name}': {str(e)}")
            return False
    
    def list_servers(
        self, 
        server_type: str = None, 
        health_status: ServerHealth = None,
        tags: Set[str] = None
    ) -> List[ServerRegistration]:
        """List registered servers with optional filtering."""
        servers = []
        
        for registration in self._registered_servers.values():
            # Apply filters
            if server_type and registration.server_type != server_type:
                continue
            if health_status and registration.health_status != health_status:
                continue
            if tags and not tags.issubset(registration.tags):
                continue
            
            servers.append(registration)
        
        return servers
    
    def get_server(self, server_name: str) -> Optional[ServerRegistration]:
        """Get server registration by name."""
        return self._registered_servers.get(server_name)
    
    # Request Routing
    
    async def route_request(self, request: MCPRequest) -> MCPResponse:
        """Route a request to the appropriate server(s)."""
        try:
            self._orchestrator_stats["requests_routed"] += 1
            
            # Find target servers using routing rules
            target_servers = await self._find_target_servers(request)
            
            if not target_servers:
                return MCPResponse(
                    id=request.id,
                    success=False,
                    error={
                        "code": "NO_SERVERS_AVAILABLE",
                        "message": "No servers available to handle this request",
                        "method": request.method
                    }
                )
            
            # Apply load balancing if multiple servers available
            if len(target_servers) > 1 and self.enable_load_balancing:
                selected_server = await self._select_server_for_load_balancing(target_servers, request)
            else:
                selected_server = target_servers[0]
            
            # Get server registration
            registration = self._registered_servers.get(selected_server)
            if not registration or not registration.server_instance:
                return MCPResponse(
                    id=request.id,
                    success=False,
                    error={
                        "code": "SERVER_UNAVAILABLE",
                        "message": f"Server '{selected_server}' is not available",
                        "server_name": selected_server
                    }
                )
            
            # Update server activity
            registration.last_activity = datetime.now(timezone.utc)
            self._server_load_counters[selected_server] += 1
            
            # Process request through context manager
            enriched_context = await self.context_manager.inject_context(
                request, {"orchestrator": self.name, "target_server": selected_server}
            )
            
            # Validate context
            if not await self.context_manager.validate_context(enriched_context):
                return MCPResponse(
                    id=request.id,
                    success=False,
                    error={
                        "code": "CONTEXT_VALIDATION_FAILED",
                        "message": "Request context validation failed"
                    }
                )
            
            # Forward request to server
            response = await registration.server_instance.process_request(request)
            
            # Add orchestrator metadata to response
            if not response.metadata:
                response.metadata = {}
            response.metadata.update({
                "routed_by": self.name,
                "target_server": selected_server,
                "routing_timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            self.logger.debug(f"Routed request {request.id} to server {selected_server}")
            
            return response
            
        except Exception as e:
            self.logger.error(f"Request routing failed: {str(e)}")
            return MCPResponse(
                id=request.id,
                success=False,
                error={
                    "code": "ROUTING_ERROR",
                    "message": f"Request routing failed: {str(e)}",
                    "details": {"error": str(e)}
                }
            )
    
    async def _find_target_servers(self, request: MCPRequest) -> List[str]:
        """Find target servers for a request using routing rules."""
        # Check routing rules first
        for rule in sorted(self._routing_rules.values(), key=lambda r: r.priority):
            if not rule.enabled:
                continue
            
            if await self._evaluate_routing_condition(request, rule.condition):
                rule.last_used = datetime.now(timezone.utc)
                return [s for s in rule.target_servers if s in self._registered_servers]
        
        # Fall back to default routing
        if self._default_routing_enabled:
            return await self._default_routing(request)
        
        return []
    
    async def _evaluate_routing_condition(self, request: MCPRequest, condition: Dict[str, Any]) -> bool:
        """Evaluate routing condition against request."""
        condition_type = condition.get("type")
        
        if condition_type == "method":
            return request.method in condition.get("values", [])
        elif condition_type == "method_prefix":
            prefix = condition.get("prefix", "")
            return request.method.startswith(prefix)
        elif condition_type == "parameter":
            param_name = condition.get("parameter")
            expected_value = condition.get("value")
            return request.params.get(param_name) == expected_value
        elif condition_type == "context":
            context_key = condition.get("key")
            expected_value = condition.get("value")
            return request.context and request.context.get(context_key) == expected_value
        elif condition_type == "always":
            return True
        else:
            return False
    
    async def _default_routing(self, request: MCPRequest) -> List[str]:
        """Default routing logic based on method patterns."""
        method = request.method
        
        # Route based on method prefixes
        if method.startswith("github_") or method.startswith("linear_") or method.startswith("create_documentation"):
            return [s for s in self._registered_servers.keys() 
                   if self._registered_servers[s].server_type == "project_management"]
        
        elif method.startswith("telegram_"):
            return [s for s in self._registered_servers.keys()
                   if self._registered_servers[s].server_type == "telegram_tools"]
        
        elif method.startswith("execute_") or method.startswith("profile_") or method.startswith("run_tests"):
            return [s for s in self._registered_servers.keys()
                   if self._registered_servers[s].server_type == "development_tools"]
        
        elif method in ["web_search", "create_calendar_event", "generate_content", "search_knowledge_base"]:
            return [s for s in self._registered_servers.keys()
                   if self._registered_servers[s].server_type == "social_tools"]
        
        # Return all healthy servers as fallback
        return [s for s, reg in self._registered_servers.items() 
               if reg.health_status == ServerHealth.HEALTHY]
    
    async def _select_server_for_load_balancing(
        self, 
        target_servers: List[str], 
        request: MCPRequest
    ) -> str:
        """Select server for load balancing."""
        healthy_servers = [
            s for s in target_servers 
            if self._registered_servers[s].health_status == ServerHealth.HEALTHY
        ]
        
        if not healthy_servers:
            # Fall back to all target servers if none are healthy
            healthy_servers = target_servers
        
        # Round-robin load balancing
        loads = [(s, self._server_load_counters.get(s, 0)) for s in healthy_servers]
        loads.sort(key=lambda x: x[1])  # Sort by load (ascending)
        
        return loads[0][0]  # Return server with lowest load
    
    # Routing Rules Management
    
    def add_routing_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule."""
        self._routing_rules[rule.rule_id] = rule
        self.logger.info(f"Added routing rule: {rule.name}")
    
    def remove_routing_rule(self, rule_id: str) -> bool:
        """Remove a routing rule."""
        if rule_id in self._routing_rules:
            rule_name = self._routing_rules[rule_id].name
            del self._routing_rules[rule_id]
            self.logger.info(f"Removed routing rule: {rule_name}")
            return True
        return False
    
    def list_routing_rules(self) -> List[RoutingRule]:
        """List all routing rules."""
        return list(self._routing_rules.values())
    
    async def _setup_default_routing(self) -> None:
        """Set up default routing rules."""
        # GitHub tools routing
        github_rule = RoutingRule(
            name="GitHub Tools",
            condition={"type": "method_prefix", "prefix": "github_"},
            target_servers=[s for s in self._registered_servers.keys() 
                          if self._registered_servers[s].server_type == "project_management"],
            priority=10
        )
        if github_rule.target_servers:
            self.add_routing_rule(github_rule)
        
        # Telegram tools routing
        telegram_rule = RoutingRule(
            name="Telegram Tools",
            condition={"type": "method_prefix", "prefix": "telegram_"},
            target_servers=[s for s in self._registered_servers.keys()
                          if self._registered_servers[s].server_type == "telegram_tools"],
            priority=10
        )
        if telegram_rule.target_servers:
            self.add_routing_rule(telegram_rule)
        
        # Development tools routing
        dev_rule = RoutingRule(
            name="Development Tools",
            condition={"type": "method_prefix", "prefix": "execute_"},
            target_servers=[s for s in self._registered_servers.keys()
                          if self._registered_servers[s].server_type == "development_tools"],
            priority=10
        )
        if dev_rule.target_servers:
            self.add_routing_rule(dev_rule)
    
    # Health Monitoring
    
    async def _health_check_loop(self) -> None:
        """Background health check loop."""
        while self._running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.health_check_interval)
            except Exception as e:
                self.logger.error(f"Health check loop error: {str(e)}")
                await asyncio.sleep(5)  # Brief pause on error
    
    async def _perform_health_checks(self) -> None:
        """Perform health checks on all registered servers."""
        for server_name, registration in self._registered_servers.items():
            if not registration.server_instance:
                continue
            
            try:
                # Perform health check
                health_request = MCPRequest(method="health_check")
                response = await registration.server_instance.process_request(health_request)
                
                # Update health status
                if response.success and response.result:
                    health_data = response.result
                    is_healthy = health_data.get("healthy", False)
                    health_score = health_data.get("health_score", 0)
                    
                    if is_healthy and health_score >= 8.0:
                        registration.health_status = ServerHealth.HEALTHY
                    elif is_healthy and health_score >= 5.0:
                        registration.health_status = ServerHealth.DEGRADED
                    else:
                        registration.health_status = ServerHealth.UNHEALTHY
                else:
                    registration.health_status = ServerHealth.UNHEALTHY
                
                registration.last_health_check = datetime.now(timezone.utc)
                
                # Store health check history
                if server_name not in self._health_check_history:
                    self._health_check_history[server_name] = []
                
                self._health_check_history[server_name].append({
                    "timestamp": registration.last_health_check.isoformat(),
                    "status": registration.health_status.value,
                    "response": response.result if response.success else None
                })
                
                # Limit history size
                if len(self._health_check_history[server_name]) > 100:
                    self._health_check_history[server_name] = self._health_check_history[server_name][-50:]
                
                self._orchestrator_stats["health_checks_performed"] += 1
                
            except Exception as e:
                self.logger.warning(f"Health check failed for server '{server_name}': {str(e)}")
                registration.health_status = ServerHealth.UNKNOWN
                registration.last_health_check = datetime.now(timezone.utc)
    
    def get_health_summary(self) -> Dict[str, Any]:
        """Get overall health summary of all servers."""
        summary = {
            "total_servers": len(self._registered_servers),
            "healthy_servers": 0,
            "degraded_servers": 0,
            "unhealthy_servers": 0,
            "unknown_servers": 0,
            "servers": {}
        }
        
        for server_name, registration in self._registered_servers.items():
            status = registration.health_status
            
            if status == ServerHealth.HEALTHY:
                summary["healthy_servers"] += 1
            elif status == ServerHealth.DEGRADED:
                summary["degraded_servers"] += 1
            elif status == ServerHealth.UNHEALTHY:
                summary["unhealthy_servers"] += 1
            else:
                summary["unknown_servers"] += 1
            
            summary["servers"][server_name] = {
                "status": status.value,
                "last_check": registration.last_health_check.isoformat() if registration.last_health_check else None,
                "last_activity": registration.last_activity.isoformat(),
                "load_count": self._server_load_counters.get(server_name, 0)
            }
        
        return summary
    
    # Inter-Server Messaging
    
    async def send_message(
        self,
        from_server: str,
        to_server: str,
        message_type: str,
        payload: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl_seconds: int = None
    ) -> str:
        """Send a message between servers."""
        if not self.enable_inter_server_messaging:
            raise MCPError(
                "Inter-server messaging is disabled",
                error_code="MESSAGING_DISABLED"
            )
        
        if to_server not in self._registered_servers:
            raise MCPError(
                f"Target server '{to_server}' not found",
                error_code="TARGET_SERVER_NOT_FOUND",
                details={"target_server": to_server}
            )
        
        message = InterServerMessage(
            from_server=from_server,
            to_server=to_server,
            message_type=message_type,
            payload=payload,
            priority=priority
        )
        
        if ttl_seconds:
            message.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        
        # Insert message in priority order
        inserted = False
        for i, existing_message in enumerate(self._message_queue):
            if message.priority.value > existing_message.priority.value:
                self._message_queue.insert(i, message)
                inserted = True
                break
        
        if not inserted:
            self._message_queue.append(message)
        
        self.logger.debug(f"Queued message {message.message_id}: {from_server} -> {to_server}")
        
        return message.message_id
    
    async def _message_processor_loop(self) -> None:
        """Background message processing loop."""
        while self._running:
            try:
                await self._process_messages()
                await asyncio.sleep(self.message_processing_interval)
            except Exception as e:
                self.logger.error(f"Message processor error: {str(e)}")
                await asyncio.sleep(1)
    
    async def _process_messages(self) -> None:
        """Process queued inter-server messages."""
        processed_messages = []
        current_time = datetime.now(timezone.utc)
        
        for message in self._message_queue[:]:  # Copy to avoid modification during iteration
            try:
                # Check if message has expired
                if message.expires_at and current_time > message.expires_at:
                    processed_messages.append(message)
                    self.logger.warning(f"Message {message.message_id} expired")
                    continue
                
                # Check delivery attempts
                if message.delivery_attempts >= message.max_attempts:
                    processed_messages.append(message)
                    self.logger.error(f"Message {message.message_id} exceeded max delivery attempts")
                    continue
                
                # Attempt delivery
                success = await self._deliver_message(message)
                
                if success:
                    message.delivered = True
                    processed_messages.append(message)
                    self._orchestrator_stats["messages_processed"] += 1
                    self.logger.debug(f"Delivered message {message.message_id}")
                else:
                    message.delivery_attempts += 1
                    if message.delivery_attempts >= message.max_attempts:
                        processed_messages.append(message)
                        self.logger.error(f"Failed to deliver message {message.message_id}")
                
            except Exception as e:
                message.delivery_attempts += 1
                self.logger.error(f"Error processing message {message.message_id}: {str(e)}")
        
        # Remove processed messages
        for message in processed_messages:
            if message in self._message_queue:
                self._message_queue.remove(message)
    
    async def _deliver_message(self, message: InterServerMessage) -> bool:
        """Deliver a message to the target server."""
        target_registration = self._registered_servers.get(message.to_server)
        
        if not target_registration or not target_registration.server_instance:
            return False
        
        # Check if server has a message handler for this message type
        handler_name = f"_handle_message_{message.message_type}"
        
        if hasattr(target_registration.server_instance, handler_name):
            handler = getattr(target_registration.server_instance, handler_name)
            try:
                await handler(message)
                return True
            except Exception as e:
                self.logger.error(f"Message handler error: {str(e)}")
                return False
        else:
            # No specific handler, log and consider delivered
            self.logger.debug(f"No handler for message type '{message.message_type}' on server '{message.to_server}'")
            return True
    
    # Statistics and Monitoring
    
    def get_orchestrator_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        uptime = time.time() - self._orchestrator_stats["start_time"]
        
        stats = self._orchestrator_stats.copy()
        stats.update({
            "uptime_seconds": uptime,
            "message_queue_size": len(self._message_queue),
            "routing_rules_count": len(self._routing_rules),
            "health_check_history_size": sum(len(history) for history in self._health_check_history.values())
        })
        
        return stats
    
    def get_server_capabilities(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get capabilities of all registered servers."""
        capabilities = {}
        
        for server_name, registration in self._registered_servers.items():
            capabilities[server_name] = [cap.dict() for cap in registration.capabilities]
        
        return capabilities
    
    # Context manager for orchestrator lifecycle
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()


# Export the orchestrator class
__all__ = ["MCPOrchestrator", "ServerRegistration", "RoutingRule", "InterServerMessage", "ServerHealth", "MessagePriority"]