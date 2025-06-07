#!/usr/bin/env python3
"""
Lightweight search tool validation using local OLLAMA instead of expensive APIs.
Converted from comprehensive test suite to reduce API costs.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import test target
from tools.search_tool import search_web


class MockRunContext:
    """Mock RunContext for testing agent tools."""
    def __init__(self, deps):
        self.deps = deps


class TestSearchWebImplementation:
    """Test suite for the core search_web implementation function."""

    def test_search_web_missing_api_key(self):
        """Test search_web returns appropriate error when API key is missing."""
        with patch.dict(os.environ, {}, clear=True):
            result = search_web("test query")
            assert "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration." in result

    def test_search_web_empty_query(self):
        """Test search_web with empty query (implementation should handle gracefully)."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            # The implementation doesn't validate empty queries, but let's test it doesn't crash
            result = search_web("")
            # Should either succeed with empty query or fail gracefully
            assert "🔍" in result

    def test_search_web_max_results_parameter(self):
        """Test that max_results parameter is accepted but doesn't affect Perplexity behavior."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            with patch('tools.search_tool.OpenAI') as mock_openai:
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.choices = [Mock()]
                mock_response.choices[0].message.content = "Test response"
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                # Test with different max_results values
                result1 = search_web("test query", max_results=1)
                result2 = search_web("test query", max_results=5)
                
                # Both should work the same way (max_results ignored by Perplexity)
                assert "🔍 **test query**" in result1
                assert "🔍 **test query**" in result2
                assert "Test response" in result1
                assert "Test response" in result2

    @patch('tools.search_tool.OpenAI')
    def test_search_web_successful_response(self, mock_openai):
        """Test search_web with successful API response."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            # Mock successful API response
            mock_client = Mock()
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = "Python 3.12 introduces several new features including..."
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            result = search_web("Python 3.12 features")
            
            # Verify response format
            assert "🔍 **Python 3.12 features**" in result
            assert "Python 3.12 introduces several new features" in result
            
            # Verify API was called correctly
            mock_openai.assert_called_once_with(
                api_key="test_key", 
                base_url="https://api.perplexity.ai", 
                timeout=180
            )
            mock_client.chat.completions.create.assert_called_once()

    @patch('tools.search_tool.OpenAI')
    def test_search_web_api_error(self, mock_openai):
        """Test search_web handles API errors gracefully."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "invalid_key"}):
            # Mock API error
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("401: Invalid API key")
            mock_openai.return_value = mock_client

            result = search_web("test query")
            
            # Should return user-friendly error message
            assert "🔍 Search error" in result
            assert "401: Invalid API key" in result

    @patch('tools.search_tool.OpenAI')
    def test_search_web_timeout_error(self, mock_openai):
        """Test search_web handles timeout errors."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            # Mock timeout error
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("timeout occurred")
            mock_openai.return_value = mock_client

            result = search_web("test query")
            
            # Should return timeout-specific error message
            assert "🔍 Search error" in result
            assert "timeout occurred" in result

    @patch('tools.search_tool.OpenAI')
    def test_search_web_network_error(self, mock_openai):
        """Test search_web handles network errors."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            # Mock network error - tools/search_tool.py doesn't import requests
            # so we'll test with a generic Exception
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("Network error")
            mock_openai.return_value = mock_client

            result = search_web("test query")
            
            # Should return generic error message (tools implementation is simpler)
            assert "🔍 Search error" in result
            assert "Network error" in result

    @pytest.mark.asyncio
    async def test_search_web_async_wrapper(self):
        """Test that async wrapper calls synchronous function correctly."""
        with patch('tools.search_tool.search_web') as mock_search:
            mock_search.return_value = "🔍 **test**\n\nTest result"
            
            result = await search_web_async("test query", max_results=5)
            
            # Verify it calls the sync function with correct parameters
            mock_search.assert_called_once_with("test query", 5)
            assert result == "🔍 **test**\n\nTest result"


class TestSearchCurrentInfoAgentTool:
    """Test suite for the search_current_info agent tool."""

    def test_agent_tool_input_validation_empty_query(self):
        """Test agent tool validates empty queries."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test empty string
        result = search_current_info(mock_context, "")
        assert "🔍 Search error: Please provide a search query." in result
        
        # Test whitespace only
        result = search_current_info(mock_context, "   ")
        assert "🔍 Search error: Please provide a search query." in result

    def test_agent_tool_input_validation_long_query(self):
        """Test agent tool validates query length."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test query over 500 characters
        long_query = "a" * 501
        result = search_current_info(mock_context, long_query)
        assert "🔍 Search error: Query too long (maximum 500 characters)." in result

    def test_agent_tool_valid_query(self):
        """Test agent tool with valid query passes through to implementation."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test query**\n\nTest result"
            
            result = search_current_info(mock_context, "test query", max_results=5)
            
            # Verify it calls search_web with correct parameters
            mock_search.assert_called_once_with("test query", 5)
            assert result == "🔍 **test query**\n\nTest result"

    def test_agent_tool_default_max_results(self):
        """Test agent tool uses default max_results when not provided."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test query**\n\nTest result"
            
            result = search_current_info(mock_context, "test query")
            
            # Should use default value of 3
            mock_search.assert_called_once_with("test query", 3)

    def test_agent_tool_context_handling(self):
        """Test agent tool properly accepts RunContext but doesn't require specific context data."""
        # Test with minimal context
        minimal_context = MockRunContext(ValorContext())
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test**\n\nResult"
            
            result = search_current_info(minimal_context, "test query")
            assert "🔍 **test**" in result

        # Test with full context
        full_context = MockRunContext(ValorContext(
            chat_id=12345,
            username="testuser",
            is_group_chat=True,
            chat_history=[{"role": "user", "content": "previous message"}]
        ))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test**\n\nResult"
            
            result = search_current_info(full_context, "test query")
            assert "🔍 **test**" in result


