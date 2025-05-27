"""
Base models for tool infrastructure.
These models support tool execution tracking and monitoring.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    """Tool operational status"""

    AVAILABLE = "available"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class ToolResult(BaseModel):
    """Result from tool execution"""

    success: bool = Field(..., description="Was tool execution successful")
    output: str = Field(..., description="Tool output/response")
    error_message: str | None = Field(None, description="Error message if failed")
    execution_time_ms: int = Field(..., ge=0, description="Execution time in milliseconds")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: datetime = Field(default_factory=datetime.now, description="Execution timestamp")
