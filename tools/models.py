"""
Base models for tool infrastructure.
These models support tool execution tracking and monitoring.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    """Tool operational status enumeration.
    
    Defines the possible operational states for PydanticAI tools.
    Used for monitoring and tracking tool availability.
    
    Attributes:
        AVAILABLE: Tool is ready to accept requests.
        BUSY: Tool is currently processing a request.
        ERROR: Tool encountered an error and may not be functional.
        OFFLINE: Tool is not available or has been disabled.
    """

    AVAILABLE = "available"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class ToolResult(BaseModel):
    """Result from tool execution.
    
    Standardized result model for tracking tool execution outcomes.
    Provides detailed information about success, output, timing, and
    any errors that occurred during tool execution.
    
    Attributes:
        success: Whether the tool execution completed successfully.
        output: The main output or response from the tool.
        error_message: Optional error message if execution failed.
        execution_time_ms: Time taken to execute the tool in milliseconds.
        metadata: Additional metadata about the execution.
        timestamp: When the tool execution occurred.
        
    Example:
        >>> result = ToolResult(
        ...     success=True,
        ...     output="Task completed",
        ...     execution_time_ms=150
        ... )
        >>> result.success
        True
    """

    success: bool = Field(..., description="Was tool execution successful")
    output: str = Field(..., description="Tool output/response")
    error_message: str | None = Field(None, description="Error message if failed")
    execution_time_ms: int = Field(..., ge=0, description="Execution time in milliseconds")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: datetime = Field(default_factory=datetime.now, description="Execution timestamp")
