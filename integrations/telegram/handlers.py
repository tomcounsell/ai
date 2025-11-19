"""Unified Handler Architecture

This module implements a comprehensive handler system with registration,
middleware, priorities, and extensible handler management for different
message types and events.
"""

import asyncio
import inspect
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from .unified_processor import ProcessingRequest, ProcessingResult
from .components.context_builder import MessageContext
from .components.type_router import MessageType
from .components.response_manager import FormattedResponse


logger = logging.getLogger(__name__)


class HandlerType(Enum):
    """Types of handlers in the system"""
    MESSAGE = "message"          # Message content handlers
    EVENT = "event"             # System event handlers
    COMMAND = "command"         # Command handlers
    MEDIA = "media"            # Media processing handlers
    ERROR = "error"            # Error handlers
    MIDDLEWARE = "middleware"   # Middleware handlers
    WEBHOOK = "webhook"        # Webhook handlers


class HandlerPriority(Enum):
    """Handler execution priorities"""
    CRITICAL = 0     # System-critical handlers (auth, security)
    HIGH = 1         # Important handlers (commands, errors)
    NORMAL = 2       # Standard handlers (message processing)
    LOW = 3          # Background handlers (analytics, logging)
    DEFERRED = 4     # Non-urgent handlers (cleanup, optimization)


class HandlerStatus(Enum):
    """Handler execution status"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class HandlerResult:
    """Result from handler execution"""
    success: bool
    handler_id: str
    execution_time: float
    status: HandlerStatus
    response: Optional[str] = None
    responses: List[FormattedResponse] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    should_continue: bool = True  # Whether to continue handler chain


@dataclass
class HandlerRegistration:
    """Handler registration information"""
    handler_id: str
    handler_func: Callable
    handler_type: HandlerType
    priority: HandlerPriority
    message_types: List[MessageType] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    conditions: List[Callable] = field(default_factory=list)
    middleware: List[str] = field(default_factory=list)
    timeout_seconds: int = 30
    retry_count: int = 0
    enabled: bool = True
    tags: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HandlerExecution:
    """Handler execution tracking"""
    execution_id: str
    handler_id: str
    request: ProcessingRequest
    context: MessageContext
    start_time: float
    end_time: Optional[float] = None
    result: Optional[HandlerResult] = None
    middleware_results: List[HandlerResult] = field(default_factory=list)


class BaseHandler(ABC):
    """Base class for all handlers"""
    
    def __init__(
        self,
        handler_id: str,
        priority: HandlerPriority = HandlerPriority.NORMAL,
        timeout_seconds: int = 30
    ):
        self.handler_id = handler_id
        self.priority = priority
        self.timeout_seconds = timeout_seconds
        self.execution_count = 0
        self.success_count = 0
        self.error_count = 0
        self.total_execution_time = 0.0
    
    @abstractmethod
    async def handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> HandlerResult:
        """Handle the request and return result"""
        pass
    
    async def can_handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> bool:
        """Check if this handler can handle the request"""
        return True
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get handler performance metrics"""
        success_rate = self.success_count / self.execution_count if self.execution_count > 0 else 0.0
        avg_execution_time = self.total_execution_time / self.execution_count if self.execution_count > 0 else 0.0
        
        return {
            "handler_id": self.handler_id,
            "execution_count": self.execution_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": success_rate,
            "avg_execution_time": avg_execution_time,
            "total_execution_time": self.total_execution_time
        }


