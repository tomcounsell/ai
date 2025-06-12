"""
Tests for UnifiedErrorManager component.

Tests error categorization, retry strategies, and recovery mechanisms.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Import from our error_management module where we defined these
from integrations.telegram.error_management import BadRequest, NetworkError, TimedOut

from integrations.telegram.error_management import (
    UnifiedErrorManager,
    ErrorCategory,
    ErrorSeverity
)
from integrations.telegram.models import MessageContext


@pytest.fixture
def error_manager():
    """Create UnifiedErrorManager instance."""
    return UnifiedErrorManager()


@pytest.fixture
def basic_context():
    """Create basic message context."""
    message = MagicMock()
    message.message_id = 123
    message.text = "Test message"
    
    return MessageContext(
        message=message,
        chat_id=12345,
        username="testuser",
        workspace="test_workspace",
        working_directory="/test/dir",
        is_dev_group=False,
        is_mention=False,
        cleaned_text="Test message",
        timestamp=datetime.now()
    )


class TestUnifiedErrorManager:
    """Test suite for UnifiedErrorManager."""

    def test_network_error_categorization(self, error_manager):
        """Test categorization of network errors."""
        errors = [
            TimedOut("Connection timed out"),
            NetworkError("Network is unreachable"),
            ConnectionError("Connection refused")
        ]
        
        for error in errors:
            result = error_manager.handle_processing_error(error)
            assert result["category"] == ErrorCategory.NETWORK
            assert result["severity"] == ErrorSeverity.LOW
            assert result["should_retry"] is True

    def test_rate_limit_error_categorization(self, error_manager):
        """Test categorization of rate limit errors."""
        errors = [
            Exception("Rate limit exceeded"),
            Exception("Too many requests"),
            BadRequest("429: Too Many Requests")
        ]
        
        for error in errors:
            result = error_manager.handle_processing_error(error)
            assert result["category"] == ErrorCategory.RATE_LIMIT
            assert result["severity"] == ErrorSeverity.MEDIUM
            assert result["should_retry"] is True
            assert result["retry_after"] == 60  # Initial delay

    def test_permission_error_categorization(self, error_manager):
        """Test categorization of permission errors."""
        errors = [
            BadRequest("Bot doesn't have enough rights"),
            BadRequest("Permission denied"),
            BadRequest("Forbidden: bot was blocked by user"),
            BadRequest("Chat not found"),
            BadRequest("User not found")
        ]
        
        for error in errors:
            result = error_manager.handle_processing_error(error)
            assert result["category"] == ErrorCategory.PERMISSION
            assert result["severity"] == ErrorSeverity.HIGH
            assert result["should_retry"] is False

    def test_validation_error_categorization(self, error_manager):
        """Test categorization of validation errors."""
        errors = [
            ValueError("Invalid value"),
            TypeError("Wrong type"),
            KeyError("Missing key")
        ]
        
        for error in errors:
            result = error_manager.handle_processing_error(error)
            assert result["category"] == ErrorCategory.VALIDATION
            assert result["severity"] == ErrorSeverity.LOW

    def test_external_service_error_categorization(self, error_manager):
        """Test categorization of external service errors."""
        errors = [
            Exception("OpenAI API error"),
            Exception("Anthropic service unavailable"),
            Exception("Perplexity rate limit"),
            Exception("Notion API failed")
        ]
        
        for error in errors:
            result = error_manager.handle_processing_error(error)
            assert result["category"] == ErrorCategory.EXTERNAL_SERVICE
            assert result["severity"] == ErrorSeverity.MEDIUM
            assert result["should_retry"] is True

    def test_unknown_error_categorization(self, error_manager):
        """Test categorization of unknown errors."""
        error = Exception("Some random error")
        result = error_manager.handle_processing_error(error)
        
        assert result["category"] == ErrorCategory.UNKNOWN
        assert result["severity"] == ErrorSeverity.MEDIUM

    def test_critical_error_detection(self, error_manager):
        """Test detection of critical errors."""
        error = Exception("CRITICAL: System failure")
        result = error_manager.handle_processing_error(error)
        
        assert result["category"] == ErrorCategory.UNKNOWN
        assert result["severity"] == ErrorSeverity.CRITICAL

    def test_retry_backoff_calculation(self, error_manager):
        """Test retry backoff calculation."""
        error = NetworkError("Network error")
        
        # First attempt
        result1 = error_manager.handle_processing_error(error)
        assert result1["should_retry"] is True
        assert result1["retry_after"] == 1  # Initial delay
        
        # Second attempt
        result2 = error_manager.handle_processing_error(error)
        assert result2["should_retry"] is True
        assert result2["retry_after"] == 2  # Backoff factor applied
        
        # Third attempt
        result3 = error_manager.handle_processing_error(error)
        assert result3["should_retry"] is True
        assert result3["retry_after"] == 4  # Further backoff
        
        # Fourth attempt (exceeds max retries)
        result4 = error_manager.handle_processing_error(error)
        assert result4["should_retry"] is False

    def test_user_friendly_messages(self, error_manager):
        """Test generation of user-friendly error messages."""
        test_cases = [
            (NetworkError("Network error"), "🌐 Network issue detected"),
            (Exception("Rate limit exceeded"), "🚦 Rate limit reached"),
            (BadRequest("Permission denied"), "🔒 Permission denied"),
            (ValueError("Invalid input"), "❓ Invalid request"),
            (Exception("Processing failed"), "⚙️ Processing error"),
            (Exception("OpenAI API error"), "🔧 External service temporarily unavailable"),
            (Exception("Unknown error"), "❌ An unexpected error occurred")
        ]
        
        for error, expected_prefix in test_cases:
            result = error_manager.handle_processing_error(error)
            assert result["user_message"].startswith(expected_prefix)

    def test_specific_error_messages(self, error_manager):
        """Test specific error message overrides."""
        error = BadRequest("Message to reply to not found")
        result = error_manager.handle_processing_error(error)
        
        assert result["user_message"] == "⚠️ The message you're replying to was deleted."

    def test_low_severity_no_user_message(self, error_manager):
        """Test that low severity errors don't generate user messages."""
        error = NetworkError("Minor network hiccup")
        result = error_manager.handle_processing_error(error)
        
        assert result["severity"] == ErrorSeverity.LOW
        assert result["user_message"] == ""

    def test_error_tracking(self, error_manager):
        """Test error counting and tracking."""
        error = NetworkError("Network error")
        
        # Generate multiple errors
        for i in range(3):
            result = error_manager.handle_processing_error(error)
            assert result["metadata"]["error_count"] == i + 1

    def test_error_statistics(self, error_manager):
        """Test error statistics generation."""
        # Generate various errors
        errors = [
            NetworkError("Network error"),
            NetworkError("Network error"),
            BadRequest("Permission denied"),
            ValueError("Invalid value"),
            Exception("API error")
        ]
        
        for error in errors:
            error_manager.handle_processing_error(error)
        
        stats = error_manager.get_error_statistics()
        
        assert stats["total_errors"] == 5
        assert stats["by_category"]["network"] == 2
        assert stats["by_category"]["permission"] == 1
        assert stats["by_category"]["validation"] == 1
        assert stats["by_category"]["external_service"] == 1

    def test_recent_errors_tracking(self, error_manager):
        """Test tracking of recent errors."""
        # Generate an error
        error = NetworkError("Network error")
        error_manager.handle_processing_error(error)
        
        stats = error_manager.get_error_statistics()
        recent_errors = stats["recent_errors"]
        
        assert len(recent_errors) > 0
        assert recent_errors[0]["error"] == "network:NetworkError"
        assert recent_errors[0]["count"] == 1

    def test_error_log_message_generation(self, error_manager, basic_context):
        """Test detailed log message generation."""
        error = NetworkError("Connection failed")
        result = error_manager.handle_processing_error(error, basic_context)
        
        log_message = result["log_message"]
        assert "Error category: network" in log_message
        assert "Error type: NetworkError" in log_message
        assert "Error message: Connection failed" in log_message
        assert f"Chat ID: {basic_context.chat_id}" in log_message
        assert f"Username: {basic_context.username}" in log_message

    def test_context_metadata_inclusion(self, error_manager, basic_context):
        """Test inclusion of context in error metadata."""
        error = ValueError("Invalid value")
        result = error_manager.handle_processing_error(error, basic_context)
        
        metadata = result["metadata"]
        assert metadata["chat_id"] == basic_context.chat_id
        assert metadata["username"] == basic_context.username

    def test_fallback_response_generation(self, error_manager):
        """Test fallback response generation for delivery errors."""
        # Test with error that generates user message
        error1 = BadRequest("Permission denied")
        response1 = error_manager.create_fallback_response(error1)
        assert "Permission denied" in response1
        
        # Test with error that doesn't generate user message
        error2 = NetworkError("Minor issue")
        response2 = error_manager.create_fallback_response(error2)
        assert response2 == "❌ An error occurred. Please try again."

    def test_statistics_reset(self, error_manager):
        """Test resetting error statistics."""
        # Generate some errors
        error_manager.handle_processing_error(NetworkError("Test"))
        error_manager.handle_processing_error(ValueError("Test"))
        
        # Verify errors tracked
        stats_before = error_manager.get_error_statistics()
        assert stats_before["total_errors"] > 0
        
        # Reset statistics
        error_manager.reset_statistics()
        
        # Verify reset
        stats_after = error_manager.get_error_statistics()
        assert stats_after["total_errors"] == 0
        assert len(stats_after["recent_errors"]) == 0

    @patch('integrations.telegram.error_management.logger')
    def test_severity_based_logging(self, mock_logger, error_manager):
        """Test that errors are logged at appropriate levels."""
        # Critical error
        critical_error = Exception("CRITICAL: System failure")
        error_manager.handle_processing_error(critical_error)
        mock_logger.critical.assert_called_once()
        
        # High severity error
        high_error = BadRequest("Permission denied")
        error_manager.handle_processing_error(high_error)
        mock_logger.error.assert_called_once()
        
        # Medium severity error
        medium_error = Exception("Rate limit exceeded")
        error_manager.handle_processing_error(medium_error)
        mock_logger.warning.assert_called()
        
        # Low severity error
        low_error = NetworkError("Minor issue")
        error_manager.handle_processing_error(low_error)
        mock_logger.info.assert_called()