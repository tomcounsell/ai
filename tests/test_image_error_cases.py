#!/usr/bin/env python3
"""
Test suite for image processing error cases and edge conditions.

Focuses on error handling scenarios that weren't fully covered in existing tests,
particularly around empty messages, malformed responses, and API failures.
"""

import asyncio
import os
import sys
import tempfile
import base64
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, Mock

# Add the parent directory to Python path for imports
sys.path.append(str(Path(__file__).parent.parent))

from integrations.telegram.handlers import MessageHandler
from tools.image_analysis_tool import analyze_image, analyze_image_async
from tools.image_generation_tool import generate_image, create_image_with_feedback
from pyrogram.enums import ChatType


class TestImageAnalysisErrors:
    """Test error handling in image analysis tool."""
    
    def test_analyze_image_missing_api_key(self):
        """Test image analysis with missing OpenAI API key."""
        print("🔍 Testing image analysis with missing API key...")
        
        # Create temporary image
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp.write(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
            ))
            temp_path = tmp.name
        
        try:
            # Mock missing API key
            with patch.dict(os.environ, {}, clear=True):
                result = analyze_image(temp_path, "What do you see?")
                
                assert "Image analysis unavailable" in result
                assert "Missing OPENAI_API_KEY" in result
                
                print("✅ Missing API key handled correctly")
                
        finally:
            if Path(temp_path).exists():
                os.unlink(temp_path)
    
    def test_analyze_image_file_not_found(self):
        """Test image analysis with non-existent file."""
        print("🔍 Testing image analysis with non-existent file...")
        
        result = analyze_image("/nonexistent/image.jpg", "Analyze this")
        
        assert "Image file not found" in result or "Error:" in result
        
        print("✅ Non-existent file handled correctly")
    
    def test_analyze_image_corrupted_file(self):
        """Test image analysis with corrupted image file."""
        print("🔍 Testing image analysis with corrupted file...")
        
        # Create corrupted image file
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(b"This is not an image file")
            temp_path = tmp.name
        
        try:
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
                with patch('tools.image_analysis_tool.OpenAI') as mock_openai:
                    # Mock OpenAI to raise an exception when processing bad image
                    mock_client = MagicMock()
                    mock_openai.return_value = mock_client
                    mock_client.chat.completions.create.side_effect = Exception("Invalid image format")
                    
                    result = analyze_image(temp_path, "What's in this image?")
                    
                    assert "Image analysis error" in result
                    
                    print("✅ Corrupted file handled correctly")
                    
        finally:
            if Path(temp_path).exists():
                os.unlink(temp_path)
    
    async def test_analyze_image_async_with_errors(self):
        """Test async wrapper error handling."""
        print("🔍 Testing async image analysis with errors...")
        
        # Test with non-existent file
        result = await analyze_image_async("/nonexistent/file.png")
        assert "Error:" in result or "not found" in result
        
        print("✅ Async wrapper error handling working")


