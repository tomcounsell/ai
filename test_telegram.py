#!/usr/bin/env python3
"""
Quick test to check Telegram connectivity
"""

import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

async def test():
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    password = os.getenv('TELEGRAM_PASSWORD')
    
    print(f"API ID: {api_id}")
    print(f"Phone: {phone}")
    print("Connecting to Telegram...")
    
    client = TelegramClient('data/ai_rebuild_session', int(api_id), api_hash)
    
    try:
        await client.connect()
        
        if await client.is_user_authorized():
            print("✅ Already authorized!")
            me = await client.get_me()
            print(f"Logged in as: {me.first_name} (@{me.username})")
            
            # Get recent chats
            print("\nRecent chats:")
            async for dialog in client.iter_dialogs(limit=10):
                print(f"- {dialog.name}")
        else:
            print("❌ Not authorized. Need to login.")
            print("Note: Telegram auth often requires manual intervention")
            
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(test())