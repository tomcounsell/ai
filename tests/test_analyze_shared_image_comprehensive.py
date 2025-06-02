#!/usr/bin/env python3
"""
Comprehensive test suite for analyze_shared_image tool and all implementations.

Tests all three layers:
- Agent tool (agents/valor/agent.py)
- Core implementation (tools/image_analysis_tool.py)
- MCP server (mcp_servers/social_tools.py)

Covers happy path, error conditions, vision capabilities, and format validation.
"""

import os
import sys
import tempfile
import base64
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import the components to test
from tools.image_analysis_tool import analyze_image, analyze_image_async
from agents.valor.agent import analyze_shared_image, ValorContext
from mcp_servers.social_tools import analyze_shared_image as mcp_analyze_shared_image


class MockRunContext:
    """Mock RunContext for testing agent tools."""
    def __init__(self, deps):
        self.deps = deps


def create_test_image(format_type="png"):
    """Create a minimal test image file in specified format."""
    # Minimal 1x1 pixel PNG image
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
    )
    
    with tempfile.NamedTemporaryFile(suffix=f'.{format_type}', delete=False) as f:
        f.write(png_data)
        return f.name


class TestImageAnalysisImplementation:
    """Test suite for the core analyze_image implementation function."""

    def test_analyze_image_missing_api_key(self):
        """Test analyze_image returns appropriate error when API key is missing."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {}, clear=True):
                result = analyze_image(test_image, "What's in this image?")
                assert "👁️ Image analysis unavailable: Missing OPENAI_API_KEY configuration." in result
        finally:
            Path(test_image).unlink()

    def test_analyze_image_empty_path(self):
        """Test analyze_image validates empty image path."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            result = analyze_image("", "What's in this image?")
            assert "👁️ Image analysis error: Image path cannot be empty." in result
            
            result = analyze_image("   ", "What's in this image?")
            assert "👁️ Image analysis error: Image path cannot be empty." in result

    def test_analyze_image_format_validation(self):
        """Test analyze_image validates supported image formats."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Test unsupported format
            result = analyze_image("/path/to/image.bmp", "Analyze this")
            assert "👁️ Image analysis error: Unsupported format '.bmp'" in result
            assert "Supported: .jpg, .jpeg, .png, .gif, .webp" in result
            
            # Test supported formats (without actual files, just validation)
            for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                test_image = create_test_image(ext.lstrip('.'))
                try:
                    # This will fail at file reading, but should pass format validation
                    result = analyze_image(test_image, "Test")
                    # Should not get format error
                    assert "Unsupported format" not in result
                finally:
                    Path(test_image).unlink()

    def test_analyze_image_file_not_found(self):
        """Test analyze_image handles missing files correctly."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            result = analyze_image("/nonexistent/image.jpg", "Analyze this")
            assert "👁️ Image analysis error: Image file not found." in result

    @patch('tools.image_analysis_tool.OpenAI')
    def test_analyze_image_successful_response(self, mock_openai):
        """Test analyze_image with successful API response."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.choices = [Mock()]
                mock_response.choices[0].message.content = "I can see a small test image with a single pixel."
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                result = analyze_image(test_image, "What do you see?")
                
                # Verify response format
                assert "👁️ **Image Analysis**" in result
                assert "I can see a small test image" in result
                
                # Verify API was called correctly
                mock_client.chat.completions.create.assert_called_once()
                call_args = mock_client.chat.completions.create.call_args
                assert call_args[1]['model'] == "gpt-4o"
                assert call_args[1]['temperature'] == 0.3
                assert call_args[1]['max_tokens'] == 500
        finally:
            Path(test_image).unlink()

    @patch('tools.image_analysis_tool.OpenAI')
    def test_analyze_image_no_question(self, mock_openai):
        """Test analyze_image without specific question."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.choices = [Mock()]
                mock_response.choices[0].message.content = "This is a minimal test image."
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                result = analyze_image(test_image)
                
                # Should use general description format
                assert "👁️ **What I see:**" in result
                assert "This is a minimal test image." in result
        finally:
            Path(test_image).unlink()

    @patch('tools.image_analysis_tool.OpenAI')
    def test_analyze_image_with_context(self, mock_openai):
        """Test analyze_image with context parameter."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.choices = [Mock()]
                mock_response.choices[0].message.content = "Considering the context, this appears to be..."
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                result = analyze_image(test_image, "What is this?", "We were discussing test images")
                
                # Verify context was included in the request
                call_args = mock_client.chat.completions.create.call_args
                messages = call_args[1]['messages']
                user_message = messages[1]['content'][0]['text']
                assert "We were discussing test images" in user_message
        finally:
            Path(test_image).unlink()

    @patch('tools.image_analysis_tool.OpenAI')
    def test_analyze_image_api_error(self, mock_openai):
        """Test analyze_image handles API errors gracefully."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "invalid_key"}):
                # Mock API error
                mock_client = Mock()
                mock_client.chat.completions.create.side_effect = Exception("OpenAI API rate limit exceeded")
                mock_openai.return_value = mock_client

                result = analyze_image(test_image, "Test")
                
                # Should return API-specific error message
                assert "👁️ OpenAI API error" in result
                assert "rate limit exceeded" in result
        finally:
            Path(test_image).unlink()

    def test_analyze_image_file_corruption(self):
        """Test analyze_image handles corrupted files."""
        # Create corrupted image file
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(b"This is not an image file")
            corrupted_path = tmp.name
        
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                with patch('tools.image_analysis_tool.OpenAI') as mock_openai:
                    # Mock client (won't be reached due to encoding error)
                    mock_client = Mock()
                    mock_openai.return_value = mock_client
                    
                    result = analyze_image(corrupted_path, "Test")
                    
                    # Should handle encoding error
                    assert "👁️ Image" in result and "error" in result
        finally:
            Path(corrupted_path).unlink()

    @pytest.mark.asyncio
    async def test_analyze_image_async_wrapper(self):
        """Test that async wrapper calls synchronous function correctly."""
        with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **What I see:**\n\nTest result"
            
            result = await analyze_image_async("/path/to/test.jpg", "What is this?", "context")
            
            # Verify it calls the sync function with correct parameters
            mock_analyze.assert_called_once_with("/path/to/test.jpg", "What is this?", "context")
            assert result == "👁️ **What I see:**\n\nTest result"


