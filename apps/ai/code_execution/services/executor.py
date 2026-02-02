"""
Main code execution orchestrator.

This module provides the CodeExecutor class, which coordinates all aspects
of safe code execution:
    1. Pre-execution validation
    2. Sandbox selection and execution
    3. Post-execution validation
    4. Logging and monitoring
    5. Error handling and reporting

The executor implements the Template Method pattern, allowing subclasses
to customize specific steps while maintaining the overall flow.

Usage Example:
    >>> executor = CodeExecutor(user_id=123)
    >>> result = executor.execute("print('Hello, World!')")
    >>> if result.success:
    ...     print(result.stdout)
    ... else:
    ...     print(f"Error: {result.error_message}")
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
from datetime import datetime

from ..exceptions import (
    CodeExecutionError,
    ValidationError,
    SandboxError,
)
from ..sandboxes import (
    BaseSandbox,
    SandboxConfig,
    SandboxResult,
    RestrictedPythonSandbox,
)
from ..validators import SyntaxValidator, ASTValidator, OutputValidator, ASTViolation

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """
    Comprehensive result of code execution.

    This class combines results from all stages of execution:
    validation, sandbox execution, and output validation.

    Attributes:
        success: Overall success/failure
        stdout: Standard output from execution
        stderr: Standard error from execution
        return_value: Value returned by code (if any)
        error_message: Error description (if failed)
        error_type: Exception class name (if failed)
        execution_time_seconds: Time spent in sandbox
        total_time_seconds: Total time including validation
        validation_violations: List of pre-execution violations found
        output_violations: List of post-execution violations found
        was_output_redacted: Whether sensitive data was redacted
        sandbox_metadata: Sandbox-specific information
        timestamp: When execution started
    """

    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Any = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    execution_time_seconds: Optional[float] = None
    total_time_seconds: Optional[float] = None
    validation_violations: List[Dict] = field(default_factory=list)
    output_violations: List[Dict] = field(default_factory=list)
    was_output_redacted: bool = False
    sandbox_metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.

        This format is suitable for:
            - API responses
            - Database storage
            - Logging
            - Providing feedback to LLMs

        Returns:
            Dictionary representation of execution result
        """
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_value": str(self.return_value) if self.return_value else None,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "execution_time_seconds": self.execution_time_seconds,
            "total_time_seconds": self.total_time_seconds,
            "validation_violations": self.validation_violations,
            "output_violations": self.output_violations,
            "was_output_redacted": self.was_output_redacted,
            "sandbox_metadata": self.sandbox_metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class CodeExecutor:
    """
    Orchestrates safe execution of LLM-generated Python code.

    This class coordinates multiple security layers:
        1. Syntax validation
        2. AST security analysis
        3. Sandboxed execution
        4. Output validation and sanitization
        5. Comprehensive logging

    Thread Safety:
        Instances are NOT thread-safe. Create a new executor per request.

    Usage:
        >>> # Basic usage
        >>> executor = CodeExecutor(user_id=123)
        >>> result = executor.execute("x = 1 + 1\\nprint(x)")

        >>> # With custom configuration
        >>> executor = CodeExecutor(
        ...     user_id=123,
        ...     sandbox_config=SandboxConfig(timeout_seconds=10),
        ...     enable_ast_validation=True,
        ... )
        >>> result = executor.execute(code)

        >>> # Check result
        >>> if result.success:
        ...     print(f"Output: {result.stdout}")
        ...     if result.output_violations:
        ...         print("Note: Output had violations (may be redacted)")
        ... else:
        ...     print(f"Error: {result.error_message}")

    Architecture Note:
        This class uses dependency injection for validators and sandboxes,
        making it easy to test and customize behavior.
    """

    def __init__(
        self,
        user_id: Optional[int] = None,
        sandbox_type: Type[BaseSandbox] = RestrictedPythonSandbox,
        sandbox_config: Optional[SandboxConfig] = None,
        enable_syntax_validation: bool = True,
        enable_ast_validation: bool = True,
        enable_output_validation: bool = True,
        redact_sensitive_output: bool = True,
        log_executions: bool = True,
    ):
        """
        Initialize code executor.

        Args:
            user_id: ID of user executing code (for logging/rate limiting)
            sandbox_type: Sandbox class to use (default: RestrictedPythonSandbox)
            sandbox_config: Custom sandbox configuration (uses defaults if None)
            enable_syntax_validation: Whether to validate syntax before execution
            enable_ast_validation: Whether to perform AST security analysis
            enable_output_validation: Whether to validate/sanitize output
            redact_sensitive_output: Whether to redact sensitive data in output
            log_executions: Whether to log execution attempts

        Example:
            >>> # Minimal security for trusted code
            >>> executor = CodeExecutor(
            ...     enable_ast_validation=False,
            ...     enable_output_validation=False,
            ... )

            >>> # Maximum security for untrusted code
            >>> executor = CodeExecutor(
            ...     enable_ast_validation=True,
            ...     enable_output_validation=True,
            ...     redact_sensitive_output=True,
            ...     sandbox_config=SandboxConfig(
            ...         timeout_seconds=5,
            ...         max_memory_mb=256,
            ...     ),
            ... )
        """
        self.user_id = user_id
        self.sandbox_type = sandbox_type
        self.sandbox_config = sandbox_config or SandboxConfig(user_id=user_id)
        self.enable_syntax_validation = enable_syntax_validation
        self.enable_ast_validation = enable_ast_validation
        self.enable_output_validation = enable_output_validation
        self.redact_sensitive_output = redact_sensitive_output
        self.log_executions = log_executions

        # Initialize validators
        self.syntax_validator = SyntaxValidator()
        self.ast_validator = ASTValidator()
        self.output_validator = OutputValidator(
            max_output_bytes=self.sandbox_config.max_output_bytes,
            redact=self.redact_sensitive_output,
        )

    def execute(
        self,
        code: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute Python code with full security pipeline.

        Execution Flow:
            1. Log execution attempt
            2. Validate syntax (if enabled)
            3. Analyze AST for security violations (if enabled)
            4. Create sandbox instance
            5. Execute code in sandbox with timeout
            6. Validate and sanitize output (if enabled)
            7. Log result
            8. Return comprehensive result

        Args:
            code: Python code to execute
            context: Additional data to make available during execution

        Returns:
            ExecutionResult with complete execution information

        Example:
            >>> executor = CodeExecutor()
            >>> result = executor.execute(
            ...     code="print(context['name'])",
            ...     context={"name": "Alice"},
            ... )
            >>> print(result.stdout)  # "Alice"

        Thread Safety:
            This method is NOT thread-safe. Use separate executor instances
            for concurrent executions.
        """
        start_time = time.time()

        # Log execution attempt
        if self.log_executions:
            logger.info(
                f"Code execution requested",
                extra={
                    "user_id": self.user_id,
                    "code_length": len(code),
                    "has_context": bool(context),
                },
            )

        try:
            # Step 1: Syntax validation
            validation_violations = []
            if self.enable_syntax_validation:
                validation_violations.extend(self._validate_syntax(code))

            # Step 2: AST security analysis
            if self.enable_ast_validation:
                validation_violations.extend(self._validate_ast(code))

            # If validation found violations, don't execute
            if validation_violations:
                return self._create_validation_failure_result(
                    validation_violations, time.time() - start_time
                )

            # Step 3: Execute in sandbox
            sandbox_result = self._execute_in_sandbox(code, context)

            # Step 4: Validate output
            output_violations = []
            sanitized_stdout = sandbox_result.stdout
            was_redacted = False

            if self.enable_output_validation and sandbox_result.success:
                output_result = self.output_validator.validate(sandbox_result.stdout)
                output_violations = output_result.violations
                sanitized_stdout = output_result.sanitized_output
                was_redacted = output_result.was_redacted

            # Step 5: Build comprehensive result
            total_time = time.time() - start_time
            result = ExecutionResult(
                success=sandbox_result.success,
                stdout=sanitized_stdout,
                stderr=sandbox_result.stderr,
                return_value=sandbox_result.return_value,
                error_message=sandbox_result.error_message,
                error_type=sandbox_result.error_type,
                execution_time_seconds=sandbox_result.execution_time_seconds,
                total_time_seconds=total_time,
                validation_violations=validation_violations,
                output_violations=output_violations,
                was_output_redacted=was_redacted,
                sandbox_metadata=sandbox_result.metadata,
            )

            # Log result
            if self.log_executions:
                self._log_execution_result(result, code)

            return result

        except Exception as e:
            # Unexpected error during execution pipeline
            logger.exception(
                "Unexpected error in code execution pipeline",
                extra={"user_id": self.user_id},
            )
            return ExecutionResult(
                success=False,
                error_message=f"Execution pipeline error: {str(e)}",
                error_type=type(e).__name__,
                total_time_seconds=time.time() - start_time,
            )

    def _validate_syntax(self, code: str) -> List[Dict]:
        """
        Validate code syntax.

        Returns:
            List of violation dictionaries (empty if valid)
        """
        result = self.syntax_validator.validate(code)
        if not result.is_valid:
            return [
                {
                    "type": "syntax_error",
                    "message": result.error_message,
                    "line_number": result.line_number,
                    "offset": result.offset,
                    "severity": "high",
                }
            ]
        return []

    def _validate_ast(self, code: str) -> List[Dict]:
        """
        Perform AST security analysis.

        Returns:
            List of violation dictionaries (empty if no violations)
        """
        violations = self.ast_validator.validate(code)
        return [v.to_dict() for v in violations]

    def _execute_in_sandbox(
        self,
        code: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SandboxResult:
        """
        Execute code in configured sandbox.

        Args:
            code: Code to execute
            context: Execution context data

        Returns:
            SandboxResult from sandbox execution
        """
        # Create config with context
        config = SandboxConfig(
            timeout_seconds=self.sandbox_config.timeout_seconds,
            max_memory_mb=self.sandbox_config.max_memory_mb,
            max_output_bytes=self.sandbox_config.max_output_bytes,
            enable_network=self.sandbox_config.enable_network,
            allowed_imports=self.sandbox_config.allowed_imports,
            execution_context=context or {},
            user_id=self.user_id,
        )

        # Create and execute in sandbox
        sandbox = self.sandbox_type()
        try:
            return sandbox.execute(code, config)
        finally:
            sandbox.cleanup()

    def _create_validation_failure_result(
        self,
        violations: List[Dict],
        total_time: float,
    ) -> ExecutionResult:
        """
        Create result object for validation failures.

        Args:
            violations: List of validation violations
            total_time: Time spent on validation

        Returns:
            ExecutionResult indicating validation failure
        """
        # Categorize violations by severity
        high_severity = [v for v in violations if v.get("severity") == "high"]

        # Build error message
        error_message = "Code validation failed: "
        if high_severity:
            error_message += f"{len(high_severity)} high-severity violations"
        else:
            error_message += f"{len(violations)} violations"

        return ExecutionResult(
            success=False,
            error_message=error_message,
            error_type="ValidationError",
            total_time_seconds=total_time,
            validation_violations=violations,
        )

    def _log_execution_result(self, result: ExecutionResult, code: str) -> None:
        """
        Log execution result for monitoring and audit.

        Args:
            result: Execution result to log
            code: Code that was executed (truncated in logs)
        """
        log_data = {
            "user_id": self.user_id,
            "success": result.success,
            "execution_time": result.execution_time_seconds,
            "total_time": result.total_time_seconds,
            "validation_violations": len(result.validation_violations),
            "output_violations": len(result.output_violations),
            "was_redacted": result.was_output_redacted,
            "code_preview": code[:100] + ("..." if len(code) > 100 else ""),
            "sandbox_type": result.sandbox_metadata.get("sandbox_type"),
        }

        if result.success:
            logger.info("Code execution succeeded", extra=log_data)
        else:
            log_data["error_type"] = result.error_type
            logger.warning("Code execution failed", extra=log_data)

            # High-priority logging for security violations
            if result.validation_violations:
                high_severity = [
                    v
                    for v in result.validation_violations
                    if v.get("severity") == "high"
                ]
                if high_severity:
                    logger.warning(
                        f"High-severity security violations detected: {high_severity}",
                        extra={"user_id": self.user_id, "violations": high_severity},
                    )