class TestSearchToolIntegration:
    """Integration tests combining agent tool and implementation."""

    @patch('tools.search_tool.OpenAI')
    def test_end_to_end_successful_search(self, mock_openai):
        """Test complete flow from agent tool through implementation to API."""
        # Mock successful API response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Current information about the topic..."
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
            
            result = search_current_info(mock_context, "current events today")
            
            # Verify complete flow
            assert "🔍 **current events today**" in result
            assert "Current information about the topic" in result
            
            # Verify API configuration
            mock_openai.assert_called_once_with(
                api_key="test_key",
                base_url="https://api.perplexity.ai",
                timeout=180
            )

    def test_end_to_end_api_key_missing(self):
        """Test complete flow when API key is missing."""
        with patch.dict(os.environ, {}, clear=True):
            mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
            
            result = search_current_info(mock_context, "test query")
            
            # Should get error from implementation
            assert "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration." in result

    def test_end_to_end_input_validation_priority(self):
        """Test that agent tool validation runs before implementation."""
        # Empty query should be caught by agent tool, not passed to implementation
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
            
            result = search_current_info(mock_context, "")
            
            # search_web should not be called because validation catches empty query
            mock_search.assert_not_called()
            assert "Please provide a search query" in result


class TestPerformanceAndTimeout:
    """Test performance characteristics and timeout behavior."""

    @patch('tools.search_tool.OpenAI')
    def test_timeout_configuration(self, mock_openai):
        """Test that API client is configured with correct timeout."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            mock_client = Mock()
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = "Response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            search_web("test query")
            
            # Verify timeout is set to 180 seconds
            mock_openai.assert_called_once_with(
                api_key="test_key",
                base_url="https://api.perplexity.ai",
                timeout=180
            )

    @patch('tools.search_tool.OpenAI')
    def test_api_parameters(self, mock_openai):
        """Test that API is called with correct parameters."""
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            mock_client = Mock()
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = "Response"
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            search_web("test query")
            
            # Verify API call parameters
            call_args = mock_client.chat.completions.create.call_args
            assert call_args[1]['model'] == "sonar-pro"
            assert call_args[1]['temperature'] == 0.2
            assert call_args[1]['max_tokens'] == 400
            
            # Verify messages structure
            messages = call_args[1]['messages']
            assert len(messages) == 2
            assert messages[0]['role'] == 'system'
            assert messages[1]['role'] == 'user'
            assert messages[1]['content'] == 'test query'


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])