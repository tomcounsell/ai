#!/usr/bin/env python3
"""
Test Telegram image generation integration.
Tests the complete flow from user request to Telegram sendPhoto API.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Add the parent directory to Python path for imports
sys.path.append(str(Path(__file__).parent.parent))

from agents.telegram_chat_agent import handle_telegram_message


class MockTelegramMessage:
    """Mock Telegram message for testing."""
    def __init__(self, chat_id: int):
        self.chat = type('Chat', (), {'id': chat_id})()
        self.from_user = type('User', (), {'username': 'test_user'})()
    
    async def reply(self, text):
        """Mock reply method."""
        print(f"📱 Would send text: {text}")
        
    async def send_photo(self, photo, caption=None):
        """Mock send_photo method."""
        print(f"📷 Would send photo: {photo}")
        if caption:
            print(f"📝 With caption: {caption}")


class MockTelegramClient:
    """Mock Telegram client for testing."""
    
    async def send_photo(self, chat_id, photo, caption=None):
        """Mock send_photo method."""
        print(f"📷 Client sending photo to {chat_id}: {photo}")
        if caption:
            print(f"📝 Caption: {caption}")
        
        # Verify the image file exists
        if Path(photo).exists():
            print(f"✅ Image file verified: {Path(photo).stat().st_size:,} bytes")
            return True
        else:
            print(f"❌ Image file not found: {photo}")
            return False


async def test_telegram_image_generation():
    """Test the complete Telegram image generation flow."""
    print("🧪 Testing Telegram Image Generation Integration")
    print("=" * 60)
    
    # Skip test if no OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        print("⏭️ Skipping test - OPENAI_API_KEY not found")
        return False
    
    try:
        # Step 1: Test agent response format
        print("📋 Step 1: Testing agent response format...")
        
        response = await handle_telegram_message(
            message="Can you create an image of a sunset over mountains?",
            chat_id=12345,
            username="test_user"
        )
        
        print(f"Agent response: {response[:100]}...")
        
        if not response.startswith("TELEGRAM_IMAGE_GENERATED|"):
            print("❌ Agent did not return expected format")
            print(f"Expected format: TELEGRAM_IMAGE_GENERATED|path|caption")
            print(f"Actual response: {response}")
            return False
        
        print("✅ Agent returned correct format")
        
        # Step 2: Parse and validate response
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
        
        # Step 4: Test message handler integration
        print("\n📋 Step 4: Testing message handler integration...")
        
        # Import and test the message processing logic
        from integrations.telegram.handlers import MessageHandler
        
        # Create mock objects
        mock_client = MockTelegramClient()
        mock_message = MockTelegramMessage(12345)
        
        # Create a minimal chat history for testing
        class MockChatHistory:
            def add_message(self, chat_id, role, content):
                print(f"💬 Chat history: {role}: {content[:50]}...")
        
        handler = MessageHandler(
            client=mock_client,
            chat_history=MockChatHistory()
        )
        
        # Test the response processing
        success = await handler._process_agent_response(mock_message, 12345, response)
        
        if success:
            print("✅ Message handler correctly processed image response")
        else:
            print("❌ Message handler failed to process image response")
            return False
        
        # Step 5: Cleanup
        print("\n📋 Step 5: Cleanup...")
        
        # The message handler should have cleaned up the file
        if not Path(image_path).exists():
            print("✅ Image file properly cleaned up")
        else:
            print("⚠️ Image file not cleaned up (cleaning manually)")
            try:
                os.remove(image_path)
            except Exception:
                pass
        
        print("\n🎉 All tests passed! Telegram image integration working correctly.")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_error_scenarios():
    """Test error handling scenarios."""
    print("\n🧪 Testing Error Scenarios")
    print("=" * 40)
    
    # Test with missing OpenAI key (if we have it, temporarily remove it)
    original_key = os.getenv("OPENAI_API_KEY")
    
    if original_key:
        print("📋 Testing with missing API key...")
        os.environ.pop("OPENAI_API_KEY", None)
        
        try:
            response = await handle_telegram_message(
                message="Create an image of a robot",
                chat_id=12345,
                username="test_user"
            )
            
            if "unavailable" in response.lower() or "error" in response.lower():
                print("✅ Correctly handled missing API key")
            else:
                print(f"⚠️ Unexpected response to missing key: {response}")
        
        finally:
            # Restore the key
            os.environ["OPENAI_API_KEY"] = original_key
    
    print("✅ Error scenario testing completed")


async def main():
    """Run all Telegram image integration tests."""
    print("🚀 Telegram Image Integration Test Suite")
    print("=" * 70)
    
    success = await test_telegram_image_generation()
    
    if success:
        await test_error_scenarios()
        print("\n🎉 All Telegram image integration tests completed successfully!")
    else:
        print("\n❌ Tests failed. Check the implementation and try again.")


if __name__ == "__main__":
    asyncio.run(main())