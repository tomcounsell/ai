"""
System Integration Layer for AI Rebuild
========================================

This module provides comprehensive system integration connecting all components:
- Database ↔ Agent integration with persistent context management
- Agent ↔ Tools integration with dynamic tool registration and orchestration  
- Tools ↔ MCP Servers integration with distributed service coordination
- Pipeline ↔ Telegram integration with real-time message processing
- Full system integration with health monitoring and stability assurance

Architecture:
- Component wiring with dependency injection
- Graceful startup/shutdown sequences
- Health checking and auto-recovery
- Component communication protocols
- System stability monitoring
- Performance optimization
"""

import asyncio
import logging
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union, Callable, Set
from enum import Enum
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import json
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

# Core components
from utilities.database import DatabaseManager
from agents.valor.agent import ValorAgent
from agents.tool_registry import ToolRegistry
from mcp_servers.orchestrator import MCPOrchestrator
from integrations.telegram.unified_processor import UnifiedProcessor
from integrations.telegram.client import TelegramClient
from tools.base import ToolImplementation
from config.settings import settings
from utilities.logging_config import setup_logging


logger = logging.getLogger(__name__)


class ComponentState(Enum):
    """Component lifecycle states"""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    DEGRADED = "degraded"


class HealthStatus(Enum):
    """System health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded" 
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"


@dataclass
class ComponentHealth:
    """Health status for system components"""
    name: str
    state: ComponentState
    health: HealthStatus
    last_check: datetime
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    performance_metrics: Dict[str, float] = field(default_factory=dict)


@dataclass
class IntegrationMetrics:
    """System integration performance metrics"""
    startup_time_ms: float = 0.0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    peak_memory_mb: float = 0.0
    component_uptime: Dict[str, float] = field(default_factory=dict)
    error_rates: Dict[str, float] = field(default_factory=dict)


class SystemEvent(BaseModel):
    """System-wide event for monitoring and debugging"""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = Field(..., description="Event type")
    component: str = Field(..., description="Source component")
    severity: str = Field(..., description="Event severity (info/warning/error/critical)")
    message: str = Field(..., description="Event message")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SystemIntegrator:
    """
    Comprehensive system integration orchestrator that manages all component
    connections, health monitoring, and system-wide coordination.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        enable_monitoring: bool = True,
        health_check_interval: int = 30,
        auto_recovery: bool = True
    ):
        """
        Initialize the system integrator.
        
        Args:
            config: System configuration dictionary
            enable_monitoring: Enable health monitoring and metrics
            health_check_interval: Health check interval in seconds
            auto_recovery: Enable automatic component recovery
        """
        self.config = config or {}
        self.enable_monitoring = enable_monitoring
        self.health_check_interval = health_check_interval
        self.auto_recovery = auto_recovery
        
        # Component instances
        self.database: Optional[DatabaseManager] = None
        self.agent: Optional[ValorAgent] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.mcp_orchestrator: Optional[MCPOrchestrator] = None
        self.telegram_processor: Optional[UnifiedProcessor] = None
        self.telegram_client: Optional[TelegramClient] = None
        
        # System state
        self.system_state = ComponentState.UNINITIALIZED
        self.startup_time: Optional[datetime] = None
        self.shutdown_requested = False
        
        # Health monitoring
        self.component_health: Dict[str, ComponentHealth] = {}
        self.system_events: List[SystemEvent] = []
        self.metrics = IntegrationMetrics()
        
        # Background tasks
        self.health_monitor_task: Optional[asyncio.Task] = None
        self.metrics_collector_task: Optional[asyncio.Task] = None
        
        # Event handlers
        self.event_handlers: Dict[str, List[Callable]] = {}
        
        logger.info("SystemIntegrator initialized")
    
    async def initialize(self) -> None:
        """Initialize all system components in proper dependency order."""
        if self.system_state != ComponentState.UNINITIALIZED:
            logger.warning(f"System already initialized (state: {self.system_state})")
            return
        
        self.system_state = ComponentState.INITIALIZING
        self.startup_time = datetime.now(timezone.utc)
        start_time = time.perf_counter()
        
        try:
            await self._emit_event("system_init_start", "system", "info", "Starting system initialization")
            
            # Phase 1: Initialize Database
            await self._initialize_database()
            
            # Phase 2: Initialize Tool Registry and Tools
            await self._initialize_tools()
            
            # Phase 3: Initialize MCP Orchestrator
            await self._initialize_mcp_orchestrator()
            
            # Phase 4: Initialize Agent with Tools
            await self._initialize_agent()
            
            # Phase 5: Initialize Telegram Components
            await self._initialize_telegram()
            
            # Phase 6: Wire all integrations
            await self._wire_integrations()
            
            # Phase 7: Start monitoring
            if self.enable_monitoring:
                await self._start_monitoring()
            
            # Calculate startup metrics
            self.metrics.startup_time_ms = (time.perf_counter() - start_time) * 1000
            self.system_state = ComponentState.READY
            
            await self._emit_event(
                "system_init_complete", "system", "info",
                f"System initialization complete in {self.metrics.startup_time_ms:.1f}ms"
            )
            
            logger.info(f"System initialization complete in {self.metrics.startup_time_ms:.1f}ms")
            
        except Exception as e:
            self.system_state = ComponentState.ERROR
            await self._emit_event(
                "system_init_error", "system", "critical",
                f"System initialization failed: {str(e)}"
            )
            logger.error(f"System initialization failed: {str(e)}", exc_info=True)
            raise
    
    async def _initialize_database(self) -> None:
        """Initialize database component."""
        logger.info("Initializing database component...")
        
        try:
            self.database = DatabaseManager()
            await self.database.initialize()
            
            self._update_component_health(
                "database", ComponentState.READY, HealthStatus.HEALTHY
            )
            
            logger.info("Database component initialized successfully")
            
        except Exception as e:
            self._update_component_health(
                "database", ComponentState.ERROR, HealthStatus.CRITICAL,
                errors=[str(e)]
            )
            raise
    
    async def _initialize_tools(self) -> None:
        """Initialize tool registry and available tools."""
        logger.info("Initializing tools and tool registry...")
        
        try:
            self.tool_registry = ToolRegistry()
            
            # Register built-in tools
            from tools.search_tool import SearchTool
            from tools.code_execution_tool import CodeExecutionTool
            from tools.image_analysis_tool import ImageAnalysisTool
            from tools.image_generation_tool import ImageGenerationTool
            from tools.knowledge_search import KnowledgeSearchTool
            
            # Register tools with registry
            tools_to_register = [
                SearchTool(),
                CodeExecutionTool(), 
                ImageAnalysisTool(),
                ImageGenerationTool(),
                KnowledgeSearchTool()
            ]
            
            for tool in tools_to_register:
                self.tool_registry.register_tool(tool)
                logger.debug(f"Registered tool: {tool.name}")
            
            self._update_component_health(
                "tool_registry", ComponentState.READY, HealthStatus.HEALTHY,
                details={"registered_tools": len(tools_to_register)}
            )
            
            logger.info(f"Tool registry initialized with {len(tools_to_register)} tools")
            
        except Exception as e:
            self._update_component_health(
                "tool_registry", ComponentState.ERROR, HealthStatus.CRITICAL,
                errors=[str(e)]
            )
            raise
    
    async def _initialize_mcp_orchestrator(self) -> None:
        """Initialize MCP orchestrator and servers."""
        logger.info("Initializing MCP orchestrator...")
        
        try:
            self.mcp_orchestrator = MCPOrchestrator(
                name="ai_rebuild_orchestrator",
                enable_inter_server_messaging=True,
                enable_load_balancing=True
            )
            
            await self.mcp_orchestrator.start()
            
            # Register MCP servers
            server_configs = [
                ("social_tools", "social_tools", {"max_requests_per_hour": 1000}),
                ("pm_tools", "project_management", {"github_integration": True}),
                ("telegram_tools", "telegram_tools", {"bot_token": self.config.get("telegram_bot_token")}),
                ("dev_tools", "development_tools", {"enable_code_execution": True})
            ]
            
            for server_name, server_type, config in server_configs:
                try:
                    await self.mcp_orchestrator.register_server(
                        server_name, server_type, config
                    )
                    logger.debug(f"Registered MCP server: {server_name}")
                except Exception as e:
                    logger.warning(f"Failed to register MCP server {server_name}: {e}")
            
            self._update_component_health(
                "mcp_orchestrator", ComponentState.READY, HealthStatus.HEALTHY,
                details={"registered_servers": len(server_configs)}
            )
            
            logger.info("MCP orchestrator initialized successfully")
            
        except Exception as e:
            self._update_component_health(
                "mcp_orchestrator", ComponentState.ERROR, HealthStatus.CRITICAL,
                errors=[str(e)]
            )
            raise
    
    async def _initialize_agent(self) -> None:
        """Initialize Valor agent with tool integration."""
        logger.info("Initializing Valor agent...")
        
        try:
            self.agent = ValorAgent(
                model=self.config.get("agent_model", "openai:gpt-4"),
                max_context_tokens=self.config.get("max_context_tokens", 100_000),
                debug=self.config.get("debug", False)
            )
            
            # Wire agent to database for context persistence
            if self.database:
                # Custom context persistence integration
                original_create_context = self.agent.create_context
                
                async def persistent_create_context(chat_id, user_name, workspace=None, metadata=None):
                    context = await original_create_context(chat_id, user_name, workspace, metadata)
                    # Store context metadata in database
                    await self.database.add_chat_message(
                        project_id=None,
                        session_id=chat_id,
                        role="system",
                        content=f"Context created for {user_name}",
                        metadata={"context_type": "initialization"}
                    )
                    return context
                
                self.agent.create_context = persistent_create_context
            
            # Wire agent to tool registry
            if self.tool_registry:
                # Register all available tools with the agent
                for tool_name, tool_func in self.tool_registry.get_available_tools().items():
                    self.agent.register_tool(tool_func)
                    logger.debug(f"Registered tool with agent: {tool_name}")
            
            self._update_component_health(
                "valor_agent", ComponentState.READY, HealthStatus.HEALTHY,
                details={"tools_registered": len(self.tool_registry.get_available_tools()) if self.tool_registry else 0}
            )
            
            logger.info("Valor agent initialized successfully")
            
        except Exception as e:
            self._update_component_health(
                "valor_agent", ComponentState.ERROR, HealthStatus.CRITICAL,
                errors=[str(e)]
            )
            raise
    
    async def _initialize_telegram(self) -> None:
        """Initialize Telegram client and processor."""
        logger.info("Initializing Telegram components...")
        
        try:
            # Initialize Telegram client
            if self.config.get("telegram_api_id") and self.config.get("telegram_api_hash"):
                self.telegram_client = TelegramClient(
                    api_id=self.config["telegram_api_id"],
                    api_hash=self.config["telegram_api_hash"],
                    bot_token=self.config.get("telegram_bot_token"),
                    session_name=self.config.get("telegram_session", "ai_rebuild")
                )
            
            # Initialize unified processor
            self.telegram_processor = UnifiedProcessor(
                performance_target_ms=self.config.get("telegram_response_target", 2000),
                enable_metrics=True,
                enable_parallel_processing=True,
                max_concurrent_requests=self.config.get("telegram_max_concurrent", 10)
            )
            
            self._update_component_health(
                "telegram", ComponentState.READY, HealthStatus.HEALTHY
            )
            
            logger.info("Telegram components initialized successfully")
            
        except Exception as e:
            self._update_component_health(
                "telegram", ComponentState.ERROR, HealthStatus.CRITICAL,
                errors=[str(e)]
            )
            raise
    
    async def _wire_integrations(self) -> None:
        """Wire all component integrations."""
        logger.info("Wiring component integrations...")
        
        try:
            # Wire Telegram processor to agent
            if self.telegram_processor and self.agent:
                # Custom message handler that uses our agent
                original_orchestrator = self.telegram_processor.agent_orchestrator
                
                class IntegratedOrchestrator:
                    def __init__(self, valor_agent: ValorAgent):
                        self.valor_agent = valor_agent
                    
                    async def orchestrate(self, message, context, message_type, route_metadata):
                        # Convert Telegram context to agent format
                        chat_id = str(context.chat_id)
                        user_name = context.user_name or "Unknown"
                        message_text = message.text if hasattr(message, 'text') else str(message)
                        
                        # Process through Valor agent
                        response = await self.valor_agent.process_message(
                            message=message_text,
                            chat_id=chat_id,
                            user_name=user_name,
                            metadata={"message_type": message_type.value, "route_metadata": route_metadata}
                        )
                        
                        # Convert agent response to expected format
                        from integrations.telegram.components.agent_orchestrator import AgentResult
                        return AgentResult(
                            agent_name="valor",
                            response_content=response.content,
                            tools_used=response.tools_used,
                            metadata=response.metadata,
                            confidence=0.9
                        )
                    
                    async def get_status(self):
                        return {"status": "ready", "agent": "valor"}
                    
                    async def shutdown(self):
                        pass
                
                self.telegram_processor.agent_orchestrator = IntegratedOrchestrator(self.agent)
            
            # Wire tools to MCP servers
            if self.tool_registry and self.mcp_orchestrator:
                # Create bridge between tools and MCP servers
                async def mcp_tool_bridge(tool_name: str, **kwargs):
                    """Bridge tool calls to MCP servers"""
                    # Route tool calls to appropriate MCP servers
                    from mcp_servers.base import MCPRequest
                    
                    request = MCPRequest(
                        method=tool_name,
                        params=kwargs,
                        id=str(uuid.uuid4())
                    )
                    
                    response = await self.mcp_orchestrator.route_request(request)
                    return response.result if response.success else None
                
                # Register MCP bridge with tool registry
                self.tool_registry.register_external_bridge("mcp", mcp_tool_bridge)
            
            # Wire database to all components for persistence
            if self.database:
                # Agent persistence already wired in agent initialization
                
                # Wire MCP orchestrator metrics to database
                if self.mcp_orchestrator:
                    original_route = self.mcp_orchestrator.route_request
                    
                    async def logged_route_request(request):
                        start_time = time.perf_counter()
                        try:
                            response = await original_route(request)
                            execution_time = int((time.perf_counter() - start_time) * 1000)
                            
                            # Log to database
                            await self.database.record_tool_metric(
                                tool_name="mcp_orchestrator",
                                operation=request.method,
                                execution_time_ms=execution_time,
                                success=response.success,
                                error_message=response.error.get("message") if response.error else None
                            )
                            
                            return response
                        except Exception as e:
                            execution_time = int((time.perf_counter() - start_time) * 1000)
                            await self.database.record_tool_metric(
                                tool_name="mcp_orchestrator",
                                operation=request.method,
                                execution_time_ms=execution_time,
                                success=False,
                                error_message=str(e)
                            )
                            raise
                    
                    self.mcp_orchestrator.route_request = logged_route_request
            
            logger.info("Component integrations wired successfully")
            
        except Exception as e:
            logger.error(f"Failed to wire integrations: {str(e)}", exc_info=True)
            raise
    
    async def _start_monitoring(self) -> None:
        """Start health monitoring and metrics collection."""
        logger.info("Starting system monitoring...")
        
        self.health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        self.metrics_collector_task = asyncio.create_task(self._metrics_collector_loop())
        
        logger.info("System monitoring started")
    
    async def start(self) -> None:
        """Start the integrated system."""
        if self.system_state != ComponentState.READY:
            raise RuntimeError(f"System not ready for startup (state: {self.system_state})")
        
        self.system_state = ComponentState.RUNNING
        
        try:
            # Start Telegram client if configured
            if self.telegram_client:
                await self.telegram_client.start()
                logger.info("Telegram client started")
            
            await self._emit_event(
                "system_started", "system", "info", "System startup complete"
            )
            
            logger.info("System is now running")
            
        except Exception as e:
            self.system_state = ComponentState.ERROR
            await self._emit_event(
                "system_start_error", "system", "critical",
                f"System startup failed: {str(e)}"
            )
            logger.error(f"System startup failed: {str(e)}", exc_info=True)
            raise
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the system."""
        logger.info("Starting system shutdown...")
        self.shutdown_requested = True
        self.system_state = ComponentState.STOPPING
        
        try:
            # Stop monitoring
            if self.health_monitor_task:
                self.health_monitor_task.cancel()
            if self.metrics_collector_task:
                self.metrics_collector_task.cancel()
            
            # Shutdown components in reverse dependency order
            if self.telegram_client:
                await self.telegram_client.disconnect()
                logger.info("Telegram client disconnected")
            
            if self.telegram_processor:
                await self.telegram_processor.shutdown()
                logger.info("Telegram processor shutdown")
            
            if self.mcp_orchestrator:
                await self.mcp_orchestrator.stop()
                logger.info("MCP orchestrator stopped")
            
            if self.database:
                await self.database.close()
                logger.info("Database closed")
            
            self.system_state = ComponentState.STOPPED
            
            await self._emit_event(
                "system_shutdown", "system", "info", "System shutdown complete"
            )
            
            logger.info("System shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}", exc_info=True)
            self.system_state = ComponentState.ERROR
            raise
    
    async def _health_monitor_loop(self) -> None:
        """Background health monitoring loop."""
        while not self.shutdown_requested:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {str(e)}")
                await asyncio.sleep(5)
    
    async def _metrics_collector_loop(self) -> None:
        """Background metrics collection loop."""
        while not self.shutdown_requested:
            try:
                await self._collect_metrics()
                await asyncio.sleep(60)  # Collect metrics every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Metrics collector error: {str(e)}")
                await asyncio.sleep(10)
    
    async def _perform_health_checks(self) -> None:
        """Perform health checks on all components."""
        components_to_check = [
            ("database", self.database),
            ("valor_agent", self.agent),
            ("tool_registry", self.tool_registry),
            ("mcp_orchestrator", self.mcp_orchestrator),
            ("telegram_processor", self.telegram_processor),
            ("telegram_client", self.telegram_client)
        ]
        
        for name, component in components_to_check:
            if component is None:
                continue
            
            try:
                # Component-specific health checks
                if name == "database":
                    info = await self.database.get_database_info()
                    health = HealthStatus.HEALTHY if info else HealthStatus.UNHEALTHY
                    details = info or {}
                
                elif name == "valor_agent":
                    contexts = self.agent.list_contexts()
                    health = HealthStatus.HEALTHY
                    details = {"active_contexts": len(contexts)}
                
                elif name == "mcp_orchestrator":
                    health_summary = self.mcp_orchestrator.get_health_summary()
                    healthy_servers = health_summary.get("healthy_servers", 0)
                    total_servers = health_summary.get("total_servers", 0)
                    
                    if total_servers == 0:
                        health = HealthStatus.DEGRADED
                    elif healthy_servers == total_servers:
                        health = HealthStatus.HEALTHY
                    elif healthy_servers > 0:
                        health = HealthStatus.DEGRADED
                    else:
                        health = HealthStatus.UNHEALTHY
                    
                    details = health_summary
                
                elif name == "telegram_processor":
                    status = await self.telegram_processor.get_pipeline_status()
                    success_rate = status.get("success_rate", 0.0)
                    
                    if success_rate >= 0.95:
                        health = HealthStatus.HEALTHY
                    elif success_rate >= 0.8:
                        health = HealthStatus.DEGRADED
                    else:
                        health = HealthStatus.UNHEALTHY
                    
                    details = status
                
                elif name == "telegram_client":
                    if self.telegram_client and self.telegram_client.is_connected():
                        health = HealthStatus.HEALTHY
                        details = {"connected": True}
                    else:
                        health = HealthStatus.UNHEALTHY
                        details = {"connected": False}
                
                else:
                    health = HealthStatus.HEALTHY
                    details = {}
                
                self._update_component_health(
                    name, ComponentState.RUNNING, health, details=details
                )
                
            except Exception as e:
                self._update_component_health(
                    name, ComponentState.ERROR, HealthStatus.CRITICAL,
                    errors=[str(e)]
                )
                
                if self.auto_recovery:
                    await self._attempt_recovery(name, component, e)
    
    async def _collect_metrics(self) -> None:
        """Collect system-wide metrics."""
        try:
            # Collect performance metrics from components
            if self.telegram_processor:
                status = await self.telegram_processor.get_pipeline_status()
                self.metrics.total_requests = status.get("total_processed", 0)
                self.metrics.avg_response_time_ms = status.get("avg_duration_ms", 0.0)
            
            if self.mcp_orchestrator:
                stats = self.mcp_orchestrator.get_orchestrator_stats()
                self.metrics.successful_requests = stats.get("requests_routed", 0)
            
            # Calculate uptime for components
            current_time = time.time()
            if self.startup_time:
                uptime_seconds = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
                self.metrics.component_uptime["system"] = uptime_seconds
            
            # Calculate error rates
            for name, health in self.component_health.items():
                error_count = len(health.errors)
                if error_count > 0:
                    # Simple error rate calculation (errors per hour)
                    time_delta = (datetime.now(timezone.utc) - health.last_check).total_seconds()
                    if time_delta > 0:
                        self.metrics.error_rates[name] = (error_count * 3600) / time_delta
            
        except Exception as e:
            logger.error(f"Error collecting metrics: {str(e)}")
    
    async def _attempt_recovery(self, component_name: str, component: Any, error: Exception) -> None:
        """Attempt automatic recovery for failed components."""
        logger.warning(f"Attempting recovery for {component_name} after error: {str(error)}")
        
        try:
            # Component-specific recovery strategies
            if component_name == "telegram_client" and self.telegram_client:
                await self.telegram_client.disconnect()
                await asyncio.sleep(5)
                await self.telegram_client.start()
                logger.info(f"Recovery successful for {component_name}")
                
            elif component_name == "mcp_orchestrator" and self.mcp_orchestrator:
                # Restart failed servers
                servers = self.mcp_orchestrator.list_servers()
                for server in servers:
                    if server.health_status.value == "unhealthy":
                        try:
                            await self.mcp_orchestrator.unregister_server(server.server_name)
                            await self.mcp_orchestrator.register_server(
                                server.server_name, server.server_type, server.config
                            )
                            logger.info(f"Restarted MCP server: {server.server_name}")
                        except Exception as e:
                            logger.error(f"Failed to restart server {server.server_name}: {e}")
            
            await self._emit_event(
                "component_recovery", component_name, "info",
                f"Recovery attempted for {component_name}"
            )
            
        except Exception as recovery_error:
            logger.error(f"Recovery failed for {component_name}: {str(recovery_error)}")
            await self._emit_event(
                "recovery_failed", component_name, "error",
                f"Recovery failed for {component_name}: {str(recovery_error)}"
            )
    
    def _update_component_health(
        self, 
        name: str, 
        state: ComponentState, 
        health: HealthStatus,
        details: Optional[Dict[str, Any]] = None,
        errors: Optional[List[str]] = None,
        performance_metrics: Optional[Dict[str, float]] = None
    ) -> None:
        """Update component health status."""
        self.component_health[name] = ComponentHealth(
            name=name,
            state=state,
            health=health,
            last_check=datetime.now(timezone.utc),
            details=details or {},
            errors=errors or [],
            performance_metrics=performance_metrics or {}
        )
    
    async def _emit_event(self, event_type: str, component: str, severity: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Emit a system event."""
        event = SystemEvent(
            event_type=event_type,
            component=component,
            severity=severity,
            message=message,
            metadata=metadata or {}
        )
        
        self.system_events.append(event)
        
        # Keep only recent events
        if len(self.system_events) > 1000:
            self.system_events = self.system_events[-500:]
        
        # Trigger event handlers
        handlers = self.event_handlers.get(event_type, []) + self.event_handlers.get("*", [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {str(e)}")
        
        # Log critical events
        if severity in ["error", "critical"]:
            logger.error(f"System event: {event_type} - {message}")
        elif severity == "warning":
            logger.warning(f"System event: {event_type} - {message}")
        else:
            logger.info(f"System event: {event_type} - {message}")
    
    def register_event_handler(self, event_type: str, handler: Callable) -> None:
        """Register an event handler for system events."""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        return {
            "system_state": self.system_state.value,
            "uptime_seconds": (
                (datetime.now(timezone.utc) - self.startup_time).total_seconds()
                if self.startup_time else 0
            ),
            "component_health": {
                name: {
                    "state": health.state.value,
                    "health": health.health.value,
                    "last_check": health.last_check.isoformat(),
                    "error_count": len(health.errors),
                    "details": health.details
                }
                for name, health in self.component_health.items()
            },
            "metrics": {
                "startup_time_ms": self.metrics.startup_time_ms,
                "total_requests": self.metrics.total_requests,
                "successful_requests": self.metrics.successful_requests,
                "failed_requests": self.metrics.failed_requests,
                "avg_response_time_ms": self.metrics.avg_response_time_ms,
                "error_rates": self.metrics.error_rates
            },
            "recent_events": [
                {
                    "type": event.event_type,
                    "component": event.component,
                    "severity": event.severity,
                    "message": event.message,
                    "timestamp": event.timestamp.isoformat()
                }
                for event in self.system_events[-10:]  # Last 10 events
            ]
        }
    
    @asynccontextmanager
    async def managed_lifecycle(self):
        """Context manager for complete system lifecycle management."""
        try:
            await self.initialize()
            await self.start()
            yield self
        finally:
            await self.shutdown()


# Global system integrator instance
system_integrator = SystemIntegrator()


# Convenience functions for external use
async def initialize_system(config: Optional[Dict[str, Any]] = None) -> SystemIntegrator:
    """Initialize the complete system."""
    if config:
        system_integrator.config.update(config)
    
    await system_integrator.initialize()
    return system_integrator


async def start_system() -> None:
    """Start the system after initialization."""
    await system_integrator.start()


async def shutdown_system() -> None:
    """Shutdown the system gracefully."""
    await system_integrator.shutdown()


async def get_system_status() -> Dict[str, Any]:
    """Get current system status."""
    return await system_integrator.get_system_status()