class MessageHandler(BaseHandler):
    """Handler for processing messages"""
    
    def __init__(
        self,
        handler_id: str,
        message_types: List[MessageType],
        handler_func: Callable,
        priority: HandlerPriority = HandlerPriority.NORMAL
    ):
        super().__init__(handler_id, priority)
        self.message_types = message_types
        self.handler_func = handler_func
    
    async def handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> HandlerResult:
        """Handle message processing"""
        
        start_time = time.perf_counter()
        self.execution_count += 1
        
        try:
            # Call handler function
            if inspect.iscoroutinefunction(self.handler_func):
                result = await self.handler_func(request, context)
            else:
                result = self.handler_func(request, context)
            
            # Process result
            if isinstance(result, str):
                response = FormattedResponse(
                    text=result,
                    chat_id=context.chat_id
                )
                responses = [response]
            elif isinstance(result, list):
                responses = result
            elif isinstance(result, FormattedResponse):
                responses = [result]
            else:
                responses = []
            
            execution_time = time.perf_counter() - start_time
            self.success_count += 1
            self.total_execution_time += execution_time
            
            return HandlerResult(
                success=True,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.COMPLETED,
                responses=responses
            )
            
        except Exception as e:
            execution_time = time.perf_counter() - start_time
            self.error_count += 1
            self.total_execution_time += execution_time
            
            logger.error(f"Handler {self.handler_id} failed: {str(e)}", exc_info=True)
            
            return HandlerResult(
                success=False,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.FAILED,
                error=str(e)
            )
    
    async def can_handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> bool:
        """Check if this handler can handle the message type"""
        
        # Check if we have message type in context
        if hasattr(context, 'message_type'):
            return context.message_type in self.message_types
        
        # Default to true if no message type filtering
        return not self.message_types


class CommandHandler(BaseHandler):
    """Handler for processing commands"""
    
    def __init__(
        self,
        handler_id: str,
        command: str,
        handler_func: Callable,
        description: str = "",
        aliases: List[str] = None
    ):
        super().__init__(handler_id, HandlerPriority.HIGH)
        self.command = command.lower()
        self.handler_func = handler_func
        self.description = description
        self.aliases = [alias.lower() for alias in (aliases or [])]
    
    async def handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> HandlerResult:
        """Handle command processing"""
        
        start_time = time.perf_counter()
        self.execution_count += 1
        
        try:
            # Extract command and arguments
            text = request.raw_text or ""
            parts = text.split()
            
            if not parts:
                raise ValueError("No command found")
            
            command = parts[0][1:].lower()  # Remove prefix
            args = parts[1:] if len(parts) > 1 else []
            
            # Call handler function
            if inspect.iscoroutinefunction(self.handler_func):
                result = await self.handler_func(command, args, request, context)
            else:
                result = self.handler_func(command, args, request, context)
            
            # Process result
            if isinstance(result, str):
                response = FormattedResponse(
                    text=result,
                    chat_id=context.chat_id,
                    reply_to_message_id=context.message_id
                )
                responses = [response]
            elif isinstance(result, list):
                responses = result
            elif isinstance(result, FormattedResponse):
                responses = [result]
            else:
                responses = []
            
            execution_time = time.perf_counter() - start_time
            self.success_count += 1
            self.total_execution_time += execution_time
            
            return HandlerResult(
                success=True,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.COMPLETED,
                responses=responses
            )
            
        except Exception as e:
            execution_time = time.perf_counter() - start_time
            self.error_count += 1
            self.total_execution_time += execution_time
            
            logger.error(f"Command handler {self.handler_id} failed: {str(e)}")
            
            return HandlerResult(
                success=False,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.FAILED,
                error=str(e)
            )
    
    async def can_handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> bool:
        """Check if this handler can handle the command"""
        
        text = request.raw_text or ""
        
        if not text.startswith(('/', '!')):
            return False
        
        parts = text.split()
        if not parts:
            return False
        
        command = parts[0][1:].lower()  # Remove prefix
        
        return command == self.command or command in self.aliases


