"""
Comprehensive tests for the AI System exception hierarchy and error handling framework.
"""

import pytest
import logging
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from utilities.exceptions import (
    AISystemError,
    ConfigurationError,
    IntegrationError,
    ResourceError,
    ValidationError,
    AuthenticationError,
    RateLimitError,
    NetworkError,
    DataError,
    PermissionError,
    ErrorCategory,
    ErrorSeverity,
    RecoveryStrategy,
    ErrorHandler,
    categorize_exception,
    handle_error
)


class TestAISystemError:
    """Test the base AISystemError class."""
    
    def test_basic_creation(self):
        """Test basic error creation with default values."""
        error = AISystemError("Test error")
        
        assert str(error) == "[SYSTEM] Test error | Severity: MEDIUM | Recovery: ABORT"
        assert error.message == "Test error"
        assert error.category == ErrorCategory.SYSTEM
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.recovery_strategy == RecoveryStrategy.ABORT
        assert error.context == {}
        assert error.cause is None
        assert error.error_code is None
        assert isinstance(error.timestamp, datetime)
    
    def test_full_creation(self):
        """Test error creation with all parameters."""
        context = {"key": "value", "number": 42}
        cause = ValueError("Original error")
        
        error = AISystemError(
            "Full error",
            category=ErrorCategory.INTEGRATION,
            severity=ErrorSeverity.HIGH,
            recovery_strategy=RecoveryStrategy.RETRY,
            context=context,
            cause=cause,
            error_code="E001"
        )
        
        assert error.message == "Full error"
        assert error.category == ErrorCategory.INTEGRATION
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.RETRY
        assert error.context == context
        assert error.cause == cause
        assert error.error_code == "E001"
    
    def test_to_dict(self):
        """Test error serialization to dictionary."""
        context = {"test": "value"}
        cause = ValueError("Test cause")
        
        error = AISystemError(
            "Test error",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
            recovery_strategy=RecoveryStrategy.IGNORE,
            context=context,
            cause=cause,
            error_code="TEST001"
        )
        
        error_dict = error.to_dict()
        
        assert error_dict["message"] == "Test error"
        assert error_dict["category"] == "VALIDATION"
        assert error_dict["severity"] == "LOW"
        assert error_dict["recovery_strategy"] == "IGNORE"
        assert error_dict["context"] == context
        assert error_dict["error_code"] == "TEST001"
        assert error_dict["cause"] == "Test cause"
        assert "timestamp" in error_dict
        assert "traceback" in error_dict
    
    def test_string_representation(self):
        """Test string representation with different parameters."""
        # Basic error
        error1 = AISystemError("Basic error")
        expected1 = "[SYSTEM] Basic error | Severity: MEDIUM | Recovery: ABORT"
        assert str(error1) == expected1
        
        # Error with code and context
        error2 = AISystemError(
            "Complex error",
            category=ErrorCategory.RESOURCE,
            severity=ErrorSeverity.CRITICAL,
            recovery_strategy=RecoveryStrategy.ESCALATE,
            context={"resource": "memory"},
            error_code="R001"
        )
        expected2 = ("[RESOURCE] Complex error | Severity: CRITICAL | "
                    "Recovery: ESCALATE | Code: R001 | Context: {'resource': 'memory'}")
        assert str(error2) == expected2


