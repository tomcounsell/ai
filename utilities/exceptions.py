"""
AI System Exception Hierarchy and Error Handling Framework

This module provides a comprehensive exception hierarchy and error categorization
system for the AI rebuild project. It includes recovery strategies and 
structured error handling patterns.
"""

import logging
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union, Callable
from datetime import datetime
import traceback


class ErrorCategory(Enum):
    """Categorizes errors for better handling and reporting."""
    CONFIGURATION = auto()
    INTEGRATION = auto()
    RESOURCE = auto()
    VALIDATION = auto()
    AUTHENTICATION = auto()
    RATE_LIMIT = auto()
    SYSTEM = auto()
    NETWORK = auto()
    DATA = auto()
    PERMISSION = auto()


class ErrorSeverity(Enum):
    """Defines error severity levels."""
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()


class RecoveryStrategy(Enum):
    """Defines available recovery strategies."""
    RETRY = auto()
    FALLBACK = auto()
    IGNORE = auto()
    ABORT = auto()
    ESCALATE = auto()
    CIRCUIT_BREAKER = auto()


class AISystemError(Exception):
    """
    Base exception for all AI system errors.
    
    Provides structured error handling with categorization,
    severity levels, and recovery strategies.
    """
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.SYSTEM,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        recovery_strategy: RecoveryStrategy = RecoveryStrategy.ABORT,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        error_code: Optional[str] = None
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.recovery_strategy = recovery_strategy
        self.context = context or {}
        self.cause = cause
        self.error_code = error_code
        self.timestamp = datetime.now().replace(tzinfo=None)
        self.traceback_info = traceback.format_exc()
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization."""
        # Serialize context safely
        serialized_context = {}
        for key, value in self.context.items():
            if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                serialized_context[key] = value
            else:
                serialized_context[key] = str(value)
        
        return {
            'message': self.message,
            'category': self.category.name,
            'severity': self.severity.name,
            'recovery_strategy': self.recovery_strategy.name,
            'context': serialized_context,
            'error_code': self.error_code,
            'timestamp': self.timestamp.isoformat(),
            'cause': str(self.cause) if self.cause else None,
            'traceback': self.traceback_info
        }
    
    def __str__(self) -> str:
        """String representation with structured information."""
        parts = [
            f"[{self.category.name}] {self.message}",
            f"Severity: {self.severity.name}",
            f"Recovery: {self.recovery_strategy.name}"
        ]
        if self.error_code:
            parts.append(f"Code: {self.error_code}")
        if self.context:
            parts.append(f"Context: {self.context}")
        return " | ".join(parts)


class ConfigurationError(AISystemError):
    """Raised when configuration is invalid or missing."""
    
    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        config_value: Optional[Any] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'config_key': config_key,
            'config_value': config_value
        })
        super().__init__(
            message,
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.ABORT,
            context=context,
            **kwargs
        )
        self.config_key = config_key
        self.config_value = config_value


class IntegrationError(AISystemError):
    """Raised when external service integration fails."""
    
    def __init__(
        self,
        message: str,
        service_name: Optional[str] = None,
        endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'service_name': service_name,
            'endpoint': endpoint,
            'status_code': status_code
        })
        super().__init__(
            message,
            category=ErrorCategory.INTEGRATION,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.RETRY,
            context=context,
            **kwargs
        )
        self.service_name = service_name
        self.endpoint = endpoint
        self.status_code = status_code


class ResourceError(AISystemError):
    """Raised when system resources are unavailable or exhausted."""
    
    def __init__(
        self,
        message: str,
        resource_type: Optional[str] = None,
        resource_limit: Optional[Union[int, float]] = None,
        current_usage: Optional[Union[int, float]] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'resource_type': resource_type,
            'resource_limit': resource_limit,
            'current_usage': current_usage
        })
        super().__init__(
            message,
            category=ErrorCategory.RESOURCE,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.CIRCUIT_BREAKER,
            context=context,
            **kwargs
        )
        self.resource_type = resource_type
        self.resource_limit = resource_limit
        self.current_usage = current_usage


class ValidationError(AISystemError):
    """Raised when input validation fails."""
    
    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        field_value: Optional[Any] = None,
        validation_rules: Optional[List[str]] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'field_name': field_name,
            'field_value': field_value,
            'validation_rules': validation_rules
        })
        super().__init__(
            message,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            recovery_strategy=RecoveryStrategy.IGNORE,
            context=context,
            **kwargs
        )
        self.field_name = field_name
        self.field_value = field_value
        self.validation_rules = validation_rules


class AuthenticationError(AISystemError):
    """Raised when authentication fails."""
    
    def __init__(
        self,
        message: str,
        auth_method: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'auth_method': auth_method,
            'user_id': user_id
        })
        super().__init__(
            message,
            category=ErrorCategory.AUTHENTICATION,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.ABORT,
            context=context,
            **kwargs
        )
        self.auth_method = auth_method
        self.user_id = user_id


class RateLimitError(AISystemError):
    """Raised when rate limits are exceeded."""
    
    def __init__(
        self,
        message: str,
        service_name: Optional[str] = None,
        limit: Optional[int] = None,
        reset_time: Optional[datetime] = None,
        current_usage: Optional[int] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'service_name': service_name,
            'limit': limit,
            'reset_time': reset_time.isoformat() if reset_time else None,
            'current_usage': current_usage
        })
        super().__init__(
            message,
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            recovery_strategy=RecoveryStrategy.RETRY,
            context=context,
            **kwargs
        )
        self.service_name = service_name
        self.limit = limit
        self.reset_time = reset_time
        self.current_usage = current_usage


class NetworkError(AISystemError):
    """Raised when network operations fail."""
    
    def __init__(
        self,
        message: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: Optional[float] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'host': host,
            'port': port,
            'timeout': timeout
        })
        super().__init__(
            message,
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.RETRY,
            context=context,
            **kwargs
        )
        self.host = host
        self.port = port
        self.timeout = timeout


class DataError(AISystemError):
    """Raised when data operations fail."""
    
    def __init__(
        self,
        message: str,
        data_source: Optional[str] = None,
        operation: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'data_source': data_source,
            'operation': operation
        })
        super().__init__(
            message,
            category=ErrorCategory.DATA,
            severity=ErrorSeverity.MEDIUM,
            recovery_strategy=RecoveryStrategy.FALLBACK,
            context=context,
            **kwargs
        )
        self.data_source = data_source
        self.operation = operation


class PermissionError(AISystemError):
    """Raised when permission checks fail."""
    
    def __init__(
        self,
        message: str,
        resource: Optional[str] = None,
        operation: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context.update({
            'resource': resource,
            'operation': operation,
            'user_id': user_id
        })
        super().__init__(
            message,
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.ABORT,
            context=context,
            **kwargs
        )
        self.resource = resource
        self.operation = operation
        self.user_id = user_id


class ErrorHandler:
    """
    Centralized error handling with recovery strategies.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self.recovery_handlers: Dict[RecoveryStrategy, Callable] = {
            RecoveryStrategy.RETRY: self._handle_retry,
            RecoveryStrategy.FALLBACK: self._handle_fallback,
            RecoveryStrategy.IGNORE: self._handle_ignore,
            RecoveryStrategy.ABORT: self._handle_abort,
            RecoveryStrategy.ESCALATE: self._handle_escalate,
            RecoveryStrategy.CIRCUIT_BREAKER: self._handle_circuit_breaker
        }
        
    def handle_error(
        self,
        error: AISystemError,
        fallback_func: Optional[Callable] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Any:
        """
        Handle error according to its recovery strategy.
        
        Args:
            error: The error to handle
            fallback_func: Function to call for fallback strategy
            max_retries: Maximum number of retries
            retry_delay: Delay between retries in seconds
            
        Returns:
            Result based on recovery strategy
        """
        self.logger.error(f"Handling error: {error}")
        
        handler = self.recovery_handlers.get(error.recovery_strategy)
        if handler:
            return handler(
                error,
                fallback_func=fallback_func,
                max_retries=max_retries,
                retry_delay=retry_delay
            )
        else:
            self.logger.warning(f"No handler for recovery strategy: {error.recovery_strategy}")
            raise error
    
    def _handle_retry(self, error: AISystemError, **kwargs) -> None:
        """Handle retry recovery strategy."""
        max_retries = kwargs.get('max_retries', 3)
        retry_delay = kwargs.get('retry_delay', 1.0)
        
        self.logger.info(f"Retry strategy for {error.category.name} error")
        # Implementation would involve actual retry logic
        raise NotImplementedError("Retry logic to be implemented by specific handlers")
    
    def _handle_fallback(self, error: AISystemError, **kwargs) -> Any:
        """Handle fallback recovery strategy."""
        fallback_func = kwargs.get('fallback_func')
        
        self.logger.info(f"Fallback strategy for {error.category.name} error")
        if fallback_func:
            return fallback_func()
        else:
            self.logger.warning("No fallback function provided")
            raise error
    
    def _handle_ignore(self, error: AISystemError, **kwargs) -> None:
        """Handle ignore recovery strategy."""
        self.logger.warning(f"Ignoring {error.category.name} error: {error.message}")
        # Return None or appropriate default value
        return None
    
    def _handle_abort(self, error: AISystemError, **kwargs) -> None:
        """Handle abort recovery strategy."""
        self.logger.critical(f"Aborting due to {error.category.name} error: {error.message}")
        raise error
    
    def _handle_escalate(self, error: AISystemError, **kwargs) -> None:
        """Handle escalate recovery strategy."""
        self.logger.critical(f"Escalating {error.category.name} error: {error.message}")
        # Implementation would involve notification/alerting systems
        raise error
    
    def _handle_circuit_breaker(self, error: AISystemError, **kwargs) -> None:
        """Handle circuit breaker recovery strategy."""
        self.logger.warning(f"Circuit breaker triggered for {error.category.name} error")
        # Implementation would involve circuit breaker pattern
        raise error


def categorize_exception(exc: Exception) -> AISystemError:
    """
    Categorize a standard exception into an AI system error.
    
    Args:
        exc: The exception to categorize
        
    Returns:
        Categorized AISystemError
    """
    if isinstance(exc, AISystemError):
        return exc
    
    # Map common exception types to AI system errors
    if isinstance(exc, (ValueError, TypeError)):
        return ValidationError(
            str(exc),
            cause=exc
        )
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        return NetworkError(
            str(exc),
            cause=exc
        )
    elif isinstance(exc, FileNotFoundError):
        return ResourceError(
            str(exc),
            resource_type="file",
            cause=exc
        )
    elif isinstance(exc, __builtins__.get('PermissionError', type(None))):
        # Use module-scoped PermissionError (our custom class)
        return globals()['PermissionError'](
            str(exc),
            cause=exc
        )
    else:
        return AISystemError(
            str(exc),
            category=ErrorCategory.SYSTEM,
            cause=exc
        )


# Global error handler instance
_global_error_handler = ErrorHandler()


def handle_error(
    error: Union[Exception, AISystemError],
    **kwargs
) -> Any:
    """
    Global error handling function.
    
    Args:
        error: The error to handle
        **kwargs: Additional arguments for error handling
        
    Returns:
        Result based on recovery strategy
    """
    if not isinstance(error, AISystemError):
        error = categorize_exception(error)
    
    return _global_error_handler.handle_error(error, **kwargs)