class TestAnalyzeSharedImageAgentTool:
    """Test suite for the analyze_shared_image agent tool."""

    def test_agent_tool_basic_functionality(self):
        """Test agent tool basic call to implementation."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **Image Analysis**\n\nI can see a test image."
            
            result = analyze_shared_image(mock_context, "/path/to/test.jpg", "What's in this image?")
            
            # Verify it calls analyze_image with correct parameters
            mock_analyze.assert_called_once_with(
                image_path="/path/to/test.jpg",
                question="What's in this image?",
                context=None  # No chat history in this test
            )
            assert result == "👁️ **Image Analysis**\n\nI can see a test image."

    def test_agent_tool_empty_question_handling(self):
        """Test agent tool handles empty question parameter."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **What I see:**\n\nGeneral description."
            
            # Test with empty string
            result = analyze_shared_image(mock_context, "/path/to/test.jpg", "")
            mock_analyze.assert_called_with(
                image_path="/path/to/test.jpg",
                question=None,  # Should convert empty string to None
                context=None
            )
            
            # Test with no question parameter (default)
            result = analyze_shared_image(mock_context, "/path/to/test.jpg")
            mock_analyze.assert_called_with(
                image_path="/path/to/test.jpg",
                question=None,
                context=None
            )

    def test_agent_tool_context_extraction(self):
        """Test agent tool extracts chat context correctly."""
        # Create context with chat history
        mock_context = MockRunContext(ValorContext(
            chat_id=12345,
            username="test",
            chat_history=[
                {"role": "user", "content": "I'm sharing a photo"},
                {"role": "assistant", "content": "I'd be happy to help analyze it"},
                {"role": "user", "content": "Here's the image"}
            ]
        ))
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **Image Analysis**\n\nContext-aware analysis."
            
            result = analyze_shared_image(mock_context, "/path/to/test.jpg", "What do you see?")
            
            # Verify context was extracted from recent messages
            call_args = mock_analyze.call_args
            assert call_args[1]['context'] is not None
            assert "I'm sharing a photo" in call_args[1]['context']
            assert "Here's the image" in call_args[1]['context']

    def test_agent_tool_no_chat_history(self):
        """Test agent tool handles missing chat history gracefully."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **Image Analysis**\n\nAnalysis without context."
            
            result = analyze_shared_image(mock_context, "/path/to/test.jpg", "Analyze this")
            
            # Should pass None for context when no chat history
            mock_analyze.assert_called_once_with(
                image_path="/path/to/test.jpg",
                question="Analyze this",
                context=None
            )

    def test_agent_tool_context_types(self):
        """Test agent tool handles different context configurations."""
        # Test with minimal context
        minimal_context = MockRunContext(ValorContext())
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **Image Analysis**\n\nResult"
            
            result = analyze_shared_image(minimal_context, "/test.jpg")
            assert "👁️" in result

        # Test with group chat context
        group_context = MockRunContext(ValorContext(
            chat_id=67890,
            username="groupuser",
            is_group_chat=True,
            chat_history=[{"role": "user", "content": "group message"}]
        ))
        
        with patch('agents.valor.agent.analyze_image') as mock_analyze:
            mock_analyze.return_value = "👁️ **Image Analysis**\n\nGroup result"
            
            result = analyze_shared_image(group_context, "/test.jpg", "What's this?")
            assert "👁️" in result


class TestMCPServerImplementation:
    """Test suite for the MCP server analyze_shared_image implementation."""

    def test_mcp_missing_api_key(self):
        """Test MCP server handles missing API key."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {}, clear=True):
                result = mcp_analyze_shared_image(test_image)
                assert "👁️ Image analysis unavailable: Missing OPENAI_API_KEY configuration." in result
        finally:
            Path(test_image).unlink()

    def test_mcp_input_validation(self):
        """Test MCP server comprehensive input validation."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Test empty image path
            result = mcp_analyze_shared_image("")
            assert "👁️ Image analysis error: Image path cannot be empty." in result
            
            # Test non-existent file
            result = mcp_analyze_shared_image("/nonexistent/image.jpg")
            assert "👁️ Image analysis error: Image file not found." in result
            
            # Test unsupported format
            result = mcp_analyze_shared_image("/path/to/image.bmp")
            assert "👁️ Image analysis error: Unsupported format '.bmp'" in result

    @patch('mcp_servers.social_tools.OpenAI')
    def test_mcp_successful_analysis(self, mock_openai):
        """Test MCP server with successful analysis."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.choices = [Mock()]
                mock_response.choices[0].message.content = "I can see a test image with minimal content."
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                result = mcp_analyze_shared_image(test_image, "What do you see?")
                
                # Verify response format
                assert "👁️ **Image Analysis**" in result
                assert "I can see a test image" in result
                
                # Verify API call
                mock_client.chat.completions.create.assert_called_once()
        finally:
            Path(test_image).unlink()

    def test_mcp_question_parameter_handling(self):
        """Test MCP server handles question parameter correctly."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                    mock_client = Mock()
                    mock_response = Mock()
                    mock_response.choices = [Mock()]
                    mock_response.choices[0].message.content = "Test response"
                    mock_client.chat.completions.create.return_value = mock_response
                    mock_openai.return_value = mock_client

                    # Test with question
                    result1 = mcp_analyze_shared_image(test_image, "What is this?")
                    assert "👁️ **Image Analysis**" in result1
                    
                    # Test without question
                    result2 = mcp_analyze_shared_image(test_image)
                    assert "👁️ **What I see:**" in result2
        finally:
            Path(test_image).unlink()

    def test_mcp_error_categorization(self):
        """Test MCP server categorizes different error types."""
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                    # Test OpenAI API error
                    mock_client = Mock()
                    mock_client.chat.completions.create.side_effect = Exception("OpenAI API error occurred")
                    mock_openai.return_value = mock_client

                    result = mcp_analyze_shared_image(test_image, "Test")
                    assert "👁️ OpenAI API error" in result
                    assert "OpenAI API error occurred" in result
        finally:
            Path(test_image).unlink()


class TestImageAnalysisIntegration:
    """Integration tests combining all implementations."""

    def test_interface_consistency_across_implementations(self):
        """Test that all implementations handle the same parameters consistently."""
        test_image = create_test_image()
        try:
            mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
            
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
                # Mock the underlying analyze_image for agent tool
                with patch('agents.valor.agent.analyze_image') as mock_agent_analyze:
                    mock_agent_analyze.return_value = "👁️ **Image Analysis**\n\nAgent result"
                    
                    agent_result = analyze_shared_image(mock_context, test_image, "What do you see?")
                    
                    # Verify agent tool called with correct parameters
                    mock_agent_analyze.assert_called_once_with(
                        image_path=test_image,
                        question="What do you see?",
                        context=None
                    )
                    assert "👁️" in agent_result

                # Test implementation tool directly
                with patch('tools.image_analysis_tool.OpenAI') as mock_openai:
                    mock_client = Mock()
                    mock_response = Mock()
                    mock_response.choices = [Mock()]
                    mock_response.choices[0].message.content = "Implementation response"
                    mock_client.chat.completions.create.return_value = mock_response
                    mock_openai.return_value = mock_client

                    impl_result = analyze_image(test_image, "What do you see?")
                    
                    # Verify API called correctly
                    mock_client.chat.completions.create.assert_called_once()
                    assert "👁️ **Image Analysis**" in impl_result

                # Test MCP server
                with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                    mock_client = Mock()
                    mock_response = Mock()
                    mock_response.choices = [Mock()]
                    mock_response.choices[0].message.content = "MCP response"
                    mock_client.chat.completions.create.return_value = mock_response
                    mock_openai.return_value = mock_client

                    mcp_result = mcp_analyze_shared_image(test_image, "What do you see?")
                    
                    assert "👁️ **Image Analysis**" in mcp_result
        finally:
            Path(test_image).unlink()

    def test_error_handling_consistency(self):
        """Test that all implementations handle errors consistently."""
        # Test missing API key across all implementations
        test_image = create_test_image()
        try:
            with patch.dict(os.environ, {}, clear=True):
                mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
                
                # Agent tool (passes through implementation error)
                agent_result = analyze_shared_image(mock_context, test_image, "Test")
                assert "👁️ Image analysis unavailable" in agent_result
                assert "OPENAI_API_KEY" in agent_result
                
                # Implementation tool
                impl_result = analyze_image(test_image, "Test")
                assert "👁️ Image analysis unavailable" in impl_result
                assert "OPENAI_API_KEY" in impl_result
                
                # MCP server
                mcp_result = mcp_analyze_shared_image(test_image, "Test")
                assert "👁️ Image analysis unavailable" in mcp_result
                assert "OPENAI_API_KEY" in mcp_result
        finally:
            Path(test_image).unlink()


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])