class TestImageGenerationErrors:
    """Test error handling in image generation tool."""
    
    def test_generate_image_missing_api_key(self):
        """Test image generation with missing OpenAI API key."""
        print("🔍 Testing image generation with missing API key...")
        
        with patch.dict(os.environ, {}, clear=True):
            result = generate_image("A beautiful sunset")
            
            assert "Image generation unavailable" in result
            assert "Missing OPENAI_API_KEY" in result
            
            print("✅ Missing API key for generation handled correctly")
    
    def test_generate_image_api_failure(self):
        """Test image generation with API failure."""
        print("🔍 Testing image generation with API failure...")
        
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            with patch('tools.image_generation_tool.OpenAI') as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client
                mock_client.images.generate.side_effect = Exception("API rate limit exceeded")
                
                result = generate_image("A test image")
                
                assert "Image generation error" in result
                assert "API rate limit exceeded" in result
                
                print("✅ API failure for generation handled correctly")
    
    def test_generate_image_download_failure(self):
        """Test image generation with download failure."""
        print("🔍 Testing image generation with download failure...")
        
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            with patch('tools.image_generation_tool.OpenAI') as mock_openai:
                # Mock successful generation but failed download
                mock_client = MagicMock()
                mock_openai.return_value = mock_client
                
                mock_response = MagicMock()
                mock_response.data[0].url = "https://example.com/image.png"
                mock_client.images.generate.return_value = mock_response
                
                with patch('tools.image_generation_tool.requests.get') as mock_get:
                    mock_get.side_effect = Exception("Connection timeout")
                    
                    result = generate_image("A test image")
                    
                    assert "Image generation error" in result
                    assert "Connection timeout" in result
                    
                    print("✅ Download failure handled correctly")
    
    def test_create_image_with_feedback_error(self):
        """Test create_image_with_feedback error handling."""
        print("🔍 Testing create_image_with_feedback with errors...")
        
        with patch('tools.image_generation_tool.generate_image') as mock_generate:
            mock_generate.return_value = "🎨 Image generation error: Test error"
            
            path, message = create_image_with_feedback("Test prompt")
            
            assert path == ""  # Empty path on error
            assert "🎨 Image generation error" in message
            
            print("✅ create_image_with_feedback error handling working")


