"""
Comprehensive tests for ContextBuilder component.
"""

import asyncio
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

import pytest
from telegram.constants import MessageEntityType

from integrations.telegram.components.context_builder import ContextBuilder
from integrations.telegram.models import MessageContext, MediaInfo, MessageType


class TestContextBuilder:
    """Test ContextBuilder functionality."""
    
    @pytest.fixture
    def context_builder(self):
        """Create ContextBuilder instance with mocks."""
        mock_validator = Mock()
        mock_validator.get_workspace_for_chat.return_value = "TestWorkspace"
        mock_validator.get_working_directory.return_value = "/test/workspace"
        mock_validator.config = {
            "workspaces": {
                "TestWorkspace": {
                    "is_dev_group": True
                }
            }
        }
        
        return ContextBuilder(workspace_validator=mock_validator)
    
    @pytest.fixture
    def mock_message(self):
        """Create a mock Telegram message."""
        message = Mock()
        message.chat = Mock(id=-1001234567890)
        message.from_user = Mock(id=123, username="testuser", is_bot=False)
        message.date = datetime.now()
        message.text = "Hello @valoraibot, how are you?"
        message.caption = None
        message.entities = []
        message.reply_to_message = None
        message.photo = None
        message.document = None
        message.audio = None
        message.video = None
        message.voice = None
        message.video_note = None
        
        return message
    
    @pytest.mark.asyncio
    async def test_build_context_basic(self, context_builder, mock_message):
        """Test basic context building."""
        context = await context_builder.build_context(mock_message)
        
        assert isinstance(context, MessageContext)
        assert context.chat_id == -1001234567890
        assert context.username == "testuser"
        assert context.workspace == "TestWorkspace"
        assert context.working_directory == "/test/workspace"
        assert context.is_dev_group is True
        assert context.is_mention is True  # Message contains @valoraibot
        assert context.cleaned_text == "Hello , how are you?"  # Mention removed
    
    @pytest.mark.asyncio
    async def test_workspace_extraction(self, context_builder):
        """Test workspace information extraction."""
        workspace_info = context_builder._extract_workspace(-1001234567890)
        
        assert workspace_info["workspace"] == "TestWorkspace"
        assert workspace_info["working_directory"] == "/test/workspace"
        assert workspace_info["is_dev_group"] is True
        
        # Test with no workspace found
        context_builder.workspace_validator.get_workspace_for_chat.return_value = None
        workspace_info = context_builder._extract_workspace(-999999)
        
        assert workspace_info["workspace"] is None
        assert workspace_info["working_directory"] is None
        assert workspace_info["is_dev_group"] is False
    
    def test_mention_processing_with_entities(self, context_builder):
        """Test mention processing using message entities."""
        message = Mock()
        message.text = "Hey @valoraibot can you help?"
        
        # Create mention entity
        entity = Mock()
        entity.type = MessageEntityType.MENTION
        entity.offset = 4
        entity.length = 11  # "@valoraibot"
        message.entities = [entity]
        
        is_mention, cleaned_text = context_builder._process_mentions(message)
        
        assert is_mention is True
        assert cleaned_text == "Hey can you help?"
    
    def test_mention_processing_without_entities(self, context_builder):
        """Test mention processing without entities (fallback)."""
        message = Mock()
        message.text = "Hello @VALORaibot, what's up?"  # Different case
        message.entities = []
        
        is_mention, cleaned_text = context_builder._process_mentions(message)
        
        assert is_mention is True
        assert cleaned_text == "Hello , what's up?"
    
    def test_mention_processing_no_mention(self, context_builder):
        """Test processing when no mention present."""
        message = Mock()
        message.text = "Just a regular message"
        message.entities = []
        
        is_mention, cleaned_text = context_builder._process_mentions(message)
        
        assert is_mention is False
        assert cleaned_text == "Just a regular message"
    
    @pytest.mark.asyncio
    async def test_chat_history_loading(self, context_builder):
        """Test chat history loading from database."""
        # Mock database connection
        with patch('integrations.telegram.components.context_builder.get_database_connection') as mock_db:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [
                (1001, "user1", "Hello", False, "2024-01-01 10:00:00"),
                (1002, "valoraibot", "Hi there!", True, "2024-01-01 10:01:00"),
                (1003, "user1", "How are you?", False, "2024-01-01 10:02:00")
            ]
            mock_conn.execute.return_value = mock_cursor
            mock_db.return_value.__enter__.return_value = mock_conn
            
            history = await context_builder._load_chat_history(123)
            
            assert len(history) == 3
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "Hello"
            assert history[1]["role"] == "assistant"
            assert history[1]["content"] == "Hi there!"
            assert history[2]["role"] == "user"
            assert history[2]["content"] == "How are you?"
    
    @pytest.mark.asyncio
    async def test_reply_context_detection(self, context_builder):
        """Test reply-to message context extraction."""
        # Create mock reply-to message
        reply_message = Mock()
        reply_message.message_id = 999
        reply_message.text = "Original message"
        reply_message.from_user = Mock(username="otheruser", is_bot=False)
        reply_message.date = datetime.now()
        reply_message.photo = None
        reply_message.document = None
        reply_message.audio = None
        reply_message.video = None
        
        message = Mock()
        message.reply_to_message = reply_message
        
        reply_context = await context_builder._detect_reply_context(message)
        
        assert reply_context is not None
        assert reply_context["message_id"] == 999
        assert reply_context["text"] == "Original message"
        assert reply_context["username"] == "otheruser"
        assert reply_context["is_bot"] is False
        assert "media_type" not in reply_context
    
    @pytest.mark.asyncio
    async def test_reply_context_with_media(self, context_builder):
        """Test reply context with media message."""
        reply_message = Mock()
        reply_message.message_id = 999
        reply_message.text = None
        reply_message.caption = "Photo caption"
        reply_message.from_user = Mock(username="photouser", is_bot=False)
        reply_message.date = datetime.now()
        reply_message.photo = [Mock()]  # Has photo
        reply_message.document = None
        reply_message.audio = None
        reply_message.video = None
        
        message = Mock()
        message.reply_to_message = reply_message
        
        with patch('integrations.telegram.components.context_builder.get_message_text', return_value="Photo caption"):
            reply_context = await context_builder._detect_reply_context(message)
        
        assert reply_context["media_type"] == "photo"
        assert reply_context["text"] == "Photo caption"
    
    def test_media_info_extraction_photo(self, context_builder):
        """Test media info extraction for photo messages."""
        photo = Mock()
        photo.file_id = "photo123"
        photo.file_unique_id = "unique123"
        photo.file_size = 1024
        photo.width = 800
        photo.height = 600
        
        message = Mock()
        message.photo = [photo]  # Telegram sends array of different sizes
        
        media_info = context_builder._extract_media_info(message)
        
        assert media_info is not None
        assert media_info.media_type == MessageType.PHOTO
        assert media_info.file_id == "photo123"
        assert media_info.width == 800
        assert media_info.height == 600
    
    def test_media_info_extraction_document(self, context_builder):
        """Test media info extraction for document messages."""
        doc = Mock()
        doc.file_id = "doc123"
        doc.file_unique_id = "unique456"
        doc.file_size = 2048
        doc.mime_type = "application/pdf"
        doc.file_name = "test.pdf"
        doc.thumbnail = Mock(file_id="thumb123")
        
        message = Mock()
        message.document = doc
        message.photo = None
        
        media_info = context_builder._extract_media_info(message)
        
        assert media_info.media_type == MessageType.DOCUMENT
        assert media_info.file_id == "doc123"
        assert media_info.mime_type == "application/pdf"
        assert media_info.file_name == "test.pdf"
        assert media_info.thumbnail_file_id == "thumb123"
    
    def test_extract_urls(self, context_builder):
        """Test URL extraction from text."""
        text = """
        Check out https://example.com and http://test.org/path?query=1
        Also see https://github.com/user/repo
        """
        
        urls = context_builder.extract_urls(text)
        
        assert len(urls) == 3
        assert "https://example.com" in urls
        assert "http://test.org/path?query=1" in urls
        assert "https://github.com/user/repo" in urls
    
    def test_detect_code_blocks(self, context_builder):
        """Test code block detection."""
        text = """
        Here's some code:
        ```python
        def hello():
            print("Hello, world!")
        ```
        
        And inline code: `variable = 42`
        """
        
        code_blocks = context_builder.detect_code_blocks(text)
        
        assert len(code_blocks) == 2
        assert code_blocks[0]["language"] == "python"
        assert "def hello():" in code_blocks[0]["code"]
        assert code_blocks[1]["language"] == "inline"
        assert code_blocks[1]["code"] == "variable = 42"
    
    @pytest.mark.asyncio
    async def test_full_context_with_all_features(self, context_builder):
        """Test building context with all features active."""
        # Create complex message
        message = Mock()
        message.chat = Mock(id=-1001234567890)
        message.from_user = Mock(id=123, username="poweruser", is_bot=False)
        message.date = datetime.now()
        message.text = "Hey @valoraibot, check this code: `print('hello')`"
        message.caption = None
        
        # Add mention entity
        entity = Mock()
        entity.type = MessageEntityType.MENTION
        entity.offset = 4
        entity.length = 11
        message.entities = [entity]
        
        # Add reply
        message.reply_to_message = Mock(
            message_id=888,
            text="Previous message",
            from_user=Mock(username="otheruser", is_bot=False),
            date=datetime.now(),
            photo=None,
            document=None,
            audio=None,
            video=None
        )
        
        # Add photo
        photo = Mock(
            file_id="photo999",
            file_unique_id="unique999",
            file_size=4096,
            width=1920,
            height=1080
        )
        message.photo = [photo]
        message.document = None
        message.audio = None
        message.video = None
        message.voice = None
        message.video_note = None
        
        context = await context_builder.build_context(message)
        
        # Verify all context features
        assert context.chat_id == -1001234567890
        assert context.username == "poweruser"
        assert context.workspace == "TestWorkspace"
        assert context.is_dev_group is True
        assert context.is_mention is True
        assert "check this code:" in context.cleaned_text
        assert context.reply_context is not None
        assert context.reply_context["message_id"] == 888
        assert context.media_info is not None
        assert context.media_info.media_type == MessageType.PHOTO
        assert context.media_info.width == 1920
        
        # Check derived properties
        assert context.is_private_chat is False
        assert context.requires_response is True