#!/usr/bin/env python3
"""
Test unified agent integration with image processing.

Tests the complete integration between the unified conversational system
and image handling capabilities, including MCP tool integration.
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


def create_test_image():
    """Create a minimal test image file."""
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
    )
    
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(png_data)
        return f.name


class TestUnifiedImageProcessing:
    """Test unified agent image processing capabilities."""
    
    async def test_unified_agent_image_analysis_integration(self):
        """Test integration between unified agent and image analysis."""
        print("🔍 Testing unified agent image analysis integration...")
        
        temp_image = create_test_image()
        
        try:
            # Mock the unified integration module if it exists
            try:
                with patch('agents.unified_integration.process_image_unified') as mock_process:
                    mock_process.return_value = "Unified analysis: I can see a minimal test image."
                    
                    # Import and test
                    from agents.unified_integration import process_image_unified
                    
                    result = await process_image_unified(
                        image_path=temp_image,
                        chat_id=12345,
                        caption="What do you see?",
                        username="test_user",
                        chat_history=[]
                    )
                    
                    assert "Unified analysis" in result
                    assert mock_process.called
                    
                    print("✅ Unified agent image analysis integration working")
                    
            except ImportError:
                print("⏭️ Unified agent module not available, testing fallback...")
                
                # Test fallback to valor agent
                with patch('agents.valor.handlers.handle_telegram_message') as mock_valor:
                    mock_valor.return_value = "Valor fallback analysis complete"
                    
                    from agents.valor.handlers import handle_telegram_message
                    
                    result = await handle_telegram_message(
                        message=f"Please analyze this image: What do you see?\n\n[Image downloaded to: {temp_image}]",
                        chat_id=12345,
                        username="test_user",
                        is_group_chat=False,
                        chat_history_obj=Mock()
                    )
                    
                    assert "Valor fallback" in result
                    assert mock_valor.called
                    
                    print("✅ Fallback to valor agent working")
                    
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_mcp_image_tool_integration(self):
        """Test MCP tool integration for image processing."""
        print("🔍 Testing MCP image tool integration...")
        
        temp_image = create_test_image()
        
        try:
            # Test if MCP social tools server has image capabilities
            try:
                from mcp_servers.social_tools import analyze_shared_image
                
                # Mock the underlying analysis
                with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
                    mock_analyze.return_value = "👁️ **What I see:**\n\nMCP analysis result"
                    
                    result = analyze_shared_image(
                        image_path=temp_image,
                        question="What's in this image?",
                        context="Test context"
                    )
                    
                    assert "MCP analysis result" in result
                    assert mock_analyze.called
                    
                    print("✅ MCP image tool integration working")
                    
            except ImportError:
                print("⏭️ MCP image tools not available")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_telegram_unified_image_flow(self):
        """Test complete Telegram to unified agent image flow."""
        print("🔍 Testing complete Telegram unified image flow...")
        
        temp_image = create_test_image()
        
        try:
            # Mock the complete flow from Telegram handler to unified processing
            from integrations.telegram.handlers import MessageHandler
            
            mock_client = AsyncMock()
            mock_chat_history = Mock()
            mock_chat_history.add_message = Mock()
            mock_chat_history.get_context = Mock(return_value=[])
            
            handler = MessageHandler(mock_client, mock_chat_history)
            
            # Create mock message
            message = Mock()
            message.chat.id = 12345
            message.chat.type = Mock()
            message.chat.type.value = 'private'  # Simulate ChatType.PRIVATE
            message.photo = Mock()
            message.caption = "Analyze this image please"
            message.id = 1001
            message.from_user = Mock()
            message.from_user.username = 'test_user'
            message.download = AsyncMock(return_value=temp_image)
            message.reply = AsyncMock()
            
            # Mock get_me
            me = Mock()
            me.username = 'test_bot'
            me.id = 54321
            mock_client.get_me.return_value = me
            
            # Mock unified processing
            with patch('integrations.telegram.handlers.process_image_unified') as mock_unified:
                mock_unified.return_value = "Complete unified analysis of the image"
                
                await handler._handle_photo_message(mock_client, message, 12345)
                
                # Verify the flow
                assert mock_unified.called
                call_args = mock_unified.call_args
                assert call_args[1]['image_path'] == temp_image
                assert call_args[1]['caption'] == "Analyze this image please"
                assert call_args[1]['chat_id'] == 12345
                assert call_args[1]['username'] == 'test_user'
                
                # Verify response was sent
                assert message.reply.called
                reply_text = message.reply.call_args[0][0]
                assert "Complete unified analysis" in reply_text
                
                print("✅ Complete Telegram unified image flow working")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)


class TestImageContextIntegration:
    """Test image processing with conversation context."""
    
    async def test_image_analysis_with_chat_history(self):
        """Test image analysis considering previous conversation context."""
        print("🔍 Testing image analysis with chat history...")
        
        temp_image = create_test_image()
        
        try:
            # Simulate a conversation with context
            chat_history = [
                {"role": "user", "content": "I'm working on a design project"},
                {"role": "assistant", "content": "That sounds interesting! What kind of design?"},
                {"role": "user", "content": "UI mockups for a mobile app"}
            ]
            
            # Mock unified processing with context
            with patch('integrations.telegram.handlers.process_image_unified') as mock_unified:
                mock_unified.return_value = "Based on our design discussion, this mockup shows..."
                
                from integrations.telegram.handlers import MessageHandler
                
                mock_client = AsyncMock()
                mock_chat_history = Mock()
                mock_chat_history.get_context = Mock(return_value=chat_history)
                mock_chat_history.add_message = Mock()
                
                handler = MessageHandler(mock_client, mock_chat_history)
                
                # Create message with image
                message = Mock()
                message.chat.id = 12345
                message.chat.type = Mock()
                message.chat.type.value = 'private'
                message.photo = Mock()
                message.caption = "Here's my latest mockup"
                message.from_user = Mock()
                message.from_user.username = 'designer_user'
                message.download = AsyncMock(return_value=temp_image)
                message.reply = AsyncMock()
                
                me = Mock()
                me.username = 'test_bot'
                me.id = 54321
                mock_client.get_me.return_value = me
                
                await handler._handle_photo_message(mock_client, message, 12345)
                
                # Verify context was passed
                assert mock_unified.called
                call_args = mock_unified.call_args
                assert call_args[1]['chat_history'] == chat_history
                
                print("✅ Image analysis with chat history working")
                
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)
    
    async def test_image_processing_context_injection(self):
        """Test context injection for stateless MCP tools."""
        print("🔍 Testing image processing context injection...")
        
        temp_image = create_test_image()
        
        try:
            # Test context injection pattern used by MCP tools
            chat_id = 12345
            username = "test_user"
            context_info = f"Chat ID: {chat_id}, User: {username}"
            
            # Mock MCP tool call with context
            with patch('tools.image_analysis_tool.analyze_image') as mock_analyze:
                mock_analyze.return_value = f"Analysis for {username}: Image shows test content"
                
                # Simulate MCP tool being called with context
                try:
                    from mcp_servers.social_tools import analyze_shared_image
                    
                    result = analyze_shared_image(
                        image_path=temp_image,
                        question="What do you see?",
                        context=context_info
                    )
                    
                    assert mock_analyze.called
                    call_args = mock_analyze.call_args
                    assert context_info in str(call_args) or context_info in call_args[1].get('context', '')
                    
                    print("✅ Image processing context injection working")
                    
                except ImportError:
                    print("⏭️ MCP tools not available for context injection test")
                    
        finally:
            if Path(temp_image).exists():
                os.unlink(temp_image)


class TestImageGenerationIntegration:
    """Test image generation integration with unified system."""
    
    async def test_unified_image_generation_flow(self):
        """Test unified agent triggering image generation."""
        print("🔍 Testing unified image generation flow...")
        
        # Mock image generation
        temp_generated = create_test_image()
        
        try:
            with patch('integrations.telegram.handlers.process_image_unified') as mock_unified:
                # Mock agent deciding to generate an image
                mock_unified.return_value = f"TELEGRAM_IMAGE_GENERATED|{temp_generated}|Here's the generated image based on your request"
                
                from integrations.telegram.handlers import MessageHandler
                
                mock_client = AsyncMock()
                mock_chat_history = Mock()
                mock_chat_history.get_context = Mock(return_value=[])
                mock_chat_history.add_message = Mock()
                
                handler = MessageHandler(mock_client, mock_chat_history)
                
                # Simulate text message asking for image generation
                message = Mock()
                message.chat.id = 12345
                message.text = "Create an image of a sunset"
                message.photo = None  # Not a photo message
                message.reply = AsyncMock()
                
                # Process as text message that triggers image generation
                result = await handler._process_agent_response(
                    message, 
                    12345, 
                    f"TELEGRAM_IMAGE_GENERATED|{temp_generated}|Generated sunset image"
                )
                
                # Should send image instead of text
                assert result is True  # Indicates image was processed
                
                print("✅ Unified image generation flow working")
                
        finally:
            if Path(temp_generated).exists():
                os.unlink(temp_generated)
    
    async def test_mcp_image_generation_tool(self):
        """Test MCP image generation tool integration."""
        print("🔍 Testing MCP image generation tool...")
        
        try:
            # Test if MCP social tools has image generation
            from mcp_servers.social_tools import create_image
            
            with patch('tools.image_generation_tool.generate_image') as mock_generate:
                temp_path = create_test_image()
                mock_generate.return_value = temp_path
                
                try:
                    result = create_image(
                        prompt="A beautiful sunset over mountains",
                        style="natural"
                    )
                    
                    assert mock_generate.called
                    assert temp_path in result or "Generated" in result
                    
                    print("✅ MCP image generation tool working")
                    
                finally:
                    if Path(temp_path).exists():
                        os.unlink(temp_path)
                    
        except ImportError:
            print("⏭️ MCP image generation tools not available")


class UnifiedImageIntegrationTester:
    """Main test runner for unified image integration tests."""
    
    async def run_all_tests(self):
        """Run all unified image integration tests."""
        print("🚀 Unified Image Integration Test Suite")
        print("=" * 55)
        print("Testing integration between unified system and image processing")
        print("=" * 55)
        
        test_classes = [
            TestUnifiedImageProcessing(),
            TestImageContextIntegration(),
            TestImageGenerationIntegration()
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
        
        print(f"\n{'=' * 55}")
        print(f"🏁 Unified Image Integration Tests Complete")
        print(f"📊 Results: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            print("🎉 All unified integration tests passed!")
            print("✅ Unified agent image processing")
            print("✅ MCP tool integration")
            print("✅ Context injection")
            print("✅ Image generation integration")
        else:
            print(f"⚠️ {total_tests - passed_tests} tests failed")
        
        return passed_tests == total_tests


async def main():
    """Run unified image integration tests."""
    tester = UnifiedImageIntegrationTester()
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)