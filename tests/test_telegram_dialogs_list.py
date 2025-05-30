#!/usr/bin/env python3
"""
Test suite for Telegram dialogs listing functionality.
Tests the TelegramClient.list_active_dialogs() method and utility functions.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from integrations.telegram.client import TelegramClient
from integrations.telegram.utils import format_dialogs_list, list_telegram_dialogs_safe


class MockDialog:
    """Mock dialog object for testing."""
    
    def __init__(self, chat, unread_count=0, top_message=None):
        self.chat = chat
        self.unread_messages_count = unread_count
        self.top_message = top_message


class MockChat:
    """Mock chat object for testing."""
    
    def __init__(self, chat_id, chat_type, title=None, first_name=None, **kwargs):
        self.id = chat_id
        self.type = chat_type
        self.title = title
        self.first_name = first_name
        
        # Optional attributes
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockMessage:
    """Mock message object for testing."""
    
    def __init__(self, date):
        self.date = date


class TestTelegramDialogsList:
    """Test cases for Telegram dialogs listing functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.client = TelegramClient()
        self.client.client = AsyncMock()
        self.client.client.is_connected = True
    
    async def test_list_active_dialogs_success(self):
        """Test successful listing of dialogs."""
        from pyrogram.enums import ChatType
        from datetime import datetime
        
        # Mock dialogs data
        mock_dialogs = [
            MockDialog(
                MockChat(
                    chat_id=-123456789,
                    chat_type=ChatType.SUPERGROUP,
                    title="Test Group",
                    username="testgroup",
                    members_count=150,
                    description="A test group"
                ),
                unread_count=5,
                top_message=MockMessage(datetime.now())
            ),
            MockDialog(
                MockChat(
                    chat_id=987654321,
                    chat_type=ChatType.PRIVATE,
                    first_name="John",
                    last_name="Doe",
                    username="johndoe",
                    phone_number="+1234567890",
                    is_contact=True
                ),
                unread_count=2
            ),
            MockDialog(
                MockChat(
                    chat_id=-111222333,
                    chat_type=ChatType.CHANNEL,
                    title="Test Channel",
                    username="testchannel",
                    members_count=1000
                ),
                unread_count=0
            )
        ]
        
        # Mock the get_dialogs method
        async def mock_get_dialogs():
            for dialog in mock_dialogs:
                yield dialog
        
        self.client.client.get_dialogs = mock_get_dialogs
        
        # Call the method
        result = await self.client.list_active_dialogs()
        
        # Verify the result structure
        assert isinstance(result, dict)
        assert 'groups' in result
        assert 'dms' in result
        assert 'total_groups' in result
        assert 'total_dms' in result
        assert 'total_dialogs' in result
        
        # Check counts
        assert result['total_groups'] == 2  # SUPERGROUP and CHANNEL
        assert result['total_dms'] == 1     # PRIVATE
        assert result['total_dialogs'] == 3
        
        # Check group data
        groups = result['groups']
        assert len(groups) == 2
        
        # Find the supergroup
        supergroup = next(g for g in groups if g['id'] == -123456789)
        assert supergroup['title'] == "Test Group"
        assert supergroup['type'] == "SUPERGROUP"
        assert supergroup['member_count'] == 150
        assert supergroup['unread_count'] == 5
        assert supergroup['username'] == "testgroup"
        assert supergroup['description'] == "A test group"
        
        # Check DM data
        dms = result['dms']
        assert len(dms) == 1
        
        dm = dms[0]
        assert dm['id'] == 987654321
        assert dm['title'] == "John"  # Uses first_name as title
        assert dm['type'] == "PRIVATE"
        assert dm['unread_count'] == 2
        assert dm['username'] == "johndoe"
        assert dm['last_name'] == "Doe"
        assert dm['phone_number'] == "+1234567890"
        assert dm['is_contact'] is True
    
    async def test_list_active_dialogs_client_not_connected(self):
        """Test error handling when client is not connected."""
        self.client.client.is_connected = False
        
        with pytest.raises(ConnectionError, match="Telegram client is not connected"):
            await self.client.list_active_dialogs()
    
    async def test_list_active_dialogs_no_client(self):
        """Test error handling when client is None."""
        self.client.client = None
        
        with pytest.raises(ConnectionError, match="Telegram client is not connected"):
            await self.client.list_active_dialogs()
    
    async def test_list_active_dialogs_api_errors(self):
        """Test handling of various API errors."""
        from pyrogram.enums import ChatType
        
        # Test rate limit error
        async def mock_get_dialogs_flood():
            raise Exception("FLOOD_WAIT_123: Too many requests")
            yield  # This is needed but never reached
        
        self.client.client.get_dialogs = mock_get_dialogs_flood
        
        with pytest.raises(Exception, match="Rate limit exceeded"):
            await self.client.list_active_dialogs()
        
        # Test authentication error
        async def mock_get_dialogs_auth():
            raise Exception("AUTH_KEY_INVALID: Invalid authentication key")
            yield  # This is needed but never reached
        
        self.client.client.get_dialogs = mock_get_dialogs_auth
        
        with pytest.raises(PermissionError, match="Authentication error"):
            await self.client.list_active_dialogs()
        
        # Test access denied error
        async def mock_get_dialogs_access():
            raise Exception("ACCESS_DENIED: Access to this resource is denied")
            yield  # This is needed but never reached
        
        self.client.client.get_dialogs = mock_get_dialogs_access
        
        with pytest.raises(PermissionError, match="Access denied"):
            await self.client.list_active_dialogs()
        
        # Test generic error
        async def mock_get_dialogs_generic():
            raise Exception("Unknown error occurred")
            yield  # This is needed but never reached
        
        self.client.client.get_dialogs = mock_get_dialogs_generic
        
        with pytest.raises(Exception, match="Failed to retrieve dialogs"):
            await self.client.list_active_dialogs()
    
    def test_format_dialogs_list(self):
        """Test the format_dialogs_list utility function."""
        from datetime import datetime
        
        # Sample data
        dialogs_data = {
            'groups': [
                {
                    'id': -123456789,
                    'title': 'Test Group',
                    'type': 'SUPERGROUP',
                    'member_count': 150,
                    'unread_count': 5,
                    'username': 'testgroup'
                },
                {
                    'id': -111222333,
                    'title': 'Test Channel',
                    'type': 'CHANNEL',
                    'member_count': 1000,
                    'unread_count': 0,
                    'username': None
                }
            ],
            'dms': [
                {
                    'id': 987654321,
                    'title': 'John Doe',
                    'username': 'johndoe',
                    'unread_count': 2,
                    'is_contact': True
                },
                {
                    'id': 876543210,
                    'title': 'Jane Smith',
                    'username': None,
                    'unread_count': 0,
                    'is_contact': False
                }
            ],
            'total_groups': 2,
            'total_dms': 2,
            'total_dialogs': 4
        }
        
        result = format_dialogs_list(dialogs_data)
        
        # Check that result contains expected elements
        assert "📊 **Telegram Dialogs Summary**" in result
        assert "Total: 4 dialogs (2 groups, 2 DMs)" in result
        assert "👥 **Groups/Channels:**" in result
        assert "💬 **Direct Messages:**" in result
        
        # Check group formatting
        assert "Test Group (ID: -123456789, Type: SUPERGROUP, Members: 150, Unread: 5)" in result
        assert "Test Channel (ID: -111222333, Type: CHANNEL, Members: 1000)" in result
        
        # Check DM formatting
        assert "John Doe (ID: 987654321, @johndoe, Contact, Unread: 2)" in result
        assert "Jane Smith (ID: 876543210)" in result
    
    def test_format_dialogs_list_empty(self):
        """Test format_dialogs_list with empty data."""
        result = format_dialogs_list({})
        assert result == "No dialog data available"
        
        result = format_dialogs_list(None)
        assert result == "No dialog data available"
    
    async def test_list_telegram_dialogs_safe_success(self):
        """Test the safe wrapper function with successful call."""
        # Mock successful response
        mock_data = {
            'groups': [],
            'dms': [],
            'total_groups': 0,
            'total_dms': 0,
            'total_dialogs': 0
        }
        
        self.client.list_active_dialogs = AsyncMock(return_value=mock_data)
        
        data, error = await list_telegram_dialogs_safe(self.client)
        
        assert data == mock_data
        assert error is None
    
    async def test_list_telegram_dialogs_safe_client_not_connected(self):
        """Test the safe wrapper function with disconnected client."""
        self.client.client.is_connected = False
        
        data, error = await list_telegram_dialogs_safe(self.client)
        
        assert data is None
        assert error == "Telegram client is not connected"
    
    async def test_list_telegram_dialogs_safe_no_client(self):
        """Test the safe wrapper function with no client."""
        data, error = await list_telegram_dialogs_safe(None)
        
        assert data is None
        assert error == "Telegram client is not connected"
    
    async def test_list_telegram_dialogs_safe_errors(self):
        """Test the safe wrapper function with various errors."""
        # Connection error
        self.client.list_active_dialogs = AsyncMock(
            side_effect=ConnectionError("Connection failed")
        )
        
        data, error = await list_telegram_dialogs_safe(self.client)
        assert data is None
        assert "Connection error" in error
        
        # Permission error
        self.client.list_active_dialogs = AsyncMock(
            side_effect=PermissionError("Access denied")
        )
        
        data, error = await list_telegram_dialogs_safe(self.client)
        assert data is None
        assert "Permission error" in error
        
        # Generic error
        self.client.list_active_dialogs = AsyncMock(
            side_effect=Exception("Something went wrong")
        )
        
        data, error = await list_telegram_dialogs_safe(self.client)
        assert data is None
        assert "Error retrieving dialogs" in error