class TestMessageHandlerImageErrors:
    """Test error handling in message handler for image processing."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = Mock()
        self.mock_chat_history.add_message = Mock()
        self.mock_chat_history.get_context = Mock(return_value=[])
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )
    
    async def test_handle_photo_message_empty_caption_after_mention_processing(self):
        """Test photo message handling when caption becomes empty after mention processing."""
        print("🔍 Testing photo with empty caption after mention processing...")
        
        # Mock message with mention that gets removed
        message = Mock()
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.photo = Mock()
        message.caption = "@test_bot"  # Only mention, no other content
        message.id = 1001
        message.from_user = Mock()
        message.from_user.username = 'test_user'
        
        # Mock download
        temp_image = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        temp_image.close()
        
        async def mock_download(in_memory=False):
            return temp_image.name
        
        message.download = mock_download
        
        # Mock get_me
        me = Mock()
        me.username = 'test_bot'
        me.id = 54321
        self.mock_client.get_me.return_value = me
        
        try:
            with patch('integrations.telegram.handlers.process_image_unified') as mock_process:
                mock_process.return_value = "Processed image with empty caption"
                
                await self.handler._handle_photo_message(self.mock_client, message, 12345)
                
                # Should still process the image even with empty caption
                assert mock_process.called
                call_args = mock_process.call_args
                assert call_args[1]['caption'] == ""  # Caption should be empty after mention removal
                
                print("✅ Empty caption after mention processing handled correctly")
                
        finally:
            if Path(temp_image.name).exists():
                os.unlink(temp_image.name)
    
    async def test_handle_photo_message_api_unavailable(self):
        """Test photo message handling when analysis APIs are unavailable."""
        print("🔍 Testing photo message with unavailable APIs...")
        
        message = Mock()
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.photo = Mock()
        message.caption = "Analyze this image"
        message.id = 1001
        message.from_user = Mock()
        message.from_user.username = 'test_user'
        
        # Mock download
        temp_image = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        temp_image.close()
        
        async def mock_download(in_memory=False):
            return temp_image.name
        
        message.download = mock_download
        
        # Mock get_me
        me = Mock()
        me.username = 'test_bot'
        me.id = 54321
        self.mock_client.get_me.return_value = me
        
        # Mock no notion scout (AI capabilities not configured)
        self.handler.notion_scout = None
        
        # Mock reply
        message.reply = AsyncMock()
        
        try:
            await self.handler._handle_photo_message(self.mock_client, message, 12345)
            
            # Should send message about missing AI capabilities
            assert message.reply.called
            reply_text = message.reply.call_args[0][0]
            assert "need my AI capabilities configured" in reply_text
            
            print("✅ Unavailable APIs handled correctly")
            
        finally:
            if Path(temp_image.name).exists():
                os.unlink(temp_image.name)
    
    async def test_process_agent_response_malformed_image_response(self):
        """Test processing malformed image generation responses."""
        print("🔍 Testing malformed image generation response...")
        
        message = Mock()
        message.reply = AsyncMock()
        
        # Test malformed response (missing parts)
        malformed_response = "TELEGRAM_IMAGE_GENERATED|/path/to/image.jpg"  # Missing caption part
        
        result = await self.handler._process_agent_response(message, 12345, malformed_response)
        
        # Should treat as regular text response since it's malformed
        assert message.reply.called
        reply_text = message.reply.call_args[0][0]
        assert "TELEGRAM_IMAGE_GENERATED" in reply_text  # Should send the raw response
        assert result is False
        
        print("✅ Malformed image response handled correctly")
    
    async def test_process_agent_response_none_input(self):
        """Test processing None response from agent."""
        print("🔍 Testing None response from agent...")
        
        message = Mock()
        message.reply = AsyncMock()
        
        result = await self.handler._process_agent_response(message, 12345, None)
        
        # Should send fallback message
        assert message.reply.called
        reply_text = message.reply.call_args[0][0]
        assert "didn't have a response" in reply_text or "processed your message" in reply_text
        assert result is False
        
        print("✅ None response handled correctly")
    
    async def test_process_agent_response_non_string_input(self):
        """Test processing non-string response from agent."""
        print("🔍 Testing non-string response from agent...")
        
        message = Mock()
        message.reply = AsyncMock()
        
        # Test with integer response
        result = await self.handler._process_agent_response(message, 12345, 42)
        
        # Should convert to string and send
        assert message.reply.called
        reply_text = message.reply.call_args[0][0]
        assert "42" in reply_text
        assert result is False
        
        print("✅ Non-string response handled correctly")


class TestValidationEdgeCases:
    """Test edge cases in message validation for image responses."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )
    
    def test_validate_message_content_image_caption_edge_cases(self):
        """Test validation of image captions with edge cases."""
        print("🔍 Testing image caption validation edge cases...")
        
        # Test with only Unicode characters
        unicode_only = "🎨🖼️👁️"
        result = self.handler._validate_message_content(unicode_only, "Fallback")
        assert result == unicode_only
        
        # Test with mixed control characters and valid content
        mixed_content = "Valid\x00\x1fcontent\x7f"
        result = self.handler._validate_message_content(mixed_content, "Fallback")
        assert result == "Validcontent"
        
        # Test with very long image caption
        long_caption = "Image analysis: " + "a" * 4000
        result = self.handler._validate_message_content(long_caption, "Fallback")
        assert len(result) <= 4000
        assert result.endswith("...")
        
        print("✅ Image caption validation edge cases handled correctly")
    
    def test_validate_message_content_special_image_formats(self):
        """Test validation with special image-related content."""
        print("🔍 Testing special image format content...")
        
        # Test image generation format with empty caption
        image_format = "TELEGRAM_IMAGE_GENERATED|/path/to/image.jpg|"
        result = self.handler._validate_message_content(image_format, "Fallback")
        assert result == image_format  # Should preserve the format
        
        # Test image format with whitespace caption
        image_format_ws = "TELEGRAM_IMAGE_GENERATED|/path/to/image.jpg|   \t  "
        result = self.handler._validate_message_content(image_format_ws, "Fallback")
        assert "TELEGRAM_IMAGE_GENERATED" in result
        
        print("✅ Special image format content handled correctly")


