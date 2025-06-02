#!/usr/bin/env python3
"""
Comprehensive test suite for create_image tool and all implementations.

Tests all three layers:
- Agent tool (agents/valor/agent.py)
- Core implementation (tools/image_generation_tool.py) 
- MCP server (mcp_servers/social_tools.py)

Covers happy path, error conditions, input validation, and Telegram format handling.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import the components to test
from tools.image_generation_tool import generate_image, generate_image_async, create_image_with_feedback
from agents.valor.agent import create_image, ValorContext
from mcp_servers.social_tools import create_image as mcp_create_image


class MockRunContext:
    """Mock RunContext for testing agent tools."""
    def __init__(self, deps):
        self.deps = deps


class TestImageGenerationImplementation:
    """Test suite for the core generate_image implementation function."""

    def test_generate_image_missing_api_key(self):
        """Test generate_image returns appropriate error when API key is missing."""
        with patch.dict(os.environ, {}, clear=True):
            result = generate_image("test prompt")
            assert "🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration." in result

    def test_generate_image_parameters(self):
        """Test that generate_image accepts all parameters correctly."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch('tools.image_generation_tool.OpenAI') as mock_openai:
                # Mock successful API response
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                # Mock requests.get for image download
                with patch('tools.image_generation_tool.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_image_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    result = generate_image(
                        prompt="test prompt",
                        size="1792x1024",
                        quality="hd", 
                        style="vivid",
                        save_directory="/tmp"
                    )
                    
                    # Verify API was called with correct parameters
                    mock_client.images.generate.assert_called_once_with(
                        prompt="test prompt",
                        model="dall-e-3",
                        size="1792x1024",
                        quality="hd",
                        style="vivid",
                        n=1
                    )
                    
                    # Verify result is a file path
                    assert result.endswith(".png")
                    assert "generated_test_prompt" in result

    @patch('tools.image_generation_tool.OpenAI')
    def test_generate_image_successful_response(self, mock_openai):
        """Test generate_image with successful API response."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Mock successful API response
            mock_client = Mock()
            mock_response = Mock()
            mock_response.data = [Mock()]
            mock_response.data[0].url = "https://dalle.example.com/image123.png"
            mock_client.images.generate.return_value = mock_response
            mock_openai.return_value = mock_client

            # Mock image download
            with patch('tools.image_generation_tool.requests.get') as mock_get:
                mock_get.return_value.content = b"fake_png_data"
                mock_get.return_value.raise_for_status.return_value = None

                result = generate_image("a beautiful sunset")
                
                # Verify response format
                assert result.endswith(".png")
                assert "generated_a_beautiful_sunset" in result
                
                # Verify API configuration
                mock_openai.assert_called_once_with(api_key="test_key", timeout=180)

    @patch('tools.image_generation_tool.OpenAI')
    def test_generate_image_api_error(self, mock_openai):
        """Test generate_image handles API errors gracefully."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "invalid_key"}):
            # Mock API error
            mock_client = Mock()
            mock_client.images.generate.side_effect = Exception("Invalid API key")
            mock_openai.return_value = mock_client

            result = generate_image("test prompt")
            
            # Should return user-friendly error message
            assert "🎨 Image generation error" in result
            assert "Invalid API key" in result

    @patch('tools.image_generation_tool.OpenAI')
    def test_generate_image_download_error(self, mock_openai):
        """Test generate_image handles image download errors."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Mock successful API but failed download
            mock_client = Mock()
            mock_response = Mock()
            mock_response.data = [Mock()]
            mock_response.data[0].url = "https://example.com/image.png"
            mock_client.images.generate.return_value = mock_response
            mock_openai.return_value = mock_client

            # Mock failed download
            with patch('tools.image_generation_tool.requests.get') as mock_get:
                mock_get.side_effect = Exception("Network error")

                result = generate_image("test prompt")
                
                assert "🎨 Image generation error" in result
                assert "Network error" in result

    def test_filename_sanitization(self):
        """Test that filenames are properly sanitized."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch('tools.image_generation_tool.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('tools.image_generation_tool.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    # Test with special characters
                    result = generate_image("a cat with @#$%^&*() special chars!")
                    
                    # Should create safe filename
                    assert "generated_a_cat_with_special_chars" in result
                    assert "@" not in result
                    assert "#" not in result

    @pytest.mark.asyncio
    async def test_generate_image_async_wrapper(self):
        """Test that async wrapper calls synchronous function correctly."""
        with patch('tools.image_generation_tool.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_test.png"
            
            result = await generate_image_async("test prompt", size="1024x1024")
            
            # Verify it calls the sync function with correct parameters
            mock_generate.assert_called_once_with("test prompt", "1024x1024", "standard", "natural", None)
            assert result == "/tmp/generated_test.png"

    def test_create_image_with_feedback_success(self):
        """Test create_image_with_feedback with successful generation."""
        with patch('tools.image_generation_tool.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_success.png"
            
            path, message = create_image_with_feedback("test prompt")
            
            assert path == "/tmp/generated_success.png"
            assert "🎨 **Generated Image**" in message
            assert "test prompt" in message

    def test_create_image_with_feedback_error(self):
        """Test create_image_with_feedback with generation error."""
        with patch('tools.image_generation_tool.generate_image') as mock_generate:
            mock_generate.return_value = "🎨 Image generation error: Something went wrong"
            
            path, message = create_image_with_feedback("test prompt")
            
            assert path == ""
            assert "🎨 Image generation error: Something went wrong" in message


class TestCreateImageAgentTool:
    """Test suite for the create_image agent tool."""

    def test_agent_tool_input_validation_empty_prompt(self):
        """Test agent tool validates empty prompts."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test empty string
        result = create_image(mock_context, "")
        assert "🎨 Image generation error: Please provide a description for the image." in result
        
        # Test whitespace only
        result = create_image(mock_context, "   ")
        assert "🎨 Image generation error: Please provide a description for the image." in result

    def test_agent_tool_input_validation_long_prompt(self):
        """Test agent tool validates prompt length."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        # Test prompt over 1000 characters
        long_prompt = "a" * 1001
        result = create_image(mock_context, long_prompt)
        assert "🎨 Image generation error: Description too long (maximum 1000 characters)." in result

    def test_agent_tool_input_validation_invalid_style(self):
        """Test agent tool validates style parameter."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        result = create_image(mock_context, "test prompt", style="invalid")
        assert "🎨 Image generation error: Style must be 'natural' or 'vivid'. Got 'invalid'." in result

    def test_agent_tool_input_validation_invalid_quality(self):
        """Test agent tool validates quality parameter."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        result = create_image(mock_context, "test prompt", quality="ultra")
        assert "🎨 Image generation error: Quality must be 'standard' or 'hd'. Got 'ultra'." in result

    def test_agent_tool_input_validation_invalid_size(self):
        """Test agent tool validates size parameter."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        result = create_image(mock_context, "test prompt", size="2048x2048")
        assert "🎨 Image generation error: Size must be '1024x1024', '1792x1024', or '1024x1792'. Got '2048x2048'." in result

    def test_agent_tool_valid_prompt_success(self):
        """Test agent tool with valid prompt returns Telegram format."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_test_image.png"
            
            result = create_image(mock_context, "a beautiful landscape", style="vivid", quality="hd", size="1792x1024")
            
            # Verify it calls generate_image with correct parameters
            mock_generate.assert_called_once_with(
                prompt="a beautiful landscape",
                style="vivid",
                quality="hd",
                size="1792x1024",
                save_directory="/tmp"
            )
            
            # Verify Telegram format response
            assert result.startswith("TELEGRAM_IMAGE_GENERATED|")
            parts = result.split("|", 2)
            assert len(parts) == 3
            assert parts[1] == "/tmp/generated_test_image.png"
            assert "🎨 **Image Generated!**" in parts[2]
            assert "a beautiful landscape" in parts[2]

    def test_agent_tool_error_passthrough(self):
        """Test agent tool passes through implementation errors."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.generate_image') as mock_generate:
            mock_generate.return_value = "🎨 Image generation error: API quota exceeded"
            
            result = create_image(mock_context, "test prompt")
            
            # Should pass through the error without Telegram formatting
            assert result == "🎨 Image generation error: API quota exceeded"

    def test_agent_tool_default_parameters(self):
        """Test agent tool uses correct default parameters."""
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch('agents.valor.agent.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_default.png"
            
            result = create_image(mock_context, "test prompt")
            
            # Should use default values
            mock_generate.assert_called_once_with(
                prompt="test prompt",
                style="natural",
                quality="standard", 
                size="1024x1024",
                save_directory="/tmp"
            )

    def test_agent_tool_context_handling(self):
        """Test agent tool properly accepts RunContext but doesn't require specific context data."""
        # Test with minimal context
        minimal_context = MockRunContext(ValorContext())
        
        with patch('agents.valor.agent.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_minimal.png"
            
            result = create_image(minimal_context, "test prompt")
            assert "TELEGRAM_IMAGE_GENERATED|" in result

        # Test with full context
        full_context = MockRunContext(ValorContext(
            chat_id=12345,
            username="testuser",
            is_group_chat=True,
            chat_history=[{"role": "user", "content": "previous message"}]
        ))
        
        with patch('agents.valor.agent.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/generated_full.png"
            
            result = create_image(full_context, "test prompt")
            assert "TELEGRAM_IMAGE_GENERATED|" in result


class TestMCPServerImplementation:
    """Test suite for the MCP server create_image implementation."""

    def test_mcp_missing_api_key(self):
        """Test MCP server handles missing API key."""
        with patch.dict(os.environ, {}, clear=True):
            result = mcp_create_image("test prompt")
            assert "🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration." in result

    def test_mcp_input_validation(self):
        """Test MCP server comprehensive input validation."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Test empty prompt
            result = mcp_create_image("")
            assert "🎨 Image generation error: Prompt cannot be empty." in result
            
            # Test invalid size
            result = mcp_create_image("test", size="invalid")
            assert "🎨 Image generation error: Size must be one of" in result
            assert "invalid" in result
            
            # Test invalid quality
            result = mcp_create_image("test", quality="ultra")
            assert "🎨 Image generation error: Quality must be one of" in result
            assert "ultra" in result
            
            # Test invalid style
            result = mcp_create_image("test", style="abstract")
            assert "🎨 Image generation error: Style must be one of" in result
            assert "abstract" in result

    def test_mcp_telegram_format_with_chat_id(self):
        """Test MCP server returns Telegram format when chat_id provided."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('mcp_servers.social_tools.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    result = mcp_create_image("test prompt", chat_id="12345")
                    
                    # Should return Telegram format
                    assert result.startswith("TELEGRAM_IMAGE_GENERATED|")
                    assert "|12345" in result

    def test_mcp_regular_format_without_chat_id(self):
        """Test MCP server returns regular path when no chat_id."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('mcp_servers.social_tools.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    result = mcp_create_image("test prompt")
                    
                    # Should return just the file path
                    assert result.endswith(".png")
                    assert not result.startswith("TELEGRAM_IMAGE_GENERATED|")

    def test_mcp_error_categorization(self):
        """Test MCP server categorizes different error types."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                # Test OpenAI API error
                mock_client = Mock()
                mock_client.images.generate.side_effect = Exception("OpenAI API rate limit exceeded")
                mock_openai.return_value = mock_client

                result = mcp_create_image("test prompt")
                assert "🎨 OpenAI API error" in result
                assert "rate limit exceeded" in result

            # Test network error
            with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('mcp_servers.social_tools.requests.get') as mock_get:
                    from mcp_servers.social_tools import requests
                    mock_get.side_effect = requests.exceptions.RequestException("Connection failed")

                    result = mcp_create_image("test prompt")
                    assert "🎨 Image download error" in result
                    assert "Connection failed" in result


class TestImageToolIntegration:
    """Integration tests combining all implementations."""

    def test_interface_consistency_across_implementations(self):
        """Test that all implementations handle the same parameters consistently."""
        test_params = {
            "prompt": "a red bicycle",
            "style": "natural",
            "quality": "standard",
            "size": "1024x1024"
        }
        
        mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            # Mock the underlying generate_image for agent tool
            with patch('agents.valor.agent.generate_image') as mock_agent_gen:
                mock_agent_gen.return_value = "/tmp/agent_test.png"
                
                agent_result = create_image(
                    mock_context,
                    test_params["prompt"],
                    test_params["style"],
                    test_params["quality"],
                    test_params["size"]
                )
                
                # Verify agent tool called with correct parameters
                mock_agent_gen.assert_called_once_with(
                    prompt=test_params["prompt"],
                    style=test_params["style"],
                    quality=test_params["quality"],
                    size=test_params["size"],
                    save_directory="/tmp"
                )
                
                assert "TELEGRAM_IMAGE_GENERATED|" in agent_result

            # Test implementation tool directly
            with patch('tools.image_generation_tool.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('tools.image_generation_tool.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    impl_result = generate_image(**test_params)
                    
                    # Verify API called with correct parameters
                    mock_client.images.generate.assert_called_once_with(
                        prompt=test_params["prompt"],
                        model="dall-e-3",
                        size=test_params["size"],
                        quality=test_params["quality"],
                        style=test_params["style"],
                        n=1
                    )
                    
                    assert impl_result.endswith(".png")

            # Test MCP server
            with patch('mcp_servers.social_tools.OpenAI') as mock_openai:
                mock_client = Mock()
                mock_response = Mock()
                mock_response.data = [Mock()]
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                mock_openai.return_value = mock_client

                with patch('mcp_servers.social_tools.requests.get') as mock_get:
                    mock_get.return_value.content = b"fake_data"
                    mock_get.return_value.raise_for_status.return_value = None

                    mcp_result = mcp_create_image(**test_params)
                    
                    assert mcp_result.endswith(".png")

    def test_error_handling_consistency(self):
        """Test that all implementations handle errors consistently."""
        # Test missing API key across all implementations
        with patch.dict(os.environ, {}, clear=True):
            mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
            
            # Agent tool
            agent_result = create_image(mock_context, "test prompt")
            assert "🎨 Image generation unavailable" in agent_result
            assert "OPENAI_API_KEY" in agent_result
            
            # Implementation tool
            impl_result = generate_image("test prompt")
            assert "🎨 Image generation unavailable" in impl_result
            assert "OPENAI_API_KEY" in impl_result
            
            # MCP server
            mcp_result = mcp_create_image("test prompt")
            assert "🎨 Image generation unavailable" in mcp_result
            assert "OPENAI_API_KEY" in mcp_result


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])