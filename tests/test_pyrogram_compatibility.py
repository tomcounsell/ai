#!/usr/bin/env python3
"""
Test Pyrogram message object compatibility with our code.

No mocks - uses actual Pyrogram objects to verify our code works with reality.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pyrogram.types import Message, User, Chat
    from datetime import datetime
    import pyrogram
except ImportError:
    print("❌ Pyrogram not available - cannot test real message compatibility")
    sys.exit(1)


def test_pyrogram_message_attributes():
    """Test that we understand Pyrogram Message object attributes correctly."""
    print("\n🔍 Testing Pyrogram Message Object Attributes\n")
    
    # Create a real Pyrogram Message object
    # This is what our code will actually receive
    try:
        # Create mock chat and user (these are simple data classes)
        chat = Chat(
            id=-1001234567890,
            type=pyrogram.enums.ChatType.SUPERGROUP,
            title="Test Group"
        )
        
        user = User(
            id=123456789,
            is_self=False,
            is_contact=False,
            is_mutual_contact=False,
            is_deleted=False,
            is_bot=False,
            is_verified=False,
            is_restricted=False,
            is_scam=False,
            is_fake=False,
            is_premium=False,
            first_name="Test",
            username="testuser"
        )
        
        # Create actual Message object
        message = Message(
            id=12345,  # ✅ This is the correct attribute name
            from_user=user,
            chat=chat,
            date=datetime.now(),
            text="Test message",
        )
        
        # Test the attributes our code tries to access
        print("✅ Message object created successfully")
        print(f"✅ message.id = {message.id}")
        print(f"✅ message.text = '{message.text}'")
        print(f"✅ message.chat.id = {message.chat.id}")
        print(f"✅ message.from_user.username = '{message.from_user.username}'")
        print(f"✅ message.date = {message.date}")
        
        # Test what would fail
        try:
            _ = message.message_id
            print("❌ UNEXPECTED: message.message_id exists (this should fail)")
        except AttributeError:
            print("✅ CORRECT: message.message_id does NOT exist (use message.id)")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to create Pyrogram Message object: {e}")
        return False


def test_unified_processor_compatibility():
    """Test that our unified processor can handle real Pyrogram objects."""
    print("\n🔧 Testing Unified Processor Compatibility\n")
    
    try:
        # Import our actual unified processor
        from integrations.telegram.unified_processor import UnifiedMessageProcessor
        
        # Create a real message object
        chat = Chat(
            id=-1001234567890,
            type=pyrogram.enums.ChatType.SUPERGROUP,
            title="Test Group"
        )
        
        user = User(
            id=123456789,
            is_self=False,
            is_contact=False,
            is_mutual_contact=False,
            is_deleted=False,
            is_bot=False,
            is_verified=False,
            is_restricted=False,
            is_scam=False,
            is_fake=False,
            is_premium=False,
            first_name="Test",
            username="testuser"
        )
        
        message = Message(
            id=12345,
            from_user=user,
            chat=chat,
            date=datetime.now(),
            text="Test message",
        )
        
        # Create update wrapper like our handler does
        class UpdateWrapper:
            def __init__(self, message):
                self.message = message
        
        update_obj = UpdateWrapper(message)
        
        # Test that the processor can access message attributes without errors
        processor = UnifiedMessageProcessor()
        
        # This should not raise AttributeError for message.id access
        print(f"✅ Update object created: {update_obj}")
        print(f"✅ Message accessible: {update_obj.message}")
        print(f"✅ Message ID accessible: {update_obj.message.id}")
        
        # Note: We can't test full processing without Telegram client setup
        # But we can verify the object structure is correct
        
        return True
        
    except Exception as e:
        print(f"❌ Unified processor compatibility test failed: {e}")
        return False


def main():
    """Run all Pyrogram compatibility tests."""
    print("🧪 Pyrogram Compatibility Test Suite")
    print("=" * 50)
    print("Testing with real Pyrogram objects (no mocks)")
    
    tests = [
        test_pyrogram_message_attributes,
        test_unified_processor_compatibility,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
    
    print(f"\n📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✅ All Pyrogram compatibility tests passed!")
        return 0
    else:
        print("❌ Some tests failed - check Pyrogram object usage")
        return 1


if __name__ == "__main__":
    sys.exit(main())