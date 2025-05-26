#!/usr/bin/env python3
"""
Chat History Duplication Test

Tests to verify that chat history is properly maintained without duplicates
and that the correct messages are sent to the LLM.
"""

import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from main import (
    chat_histories,
    add_to_chat_history,
    get_chat_context,
    handle_general_question,
    NotionScout
)
from dotenv import load_dotenv
import os

load_dotenv()

class ChatHistoryTester:
    """Test chat history functionality for duplicates and accuracy"""
    
    def __init__(self):
        self.test_chat_id = 88888  # Use unique chat ID for tests
        self.notion_scout = None
        
        # Initialize NotionScout if keys available
        notion_key = os.getenv('NOTION_API_KEY')
        anthropic_key = os.getenv('ANTHROPIC_API_KEY')
        if notion_key and anthropic_key:
            self.notion_scout = NotionScout(notion_key, anthropic_key)
    
    def clear_test_chat(self):
        """Clear test chat history"""
        if self.test_chat_id in chat_histories:
            del chat_histories[self.test_chat_id]
        print(f"🧹 Cleared test chat {self.test_chat_id}")
    
    def test_basic_message_storage(self):
        """Test that messages are stored correctly without duplicates"""
        print("\n🧪 Test: Basic Message Storage")
        self.clear_test_chat()
        
        # Add some messages
        add_to_chat_history(self.test_chat_id, "user", "Hello")
        add_to_chat_history(self.test_chat_id, "assistant", "Hi there!")
        add_to_chat_history(self.test_chat_id, "user", "How are you?")
        
        # Check the history
        history = chat_histories.get(self.test_chat_id, [])
        
        expected_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"}
        ]
        
        print(f"Expected: {len(expected_messages)} messages")
        print(f"Actual: {len(history)} messages")
        
        for i, (expected, actual) in enumerate(zip(expected_messages, history)):
            if expected["role"] != actual["role"] or expected["content"] != actual["content"]:
                print(f"❌ Mismatch at index {i}:")
                print(f"  Expected: {expected}")
                print(f"  Actual: {actual}")
                return False
        
        print("✅ Basic message storage test passed")
        return True
    
    def test_duplicate_prevention(self):
        """Test that duplicate messages are prevented"""
        print("\n🧪 Test: Duplicate Prevention")
        self.clear_test_chat()
        
        # Add a message
        add_to_chat_history(self.test_chat_id, "user", "Test message")
        initial_count = len(chat_histories.get(self.test_chat_id, []))
        
        # Try to add the same message again
        add_to_chat_history(self.test_chat_id, "user", "Test message")
        final_count = len(chat_histories.get(self.test_chat_id, []))
        
        if initial_count == final_count:
            print("✅ Duplicate prevention test passed")
            return True
        else:
            print(f"❌ Duplicate prevention failed: {initial_count} -> {final_count}")
            return False
    
    def test_context_formatting(self):
        """Test that chat context is formatted correctly for LLM"""
        print("\n🧪 Test: Context Formatting")
        self.clear_test_chat()
        
        # Add some messages
        add_to_chat_history(self.test_chat_id, "user", "First message")
        add_to_chat_history(self.test_chat_id, "assistant", "First response")
        add_to_chat_history(self.test_chat_id, "user", "Second message")
        add_to_chat_history(self.test_chat_id, "assistant", "Second response")
        
        # Get formatted context
        context = get_chat_context(self.test_chat_id)
        
        expected_context = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
            {"role": "assistant", "content": "Second response"}
        ]
        
        if context == expected_context:
            print("✅ Context formatting test passed")
            return True
        else:
            print(f"❌ Context formatting failed:")
            print(f"  Expected: {expected_context}")
            print(f"  Actual: {context}")
            return False
    
    async def test_llm_message_sequence(self):
        """Test that the correct message sequence is sent to LLM"""
        print("\n🧪 Test: LLM Message Sequence")
        
        if not self.notion_scout:
            print("⚠️  Skipping LLM test - NotionScout not available")
            return True
        
        self.clear_test_chat()
        
        # Simulate a conversation
        print("🔄 Simulating conversation...")
        
        # First message
        add_to_chat_history(self.test_chat_id, "user", "Hello")
        response1 = await handle_general_question(
            "Hello", 
            self.notion_scout.anthropic_client, 
            self.test_chat_id
        )
        add_to_chat_history(self.test_chat_id, "assistant", response1)
        print(f"  First exchange completed")
        
        # Second message - this is where duplication might occur
        print("🔄 Sending second message...")
        add_to_chat_history(self.test_chat_id, "user", "What's the weather like?")
        response2 = await handle_general_question(
            "What's the weather like?", 
            self.notion_scout.anthropic_client, 
            self.test_chat_id
        )
        add_to_chat_history(self.test_chat_id, "assistant", response2)
        print(f"  Second exchange completed")
        
        # Check final history
        final_history = chat_histories.get(self.test_chat_id, [])
        expected_count = 4  # user->assistant->user->assistant
        
        print(f"Final history count: {len(final_history)} (expected: {expected_count})")
        
        # Check for duplicates
        messages_seen = set()
        duplicates = []
        
        for i, msg in enumerate(final_history):
            key = f"{msg['role']}:{msg['content']}"
            if key in messages_seen:
                duplicates.append(f"Index {i}: {key}")
            messages_seen.add(key)
        
        if duplicates:
            print(f"❌ Found duplicates: {duplicates}")
            return False
        elif len(final_history) != expected_count:
            print(f"❌ Wrong message count: {len(final_history)} vs {expected_count}")
            return False
        else:
            print("✅ LLM message sequence test passed")
            return True
    
    def test_chat_history_isolation(self):
        """Test that different chats don't interfere with each other"""
        print("\n🧪 Test: Chat History Isolation")
        
        chat1_id = 11111
        chat2_id = 22222
        
        # Clear both chats
        if chat1_id in chat_histories:
            del chat_histories[chat1_id]
        if chat2_id in chat_histories:
            del chat_histories[chat2_id]
        
        # Add messages to different chats
        add_to_chat_history(chat1_id, "user", "Chat 1 message")
        add_to_chat_history(chat2_id, "user", "Chat 2 message")
        add_to_chat_history(chat1_id, "assistant", "Chat 1 response")
        
        # Check isolation
        chat1_history = chat_histories.get(chat1_id, [])
        chat2_history = chat_histories.get(chat2_id, [])
        
        if (len(chat1_history) == 2 and 
            len(chat2_history) == 1 and
            chat1_history[0]["content"] == "Chat 1 message" and
            chat2_history[0]["content"] == "Chat 2 message"):
            print("✅ Chat history isolation test passed")
            return True
        else:
            print(f"❌ Chat isolation failed:")
            print(f"  Chat1: {chat1_history}")
            print(f"  Chat2: {chat2_history}")
            return False

async def main():
    """Run all chat history tests"""
    print("🧪 Chat History Duplication Test Suite")
    print("="*50)
    
    tester = ChatHistoryTester()
    tests = [
        ("Basic Message Storage", tester.test_basic_message_storage),
        ("Duplicate Prevention", tester.test_duplicate_prevention), 
        ("Context Formatting", tester.test_context_formatting),
        ("LLM Message Sequence", tester.test_llm_message_sequence),
        ("Chat History Isolation", tester.test_chat_history_isolation)
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
    
    print(f"\n📊 Test Results: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All chat history tests passed!")
        return 0
    else:
        print("❌ Some tests failed - check for message duplication issues")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)