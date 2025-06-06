#!/usr/bin/env python3
"""
Test the expanded emoji list for reactions showing creative variety.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.telegram.reaction_manager import reaction_manager


def test_expanded_emoji_list():
    """Show the variety of emojis now available for reactions."""
    print("\n🎨 Expanded Emoji Categories for Telegram Reactions\n")
    
    # Count total emojis
    total = len(reaction_manager.valid_telegram_emojis)
    print(f"Total available emojis: {total}\n")
    
    # Show some examples by category
    categories = {
        "Processing & Status": ["🔍", "📊", "🔨", "✨", "🌐", "📡", "⚙️", "🧠", "💡", "🎯"],
        "Happy & Positive": ["😊", "😄", "🥳", "🤩", "🙌", "👏", "🎉", "🌈", "☀️", "💯"],
        "Animals": ["🐶", "🐱", "🦊", "🐼", "🦄", "🦋", "🐬", "🐳", "🦈", "🐙"],
        "Food & Drink": ["🍎", "🍕", "🍔", "🍣", "🍰", "☕", "🧋", "🍾", "🥂", "🍹"],
        "Nature": ["🌸", "🌺", "🌻", "🌹", "🌿", "🌱", "🌲", "🌊", "⛰️", "🏔️"],
        "Tech & Objects": ["💻", "📱", "🎮", "🎧", "🎤", "📷", "🚗", "✈️", "🚀", "🛸"],
        "Weather": ["☀️", "🌤️", "☁️", "🌧️", "⛈️", "❄️", "🌨️", "🌪️", "🌈", "⭐"],
        "Sports & Games": ["⚽", "🏀", "🎾", "🏆", "🥇", "🎮", "🎲", "🎯", "🎳", "🏹"],
    }
    
    for category, emojis in categories.items():
        # Check which emojis from our examples are valid
        valid = [e for e in emojis if e in reaction_manager.valid_telegram_emojis]
        print(f"{category}: {' '.join(valid)}")
    
    print("\n✨ Example usage scenarios:\n")
    
    scenarios = [
        ("User asks about the weather", "☀️ or 🌧️ or 🌈"),
        ("User shares a cute pet photo", "🐶 or 🐱 or 😍"),
        ("User asks for pizza recommendations", "🍕 or 🍔 or 😋"),
        ("Bot is searching for information", "🔍 → 📊 → 💡"),
        ("User achieves a milestone", "🎉 or 🏆 or 🥇"),
        ("Bot is processing an image", "👀 → 🎨 → ✨"),
        ("User shares good news", "🎊 or 🥳 or 🙌"),
        ("Bot is thinking deeply", "🤔 → 🧠 → 💭"),
    ]
    
    for scenario, reactions in scenarios:
        print(f"  • {scenario}: {reactions}")
    
    print("\n🚀 The bot can now express itself with much more variety!")
    print("   Reactions accumulate to show progress: 👀 → 🔍 → 📊 → ✨ → ✅")


async def test_emoji_validation():
    """Test that all emoji categories are properly included."""
    print("\n🧪 Testing Emoji Validation\n")
    
    # Test some emojis from each category
    test_emojis = {
        "Standard": ["👍", "❤️", "🔥", "😁", "🤔"],
        "Animals": ["🐶", "🦊", "🦄", "🦋", "🐬"],
        "Food": ["🍕", "🍔", "☕", "🍰", "🍾"],
        "Nature": ["🌸", "🌊", "🌈", "⛰️", "🌲"],
        "Objects": ["💻", "📱", "🎮", "🚀", "💡"],
    }
    
    all_valid = True
    for category, emojis in test_emojis.items():
        valid = [e for e in emojis if e in reaction_manager.valid_telegram_emojis]
        invalid = [e for e in emojis if e not in reaction_manager.valid_telegram_emojis]
        
        print(f"{category}:")
        print(f"  ✅ Valid: {' '.join(valid)}")
        if invalid:
            print(f"  ❌ Invalid: {' '.join(invalid)}")
            all_valid = False
    
    if all_valid:
        print("\n✅ All test emojis are properly included!")
    else:
        print("\n⚠️  Some emojis might need to be added to the valid list")


def main():
    """Run all tests."""
    test_expanded_emoji_list()
    asyncio.run(test_emoji_validation())


if __name__ == "__main__":
    main()