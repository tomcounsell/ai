#!/usr/bin/env python3
"""
Comprehensive end-to-end image message handling tests.

Tests the complete flow from image receipt through processing to response,
including error handling cases and edge conditions.
"""

import asyncio
import os
import sys
import tempfile
import base64
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Add the parent directory to Python path for imports
sys.path.append(str(Path(__file__).parent.parent))

from integrations.telegram.handlers import MessageHandler
from pyrogram.enums import ChatType


class MockMessage:
    """Mock Telegram message for testing."""
    
    def __init__(self, chat_id: int, has_photo: bool = False, caption: str = None, 
                 message_text: str = None, file_path: str = None):
        self.chat = MagicMock()
        self.chat.id = chat_id
        self.chat.type = ChatType.PRIVATE
        
        self.from_user = MagicMock()
        self.from_user.username = 'test_user'
        self.from_user.id = 12345
        
        # Photo-related attributes
        self.photo = MagicMock() if has_photo else None
        self.caption = caption
        self.text = message_text
        
        # Document/video/audio attributes
        self.document = None
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        
        # Message metadata
        self.id = 1001
        self.date = MagicMock()
        self.date.timestamp.return_value = 1234567890
        
        # Mock download method
        self.download_path = file_path
        self.replies = []
        
    async def download(self, in_memory=False):
        """Mock download method."""
        if self.download_path:
            return self.download_path
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            # Write minimal JPEG header
            tmp.write(b'\xFF\xD8\xFF\xE0')
            return tmp.name
            
    async def reply(self, text):
        """Mock reply method."""
        self.replies.append(text)
        print(f"Mock reply: {text[:100]}...")


class MockTelegramClient:
    """Mock Telegram client for testing."""
    
    def __init__(self):
        self.sent_photos = []
        self.sent_reactions = []
        self.read_receipts = []
        
    async def get_me(self):
        """Mock get_me method."""
        me = MagicMock()
        me.username = 'test_bot'
        me.id = 54321
        return me
        
    async def send_photo(self, chat_id, photo, caption=None):
        """Mock send_photo method."""
        self.sent_photos.append({
            'chat_id': chat_id,
            'photo': photo,
            'caption': caption
        })
        return True
        
    async def send_reaction(self, chat_id, message_id, reaction):
        """Mock send_reaction method."""
        self.sent_reactions.append({
            'chat_id': chat_id,
            'message_id': message_id,
            'reaction': reaction
        })
        
    async def read_chat_history(self, chat_id, message_id):
        """Mock read_chat_history method."""
        self.read_receipts.append({
            'chat_id': chat_id,
            'message_id': message_id
        })


class MockChatHistory:
    """Mock chat history for testing."""
    
    def __init__(self):
        self.messages = []
        self.chat_histories = {}
        
    def add_message(self, chat_id: int, role: str, content: str):
        """Add message to history."""
        self.messages.append({
            'chat_id': chat_id,
            'role': role,
            'content': content
        })
        
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []
        self.chat_histories[chat_id].append({
            'role': role,
            'content': content
        })
        
    def get_context(self, chat_id: int, max_context_messages: int = 10):
        """Get chat context."""
        return self.chat_histories.get(chat_id, [])[-max_context_messages:]


def create_test_image(suffix='.png'):
    """Create a temporary test image file."""
    # Create a minimal PNG image (1x1 transparent)
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
    )
    
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(png_data)
        return f.name


def create_handler_with_unified_support():
    """Create a message handler with unified processing support enabled."""
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    handler = MessageHandler(client, chat_history)
    
    # Mock notion_scout to enable unified processing
    handler.notion_scout = MagicMock()
    handler.notion_scout.anthropic_client = MagicMock()
    
    return handler, client, chat_history


