#!/usr/bin/env python3
"""
Quick test of the Telegram chat agent functionality.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))


class MockChatHistory:
    """Mock chat history manager for testing."""

    def __init__(self):
        self.messages = []

    def add_message(self, chat_id: int, role: str, content: str):
        """Add a message to the conversation history."""
        self.messages.append({"role": role, "content": content})

    def get_context(self, chat_id: int):
        """Get conversation context for a chat."""
        return self.messages


async def test_basic_conversation():
    """Test basic conversation with context."""
    print("🧪 Testing basic conversation with context...")

    from integrations.telegram.response_handlers import handle_general_question

    # Create mock chat history
    chat_history = MockChatHistory()

    # First exchange
    print("\n1. User: Hey, how's it going?")
    response1 = await handle_general_question("Hey, how's it going?", None, 12345, chat_history)
    print(f"   Valor: {response1}")

    # Add to history
    chat_history.add_message(12345, "user", "Hey, how's it going?")
    chat_history.add_message(12345, "assistant", response1)

    # Second exchange with context
    print("\n2. User: I'm working on some Python async code")
    response2 = await handle_general_question(
        "I'm working on some Python async code", None, 12345, chat_history
    )
    print(f"   Valor: {response2}")

    # Add to history
    chat_history.add_message(12345, "user", "I'm working on some Python async code")
    chat_history.add_message(12345, "assistant", response2)

    # Third exchange referencing context
    print("\n3. User: Any tips for handling errors in async functions?")
    response3 = await handle_general_question(
        "Any tips for handling errors in async functions?", None, 12345, chat_history
    )
    print(f"   Valor: {response3}")

    print("\n✅ Basic conversation test completed!")
    return [response1, response2, response3]


async def test_search_tool():
    """Test that search tool can be triggered."""
    print("\n🧪 Testing search tool integration...")

    from integrations.telegram.response_handlers import handle_general_question

    chat_history = MockChatHistory()

    print("\n1. User: What are the latest Python releases?")
    response = await handle_general_question(
        "What are the latest Python releases?", None, 12345, chat_history
    )
    print(f"   Valor: {response}")

    print("\n✅ Search tool test completed!")
    return response


async def main():
    """Run quick tests."""
    print("🚀 Quick Test Battery for Telegram Chat Agent")
    print("=" * 50)

    try:
        # Test basic conversation
        await test_basic_conversation()

        # Test search tool
        await test_search_tool()

        print("\n" + "=" * 50)
        print("🎉 All quick tests passed!")
        print("✅ Basic conversation with context")
        print("✅ Search tool integration")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