async def run_tests():
    """Run all tests."""
    test_class = TestTelegramDialogsList()
    
    print("🧪 Testing Telegram Dialogs List Functionality")
    print("=" * 60)
    
    # Test methods that need async
    async_tests = [
        'test_list_active_dialogs_success',
        'test_list_active_dialogs_client_not_connected',
        'test_list_active_dialogs_no_client',
        'test_list_active_dialogs_api_errors',
        'test_list_telegram_dialogs_safe_success',
        'test_list_telegram_dialogs_safe_client_not_connected',
        'test_list_telegram_dialogs_safe_no_client',
        'test_list_telegram_dialogs_safe_errors'
    ]
    
    # Test methods that are synchronous
    sync_tests = [
        'test_format_dialogs_list',
        'test_format_dialogs_list_empty'
    ]
    
    total_tests = len(async_tests) + len(sync_tests)
    passed_tests = 0
    failed_tests = 0
    
    # Run async tests
    for test_name in async_tests:
        test_instance = TestTelegramDialogsList()
        test_instance.setup_method()
        
        try:
            print(f"Running {test_name}...")
            await getattr(test_instance, test_name)()
            print(f"✅ {test_name} passed")
            passed_tests += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            failed_tests += 1
    
    # Run sync tests
    for test_name in sync_tests:
        test_instance = TestTelegramDialogsList()
        test_instance.setup_method()
        
        try:
            print(f"Running {test_name}...")
            getattr(test_instance, test_name)()
            print(f"✅ {test_name} passed")
            passed_tests += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            failed_tests += 1
    
    print("\n" + "=" * 60)
    print(f"📊 Test Results: {passed_tests}/{total_tests} passed, {failed_tests} failed")
    
    if failed_tests == 0:
        print("🎉 All tests passed!")
        return True
    else:
        print(f"💥 {failed_tests} tests failed")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)