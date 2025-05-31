#!/usr/bin/env python3
"""
Script to list all Telegram groups the user is a member of.

Usage:
    python scripts/list_telegram_groups.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.telegram.client import TelegramClient
from integrations.telegram.utils import list_telegram_dialogs_safe, format_dialogs_list


async def main():
    """List all Telegram groups the user is a member of."""
    print("🔍 Fetching Telegram groups...")
    
    client = TelegramClient()
    
    try:
        # Initialize the client
        if not await client.initialize():
            print("❌ Failed to initialize Telegram client")
            print("💡 Try running: scripts/telegram_login.sh")
            return
        
        print("✅ Connected to Telegram")
        
        # Get dialogs safely
        dialogs_data, error = await list_telegram_dialogs_safe(client)
        
        if error:
            print(f"❌ Error fetching dialogs: {error}")
            return
        
        # Display results
        print("\n" + "="*50)
        print("📱 TELEGRAM GROUPS & CHANNELS")
        print("="*50)
        
        groups = dialogs_data['groups']
        
        if not groups:
            print("No groups found.")
        else:
            for i, group in enumerate(groups, 1):
                print(f"\n{i}. {group['title']}")
                print(f"   ID: {group['id']}")
                print(f"   Type: {group['type']}")
                if group.get('member_count'):
                    print(f"   Members: {group['member_count']:,}")
                if group.get('username'):
                    print(f"   Username: @{group['username']}")
                if group.get('unread_count', 0) > 0:
                    print(f"   Unread: {group['unread_count']}")
        
        print(f"\n📊 Summary: {len(groups)} groups/channels found")
        
        # Also show DMs count
        dms = dialogs_data['dms']
        print(f"💬 Direct messages: {len(dms)} conversations")
        
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    finally:
        try:
            await client.stop()
            print("🔌 Disconnected from Telegram")
        except:
            pass


if __name__ == "__main__":
    asyncio.run(main())