class MiddlewareHandler(BaseHandler):
    """Handler for middleware processing"""
    
    def __init__(
        self,
        handler_id: str,
        handler_func: Callable,
        priority: HandlerPriority = HandlerPriority.HIGH
    ):
        super().__init__(handler_id, priority)
        self.handler_func = handler_func
    
    async def handle(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> HandlerResult:
        """Handle middleware processing"""
        
        start_time = time.perf_counter()
        self.execution_count += 1
        
        try:
            # Call middleware function
            if inspect.iscoroutinefunction(self.handler_func):
                should_continue = await self.handler_func(request, context)
            else:
                should_continue = self.handler_func(request, context)
            
            # Middleware should return boolean indicating whether to continue
            if should_continue is None:
                should_continue = True
            
            execution_time = time.perf_counter() - start_time
            self.success_count += 1
            self.total_execution_time += execution_time
            
            return HandlerResult(
                success=True,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.COMPLETED,
                should_continue=bool(should_continue)
            )
            
        except Exception as e:
            execution_time = time.perf_counter() - start_time
            self.error_count += 1
            self.total_execution_time += execution_time
            
            logger.error(f"Middleware {self.handler_id} failed: {str(e)}")
            
            return HandlerResult(
                success=False,
                handler_id=self.handler_id,
                execution_time=execution_time,
                status=HandlerStatus.FAILED,
                error=str(e),
                should_continue=False  # Stop on middleware failure
            )


class HandlerRegistry:
    """
    Unified registry for all handler types with advanced routing,
    middleware support, and comprehensive execution management.
    """
    
    def __init__(
        self,
        enable_middleware: bool = True,
        enable_handler_timeout: bool = True,
        default_timeout_seconds: int = 30,
        max_concurrent_handlers: int = 10
    ):
        """
        Initialize the handler registry.
        
        Args:
            enable_middleware: Enable middleware processing
            enable_handler_timeout: Enable handler timeout protection
            default_timeout_seconds: Default timeout for handlers
            max_concurrent_handlers: Maximum concurrent handler executions
        """
        self.enable_middleware = enable_middleware
        self.enable_handler_timeout = enable_handler_timeout
        self.default_timeout_seconds = default_timeout_seconds
        
        # Handler storage
        self.handlers: Dict[str, BaseHandler] = {}
        self.handlers_by_type: Dict[HandlerType, List[BaseHandler]] = defaultdict(list)
        self.middleware_handlers: List[MiddlewareHandler] = []
        self.command_handlers: Dict[str, CommandHandler] = {}
        
        # Execution tracking
        self.active_executions: Dict[str, HandlerExecution] = {}
        self.execution_history: deque = deque(maxlen=1000)
        self.execution_semaphore = asyncio.Semaphore(max_concurrent_handlers)
        
        # Performance metrics
        self.total_executions = 0
        self.successful_executions = 0
        self.failed_executions = 0
        self.total_execution_time = 0.0
        
        logger.info(
            f"HandlerRegistry initialized with middleware={enable_middleware}, "
            f"timeout={enable_handler_timeout}, max_concurrent={max_concurrent_handlers}"
        )
    
    def register_handler(
        self,
        handler: BaseHandler,
        handler_type: HandlerType,
        patterns: List[str] = None,
        conditions: List[Callable] = None,
        middleware: List[str] = None,
        tags: Set[str] = None
    ) -> None:
        """
        Register a handler with the registry.
        
        Args:
            handler: Handler instance to register
            handler_type: Type of handler
            patterns: Regex patterns for matching
            conditions: Condition functions for matching
            middleware: Middleware to apply to this handler
            tags: Tags for handler organization
        """
        
        if handler.handler_id in self.handlers:
            logger.warning(f"Handler {handler.handler_id} already registered, replacing")
        
        self.handlers[handler.handler_id] = handler
        self.handlers_by_type[handler_type].append(handler)
        
        # Special handling for command handlers
        if isinstance(handler, CommandHandler):
            self.command_handlers[handler.command] = handler
            for alias in handler.aliases:
                self.command_handlers[alias] = handler
        
        # Add to middleware list if it's middleware
        if isinstance(handler, MiddlewareHandler):
            self.middleware_handlers.append(handler)
            # Sort middleware by priority
            self.middleware_handlers.sort(key=lambda h: h.priority.value)
        
        logger.info(
            f"Registered {handler_type.value} handler: {handler.handler_id}, "
            f"priority: {handler.priority.value}"
        )
    
    def register_message_handler(
        self,
        handler_id: str,
        handler_func: Callable,
        message_types: List[MessageType] = None,
        priority: HandlerPriority = HandlerPriority.NORMAL
    ) -> None:
        """Register a message handler"""
        
        handler = MessageHandler(
            handler_id=handler_id,
            message_types=message_types or [],
            handler_func=handler_func,
            priority=priority
        )
        
        self.register_handler(handler, HandlerType.MESSAGE)
    
    def register_command_handler(
        self,
        command: str,
        handler_func: Callable,
        description: str = "",
        aliases: List[str] = None
    ) -> None:
        """Register a command handler"""
        
        handler_id = f"cmd_{command}"
        handler = CommandHandler(
            handler_id=handler_id,
            command=command,
            handler_func=handler_func,
            description=description,
            aliases=aliases or []
        )
        
        self.register_handler(handler, HandlerType.COMMAND)
    
    def register_middleware(
        self,
        middleware_id: str,
        middleware_func: Callable,
        priority: HandlerPriority = HandlerPriority.HIGH
    ) -> None:
        """Register middleware"""
        
        handler = MiddlewareHandler(
            handler_id=middleware_id,
            handler_func=middleware_func,
            priority=priority
        )
        
        self.register_handler(handler, HandlerType.MIDDLEWARE)
    
    async def execute_handlers(
        self,
        request: ProcessingRequest,
        context: MessageContext,
        handler_types: List[HandlerType] = None
    ) -> List[HandlerResult]:
        """
        Execute handlers for a request.
        
        Args:
            request: Processing request
            context: Message context
            handler_types: Types of handlers to execute (None for all applicable)
            
        Returns:
            List of handler results
        """
        
        execution_id = f"exec_{int(time.time() * 1000)}_{context.chat_id}"
        
        async with self.execution_semaphore:
            try:
                # Create execution tracking
                execution = HandlerExecution(
                    execution_id=execution_id,
                    handler_id="batch_execution",
                    request=request,
                    context=context,
                    start_time=time.perf_counter()
                )
                
                self.active_executions[execution_id] = execution
                results = []
                
                logger.debug(
                    f"Executing handlers for message {context.message_id}, "
                    f"execution_id: {execution_id}"
                )
                
                # Execute middleware first
                if self.enable_middleware:
                    middleware_results = await self._execute_middleware(request, context)
                    execution.middleware_results = middleware_results
                    
                    # Check if middleware blocked processing
                    if any(not result.should_continue for result in middleware_results):
                        logger.info(f"Middleware blocked processing for {execution_id}")
                        execution.end_time = time.perf_counter()
                        return middleware_results
                
                # Find applicable handlers
                applicable_handlers = await self._find_applicable_handlers(
                    request, context, handler_types
                )
                
                # Execute handlers by priority
                for priority_group in self._group_handlers_by_priority(applicable_handlers):
                    # Execute handlers in parallel within same priority group
                    handler_tasks = []
                    for handler in priority_group:
                        task = asyncio.create_task(
                            self._execute_single_handler(handler, request, context, execution_id)
                        )
                        handler_tasks.append(task)
                    
                    # Wait for all handlers in this priority group
                    group_results = await asyncio.gather(*handler_tasks, return_exceptions=True)
                    
                    for result in group_results:
                        if isinstance(result, HandlerResult):
                            results.append(result)
                            
                            # Stop if handler says not to continue
                            if not result.should_continue:
                                logger.info(f"Handler {result.handler_id} stopped execution chain")
                                break
                        elif isinstance(result, Exception):
                            logger.error(f"Handler execution exception: {str(result)}")
                
                # Update metrics
                execution.end_time = time.perf_counter()
                execution.result = HandlerResult(
                    success=any(r.success for r in results),
                    handler_id="batch_execution",
                    execution_time=execution.end_time - execution.start_time,
                    status=HandlerStatus.COMPLETED,
                    metadata={"handler_count": len(results)}
                )
                
                self._update_execution_metrics(execution, results)
                
                logger.debug(
                    f"Handler execution {execution_id} completed with {len(results)} results"
                )
                
                return results
                
            except Exception as e:
                logger.error(f"Handler execution {execution_id} failed: {str(e)}", exc_info=True)
                
                error_result = HandlerResult(
                    success=False,
                    handler_id="batch_execution",
                    execution_time=time.perf_counter() - execution.start_time,
                    status=HandlerStatus.FAILED,
                    error=str(e)
                )
                
                return [error_result]
            
            finally:
                self.active_executions.pop(execution_id, None)
    
    async def _execute_middleware(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> List[HandlerResult]:
        """Execute middleware handlers"""
        
        results = []
        
        for middleware in self.middleware_handlers:
            if not middleware.enabled:
                continue
            
            result = await self._execute_single_handler(
                middleware, request, context, "middleware"
            )
            results.append(result)
            
            # Stop on middleware failure or explicit stop
            if not result.success or not result.should_continue:
                break
        
        return results
    
    async def _find_applicable_handlers(
        self,
        request: ProcessingRequest,
        context: MessageContext,
        handler_types: List[HandlerType] = None
    ) -> List[BaseHandler]:
        """Find handlers that can handle the request"""
        
        applicable_handlers = []
        
        # Filter by handler types if specified
        types_to_check = handler_types or list(HandlerType)
        
        for handler_type in types_to_check:
            if handler_type == HandlerType.MIDDLEWARE:
                continue  # Middleware handled separately
            
            handlers = self.handlers_by_type.get(handler_type, [])
            
            for handler in handlers:
                if not handler.enabled:
                    continue
                
                try:
                    if await handler.can_handle(request, context):
                        applicable_handlers.append(handler)
                except Exception as e:
                    logger.error(f"Error checking handler {handler.handler_id}: {str(e)}")
        
        return applicable_handlers
    
    def _group_handlers_by_priority(
        self,
        handlers: List[BaseHandler]
    ) -> List[List[BaseHandler]]:
        """Group handlers by priority for ordered execution"""
        
        priority_groups: Dict[int, List[BaseHandler]] = defaultdict(list)
        
        for handler in handlers:
            priority_groups[handler.priority.value].append(handler)
        
        # Return groups in priority order
        sorted_priorities = sorted(priority_groups.keys())
        return [priority_groups[priority] for priority in sorted_priorities]
    
    async def _execute_single_handler(
        self,
        handler: BaseHandler,
        request: ProcessingRequest,
        context: MessageContext,
        execution_id: str
    ) -> HandlerResult:
        """Execute a single handler with timeout and error handling"""
        
        try:
            if self.enable_handler_timeout:
                result = await asyncio.wait_for(
                    handler.handle(request, context),
                    timeout=handler.timeout_seconds
                )
            else:
                result = await handler.handle(request, context)
            
            logger.debug(
                f"Handler {handler.handler_id} completed in {result.execution_time:.3f}s"
            )
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"Handler {handler.handler_id} timed out")
            
            return HandlerResult(
                success=False,
                handler_id=handler.handler_id,
                execution_time=handler.timeout_seconds,
                status=HandlerStatus.TIMEOUT,
                error=f"Handler timed out after {handler.timeout_seconds}s"
            )
            
        except Exception as e:
            logger.error(f"Handler {handler.handler_id} failed: {str(e)}", exc_info=True)
            
            return HandlerResult(
                success=False,
                handler_id=handler.handler_id,
                execution_time=0.0,
                status=HandlerStatus.FAILED,
                error=str(e)
            )
    
    def _update_execution_metrics(
        self,
        execution: HandlerExecution,
        results: List[HandlerResult]
    ) -> None:
        """Update execution metrics"""
        
        self.total_executions += 1
        
        if any(r.success for r in results):
            self.successful_executions += 1
        else:
            self.failed_executions += 1
        
        self.total_execution_time += execution.end_time - execution.start_time
        
        # Add to execution history
        self.execution_history.append({
            "execution_id": execution.execution_id,
            "timestamp": execution.start_time,
            "execution_time": execution.end_time - execution.start_time,
            "handler_count": len(results),
            "success_count": sum(1 for r in results if r.success),
            "failed_count": sum(1 for r in results if not r.success)
        })
    
    def get_handler(self, handler_id: str) -> Optional[BaseHandler]:
        """Get handler by ID"""
        return self.handlers.get(handler_id)
    
    def get_command_handler(self, command: str) -> Optional[CommandHandler]:
        """Get command handler by command name"""
        return self.command_handlers.get(command.lower())
    
    def list_handlers(
        self,
        handler_type: Optional[HandlerType] = None,
        enabled_only: bool = True
    ) -> List[BaseHandler]:
        """List registered handlers"""
        
        if handler_type:
            handlers = self.handlers_by_type.get(handler_type, [])
        else:
            handlers = list(self.handlers.values())
        
        if enabled_only:
            handlers = [h for h in handlers if h.enabled]
        
        return handlers
    
    def enable_handler(self, handler_id: str) -> bool:
        """Enable a handler"""
        
        handler = self.handlers.get(handler_id)
        if handler:
            handler.enabled = True
            logger.info(f"Enabled handler: {handler_id}")
            return True
        
        return False
    
    def disable_handler(self, handler_id: str) -> bool:
        """Disable a handler"""
        
        handler = self.handlers.get(handler_id)
        if handler:
            handler.enabled = False
            logger.info(f"Disabled handler: {handler_id}")
            return True
        
        return False
    
    def unregister_handler(self, handler_id: str) -> bool:
        """Unregister a handler"""
        
        handler = self.handlers.pop(handler_id, None)
        if not handler:
            return False
        
        # Remove from type-specific lists
        for handler_list in self.handlers_by_type.values():
            if handler in handler_list:
                handler_list.remove(handler)
        
        # Remove from middleware list
        if isinstance(handler, MiddlewareHandler):
            self.middleware_handlers = [h for h in self.middleware_handlers if h != handler]
        
        # Remove from command handlers
        if isinstance(handler, CommandHandler):
            self.command_handlers = {
                k: v for k, v in self.command_handlers.items() if v != handler
            }
        
        logger.info(f"Unregistered handler: {handler_id}")
        return True
    
    async def get_status(self) -> Dict[str, Any]:
        """Get registry status and metrics"""
        
        success_rate = (
            self.successful_executions / self.total_executions
            if self.total_executions > 0 else 0.0
        )
        
        avg_execution_time = (
            self.total_execution_time / self.total_executions
            if self.total_executions > 0 else 0.0
        )
        
        handler_counts = {
            handler_type.value: len(handlers)
            for handler_type, handlers in self.handlers_by_type.items()
        }
        
        return {
            "total_handlers": len(self.handlers),
            "handler_counts": handler_counts,
            "middleware_count": len(self.middleware_handlers),
            "command_count": len(self.command_handlers),
            "active_executions": len(self.active_executions),
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "success_rate": success_rate,
            "avg_execution_time": avg_execution_time,
            "enable_middleware": self.enable_middleware,
            "enable_timeout": self.enable_handler_timeout
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the handler registry"""
        
        logger.info("Shutting down handler registry...")
        
        # Wait for active executions to complete
        while self.active_executions:
            logger.info(f"Waiting for {len(self.active_executions)} active executions...")
            await asyncio.sleep(0.5)
        
        # Clear all handlers
        self.handlers.clear()
        self.handlers_by_type.clear()
        self.middleware_handlers.clear()
        self.command_handlers.clear()
        
        logger.info("Handler registry shutdown complete")


# Decorator functions for easy handler registration

def message_handler(
    registry: HandlerRegistry,
    message_types: List[MessageType] = None,
    priority: HandlerPriority = HandlerPriority.NORMAL
):
    """Decorator to register a message handler"""
    
    def decorator(func):
        handler_id = func.__name__
        registry.register_message_handler(
            handler_id=handler_id,
            handler_func=func,
            message_types=message_types,
            priority=priority
        )
        return func
    
    return decorator


def command_handler(
    registry: HandlerRegistry,
    command: str,
    description: str = "",
    aliases: List[str] = None
):
    """Decorator to register a command handler"""
    
    def decorator(func):
        registry.register_command_handler(
            command=command,
            handler_func=func,
            description=description,
            aliases=aliases
        )
        return func
    
    return decorator


def middleware_handler(
    registry: HandlerRegistry,
    priority: HandlerPriority = HandlerPriority.HIGH
):
    """Decorator to register middleware"""
    
    def decorator(func):
        middleware_id = func.__name__
        registry.register_middleware(
            middleware_id=middleware_id,
            middleware_func=func,
            priority=priority
        )
        return func
    
    return decorator