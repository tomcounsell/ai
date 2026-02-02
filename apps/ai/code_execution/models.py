"""
Models for tracking code execution history.

These models provide:
    - Audit trail of all code executions
    - Performance metrics tracking
    - Security violation monitoring
    - User execution history

Note: These are optional. You can use the code_execution module without
persisting execution records to the database.
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.postgres.fields import ArrayField
from apps.common.behaviors import Timestampable

User = get_user_model()


class CodeExecution(Timestampable, models.Model):
    """
    Record of a code execution attempt.

    This model stores comprehensive information about each execution for:
        - Security auditing
        - Performance monitoring
        - Debugging
        - User analytics

    Fields:
        user: User who executed the code
        code: The Python code that was executed
        code_hash: SHA-256 hash of code (for deduplication)
        context_data: JSON field with execution context
        success: Whether execution succeeded
        stdout: Standard output from execution
        stderr: Standard error output
        error_message: Error description if failed
        error_type: Exception class name
        execution_time_seconds: Time spent in sandbox
        total_time_seconds: Total time including validation
        validation_violations: List of pre-execution violations
        output_violations: List of post-execution violations
        was_output_redacted: Whether sensitive data was removed
        sandbox_type: Which sandbox was used
        timeout_seconds: Configured timeout
        max_memory_mb: Configured memory limit

    Indexes:
        - user (for per-user queries)
        - created_at (for time-based queries)
        - success (for filtering by outcome)
        - code_hash (for finding duplicate code)

    Usage:
        >>> from apps.ai.code_execution.models import CodeExecution
        >>> execution = CodeExecution.create_from_result(
        ...     user=request.user,
        ...     code="print('hello')",
        ...     result=execution_result,
        ... )
        >>> execution.save()
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="code_executions",
        help_text="User who executed the code",
    )

    code = models.TextField(help_text="Python code that was executed")

    code_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 hash of code for deduplication",
    )

    context_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Execution context data (sanitized)",
    )

    # Execution outcome
    success = models.BooleanField(
        db_index=True,
        help_text="Whether execution succeeded",
    )

    stdout = models.TextField(
        blank=True,
        help_text="Standard output from execution",
    )

    stderr = models.TextField(
        blank=True,
        help_text="Standard error output",
    )

    error_message = models.TextField(
        blank=True,
        help_text="Error description if execution failed",
    )

    error_type = models.CharField(
        max_length=100,
        blank=True,
        help_text="Exception class name",
    )

    # Performance metrics
    execution_time_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Time spent in sandbox (seconds)",
    )

    total_time_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Total time including validation (seconds)",
    )

    # Validation results
    validation_violations = models.JSONField(
        default=list,
        blank=True,
        help_text="Pre-execution security violations",
    )

    output_violations = models.JSONField(
        default=list,
        blank=True,
        help_text="Post-execution output violations",
    )

    was_output_redacted = models.BooleanField(
        default=False,
        help_text="Whether sensitive data was redacted from output",
    )

    # Configuration
    sandbox_type = models.CharField(
        max_length=100,
        default="RestrictedPythonSandbox",
        help_text="Sandbox implementation used",
    )

    timeout_seconds = models.FloatField(
        default=30.0,
        help_text="Configured execution timeout",
    )

    max_memory_mb = models.IntegerField(
        default=512,
        help_text="Configured memory limit (MB)",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["success", "-created_at"]),
            models.Index(fields=["code_hash"]),
        ]
        verbose_name = "Code Execution"
        verbose_name_plural = "Code Executions"

    def __str__(self) -> str:
        status = "✓" if self.success else "✗"
        return f"{status} {self.user.username} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    @classmethod
    def create_from_result(cls, user, code: str, result, context=None):
        """
        Create CodeExecution instance from ExecutionResult.

        Args:
            user: User who executed the code
            code: Python code that was executed
            result: ExecutionResult from CodeExecutor
            context: Optional execution context (will be sanitized)

        Returns:
            Unsaved CodeExecution instance

        Example:
            >>> execution = CodeExecution.create_from_result(
            ...     user=request.user,
            ...     code=code,
            ...     result=result,
            ...     context={"user_id": 123}
            ... )
            >>> execution.save()
        """
        import hashlib

        # Hash code for deduplication
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        # Sanitize context (remove sensitive data)
        sanitized_context = cls._sanitize_context(context or {})

        return cls(
            user=user,
            code=code,
            code_hash=code_hash,
            context_data=sanitized_context,
            success=result.success,
            stdout=result.stdout[:10000],  # Truncate very long output
            stderr=result.stderr[:10000],
            error_message=result.error_message or "",
            error_type=result.error_type or "",
            execution_time_seconds=result.execution_time_seconds,
            total_time_seconds=result.total_time_seconds,
            validation_violations=result.validation_violations,
            output_violations=result.output_violations,
            was_output_redacted=result.was_output_redacted,
            sandbox_type=result.sandbox_metadata.get("sandbox_type", "unknown"),
            # Config would need to be passed separately if different from defaults
        )

    @staticmethod
    def _sanitize_context(context: dict) -> dict:
        """
        Remove sensitive data from context before storing.

        Args:
            context: Execution context dictionary

        Returns:
            Sanitized context safe to store
        """
        # Create a copy
        sanitized = context.copy()

        # Remove common sensitive keys
        sensitive_keys = {
            "password",
            "secret",
            "token",
            "api_key",
            "private_key",
            "session_id",
        }

        for key in list(sanitized.keys()):
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = "[REDACTED]"

        return sanitized

    def get_violation_summary(self) -> dict:
        """
        Get summary of violations for this execution.

        Returns:
            Dictionary with violation counts by severity
        """
        all_violations = self.validation_violations + self.output_violations

        return {
            "total": len(all_violations),
            "high": sum(1 for v in all_violations if v.get("severity") == "high"),
            "medium": sum(1 for v in all_violations if v.get("severity") == "medium"),
            "low": sum(1 for v in all_violations if v.get("severity") == "low"),
        }

    @property
    def has_security_violations(self) -> bool:
        """Whether this execution had high-severity security violations."""
        return any(
            v.get("severity") == "high"
            for v in self.validation_violations + self.output_violations
        )


