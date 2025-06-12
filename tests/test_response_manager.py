"""
Tests for ResponseManager component.

Tests response formatting, delivery, media handling, and error recovery.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime

# Mock telegram exceptions since we're using pyrogram
class BadRequest(Exception): pass
class NetworkError(Exception): pass
class TimedOut(Exception): pass

class ParseMode:
    MARKDOWN = "markdown"

from integrations.telegram.components.response_manager import ResponseManager
from integrations.telegram.models import (
    AgentResponse,
    MessageContext,
    DeliveryResult,
    MediaAttachment,
    MessageType
)


@pytest.fixture
def mock_bot():
    """Create mock Telegram bot."""
    bot = AsyncMock()
    bot.send_message.return_value = MagicMock(message_id=456)
    bot.send_photo.return_value = MagicMock(message_id=457)
    bot.send_document.return_value = MagicMock(message_id=458)
    bot.set_message_reaction.return_value = True
    return bot


@pytest.fixture
def response_manager(mock_bot):
    """Create ResponseManager instance."""
    return ResponseManager(telegram_bot=mock_bot)


@pytest.fixture
def basic_context():
    """Create basic message context."""
    message = MagicMock()
    message.message_id = 123
    message.text = "Test message"
    message.from_user = MagicMock(username="testuser")
    
    return MessageContext(
        message=message,
        chat_id=12345,
        username="testuser",
        workspace=None,
        working_directory=None,
        is_dev_group=False,
        is_mention=False,
        cleaned_text="Test message",
        timestamp=datetime.now()
    )


@pytest.fixture
def basic_response():
    """Create basic agent response."""
    return AgentResponse(
        success=True,
        content="This is a test response",
        agent_name="valor_agent",
        message_type=MessageType.TEXT,
        processing_time=1.5,
        tokens_used=50,
        error=None,
        metadata={},
        has_media=False,
        media_attachments=[],
        reactions=[]
    )


class TestResponseManager:
    """Test suite for ResponseManager."""

    @pytest.mark.asyncio
    async def test_basic_response_delivery(self, response_manager, basic_context, basic_response, mock_bot):
        """Test basic text response delivery."""
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is True
        assert result.message_id == 456
        
        # Verify bot methods called
        mock_bot.send_message.assert_called_once_with(
            chat_id=12345,
            text="This is a test response",
            parse_mode=ParseMode.MARKDOWN,
            reply_to_message_id=123
        )

    @pytest.mark.asyncio
    async def test_long_message_splitting(self, response_manager, basic_context, mock_bot):
        """Test splitting of long messages."""
        # Create response longer than Telegram limit
        long_content = "A" * 5000  # Over 4096 char limit
        long_response = AgentResponse(
            success=True,
            content=long_content,
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=1.0,
            tokens_used=1000,
            error=None,
            metadata={},
            has_media=False,
            media_attachments=[],
            reactions=[]
        )
        
        result = await response_manager.deliver_response(long_response, basic_context)
        
        assert result.success is True
        # Should have been split into multiple messages
        assert mock_bot.send_message.call_count > 1

    @pytest.mark.asyncio
    async def test_markdown_formatting_error(self, response_manager, basic_context, mock_bot):
        """Test fallback when markdown parsing fails."""
        # Mock markdown parsing error
        mock_bot.send_message.side_effect = [
            BadRequest("Can't parse entities"),
            MagicMock(message_id=456)  # Success on retry without markdown
        ]
        
        response = AgentResponse(
            success=True,
            content="*Invalid markdown [link",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=1.0,
            tokens_used=10,
            error=None,
            metadata={},
            has_media=False,
            media_attachments=[],
            reactions=[]
        )
        
        result = await response_manager.deliver_response(response, basic_context)
        
        assert result.success is True
        assert mock_bot.send_message.call_count == 2
        # Second call should be without parse_mode
        second_call = mock_bot.send_message.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    @pytest.mark.asyncio
    async def test_media_attachment_delivery(self, response_manager, basic_context, mock_bot):
        """Test delivery of response with media attachments."""
        response_with_media = AgentResponse(
            success=True,
            content="Here's the image you requested",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=2.0,
            tokens_used=100,
            error=None,
            metadata={},
            has_media=True,
            media_attachments=[
                MediaAttachment(
                    media_type="image",
                    file_path="/tmp/generated_image.png",
                    caption="Generated image"
                )
            ],
            reactions=[]
        )
        
        result = await response_manager.deliver_response(response_with_media, basic_context)
        
        assert result.success is True
        # Should send both text and photo
        mock_bot.send_message.assert_called_once()
        mock_bot.send_photo.assert_called_once_with(
            chat_id=12345,
            photo="/tmp/generated_image.png",
            caption="Generated image",
            reply_to_message_id=456  # Reply to the text message
        )

    @pytest.mark.asyncio
    async def test_reaction_delivery(self, response_manager, basic_context, mock_bot):
        """Test adding reactions to messages."""
        response_with_reactions = AgentResponse(
            success=True,
            content="That's amazing!",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=1.0,
            tokens_used=20,
            error=None,
            metadata={},
            has_media=False,
            media_attachments=[],
            reactions=["🎉", "👍", "🔥"]
        )
        
        result = await response_manager.deliver_response(response_with_reactions, basic_context)
        
        assert result.success is True
        # Should add reactions to original message
        assert mock_bot.set_message_reaction.call_count == 3
        
        # Verify each reaction
        reaction_calls = mock_bot.set_message_reaction.call_args_list
        for call, expected_reaction in zip(reaction_calls, ["🎉", "👍", "🔥"]):
            assert call.kwargs["chat_id"] == 12345
            assert call.kwargs["message_id"] == 123  # Original message
            assert call.kwargs["reaction"] == expected_reaction

    @pytest.mark.asyncio
    async def test_network_error_retry(self, response_manager, basic_context, basic_response, mock_bot):
        """Test retry logic for network errors."""
        # Mock network error then success
        mock_bot.send_message.side_effect = [
            NetworkError("Network error"),
            NetworkError("Network error"),
            MagicMock(message_id=456)  # Success on third try
        ]
        
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is True
        assert mock_bot.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self, response_manager, basic_context, basic_response, mock_bot):
        """Test handling of timeout errors."""
        # Mock timeout error
        mock_bot.send_message.side_effect = TimedOut("Timed out")
        
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is False
        assert result.retry_after == 60
        assert "Network error" in result.error

    @pytest.mark.asyncio
    async def test_deleted_message_fallback(self, response_manager, basic_context, basic_response, mock_bot):
        """Test fallback when replying to deleted message."""
        # Mock "message not found" error
        mock_bot.send_message.side_effect = [
            BadRequest("Message not found"),
            MagicMock(message_id=456)  # Success without reply
        ]
        
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is True
        # First message should be warning
        first_call = mock_bot.send_message.call_args_list[0]
        assert "Original message was deleted" in first_call.kwargs["text"]

    @pytest.mark.asyncio
    async def test_conversation_history_storage(self, response_manager, basic_context, basic_response):
        """Test storage of conversation history."""
        with patch('utilities.database.get_database_connection') as mock_db:
            mock_conn = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_conn
            
            result = await response_manager.deliver_response(basic_response, basic_context)
            
            assert result.success is True
            # Should store both user message and bot response
            assert mock_conn.execute.call_count == 2
            mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_response_handling(self, response_manager, basic_context, mock_bot):
        """Test handling of empty responses."""
        empty_response = AgentResponse(
            success=True,
            content="",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=1.0,
            tokens_used=0,
            error=None,
            metadata={},
            has_media=False,
            media_attachments=[],
            reactions=[]
        )
        
        result = await response_manager.deliver_response(empty_response, basic_context)
        
        assert result.success is True
        # Should send default message
        mock_bot.send_message.assert_called_once()
        sent_text = mock_bot.send_message.call_args.kwargs["text"]
        assert "no response" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_multiple_media_attachments(self, response_manager, basic_context, mock_bot):
        """Test delivery of multiple media attachments."""
        multi_media_response = AgentResponse(
            success=True,
            content="Here are your files",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=3.0,
            tokens_used=150,
            error=None,
            metadata={},
            has_media=True,
            media_attachments=[
                MediaAttachment(media_type="image", file_path="/tmp/image1.png", caption="First image"),
                MediaAttachment(media_type="image", file_path="/tmp/image2.png", caption="Second image"),
                MediaAttachment(media_type="document", file_path="/tmp/report.pdf", caption="Report")
            ],
            reactions=[]
        )
        
        result = await response_manager.deliver_response(multi_media_response, basic_context)
        
        assert result.success is True
        assert mock_bot.send_photo.call_count == 2
        assert mock_bot.send_document.call_count == 1

    @pytest.mark.asyncio
    async def test_invalid_reaction_filtering(self, response_manager, basic_context, mock_bot):
        """Test filtering of invalid reactions."""
        response_with_invalid_reactions = AgentResponse(
            success=True,
            content="Test",
            agent_name="valor_agent",
            message_type=MessageType.TEXT,
            processing_time=1.0,
            tokens_used=10,
            error=None,
            metadata={},
            has_media=False,
            media_attachments=[],
            reactions=["👍", "😀", "🎉", "invalid_emoji", "💯"]  # Mix of valid and invalid
        )
        
        result = await response_manager.deliver_response(response_with_invalid_reactions, basic_context)
        
        assert result.success is True
        # Should only set valid reactions
        assert mock_bot.set_message_reaction.call_count == 2  # Only 👍 and 🎉 are valid

    @pytest.mark.asyncio
    async def test_complete_delivery_failure(self, response_manager, basic_context, basic_response, mock_bot):
        """Test complete delivery failure handling."""
        # Mock persistent failure
        mock_bot.send_message.side_effect = BadRequest("Chat not found")
        
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is False
        assert "Chat no longer exists" in result.error

    @pytest.mark.asyncio
    async def test_metadata_propagation(self, response_manager, basic_context, basic_response):
        """Test metadata propagation in delivery result."""
        basic_response.processing_time = 2.5
        basic_response.tokens_used = 75
        basic_response.has_media = True
        basic_response.reactions = ["👍", "🎉"]
        
        result = await response_manager.deliver_response(basic_response, basic_context)
        
        assert result.success is True
        assert result.metadata["processing_time"] == 2.5
        assert result.metadata["tokens_used"] == 75
        assert result.metadata["has_media"] is True
        assert result.metadata["reaction_count"] == 2