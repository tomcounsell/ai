"""
UnifiedErrorManager: Comprehensive error handling for message processing.

Provides centralized error categorization, recovery strategies, and user-friendly messages.
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


# Using generic exceptions since we're using pyrogram
class BadRequest(Exception):  # noqa: N818
    pass


class NetworkError(Exception):
    pass


class TimedOut(Exception):  # noqa: N818
    pass


from integrations.telegram.models import MessageContext

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for handling strategies."""

    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    PERMISSION = "permission"
    VALIDATION = "validation"
    PROCESSING = "processing"
    EXTERNAL_SERVICE = "external_service"
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """Error severity levels."""

    LOW = "low"  # Can be ignored or logged
    MEDIUM = "medium"  # Should be handled gracefully
    HIGH = "high"  # Requires user notification
    CRITICAL = "critical"  # System failure, needs immediate attention


class UnifiedErrorManager:
    """Comprehensive error handling for message processing."""

    def __init__(self):
        """Initialize error manager with retry policies."""
        self.retry_policies = {
            ErrorCategory.NETWORK: {"max_retries": 3, "initial_delay": 1, "backoff_factor": 2},
            ErrorCategory.RATE_LIMIT: {"max_retries": 1, "initial_delay": 60, "backoff_factor": 1},
            ErrorCategory.EXTERNAL_SERVICE: {
                "max_retries": 2,
                "initial_delay": 5,
                "backoff_factor": 3,
            },
        }

        # Track errors for monitoring
        self.error_counts: dict[str, int] = {}
        self.last_errors: dict[str, datetime] = {}

    def handle_processing_error(
        self, error: Exception, context: MessageContext | None = None
    ) -> dict[str, Any]:
        """
        Centralized error categorization and response.

        Returns:
            Dict containing:
            - category: ErrorCategory
            - severity: ErrorSeverity
            - should_retry: bool
            - retry_after: Optional[int] (seconds)
            - user_message: str
            - log_message: str
            - metadata: Dict[str, Any]
        """
        category = self._categorize_error(error)
        severity = self._determine_severity(error, category)

        # Track error
        error_key = f"{category.value}:{type(error).__name__}"
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1
        self.last_errors[error_key] = datetime.now()

        # Determine retry strategy
        should_retry, retry_after = self._should_retry(error, category)

        # Generate messages
        user_message = self._create_user_message(error, category, severity)
        log_message = self._create_log_message(error, category, context)

        result = {
            "category": category,
            "severity": severity,
            "should_retry": should_retry,
            "retry_after": retry_after,
            "user_message": user_message,
            "log_message": log_message,
            "metadata": {
                "error_type": type(error).__name__,
                "error_count": self.error_counts[error_key],
                "chat_id": context.chat_id if context else None,
                "username": context.username if context else None,
            },
        }

        # Log based on severity
        if severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message, exc_info=True)
        elif severity == ErrorSeverity.HIGH:
            logger.error(log_message, exc_info=True)
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message)
        else:
            logger.info(log_message)

        return result

    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize error for appropriate handling."""
        error_msg = str(error).lower()

        # Network errors
        if isinstance(error, TimedOut | NetworkError | ConnectionError):
            return ErrorCategory.NETWORK

        # Rate limiting
        if "rate limit" in error_msg or "too many requests" in error_msg:
            return ErrorCategory.RATE_LIMIT

        # Permission errors
        if isinstance(error, BadRequest):
            if any(
                phrase in error_msg
                for phrase in [
                    "not enough rights",
                    "permission",
                    "forbidden",
                    "chat not found",
                    "user not found",
                    "bot was blocked",
                ]
            ):
                return ErrorCategory.PERMISSION

        # Validation errors
        if isinstance(error, ValueError | TypeError | KeyError):
            return ErrorCategory.VALIDATION

        # External service errors
        if any(
            service in error_msg
            for service in ["api", "openai", "anthropic", "perplexity", "notion"]
        ):
            return ErrorCategory.EXTERNAL_SERVICE

        # Processing errors
        if "processing" in error_msg or "handler" in error_msg:
            return ErrorCategory.PROCESSING

        return ErrorCategory.UNKNOWN

    def _determine_severity(self, error: Exception, category: ErrorCategory) -> ErrorSeverity:
        """Determine error severity for logging and alerting."""
        # Critical errors
        if category == ErrorCategory.UNKNOWN and "critical" in str(error).lower():
            return ErrorSeverity.CRITICAL

        # High severity
        if category in [ErrorCategory.PERMISSION, ErrorCategory.PROCESSING]:
            return ErrorSeverity.HIGH

        # Medium severity
        if category in [ErrorCategory.RATE_LIMIT, ErrorCategory.EXTERNAL_SERVICE]:
            return ErrorSeverity.MEDIUM

        # Low severity
        if category in [ErrorCategory.NETWORK, ErrorCategory.VALIDATION]:
            return ErrorSeverity.LOW

        return ErrorSeverity.MEDIUM

    def _should_retry(self, error: Exception, category: ErrorCategory) -> tuple[bool, int | None]:
        """Determine if error warrants retry and delay."""
        # Never retry permission errors
        if category == ErrorCategory.PERMISSION:
            return False, None

        # Check retry policy
        policy = self.retry_policies.get(category)
        if not policy:
            return False, None

        # Check error frequency
        error_key = f"{category.value}:{type(error).__name__}"
        error_count = self.error_counts.get(error_key, 0)

        if error_count > policy["max_retries"]:
            return False, None

        # Calculate retry delay
        retry_delay = policy["initial_delay"] * (policy["backoff_factor"] ** (error_count - 1))

        return True, int(retry_delay)

    def _create_user_message(
        self, error: Exception, category: ErrorCategory, severity: ErrorSeverity
    ) -> str:
        """Generate user-friendly error message."""
        if severity == ErrorSeverity.LOW:
            # Don't bother user with low severity errors
            return ""

        messages = {
            ErrorCategory.NETWORK: "🌐 Network issue detected. Please try again in a moment.",
            ErrorCategory.RATE_LIMIT: "🚦 Rate limit reached. Please wait a minute before trying again.",
            ErrorCategory.PERMISSION: "🔒 Permission denied. Please check your access rights.",
            ErrorCategory.VALIDATION: "❓ Invalid request. Please check your input and try again.",
            ErrorCategory.PROCESSING: "⚙️ Processing error. Please try a simpler request.",
            ErrorCategory.EXTERNAL_SERVICE: "🔧 External service temporarily unavailable. Please try again later.",
            ErrorCategory.UNKNOWN: "❌ An unexpected error occurred. Please try again or contact support.",
        }

        base_message = messages.get(category, messages[ErrorCategory.UNKNOWN])

        # Add specific details for certain errors
        if isinstance(error, BadRequest) and "message to reply to not found" in str(error).lower():
            return "⚠️ The message you're replying to was deleted."

        return base_message

    def _create_log_message(
        self, error: Exception, category: ErrorCategory, context: MessageContext | None
    ) -> str:
        """Create detailed log message for debugging."""
        parts = [
            f"Error category: {category.value}",
            f"Error type: {type(error).__name__}",
            f"Error message: {str(error)}",
        ]

        if context:
            parts.extend(
                [
                    f"Chat ID: {context.chat_id}",
                    f"Username: {context.username}",
                    f"Message: {context.cleaned_text[:50]}..."
                    if len(context.cleaned_text) > 50
                    else f"Message: {context.cleaned_text}",
                ]
            )

        error_key = f"{category.value}:{type(error).__name__}"
        if error_key in self.error_counts:
            parts.append(f"Occurrence: {self.error_counts[error_key]}")

        return " | ".join(parts)

    def create_fallback_response(self, error: Exception) -> str:
        """Generate user-friendly error messages for response delivery."""
        error_info = self.handle_processing_error(error)
        return error_info["user_message"] or "❌ An error occurred. Please try again."

    def get_error_statistics(self) -> dict[str, Any]:
        """Get error statistics for monitoring."""
        total_errors = sum(self.error_counts.values())

        # Group by category
        category_counts = {}
        for error_key, count in self.error_counts.items():
            category = error_key.split(":")[0]
            category_counts[category] = category_counts.get(category, 0) + count

        # Find most recent errors
        recent_errors = []
        now = datetime.now()
        for error_key, last_time in self.last_errors.items():
            if (now - last_time) < timedelta(hours=1):
                recent_errors.append(
                    {
                        "error": error_key,
                        "count": self.error_counts[error_key],
                        "last_seen": last_time.isoformat(),
                    }
                )

        return {
            "total_errors": total_errors,
            "by_category": category_counts,
            "recent_errors": sorted(recent_errors, key=lambda x: x["count"], reverse=True)[:10],
            "error_rate": self._calculate_error_rate(),
        }

    def _calculate_error_rate(self) -> float:
        """Calculate error rate over last hour."""
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)

        recent_count = 0
        for error_key, last_time in self.last_errors.items():
            if last_time > hour_ago:
                recent_count += self.error_counts[error_key]

        # This would need total message count from processor
        return 0.0  # Placeholder

    def reset_statistics(self):
        """Reset error statistics."""
        self.error_counts.clear()
        self.last_errors.clear()
