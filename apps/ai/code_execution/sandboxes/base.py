"""
Base sandbox interface and shared types.

This module defines the contract that all sandbox implementations must follow,
enabling the Strategy pattern for swapping sandbox backends.

Key Design Principles:
    1. Immutability - SandboxConfig is frozen, preventing accidental changes
    2. Type Safety - Dataclasses with type hints for all fields
    3. Clear Interface - Single execute() method with well-defined inputs/outputs
    4. Observability - Results include timing, resource usage, and execution metadata
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class SandboxConfig:
    """
    Configuration for sandbox execution.

    This frozen dataclass ensures configurations cannot be accidentally modified
    after creation, preventing subtle bugs in concurrent execution scenarios.

    Attributes:
        timeout_seconds: Maximum execution time (default: 30s)
        max_memory_mb: Memory limit in megabytes (default: 512MB)
        max_output_bytes: Maximum size of stdout/stderr (default: 1MB)
        enable_network: Whether to allow network access (default: False)
        allowed_imports: Whitelist of importable modules (default: safe modules)
        execution_context: Additional data available during execution
        user_id: User requesting execution (for logging/rate limiting)

    Security Defaults:
        - Network disabled
        - Limited to safe standard library modules
        - Strict resource limits
        - Short timeout for fast failure
    """

    timeout_seconds: float = 30.0
    max_memory_mb: int = 512
    max_output_bytes: int = 1_000_000  # 1 MB
    enable_network: bool = False
    allowed_imports: tuple[str, ...] = (
        # Safe data processing libraries
        "pandas",
        "numpy",
        "scipy",
        # Safe standard library
        "math",
        "json",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "statistics",
        "decimal",
        "fractions",
    )
    execution_context: dict[str, Any] = field(default_factory=dict)
    user_id: Optional[int] = None

    def __post_init__(self):
        """Validate configuration values."""
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


@dataclass
class SandboxResult:
    """
    Result of code execution in a sandbox.

    This structure provides comprehensive information about the execution,
    enabling detailed logging, error reporting, and LLM feedback.

    Attributes:
        success: Whether execution completed without errors
        stdout: Standard output captured during execution
        stderr: Standard error output
        return_value: Value returned by the code (if any)
        error_message: Human-readable error description (if failed)
        error_type: Exception class name (if failed)
        execution_time_seconds: Actual time taken to execute
        memory_used_mb: Peak memory usage (if available)
        exit_code: Process exit code (for process-based sandboxes)
        metadata: Additional information (sandbox type, resource limits hit, etc.)

    Design Note:
        This class is mutable (not frozen) because it's constructed progressively
        during execution and modified with timing/resource information.
    """

    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Any = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    execution_time_seconds: Optional[float] = None
    memory_used_mb: Optional[float] = None
    exit_code: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert result to dictionary for JSON serialization.

        Returns:
            Dictionary representation suitable for:
            - API responses
            - Database storage
            - Logging systems
            - LLM error feedback
        """
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_value": str(self.return_value) if self.return_value else None,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "execution_time_seconds": self.execution_time_seconds,
            "memory_used_mb": self.memory_used_mb,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
        }


class BaseSandbox(ABC):
    """
    Abstract base class for all sandbox implementations.

    This class defines the interface that all sandboxes must implement,
    enabling the Strategy pattern for swapping between implementations.

    Implementation Requirements:
        1. Implement execute() method
        2. Handle timeouts and resource limits
        3. Capture stdout/stderr
        4. Return structured SandboxResult
        5. Clean up resources in case of errors

    Example Implementation:
        class MySandbox(BaseSandbox):
            def execute(self, code: str, config: SandboxConfig) -> SandboxResult:
                # 1. Set up isolated environment
                # 2. Apply resource limits
                # 3. Execute code with timeout
                # 4. Capture output
                # 5. Return result
                pass

    Usage:
        sandbox = MySandbox()
        config = SandboxConfig(timeout_seconds=10)
        result = sandbox.execute("print('Hello')", config)
        if result.success:
            print(result.stdout)
    """

    @abstractmethod
    def execute(self, code: str, config: SandboxConfig) -> SandboxResult:
        """
        Execute code in the sandbox with specified configuration.

        This is the core method that all sandboxes must implement. It should:
        1. Validate the code is a string
        2. Set up the isolated environment
        3. Apply all resource limits from config
        4. Execute the code with timeout
        5. Capture all output
        6. Handle errors gracefully
        7. Clean up resources
        8. Return comprehensive result

        Args:
            code: Python code to execute (must be valid Python 3.11+)
            config: Sandbox configuration with limits and settings

        Returns:
            SandboxResult with execution outcome and metadata

        Raises:
            SandboxError: If sandbox setup/teardown fails
            ValidationError: If code is invalid
            TimeoutError: If execution exceeds timeout (may be caught and returned in result)
            ResourceLimitError: If resource limits are exceeded

        Thread Safety:
            Implementations should be thread-safe, allowing multiple concurrent
            executions (each with their own isolated environment).

        Example:
            >>> sandbox = MySandbox()
            >>> config = SandboxConfig(timeout_seconds=5)
            >>> result = sandbox.execute("x = 1 + 1", config)
            >>> assert result.success
        """
        pass

    def __enter__(self):
        """Context manager support for resource cleanup."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources when exiting context."""
        self.cleanup()

    def cleanup(self):
        """
        Clean up any resources held by the sandbox.

        Override this method if your sandbox needs cleanup
        (e.g., terminating processes, removing temp files).

        Default implementation does nothing.
        """
        pass

    def _validate_code(self, code: str) -> None:
        """
        Basic validation of code input.

        Args:
            code: Code string to validate

        Raises:
            ValidationError: If code is not a valid string
        """
        if not isinstance(code, str):
            raise TypeError("Code must be a string")
        if not code.strip():
            raise ValueError("Code cannot be empty")

    def _create_error_result(
        self,
        error: Exception,
        execution_time: Optional[float] = None,
    ) -> SandboxResult:
        """
        Helper to create SandboxResult from an exception.

        Args:
            error: Exception that occurred
            execution_time: Time elapsed before error (if known)

        Returns:
            SandboxResult indicating failure with error details
        """
        return SandboxResult(
            success=False,
            error_message=str(error),
            error_type=type(error).__name__,
            execution_time_seconds=execution_time,
            metadata={"sandbox_type": self.__class__.__name__},
        )
