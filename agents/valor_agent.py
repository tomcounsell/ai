#!/usr/bin/env python3
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic-ai",
#   "openai",
#   "python-dotenv",
#   "anthropic"
# ]
# ///

Unified PydanticAI agent for Valor Engels persona with comprehensive tool integration.
This agent handles both standalone interactions and Telegram chat integration.
"""

import asyncio

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import the refactored components
from agents.valor.agent import ValorContext, run_valor_agent
from agents.valor.handlers import handle_telegram_message

# Test function and example usage
if __name__ == "__main__":

    async def test_valor_agent():
        """Test the unified Valor agent with various types of queries.

        This function runs a series of test cases to validate that the Valor
        agent is working correctly with different types of queries including
        general questions, tool usage, and Telegram integration.

        The test cases cover:
        - Technical advice questions
        - Web search functionality
        - Complex coding task delegation
        - Telegram-style interactions

        Raises:
            Exception: If any test case fails unexpectedly.
        """

        test_cases = [
            # Standalone usage
            "How should I structure a FastAPI project for production?",
            "What are the latest trends in AI development?",
            # Telegram simulation
            (
                "Hey Valor, what's the latest news about Python?",
                {"chat_id": 12345, "username": "test_user"},
            ),
            (
                "Create a simple CLI tool in /tmp using TypeScript",
                {"chat_id": 12345, "username": "test_user"},
            ),
        ]

        print("🤖 Testing Unified Valor Engels Agent with Comprehensive Tools")
        print("=" * 70)

        for i, test_case in enumerate(test_cases, 1):
            if isinstance(test_case, tuple):
                query, context_data = test_case
                context = ValorContext(**context_data)
                print(f"\n{i}. Telegram Query: {query}")
            else:
                query = test_case
                context = ValorContext()
                print(f"\n{i}. Standalone Query: {query}")

            print("-" * 50)

            try:
                response = await run_valor_agent(query, context)
                print(f"Valor: {response}")
            except Exception as e:
                print(f"Error: {e}")

            if i < len(test_cases):
                print("\n" + "=" * 70)

    # Test telegram message handler
    async def test_telegram_integration():
        """Test the Telegram message handling functionality."""

        print("\n🔗 Testing Telegram Integration")
        print("=" * 70)

        response = await handle_telegram_message(
            message="How's it going?", chat_id=12345, username="test_user"
        )
        print(f"Telegram Test Response: {response}")

    # Only run test if executed directly
    try:
        asyncio.run(test_valor_agent())
        asyncio.run(test_telegram_integration())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    except Exception as e:
        print(f"\nTest failed: {e}")