class TestSpecificErrors:
    """Test specific error types with their specialized attributes."""
    
    def test_configuration_error(self):
        """Test ConfigurationError creation and attributes."""
        error = ConfigurationError(
            "Invalid config",
            config_key="database.host",
            config_value="invalid_host"
        )
        
        assert error.category == ErrorCategory.CONFIGURATION
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.ABORT
        assert error.config_key == "database.host"
        assert error.config_value == "invalid_host"
        assert error.context["config_key"] == "database.host"
        assert error.context["config_value"] == "invalid_host"
    
    def test_integration_error(self):
        """Test IntegrationError creation and attributes."""
        error = IntegrationError(
            "API call failed",
            service_name="OpenAI",
            endpoint="/v1/completions",
            status_code=429
        )
        
        assert error.category == ErrorCategory.INTEGRATION
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.RETRY
        assert error.service_name == "OpenAI"
        assert error.endpoint == "/v1/completions"
        assert error.status_code == 429
    
    def test_resource_error(self):
        """Test ResourceError creation and attributes."""
        error = ResourceError(
            "Memory exhausted",
            resource_type="memory",
            resource_limit=1000,
            current_usage=1200
        )
        
        assert error.category == ErrorCategory.RESOURCE
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.CIRCUIT_BREAKER
        assert error.resource_type == "memory"
        assert error.resource_limit == 1000
        assert error.current_usage == 1200
    
    def test_validation_error(self):
        """Test ValidationError creation and attributes."""
        error = ValidationError(
            "Invalid email format",
            field_name="email",
            field_value="invalid-email",
            validation_rules=["email_format", "required"]
        )
        
        assert error.category == ErrorCategory.VALIDATION
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.recovery_strategy == RecoveryStrategy.IGNORE
        assert error.field_name == "email"
        assert error.field_value == "invalid-email"
        assert error.validation_rules == ["email_format", "required"]
    
    def test_authentication_error(self):
        """Test AuthenticationError creation and attributes."""
        error = AuthenticationError(
            "Invalid credentials",
            auth_method="jwt",
            user_id="user123"
        )
        
        assert error.category == ErrorCategory.AUTHENTICATION
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.ABORT
        assert error.auth_method == "jwt"
        assert error.user_id == "user123"
    
    def test_rate_limit_error(self):
        """Test RateLimitError creation and attributes."""
        reset_time = datetime.now() + timedelta(minutes=5)
        error = RateLimitError(
            "Rate limit exceeded",
            service_name="OpenAI",
            limit=100,
            reset_time=reset_time,
            current_usage=105
        )
        
        assert error.category == ErrorCategory.RATE_LIMIT
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.recovery_strategy == RecoveryStrategy.RETRY
        assert error.service_name == "OpenAI"
        assert error.limit == 100
        assert error.reset_time == reset_time
        assert error.current_usage == 105
    
    def test_network_error(self):
        """Test NetworkError creation and attributes."""
        error = NetworkError(
            "Connection timeout",
            host="api.openai.com",
            port=443,
            timeout=30.0
        )
        
        assert error.category == ErrorCategory.NETWORK
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.RETRY
        assert error.host == "api.openai.com"
        assert error.port == 443
        assert error.timeout == 30.0
    
    def test_data_error(self):
        """Test DataError creation and attributes."""
        error = DataError(
            "Data corruption detected",
            data_source="database",
            operation="read"
        )
        
        assert error.category == ErrorCategory.DATA
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.recovery_strategy == RecoveryStrategy.FALLBACK
        assert error.data_source == "database"
        assert error.operation == "read"
    
    def test_permission_error(self):
        """Test PermissionError creation and attributes."""
        error = PermissionError(
            "Access denied",
            resource="/admin/users",
            operation="read",
            user_id="user123"
        )
        
        assert error.category == ErrorCategory.PERMISSION
        assert error.severity == ErrorSeverity.HIGH
        assert error.recovery_strategy == RecoveryStrategy.ABORT
        assert error.resource == "/admin/users"
        assert error.operation == "read"
        assert error.user_id == "user123"


