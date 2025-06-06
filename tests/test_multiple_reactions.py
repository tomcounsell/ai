#!/usr/bin/env python3
"""
Test the enhanced reaction system with Layer 169+ multiple reaction support.

This test demonstrates how reactions accumulate as message processing evolves:
1. Initial received reaction (👀)
2. Intent classification reaction (🤔, 🎨, etc.)
3. Processing stage reactions (🔍, 📊, 🔨, etc.)
4. Final completion reaction (✅ or 👎)
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import AsyncMock, MagicMock, patch

from integrations.ollama_intent import IntentResult, MessageIntent
from integrations.telegram.reaction_manager import (
    TelegramReactionManager,
    add_message_received_reaction,
    add_intent_based_reaction,
    add_processing_stage_reaction,
    complete_reaction_sequence,
)


async def test_progressive_reactions():
    """Test that reactions accumulate progressively as message handling evolves."""
    print("\n🧪 Testing Progressive Reaction System with Layer 169+ Support\n")
    
    # Create mock client
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    # Test parameters
    chat_id = 12345
    message_id = 67890
    
    # Create reaction manager instance
    manager = TelegramReactionManager()
    
    print("📍 Step 1: Message received - adding initial reaction")
    success = await manager.add_received_reaction(mock_client, chat_id, message_id)
    print(f"   ✅ Added 👀 reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀"]
    
    print("\n📍 Step 2: Intent classified as WEB_SEARCH")
    intent_result = IntentResult(
        intent=MessageIntent.WEB_SEARCH,
        confidence=0.85,
        reasoning="User asking for current information",
        suggested_emoji="🗿"
    )
    success = await manager.add_intent_reaction(mock_client, chat_id, message_id, intent_result)
    print(f"   ✅ Added 🗿 reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🗿"]
    
    print("\n📍 Step 3: Processing stages - adding reactions as work progresses")
    
    # Simulate searching phase
    await asyncio.sleep(0.1)  # Simulate processing time
    success = await manager.add_processing_stage_reaction(mock_client, chat_id, message_id, "🔍")
    print(f"   ✅ Added 🔍 (searching) reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🗿", "🔍"]
    
    # Simulate analysis phase
    await asyncio.sleep(0.1)
    success = await manager.add_processing_stage_reaction(mock_client, chat_id, message_id, "📊")
    print(f"   ✅ Added 📊 (analyzing) reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🗿", "🔍", "📊"]
    
    print("\n📍 Step 4: Processing complete - adding final reaction")
    success = await manager.add_completion_reaction(mock_client, chat_id, message_id)
    print(f"   ✅ Added ✅ (completed) reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🗿", "🔍", "📊", "✅"]
    
    print("\n🎉 Success! All reactions accumulated properly:")
    print(f"   Final reaction sequence: {' → '.join(manager.get_message_reactions(chat_id, message_id))}")
    
    # Verify raw API calls
    print(f"\n📞 Raw API invoke() was called {mock_client.invoke.call_count} times")
    
    # Test duplicate prevention
    print("\n📍 Testing duplicate prevention...")
    success = await manager.add_processing_stage_reaction(mock_client, chat_id, message_id, "🔍")
    print(f"   Attempted to add duplicate 🔍: {success} (should skip)")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🗿", "🔍", "📊", "✅"]
    
    print("\n✅ All tests passed! The reaction system properly accumulates reactions.")


async def test_error_handling():
    """Test error scenarios and fallback behavior."""
    print("\n🧪 Testing Error Handling and Fallback\n")
    
    # Create mock client that fails on raw API but works with send_reaction
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(side_effect=Exception("Raw API error"))
    mock_client.send_reaction = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    manager = TelegramReactionManager()
    chat_id = 12345
    message_id = 99999
    
    print("📍 Testing fallback when raw API fails")
    success = await manager.add_received_reaction(mock_client, chat_id, message_id)
    print(f"   Added reaction with fallback: {success}")
    print(f"   send_reaction was called: {mock_client.send_reaction.called}")
    assert success
    assert mock_client.send_reaction.called
    assert manager.get_message_reactions(chat_id, message_id) == ["👀"]
    
    print("\n✅ Fallback mechanism works correctly!")


async def test_reaction_flow_example():
    """Example of a complete message processing flow with reactions."""
    print("\n🌟 Complete Message Processing Flow Example\n")
    
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    chat_id = 12345
    message_id = 11111
    
    print("User: 'Generate an image of a sunset over mountains'")
    
    # Message received
    await add_message_received_reaction(mock_client, chat_id, message_id)
    print("Bot: 👀 (message seen)")
    await asyncio.sleep(0.5)
    
    # Intent classified
    intent_result = IntentResult(
        intent=MessageIntent.IMAGE_GENERATION,
        confidence=0.95,
        reasoning="User wants to generate an image",
        suggested_emoji="🎨"
    )
    await add_intent_based_reaction(mock_client, chat_id, message_id, intent_result)
    print("Bot: 👀🎨 (identified as image generation request)")
    await asyncio.sleep(0.5)
    
    # Processing stages
    await add_processing_stage_reaction(mock_client, chat_id, message_id, "🤔")
    print("Bot: 👀🎨🤔 (thinking about the prompt)")
    await asyncio.sleep(0.5)
    
    await add_processing_stage_reaction(mock_client, chat_id, message_id, "✨")
    print("Bot: 👀🎨🤔✨ (generating image)")
    await asyncio.sleep(1.0)
    
    # Complete
    await complete_reaction_sequence(mock_client, chat_id, message_id, intent_result, success=True)
    print("Bot: 👀🎨🤔✨✅ (image generated successfully!)")
    
    print("\n🎉 The user can see the progress through accumulating reactions!")


async def main():
    """Run all tests."""
    await test_progressive_reactions()
    await test_error_handling()
    await test_reaction_flow_example()
    print("\n🚀 All tests completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())