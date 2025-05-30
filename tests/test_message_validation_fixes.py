"""
Test suite for MESSAGE_EMPTY error fixes in Telegram message handling.

Tests the comprehensive solution for preventing [400 MESSAGE_EMPTY] errors
by validating message content before sending to Telegram API.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from integrations.telegram.handlers import MessageHandler


class TestMessageValidation:
    """Test message content validation to prevent MESSAGE_EMPTY errors."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )

    def test_validate_message_content_empty_string(self):
        """Test validation handles empty strings."""
        result = self.handler._validate_message_content("", "Fallback")
        assert result == "Fallback"

    def test_validate_message_content_whitespace_only(self):
        """Test validation handles whitespace-only content."""
        result = self.handler._validate_message_content("   \n\t  ", "Fallback")
        assert result == "Fallback"

    def test_validate_message_content_none_input(self):
        """Test validation handles None input."""
        result = self.handler._validate_message_content(None, "Fallback")
        assert result == "Fallback"

    def test_validate_message_content_non_string_input(self):
        """Test validation handles non-string input."""
        result = self.handler._validate_message_content(123, "Fallback")
        assert result == "123"

    def test_validate_message_content_valid_content(self):
        """Test validation preserves valid content."""
        content = "This is a valid message!"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content

    def test_validate_message_content_strips_whitespace(self):
        """Test validation strips leading/trailing whitespace."""
        content = "  Valid message  "
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == "Valid message"

    def test_validate_message_content_removes_control_chars(self):
        """Test validation removes control characters."""
        content = "Valid\x00message\x1f"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == "Validmessage"

    def test_validate_message_content_preserves_newlines_tabs(self):
        """Test validation preserves newlines and tabs."""
        content = "Line 1\nLine 2\tTabbed"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content

    def test_validate_message_content_truncates_long_messages(self):
        """Test validation truncates very long messages."""
        content = "a" * 5000
        result = self.handler._validate_message_content(content, "Fallback")
        assert len(result) <= 4000
        assert result.endswith("...")

    def test_validate_message_content_unicode_handling(self):
        """Test validation handles Unicode content properly."""
        content = "Hello 🌍 Unicode test ñ"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content

    @pytest.mark.asyncio
    async def test_safe_reply_with_valid_content(self):
        """Test _safe_reply sends valid content successfully."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        await self.handler._safe_reply(mock_message, "Valid content", "Fallback")
        
        mock_message.reply.assert_called_once_with("Valid content")

    @pytest.mark.asyncio
    async def test_safe_reply_with_empty_content(self):
        """Test _safe_reply uses fallback for empty content."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        await self.handler._safe_reply(mock_message, "", "Fallback message")
        
        mock_message.reply.assert_called_once_with("Fallback message")

    @pytest.mark.asyncio
    async def test_safe_reply_handles_telegram_errors(self):
        """Test _safe_reply handles Telegram API errors gracefully."""
        mock_message = Mock()
        mock_message.reply = AsyncMock(side_effect=Exception("Telegram error"))
        
        # Should not raise exception
        await self.handler._safe_reply(mock_message, "Content", "Fallback")
        
        # Should attempt fallback
        assert mock_message.reply.call_count == 2

    @pytest.mark.asyncio
    async def test_process_agent_response_with_empty_answer(self):
        """Test _process_agent_response handles empty agent responses."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        chat_id = 12345
        
        # Test with empty answer
        result = await self.handler._process_agent_response(mock_message, chat_id, "")
        
        # Should send fallback message
        mock_message.reply.assert_called_once()
        args = mock_message.reply.call_args[0]
        assert "I processed your message but didn't have a response" in args[0]
        assert result is False

    @pytest.mark.asyncio
    async def test_process_agent_response_with_whitespace_answer(self):
        """Test _process_agent_response handles whitespace-only responses."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        chat_id = 12345
        
        # Test with whitespace-only answer
        result = await self.handler._process_agent_response(mock_message, chat_id, "   \n\t  ")
        
        # Should send fallback message
        mock_message.reply.assert_called_once()
        args = mock_message.reply.call_args[0]
        assert "I processed your message but didn't have a response" in args[0]
        assert result is False

    @pytest.mark.asyncio
    async def test_process_agent_response_with_valid_answer(self):
        """Test _process_agent_response handles valid responses."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        chat_id = 12345
        answer = "This is a valid response!"
        
        result = await self.handler._process_agent_response(mock_message, chat_id, answer)
        
        # Should send the actual answer
        mock_message.reply.assert_called_once_with(answer)
        assert result is False

    @pytest.mark.asyncio
    async def test_process_agent_response_with_image_empty_caption(self):
        """Test _process_agent_response handles image generation with empty caption."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        # Mock the client.send_photo method
        self.handler.client.send_photo = AsyncMock()
        
        chat_id = 12345
        # Image response with empty caption
        answer = "TELEGRAM_IMAGE_GENERATED|/path/to/image.jpg|"
        
        # Mock Path.exists to return True
        import unittest.mock
        with unittest.mock.patch('pathlib.Path.exists', return_value=True):
            with unittest.mock.patch('os.remove'):
                result = await self.handler._process_agent_response(mock_message, chat_id, answer)
        
        # Should send image with fallback caption
        self.handler.client.send_photo.assert_called_once()
        args, kwargs = self.handler.client.send_photo.call_args
        assert kwargs['caption'] == "🖼️ Generated image"  # Fallback caption
        assert result is True

    @pytest.mark.asyncio
    async def test_process_agent_response_long_message_split(self):
        """Test _process_agent_response properly splits long messages."""
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        chat_id = 12345
        # Create a message longer than 4000 characters
        long_answer = "a" * 5000
        
        result = await self.handler._process_agent_response(mock_message, chat_id, long_answer)
        
        # Should split into multiple messages
        assert mock_message.reply.call_count > 1
        assert result is False


class TestEdgeCasesValidation:
    """Test edge cases for message validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )

    def test_validate_content_with_only_emojis(self):
        """Test validation handles emoji-only content."""
        content = "🎉🎊🥳"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content

    def test_validate_content_with_mixed_whitespace_and_chars(self):
        """Test validation handles mixed whitespace and characters."""
        content = "  \n  a  \t  "
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == "a"

    def test_validate_content_boundary_length(self):
        """Test validation handles messages at the 4000 character boundary."""
        content = "a" * 4000
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content
        assert len(result) == 4000

    def test_validate_content_just_over_boundary(self):
        """Test validation handles messages just over the 4000 character boundary."""
        content = "a" * 4001
        result = self.handler._validate_message_content(content, "Fallback")
        assert result.endswith("...")
        assert len(result) == 4000

    def test_validate_content_with_special_telegram_characters(self):
        """Test validation preserves Telegram markdown characters."""
        content = "*Bold* _italic_ `code` [link](url)"
        result = self.handler._validate_message_content(content, "Fallback")
        assert result == content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])