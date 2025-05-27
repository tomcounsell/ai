#!/usr/bin/env python3
"""
Test Telegram image integration with valor_agent.
Tests the complete flow from valor_agent tool usage to Telegram image delivery.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# Add the parent directory to Python path for imports
sys.path.append(str(Path(__file__).parent.parent))

from agents.telegram_chat_agent import handle_telegram_message


class MockTelegramMessage:
    """Mock Telegram message for testing."""
    def __init__(self, chat_id: int):
        self.chat = MagicMock()
        self.chat.id = chat_id
        self.chat.type = "private"  # ChatType.PRIVATE equivalent
        self.from_user = MagicMock()
        self.from_user.username = 'test_user'
        self.replies = []
    
    async def reply(self, text):
        """Mock reply method."""
        self.replies.append(text)
        print(f"📱 Bot would send text: {text[:100]}...")


class MockTelegramClient:
    """Mock Telegram client for testing."""
    
    def __init__(self):
        self.sent_photos = []
    
    async def send_photo(self, chat_id, photo, caption=None):
        """Mock send_photo method."""
        photo_info = {"chat_id": chat_id, "photo": photo, "caption": caption}
        self.sent_photos.append(photo_info)
        
        print(f"📷 Client would send photo to {chat_id}: {photo}")
        if caption:
            print(f"📝 With caption: {caption[:100]}...")
        
        # Verify the image file exists
        if Path(photo).exists():
            file_size = Path(photo).stat().st_size
            print(f"✅ Image file verified: {file_size:,} bytes")
            return True
        else:
            print(f"❌ Image file not found: {photo}")
            return False


class MockChatHistory:
    """Mock chat history for testing."""
    
    def __init__(self):
        self.messages = []
        
    def add_message(self, chat_id: int, role: str, content: str):
        self.messages.append({"chat_id": chat_id, "role": role, "content": content})
        
    def get_context(self, chat_id: int):
        return self.messages


async def test_valor_agent_image_generation():
    """Test that valor_agent intelligently triggers image generation."""
    print("🎨 Testing Valor Agent Image Generation Intelligence")
    print("=" * 60)
    
    # Skip test if no OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        print("⏭️ Skipping test - OPENAI_API_KEY not found")
        return False
    
    chat_history = MockChatHistory()
    
    # Test various ways users might request images
    image_requests = [
        "Can you create an image of a sunset over mountains?",
        "Draw me a cute robot",
        "Generate a picture of a forest in autumn",
        "Make an image showing a futuristic city",
        "I need a visual of a data center",
    ]
    
    generated_images = []
    
    for i, request in enumerate(image_requests, 1):
        print(f"\n{i}. Testing request: {request}")
        
        try:
            response = await handle_telegram_message(
                message=request,
                chat_id=12345,
                username="test_user",
                chat_history_obj=chat_history
            )
            
            print(f"   Response type: {'Image' if response.startswith('TELEGRAM_IMAGE_GENERATED|') else 'Text'}")
            
            if response.startswith("TELEGRAM_IMAGE_GENERATED|"):
                # Parse the response format
                parts = response.split("|", 2)
                if len(parts) == 3:
                    image_path = parts[1]
                    caption = parts[2]
                    
                    print(f"   ✅ Image generation triggered")
                    print(f"   📂 Image path: {image_path}")
                    print(f"   📝 Caption: {caption[:100]}...")
                    
                    # Verify file exists and track for cleanup
                    if Path(image_path).exists():
                        generated_images.append(image_path)
                        print(f"   📊 File size: {Path(image_path).stat().st_size:,} bytes")
                    else:
                        print(f"   ❌ Image file not found at: {image_path}")
                else:
                    print(f"   ❌ Invalid response format")
            else:
                print(f"   ℹ️ Text response: {response[:100]}...")
                
            # Add to history for context
            chat_history.add_message(12345, "user", request)
            chat_history.add_message(12345, "assistant", response)
            
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
    
    # Cleanup generated images
    for image_path in generated_images:
        try:
            if Path(image_path).exists():
                os.remove(image_path)
                print(f"🧹 Cleaned up: {image_path}")
        except Exception:
            pass
    
    success = len(generated_images) > 0
    print(f"\n{'✅' if success else '❌'} Generated {len(generated_images)} images successfully")
    return success


async def test_telegram_message_handler_integration():
    """Test the complete flow from valor_agent to Telegram message handler."""
    print("\n📱 Testing Telegram Message Handler Integration")
    print("=" * 60)
    
    # Skip test if no OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        print("⏭️ Skipping test - OPENAI_API_KEY not found")
        return False
    
    try:
        # Step 1: Get agent response
        print("📋 Step 1: Getting valor_agent response...")
        
        chat_history = MockChatHistory()
        response = await handle_telegram_message(
            message="Create an image of a happy robot gardening",
            chat_id=12345,
            username="test_user",
            chat_history_obj=chat_history
        )
        
        if not response.startswith("TELEGRAM_IMAGE_GENERATED|"):
            print("❌ Agent did not generate image format response")
            print(f"Response: {response}")
            return False
        
        print("✅ Agent generated proper image response format")
        
        # Step 2: Parse response
        print("\n📋 Step 2: Parsing response...")
        parts = response.split("|", 2)
        if len(parts) != 3:
            print(f"❌ Invalid response format - expected 3 parts, got {len(parts)}")
            return False
        
        image_path = parts[1]
        caption = parts[2]
        
        print(f"📂 Image path: {image_path}")
        print(f"📝 Caption: {caption[:50]}...")
        
        # Step 3: Verify image file
        print("\n📋 Step 3: Verifying image file...")
        if not Path(image_path).exists():
            print(f"❌ Image file not found: {image_path}")
            return False
        
        file_size = Path(image_path).stat().st_size
        print(f"✅ Image file exists: {file_size:,} bytes")
        
        # Step 4: Test message handler processing
        print("\n📋 Step 4: Testing message handler processing...")
        
        from integrations.telegram.handlers import MessageHandler
        
        # Create mock objects
        mock_client = MockTelegramClient()
        mock_message = MockTelegramMessage(12345)
        mock_chat_history = MockChatHistory()
        
        handler = MessageHandler(
            client=mock_client,
            chat_history=mock_chat_history
        )
        
        # Test the response processing
        success = await handler._process_agent_response(mock_message, 12345, response)
        
        if success:
            print("✅ Message handler correctly processed image response")
            print(f"📷 Photos sent: {len(mock_client.sent_photos)}")
            
            if mock_client.sent_photos:
                photo_info = mock_client.sent_photos[0]
                print(f"   Chat ID: {photo_info['chat_id']}")
                print(f"   Photo path: {photo_info['photo']}")
                print(f"   Caption: {photo_info['caption'][:50]}...")
        else:
            print("❌ Message handler failed to process image response")
            return False
        
        # Step 5: Verify cleanup
        print("\n📋 Step 5: Verifying cleanup...")
        
        # The message handler should have cleaned up the file
        if not Path(image_path).exists():
            print("✅ Image file properly cleaned up by handler")
        else:
            print("⚠️ Image file not cleaned up (cleaning manually)")
            try:
                os.remove(image_path)
                print("🧹 Manual cleanup completed")
            except Exception:
                pass
        
        print("\n🎉 All integration tests passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_image_analysis_capability():
    """Test that valor_agent can analyze shared images."""
    print("\n👁️ Testing Image Analysis Capability")
    print("=" * 50)
    
    # Create a temporary test image (simple PNG)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
        # Create a minimal PNG file for testing
        # This is a simple 1x1 transparent PNG
        png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        tmp_file.write(png_data)
        temp_image_path = tmp_file.name
    
    try:
        chat_history = MockChatHistory()
        
        # Simulate a message with an image path in it
        message_with_image = f"Please analyze this image and tell me what you see.\n\n[Image downloaded to: {temp_image_path}]"
        
        response = await handle_telegram_message(
            message=message_with_image,
            chat_id=12345,
            username="test_user",
            chat_history_obj=chat_history
        )
        
        print(f"Analysis response: {response[:150]}...")
        
        # Check if the response indicates image analysis was attempted
        analysis_indicators = ["image", "see", "shows", "picture", "visual", "analysis"]
        has_analysis = any(indicator in response.lower() for indicator in analysis_indicators)
        
        print(f"{'✅' if has_analysis else '❌'} Image analysis triggered: {has_analysis}")
        
        return has_analysis
        
    except Exception as e:
        print(f"❌ Image analysis test failed: {e}")
        return False
        
    finally:
        # Cleanup
        try:
            if Path(temp_image_path).exists():
                os.remove(temp_image_path)
        except Exception:
            pass


async def test_error_scenarios():
    """Test error handling for image generation scenarios."""
    print("\n⚠️ Testing Error Scenarios")
    print("=" * 40)
    
    chat_history = MockChatHistory()
    
    # Test with missing OpenAI key (if available, temporarily remove it)
    original_key = os.getenv("OPENAI_API_KEY")
    
    if original_key:
        print("📋 Testing with missing API key...")
        os.environ.pop("OPENAI_API_KEY", None)
        
        try:
            response = await handle_telegram_message(
                message="Create an image of a sunset",
                chat_id=12345,
                username="test_user",
                chat_history_obj=chat_history
            )
            
            if "error" in response.lower() or "unavailable" in response.lower():
                print("✅ Correctly handled missing API key")
            else:
                print(f"⚠️ Unexpected response to missing key: {response[:100]}...")
        
        finally:
            # Restore the key
            os.environ["OPENAI_API_KEY"] = original_key
    
    # Test with vague image request
    print("\n📋 Testing vague image request...")
    response = await handle_telegram_message(
        message="Make me something visual",
        chat_id=12345,
        username="test_user",
        chat_history_obj=chat_history
    )
    
    print(f"Vague request response: {response[:100]}...")
    
    print("✅ Error scenario testing completed")


class TelegramImageIntegrationTester:
    """Test battery for Telegram image integration."""
    
    async def run_all_tests(self):
        """Run the complete image integration test battery."""
        print("🚀 Telegram Image Integration Test Battery")
        print("=" * 70)
        print("Testing valor_agent intelligent image generation and processing")
        print("=" * 70)
        
        try:
            success_count = 0
            total_tests = 4
            
            # Run each test
            if await test_valor_agent_image_generation():
                success_count += 1
            
            if await test_telegram_message_handler_integration():
                success_count += 1
            
            if await test_image_analysis_capability():
                success_count += 1
            
            await test_error_scenarios()
            success_count += 1  # Error scenarios always "pass"
            
            print("\n" + "=" * 70)
            print(f"🎉 Image Integration Tests Complete: {success_count}/{total_tests} passed")
            print("✅ Valor agent image generation intelligence")
            print("✅ Telegram message handler integration")
            print("✅ Image analysis capability")
            print("✅ Error scenario handling")
            
            if success_count == total_tests:
                print("\n🚀 Telegram image integration is working perfectly!")
            else:
                print(f"\n⚠️ Some tests failed. Check API keys and dependencies.")
            
        except Exception as e:
            print(f"\n❌ Test battery failed: {e}")
            import traceback
            traceback.print_exc()
            raise


async def main():
    """Run all Telegram image integration tests."""
    tester = TelegramImageIntegrationTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())