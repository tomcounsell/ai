#!/usr/bin/env python3
"""
Message History Merging Test Suite

Tests for the new Telegram + PydanticAI message history merging functionality.
Verifies proper chronological ordering, duplicate detection, and integration.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from agents.message_history_converter import (
    merge_telegram_with_pydantic_history,
    _remove_duplicate_messages,
    integrate_with_existing_telegram_chat
)
from integrations.telegram.chat_history import ChatHistoryManager
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart


def model_messages_to_dicts(messages: list[ModelRequest | ModelResponse]) -> list[dict[str, str]]:
    """Convert ModelMessage objects back to dictionaries for testing."""
    result = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            role = "user"
        elif isinstance(msg, ModelResponse):
            role = "assistant"
        else:
            continue
        
        # Extract text content from parts
        content = ""
        for part in msg.parts:
            if hasattr(part, 'content'):
                content += part.content
            elif hasattr(part, 'text'):  # TextPart uses 'text' attribute
                content += part.text
        
        result.append({"role": role, "content": content})
    
    return result


class MessageHistoryMergingTester:
    """Test message history merging functionality"""

    def __init__(self):
        self.test_chat_id = 99999  # Use unique chat ID for tests
        self.chat_history = ChatHistoryManager()

    def clear_test_chat(self):
        """Clear test chat history"""
        if self.test_chat_id in self.chat_history.chat_histories:
            del self.chat_history.chat_histories[self.test_chat_id]
        print(f"🧹 Cleared test chat {self.test_chat_id}")

    def test_empty_history_merging(self):
        """Test merging with empty history"""
        print("\n🧪 Test: Empty History Merging")
        self.clear_test_chat()

        # Test with no history
        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=None,
            max_context_messages=10,
            deduplicate=True
        )

        if len(merged) == 0:
            print("✅ Empty history merging test passed")
            return True
        else:
            print(f"❌ Expected empty list, got: {model_messages_to_dicts(merged)}")
            return False

    def test_telegram_only_merging(self):
        """Test merging with Telegram history only"""
        print("\n🧪 Test: Telegram Only Merging")
        self.clear_test_chat()

        # Add some Telegram messages
        self.chat_history.add_message(self.test_chat_id, "user", "Hello from Telegram")
        self.chat_history.add_message(self.test_chat_id, "assistant", "Hi from bot")

        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=None,
            max_context_messages=10,
            deduplicate=True
        )

        expected = [
            {"role": "user", "content": "Hello from Telegram"},
            {"role": "assistant", "content": "Hi from bot"}
        ]

        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == expected:
            print("✅ Telegram only merging test passed")
            return True
        else:
            print(f"❌ Expected: {expected}")
            print(f"❌ Actual: {merged_dicts}")
            return False

    def test_pydantic_only_merging(self):
        """Test merging with PydanticAI history only"""
        print("\n🧪 Test: PydanticAI Only Merging")
        self.clear_test_chat()

        # Create mock PydanticAI history
        pydantic_history = [
            {"role": "user", "content": "Hello from PydanticAI"},
            {"role": "assistant", "content": "Hi from agent"}
        ]

        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=pydantic_history,
            max_context_messages=10,
            deduplicate=True
        )

        expected = [
            {"role": "user", "content": "Hello from PydanticAI"},
            {"role": "assistant", "content": "Hi from agent"}
        ]

        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == expected:
            print("✅ PydanticAI only merging test passed")
            return True
        else:
            print(f"❌ Expected: {expected}")
            print(f"❌ Actual: {merged_dicts}")
            return False

    def test_chronological_ordering(self):
        """Test that messages are properly ordered chronologically"""
        print("\n🧪 Test: Chronological Ordering")
        self.clear_test_chat()

        # Add Telegram messages with specific timestamps
        base_time = time.time()
        
        # First add some messages to Telegram history manually with timestamps
        telegram_msg1 = {"role": "user", "content": "Telegram message 1", "timestamp": base_time}
        telegram_msg2 = {"role": "assistant", "content": "Telegram response 1", "timestamp": base_time + 1}
        telegram_msg3 = {"role": "user", "content": "Telegram message 2", "timestamp": base_time + 3}
        
        # Manually add to history (bypassing normal add_message to control timestamps)
        self.chat_history.chat_histories[self.test_chat_id] = [
            telegram_msg1, telegram_msg2, telegram_msg3
        ]

        # Create PydanticAI history with intermediate timestamp
        pydantic_history = [
            {"role": "user", "content": "PydanticAI message", "timestamp": base_time + 2}
        ]

        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=pydantic_history,
            max_context_messages=10,
            deduplicate=True
        )

        expected = [
            {"role": "user", "content": "Telegram message 1"},
            {"role": "assistant", "content": "Telegram response 1"},
            {"role": "user", "content": "PydanticAI message"},
            {"role": "user", "content": "Telegram message 2"}
        ]

        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == expected:
            print("✅ Chronological ordering test passed")
            return True
        else:
            print(f"❌ Expected: {expected}")
            print(f"❌ Actual: {merged_dicts}")
            return False

    def test_duplicate_detection(self):
        """Test duplicate message detection and removal"""
        print("\n🧪 Test: Duplicate Detection")
        
        # Create test data with duplicates
        messages_with_duplicates = [
            {"role": "user", "content": "Hello", "timestamp": 1, "source": "telegram"},
            {"role": "assistant", "content": "Hi", "timestamp": 2, "source": "telegram"},
            {"role": "user", "content": "Hello", "timestamp": 3, "source": "pydantic"},  # Duplicate
            {"role": "user", "content": "How are you?", "timestamp": 4, "source": "telegram"},
            {"role": "assistant", "content": "Hi", "timestamp": 5, "source": "pydantic"},  # Duplicate
        ]

        deduplicated = _remove_duplicate_messages(messages_with_duplicates)

        # Should keep the most recent occurrence of each duplicate
        expected = [
            {"role": "user", "content": "Hello", "timestamp": 3, "source": "pydantic"},
            {"role": "user", "content": "How are you?", "timestamp": 4, "source": "telegram"},
            {"role": "assistant", "content": "Hi", "timestamp": 5, "source": "pydantic"},
        ]

        if deduplicated == expected:
            print("✅ Duplicate detection test passed")
            return True
        else:
            print(f"❌ Expected: {expected}")
            print(f"❌ Actual: {deduplicated}")
            return False

    def test_max_context_messages_limit(self):
        """Test that max_context_messages limit is respected"""
        print("\n🧪 Test: Max Context Messages Limit")
        self.clear_test_chat()

        # Add many messages to Telegram history
        for i in range(15):
            self.chat_history.add_message(self.test_chat_id, "user", f"Message {i}")

        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=None,
            max_context_messages=5,  # Limit to 5 messages
            deduplicate=True
        )

        merged_dicts = model_messages_to_dicts(merged)
        if len(merged_dicts) == 5 and merged_dicts[-1]["content"] == "Message 14":
            print("✅ Max context messages limit test passed")
            return True
        else:
            print(f"❌ Expected 5 messages ending with 'Message 14', got {len(merged_dicts)} messages")
            print(f"❌ Last message: {merged_dicts[-1] if merged_dicts else 'None'}")
            return False

    def test_legacy_compatibility(self):
        """Test backward compatibility with existing function"""
        print("\n🧪 Test: Legacy Compatibility")
        self.clear_test_chat()

        # Add some messages
        self.chat_history.add_message(self.test_chat_id, "user", "Legacy test")
        self.chat_history.add_message(self.test_chat_id, "assistant", "Legacy response")

        # Test legacy function
        legacy_result = integrate_with_existing_telegram_chat(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            system_prompt="Test prompt",  # Should be ignored
            max_context_messages=6
        )

        # Test new function with same parameters
        new_result = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=None,
            max_context_messages=6,
            deduplicate=True
        )

        legacy_dicts = model_messages_to_dicts(legacy_result)
        new_dicts = model_messages_to_dicts(new_result)
        
        if legacy_dicts == new_dicts:
            print("✅ Legacy compatibility test passed")
            return True
        else:
            print(f"❌ Legacy and new functions returned different results")
            print(f"❌ Legacy: {legacy_dicts}")
            print(f"❌ New: {new_dicts}")
            return False

    def test_mixed_source_merging(self):
        """Test merging messages from both sources with complex scenario"""
        print("\n🧪 Test: Mixed Source Merging")
        self.clear_test_chat()

        # Create a complex scenario with interwoven messages
        base_time = time.time()
        
        # Add Telegram messages
        self.chat_history.chat_histories[self.test_chat_id] = [
            {"role": "user", "content": "Start conversation", "timestamp": base_time},
            {"role": "assistant", "content": "Hello!", "timestamp": base_time + 1},
            {"role": "user", "content": "What's the weather?", "timestamp": base_time + 4},
            {"role": "assistant", "content": "Let me check", "timestamp": base_time + 5},
        ]

        # Create PydanticAI history with some overlapping and new messages
        pydantic_history = [
            {"role": "user", "content": "Actually, tell me a joke", "timestamp": base_time + 2},
            {"role": "assistant", "content": "Why did the chicken cross the road?", "timestamp": base_time + 3},
            {"role": "assistant", "content": "It's sunny!", "timestamp": base_time + 6},
        ]

        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            pydantic_agent_history=pydantic_history,
            max_context_messages=20,
            deduplicate=True
        )

        expected = [
            {"role": "user", "content": "Start conversation"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "Actually, tell me a joke"},
            {"role": "assistant", "content": "Why did the chicken cross the road?"},
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "Let me check"},
            {"role": "assistant", "content": "It's sunny!"},
        ]

        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == expected:
            print("✅ Mixed source merging test passed")
            return True
        else:
            print(f"❌ Expected: {expected}")
            print(f"❌ Actual: {merged_dicts}")
            return False

    def test_edge_cases(self):
        """Test various edge cases"""
        print("\n🧪 Test: Edge Cases")
        
        # Test with None chat_history_obj
        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=None,
            chat_id=self.test_chat_id,
            pydantic_agent_history=[{"role": "user", "content": "Test"}],
            max_context_messages=10,
            deduplicate=True
        )
        
        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == [{"role": "user", "content": "Test"}]:
            print("✅ None chat_history_obj edge case passed")
        else:
            print(f"❌ None chat_history_obj failed: {merged_dicts}")
            return False

        # Test with empty content messages (should be filtered out)
        pydantic_history = [
            {"role": "user", "content": "Valid message"},
            {"role": "user", "content": ""},  # Empty content
            {"role": "assistant", "content": "Another valid message"},
        ]
        
        merged = merge_telegram_with_pydantic_history(
            telegram_chat_history_obj=None,
            chat_id=self.test_chat_id,
            pydantic_agent_history=pydantic_history,
            max_context_messages=10,
            deduplicate=True
        )
        
        expected = [
            {"role": "user", "content": "Valid message"},
            {"role": "assistant", "content": "Another valid message"},
        ]
        
        merged_dicts = model_messages_to_dicts(merged)
        if merged_dicts == expected:
            print("✅ Empty content filtering edge case passed")
            return True
        else:
            print(f"❌ Empty content filtering failed: {merged_dicts}")
            return False


async def main():
    """Run all message history merging tests"""
    print("🧪 Message History Merging Test Suite")
    print("=" * 50)

    tester = MessageHistoryMergingTester()
    tests = [
        ("Empty History Merging", tester.test_empty_history_merging),
        ("Telegram Only Merging", tester.test_telegram_only_merging),
        ("PydanticAI Only Merging", tester.test_pydantic_only_merging),
        ("Chronological Ordering", tester.test_chronological_ordering),
        ("Duplicate Detection", tester.test_duplicate_detection),
        ("Max Context Messages Limit", tester.test_max_context_messages_limit),
        ("Legacy Compatibility", tester.test_legacy_compatibility),
        ("Mixed Source Merging", tester.test_mixed_source_merging),
        ("Edge Cases", tester.test_edge_cases),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()

            if result:
                passed += 1
        except Exception as e:
            print(f"❌ Test '{test_name}' failed with error: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n📊 Test Results: {passed}/{total} passed")

    if passed == total:
        print("🎉 All message history merging tests passed!")
        return 0
    else:
        print("❌ Some tests failed - check message history merging implementation")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)