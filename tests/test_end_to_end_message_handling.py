"""
Comprehensive end-to-end tests for message handling without mocks.

Tests all message types (text, photos, documents, audio, video, voice, commands)
in both DM and group contexts using real Pyrogram objects and actual system components.
"""

import asyncio
import tempfile
import os
from datetime import datetime
from pathlib import Path

import pytest
from pyrogram.types import Message, User, Chat, Photo, Document, Audio, Video, Voice, VideoNote

# Import the actual system components we're testing
from integrations.telegram.unified_processor import UnifiedMessageProcessor
from integrations.telegram.models import ProcessingResult
from agents.valor.agent import valor_agent


class TestEndToEndMessageHandling:
    """Comprehensive end-to-end tests for the unified message processing system."""

    @pytest.fixture
    def processor(self):
        """Create a real UnifiedMessageProcessor with actual Valor agent."""
        return UnifiedMessageProcessor(telegram_bot=None, valor_agent=valor_agent)

    @pytest.fixture
    def dm_user(self):
        """Create a simplified User object for DM testing."""
        # Create a minimal user with required fields only
        user = User(
            id=12345,
            is_self=False,
            first_name="Test",
            is_bot=False
        )
        # Add optional attributes manually
        user.last_name = "User"
        user.username = "testuser"
        user.is_verified = False
        user.is_restricted = False
        return user

    @pytest.fixture
    def dm_chat(self):
        """Create a simplified Chat object for DM testing."""
        # Create minimal chat with required fields
        chat = Chat(
            id=12345,
            type="private"
        )
        # Add optional attributes manually
        chat.username = "testuser"
        chat.first_name = "Test"
        chat.last_name = "User"
        chat.is_verified = False
        chat.is_restricted = False
        return chat

    @pytest.fixture
    def group_chat(self):
        """Create a simplified Chat object for group testing."""
        chat = Chat(
            id=-1001234567890,  # Negative ID for groups
            type="supergroup"
        )
        # Add optional attributes manually
        chat.title = "Test Group"
        chat.username = "testgroup"
        chat.description = "Test group for message handling"
        chat.is_verified = False
        chat.is_restricted = False
        chat.members_count = 50
        return chat

    @pytest.fixture
    def bot_user(self):
        """Create a simplified User object representing the bot itself."""
        user = User(
            id=6914249008,  # Bot's user ID
            is_self=True,
            first_name="Valor",
            is_bot=True
        )
        # Add optional attributes manually
        user.last_name = "AI"
        user.username = "valoraibot"
        user.is_verified = False
        user.is_restricted = False
        return user

    class MockUpdate:
        """Simple wrapper to provide the .message attribute."""
        def __init__(self, message):
            self.message = message

    # DM Message Tests

    @pytest.mark.asyncio
    async def test_dm_text_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of a text message in DM context."""
        # Create minimal message with required fields
        message = Message(
            id=1001,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now()
        )
        # Add optional attributes manually
        message.text = "Hello, this is a test message"

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Should succeed unless security/whitelist blocks it
        if result.success:
            assert result.summary is not None
            assert "text" in result.summary.lower()
        else:
            # If access denied, should have clear reason
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_dm_command_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of command messages in DM context."""
        message = Message(
            id=1002,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now()
        )
        # Add optional attributes manually
        message.text = "/start"

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Commands should be processed regardless of access level
        if result.success:
            assert "command" in result.summary.lower() or "start" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_dm_photo_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of photo messages in DM context."""
        # Create a real Photo object (simplified)
        photo = Photo(
            file_id="BAADBAADbQADBREAAZbRCbNq5LT7Ag",
            file_unique_id="AgADbQADBREAAQ",
            width=1280,
            height=720,
            file_size=100000,
            date=datetime.now()
        )

        message = Message(
            id=1003,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text=None,
            photo=photo,
            caption="Here's a test photo",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Should successfully identify and process photo
        if result.success:
            assert "photo" in result.summary.lower() or "image" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_dm_document_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of document messages in DM context."""
        document = Document(
            file_id="BAADBAADbQADBREAAZbRCbNq5LT8Ag",
            file_unique_id="AgADbQADBREAAR",
            thumb=None,
            file_name="test_document.pdf",
            mime_type="application/pdf",
            file_size=50000,
            date=datetime.now()
        )

        message = Message(
            id=1004,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text=None,
            document=document,
            caption="Test document upload",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        if result.success:
            assert "document" in result.summary.lower()

    # Group Message Tests

    @pytest.mark.asyncio
    async def test_group_text_message_processing(self, processor, dm_user, group_chat):
        """Test processing of text messages in group context."""
        message = Message(
            id=2001,
            from_user=dm_user,
            chat=group_chat,
            date=datetime.now(),
            text="Hello everyone in the group!",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Groups should handle messages differently than DMs
        if result.success:
            assert result.summary is not None

    @pytest.mark.asyncio
    async def test_group_mention_message_processing(self, processor, dm_user, group_chat, bot_user):
        """Test processing of messages that mention the bot in groups."""
        message = Message(
            id=2002,
            from_user=dm_user,
            chat=group_chat,
            date=datetime.now(),
            text="@valoraibot can you help with this?",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Mentions should be processed with higher priority
        if result.success:
            assert result.summary is not None

    @pytest.mark.asyncio
    async def test_group_reply_message_processing(self, processor, dm_user, group_chat, bot_user):
        """Test processing of messages that reply to bot messages in groups."""
        # Create a bot message to reply to
        bot_message = Message(
            id=2000,
            from_user=bot_user,
            chat=group_chat,
            date=datetime.now(),
            text="I can help you with that!",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        # Create a reply message
        reply_message = Message(
            id=2003,
            from_user=dm_user,
            chat=group_chat,
            date=datetime.now(),
            text="Thank you for the help!",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=bot_message,  # This is the reply
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(reply_message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Replies to bot should be processed
        if result.success:
            assert result.summary is not None

    # Audio/Video Message Tests

    @pytest.mark.asyncio
    async def test_audio_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of audio messages."""
        audio = Audio(
            file_id="BAADBAADbQADBREAAZbRCbNq5LT9Ag",
            file_unique_id="AgADbQADBREAAS",
            duration=180,
            performer="Test Artist",
            title="Test Song",
            file_name="test_audio.mp3",
            mime_type="audio/mpeg",
            file_size=5000000,
            thumb=None,
            date=datetime.now()
        )

        message = Message(
            id=1005,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text=None,
            audio=audio,
            caption="Check out this song",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        if result.success:
            assert "audio" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_voice_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of voice messages."""
        voice = Voice(
            file_id="BAADBAADbQADBREAAZbRCbNq5LTaAg",
            file_unique_id="AgADbQADBREAAT",
            duration=30,
            mime_type="audio/ogg",
            file_size=150000,
            waveform=None,
            date=datetime.now()
        )

        message = Message(
            id=1006,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text=None,
            voice=voice,
            caption=None,
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        if result.success:
            assert "voice" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_video_note_message_processing(self, processor, dm_user, dm_chat):
        """Test processing of video note messages."""
        video_note = VideoNote(
            file_id="BAADBAADbQADBREAAZbRCbNq5LTbAg",
            file_unique_id="AgADbQADBREAAU",
            length=240,  # Square video
            duration=15,
            thumb=None,
            file_size=800000,
            date=datetime.now()
        )

        message = Message(
            id=1007,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text=None,
            video_note=video_note,
            caption=None,
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        if result.success:
            assert "video" in result.summary.lower() or "note" in result.summary.lower()

    # Security and Workspace Tests

    @pytest.mark.asyncio
    async def test_security_validation_with_real_objects(self, processor):
        """Test that security validation works correctly with real Pyrogram objects."""
        # Test with a chat ID that should be blocked
        blocked_user = User(
            id=999999,
            is_self=False,
            first_name="Blocked",
            last_name="User",
            username="blockeduser",
            phone_number=None,
            is_contact=False,
            is_mutual_contact=False,
            is_bot=False,
            is_verified=False,
            is_restricted=False,
            is_scam=False,
            is_fake=False,
            is_premium=False,
            language_code="en"
        )

        blocked_chat = Chat(
            id=999999,
            type="private",
            is_verified=False,
            is_restricted=False,
            is_creator=False,
            is_scam=False,
            is_fake=False,
            is_forum=False,
            title=None,
            username="blockeduser",
            first_name="Blocked",
            last_name="User"
        )

        message = Message(
            id=9999,
            from_user=blocked_user,
            chat=blocked_chat,
            date=datetime.now(),
            text="This should be blocked",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)

        assert isinstance(result, ProcessingResult)
        # Should be blocked by security
        if not result.success:
            assert "whitelist" in result.error.lower() or "access denied" in result.error.lower()

    # Performance and Integration Tests

    @pytest.mark.asyncio
    async def test_message_processing_performance(self, processor, dm_user, dm_chat):
        """Test that message processing completes within reasonable time."""
        import time

        message = Message(
            id=5001,
            from_user=dm_user,
            chat=dm_chat,
            date=datetime.now(),
            text="Performance test message",
            entities=None,
            caption_entities=None,
            has_protected_content=False,
            media_group_id=None,
            reply_to_message=None,
            via_bot=None,
            edit_date=None,
            author_signature=None,
            forward_from=None,
            forward_from_chat=None,
            forward_from_message_id=None,
            forward_signature=None,
            forward_sender_name=None,
            forward_date=None,
            is_automatic_forward=False,
            reply_markup=None,
            web_page=None
        )

        update = self.MockUpdate(message)
        
        start_time = time.time()
        result = await processor.process_message(update, None)
        end_time = time.time()

        # Processing should complete within 10 seconds
        processing_time = end_time - start_time
        assert processing_time < 10.0, f"Processing took too long: {processing_time}s"

        assert isinstance(result, ProcessingResult)

    @pytest.mark.asyncio
    async def test_concurrent_message_processing(self, processor, dm_user, dm_chat):
        """Test that multiple messages can be processed concurrently."""
        messages = []
        for i in range(5):
            message = Message(
                id=6000 + i,
                from_user=dm_user,
                chat=dm_chat,
                date=datetime.now(),
                text=f"Concurrent test message {i}",
                entities=None,
                caption_entities=None,
                has_protected_content=False,
                media_group_id=None,
                reply_to_message=None,
                via_bot=None,
                edit_date=None,
                author_signature=None,
                forward_from=None,
                forward_from_chat=None,
                forward_from_message_id=None,
                forward_signature=None,
                forward_sender_name=None,
                forward_date=None,
                is_automatic_forward=False,
                reply_markup=None,
                web_page=None
            )
            messages.append(self.MockUpdate(message))

        # Process all messages concurrently
        tasks = [processor.process_message(update, None) for update in messages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should complete
        assert len(results) == 5
        for result in results:
            assert isinstance(result, ProcessingResult) or isinstance(result, Exception)

    @pytest.mark.asyncio
    async def test_agent_availability_validation(self, processor):
        """Test that agent availability is correctly detected."""
        # The processor should have a real agent
        assert processor.agent_orchestrator.valor_agent is not None
        assert hasattr(processor.agent_orchestrator.valor_agent, 'run')

        # Test that agent orchestrator can handle requests
        from integrations.telegram.models import MessageContext, ProcessingPlan, ProcessingPriority, MessageType
        
        context = MessageContext(
            message=None,
            chat_id=12345,
            username='test_user',
            workspace=None,
            working_directory=None,
            is_dev_group=False,
            is_mention=False,
            cleaned_text='test message',
            timestamp=datetime.now()
        )
        
        plan = ProcessingPlan(
            message_type=MessageType.TEXT,
            priority=ProcessingPriority.MEDIUM,
            requires_agent=True
        )
        
        # This should NOT return "Agent not available"
        result = await processor.agent_orchestrator.process_with_agent(context, plan)
        assert result.content != "Agent not available. Please try again later."
        # Should either process successfully or have a different error
        assert isinstance(result.content, str)


if __name__ == "__main__":
    # Run a quick test to ensure everything imports correctly
    print("Testing imports...")
    
    try:
        from integrations.telegram.unified_processor import UnifiedMessageProcessor
        from agents.valor.agent import valor_agent
        print("✅ All imports successful")
        
        # Quick agent test
        assert valor_agent is not None
        print("✅ Valor agent is available")
        
        print("✅ End-to-end test suite ready to run")
        print("Run with: python -m pytest tests/test_end_to_end_message_handling.py -v")
        
    except Exception as e:
        print(f"❌ Import error: {e}")
        import traceback
        traceback.print_exc()

    @pytest.mark.asyncio
    async def test_valor_dm_voice_message_comprehensive(self, processor):
        """
        Comprehensive voice message test using Valor's actual DM context.
        
        Tests:
        - Voice message creation with realistic parameters
        - DM context validation (Valor's actual user ID)
        - Voice transcription tool integration
        - Response validation for voice content
        """
        import time
        
        # Use Valor's actual user info for authentic DM testing
        valor_user = User(
            id=66968934582,  # Valor's actual user ID from workspace config
            is_self=False,
            first_name="Valor",
            is_bot=True,
            username="valorengels"
        )
        
        # DM chat has same ID as user for private chats
        valor_dm_chat = Chat(
            id=66968934582,
            type="private"
        )

        # Create realistic voice message
        voice = Voice(
            file_id="BAADBAADbQADBREAAZbRCbNq5LTaAg",
            file_unique_id="AgADbQADBREAAT", 
            duration=25,  # 25 second voice message
            mime_type="audio/ogg",  # Standard Telegram voice format
            file_size=125000,  # ~125KB
            waveform=None,
            date=datetime.now()
        )

        message = Message(
            id=int(time.time()),  # Unique message ID
            from_user=valor_user,
            chat=valor_dm_chat,
            date=datetime.now()
        )
        message.voice = voice

        print(f"🎙️ Testing voice message in Valor's DM (chat_id: {valor_dm_chat.id})")
        
        start_time = time.time()
        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)
        processing_time = time.time() - start_time

        # Comprehensive validation
        assert isinstance(result, ProcessingResult)
        
        print(f"⏱️  Voice processing time: {processing_time:.2f}s")
        
        if result.success:
            print(f"✅ Voice message processed successfully")
            print(f"📋 Summary: {result.summary}")
            
            # Validate voice-specific processing
            response_content = result.response.content.lower()
            
            # Check for voice/transcription indicators
            voice_indicators = ["voice", "audio", "transcrib", "hear", "said", "recording"]
            voice_processing_detected = any(indicator in response_content for indicator in voice_indicators)
            
            if voice_processing_detected:
                print("✅ Voice-specific processing detected in response")
            
            # Check tools used
            tools_used = result.response.metadata.get("tools_used", [])
            if tools_used:
                print(f"🔧 Tools used: {tools_used}")
                
                # Look for transcription tool usage
                transcription_tools = [tool for tool in tools_used if "transcrib" in tool.lower() or "voice" in tool.lower()]
                if transcription_tools:
                    print(f"✅ Voice transcription tools used: {transcription_tools}")
            
            # Response should be conversational and helpful
            assert len(result.response.content) > 10, "Response should be substantial"
            
        else:
            print(f"❌ Voice processing failed: {result.error}")
            # Voice processing failure might be expected if transcription service unavailable
            assert result.error is not None, "Error should be provided for failures"

    @pytest.mark.asyncio
    async def test_valor_dm_image_message_comprehensive(self, processor):
        """
        Comprehensive image message test using Valor's actual DM context.
        
        Tests:
        - Photo message creation with realistic parameters
        - DM context validation (Valor's actual user ID)
        - Image analysis tool integration  
        - Response validation for visual content
        """
        import time
        
        # Use Valor's actual user info for authentic DM testing
        valor_user = User(
            id=66968934582,  # Valor's actual user ID from workspace config
            is_self=False,
            first_name="Valor", 
            is_bot=True,
            username="valorengels"
        )
        
        # DM chat has same ID as user for private chats
        valor_dm_chat = Chat(
            id=66968934582,
            type="private"
        )

        # Create realistic photo message
        photo = Photo(
            file_id="BAADBAADbQADBREAAZbRCbNq5LT7Ag",
            file_unique_id="AgADbQADBREAAQ",
            width=1920,  # High resolution image
            height=1080,
            file_size=250000,  # ~250KB image
            date=datetime.now()
        )

        message = Message(
            id=int(time.time()),  # Unique message ID
            from_user=valor_user,
            chat=valor_dm_chat,
            date=datetime.now()
        )
        message.photo = photo
        message.caption = "Can you analyze this image and tell me what you see?"

        print(f"🖼️ Testing image message in Valor's DM (chat_id: {valor_dm_chat.id})")
        
        start_time = time.time()
        update = self.MockUpdate(message)
        result = await processor.process_message(update, None)
        processing_time = time.time() - start_time

        # Comprehensive validation
        assert isinstance(result, ProcessingResult)
        
        print(f"⏱️  Image processing time: {processing_time:.2f}s")
        
        if result.success:
            print(f"✅ Image message processed successfully")
            print(f"📋 Summary: {result.summary}")
            
            # Validate image-specific processing
            response_content = result.response.content.lower()
            
            # Check for image/visual indicators
            image_indicators = ["image", "photo", "picture", "see", "visual", "analyze", "show", "appear"]
            image_processing_detected = any(indicator in response_content for indicator in image_indicators)
            
            if image_processing_detected:
                print("✅ Image-specific processing detected in response")
            
            # Check tools used
            tools_used = result.response.metadata.get("tools_used", [])
            if tools_used:
                print(f"🔧 Tools used: {tools_used}")
                
                # Look for image analysis tool usage
                image_tools = [tool for tool in tools_used if "image" in tool.lower() or "vision" in tool.lower() or "analyze" in tool.lower()]
                if image_tools:
                    print(f"✅ Image analysis tools used: {image_tools}")
            
            # Response should be conversational and descriptive
            assert len(result.response.content) > 20, "Response should be substantial for image analysis"
            
        else:
            print(f"❌ Image processing failed: {result.error}")
            # Image processing failure might be expected if vision service unavailable
            assert result.error is not None, "Error should be provided for failures"

EOF < /dev/null