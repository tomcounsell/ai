#!/usr/bin/env python3
"""
Telegram Client Authorization Utility

This script handles the interactive authorization process for Pyrogram Telegram client.
It creates a session file that allows the bot to connect without re-authorization.

Usage:
    python integrations/telegram/auth.py

Requirements:
    - TELEGRAM_API_ID in environment variables
    - TELEGRAM_API_HASH in environment variables
    - Phone number access for receiving verification code
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from pyrogram import Client

# Load environment variables
load_dotenv()


async def authorize_telegram_client():
    """Interactive Telegram client authorization process."""

    print("🔐 Telegram Client Authorization")
    print("=" * 40)

    # Check environment variables
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("❌ Missing Telegram API credentials!")
        print("\nPlease ensure these environment variables are set:")
        print("- TELEGRAM_API_ID")
        print("- TELEGRAM_API_HASH")
        print("\nGet these from https://my.telegram.org/apps")
        return False

    print(f"✅ API ID: {api_id}")
    print(f"✅ API Hash: {'*' * (len(api_hash) - 4) + api_hash[-4:]}")

    # Set working directory to project root and create isolated session directory
    workdir = str(Path(__file__).parent.parent.parent)
    telegram_session_dir = os.path.join(workdir, "telegram_sessions")
    os.makedirs(telegram_session_dir, exist_ok=True)
    print(f"📁 Working directory: {workdir}")
    print(f"📁 Session directory: {telegram_session_dir}")

    try:
        print("\n🚀 Creating Telegram client...")

        # Create client with isolated session storage to prevent database conflicts
        client = Client("ai_project_bot", api_id=int(api_id), api_hash=api_hash, workdir=telegram_session_dir)

        print("📱 Starting authorization process...")
        print("\nThis will prompt for:")
        print("1. Phone number (international format: +1234567890)")
        print("2. Verification code (sent via Telegram)")
        print("3. Two-factor password (if enabled)")

        # Start the client - this triggers interactive auth if needed
        await client.start()

        # Get user info to confirm success
        me = await client.get_me()
        print("\n✅ Authorization successful!")
        print(f"👤 Logged in as: {me.first_name} {me.last_name or ''}")
        print(f"📞 Phone: {me.phone_number}")
        print(f"🆔 User ID: {me.id}")

        # Stop the client
        await client.stop()

        # Check if session file was created
        session_file = Path(workdir) / "ai_project_bot.session"
        if session_file.exists():
            print(f"\n💾 Session file created: {session_file}")
            print("🎉 Future bot runs will use this session automatically!")
        else:
            print("\n⚠️  Session file not found - authorization may have failed")
            return False

        return True

    except KeyboardInterrupt:
        print("\n\n⚠️  Authorization cancelled by user")
        return False

    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        print("\nCommon issues:")
        print("- Invalid API credentials")
        print("- Network connectivity problems")
        print("- Phone number format (use international: +1234567890)")
        print("- Incorrect verification code")
        return False


async def check_existing_session():
    """Check if there's already a valid session."""
    workdir = str(Path(__file__).parent.parent.parent)
    session_file = Path(workdir) / "ai_project_bot.session"

    if not session_file.exists():
        print("📋 No existing session found")
        return False

    print(f"📋 Found existing session: {session_file}")
    print("🔍 Checking if session is still valid...")

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("❌ Missing API credentials to test session")
        return False

    try:
        client = Client("ai_project_bot", api_id=int(api_id), api_hash=api_hash, workdir=workdir)

        await client.start()
        me = await client.get_me()
        await client.stop()

        print(f"✅ Session is valid! Logged in as: {me.first_name} {me.last_name or ''}")
        return True

    except Exception as e:
        print(f"❌ Session is invalid: {e}")
        print("🔄 Will need to re-authorize...")
        return False


async def main():
    """Main authorization flow."""
    print("🤖 Telegram Bot Authorization Utility")
    print("=" * 50)

    # Check if session already exists and is valid
    if await check_existing_session():
        print("\n🎉 Authorization already complete!")
        print("The bot can connect using the existing session.")

        # Check if running in non-interactive mode (like from start.sh)
        import sys
        if not sys.stdin.isatty():
            print("👍 Using existing session (non-interactive mode).")
            return True

        while True:
            try:
                choice = input("\nDo you want to re-authorize anyway? (y/n): ").lower().strip()
                if choice in ["n", "no"]:
                    print("👍 Using existing session.")
                    return True
                elif choice in ["y", "yes"]:
                    print("🔄 Proceeding with re-authorization...")
                    break
                else:
                    print("Please enter 'y' or 'n'")
            except EOFError:
                # Handle EOF when running non-interactively
                print("👍 Using existing session (EOF detected).")
                return True

    # Perform authorization
    success = await authorize_telegram_client()

    if success:
        print("\n🎉 Authorization complete!")
        print("You can now run the Telegram bot with:")
        print("  uv run agents/valor_agent.py")
        print("  scripts/start.sh")
    else:
        print("\n❌ Authorization failed!")
        print("Please check the errors above and try again.")
        return False

    return True


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\nAuthorization cancelled.")
        sys.exit(1)
