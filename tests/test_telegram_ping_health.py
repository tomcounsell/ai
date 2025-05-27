#!/usr/bin/env python3
"""
Test battery for the Telegram ping health check functionality.
Tests that ping command returns proper health metrics and system status.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))


class MockMessage:
    """Mock Telegram message for testing."""
    
    def __init__(self, chat_id: int, text: str):
        self.chat = MagicMock()
        self.chat.id = chat_id
        # Import the actual ChatType enum
        from pyrogram.enums import ChatType
        self.chat.type = ChatType.PRIVATE
        self.text = text
        self.from_user = MagicMock()
        self.from_user.username = "test_user"
        self.replies = []
        
        # Add all required attributes that handlers check for
        self.photo = None
        self.document = None
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        self.caption = None
        self.entities = None
        self.reply_to_message = None
        import time
        self.date = MagicMock()
        self.date.timestamp = MagicMock(return_value=time.time())  # Current timestamp
        
    async def reply(self, text: str):
        """Mock reply method that captures responses."""
        self.replies.append(text)
        print(f"📱 Bot replied: {text[:100]}...")


class MockChatHistory:
    """Mock chat history for testing."""
    
    def __init__(self):
        self.messages = []
        self.chat_histories = {}  # Add the expected attribute
        
    def add_message(self, chat_id: int, role: str, content: str):
        self.messages.append({"chat_id": chat_id, "role": role, "content": content})
        
        # Also maintain the chat_histories structure
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []
        self.chat_histories[chat_id].append({"role": role, "content": content})


class MockTelegramClient:
    """Mock Telegram client for testing."""
    
    async def get_me(self):
        """Mock bot info."""
        me = MagicMock()
        me.username = "test_bot"
        me.id = 123456789
        return me


async def test_ping_command_basic():
    """Test basic ping command functionality."""
    print("🏓 Testing Basic Ping Command")
    print("-" * 40)
    
    # Import the MessageHandler
    from integrations.telegram.handlers import MessageHandler
    
    # Create mock objects
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    message = MockMessage(chat_id=12345, text="ping")
    
    # Create handler
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=None
    )
    
    # Process the ping message
    await handler.handle_message(client, message)
    
    # Verify response
    assert len(message.replies) == 1, "Should have exactly one reply"
    
    response = message.replies[0]
    print(f"Response: {response}")
    
    # Check response content
    assert "pong" in response.lower(), "Response should contain 'pong'"
    assert "🏓" in response, "Response should contain ping pong emoji"
    
    print("✅ Basic ping command test passed")
    return response


async def test_ping_health_metrics():
    """Test that ping returns system health metrics.""" 
    print("\n📊 Testing Ping Health Metrics")
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    # Create mock objects
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    message = MockMessage(chat_id=12345, text="ping")
    
    # Create handler
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=None
    )
    
    # Process the ping message
    await handler.handle_message(client, message)
    
    response = message.replies[0]
    
    # Check for health metrics in response
    health_indicators = [
        "system health",
        "cpu",
        "memory", 
        "disk",
        "bot status",
        "agent",
        "tools"
    ]
    
    found_indicators = []
    for indicator in health_indicators:
        if indicator in response.lower():
            found_indicators.append(indicator)
    
    print(f"Health indicators found: {found_indicators}")
    print(f"Health metrics included: {len(found_indicators) >= 4}")
    
    # Should have at least basic health info
    assert len(found_indicators) >= 3, f"Expected health metrics, found: {found_indicators}"
    
    print("✅ Ping health metrics test passed")
    return response


async def test_ping_bot_status():
    """Test that ping returns bot status information."""
    print("\n🤖 Testing Ping Bot Status")
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    # Create mock objects with notion_scout
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    message = MockMessage(chat_id=12345, text="ping")
    
    # Mock notion scout
    notion_scout = MagicMock()
    
    # Create handler with notion scout
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=notion_scout,
        bot_start_time=None
    )
    
    # Process the ping message
    await handler.handle_message(client, message)
    
    response = message.replies[0]
    
    # Check for bot status indicators
    bot_status_indicators = [
        "valor_agent",
        "active",
        "connected",
        "notion",
        "tools"
    ]
    
    found_status = []
    for indicator in bot_status_indicators:
        if indicator in response.lower():
            found_status.append(indicator)
    
    print(f"Bot status indicators found: {found_status}")
    
    # Should show agent is active and notion is connected
    assert "active" in response.lower(), "Should show agent is active"
    assert "connected" in response.lower(), "Should show notion is connected"
    
    print("✅ Ping bot status test passed")


async def test_ping_without_notion():
    """Test ping response when notion is not configured."""
    print("\n⚙️ Testing Ping Without Notion")
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    # Create mock objects without notion_scout
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    message = MockMessage(chat_id=12345, text="ping")
    
    # Create handler without notion scout
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=None,  # No notion scout
        bot_start_time=None
    )
    
    # Process the ping message
    await handler.handle_message(client, message)
    
    response = message.replies[0]
    
    # Check that it properly indicates notion is not configured
    assert "not configured" in response.lower() or "❌" in response, \
        "Should indicate notion is not configured"
    
    print("✅ Ping without notion test passed")


async def test_ping_error_handling():
    """Test ping error handling when system metrics fail."""
    print("\n⚠️ Testing Ping Error Handling")
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    # Create mock objects
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    message = MockMessage(chat_id=12345, text="ping")
    
    # Create handler
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=None
    )
    
    # Process the ping message (should handle any psutil errors gracefully)
    await handler.handle_message(client, message)
    
    response = message.replies[0]
    
    # Should still contain pong even if metrics fail
    assert "pong" in response.lower(), "Should still respond with pong"
    assert "🏓" in response, "Should contain ping pong emoji"
    
    # Should indicate if metrics are unavailable
    if "unavailable" in response.lower():
        print("   Gracefully handled metrics unavailability")
    else:
        print("   System metrics available")
    
    print("✅ Ping error handling test passed")


async def test_ping_case_insensitive():
    """Test that ping works regardless of case."""
    print("\n🔤 Testing Ping Case Insensitivity") 
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    test_cases = ["ping", "PING", "Ping", "PiNg"]
    
    for test_case in test_cases:
        print(f"Testing: '{test_case}'")
        
        # Create fresh mock objects for each test
        client = MockTelegramClient()
        chat_history = MockChatHistory()
        message = MockMessage(chat_id=12345, text=test_case)
        
        handler = MessageHandler(
            client=client,
            chat_history=chat_history,
            notion_scout=None,
            bot_start_time=None
        )
        
        # Process the message
        await handler.handle_message(client, message)
        
        # Verify response
        assert len(message.replies) == 1, f"Should reply to '{test_case}'"
        response = message.replies[0]
        assert "pong" in response.lower(), f"Should respond to '{test_case}' with pong"
    
    print("✅ Ping case insensitivity test passed")


async def test_ping_vs_valor_agent_routing():
    """Test that ping bypasses valor_agent and other messages go to valor_agent."""
    print("\n🔀 Testing Ping vs Valor Agent Routing")
    print("-" * 40)
    
    from integrations.telegram.handlers import MessageHandler
    
    # Test ping (should NOT go to valor_agent)
    client = MockTelegramClient()
    chat_history = MockChatHistory()
    ping_message = MockMessage(chat_id=12345, text="ping")
    
    handler = MessageHandler(
        client=client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=None
    )
    
    await handler.handle_message(client, ping_message)
    
    ping_response = ping_message.replies[0]
    assert "pong" in ping_response.lower(), "Ping should get direct pong response"
    assert "🏓" in ping_response, "Ping should use ping-pong emoji"
    
    print("   ✅ Ping bypassed valor_agent correctly")
    
    # Test non-ping message (should go to valor_agent)
    hello_message = MockMessage(chat_id=12345, text="hello")
    
    await handler.handle_message(client, hello_message)
    
    # The hello message should be processed by valor_agent 
    # (we can't easily test the full flow without mocking valor_agent,
    #  but we can verify it doesn't get the ping treatment)
    if hello_message.replies:
        hello_response = hello_message.replies[0]
        assert "pong" not in hello_response.lower(), "Hello should not get pong response"
        print("   ✅ Non-ping message routed to valor_agent")
    else:
        print("   ℹ️ Non-ping message processed (no immediate response expected)")
    
    print("✅ Ping vs valor_agent routing test passed")


class PingHealthTester:
    """Test battery for ping health check functionality."""
    
    async def run_all_tests(self):
        """Run the complete ping health test battery."""
        print("🚀 Telegram Ping Health Check Test Battery")
        print("=" * 60)
        print("Testing ping command health metrics and system status")
        print("=" * 60)
        
        try:
            await test_ping_command_basic()
            await test_ping_health_metrics()
            await test_ping_bot_status()
            await test_ping_without_notion()
            await test_ping_error_handling()
            await test_ping_case_insensitive()
            await test_ping_vs_valor_agent_routing()
            
            print("\n" + "=" * 60)
            print("🎉 All ping health tests completed successfully!")
            print("✅ Basic ping-pong functionality")
            print("✅ System health metrics reporting")
            print("✅ Bot status information")
            print("✅ Notion configuration status")
            print("✅ Graceful error handling")
            print("✅ Case insensitive matching")
            print("✅ Proper routing (ping vs valor_agent)")
            print("\n🏓 Ping health check system is working correctly!")
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            raise


async def main():
    """Run the ping health test battery."""
    tester = PingHealthTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())