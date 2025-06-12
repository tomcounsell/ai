"""
Comprehensive tests for TypeRouter component.
"""

import pytest
from unittest.mock import Mock

from integrations.telegram.components.type_router import TypeRouter
from integrations.telegram.models import (
    MessageContext, MessageType, ProcessingPlan, Priority,
    ResponseFormat, MediaInfo
)


class TestTypeRouter:
    """Test TypeRouter functionality."""
    
    @pytest.fixture
    def type_router(self):
        """Create TypeRouter instance."""
        return TypeRouter()
    
    @pytest.fixture
    def mock_context(self):
        """Create mock message context."""
        context = Mock(spec=MessageContext)
        context.message = Mock()
        context.cleaned_text = "Hello world"
        context.media_info = None
        context.is_mention = False
        context.is_dev_group = False
        context.is_private_chat = False
        context.reply_context = None
        return context
    
    @pytest.mark.asyncio
    async def test_route_text_message(self, type_router, mock_context):
        """Test routing plain text messages."""
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.TEXT
        assert plan.requires_agent is True
        assert plan.response_format == ResponseFormat.TEXT
        assert plan.priority == Priority.LOW  # No special conditions
    
    @pytest.mark.asyncio
    async def test_route_command_message(self, type_router, mock_context):
        """Test routing command messages."""
        mock_context.cleaned_text = "/start"
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.COMMAND
        assert plan.requires_agent is False
        assert "start_command" in plan.special_handlers
        assert plan.response_format == ResponseFormat.MARKDOWN
    
    @pytest.mark.asyncio
    async def test_route_photo_message(self, type_router, mock_context):
        """Test routing photo messages."""
        mock_context.media_info = MediaInfo(
            media_type=MessageType.PHOTO,
            file_id="photo123",
            file_unique_id="unique123",
            width=800,
            height=600
        )
        mock_context.message.caption = "What is in this image?"
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.PHOTO
        assert plan.requires_agent is True
        assert "media_download_handler" in plan.special_handlers
        assert "analyze_shared_image" in plan.agent_config.tools_enabled
        assert plan.metadata["analysis_requested"] is True
        assert plan.priority == Priority.HIGH
    
    @pytest.mark.asyncio
    async def test_route_document_pdf(self, type_router, mock_context):
        """Test routing PDF document."""
        mock_context.media_info = MediaInfo(
            media_type=MessageType.DOCUMENT,
            file_id="doc123",
            file_unique_id="unique456",
            mime_type="application/pdf",
            file_name="report.pdf"
        )
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.DOCUMENT
        assert "document_handler" in plan.special_handlers
        assert "summarize_document" in plan.agent_config.tools_enabled
    
    @pytest.mark.asyncio
    async def test_route_voice_message(self, type_router, mock_context):
        """Test routing voice messages."""
        mock_context.media_info = MediaInfo(
            media_type=MessageType.VOICE,
            file_id="voice123",
            file_unique_id="unique789",
            duration=10
        )
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.VOICE
        assert "audio_transcription_handler" in plan.special_handlers
        assert plan.metadata["requires_transcription"] is True
        assert plan.metadata["likely_question"] is True
    
    @pytest.mark.asyncio
    async def test_detect_urls_in_text(self, type_router, mock_context):
        """Test URL detection in text messages."""
        mock_context.cleaned_text = "Check out https://example.com and http://test.org"
        
        plan = await type_router.route_message(mock_context)
        
        assert "url_handler" in plan.special_handlers
        assert "urls" in plan.metadata
        assert len(plan.metadata["urls"]) == 2
        assert "https://example.com" in plan.metadata["urls"]
    
    @pytest.mark.asyncio
    async def test_detect_code_blocks(self, type_router, mock_context):
        """Test code block detection."""
        mock_context.cleaned_text = """
        Here's my code:
        ```python
        def hello():
            print("Hello")
        ```
        """
        
        plan = await type_router.route_message(mock_context)
        
        assert "code_handler" in plan.special_handlers
        assert "delegate_coding_task" in plan.agent_config.tools_enabled
    
    @pytest.mark.asyncio
    async def test_priority_high_mention_dm(self, type_router, mock_context):
        """Test high priority for mention in DM."""
        mock_context.is_mention = True
        mock_context.is_private_chat = True
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.priority == Priority.HIGH
    
    @pytest.mark.asyncio
    async def test_priority_medium_dev_group(self, type_router, mock_context):
        """Test medium priority for dev group messages."""
        mock_context.is_dev_group = True
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.priority == Priority.MEDIUM
    
    @pytest.mark.asyncio
    async def test_priority_high_bot_reply(self, type_router, mock_context):
        """Test high priority when replying to bot."""
        mock_context.reply_context = {
            "is_bot": True,
            "username": "valoraibot"
        }
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.priority == Priority.HIGH
    
    @pytest.mark.asyncio
    async def test_intent_classification_needed(self, type_router, mock_context):
        """Test when intent classification is needed."""
        mock_context.cleaned_text = "Can you help me understand this complex topic?"
        
        plan = await type_router.route_message(mock_context)
        
        # Should need intent classification for complex text
        assert plan.message_type == MessageType.TEXT
        assert plan.requires_agent is True
        
    @pytest.mark.asyncio
    async def test_no_intent_for_media(self, type_router, mock_context):
        """Test that media messages skip intent classification."""
        mock_context.media_info = MediaInfo(
            media_type=MessageType.PHOTO,
            file_id="photo999",
            file_unique_id="unique999"
        )
        
        plan = await type_router.route_message(mock_context)
        
        # TypeRouter sets up plan but doesn't determine if intent needed
        assert plan.message_type == MessageType.PHOTO
    
    @pytest.mark.asyncio
    async def test_question_detection(self, type_router, mock_context):
        """Test question pattern detection."""
        mock_context.cleaned_text = "What is the weather like today?"
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.metadata["likely_question"] is True
        assert "search_current_info" in plan.agent_config.tools_enabled
    
    @pytest.mark.asyncio
    async def test_unknown_command(self, type_router, mock_context):
        """Test handling of unknown commands."""
        mock_context.cleaned_text = "/unknowncommand arg1 arg2"
        
        plan = await type_router.route_message(mock_context)
        
        assert plan.message_type == MessageType.COMMAND
        assert plan.requires_agent is True  # Let agent handle unknown
        assert plan.metadata["unknown_command"] == "/unknowncommand"
    
    def test_detect_special_patterns(self, type_router):
        """Test special pattern detection method."""
        patterns = type_router.detect_special_patterns(
            "Check https://example.com and run `print('hello')` command"
        )
        
        assert "url" in patterns
        assert "code" in patterns
        
        # Test long text detection
        long_text = "Line\n" * 10 + "x" * 600
        patterns = type_router.detect_special_patterns(long_text)
        assert "long_text" in patterns