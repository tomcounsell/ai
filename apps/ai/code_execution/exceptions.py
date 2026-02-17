"""
Exception classes for code execution module.

This module defines a hierarchy of exceptions that provide specific error
information for different failure modes during code execution.

The exception hierarchy enables:
    - Specific error handling for different failure types
    - Structured error responses to LLMs for self-correction
    - Detailed logging and monitoring of security events
"""


class CodeExecutionError(Exception):
    """
    Base exception for all code execution errors.

    All exceptions in this module inherit from this class, allowing
    callers to catch all execution-related errors with a single except clause.

    Attributes:
        message: Human-readable error description
        details: Additional context (line numbers, attempted operations, etc.)
        can_retry: Whether the operation might succeed if retried
    """

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        can_retry: bool = False,
    ):
        self.message = message
        self.details = details or {}
        self.can_retry = can_retry
        super().__init__(message)

    def to_dict(self) -> dict:
        """
        Convert exception to dictionary for JSON serialization.

        This format is designed to be useful for:
        - API responses
        - Logging structured data
        - Providing context to LLMs for error correction

        Returns:
            Dictionary with error type, message, details, and retry flag
        """
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
            "can_retry": self.can_retry,
        }


class ValidationError(CodeExecutionError):
    """
    Raised when code fails pre-execution validation.

    Examples:
        - Syntax errors
        - Forbidden imports detected
        - Dangerous AST patterns found
        - Code too complex (too many operations)

    These errors indicate the code should not be executed and needs
    to be rewritten by the LLM.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, can_retry=True)


class SandboxError(CodeExecutionError):
    """
    Raised when the sandbox environment fails.

    Examples:
        - Sandbox initialization failure
        - Container/VM launch failure
        - Sandbox communication error

    These errors indicate an infrastructure problem, not a code problem.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, can_retry=True)


class TimeoutError(CodeExecutionError):
    """
    Raised when code execution exceeds the time limit.

    This indicates either:
        - Infinite loop in the code
        - Computation too complex for allowed time
        - Deliberate resource exhaustion attempt

    The LLM should optimize the code or break it into smaller chunks.
    """

    def __init__(
        self,
        message: str = "Code execution exceeded time limit",
        timeout_seconds: float | None = None,
    ):
        details = {"timeout_seconds": timeout_seconds} if timeout_seconds else {}
        super().__init__(message, details, can_retry=True)


class ResourceLimitError(CodeExecutionError):
    """
    Raised when code exceeds resource limits.

    Examples:
        - Memory limit exceeded
        - Too many operations
        - Output too large

    Indicates the code needs to be optimized or the resource limits
    need to be adjusted for legitimate use cases.
    """

    def __init__(
        self,
        message: str,
        limit_type: str | None = None,
        limit_value: any | None = None,
    ):
        details = {}
        if limit_type:
            details["limit_type"] = limit_type
        if limit_value:
            details["limit_value"] = limit_value
        super().__init__(message, details, can_retry=True)


class SecurityViolationError(CodeExecutionError):
    """
    Raised when code attempts a prohibited operation.

    Examples:
        - Attempted import of blocked module during execution
        - File access attempt
        - Network request attempt
        - Privilege escalation attempt

    These errors should be logged with high priority as they may indicate:
        - LLM prompt injection attacks
        - Deliberate security probing
        - Need to update blocked module list

    Should NOT be retried without security review.
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        attempted_operation: str | None = None,
    ):
        details = {}
        if violation_type:
            details["violation_type"] = violation_type
        if attempted_operation:
            details["attempted_operation"] = attempted_operation
        super().__init__(message, details, can_retry=False)


class OutputValidationError(CodeExecutionError):
    """
    Raised when execution output fails validation.

    Examples:
        - Output too large
        - Sensitive data detected in output
        - Invalid data type returned

    This protects against:
        - Data exfiltration
        - Accidental exposure of secrets
        - Resource exhaustion via large outputs
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, can_retry=False)
