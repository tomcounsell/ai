#!/usr/bin/env python3
"""
One-time Telegram authentication script
Authenticates and saves session, then exits
"""

import asyncio
import os
import sys
from pathlib import Path
from telethon import TelegramClient
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment
load_dotenv()

async def authenticate():
    """Perform one-time authentication with Telegram"""
    
    # Get credentials
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    password = os.getenv('TELEGRAM_PASSWORD')
    session_name = os.getenv('TELEGRAM_SESSION_NAME', 'ai_rebuild_session')
    
    if not api_id or api_id == 'your_telegram_api_id_here':
        print("❌ TELEGRAM_API_ID not configured in .env file")
        return False
        
    if not api_hash or api_hash == 'your_telegram_api_hash_here':
        print("❌ TELEGRAM_API_HASH not configured in .env file")
        return False
    
    print("🔐 Starting Telegram Authentication...")
    print(f"📱 Phone: {phone}")
    print("")
    
    # Create client
    client = TelegramClient(f'data/{session_name}', int(api_id), api_hash)
    
    try:
        # Connect and authenticate
        await client.start(
            phone=lambda: phone,
            password=lambda: password if password else None
        )
        
        # Check if authenticated
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Successfully authenticated as: {me.first_name} (@{me.username})")
            print(f"📱 Phone: {me.phone}")
            print(f"💾 Session saved to: data/{session_name}.session")
            print("")
            print("✨ Authentication complete! You can now run:")
            print("   ./scripts/start.sh --telegram")
            return True
        else:
            print("❌ Authentication failed")
            return False
            
    except Exception as e:
        print(f"❌ Error during authentication: {e}")
        return False
        
    finally:
        await client.disconnect()

async def main():
    """Main function"""
    success = await authenticate()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    print("")
    print("="*60)
    print("🔐 TELEGRAM ONE-TIME AUTHENTICATION")
    print("="*60)
    print("")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Authentication cancelled by user")
        sys.exit(1)