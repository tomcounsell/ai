#!/usr/bin/env python3
"""
Telegram Client Authorization Utility

This script handles the interactive authorization process for Telethon Telegram client.
It creates a session file that allows the bridge to connect without re-authorization.

Usage:
    python scripts/telegram_login.py

Requirements:
    - TELEGRAM_API_ID in environment variables
    - TELEGRAM_API_HASH in environment variables
    - TELEGRAM_PHONE in environment variables
    - TELEGRAM_PASSWORD in environment variables (if 2FA enabled)
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv(Path(__file__).parent.parent / ".env")

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")
SESSION_PATH = Path(__file__).parent.parent / "data" / "valor_bridge"


async def check_existing_session():
    """Check if there's already a valid session."""
    session_file = Path(str(SESSION_PATH) + ".session")

    if not session_file.exists():
        print("📋 No existing session found")
        return False

    print(f"📋 Found existing session: {session_file}")
    print("🔍 Checking if session is still valid...")

    try:
        client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            await client.disconnect()
            print(
                f"✅ Session is valid! Logged in as: {me.first_name} (@{me.username})"
            )
            return True
        else:
            await client.disconnect()
            print("❌ Session exists but is not authorized")
            return False

    except Exception as e:
        print(f"❌ Session check failed: {e}")
        return False


async def authorize_telegram_client():
    """Interactive Telegram client authorization process."""

    print("\n🚀 Creating Telegram client...")

    try:
        client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
        await client.connect()

        print(f"📱 Requesting verification code for {PHONE}...")

        # Explicitly request the code
        try:
            sent_code = await client.send_code_request(PHONE, force_sms=False)
            code_type = sent_code.type.__class__.__name__
            print(f"✅ Code sent via: {code_type}")
            print(
                f"   Code length: {sent_code.type.length if hasattr(sent_code.type, 'length') else 'unknown'}"
            )

            code = None  # Will be set below

            # Show where to look for the code
            if "App" in code_type:
                print("\n💡 Code sent to your Telegram app!")
                print("   Check for a message from 'Telegram' (blue checkmark)")
                print("   Look on ALL devices: phone, desktop, web")
        except Exception as e:
            print(f"❌ Failed to send code: {e}")
            await client.disconnect()
            return False

        print("\n📨 Check your Telegram app or SMS for the code!")

        # Get code from user (if not already entered above)
        if not code:
            code = input("\nEnter the verification code: ").strip()

        try:
            await client.sign_in(PHONE, code)
        except Exception as e:
            error_str = str(e).lower()
            if "two-step" in error_str or "password" in error_str or "2fa" in error_str:
                print("🔐 Two-factor authentication required...")
                if PASSWORD:
                    await client.sign_in(password=PASSWORD)
                else:
                    pwd = input("Enter your 2FA password: ").strip()
                    await client.sign_in(password=pwd)
            else:
                raise

        me = await client.get_me()
        print("\n✅ Authorization successful!")
        print(f"👤 Logged in as: {me.first_name} {me.last_name or ''}")
        print(f"📱 Username: @{me.username}")
        print(f"🆔 User ID: {me.id}")

        await client.disconnect()

        session_file = Path(str(SESSION_PATH) + ".session")
        if session_file.exists():
            print(f"\n💾 Session file created: {session_file}")
            print("🎉 Future bridge runs will use this session automatically!")
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
        print("  - Invalid API credentials")
        print("  - Network connectivity problems")
        print("  - Phone number format (use international: +1234567890)")
        print("  - Incorrect verification code")
        print("  - Wrong 2FA password")
        return False


async def main():
    """Main authorization flow."""
    print("🤖 Telegram Bot Authorization Utility")
    print("=" * 50)

    # Check environment variables
    if not API_ID or not API_HASH:
        print("❌ Missing Telegram API credentials!")
        print("\nPlease ensure these environment variables are set:")
        print("  - TELEGRAM_API_ID")
        print("  - TELEGRAM_API_HASH")
        print("\nGet these from https://my.telegram.org/apps")
        return False

    if not PHONE:
        print("❌ Missing TELEGRAM_PHONE!")
        print("Please set TELEGRAM_PHONE in your .env file")
        return False

    print(f"✅ API ID: {API_ID}")
    print(f"✅ API Hash: {'*' * (len(API_HASH) - 4) + API_HASH[-4:]}")
    print(f"✅ Phone: {PHONE}")
    print(f"📁 Session path: {SESSION_PATH}")

    # Check if session already exists and is valid
    if await check_existing_session():
        print("\n🎉 Authorization already complete!")
        print("The bridge can connect using the existing session.")

        while True:
            choice = (
                input("\nDo you want to re-authorize anyway? (y/n): ").lower().strip()
            )
            if choice in ["n", "no", ""]:
                print("👍 Using existing session.")
                return True
            elif choice in ["y", "yes"]:
                print("🔄 Proceeding with re-authorization...")
                break
            else:
                print("Please enter 'y' or 'n'")

    # Perform authorization
    success = await authorize_telegram_client()

    if success:
        print("\n" + "=" * 50)
        print("🎉 Authorization complete!")
        print("\nYou can now start the Telegram bridge:")
        print("  ./scripts/valor-service.sh start")
    else:
        print("\n" + "=" * 50)
        print("❌ Authorization failed!")
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
