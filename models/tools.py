# models/tools.py
"""
Base tool models for PydanticAI function tools integration.
Actual tools should be implemented as PydanticAI function tools.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from enum import Enum

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
    error_message: Optional[str] = Field(None, description="Error message if failed")
    execution_time_ms: int = Field(..., ge=0, description="Execution time in milliseconds")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: datetime = Field(default_factory=datetime.now, description="Execution timestamp")

class ToolInput(BaseModel):
    """Base class for tool input validation"""
    pass

class ToolOutput(BaseModel):
    """Base class for tool output formatting"""
    pass

# Example usage models (to be removed when PydanticAI tools are implemented)

class SearchInput(ToolInput):
    """Input for web search tool"""
    query: str = Field(..., description="Search query")
    max_results: int = Field(default=3, ge=1, le=10, description="Maximum results to return")

class SearchOutput(ToolOutput):
    """Output from web search tool"""
    query: str = Field(..., description="Original search query")
    answer: str = Field(..., description="AI-synthesized answer from web results")
    sources: List[str] = Field(default_factory=list, description="Source URLs used")
    success: bool = Field(..., description="Was search successful")