class TestErrorHandler:
    """Test the ErrorHandler class."""
    
    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger for testing."""
        return Mock(spec=logging.Logger)
    
    @pytest.fixture
    def error_handler(self, mock_logger):
        """Create an ErrorHandler instance with mock logger."""
        return ErrorHandler(mock_logger)
    
    def test_initialization(self, mock_logger):
        """Test ErrorHandler initialization."""
        handler = ErrorHandler(mock_logger)
        assert handler.logger == mock_logger
        assert len(handler.recovery_handlers) == 6
        
        # Test default logger creation
        handler_default = ErrorHandler()
        assert handler_default.logger is not None
    
    def test_handle_ignore_strategy(self, error_handler, mock_logger):
        """Test handling of IGNORE recovery strategy."""
        error = ValidationError("Test validation error")
        
        result = error_handler.handle_error(error)
        
        assert result is None
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_called_once()
    
    def test_handle_abort_strategy(self, error_handler, mock_logger):
        """Test handling of ABORT recovery strategy."""
        error = ConfigurationError("Test config error")
        
        with pytest.raises(ConfigurationError):
            error_handler.handle_error(error)
        
        mock_logger.error.assert_called_once()
        mock_logger.critical.assert_called_once()
    
    def test_handle_fallback_strategy(self, error_handler, mock_logger):
        """Test handling of FALLBACK recovery strategy."""
        error = DataError("Test data error")
        fallback_result = "fallback_value"
        fallback_func = Mock(return_value=fallback_result)
        
        result = error_handler.handle_error(error, fallback_func=fallback_func)
        
        assert result == fallback_result
        fallback_func.assert_called_once()
        mock_logger.info.assert_called_once()
    
    def test_handle_fallback_strategy_no_function(self, error_handler, mock_logger):
        """Test handling of FALLBACK recovery strategy without fallback function."""
        error = DataError("Test data error")
        
        with pytest.raises(DataError):
            error_handler.handle_error(error)
        
        mock_logger.warning.assert_called_once()
    
    def test_handle_retry_strategy(self, error_handler, mock_logger):
        """Test handling of RETRY recovery strategy."""
        error = IntegrationError("Test integration error")
        
        with pytest.raises(NotImplementedError):
            error_handler.handle_error(error)
        
        mock_logger.info.assert_called_once()
    
    def test_handle_escalate_strategy(self, error_handler, mock_logger):
        """Test handling of ESCALATE recovery strategy."""
        error = AISystemError(
            "Critical error",
            recovery_strategy=RecoveryStrategy.ESCALATE
        )
        
        with pytest.raises(AISystemError):
            error_handler.handle_error(error)
        
        mock_logger.critical.assert_called_once()
    
    def test_handle_circuit_breaker_strategy(self, error_handler, mock_logger):
        """Test handling of CIRCUIT_BREAKER recovery strategy."""
        error = ResourceError("Resource exhausted")
        
        with pytest.raises(ResourceError):
            error_handler.handle_error(error)
        
        mock_logger.warning.assert_called_once()
    
    def test_unknown_recovery_strategy(self, error_handler, mock_logger):
        """Test handling of unknown recovery strategy."""
        # Create error with invalid recovery strategy
        error = AISystemError("Test error")
        
        # Mock the recovery_handlers to not contain the strategy
        original_handlers = error_handler.recovery_handlers.copy()
        error_handler.recovery_handlers.clear()
        
        try:
            with pytest.raises(AISystemError):
                error_handler.handle_error(error)
            
            mock_logger.warning.assert_called_once()
        finally:
            # Restore original handlers
            error_handler.recovery_handlers = original_handlers


class TestCategorizeException:
    """Test the categorize_exception function."""
    
    def test_categorize_ai_system_error(self):
        """Test that AISystemError instances are returned unchanged."""
        original_error = ConfigurationError("Config error")
        result = categorize_exception(original_error)
        assert result is original_error
    
    def test_categorize_value_error(self):
        """Test categorization of ValueError."""
        original_error = ValueError("Invalid value")
        result = categorize_exception(original_error)
        
        assert isinstance(result, ValidationError)
        assert result.message == "Invalid value"
        assert result.cause == original_error
    
    def test_categorize_type_error(self):
        """Test categorization of TypeError."""
        original_error = TypeError("Type mismatch")
        result = categorize_exception(original_error)
        
        assert isinstance(result, ValidationError)
        assert result.message == "Type mismatch"
        assert result.cause == original_error
    
    def test_categorize_connection_error(self):
        """Test categorization of ConnectionError."""
        original_error = ConnectionError("Connection failed")
        result = categorize_exception(original_error)
        
        assert isinstance(result, NetworkError)
        assert result.message == "Connection failed"
        assert result.cause == original_error
    
    def test_categorize_timeout_error(self):
        """Test categorization of TimeoutError."""
        original_error = TimeoutError("Request timeout")
        result = categorize_exception(original_error)
        
        assert isinstance(result, NetworkError)
        assert result.message == "Request timeout"
        assert result.cause == original_error
    
    def test_categorize_file_not_found_error(self):
        """Test categorization of FileNotFoundError."""
        original_error = FileNotFoundError("File not found")
        result = categorize_exception(original_error)
        
        assert isinstance(result, ResourceError)
        assert result.message == "File not found"
        assert result.resource_type == "file"
        assert result.cause == original_error
    
    def test_categorize_permission_error_builtin(self):
        """Test categorization of built-in PermissionError."""
        import builtins
        original_error = builtins.PermissionError("Permission denied")
        result = categorize_exception(original_error)
        
        assert isinstance(result, PermissionError)
        assert result.message == "Permission denied"
        assert result.cause == original_error
    
    def test_categorize_generic_exception(self):
        """Test categorization of generic exceptions."""
        original_error = RuntimeError("Runtime error")
        result = categorize_exception(original_error)
        
        assert isinstance(result, AISystemError)
        assert result.message == "Runtime error"
        assert result.category == ErrorCategory.SYSTEM
        assert result.cause == original_error


class TestGlobalErrorHandling:
    """Test the global handle_error function."""
    
    def test_handle_ai_system_error(self):
        """Test handling of AISystemError through global function."""
        error = ValidationError("Test error")
        
        # Mock the global error handler
        with patch('utilities.exceptions._global_error_handler') as mock_handler:
            mock_handler.handle_error.return_value = "handled"
            
            result = handle_error(error)
            
            assert result == "handled"
            mock_handler.handle_error.assert_called_once_with(error)
    
    def test_handle_standard_exception(self):
        """Test handling of standard exceptions through global function."""
        error = ValueError("Standard error")
        
        with patch('utilities.exceptions._global_error_handler') as mock_handler:
            with patch('utilities.exceptions.categorize_exception') as mock_categorize:
                categorized_error = ValidationError("Categorized error")
                mock_categorize.return_value = categorized_error
                mock_handler.handle_error.return_value = "handled"
                
                result = handle_error(error)
                
                assert result == "handled"
                mock_categorize.assert_called_once_with(error)
                mock_handler.handle_error.assert_called_once_with(categorized_error)


class TestEnumClasses:
    """Test the enum classes."""
    
    def test_error_category_enum(self):
        """Test ErrorCategory enum values."""
        assert ErrorCategory.CONFIGURATION.name == "CONFIGURATION"
        assert ErrorCategory.INTEGRATION.name == "INTEGRATION"
        assert ErrorCategory.RESOURCE.name == "RESOURCE"
        assert ErrorCategory.VALIDATION.name == "VALIDATION"
        assert ErrorCategory.AUTHENTICATION.name == "AUTHENTICATION"
        assert ErrorCategory.RATE_LIMIT.name == "RATE_LIMIT"
        assert ErrorCategory.SYSTEM.name == "SYSTEM"
        assert ErrorCategory.NETWORK.name == "NETWORK"
        assert ErrorCategory.DATA.name == "DATA"
        assert ErrorCategory.PERMISSION.name == "PERMISSION"
    
    def test_error_severity_enum(self):
        """Test ErrorSeverity enum values."""
        assert ErrorSeverity.LOW.name == "LOW"
        assert ErrorSeverity.MEDIUM.name == "MEDIUM"
        assert ErrorSeverity.HIGH.name == "HIGH"
        assert ErrorSeverity.CRITICAL.name == "CRITICAL"
    
    def test_recovery_strategy_enum(self):
        """Test RecoveryStrategy enum values."""
        assert RecoveryStrategy.RETRY.name == "RETRY"
        assert RecoveryStrategy.FALLBACK.name == "FALLBACK"
        assert RecoveryStrategy.IGNORE.name == "IGNORE"
        assert RecoveryStrategy.ABORT.name == "ABORT"
        assert RecoveryStrategy.ESCALATE.name == "ESCALATE"
        assert RecoveryStrategy.CIRCUIT_BREAKER.name == "CIRCUIT_BREAKER"


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_error_with_none_context(self):
        """Test error creation with None context."""
        error = AISystemError("Test", context=None)
        assert error.context == {}
    
    def test_error_to_dict_with_complex_context(self):
        """Test serialization with complex context objects."""
        class CustomObject:
            def __str__(self):
                return "custom_object"
        
        context = {
            "string": "value",
            "number": 42,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "object": CustomObject()
        }
        
        error = AISystemError("Test", context=context)
        error_dict = error.to_dict()
        
        assert error_dict["context"]["string"] == "value"
        assert error_dict["context"]["number"] == 42
        assert error_dict["context"]["list"] == [1, 2, 3]
        assert error_dict["context"]["dict"] == {"nested": "value"}
        assert error_dict["context"]["object"] == "custom_object"
    
    def test_rate_limit_error_without_reset_time(self):
        """Test RateLimitError without reset_time."""
        error = RateLimitError(
            "Rate limited",
            service_name="API",
            limit=100,
            current_usage=101
        )
        
        assert error.reset_time is None
        error_dict = error.to_dict()
        assert error_dict["context"]["reset_time"] is None
    
    def test_error_inheritance_chain(self):
        """Test that all specific errors inherit from AISystemError."""
        errors = [
            ConfigurationError("test"),
            IntegrationError("test"),
            ResourceError("test"),
            ValidationError("test"),
            AuthenticationError("test"),
            RateLimitError("test"),
            NetworkError("test"),
            DataError("test"),
            PermissionError("test")
        ]
        
        for error in errors:
            assert isinstance(error, AISystemError)
            assert isinstance(error, Exception)