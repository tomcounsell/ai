#!/usr/bin/env python3
"""Test startup missed message check functionality."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from integrations.telegram.client import TelegramClient
from integrations.telegram.chat_history import ChatHistoryManager
from integrations.telegram.handlers import MessageHandler


class MockDialog:
    """Mock dialog for testing."""
    
    def __init__(self, chat_id, chat_type="private"):
        from pyrogram.enums import ChatType
        self.chat = MagicMock()
        self.chat.id = chat_id
        if chat_type == "private":
            self.chat.type = ChatType.PRIVATE
        elif chat_type == "supergroup":
            self.chat.type = ChatType.SUPERGROUP
        else:
            self.chat.type = chat_type


class MockMessage:
    """Mock message for testing."""
    
    def __init__(self, text, timestamp, chat_id=12345):
        self.text = text
        self.date = MagicMock()
        self.date.timestamp.return_value = timestamp
        self.chat = MagicMock()
        self.chat.id = chat_id


async def test_startup_missed_messages_basic():
    """Test basic startup missed message detection."""
    print("🧪 Testing startup missed message detection...")
    
    # Create a TelegramClient instance
    client = TelegramClient()
    current_time = time.time()
    client.bot_start_time = current_time - 1800  # Bot started 30 minutes ago
    
    # Mock the pyrogram client
    mock_client = AsyncMock()
    client.client = mock_client
    
    # Create chat history and message handler
    chat_history = ChatHistoryManager()
    message_handler = MessageHandler(
        client=mock_client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=client.bot_start_time
    )
    client.message_handler = message_handler
    client.chat_history = chat_history
    
    # Setup mock dialogs (one DM that should be handled)
    mock_dialogs = [
        MockDialog(12345, "private"),  # DM - should be handled (DMs enabled by default)
        MockDialog(-1001111111111, "supergroup"),  # Group - should be ignored (not in allowed groups)
    ]
    
    # Create mock messages - some old (missed), some new
    # Note: current_time is already defined above
    old_message_time = current_time - 600  # 10 minutes ago (missed - after bot start but old enough)
    new_message_time = current_time - 60   # 1 minute ago (not missed)
    very_old_time = current_time - 7200    # 2 hours ago (should be ignored - before bot start)
    
    mock_messages_dm = [
        MockMessage("Hello, are you there?", old_message_time, 12345),  # Missed
        MockMessage("Can you help me?", old_message_time - 100, 12345),  # Missed
        MockMessage("Never mind", new_message_time, 12345),              # Not missed
    ]
    
    # Mock the async iterators
    async def mock_get_dialogs():
        for dialog in mock_dialogs:
            yield dialog
    
    async def mock_get_chat_history_dm(chat_id, limit=50):
        if chat_id == 12345:  # DM chat
            for message in mock_messages_dm:
                yield message
        else:
            return  # No messages for other chats
    
    # Create async generator mocks
    mock_client.get_dialogs = lambda: mock_get_dialogs()
    mock_client.get_chat_history = mock_get_chat_history_dm
    
    # Test the startup missed message check
    await client._check_startup_missed_messages()
    
    # Verify that missed messages were collected
    missed_messages = message_handler.missed_messages_per_chat
    
    print(f"📊 Missed messages collected: {missed_messages}")
    
    # Should have found missed messages for chat 12345
    assert 12345 in missed_messages, "Should have found missed messages for DM chat 12345"
    assert len(missed_messages[12345]) == 2, f"Should have found 2 missed messages, got {len(missed_messages[12345])}"
    
    # Verify the content (should be in chronological order)
    expected_messages = ["Can you help me?", "Hello, are you there?"]
    assert missed_messages[12345] == expected_messages, f"Expected {expected_messages}, got {missed_messages[12345]}"
    
    print("✅ Basic startup missed message detection test passed")


async def test_startup_missed_messages_no_missed():
    """Test startup when there are no missed messages."""
    print("🧪 Testing startup with no missed messages...")
    
    # Create a TelegramClient instance
    client = TelegramClient()
    client.bot_start_time = time.time() - 3600  # Bot started 1 hour ago
    
    # Mock the pyrogram client
    mock_client = AsyncMock()
    client.client = mock_client
    
    # Create chat history and message handler
    chat_history = ChatHistoryManager()
    message_handler = MessageHandler(
        client=mock_client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=client.bot_start_time
    )
    client.message_handler = message_handler
    client.chat_history = chat_history
    
    # Setup mock dialogs
    mock_dialogs = [MockDialog(12345, "private")]
    
    # Create only new messages (not missed)
    current_time = time.time()
    new_message_time = current_time - 60  # 1 minute ago (not missed)
    
    mock_messages = [
        MockMessage("Recent message", new_message_time, 12345),
    ]
    
    # Mock the async iterators
    async def mock_get_dialogs():
        for dialog in mock_dialogs:
            yield dialog
    
    async def mock_get_chat_history(chat_id, limit=50):
        if chat_id == 12345:
            for message in mock_messages:
                yield message
    
    # Create async generator mocks
    mock_client.get_dialogs = lambda: mock_get_dialogs()
    mock_client.get_chat_history = mock_get_chat_history
    
    # Test the startup missed message check
    await client._check_startup_missed_messages()
    
    # Verify that no missed messages were collected
    missed_messages = message_handler.missed_messages_per_chat
    
    print(f"📊 Missed messages collected: {missed_messages}")
    
    # Should not have any missed messages
    assert len(missed_messages) == 0, f"Should have no missed messages, got {missed_messages}"
    
    print("✅ No missed messages test passed")


async def test_startup_missed_messages_error_handling():
    """Test error handling during startup missed message check."""
    print("🧪 Testing startup missed message error handling...")
    
    # Create a TelegramClient instance
    client = TelegramClient()
    client.bot_start_time = time.time()
    
    # Mock the pyrogram client that will raise an error
    mock_client = AsyncMock()
    mock_client.get_dialogs.side_effect = Exception("Network error")
    client.client = mock_client
    
    # Create chat history and message handler
    chat_history = ChatHistoryManager()
    message_handler = MessageHandler(
        client=mock_client,
        chat_history=chat_history,
        notion_scout=None,
        bot_start_time=client.bot_start_time
    )
    client.message_handler = message_handler
    client.chat_history = chat_history
    
    # Test that error handling works (should not raise exception)
    try:
        await client._check_startup_missed_messages()
        print("✅ Error handling test passed - no exception raised")
    except Exception as e:
        print(f"❌ Error handling test failed - exception raised: {e}")
        raise


async def main():
    """Run all tests."""
    print("🚀 Startup Missed Messages Test Battery")
    print("=" * 60)
    print("Testing startup missed message detection functionality")
    print("=" * 60)
    
    try:
        await test_startup_missed_messages_basic()
        await test_startup_missed_messages_no_missed()
        await test_startup_missed_messages_error_handling()
        
        print("=" * 60)
        print("🎉 All startup missed message tests completed successfully!")
        print("✅ Basic missed message detection")
        print("✅ No missed messages handling")
        print("✅ Error handling")
        print("🔍 Startup missed message check system is working correctly!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())