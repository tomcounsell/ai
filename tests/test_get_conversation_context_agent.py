"""
Test suite for get_conversation_context agent tool functionality.

This module provides comprehensive testing for the PydanticAI agent tool,
including parameter validation, error handling, and integration with the
chat history system through mocked RunContext.
"""

import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.valor.agent import get_conversation_context, ValorContext


class TestGetConversationContextAgentTool:
    """Test suite for get_conversation_context PydanticAI agent tool."""

    def test_successful_context_retrieval(self):
        """Test successful conversation context retrieval with mocked data."""
        # Mock RunContext with proper structure
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        # Mock the underlying implementation
        with patch('agents.valor.agent.get_telegram_context_summary') as mock_summary:
            mock_summary.return_value = "Conversation summary (last 24 hours, 3 messages):\n\n1. user: Hello\n2. assistant: Hi there\n3. user: How are you?"
            
            result = get_conversation_context(mock_ctx, hours_back=24)
            
            assert "Conversation summary" in result
            assert "Hello" in result
            assert "Hi there" in result
            
            # Verify the function was called with correct parameters
            mock_summary.assert_called_once_with(
                chat_history_obj=mock_ctx.deps.chat_history_obj,
                chat_id=mock_ctx.deps.chat_id,
                hours_back=24
            )

    def test_no_chat_history_available(self):
        """Test handling when chat history object is not available."""
        # Mock RunContext with missing chat history
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = None
        mock_ctx.deps.chat_id = 12345
        
        result = get_conversation_context(mock_ctx, hours_back=24)
        
        assert result == "No chat history available"

    def test_no_chat_id_available(self):
        """Test handling when chat ID is not available."""
        # Mock RunContext with missing chat ID
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = None
        
        result = get_conversation_context(mock_ctx, hours_back=24)
        
        assert result == "No chat history available"

    def test_parameter_validation_negative_hours(self):
        """Test parameter validation rejects negative hours_back."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        result = get_conversation_context(mock_ctx, hours_back=-5)
        
        assert "❌ hours_back must be between 1 and 168 hours" in result

    def test_parameter_validation_zero_hours(self):
        """Test parameter validation rejects zero hours_back."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        result = get_conversation_context(mock_ctx, hours_back=0)
        
        assert "❌ hours_back must be between 1 and 168 hours" in result

    def test_parameter_validation_excessive_hours(self):
        """Test parameter validation rejects excessively large hours_back."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        result = get_conversation_context(mock_ctx, hours_back=200)
        
        assert "❌ hours_back must be between 1 and 168 hours" in result

    def test_parameter_validation_boundary_values(self):
        """Test parameter validation accepts boundary values."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        with patch('agents.valor.agent.get_telegram_context_summary') as mock_summary:
            mock_summary.return_value = "Test result"
            
            # Test minimum boundary (1 hour)
            result = get_conversation_context(mock_ctx, hours_back=1)
            assert result == "Test result"
            
            # Test maximum boundary (168 hours = 1 week)
            result = get_conversation_context(mock_ctx, hours_back=168)
            assert result == "Test result"

    def test_default_parameter_value(self):
        """Test that default parameter value works correctly."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        with patch('agents.valor.agent.get_telegram_context_summary') as mock_summary:
            mock_summary.return_value = "Default test result"
            
            # Call without specifying hours_back (should use default 24)
            result = get_conversation_context(mock_ctx)
            
            assert result == "Default test result"
            mock_summary.assert_called_once_with(
                chat_history_obj=mock_ctx.deps.chat_history_obj,
                chat_id=mock_ctx.deps.chat_id,
                hours_back=24  # Default value
            )

    def test_agent_tool_integration_patterns(self):
        """Test that the agent tool follows expected PydanticAI patterns."""
        # Verify the function is properly decorated (this would be in integration tests)
        # but we can verify the function signature and behavior patterns
        
        import inspect
        sig = inspect.signature(get_conversation_context)
        
        # Verify function signature matches expected pattern
        params = list(sig.parameters.keys())
        assert params[0] == 'ctx', "First parameter should be 'ctx'"
        assert params[1] == 'hours_back', "Second parameter should be 'hours_back'"
        
        # Verify default value
        assert sig.parameters['hours_back'].default == 24
        
        # Verify return type annotation (if present)
        assert sig.return_annotation == str

    def test_error_handling_with_implementation_exception(self):
        """Test error handling when the underlying implementation raises an exception."""
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        with patch('agents.valor.agent.get_telegram_context_summary') as mock_summary:
            mock_summary.side_effect = Exception("Database connection error")
            
            # The agent tool should let the exception propagate (or handle it)
            # depending on the system's error handling strategy
            with pytest.raises(Exception):
                get_conversation_context(mock_ctx, hours_back=24)

    def test_mock_context_structure(self):
        """Test that our mock context structure matches expected RunContext pattern."""
        # This test validates our test setup matches the real RunContext structure
        mock_ctx = Mock()
        mock_ctx.deps.chat_history_obj = Mock()
        mock_ctx.deps.chat_id = 12345
        
        # Verify we can access the expected attributes
        assert hasattr(mock_ctx.deps, 'chat_history_obj')
        assert hasattr(mock_ctx.deps, 'chat_id')
        assert mock_ctx.deps.chat_id == 12345
        assert mock_ctx.deps.chat_history_obj is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])