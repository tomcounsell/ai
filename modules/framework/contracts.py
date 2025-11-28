"""
Standard Module I/O Contracts

Every module in the system accepts ModuleInput and returns ModuleOutput.
This standardization enables:
- Autonomous module generation
- Easy discovery and integration
- Consistent error handling
- Auditable side effects
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Execution result status."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    ERROR = "error"


class ExecutionContext(BaseModel):
    """
    Shared context passed to modules.

    Contains user, session, and security information that modules
    can use to customize behavior and enforce permissions.
    """

    # Identity
    user_id: Optional[str] = Field(None, description="Current user identifier")
    session_id: Optional[str] = Field(None, description="Session identifier")
    workspace_id: Optional[str] = Field(None, description="Workspace/tenant identifier")

    # Conversation context
    conversation_history: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Recent conversation messages for context",
    )

    # Security context
    permissions: List[str] = Field(
        default_factory=list,
        description="User permissions for authorization checks",
    )
    auth_token: Optional[str] = Field(
        None,
        description="Authentication token if needed",
    )

    # Performance hints
    timeout: int = Field(
        default=30,
        description="Maximum execution time in seconds",
    )
    priority: str = Field(
        default="normal",
        description="Execution priority: low | normal | high | critical",
    )

    # Tracing
    trace_id: Optional[str] = Field(
        None,
        description="Distributed tracing identifier",
    )
    parent_span_id: Optional[str] = Field(
        None,
        description="Parent span for nested operations",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ModuleInput(BaseModel):
    """
    Standard input contract for all modules.

    Every module accepts this standardized input structure,
    enabling consistent processing and validation.
    """

    # Core fields (REQUIRED)
    operation: str = Field(
        ...,
        description="Operation to perform (e.g., 'charge', 'refund', 'search')",
    )

    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific parameters",
    )

    # Context fields (OPTIONAL but recommended)
    context: Optional[ExecutionContext] = Field(
        None,
        description="Execution context from parent agent",
    )

    # Metadata
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier for tracing",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Request timestamp",
    )

    # Optional flags
    dry_run: bool = Field(
        default=False,
        description="If True, validate but don't execute",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ErrorDetail(BaseModel):
    """
    Standardized error information.

    Provides structured error details for consistent handling
    and user-friendly error messages.
    """

    code: str = Field(
        ...,
        description="Error code (e.g., 'STRIPE_API_ERROR', 'VALIDATION_FAILED')",
    )

    message: str = Field(
        ...,
        description="Human-readable error message",
    )

    category: str = Field(
        ...,
        description="Error category: validation | auth | api | internal | timeout",
    )

    recoverable: bool = Field(
        default=True,
        description="Whether this error can be retried",
    )

    recovery_suggestion: Optional[str] = Field(
        None,
        description="Suggested action to recover from error",
    )

    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional error context",
    )

    stack_trace: Optional[str] = Field(
        None,
        description="Stack trace for debugging (redacted in production)",
    )


class SideEffect(BaseModel):
    """
    Track side effects for auditability.

    Records external actions taken during module execution
    for auditing, debugging, and potential rollback.
    """

    type: str = Field(
        ...,
        description="Side effect type: api_call | database_write | notification | file_write",
    )

    description: str = Field(
        ...,
        description="Human-readable description of the side effect",
    )

    target: str = Field(
        ...,
        description="What was affected (e.g., 'stripe:customers/cus_123')",
    )

    reversible: bool = Field(
        default=False,
        description="Whether this side effect can be undone",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the side effect occurred",
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata about the side effect",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ModuleOutput(BaseModel):
    """
    Standard output contract for all modules.

    Every module returns this standardized output structure,
    enabling consistent result handling and monitoring.
    """

    # Status (REQUIRED)
    status: ExecutionStatus = Field(
        ...,
        description="Execution result status",
    )

    # Result data (REQUIRED for SUCCESS/PARTIAL_SUCCESS)
    data: Optional[Dict[str, Any]] = Field(
        None,
        description="Result data from the operation",
    )

    # Error information (REQUIRED for FAILURE/ERROR)
    error: Optional[ErrorDetail] = Field(
        None,
        description="Error details if execution failed",
    )

    # Metadata
    request_id: str = Field(
        ...,
        description="Request ID this response corresponds to",
    )

    execution_time_ms: int = Field(
        ...,
        description="Execution time in milliseconds",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Response timestamp",
    )

    # Side effects and state changes
    side_effects: List[SideEffect] = Field(
        default_factory=list,
        description="List of side effects that occurred",
    )

    # Warnings and recommendations
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal warnings during execution",
    )

    recommendations: List[str] = Field(
        default_factory=list,
        description="Suggested follow-up actions",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    @classmethod
    def success(
        cls,
        request_id: str,
        data: Dict[str, Any],
        execution_time_ms: int,
        side_effects: Optional[List[SideEffect]] = None,
        warnings: Optional[List[str]] = None,
        recommendations: Optional[List[str]] = None,
    ) -> "ModuleOutput":
        """Create a successful response."""
        return cls(
            status=ExecutionStatus.SUCCESS,
            data=data,
            request_id=request_id,
            execution_time_ms=execution_time_ms,
            side_effects=side_effects or [],
            warnings=warnings or [],
            recommendations=recommendations or [],
        )

    @classmethod
    def failure(
        cls,
        request_id: str,
        error: ErrorDetail,
        execution_time_ms: int,
        side_effects: Optional[List[SideEffect]] = None,
    ) -> "ModuleOutput":
        """Create a failure response."""
        return cls(
            status=ExecutionStatus.FAILURE,
            error=error,
            request_id=request_id,
            execution_time_ms=execution_time_ms,
            side_effects=side_effects or [],
        )

    @classmethod
    def error(
        cls,
        request_id: str,
        code: str,
        message: str,
        execution_time_ms: int,
        category: str = "internal",
        recoverable: bool = False,
    ) -> "ModuleOutput":
        """Create an error response with minimal details."""
        return cls(
            status=ExecutionStatus.ERROR,
            error=ErrorDetail(
                code=code,
                message=message,
                category=category,
                recoverable=recoverable,
            ),
            request_id=request_id,
            execution_time_ms=execution_time_ms,
        )
