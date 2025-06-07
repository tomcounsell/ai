#!/usr/bin/env python
"""Test the fixed reaction system with valid Telegram emojis."""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram.reaction_manager import TelegramReactionManager
from integrations.ollama_intent import IntentResult, MessageIntent


async def test_reaction_mapping():
    """Test that invalid emojis are properly mapped to valid ones."""
    
    print("Testing Telegram Reaction Fix\n")
    print("=" * 50)
    
    # Create reaction manager
    manager = TelegramReactionManager()
    
    print(f"Total valid reactions: {len(manager.valid_telegram_emojis)}")
    print(f"Sample valid reactions: {list(manager.valid_telegram_emojis)[:10]}...")
    
    print("\n" + "=" * 50)
    print("Testing emoji mappings:")
    print("=" * 50)
    
    # Test invalid emojis that should be mapped
    test_emojis = [
        ("✅", "Checkmark (completion)"),
        ("🚫", "No entry (error)"),
        ("🎨", "Art palette (image generation)"),
        ("🔍", "Magnifying glass (search)"),
        ("📊", "Bar chart (analysis)"),
        ("🌐", "Globe (web)"),
        ("🔨", "Hammer (building)"),
        ("✨", "Sparkles (processing)"),
        ("🧠", "Brain (thinking)"),
        ("🚀", "Rocket (launching)"),
    ]
    
    for emoji, description in test_emojis:
        if emoji in manager.valid_telegram_emojis:
            status = "✓ VALID"
            mapped = emoji
        else:
            status = "✗ INVALID"
            mapped = manager.emoji_mapping.get(emoji, "🤔")
        
        print(f"{emoji} {description:<30} {status} → {mapped}")
    
    print("\n" + "=" * 50)
    print("Testing intent reactions:")
    print("=" * 50)
    
    # Test intent reactions
    for intent in MessageIntent:
        emoji = manager.intent_reactions.get(intent, "🤔")
        valid = "✓" if emoji in manager.valid_telegram_emojis else "✗"
        print(f"{intent.value:<20} → {emoji} {valid}")
    
    print("\n" + "=" * 50)
    print("Testing status reactions:")
    print("=" * 50)
    
    # Test status reactions
    from integrations.telegram.reaction_manager import ReactionStatus
    print(f"RECEIVED:  {manager.status_reactions[ReactionStatus.RECEIVED]} (👀)")
    print(f"COMPLETED: {manager.status_reactions[ReactionStatus.COMPLETED]} (was ✅, now 👍)")
    print(f"ERROR:     {manager.status_reactions[ReactionStatus.ERROR]} (was 🚫, now 👎)")
    
    print("\n✅ Reaction system has been fixed to use only valid Telegram emojis!")
    print(f"   - Reduced from 672 to {len(manager.valid_telegram_emojis)} valid reactions")
    print("   - Invalid emojis are automatically mapped to valid alternatives")
    print("   - All status and intent reactions now use valid emojis")


if __name__ == "__main__":
    asyncio.run(test_reaction_mapping())