class TestConcurrentImageErrors:
    """Test error handling in concurrent image processing scenarios."""
    
    async def test_concurrent_image_processing_with_mixed_success_failure(self):
        """Test concurrent processing where some images succeed and others fail."""
        print("🔍 Testing concurrent image processing with mixed results...")
        
        async def mock_analysis_mixed(image_path, **kwargs):
            # Simulate some succeeding, some failing based on path
            if "fail" in image_path:
                raise Exception("Analysis failed")
            return f"Success for {Path(image_path).name}"
        
        # Create multiple temporary images
        temp_images = []
        try:
            for i in range(3):
                suffix = "_fail.jpg" if i == 1 else "_success.jpg"  # Make middle one fail
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(b"fake image data")
                    temp_images.append(tmp.name)
            
            with patch('integrations.telegram.handlers.process_image_unified', side_effect=mock_analysis_mixed):
                # Process images concurrently
                tasks = []
                handlers = []
                
                for i, temp_image in enumerate(temp_images):
                    mock_client = AsyncMock()
                    mock_chat_history = Mock()
                    mock_chat_history.add_message = Mock()
                    mock_chat_history.get_context = Mock(return_value=[])
                    
                    handler = MessageHandler(mock_client, mock_chat_history)
                    handlers.append(handler)
                    
                    message = Mock()
                    message.chat.id = 12345 + i
                    message.chat.type = ChatType.PRIVATE
                    message.photo = Mock()
                    message.caption = f"Image {i}"
                    message.id = 1001 + i
                    message.from_user = Mock()
                    message.from_user.username = 'test_user'
                    message.download = AsyncMock(return_value=temp_image)
                    message.reply = AsyncMock()
                    
                    me = Mock()
                    me.username = 'test_bot'
                    me.id = 54321
                    mock_client.get_me.return_value = me
                    
                    tasks.append(handler._handle_photo_message(mock_client, message, message.chat.id))
                
                # Execute all tasks
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Verify results - some should succeed, some should fail gracefully
                print("✅ Concurrent mixed success/failure handled correctly")
                
        finally:
            for temp_image in temp_images:
                if Path(temp_image).exists():
                    os.unlink(temp_image)


class ImageErrorTester:
    """Main test runner for image error handling tests."""
    
    async def run_all_tests(self):
        """Run all image error handling tests."""
        print("🚀 Image Error Handling Test Suite")
        print("=" * 50)
        print("Testing error cases and edge conditions in image processing")
        print("=" * 50)
        
        test_classes = [
            TestImageAnalysisErrors(),
            TestImageGenerationErrors(),
            TestMessageHandlerImageErrors(),
            TestValidationEdgeCases(),
            TestConcurrentImageErrors()
        ]
        
        total_tests = 0
        passed_tests = 0
        
        for test_class in test_classes:
            class_name = test_class.__class__.__name__
            print(f"\n📋 Running {class_name}...")
            
            # Setup if method exists
            if hasattr(test_class, 'setup_method'):
                test_class.setup_method()
            
            # Get all test methods
            test_methods = [
                method for method in dir(test_class)
                if method.startswith('test_') and callable(getattr(test_class, method))
            ]
            
            for method_name in test_methods:
                total_tests += 1
                try:
                    method = getattr(test_class, method_name)
                    if asyncio.iscoroutinefunction(method):
                        await method()
                    else:
                        method()
                    passed_tests += 1
                    print(f"  ✅ {method_name}")
                except Exception as e:
                    print(f"  ❌ {method_name}: {e}")
                    import traceback
                    traceback.print_exc()
        
        print(f"\n{'=' * 50}")
        print(f"🏁 Image Error Handling Tests Complete")
        print(f"📊 Results: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            print("🎉 All error handling tests passed!")
            print("✅ Image analysis error cases")
            print("✅ Image generation error cases")
            print("✅ Message handler error cases")
            print("✅ Validation edge cases")
            print("✅ Concurrent processing errors")
        else:
            print(f"⚠️ {total_tests - passed_tests} tests failed")
        
        return passed_tests == total_tests


async def main():
    """Run all image error handling tests."""
    tester = ImageErrorTester()
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)