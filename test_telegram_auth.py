#!/usr/bin/env python3
"""
Test Telegram authentication
"""

import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

async def test_auth():
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    password = os.getenv('TELEGRAM_PASSWORD')
    
    print(f"API ID: {api_id[:5]}..." if api_id else "API ID not set")
    print(f"API Hash: {api_hash[:5]}..." if api_hash else "API Hash not set")
    print(f"Phone: {phone}" if phone else "Phone not set")
    print(f"Password: {'Set' if password else 'Not set'}")
    
    if not api_id or not api_hash:
        print("❌ Missing API credentials")
        return
    
    client = TelegramClient('data/test_session', int(api_id), api_hash)
    
    await client.connect()
    
    if await client.is_user_authorized():
        print("✅ Already authorized!")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
    else:
        print("❌ Not authorized - would need to login")
        print("The bot would need to:")
        print("1. Send code request to phone")
        print("2. Wait for user to input code")
        print("3. Handle 2FA if enabled")
        
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(test_auth())