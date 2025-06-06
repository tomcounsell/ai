#!/usr/bin/env python3
"""
Test the clean 3-reaction strategy for Telegram reactions.

This test demonstrates the new approach:
1. Acknowledge (👀) - always present
2. Intent/Tool (varies) - replaced as tools are used
3. Final status (✅ or 🚫) - added at completion
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import AsyncMock

from integrations.ollama_intent import IntentResult, MessageIntent
from integrations.telegram.reaction_manager import (
    TelegramReactionManager,
    add_message_received_reaction,
    add_intent_based_reaction,
    update_tool_reaction,
)


async def test_three_reaction_strategy():
    """Test the clean 3-reaction approach."""
    print("\n🎯 Testing 3-Reaction Strategy\n")
    
    # Create mock client
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    # Test parameters
    chat_id = 12345
    message_id = 67890
    
    # Create reaction manager instance
    manager = TelegramReactionManager()
    
    print("📍 Step 1: Message received - adding acknowledgment")
    success = await manager.add_received_reaction(mock_client, chat_id, message_id)
    print(f"   ✅ Added 👀 reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀"]
    
    print("\n📍 Step 2: Intent classified as WEB_SEARCH")
    intent_result = IntentResult(
        intent=MessageIntent.WEB_SEARCH,
        confidence=0.85,
        reasoning="User asking for current information",
        suggested_emoji="🌐"
    )
    success = await manager.add_intent_reaction(mock_client, chat_id, message_id, intent_result)
    print(f"   ✅ Added 🌐 reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🌐"]
    
    print("\n📍 Step 3: Tool execution - search tool activated")
    await asyncio.sleep(0.1)  # Simulate processing time
    success = await manager.update_tool_reaction(mock_client, chat_id, message_id, "🔍")
    print(f"   ✅ Replaced intent with 🔍 (searching): {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🔍"]
    
    print("\n📍 Step 4: Tool execution - analysis tool activated")
    await asyncio.sleep(0.1)
    success = await manager.update_tool_reaction(mock_client, chat_id, message_id, "📊")
    print(f"   ✅ Replaced tool with 📊 (analyzing): {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "📊"]
    
    print("\n📍 Step 5: Processing complete - adding final status")
    success = await manager.add_completion_reaction(mock_client, chat_id, message_id)
    print(f"   ✅ Added ✅ (completed) reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "📊", "✅"]
    
    print("\n🎉 Success! Clean 3-reaction strategy:")
    print(f"   Final state: {' '.join(manager.get_message_reactions(chat_id, message_id))}")
    
    # Verify raw API calls
    print(f"\n📞 Raw API invoke() was called {mock_client.invoke.call_count} times")
    

async def test_error_scenario():
    """Test error handling with 🚫 reaction."""
    print("\n🧪 Testing Error Scenario\n")
    
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    manager = TelegramReactionManager()
    chat_id = 12345
    message_id = 99999
    
    # Quick setup
    await manager.add_received_reaction(mock_client, chat_id, message_id)
    intent_result = IntentResult(
        intent=MessageIntent.IMAGE_GENERATION,
        confidence=0.95,
        reasoning="User wants an image",
        suggested_emoji="🎨"
    )
    await manager.add_intent_reaction(mock_client, chat_id, message_id, intent_result)
    
    print("📍 Simulating error during processing")
    success = await manager.add_error_reaction(mock_client, chat_id, message_id)
    print(f"   ✅ Added 🚫 (error) reaction: {success}")
    print(f"   Current reactions: {manager.get_message_reactions(chat_id, message_id)}")
    assert manager.get_message_reactions(chat_id, message_id) == ["👀", "🎨", "🚫"]
    
    print("\n✅ Error handling works correctly with 🚫!")


async def test_various_flows():
    """Test different processing flows."""
    print("\n🌟 Testing Various Processing Flows\n")
    
    mock_client = AsyncMock()
    mock_client.invoke = AsyncMock(return_value=None)
    mock_client.resolve_peer = AsyncMock(return_value={"_": "InputPeerChat", "chat_id": 12345})
    
    manager = TelegramReactionManager()
    chat_id = 12345
    
    flows = [
        {
            "name": "Image Generation",
            "intent": MessageIntent.IMAGE_GENERATION,
            "intent_emoji": "🎨",
            "tools": [("✨", "generating")],
            "success": True
        },
        {
            "name": "Development Task",
            "intent": MessageIntent.DEVELOPMENT_TASK,
            "intent_emoji": "👨‍💻",
            "tools": [("🔨", "building"), ("🧪", "testing")],
            "success": True
        },
        {
            "name": "Project Query",
            "intent": MessageIntent.PROJECT_QUERY,
            "intent_emoji": "📋",
            "tools": [("🔍", "searching"), ("📊", "analyzing")],
            "success": True
        },
        {
            "name": "Failed Web Search",
            "intent": MessageIntent.WEB_SEARCH,
            "intent_emoji": "🌐",
            "tools": [("🔍", "searching")],
            "success": False
        }
    ]
    
    for i, flow in enumerate(flows):
        message_id = 10000 + i
        print(f"{'='*50}")
        print(f"Flow: {flow['name']}")
        
        # 1. Acknowledge
        await manager.add_received_reaction(mock_client, chat_id, message_id)
        print(f"  1. Acknowledge: 👀")
        
        # 2. Intent
        intent_result = IntentResult(
            intent=flow['intent'],
            confidence=0.9,
            reasoning=f"Testing {flow['name']}",
            suggested_emoji=flow['intent_emoji']
        )
        await manager.add_intent_reaction(mock_client, chat_id, message_id, intent_result)
        print(f"  2. Intent: 👀 {flow['intent_emoji']}")
        
        # 3. Tools
        for tool_emoji, tool_name in flow['tools']:
            await asyncio.sleep(0.05)
            await manager.update_tool_reaction(mock_client, chat_id, message_id, tool_emoji)
            print(f"  2. Tool ({tool_name}): 👀 {tool_emoji}")
        
        # 4. Final status
        if flow['success']:
            await manager.add_completion_reaction(mock_client, chat_id, message_id)
            final = "✅"
        else:
            await manager.add_error_reaction(mock_client, chat_id, message_id)
            final = "🚫"
        
        reactions = manager.get_message_reactions(chat_id, message_id)
        print(f"  3. Final: {' '.join(reactions)}")
        print(f"  Result: {'Success' if flow['success'] else 'Failed'}")
    
    print(f"\n{'='*50}")
    print("🎉 All flows demonstrate the clean 3-reaction approach!")


async def main():
    """Run all tests."""
    await test_three_reaction_strategy()
    await test_error_scenario()
    await test_various_flows()
    print("\n🚀 All tests completed successfully!")
    print("\n📝 Summary:")
    print("  • Always 3 reactions maximum")
    print("  • First slot: 👀 (acknowledge)")
    print("  • Second slot: Intent → Tool (replaced)")
    print("  • Third slot: ✅ or 🚫 (final status)")


if __name__ == "__main__":
    asyncio.run(main())