class TestImageReceiptFlow:
    """Test image message receipt and initial processing."""
    
    async def test_photo_message_detection(self):
        """Test that photo messages are correctly detected and routed."""
        print("🔍 Testing photo message detection...")
        
        handler, client, chat_history = create_handler_with_unified_support()
        
        # Create a mock photo message
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption="Test photo caption",
                file_path=temp_image
            )
            
            # Mock the unified agent processing
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "Image analysis result"
                
                await handler.handle_message(client, message)
                
                # Verify photo was detected and processed
                assert len(client.sent_reactions) > 0  # Should add processing reaction
                assert len(client.read_receipts) > 0  # Should mark as read
                assert len(chat_history.messages) > 0  # Should store in history
                
                # Verify unified agent was called
                assert mock_process.called
                
                print("✅ Photo message detection working correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_photo_without_caption(self):
        """Test handling of photo messages without captions."""
        print("🔍 Testing photo without caption...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption=None,  # No caption
                file_path=temp_image
            )
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "Image analysis without caption"
                
                await handler.handle_message(client, message)
                
                # Should still process the image
                assert mock_process.called
                call_args = mock_process.call_args
                assert call_args[1]['caption'] == ""  # Empty caption passed
                
                print("✅ Photo without caption handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_photo_mention_processing_in_groups(self):
        """Test photo mention processing in group chats."""
        print("🔍 Testing photo mention processing in groups...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            # Create group message with bot mention in caption
            message = MockMessage(
                chat_id=-67890,  # Negative ID indicates group
                has_photo=True,
                caption="@test_bot analyze this image please",
                file_path=temp_image
            )
            message.chat.type = ChatType.GROUP
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "Group image analysis"
                
                await handler.handle_message(client, message)
                
                # Should process because bot was mentioned
                assert mock_process.called
                call_args = mock_process.call_args
                assert "analyze this image please" in call_args[1]['caption']
                
                print("✅ Group photo mention processing working")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)


class TestImageProcessingPipeline:
    """Test the image processing and analysis pipeline."""
    
    async def test_image_analysis_success(self):
        """Test successful image analysis flow."""
        print("🔍 Testing image analysis success flow...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption="What do you see?",
                file_path=temp_image
            )
            
            # Mock successful analysis
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "I can see a test image with minimal content."
                
                await handler.handle_message(client, message)
                
                # Verify analysis was called with correct parameters
                assert mock_process.called
                call_args = mock_process.call_args
                assert call_args[1]['image_path'] == temp_image
                assert call_args[1]['caption'] == "What do you see?"
                assert call_args[1]['chat_id'] == 12345
                assert call_args[1]['username'] == 'test_user'
                
                # Verify response was sent
                assert len(message.replies) > 0
                assert "test image" in message.replies[0]
                
                print("✅ Image analysis success flow working")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_image_analysis_fallback(self):
        """Test fallback to original valor agent when unified agent fails."""
        print("🔍 Testing image analysis fallback...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption="Analyze this",
                file_path=temp_image
            )
            
            # Mock unified agent failure, valor agent success
            with patch('agents.unified_integration.process_image_unified') as mock_unified:
                mock_unified.side_effect = ImportError("Unified agent not available")
                
                with patch('agents.valor.handlers.handle_telegram_message') as mock_valor:
                    mock_valor.return_value = "Fallback analysis complete"
                    
                    await handler.handle_message(client, message)
                    
                    # Verify fallback was used
                    assert mock_valor.called
                    call_args = mock_valor.call_args
                    assert "Please analyze this image" in call_args[1]['message']
                    assert temp_image in call_args[1]['message']
                    
                    print("✅ Image analysis fallback working")
                    
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)


class TestImageResponseGeneration:
    """Test image response generation and sending."""
    
    async def test_text_response_processing(self):
        """Test processing of text responses from image analysis."""
        print("🔍 Testing text response processing...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_image
            )
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "This is a detailed analysis of the uploaded image."
                
                await handler.handle_message(client, message)
                
                # Verify text response was sent
                assert len(message.replies) > 0
                assert "detailed analysis" in message.replies[0]
                
                # Verify message was stored in history
                user_messages = [m for m in chat_history.messages if m['role'] == 'user']
                assistant_messages = [m for m in chat_history.messages if m['role'] == 'assistant']
                
                assert len(user_messages) > 0
                assert "[Photo]" in user_messages[-1]['content']
                assert len(assistant_messages) > 0
                assert "detailed analysis" in assistant_messages[-1]['content']
                
                print("✅ Text response processing working")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_image_generation_response(self):
        """Test processing of image generation responses."""
        print("🔍 Testing image generation response...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_input_image = create_test_image()
        temp_generated_image = create_test_image('.png')
        
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_input_image
            )
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                # Mock image generation response
                mock_process.return_value = f"TELEGRAM_IMAGE_GENERATED|{temp_generated_image}|Generated based on your image"
                
                await handler.handle_message(client, message)
                
                # Verify image was sent
                assert len(client.sent_photos) > 0
                photo_info = client.sent_photos[0]
                assert photo_info['chat_id'] == 12345
                assert photo_info['photo'] == temp_generated_image
                assert "Generated based on your image" in photo_info['caption']
                
                print("✅ Image generation response working")
                
        finally:
            for path in [temp_input_image, temp_generated_image]:
                if Path(path).exists():
                    os.unlink(path)


class TestImageErrorHandling:
    """Test error handling in image processing."""
    
    async def test_image_download_failure(self):
        """Test handling of image download failures."""
        print("🔍 Testing image download failure...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        message = MockMessage(
            chat_id=12345,
            has_photo=True,
            caption="Download this"
        )
        
        # Mock download failure
        async def failing_download(in_memory=False):
            raise Exception("Download failed")
        
        message.download = failing_download
        
        await handler.handle_message(client, message)
        
        # Should send error message
        assert len(message.replies) > 0
        assert "Error processing image" in message.replies[0]
        
        print("✅ Image download failure handled correctly")
    
    async def test_analysis_api_failure(self):
        """Test handling of analysis API failures."""
        print("🔍 Testing analysis API failure...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_image
            )
            
            # Mock API failure
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.side_effect = Exception("API connection failed")
                
                await handler.handle_message(client, message)
                
                # Should send error message
                assert len(message.replies) > 0
                assert "Error processing image" in message.replies[0]
                
                print("✅ Analysis API failure handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_empty_analysis_response(self):
        """Test handling of empty analysis responses."""
        print("🔍 Testing empty analysis response...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_image
            )
            
            # Mock empty response
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = ""  # Empty response
                
                await handler.handle_message(client, message)
                
                # Should send fallback message
                assert len(message.replies) > 0
                response = message.replies[0]
                assert ("didn't have a response" in response or 
                       "processed your message" in response)
                
                print("✅ Empty analysis response handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_whitespace_only_response(self):
        """Test handling of whitespace-only responses."""
        print("🔍 Testing whitespace-only response...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_image
            )
            
            # Mock whitespace-only response
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "   \n\t  "  # Whitespace only
                
                await handler.handle_message(client, message)
                
                # Should send fallback message
                assert len(message.replies) > 0
                response = message.replies[0]
                assert ("didn't have a response" in response or 
                       "processed your message" in response)
                
                print("✅ Whitespace-only response handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_missing_generated_image_file(self):
        """Test handling when generated image file is missing."""
        print("🔍 Testing missing generated image file...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                file_path=temp_image
            )
            
            # Mock response with non-existent image path
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "TELEGRAM_IMAGE_GENERATED|/nonexistent/image.png|Generated image"
                
                await handler.handle_message(client, message)
                
                # Should send error message instead of image
                assert len(message.replies) > 0
                assert "Image was generated but file not found" in message.replies[0]
                assert len(client.sent_photos) == 0  # No image should be sent
                
                print("✅ Missing generated image file handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)


class TestImageEdgeCases:
    """Test edge cases in image handling."""
    
    async def test_very_long_caption(self):
        """Test handling of very long image captions."""
        print("🔍 Testing very long caption...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            # Create very long caption
            long_caption = "a" * 5000
            
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption=long_caption,
                file_path=temp_image
            )
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "Analysis of image with long caption"
                
                await handler.handle_message(client, message)
                
                # Should process successfully
                assert mock_process.called
                call_args = mock_process.call_args
                assert call_args[1]['caption'] == long_caption
                
                print("✅ Long caption handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_image_with_special_characters_in_caption(self):
        """Test handling of special characters in captions."""
        print("🔍 Testing special characters in caption...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_image = create_test_image()
        try:
            special_caption = "🎨 Analysis with émojis and spëcial chars! @mention #hashtag"
            
            message = MockMessage(
                chat_id=12345,
                has_photo=True,
                caption=special_caption,
                file_path=temp_image
            )
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.return_value = "Analysis complete with special characters"
                
                await handler.handle_message(client, message)
                
                # Should process special characters correctly
                assert mock_process.called
                call_args = mock_process.call_args
                assert call_args[1]['caption'] == special_caption
                
                print("✅ Special characters in caption handled correctly")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_concurrent_image_processing(self):
        """Test handling of multiple concurrent image messages."""
        print("🔍 Testing concurrent image processing...")
        
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        handler = MessageHandler(client, chat_history)
        
        temp_images = [create_test_image(f'_{i}.png') for i in range(3)]
        
        try:
            messages = [
                MockMessage(
                    chat_id=12345 + i,
                    has_photo=True,
                    caption=f"Image {i}",
                    file_path=temp_images[i]
                )
                for i in range(3)
            ]
            
            with patch('agents.unified_integration.process_image_unified') as mock_process:
                mock_process.side_effect = [
                    f"Analysis result {i}" for i in range(3)
                ]
                
                # Process messages concurrently
                tasks = [
                    handler.handle_message(client, msg) for msg in messages
                ]
                await asyncio.gather(*tasks)
                
                # All should process successfully
                assert mock_process.call_count == 3
                for i, message in enumerate(messages):
                    assert len(message.replies) > 0
                    assert f"Analysis result {i}" in message.replies[0]
                
                print("✅ Concurrent image processing working")
                
        finally:
            for temp_image in temp_images:
                if Path(temp_image).exists():
                    os.unlink(temp_image)


class ComprehensiveImageFlowTester:
    """Main test runner for comprehensive image flow testing."""
    
    async def run_all_tests(self):
        """Run all comprehensive image flow tests."""
        print("🚀 Comprehensive Image Flow Test Suite")
        print("=" * 60)
        print("Testing complete end-to-end image message handling")
        print("=" * 60)
        
        test_classes = [
            TestImageReceiptFlow(),
            TestImageProcessingPipeline(),
            TestImageResponseGeneration(),
            TestImageErrorHandling(),
            TestImageEdgeCases()
        ]
        
        total_tests = 0
        passed_tests = 0
        
        for test_class in test_classes:
            class_name = test_class.__class__.__name__
            print(f"\n📋 Running {class_name}...")
            
            # Get all test methods
            test_methods = [
                method for method in dir(test_class)
                if method.startswith('test_') and callable(getattr(test_class, method))
            ]
            
            for method_name in test_methods:
                total_tests += 1
                try:
                    method = getattr(test_class, method_name)
                    await method()
                    passed_tests += 1
                    print(f"  ✅ {method_name}")
                except Exception as e:
                    print(f"  ❌ {method_name}: {e}")
                    import traceback
                    traceback.print_exc()
        
        print(f"\n{'=' * 60}")
        print(f"🏁 Comprehensive Image Flow Tests Complete")
        print(f"📊 Results: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            print("🎉 All image flow tests passed!")
            print("✅ Image receipt and detection")
            print("✅ Image processing pipeline") 
            print("✅ Response generation and sending")
            print("✅ Error handling cases")
            print("✅ Edge case handling")
        else:
            print(f"⚠️ {total_tests - passed_tests} tests failed")
            print("Check the output above for specific failure details")
        
        return passed_tests == total_tests


async def main():
    """Run comprehensive image flow tests."""
    tester = ComprehensiveImageFlowTester()
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)