class CodeExecutionManager(models.Manager):
    """Custom manager for CodeExecution queries."""

    def for_user(self, user):
        """Get executions for a specific user."""
        return self.filter(user=user)

    def successful(self):
        """Get only successful executions."""
        return self.filter(success=True)

    def failed(self):
        """Get only failed executions."""
        return self.filter(success=False)

    def with_violations(self):
        """Get executions that had security violations."""
        return self.exclude(validation_violations=[]).exclude(output_violations=[])

    def by_code_hash(self, code: str):
        """Find executions of the same code (by hash)."""
        import hashlib

        code_hash = hashlib.sha256(code.encode()).hexdigest()
        return self.filter(code_hash=code_hash)


# Attach custom manager
CodeExecution.objects = CodeExecutionManager()


class ExecutionQuota(models.Model):
    """
    Track and enforce execution quotas per user.

    This model enables rate limiting and fair use policies:
        - Daily/monthly execution limits
        - Resource usage tracking
        - Cost tracking (for paid tiers)

    Fields:
        user: User this quota applies to
        period_start: Start of current quota period
        period_end: End of current quota period
        executions_used: Number of executions in this period
        executions_limit: Maximum allowed executions
        total_execution_seconds: Cumulative execution time
        execution_seconds_limit: Maximum execution time allowed
        is_unlimited: Whether user has unlimited quota

    Usage:
        >>> quota = ExecutionQuota.get_or_create_for_user(request.user)
        >>> if quota.can_execute():
        ...     # Execute code
        ...     quota.record_execution(result)
        ... else:
        ...     # Quota exceeded
        ...     raise QuotaExceededError()
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="execution_quota",
    )

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    executions_used = models.IntegerField(default=0)
    executions_limit = models.IntegerField(default=1000)

    total_execution_seconds = models.FloatField(default=0.0)
    execution_seconds_limit = models.FloatField(default=3600.0)  # 1 hour

    is_unlimited = models.BooleanField(
        default=False,
        help_text="Enterprise users with unlimited quota",
    )

    class Meta:
        verbose_name = "Execution Quota"
        verbose_name_plural = "Execution Quotas"

    def __str__(self) -> str:
        return f"{self.user.username}: {self.executions_used}/{self.executions_limit}"

    def can_execute(self) -> bool:
        """Check if user can execute more code."""
        if self.is_unlimited:
            return True

        return (
            self.executions_used < self.executions_limit
            and self.total_execution_seconds < self.execution_seconds_limit
        )

    def record_execution(self, result) -> None:
        """
        Record an execution against this quota.

        Args:
            result: ExecutionResult from code execution
        """
        self.executions_used += 1
        if result.execution_time_seconds:
            self.total_execution_seconds += result.execution_time_seconds
        self.save()

    def reset_quota(self) -> None:
        """Reset quota counters (called at period_end)."""
        from django.utils import timezone
        from datetime import timedelta

        self.executions_used = 0
        self.total_execution_seconds = 0.0
        self.period_start = timezone.now()
        self.period_end = self.period_start + timedelta(days=30)
        self.save()

    @classmethod
    def get_or_create_for_user(cls, user, executions_limit=1000):
        """
        Get or create quota for user.

        Args:
            user: User to get quota for
            executions_limit: Execution limit for new quotas

        Returns:
            ExecutionQuota instance
        """
        from django.utils import timezone
        from datetime import timedelta

        quota, created = cls.objects.get_or_create(
            user=user,
            defaults={
                "period_start": timezone.now(),
                "period_end": timezone.now() + timedelta(days=30),
                "executions_limit": executions_limit,
            },
        )

        # Check if period has ended
        if timezone.now() > quota.period_end:
            quota.reset_quota()

        return quota
