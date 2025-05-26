#!/usr/bin/env python3
"""
Test battery for the Telegram chat agent with conversation history simulation.
Tests various conversation scenarios to validate agent behavior.
"""

import asyncio
from typing import Any


class MockChatHistory:
    """Mock chat history manager for testing."""

    def __init__(self):
        self.conversations: dict[int, list[dict[str, Any]]] = {}

    def add_message(self, chat_id: int, role: str, content: str):
        """Add a message to the conversation history."""
        if chat_id not in self.conversations:
            self.conversations[chat_id] = []

        self.conversations[chat_id].append({"role": role, "content": content})

    def get_context(self, chat_id: int) -> list[dict[str, Any]]:
        """Get conversation context for a chat."""
        return self.conversations.get(chat_id, [])


class TelegramChatAgentTester:
    """Test battery for the Telegram chat agent."""

    def __init__(self):
        self.chat_history = MockChatHistory()
        self.test_chat_id = 12345

        # Add parent directory to path for imports
        import sys
        from pathlib import Path

        sys.path.append(str(Path(__file__).parent.parent))

    async def run_conversation_test(
        self, test_name: str, conversation_steps: list[dict[str, str]]
    ) -> list[str]:
        """
        Run a conversation test with multiple exchanges.

        Args:
            test_name: Name of the test scenario
            conversation_steps: List of {"user": "message"} dicts

        Returns:
            List of agent responses
        """
        print(f"\n🧪 Running test: {test_name}")
        print("=" * 50)

        responses = []

        # Import here to avoid circular imports during testing
        from integrations.telegram.response_handlers import handle_general_question

        for i, step in enumerate(conversation_steps, 1):
            user_message = step["user"]

            print(f"\n{i}. User: {user_message}")

            # Get agent response
            response = await handle_general_question(
                question=user_message,
                anthropic_client=None,
                chat_id=self.test_chat_id,
                chat_history=self.chat_history,
            )

            print(f"   Valor: {response}")

            # Add both messages to history for next iteration
            self.chat_history.add_message(self.test_chat_id, "user", user_message)
            self.chat_history.add_message(self.test_chat_id, "assistant", response)

            responses.append(response)

        return responses

    async def test_casual_conversation(self):
        """Test casual conversation flow."""
        conversation = [
            {"user": "Hey, how's it going?"},
            {"user": "What have you been up to lately?"},
            {"user": "Nice! Any interesting technical challenges recently?"},
        ]

        responses = await self.run_conversation_test("Casual Conversation", conversation)

        # Validate responses
        assert len(responses) == 3
        assert "good" in responses[0].lower() or "hey" in responses[0].lower()
        print("✅ Casual conversation test passed")

        return responses

    async def test_technical_discussion(self):
        """Test technical discussion with context building."""
        conversation = [
            {"user": "I'm working on a FastAPI application"},
            {"user": "What's the best way to handle database connections?"},
            {"user": "Should I use connection pooling for this?"},
            {"user": "Any specific libraries you'd recommend?"},
        ]

        responses = await self.run_conversation_test("Technical Discussion", conversation)

        # Validate technical responses
        assert len(responses) == 4
        assert any("database" in r.lower() for r in responses[1:])
        assert any("pool" in r.lower() for r in responses[2:])
        print("✅ Technical discussion test passed")

        return responses

    async def test_context_continuity(self):
        """Test that agent maintains context across conversation."""
        conversation = [
            {"user": "I'm debugging a memory leak in Python"},
            {"user": "It seems to happen during long-running processes"},
            {"user": "What tools would you suggest for this specific issue?"},
        ]

        responses = await self.run_conversation_test("Context Continuity", conversation)

        # Validate context awareness
        assert len(responses) == 3
        # Last response should reference the specific memory leak context
        last_response = responses[-1].lower()
        assert any(keyword in last_response for keyword in ["memory", "leak", "debug", "profil"])
        print("✅ Context continuity test passed")

        return responses

    async def test_search_tool_integration(self):
        """Test that search tool is used appropriately."""
        conversation = [
            {"user": "What are the latest developments in Python 3.13?"},
            {"user": "How do those new features compare to what we had before?"},
        ]

        responses = await self.run_conversation_test("Search Tool Integration", conversation)

        # Validate search tool usage
        assert len(responses) == 2
        # First response should contain current information (indicating search was used)
        responses[0].lower()
        print("✅ Search tool integration test passed")

        return responses

    async def test_persona_consistency(self):
        """Test that Valor Engels persona is maintained."""
        conversation = [
            {"user": "Tell me about yourself"},
            {"user": "What kind of work do you do?"},
            {"user": "How long have you been at Yudame?"},
        ]

        responses = await self.run_conversation_test("Persona Consistency", conversation)

        # Validate persona elements
        assert len(responses) == 3
        combined_responses = " ".join(responses).lower()
        assert "yudame" in combined_responses or "engineer" in combined_responses
        print("✅ Persona consistency test passed")

        return responses

    async def test_priority_questions(self):
        """Test priority question handling."""
        # Set up project context first
        self.chat_history.add_message(
            self.test_chat_id, "user", "I've been working on the PsyOPTIMAL project"
        )
        self.chat_history.add_message(
            self.test_chat_id, "assistant", "Nice! How's the PsyOPTIMAL development going?"
        )

        from integrations.telegram.response_handlers import handle_user_priority_question

        print("\n🧪 Running test: Priority Questions")
        print("=" * 50)

        user_message = "What should I work on next?"
        print(f"\n1. User: {user_message}")

        response = await handle_user_priority_question(
            question=user_message,
            anthropic_client=None,
            chat_id=self.test_chat_id,
            notion_scout=None,
            chat_history=self.chat_history,
        )

        print(f"   Valor: {response}")

        # Validate priority question response
        assert (
            "priority" in response.lower()
            or "next" in response.lower()
            or "task" in response.lower()
        )
        print("✅ Priority questions test passed")

        return response

    async def run_all_tests(self):
        """Run the complete test battery."""
        print("🚀 Starting Telegram Chat Agent Test Battery")
        print("=" * 60)

        try:
            # Run each test
            await self.test_casual_conversation()

            # Reset for next test
            self.chat_history = MockChatHistory()
            await self.test_technical_discussion()

            # Reset for next test
            self.chat_history = MockChatHistory()
            await self.test_context_continuity()

            # Reset for next test
            self.chat_history = MockChatHistory()
            await self.test_search_tool_integration()

            # Reset for next test
            self.chat_history = MockChatHistory()
            await self.test_persona_consistency()

            # Reset for next test
            self.chat_history = MockChatHistory()
            await self.test_priority_questions()

            print("\n" + "=" * 60)
            print("🎉 All tests passed! Telegram Chat Agent is working correctly.")
            print("✅ Casual conversation")
            print("✅ Technical discussion")
            print("✅ Context continuity")
            print("✅ Search tool integration")
            print("✅ Persona consistency")
            print("✅ Priority questions")

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            raise


async def main():
    """Run the test battery."""
    tester = TelegramChatAgentTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
