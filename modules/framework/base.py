"""
Base Module Implementation

Provides the abstract base class that all modules must inherit from.
Handles common concerns like validation, error handling, and metrics.
"""

import logging
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from modules.framework.contracts import (
    ErrorDetail,
    ExecutionStatus,
    ModuleInput,
    ModuleOutput,
    SideEffect,
)


class ModuleCapabilities(BaseModel):
    """Describes what a module can do."""

    operations: List[str]
    capabilities: List[str]
    tags: List[str]
    category: str


class BaseModule(ABC):
    """
    Abstract base class for all modules.

    Provides:
    - Standard execute() method with error handling
    - Input validation
    - Performance tracking
    - Side effect recording

    Subclasses must implement:
    - _execute_operation(): Core business logic
    - get_supported_operations(): List of valid operations
    - get_capabilities(): Module capabilities for discovery
    """

    def __init__(
        self,
        module_id: str,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        logger: Optional[logging.Logger] = None,
    ):
        self.module_id = module_id
        self.name = name
        self.version = version
        self.description = description
        self.logger = logger or logging.getLogger(f"module.{module_id}")

        # Metrics tracking
        self._total_executions = 0
        self._successful_executions = 0
        self._failed_executions = 0
        self._total_execution_time_ms = 0

    @abstractmethod
    def get_supported_operations(self) -> Set[str]:
        """Return the set of operations this module supports."""
        pass

    @abstractmethod
    def get_capabilities(self) -> ModuleCapabilities:
        """Return module capabilities for discovery."""
        pass

    @abstractmethod
    async def _execute_operation(
        self,
        operation: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the core operation logic.

        Args:
            operation: The operation to perform
            parameters: Operation-specific parameters
            context: Execution context

        Returns:
            Dict with operation results

        Raises:
            Any exceptions will be caught and converted to ModuleOutput.error()
        """
        pass

    def validate_operation(self, operation: str) -> Optional[str]:
        """
        Validate the operation is supported.

        Returns:
            None if valid, error message if invalid
        """
        supported = self.get_supported_operations()
        if operation not in supported:
            return f"Unsupported operation '{operation}'. Supported: {sorted(supported)}"
        return None

    def validate_parameters(
        self, operation: str, parameters: Dict[str, Any]
    ) -> Optional[str]:
        """
        Validate operation parameters.

        Override in subclasses for operation-specific validation.

        Returns:
            None if valid, error message if invalid
        """
        return None

    async def execute(self, input_data: ModuleInput) -> ModuleOutput:
        """
        Main execution method with comprehensive error handling.

        This method:
        1. Validates the operation and parameters
        2. Executes the core logic
        3. Tracks metrics
        4. Returns standardized output
        """
        start_time = time.time()
        side_effects: List[SideEffect] = []

        try:
            self._total_executions += 1

            # Log execution start
            self.logger.info(
                f"Executing {self.name}.{input_data.operation}",
                extra={
                    "module_id": self.module_id,
                    "operation": input_data.operation,
                    "request_id": input_data.request_id,
                },
            )

            # Validate operation
            operation_error = self.validate_operation(input_data.operation)
            if operation_error:
                execution_time_ms = int((time.time() - start_time) * 1000)
                self._failed_executions += 1
                return ModuleOutput.failure(
                    request_id=input_data.request_id,
                    error=ErrorDetail(
                        code="INVALID_OPERATION",
                        message=operation_error,
                        category="validation",
                        recoverable=False,
                    ),
                    execution_time_ms=execution_time_ms,
                )

            # Handle dry run - skip parameter validation and execution
            if input_data.dry_run:
                execution_time_ms = int((time.time() - start_time) * 1000)
                return ModuleOutput.success(
                    request_id=input_data.request_id,
                    data={"dry_run": True, "would_execute": input_data.operation},
                    execution_time_ms=execution_time_ms,
                )

            # Validate parameters
            param_error = self.validate_parameters(
                input_data.operation, input_data.parameters
            )
            if param_error:
                execution_time_ms = int((time.time() - start_time) * 1000)
                self._failed_executions += 1
                return ModuleOutput.failure(
                    request_id=input_data.request_id,
                    error=ErrorDetail(
                        code="INVALID_PARAMETERS",
                        message=param_error,
                        category="validation",
                        recoverable=True,
                        recovery_suggestion="Check parameter requirements and retry",
                    ),
                    execution_time_ms=execution_time_ms,
                )

            # Execute core operation
            context_dict = (
                input_data.context.dict() if input_data.context else None
            )
            result = await self._execute_operation(
                operation=input_data.operation,
                parameters=input_data.parameters,
                context=context_dict,
            )

            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._total_execution_time_ms += execution_time_ms
            self._successful_executions += 1

            # Log success
            self.logger.info(
                f"Completed {self.name}.{input_data.operation} in {execution_time_ms}ms",
                extra={
                    "module_id": self.module_id,
                    "operation": input_data.operation,
                    "request_id": input_data.request_id,
                    "execution_time_ms": execution_time_ms,
                },
            )

            return ModuleOutput.success(
                request_id=input_data.request_id,
                data=result,
                execution_time_ms=execution_time_ms,
                side_effects=side_effects,
            )

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._failed_executions += 1

            # Log error
            self.logger.error(
                f"Error in {self.name}.{input_data.operation}: {str(e)}",
                extra={
                    "module_id": self.module_id,
                    "operation": input_data.operation,
                    "request_id": input_data.request_id,
                    "error": str(e),
                },
                exc_info=True,
            )

            # Determine error category
            error_category = self._categorize_error(e)

            return ModuleOutput.failure(
                request_id=input_data.request_id,
                error=ErrorDetail(
                    code=f"{self.module_id.upper()}_ERROR",
                    message=str(e),
                    category=error_category,
                    recoverable=error_category != "internal",
                    stack_trace=traceback.format_exc(),
                ),
                execution_time_ms=execution_time_ms,
                side_effects=side_effects,
            )

    def _categorize_error(self, error: Exception) -> str:
        """Categorize an error for structured handling."""
        error_type = type(error).__name__.lower()

        if "validation" in error_type or "value" in error_type:
            return "validation"
        elif "auth" in error_type or "permission" in error_type:
            return "auth"
        elif "timeout" in error_type:
            return "timeout"
        elif "api" in error_type or "http" in error_type:
            return "api"
        else:
            return "internal"

    def get_metrics(self) -> Dict[str, Any]:
        """Get module execution metrics."""
        avg_time = (
            self._total_execution_time_ms / self._total_executions
            if self._total_executions > 0
            else 0
        )
        success_rate = (
            self._successful_executions / self._total_executions
            if self._total_executions > 0
            else 0
        )

        return {
            "module_id": self.module_id,
            "total_executions": self._total_executions,
            "successful_executions": self._successful_executions,
            "failed_executions": self._failed_executions,
            "success_rate": success_rate,
            "average_execution_time_ms": avg_time,
            "total_execution_time_ms": self._total_execution_time_ms,
        }

    def health_check(self) -> Dict[str, Any]:
        """Perform a health check on the module."""
        metrics = self.get_metrics()

        # Determine health status
        is_healthy = True
        issues = []

        if metrics["success_rate"] < 0.9 and metrics["total_executions"] > 10:
            is_healthy = False
            issues.append(f"Low success rate: {metrics['success_rate']:.2%}")

        if metrics["average_execution_time_ms"] > 5000:
            issues.append("High average execution time")

        return {
            "healthy": is_healthy,
            "module_id": self.module_id,
            "name": self.name,
            "version": self.version,
            "metrics": metrics,
            "issues": issues,
        }
