"""Tool Registry System

This module provides a comprehensive tool registration, discovery, and management
system for AI agents with support for versioning, dependencies, and metadata.
"""

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4

from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    """Definition of a tool parameter."""
    
    name: str = Field(..., description="Parameter name")
    type: str = Field(..., description="Parameter type")
    description: str = Field(..., description="Parameter description")
    required: bool = Field(default=True, description="Whether parameter is required")
    default: Any = Field(None, description="Default value if not required")
    constraints: Dict[str, Any] = Field(default_factory=dict, description="Parameter constraints")


class ToolMetadata(BaseModel):
    """Comprehensive metadata for registered tools."""
    
    # Basic identification
    name: str = Field(..., description="Tool name")
    version: str = Field(default="1.0.0", description="Tool version")
    description: str = Field(..., description="Tool description")
    category: str = Field(default="general", description="Tool category")
    
    # Function details
    parameters: List[ToolParameter] = Field(default_factory=list, description="Tool parameters")
    return_type: str = Field(default="Any", description="Return type description")
    is_async: bool = Field(default=False, description="Whether tool is async")
    
    # Dependencies and requirements
    dependencies: List[str] = Field(default_factory=list, description="Required dependencies")
    conflicts: List[str] = Field(default_factory=list, description="Conflicting tools")
    prerequisites: List[str] = Field(default_factory=list, description="Required tools to be loaded first")
    
    # Usage and permissions
    permissions_required: List[str] = Field(default_factory=list, description="Required permissions")
    rate_limit: Optional[int] = Field(None, description="Rate limit per minute")
    max_concurrent: Optional[int] = Field(None, description="Max concurrent executions")
    timeout_seconds: Optional[int] = Field(None, description="Execution timeout")
    
    # Documentation and examples
    examples: List[Dict[str, Any]] = Field(default_factory=list, description="Usage examples")
    documentation_url: Optional[str] = Field(None, description="Documentation URL")
    tags: List[str] = Field(default_factory=list, description="Tool tags")
    
    # Runtime metadata
    registration_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: Optional[datetime] = Field(None, description="Last usage timestamp")
    usage_count: int = Field(default=0, description="Total usage count")
    success_rate: float = Field(default=1.0, description="Success rate (0.0-1.0)")
    
    # Versioning and compatibility
    api_version: str = Field(default="1.0", description="API version")
    min_agent_version: Optional[str] = Field(None, description="Minimum agent version required")
    max_agent_version: Optional[str] = Field(None, description="Maximum agent version supported")
    
    @validator('success_rate')
    def validate_success_rate(cls, v):
        """Validate success rate range."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Success rate must be between 0.0 and 1.0")
        return v
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ToolExecution(BaseModel):
    """Record of tool execution."""
    
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_name: str = Field(..., description="Name of executed tool")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = Field(None)
    execution_time: Optional[float] = Field(None, description="Execution time in seconds")
    success: bool = Field(default=True)
    result: Any = Field(None, description="Tool execution result")
    error: Optional[str] = Field(None, description="Error message if failed")
    context: Dict[str, Any] = Field(default_factory=dict, description="Execution context")
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ToolRegistry:
    """
    Comprehensive tool registry with advanced features for tool management,
    versioning, dependency resolution, and usage tracking.
    """
    
    def __init__(self):
        """Initialize the tool registry."""
        self._tools: Dict[str, Callable] = {}
        self._metadata: Dict[str, ToolMetadata] = {}
        self._executions: List[ToolExecution] = []
        self._active_executions: Dict[str, ToolExecution] = {}
        self._rate_limits: Dict[str, List[datetime]] = {}
        
        logger.info("Tool registry initialized")
    
    def register_tool(
        self,
        func: Callable,
        metadata: Optional[Union[ToolMetadata, Dict[str, Any]]] = None,
        name: Optional[str] = None,
        version: str = "1.0.0",
        description: Optional[str] = None,
        category: str = "general",
        **kwargs
    ) -> str:
        """
        Register a tool with the registry.
        
        Args:
            func: Function to register as a tool
            metadata: ToolMetadata object or dict with metadata
            name: Override tool name (defaults to function name)
            version: Tool version
            description: Tool description
            category: Tool category
            **kwargs: Additional metadata fields
            
        Returns:
            str: Registered tool name
        """
        tool_name = name or func.__name__
        
        # Create metadata if not provided
        if metadata is None:
            metadata = self._create_metadata_from_function(
                func, tool_name, version, description, category, **kwargs
            )
        elif isinstance(metadata, dict):
            metadata = ToolMetadata(**metadata)
        
        # Validate dependencies
        self._validate_dependencies(tool_name, metadata)
        
        # Register the tool
        self._tools[tool_name] = func
        self._metadata[tool_name] = metadata
        
        logger.info(f"Registered tool: {tool_name} v{metadata.version}")
        return tool_name
    
    def _create_metadata_from_function(
        self,
        func: Callable,
        name: str,
        version: str,
        description: Optional[str],
        category: str,
        **kwargs
    ) -> ToolMetadata:
        """Create metadata by inspecting function signature."""
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        
        # Extract parameters
        parameters = []
        for param_name, param in sig.parameters.items():
            param_info = ToolParameter(
                name=param_name,
                type=str(param.annotation) if param.annotation != inspect.Parameter.empty else "Any",
                description=f"Parameter: {param_name}",
                required=param.default == inspect.Parameter.empty,
                default=param.default if param.default != inspect.Parameter.empty else None
            )
            parameters.append(param_info)
        
        # Determine if async
        is_async = asyncio.iscoroutinefunction(func)
        
        # Create metadata
        metadata = ToolMetadata(
            name=name,
            version=version,
            description=description or doc.split('\n')[0] if doc else f"Tool: {name}",
            category=category,
            parameters=parameters,
            return_type=str(sig.return_annotation) if sig.return_annotation != inspect.Signature.empty else "Any",
            is_async=is_async,
            **kwargs
        )
        
        return metadata
    
    def _validate_dependencies(self, tool_name: str, metadata: ToolMetadata) -> None:
        """Validate tool dependencies and conflicts."""
        # Check for conflicts
        for conflict in metadata.conflicts:
            if conflict in self._tools:
                raise ValueError(f"Tool {tool_name} conflicts with already registered tool: {conflict}")
        
        # Check prerequisites
        for prereq in metadata.prerequisites:
            if prereq not in self._tools:
                logger.warning(f"Tool {tool_name} requires {prereq} which is not registered")
    
    def unregister_tool(self, name: str) -> bool:
        """
        Unregister a tool from the registry.
        
        Args:
            name: Name of tool to unregister
            
        Returns:
            bool: True if tool was unregistered, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            del self._metadata[name]
            # Clean up rate limit tracking
            self._rate_limits.pop(name, None)
            logger.info(f"Unregistered tool: {name}")
            return True
        return False
    
    def get_tool(self, name: str) -> Optional[Callable]:
        """Get a registered tool by name."""
        return self._tools.get(name)
    
    def get_tool_metadata(self, name: str) -> Optional[ToolMetadata]:
        """Get metadata for a registered tool."""
        return self._metadata.get(name)
    
    def list_tools(self, category: Optional[str] = None, tags: Optional[List[str]] = None) -> List[str]:
        """
        List registered tools with optional filtering.
        
        Args:
            category: Filter by category
            tags: Filter by tags (any tag match)
            
        Returns:
            List[str]: List of tool names
        """
        tools = []
        for name, metadata in self._metadata.items():
            # Filter by category
            if category and metadata.category != category:
                continue
            
            # Filter by tags
            if tags and not any(tag in metadata.tags for tag in tags):
                continue
            
            tools.append(name)
        
        return sorted(tools)
    
    def get_available_tools(self) -> Dict[str, Callable]:
        """Get all available tools as a dictionary."""
        return self._tools.copy()
    
    def search_tools(self, query: str) -> List[str]:
        """
        Search for tools by name, description, or tags.
        
        Args:
            query: Search query
            
        Returns:
            List[str]: List of matching tool names
        """
        query_lower = query.lower()
        matches = []
        
        for name, metadata in self._metadata.items():
            # Search in name
            if query_lower in name.lower():
                matches.append(name)
                continue
            
            # Search in description
            if query_lower in metadata.description.lower():
                matches.append(name)
                continue
            
            # Search in tags
            if any(query_lower in tag.lower() for tag in metadata.tags):
                matches.append(name)
                continue
        
        return matches
    
    async def execute_tool(
        self,
        name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> ToolExecution:
        """
        Execute a registered tool with rate limiting and error handling.
        
        Args:
            name: Name of tool to execute
            parameters: Parameters to pass to the tool
            context: Optional execution context
            
        Returns:
            ToolExecution: Execution record
        """
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' not found in registry")
        
        tool_func = self._tools[name]
        metadata = self._metadata[name]
        
        # Check rate limits
        if not self._check_rate_limit(name, metadata):
            raise RuntimeError(f"Rate limit exceeded for tool: {name}")
        
        # Create execution record
        execution = ToolExecution(
            tool_name=name,
            parameters=parameters,
            context=context or {}
        )
        
        self._active_executions[execution.execution_id] = execution
        
        try:
            # Execute the tool
            if metadata.is_async:
                if metadata.timeout_seconds:
                    result = await asyncio.wait_for(
                        tool_func(**parameters),
                        timeout=metadata.timeout_seconds
                    )
                else:
                    result = await tool_func(**parameters)
            else:
                # Run sync function in thread pool if needed
                if metadata.timeout_seconds:
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, lambda: tool_func(**parameters)
                        ),
                        timeout=metadata.timeout_seconds
                    )
                else:
                    result = tool_func(**parameters)
            
            # Record successful execution
            execution.end_time = datetime.now(timezone.utc)
            execution.execution_time = (execution.end_time - execution.start_time).total_seconds()
            execution.result = result
            execution.success = True
            
            # Update metadata
            metadata.last_used = execution.end_time
            metadata.usage_count += 1
            
        except Exception as e:
            # Record failed execution
            execution.end_time = datetime.now(timezone.utc)
            execution.execution_time = (execution.end_time - execution.start_time).total_seconds()
            execution.success = False
            execution.error = str(e)
            
            # Update success rate
            total_executions = len([ex for ex in self._executions if ex.tool_name == name]) + 1
            successful_executions = len([ex for ex in self._executions if ex.tool_name == name and ex.success])
            metadata.success_rate = successful_executions / total_executions
            
            logger.error(f"Tool execution failed: {name} - {e}")
        
        finally:
            # Clean up active execution
            self._active_executions.pop(execution.execution_id, None)
            self._executions.append(execution)
        
        return execution
    
    def _check_rate_limit(self, tool_name: str, metadata: ToolMetadata) -> bool:
        """Check if tool execution is within rate limits."""
        if not metadata.rate_limit:
            return True
        
        now = datetime.now(timezone.utc)
        minute_ago = now.replace(second=now.second - 60)
        
        # Initialize rate limit tracking
        if tool_name not in self._rate_limits:
            self._rate_limits[tool_name] = []
        
        # Clean old entries
        self._rate_limits[tool_name] = [
            timestamp for timestamp in self._rate_limits[tool_name]
            if timestamp > minute_ago
        ]
        
        # Check limit
        if len(self._rate_limits[tool_name]) >= metadata.rate_limit:
            return False
        
        # Record this execution
        self._rate_limits[tool_name].append(now)
        return True
    
    def get_tool_usage_stats(self, name: str) -> Dict[str, Any]:
        """
        Get usage statistics for a tool.
        
        Args:
            name: Tool name
            
        Returns:
            Dict[str, Any]: Usage statistics
        """
        if name not in self._metadata:
            return {}
        
        metadata = self._metadata[name]
        tool_executions = [ex for ex in self._executions if ex.tool_name == name]
        
        if not tool_executions:
            return {
                'usage_count': 0,
                'success_rate': 1.0,
                'average_execution_time': None,
                'last_used': None
            }
        
        successful_executions = [ex for ex in tool_executions if ex.success]
        execution_times = [ex.execution_time for ex in tool_executions if ex.execution_time]
        
        return {
            'usage_count': len(tool_executions),
            'success_rate': len(successful_executions) / len(tool_executions),
            'average_execution_time': sum(execution_times) / len(execution_times) if execution_times else None,
            'last_used': metadata.last_used.isoformat() if metadata.last_used else None,
            'total_execution_time': sum(execution_times) if execution_times else 0,
            'error_count': len([ex for ex in tool_executions if not ex.success])
        }
    
    def get_registry_stats(self) -> Dict[str, Any]:
        """Get overall registry statistics."""
        total_tools = len(self._tools)
        total_executions = len(self._executions)
        successful_executions = len([ex for ex in self._executions if ex.success])
        
        # Category distribution
        categories = {}
        for metadata in self._metadata.values():
            categories[metadata.category] = categories.get(metadata.category, 0) + 1
        
        # Most used tools
        tool_usage = {}
        for execution in self._executions:
            tool_usage[execution.tool_name] = tool_usage.get(execution.tool_name, 0) + 1
        
        most_used = sorted(tool_usage.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            'total_tools': total_tools,
            'total_executions': total_executions,
            'overall_success_rate': successful_executions / total_executions if total_executions > 0 else 1.0,
            'categories': categories,
            'most_used_tools': dict(most_used),
            'active_executions': len(self._active_executions)
        }
    
    def export_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Export all tool definitions for backup or transfer."""
        return {
            name: {
                'metadata': metadata.dict(),
                'function_name': self._tools[name].__name__,
                'module': self._tools[name].__module__
            }
            for name, metadata in self._metadata.items()
        }
    
    def clear_execution_history(self, tool_name: Optional[str] = None) -> int:
        """
        Clear execution history for a specific tool or all tools.
        
        Args:
            tool_name: Optional tool name to clear history for
            
        Returns:
            int: Number of execution records cleared
        """
        if tool_name:
            original_count = len(self._executions)
            self._executions = [ex for ex in self._executions if ex.tool_name != tool_name]
            cleared = original_count - len(self._executions)
        else:
            cleared = len(self._executions)
            self._executions.clear()
        
        logger.info(f"Cleared {cleared} execution records")
        return cleared
    
    def validate_tool_compatibility(self, tool_name: str, agent_version: str) -> bool:
        """
        Validate if a tool is compatible with the given agent version.
        
        Args:
            tool_name: Name of tool to validate
            agent_version: Agent version to check against
            
        Returns:
            bool: True if compatible, False otherwise
        """
        metadata = self.get_tool_metadata(tool_name)
        if not metadata:
            return False
        
        # Simple version comparison (in production, use proper version parsing)
        if metadata.min_agent_version and agent_version < metadata.min_agent_version:
            return False
        
        if metadata.max_agent_version and agent_version > metadata.max_agent_version:
            return False
        
        return True


def tool_registry_decorator(
    name: Optional[str] = None,
    version: str = "1.0.0",
    description: Optional[str] = None,
    category: str = "general",
    **metadata_kwargs
):
    """
    Decorator for registering functions as tools.
    
    Args:
        name: Tool name (defaults to function name)
        version: Tool version
        description: Tool description
        category: Tool category
        **metadata_kwargs: Additional metadata fields
    
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        # Store registration info in function attributes
        func._tool_registration = {
            'name': name or func.__name__,
            'version': version,
            'description': description,
            'category': category,
            **metadata_kwargs
        }
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            async_wrapper._tool_registration = func._tool_registration
            return async_wrapper
        else:
            sync_wrapper._tool_registration = func._tool_registration
            return sync_wrapper
    
    return decorator


def discover_tools(module) -> List[Tuple[str, Callable, Dict[str, Any]]]:
    """
    Discover tools in a module by looking for decorated functions.
    
    Args:
        module: Module to search for tools
        
    Returns:
        List[Tuple[str, Callable, Dict[str, Any]]]: List of (name, function, metadata) tuples
    """
    tools = []
    
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, '_tool_registration'):
            tool_name = obj._tool_registration['name']
            metadata = obj._tool_registration.copy()
            tools.append((tool_name, obj, metadata))
    
    return tools