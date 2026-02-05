#!/usr/bin/env python3
"""
Test which emoji reactions ACTUALLY work on Telegram.

IMPORTANT: The Telegram API's GetAvailableReactionsRequest can be misleading!
This script tests each emoji by actually setting it as a reaction.

Note: "Saved Messages" requires Premium for reactions. This script tests
against a real chat where free reactions work.

Run this periodically to validate the list hasn't changed.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "ai_rebuild_session")
SESSION_PATH = Path(__file__).parent.parent / "data" / SESSION_NAME

# Tom's user ID from the logs
TEST_CHAT_ID = 179144806  # Tom's DM

# Previously rate-limited emojis that need validation
RATE_LIMITED_EMOJIS = [
    "👨‍💻", "👀", "🔥", "⚡", "💯", "🏆", "🎉", "🎃", "🎄", "🎅",
    "🕊", "🐳", "🦄", "🙈", "🙉", "🙊", "🌚", "🌭", "🍌", "🍓",
    "🍾", "💅", "🗿", "🆒", "💊", "🤷", "☃",
    # Also test shrug variants
    "🤷‍♂", "🤷‍♀",
]

# Full test list - use --full flag to test all
FULL_TEST_EMOJIS = [
    # Hearts and love
    "❤", "❤️", "❤‍🔥", "❤️‍🔥", "💔", "💘", "😍", "🥰", "😘", "💋",
    # Hands
    "👍", "👎", "👏", "🙏", "👌", "🤝", "✍", "✍️", "🖕",
    # Faces - positive
    "😁", "🤣", "😂", "🤩", "😇", "😎", "🤓", "🤗", "🫡",
    # Faces - negative
    "😱", "🤯", "🤬", "😢", "😭", "🤮", "😨", "😡",
    # Faces - neutral/other
    "🤔", "🥱", "🥴", "😴", "😐", "🤨", "🤪",
    # Characters
    "🤡", "👻", "👾", "😈", "💩", "🎅", "👨‍💻",
    # Animals/nature
    "🕊", "🐳", "🦄", "🙈", "🙉", "🙊",
    # Objects
    "🔥", "⚡", "💯", "🏆", "🎉", "🎃", "🎄", "☃", "☃️", "🗿", "💊", "🆒",
    # Food
    "🍌", "🍓", "🌭", "🍾",
    # Other
    "🌚", "💅", "👀",
    # Shrug variants
    "🤷", "🤷‍♂", "🤷‍♂️", "🤷‍♀", "🤷‍♀️",
    # Commonly attempted but likely invalid
    "💻", "🎨", "❌", "✅", "🔄", "⏳", "🚀", "💡", "📝", "🔍",
]


async def test_reactions_for_real(emojis_to_test: list[str], delay: float = 0.5):
    """Test each emoji by actually trying to set it as a reaction."""

    print(f"Connecting to Telegram...")
    client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")

    # Send a test message to the test chat
    print(f"\nSending test message to chat {TEST_CHAT_ID}...")
    test_msg = await client.send_message(TEST_CHAT_ID, f"🧪 Testing {len(emojis_to_test)} emoji reactions - will delete shortly")
    print(f"Test message ID: {test_msg.id}")
    print(f"Using {delay}s delay between reactions to avoid rate limiting\n")

    valid = []
    invalid = []
    invalid_reasons = {}

    print("=" * 60)
    print("TESTING EACH EMOJI BY ACTUALLY SETTING IT AS A REACTION")
    print("=" * 60 + "\n")

    for i, emoji in enumerate(emojis_to_test):
        try:
            # Try to set the reaction
            await client(SendReactionRequest(
                peer=TEST_CHAT_ID,
                msg_id=test_msg.id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
            valid.append(emoji)
            codepoints = " ".join(f"U+{ord(c):04X}" for c in emoji)
            print(f"  [{i+1}/{len(emojis_to_test)}] ✓ VALID: {emoji}  ({codepoints})")

            # Delay to avoid rate limiting
            await asyncio.sleep(delay)

        except Exception as e:
            invalid.append(emoji)
            error_type = type(e).__name__
            invalid_reasons[emoji] = error_type
            codepoints = " ".join(f"U+{ord(c):04X}" for c in emoji)
            print(f"  [{i+1}/{len(emojis_to_test)}] ✗ INVALID: {emoji}  ({codepoints}) - {error_type}")

            # If rate limited, wait the required time
            if "FloodWait" in error_type:
                wait_match = str(e)
                print(f"      Rate limited! Waiting before continuing...")
                await asyncio.sleep(5)  # Brief pause before continuing
            else:
                await asyncio.sleep(0.2)

    # Clear reaction and delete test message
    print("\nCleaning up...")
    try:
        await client(SendReactionRequest(peer=TEST_CHAT_ID, msg_id=test_msg.id, reaction=[]))
        await asyncio.sleep(1)
        await test_msg.delete()
        print("Test message deleted.")
    except Exception as e:
        print(f"Cleanup note: {e}")

    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)

    print(f"\n✓ VALID ({len(valid)}):")
    print(" ".join(valid))

    if invalid:
        print(f"\n✗ INVALID ({len(invalid)}):")
        for emoji in invalid:
            codepoints = " ".join(f"U+{ord(c):04X}" for c in emoji)
            print(f"  {emoji}  ({codepoints}) - {invalid_reasons[emoji]}")

    # Print as Python code
    print("\n" + "=" * 60)
    print("COPY-PASTE FOR bridge/telegram_bridge.py:")
    print("=" * 60)
    print(f'''
# Validated {len(valid)} emojis on {date.today()} via scripts/test_emoji_reactions.py
# IMPORTANT: Do NOT trust GetAvailableReactionsRequest - it lies!
# These were tested by actually setting each as a reaction.
# fmt: off
VALIDATED_REACTIONS = {valid!r}
# fmt: on
''')

    await client.disconnect()
    return valid, invalid


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Test Telegram emoji reactions")
    parser.add_argument("--full", action="store_true", help="Test all emojis (not just rate-limited ones)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between reactions (default: 0.5s)")
    args = parser.parse_args()

    if args.full:
        print("Testing FULL emoji list...")
        emojis = FULL_TEST_EMOJIS
    else:
        print("Testing previously RATE-LIMITED emojis...")
        emojis = RATE_LIMITED_EMOJIS

    asyncio.run(test_reactions_for_real(emojis, delay=args.delay))


if __name__ == "__main__":
    main()
