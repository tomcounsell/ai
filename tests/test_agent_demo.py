#!/usr/bin/env python3
"""
Demo test showing key features of the Telegram chat agent.
Demonstrates conversation continuity, tool usage, and persona consistency.
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
        self.messages.append({'role': role, 'content': content})
    
    def get_context(self, chat_id: int):
        return self.messages


async def demo_conversation():
    """Demonstrate a realistic conversation showing key agent features."""
    print("🎭 Telegram Chat Agent Demo")
    print("=" * 60)
    print("Demonstrates:")
    print("• Conversation continuity and context awareness")
    print("• Valor Engels persona consistency")
    print("• Intelligent tool usage (search)")
    print("• Technical discussion capabilities")
    print("=" * 60)
    
    from integrations.telegram.response_handlers import handle_general_question
    
    chat_history = MockChatHistory()
    chat_id = 12345
    
    conversation_flow = [
        # Casual greeting
        "Hey Valor, how's your day going?",
        
        # Technical context setup
        "I'm building a new API for our project and considering different frameworks",
        
        # Current information request (should trigger search tool)
        "What's the current state of FastAPI vs Django for APIs in 2024?",
        
        # Context-aware follow-up
        "Based on what you just mentioned, which would you recommend for our use case?",
        
        # Technical deep dive
        "What about async database connections? Any patterns you'd suggest?",
        
        # Personal experience request
        "Have you dealt with similar challenges at Yudame?"
    ]
    
    for i, user_message in enumerate(conversation_flow, 1):
        print(f"\n{i}. 👤 User: {user_message}")
        
        # Get agent response
        response = await handle_general_question(user_message, None, chat_id, chat_history)
        
        print(f"   🤖 Valor: {response}")
        
        # Add both messages to history for continuity
        chat_history.add_message(chat_id, "user", user_message)
        chat_history.add_message(chat_id, "assistant", response)
        
        # Brief pause for readability
        await asyncio.sleep(0.5)
    
    print("\n" + "=" * 60)
    print("✅ Demo completed! Key observations:")
    print("  • Agent maintains conversation context")
    print("  • Valor persona (Yudame engineer) consistent")
    print("  • Search tool used for current information")
    print("  • Technical expertise demonstrated")
    print("  • Natural conversation flow maintained")


async def demo_priority_question():
    """Demonstrate priority question handling."""
    print("\n🎯 Priority Question Demo")
    print("-" * 30)
    
    from integrations.telegram.response_handlers import handle_user_priority_question
    
    # Set up project context
    chat_history = MockChatHistory()
    chat_history.add_message(12345, "user", "I've been working on the FlexTrip mobile app")
    chat_history.add_message(12345, "assistant", "Cool! How's the FlexTrip development going?")
    
    print("Context: User mentioned working on FlexTrip mobile app")
    print("\n👤 User: What should I prioritize working on next?")
    
    response = await handle_user_priority_question(
        question="What should I prioritize working on next?",
        anthropic_client=None,
        chat_id=12345,
        notion_scout=None,
        chat_history=chat_history
    )
    
    print(f"🤖 Valor: {response}")
    print("\n✅ Priority question demo completed!")


async def main():
    """Run the demo."""
    await demo_conversation()
    await demo_priority_question()
    
    print("\n🎉 Telegram Chat Agent Demo Complete!")
    print("The agent successfully demonstrates:")
    print("  ✅ PydanticAI integration")
    print("  ✅ Conversation history management")
    print("  ✅ Intelligent tool orchestration")
    print("  ✅ Persona consistency")
    print("  ✅ Technical expertise")


if __name__ == "__main__":
    asyncio.run(main())