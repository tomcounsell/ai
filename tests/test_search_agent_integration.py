#!/usr/bin/env python3
"""
Agent integration tests for search_current_info tool.

Tests PydanticAI agent integration, tool selection, and conversation formatting
using the established mock RunContext pattern from existing tests.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

# Add project root to path  
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.valor.agent import valor_agent, search_current_info, ValorContext


class MockRunContext:
    """Mock RunContext for testing agent tools following established pattern."""
    def __init__(self, deps):
        self.deps = deps


class TestSearchAgentIntegration:
    """Test search_current_info integration with PydanticAI agent."""

    def test_agent_tool_registration(self):
        """Test that search_current_info tool is properly registered with agent."""
        # Check that the tool is registered
        tool_names = list(valor_agent._function_tools.keys())
        assert "search_current_info" in tool_names

    def test_agent_tool_signature(self):
        """Test that agent tool has correct function signature."""
        # Find the search tool
        search_tool = valor_agent._function_tools.get("search_current_info")
        assert search_tool is not None
        
        # Check function signature
        import inspect
        sig = inspect.signature(search_tool.function)
        params = list(sig.parameters.keys())
        
        # Should have ctx, query, and max_results parameters
        assert "ctx" in params
        assert "query" in params  
        assert "max_results" in params
        
        # Check default value for max_results
        assert sig.parameters["max_results"].default == 3

    @patch('agents.valor.agent.search_web')
    def test_agent_tool_conversation_formatting(self, mock_search):
        """Test that tool returns properly formatted responses for conversation."""
        mock_search.return_value = "🔍 **Python 3.12**\n\nPython 3.12 introduces..."
        
        mock_context = MockRunContext(ValorContext(
            chat_id=12345,
            username="testuser"
        ))
        
        result = search_current_info(mock_context, "Python 3.12 features")
        
        # Verify conversation-friendly formatting
        assert result.startswith("🔍 **")
        assert "Python 3.12" in result
        assert "\n\n" in result  # Should have proper line breaks
        
        # Verify implementation was called correctly
        mock_search.assert_called_once_with("Python 3.12 features", 3)

    def test_agent_tool_context_types(self):
        """Test that tool properly handles different ValorContext configurations."""
        # Test with minimal context
        minimal_context = MockRunContext(ValorContext())
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test**\n\nResult"
            
            result = search_current_info(minimal_context, "test query")
            assert "🔍 **test**" in result

        # Test with full context (group chat)
        group_context = MockRunContext(ValorContext(
            chat_id=67890,
            username="groupuser",
            is_group_chat=True,
            chat_history=[
                {"role": "user", "content": "What's new in AI?"},
                {"role": "assistant", "content": "Let me search for current AI news..."}
            ]
        ))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **AI news**\n\nLatest AI developments..."
            
            result = search_current_info(group_context, "AI news today")
            assert "🔍 **AI news**" in result

        # Test with priority question context
        priority_context = MockRunContext(ValorContext(
            chat_id=11111,
            username="priority_user",
            is_priority_question=True,
            notion_data="Project: PsyOPTIMAL, Tasks: 5 ready for dev"
        ))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **tech trends**\n\nCurrent technology trends..."
            
            result = search_current_info(priority_context, "tech trends 2025")
            assert "🔍 **tech trends**" in result

    def test_agent_tool_error_handling_in_conversation(self):
        """Test that tool errors are formatted appropriately for conversation."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test input validation errors (should be conversation-friendly)
        result = search_current_info(mock_context, "")
        assert result.startswith("🔍")
        assert "Please provide a search query" in result
        # The error message does end with a period, which is fine for error messages
        
        # Test API errors passed through from implementation
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration."
            
            result = search_current_info(mock_context, "test query")
            assert "🔍 Search unavailable" in result
            assert "PERPLEXITY_API_KEY" in result

    def test_agent_tool_parameter_handling(self):
        """Test that tool properly handles optional and required parameters."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **test**\n\nResult"
            
            # Test with only required parameter
            search_current_info(mock_context, "test query")
            mock_search.assert_called_with("test query", 3)  # Default max_results
            
            # Test with explicit max_results
            search_current_info(mock_context, "another query", max_results=5)
            mock_search.assert_called_with("another query", 5)
            
            # Test with max_results=1
            search_current_info(mock_context, "third query", max_results=1)
            mock_search.assert_called_with("third query", 1)

    @patch('agents.valor.agent.search_web')
    def test_agent_tool_response_consistency(self, mock_search):
        """Test that tool consistently returns string responses."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test successful response
        mock_search.return_value = "🔍 **success**\n\nSuccessful search result"
        result = search_current_info(mock_context, "test query")
        assert isinstance(result, str)
        assert len(result) > 0
        
        # Test error response
        mock_search.return_value = "🔍 Search error: Something went wrong"
        result = search_current_info(mock_context, "error query") 
        assert isinstance(result, str)
        assert "🔍" in result

    def test_agent_tool_unicode_handling(self):
        """Test that tool properly handles Unicode characters in queries."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **测试**\n\nUnicode result"
            
            # Test with Unicode query
            result = search_current_info(mock_context, "测试查询")
            mock_search.assert_called_with("测试查询", 3)
            assert "🔍" in result
            
            # Test with emoji in query  
            result = search_current_info(mock_context, "AI trends 🤖")
            mock_search.assert_called_with("AI trends 🤖", 3)

    def test_agent_tool_performance_characteristics(self):
        """Test tool performance characteristics for agent usage."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.search_web') as mock_search:
            mock_search.return_value = "🔍 **fast**\n\nQuick result"
            
            import time
            start_time = time.time()
            result = search_current_info(mock_context, "quick query")
            end_time = time.time()
            
            # Agent tool itself should be very fast (validation + delegation)
            execution_time = end_time - start_time
            assert execution_time < 0.1  # Should be sub-100ms for the wrapper
            
            # Verify call was made
            mock_search.assert_called_once()


class TestAgentToolDocumentationIntegration:
    """Test that tool documentation integrates properly with agent."""

    def test_agent_tool_docstring_content(self):
        """Test that tool docstring provides appropriate guidance for agent."""
        # Find the search tool
        search_tool = valor_agent._function_tools.get("search_current_info")
        assert search_tool is not None
        
        docstring = search_tool.function.__doc__
        assert docstring is not None
        
        # Check for key guidance elements
        assert "current information" in docstring.lower()
        assert "perplexity" in docstring.lower()
        assert "when you need" in docstring.lower() or "use this when" in docstring.lower()
        
        # Check for usage scenarios
        assert "current events" in docstring.lower()
        assert "technology trends" in docstring.lower()
        
        # Check for error scenario documentation
        assert "error scenarios" in docstring.lower()
        assert "search unavailable" in docstring.lower()
        
        # Check for example
        assert "example" in docstring.lower()
        assert ">>>" in docstring

    def test_agent_tool_parameter_documentation(self):
        """Test that parameters are properly documented for agent understanding."""
        # Find the search tool
        search_tool = valor_agent._function_tools.get("search_current_info")
        
        docstring = search_tool.function.__doc__
        
        # Check parameter documentation
        assert "Args:" in docstring
        assert "ctx:" in docstring
        assert "query:" in docstring  
        assert "max_results:" in docstring
        
        # Check return documentation
        assert "Returns:" in docstring
        assert "str:" in docstring


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])